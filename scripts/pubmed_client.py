"""One rate limiter for all E-utilities requests; partial data is never discarded."""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


class PubMedError(RuntimeError):
    pass


class PubMedClient:
    def __init__(self, opener=None, sleep=None, clock=None):
        self.opener = opener or urllib.request.urlopen
        self.sleep = sleep or time.sleep
        self.clock = clock or time.monotonic
        self.next_request = 0

    def request(self, endpoint, params, xml=False):
        params = dict(params, db="pubmed", tool="literature_daily")
        if os.getenv("NCBI_API_KEY"):
            params["api_key"] = os.environ["NCBI_API_KEY"]
        if os.getenv("NCBI_EMAIL"):
            params["email"] = os.environ["NCBI_EMAIL"]
        # POST keeps credentials and long PMID lists out of URLs/logs.
        req = urllib.request.Request(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/" + endpoint,
            data=urllib.parse.urlencode(params).encode(),
            headers={"User-Agent": "LiteratureDaily/3.0"})
        for attempt in range(4):
            self.sleep(max(0, self.next_request - self.clock()))
            self.next_request = self.clock() + 0.4
            try:
                with self.opener(req, timeout=30) as response:
                    text = response.read().decode("utf-8")
                value = ET.fromstring(text) if xml else json.loads(text)
                if not xml and (value.get("error") or value.get("esearchresult", {}).get("ERROR")):
                    raise PubMedError("PubMed returned an API error")
                if xml and value.find(".//ERROR") is not None:
                    raise PubMedError("PubMed returned an XML error")
                return value
            except urllib.error.HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504):
                    raise PubMedError(f"PubMed HTTP {exc.code}") from None
                retry_after = exc.headers.get("Retry-After", "0") if exc.headers else "0"
                delay = max(2 ** (attempt + 1), float(retry_after) if retry_after.isdigit() else 0)
            except (OSError, ValueError, ET.ParseError, PubMedError):
                delay = 2 ** (attempt + 1)
            if attempt < 3:
                self.sleep(min(delay, 60))
        raise PubMedError("PubMed retries exhausted")

    def ids(self, query, limit=10000):
        ids, total = [], None
        while total is None or len(ids) < min(total, limit, 9999):
            result = self.request("esearch.fcgi", {
                "term": query, "retmode": "json", "sort": "pub date",
                "retstart": len(ids), "retmax": min(200, limit - len(ids))})["esearchresult"]
            total = int(result["count"])
            page = result.get("idlist", [])
            if not page:
                break
            before = len(ids)
            ids = list(dict.fromkeys(ids + page))
            if len(ids) == before:
                break
        return ids, total

    def fetch(self, ids):
        papers, pending = [], []
        for offset in range(0, len(ids), 50):
            batch = ids[offset:offset + 50]
            try:
                root = self.request("efetch.fcgi", {"id": ",".join(batch), "retmode": "xml"}, xml=True)
                found = {p["pmid"]: p for p in parse_articles(root)}
                papers.extend(found[p] for p in batch if p in found)
                pending.extend(p for p in batch if p not in found)
            except PubMedError:
                pending.extend(batch)
        return papers, pending

    def search(self, query, limit=10000):
        try:
            ids, total = self.ids(query, limit)
        except (PubMedError, KeyError, ValueError):
            return {"papers": [], "retrieved": 0, "total_results": 0,
                    "status": "failed", "pending_pmids": [], "error": "search_failed"}
        papers, pending = self.fetch(ids)
        return {"papers": papers, "retrieved": len(papers), "total_results": total,
                "pending_pmids": pending,
                "status": "ok" if len(ids) == total and not pending else "partial"}


def content(node):
    return "".join(node.itertext()).strip() if node is not None else ""


def parse_articles(root):
    for record in root.findall(".//PubmedArticle"):
        citation = record.find("MedlineCitation")
        if citation is None:
            continue
        article = citation.find("Article")
        if article is None:
            continue
        pmid = content(citation.find("PMID"))
        title = content(article.find("ArticleTitle"))
        if not pmid or not title:
            continue
        parts = []
        for node in article.findall("Abstract/AbstractText"):
            text = content(node)
            if text:
                parts.append((node.get("Label") + ": " if node.get("Label") else "") + text)
        authors = []
        for author in article.findall("AuthorList/Author"):
            name = " ".join(filter(None, [content(author.find("LastName")), content(author.find("Initials"))]))
            authors.append(name or content(author.find("CollectiveName")))
        doi = next((content(n) for n in record.findall("PubmedData/ArticleIdList/ArticleId")
                    if n.get("IdType") == "doi"), "")
        date = article.find("Journal/JournalIssue/PubDate")
        pubdate = " ".join(content(n) for n in date) if date is not None else ""
        yield {"pmid": pmid, "title": title, "abstract": "\n".join(parts),
               "abstract_status": "available" if parts else "not_provided",
               "authors": [a for a in authors if a], "doi": doi, "pubdate": pubdate,
               "journal": content(article.find("Journal/Title")),
               "iso_journal": content(article.find("Journal/ISOAbbreviation")),
               "publication_types": [content(n) for n in article.findall("PublicationTypeList/PublicationType")],
               "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"}


CLIENT = PubMedClient()
