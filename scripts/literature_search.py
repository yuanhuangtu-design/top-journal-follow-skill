#!/usr/bin/env python3
"""
顶刊文献检索工具 v2 — PubMed E-utilities API
============================================
支持自然语言检索式，自动识别作者/主题/期刊搜索。
每次输出带时间戳，不会覆盖之前的结果。

用法:
    python3 literature_search.py --query "find papers by Gelinas JN about epilepsy"
    python3 literature_search.py --query "CRISPR gene editing" --journals "Nature,Science" --years 2024-2025
    python3 literature_search.py --query "author:Gelinas JN topic:neurostimulation"

输出:
    output/search_results_YYYYMMDD_HHMMSS.json
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime

# ============ 配置 ============
BASE_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
BASE_ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
BASE_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
USER_AGENT = "TopJournalSkill/2.0 (research tool)"
REQUEST_INTERVAL = 1.5  # 增加到1.5秒，避免PubMed限流
MAX_RETRIES = 6  # 最大重试次数
RETRY_BACKOFF = 2.0  # 指数退避基数

# 期刊名映射
JOURNAL_MAP = {
    "nature": "Nature",
    "science": "Science",
    "cell": "Cell",
    "nature biotechnology": "Nat Biotechnol",
    "nature medicine": "Nat Med",
    "nature genetics": "Nat Genet",
    "nature neuroscience": "Nat Neurosci",
    "nature methods": "Nat Methods",
    "nature communications": "Nat Commun",
    "nature reviews": "Nat Rev",
    "nature reviews neuroscience": "Nat Rev Neurosci",
    "lancet": "Lancet",
    "nejm": "N Engl J Med",
    "pnas": "Proc Natl Acad Sci U S A",
    "new england journal of medicine": "N Engl J Med",
}


class SearchError(RuntimeError):
    """The requested PubMed coverage could not be verified."""


def parse_natural_query(raw_query):
    """
    解析自然语言检索式。
    支持：
      - "find papers by John Smith" → 作者搜索
      - "papers about CRISPR" → 主题搜索
      - "author:Smith J topic:gene therapy" → 显式指定
      - "Smith J[NM]" → 直接传递 PubMed 字段语法
      - "CRISPR AND Nature" → 布尔检索
    """
    raw = raw_query.strip()

    # 如果已包含 PubMed 字段标签 [xxx]，直接返回
    if re.search(r'\[.*?\]', raw):
        return {"type": "raw", "query": raw}

    # 如果包含 AND/OR/NOT，作为布尔检索
    if re.search(r'\b(AND|OR|NOT)\b', raw, re.IGNORECASE):
        return {"type": "bool", "query": raw}

    # 尝试提取 author:xxx 和 topic:xxx
    author_match = re.search(r'\b(?:author|by|from)\s*[:]?\s*["\']?([A-Za-zÀ-ÿ\s\.\,\-]+?)["\']?(?:\s+(?:about|on\b|topic:|\bin\b)\s+|$)', raw, re.IGNORECASE)
    topic_match = re.search(r'\b(?:about|on|topic:|\bin\b|regarding)\s*[:]?\s*["\']?(.+?)["\']?$', raw, re.IGNORECASE)

    author = None
    topic = None

    if author_match:
        author = author_match.group(1).strip().rstrip(".,;")
    if topic_match:
        topic = topic_match.group(1).strip().rstrip(".,;")

    if author and topic:
        return {"type": "combined", "author": author, "topic": topic}
    if author:
        return {"type": "author", "author": author}

    person_pattern = re.match(r'^["\']?([A-Z][a-zÀ-ÿ]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-zÀ-ÿ]+)["\']?$', raw)
    if person_pattern:
        return {"type": "author", "author": person_pattern.group(1)}
    if topic:
        return {"type": "topic", "topic": topic}

    return {"type": "topic", "topic": raw}


def build_query_from_parsed(parsed, journals=None, years=None):
    """从解析结果构建 PubMed 检索式"""
    parts = []

    if parsed["type"] == "raw":
        parts.append(f"({parsed['query']})")
    elif parsed["type"] == "bool":
        parts.append(f"({parsed['query']})")
    elif parsed["type"] == "author":
        author = re.sub(r'[\.\s]+', ' ', parsed["author"]).strip()
        parts.append(f'"{author}"[Author]')
    elif parsed["type"] == "topic":
        topic = parsed["topic"]
        parts.append(f'"{topic}"[Title/Abstract]')
    elif parsed["type"] == "combined":
        author = re.sub(r'[\.\s]+', ' ', parsed["author"]).strip()
        topic = parsed["topic"]
        parts.append(f'("{author}"[Author]) AND ("{topic}"[Title/Abstract])')

    if journals:
        j_list = [j.strip() for j in journals.split(",") if j.strip()]
        j_terms = []
        for j in j_list:
            j_lower = j.lower().strip()
            mapped = JOURNAL_MAP.get(j_lower, j)
            j_terms.append(f'"{mapped}"[Journal]')
        if j_terms:
            parts.append("(" + " OR ".join(j_terms) + ")")

    if years:
        year_match = re.match(r"(\d{4})-(\d{4})", str(years))
        if year_match:
            start, end = year_match.groups()
            parts.append(f'("{start}"[Date - Publication] : "{end}"[Date - Publication])')

    return " AND ".join(parts)


# ============ API 调用 ============

def call_pubmed_api(url):
    """调用PubMed JSON API，带指数退避重试（处理429限流和5xx错误）"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                wait = RETRY_BACKOFF ** attempt
                print(f"[WARNING] PubMed HTTP {e.code}，第{attempt+1}次重试，等待{wait:.1f}秒...", file=sys.stderr)
                time.sleep(wait)
                continue
            else:
                print(f"[WARNING] API 请求失败 (HTTP {e.code}): {e}", file=sys.stderr)
                return None
        except Exception as e:
            print(f"[WARNING] API 请求异常: {e}", file=sys.stderr)
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF ** attempt)
                continue
            return None
    print(f"[ERROR] API 请求重试{MAX_RETRIES}次后仍失败", file=sys.stderr)
    return None


def call_pubmed_xml(url):
    """调用PubMed XML API，带指数退避重试（处理429限流和5xx错误）"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                wait = RETRY_BACKOFF ** attempt
                print(f"[WARNING] PubMed HTTP {e.code}，第{attempt+1}次重试，等待{wait:.1f}秒...", file=sys.stderr)
                time.sleep(wait)
                continue
            else:
                print(f"[WARNING] XML 请求失败 (HTTP {e.code}): {e}", file=sys.stderr)
                return None
        except Exception as e:
            print(f"[WARNING] XML 请求异常: {e}", file=sys.stderr)
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF ** attempt)
                continue
            return None
    print(f"[ERROR] XML 请求重试{MAX_RETRIES}次后仍失败", file=sys.stderr)
    return None


def extract_abstract(pmid):
    url = f"{BASE_EFETCH}?db=pubmed&id={pmid}&retmode=xml&rettype=abstract"
    xml_text = call_pubmed_xml(url)
    if not xml_text:
        return ""
    try:
        root = ET.fromstring(xml_text)
        abstract_parts = []
        for abstract_text in root.iter("AbstractText"):
            label = abstract_text.get("Label", "")
            text = (abstract_text.text or "")
            for child in abstract_text:
                if child.text:
                    text += " " + child.text
                if child.tail:
                    text += " " + child.tail
            if label:
                abstract_parts.append(f"{label}: {text.strip()}")
            else:
                abstract_parts.append(text.strip())
        return "\n".join(abstract_parts)
    except ET.ParseError:
        return ""


# ============ 搜索功能 ============

def search_pubmed(query, retmax=20):
    if not 1 <= retmax <= 10000:
        raise SearchError('retmax must be between 1 and PubMed\'s 10000-ID limit')
    ids, total = [], None
    while total is None or len(ids) < min(total, retmax):
        params = {"db": "pubmed", "term": query, "retmax": min(100, retmax-len(ids)),
                  "retstart": len(ids), "retmode": "json", "sort": "date"}
        data = call_pubmed_api(f"{BASE_ESEARCH}?{urllib.parse.urlencode(params)}")
        if not data or "esearchresult" not in data:
            raise SearchError('PubMed search request failed')
        result = data['esearchresult']
        if result.get('errorlist') or result.get('ERROR'):
            raise SearchError('PubMed rejected the search expression')
        count = int(result['count'])
        if total is not None and total != count:
            raise SearchError('PubMed count changed during pagination; retry the window')
        total = count
        batch = result.get('idlist', [])
        if len(batch) != len(set(batch)) or set(ids).intersection(batch):
            raise SearchError('Repeated PubMed IDs during pagination')
        if not batch and len(ids) < min(total, retmax):
            raise SearchError('PubMed pagination ended before the declared count')
        ids.extend(batch)
        if len(ids) >= min(total, retmax):
            break
        time.sleep(REQUEST_INTERVAL)
    return ids, total


def publication_label(types):
    for code, label in [('Editorial', '述评/社论'), ('Letter', '来信'),
                        ('Meta-Analysis', 'Meta分析'), ('Systematic Review', '系统综述'),
                        ('Review', '综述'), ('Case Reports', '病例报告')]:
        if code in types:
            return label
    return '期刊论文（研究设计待核对）'


def parse_article(article):
    citation = article.find('./MedlineCitation')
    content = citation.find('./Article')
    pmid = citation.findtext('./PMID', '').strip()
    if not pmid or content.find('ArticleTitle') is None:
        raise SearchError('Article metadata is incomplete')
    own_ids = {x.get('IdType'): x.text for x in article.findall('./PubmedData/ArticleIdList/ArticleId')}
    abstracts = []
    for node in content.findall('./Abstract/AbstractText'):
        value = ''.join(node.itertext()).strip()
        if value:
            abstracts.append((node.get('Label')+': ' if node.get('Label') else '')+value)
    types = [n.text for n in content.findall('./PublicationTypeList/PublicationType') if n.text]
    date_node = content.find('./ArticleDate')
    if date_node is None:
        date_node = content.find('./Journal/JournalIssue/PubDate')
    pubdate = ' '.join(date_node.findtext(k, '') for k in ['Year','Month','Day']).strip() if date_node is not None else ''
    if not pubdate and date_node is not None:
        pubdate = date_node.findtext('MedlineDate', '')
    related = [{'relation': n.get('RefType'), 'pmid': n.findtext('PMID'), 'source': n.findtext('RefSource')}
               for n in citation.findall('./CommentsCorrectionsList/CommentsCorrections')]
    return {'pmid': pmid, 'title': ''.join(content.find('ArticleTitle').itertext()),
            'authors': [n.findtext('CollectiveName') or ' '.join(filter(None, [n.findtext('LastName'), n.findtext('Initials')]))
                        for n in content.findall('./AuthorList/Author')],
            'journal': content.findtext('./Journal/Title', ''),
            'iso_journal': content.findtext('./Journal/ISOAbbreviation', ''),
            'pubdate': pubdate, 'doi': own_ids.get('doi', ''),
            'abstract': '\n'.join(abstracts), 'abstract_status': 'available' if abstracts else 'not_provided',
            'publication_types': types, 'publication_label': publication_label(types),
            'related_articles': related, 'url': f'https://pubmed.ncbi.nlm.nih.gov/{pmid}/'}


def parse_book_article(article):
    document = article.find('./BookDocument')
    pmid = document.findtext('PMID', '').strip()
    title = document.find('ArticleTitle')
    if not pmid or title is None:
        raise SearchError('Book chapter metadata is incomplete')
    book_title = document.find('./Book/BookTitle')
    journal = ''.join(book_title.itertext()) if book_title is not None else 'NCBI Bookshelf'
    own_ids = {x.get('IdType'): x.text for x in document.findall('./ArticleIdList/ArticleId')}
    def date_text(name):
        node = document.find(name)
        return '-'.join(node.findtext(key, '') for key in ['Year','Month','Day']).strip('-') if node is not None else ''
    published, revised = date_text('ContributionDate'), date_text('DateRevised')
    parts = []
    for node in document.findall('./Abstract/AbstractText'):
        value = ''.join(node.itertext()).strip()
        if value:
            parts.append((node.get('Label')+': ' if node.get('Label') else '')+value)
    return {'pmid': pmid, 'title': ''.join(title.itertext()),
            'authors': [n.findtext('CollectiveName') or ' '.join(filter(None,[n.findtext('LastName'),n.findtext('Initials')]))
                        for n in document.findall('./AuthorList/Author')],
            'journal': journal, 'iso_journal': journal, 'doi': own_ids.get('doi',''),
            'pubdate': published + ('（更新 '+revised+'）' if revised else ''),
            'publication_types': ['Book Chapter']+[n.text for n in document.findall('./PublicationType') if n.text],
            'publication_label': '图书章节/更新', 'abstract': '\n'.join(parts),
            'abstract_status': 'available' if parts else 'not_provided',
            'related_articles': [], 'url': f'https://pubmed.ncbi.nlm.nih.gov/{pmid}/'}


def fetch_summaries(pmids):
    """Fetch verified metadata and abstracts in batches, including publication types."""
    records = {}
    for offset in range(0, len(pmids), 20):
        batch = pmids[offset:offset+20]
        url = BASE_EFETCH + "?" + urllib.parse.urlencode({"db":"pubmed", "id":",".join(batch), "retmode":"xml"})
        xml = call_pubmed_xml(url)
        if not xml:
            raise SearchError("PubMed detail request failed")
        try:
            root = ET.fromstring(xml)
            articles = [parse_book_article(node) if node.tag == 'PubmedBookArticle' else parse_article(node)
                        for node in root if node.tag in ('PubmedArticle', 'PubmedBookArticle')]
        except (ET.ParseError, AttributeError) as exc:
            raise SearchError("Invalid PubMed article response") from exc
        found = {p["pmid"]:p for p in articles}
        if set(found) != set(batch) or len(articles) != len(batch):
            raise SearchError("PubMed detail response omitted or repeated requested articles")
        records.update(found)
        time.sleep(REQUEST_INTERVAL)
    return [records[pmid] for pmid in pmids]


def search(query_text, journals=None, years=None, max_results=20):
    """主搜索函数：解析 → 构建 → 搜索 → 获取详情"""
    parsed = parse_natural_query(query_text)
    pubmed_query = build_query_from_parsed(parsed, journals, years)

    print(f"[INFO] 解析结果: {parsed['type']}", file=sys.stderr)
    print(f"[INFO] 原始输入: {query_text}", file=sys.stderr)
    print(f"[INFO] 检索式: {pubmed_query}", file=sys.stderr)

    pmids, total = search_pubmed(pubmed_query, max_results)
    print(f"[INFO] 共找到 {total} 篇文献，获取 {len(pmids)} 篇", file=sys.stderr)

    papers = fetch_summaries(pmids) if pmids else []
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    return {
        "meta": {
            "timestamp": timestamp,
            "query_raw": query_text,
            "query_type": parsed["type"],
            "query_pubmed": pubmed_query,
            "search_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
        "complete": len(pmids) == total and len(papers) == total,
        "total_results": total,
        "retrieved": len(papers),
        "journals_searched": [j.strip() for j in journals.split(",")] if journals else [],
        "years_range": years or "all",
        "papers": papers,
    }


def save_results(results, output_dir="output"):
    """保存结果到带时间戳的文件"""
    os.makedirs(output_dir, exist_ok=True)
    timestamp = results["meta"]["timestamp"]
    filename = f"search_results_{timestamp}.json"
    output_path = os.path.join(output_dir, filename)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"[SUCCESS] 结果已保存: {output_path}")
    latest_path = os.path.join(output_dir, "search_results_latest.json")
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"[SUCCESS] 最新结果已同步: {latest_path}")
    return output_path, latest_path


# ============ 主程序 ============

def main():
    parser = argparse.ArgumentParser(
        description="顶刊文献检索 v2 — 支持自然语言",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python literature_search.py --query "find papers by Gelinas JN"
    python literature_search.py --query "papers by Jennifer Gelinas about epilepsy"
    python literature_search.py --query "CRISPR gene editing in Nature"
    python literature_search.py --query "Gelinas JN"[Author] --years 2020-2025
    python literature_search.py --query "deep learning" --journals "Nature,Science" --max 30
        """
    )
    parser.add_argument("--query", "-q", required=True, help="检索关键词（支持自然语言）")
    parser.add_argument("--journals", "-j", default="", help="目标期刊逗号分隔（默认: 不限）")
    parser.add_argument("--years", "-y", default="", help="年份范围 YYYY-YYYY（默认: 不限）")
    parser.add_argument("--max", "-m", type=int, default=20, help="最大获取数（默认: 20）")
    parser.add_argument("--output", "-o", default="output", help="输出目录")

    args = parser.parse_args()
    results = search(args.query, args.journals, args.years, args.max)
    main_path, _ = save_results(results, args.output)

    print(f"\n{'='*50}")
    print(f"  检索摘要")
    print(f"{'='*50}")
    print(f"  原始输入: {results['meta']['query_raw']}")
    print(f"  检索类型: {results['meta']['query_type']}")
    print(f"  PubMed 检索式: {results['meta']['query_pubmed']}")
    print(f"  总结果数: {results['total_results']}")
    print(f"  已获取: {results['retrieved']} 篇")
    if results['journals_searched']:
        print(f"  期刊范围: {', '.join(results['journals_searched'])}")
    if results['years_range'] and results['years_range'] != 'all':
        print(f"  时间范围: {results['years_range']}")

    if results["papers"]:
        print(f"\n  文献列表 (前{len(results['papers'])}篇):")
        for i, paper in enumerate(results["papers"], 1):
            title_short = paper["title"][:70]
            abs_preview = (paper["abstract"][:80].replace("\n", " ") + "...") if paper["abstract"] else "(无摘要)"
            print(f"  {i:2d}. [{paper['pubdate']}] {title_short}")
            print(f"      期刊: {paper['journal']}")
            print(f"      作者: {', '.join(paper['authors'][:3])}{'...' if len(paper['authors'])>3 else ''}")
            print(f"      摘要: {abs_preview}")
            print()
    print(f"[DONE] 结果文件: {main_path}")


if __name__ == "__main__":
    main()
