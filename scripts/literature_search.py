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
import xml.etree.ElementTree as ET
from datetime import datetime

# ============ 配置 ============
BASE_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
BASE_ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
BASE_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
USER_AGENT = "TopJournalSkill/2.0 (research tool)"
REQUEST_INTERVAL = 0.35

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
    # 注意：所有关键词必须加 \b 词边界，防止"find"里的"in"被误匹配
    author_match = re.search(r'\b(?:author|by|from)\s*[:]?\s*["\']?([A-Za-zÀ-ÿ\s\.\,\-]+?)["\']?(?:\s+(?:about|on\b|topic:|\bin\b)\s+|$)', raw, re.IGNORECASE)
    topic_match = re.search(r'\b(?:about|on|topic:|\bin\b|regarding)\s*[:]?\s*["\']?(.+?)["\']?$', raw, re.IGNORECASE)

    author = None
    topic = None

    if author_match:
        author = author_match.group(1).strip().rstrip(".,;")
    if topic_match:
        topic = topic_match.group(1).strip().rstrip(".,;")

    # 如果同时有 author 和 topic
    if author and topic:
        return {"type": "combined", "author": author, "topic": topic}

    # 只有 author
    if author:
        return {"type": "author", "author": author}

    # 尝试检测人名模式 (两个词，首字母大写，可以带中间名缩写)
    person_pattern = re.match(r'^["\']?([A-Z][a-zÀ-ÿ]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-zÀ-ÿ]+)["\']?$', raw)
    if person_pattern:
        return {"type": "author", "author": person_pattern.group(1)}

    # 只有 topic
    if topic:
        return {"type": "topic", "topic": topic}

    # 默认作为主题搜索
    return {"type": "topic", "topic": raw}


def build_query_from_parsed(parsed, journals=None, years=None):
    """从解析结果构建 PubMed 检索式"""
    parts = []

    if parsed["type"] == "raw":
        parts.append(f"({parsed['query']})")
    elif parsed["type"] == "bool":
        parts.append(f"({parsed['query']})")
    elif parsed["type"] == "author":
        # 自动处理作者名格式
        author = parsed["author"]
        # 标准化：去除多余空格和标点
        author = re.sub(r'[\.\s]+', ' ', author).strip()
        parts.append(f'"{author}"[Author]')
    elif parsed["type"] == "topic":
        topic = parsed["topic"]
        parts.append(f'"{topic}"[Title/Abstract]')
    elif parsed["type"] == "combined":
        author = re.sub(r'[\.\s]+', ' ', parsed["author"]).strip()
        topic = parsed["topic"]
        parts.append(f'("{author}"[Author]) AND ("{topic}"[Title/Abstract])')

    # 期刊筛选
    if journals:
        j_list = [j.strip() for j in journals.split(",") if j.strip()]
        j_terms = []
        for j in j_list:
            j_lower = j.lower().strip()
            mapped = JOURNAL_MAP.get(j_lower, j)
            j_terms.append(f'"{mapped}"[Journal]')
        if j_terms:
            parts.append("(" + " OR ".join(j_terms) + ")")

    # 时间范围
    if years:
        year_match = re.match(r"(\d{4})-(\d{4})", str(years))
        if year_match:
            start, end = year_match.groups()
            parts.append(f'("{start}"[Date - Publication] : "{end}"[Date - Publication])')

    return " AND ".join(parts)


# ============ API 调用 ============

def call_pubmed_api(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[WARNING] API 请求失败: {e}", file=sys.stderr)
        return None


def call_pubmed_xml(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8")
    except Exception as e:
        print(f"[WARNING] XML 请求失败: {e}", file=sys.stderr)
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
    params = {
        "db": "pubmed",
        "term": query,
        "retmax": min(retmax, 100),
        "retmode": "json",
        "sort": "date",
    }
    url = f"{BASE_ESEARCH}?{urllib.parse.urlencode(params)}"
    print(f"[INFO] 搜索 URL: {url}", file=sys.stderr)

    data = call_pubmed_api(url)
    if not data or "esearchresult" not in data:
        return [], 0

    result = data["esearchresult"]
    id_list = result.get("idlist", [])
    total = int(result.get("count", 0))
    return id_list, total


def fetch_summaries(pmids):
    if not pmids:
        return []

    params = {"db": "pubmed", "id": ",".join(pmids), "retmode": "json"}
    url = f"{BASE_ESUMMARY}?{urllib.parse.urlencode(params)}"
    data = call_pubmed_api(url)
    if not data or "result" not in data:
        return []

    results = []
    result_data = data["result"]
    for pmid in pmids:
        if pmid not in result_data:
            continue
        item = result_data[pmid]
        authors = []
        for author in item.get("authors", []):
            name = author.get("name", "")
            if name:
                authors.append(name)
        doi = ""
        for aid in item.get("articleids", []):
            if aid.get("idtype") == "doi":
                doi = aid.get("value", "")
                break
        results.append({
            "pmid": pmid,
            "title": item.get("title", "").replace("&quot;", '"').replace("&amp;", "&"),
            "authors": authors,
            "journal": item.get("fulljournalname", ""),
            "iso_journal": item.get("source", ""),
            "pubdate": item.get("pubdate", ""),
            "doi": doi,
            "abstract": "",
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        })

    # 批量获取摘要
    for i in range(0, len(results), 10):
        batch = results[i:i + 10]
        for r in batch:
            r["abstract"] = extract_abstract(r["pmid"])
        time.sleep(REQUEST_INTERVAL)

    return results


def search(query_text, journals=None, years=None, max_results=20):
    """主搜索函数：解析 → 构建 → 搜索 → 获取详情"""
    # 1. 解析自然语言
    parsed = parse_natural_query(query_text)

    # 2. 构建 PubMed 检索式
    pubmed_query = build_query_from_parsed(parsed, journals, years)

    print(f"[INFO] 解析结果: {parsed['type']}", file=sys.stderr)
    print(f"[INFO] 原始输入: {query_text}", file=sys.stderr)
    print(f"[INFO] 检索式: {pubmed_query}", file=sys.stderr)

    # 3. 搜索
    pmids, total = search_pubmed(pubmed_query, max_results)
    print(f"[INFO] 共找到 {total} 篇文献，获取 {len(pmids)} 篇", file=sys.stderr)

    # 4. 获取详情
    papers = fetch_summaries(pmids) if pmids else []

    # 5. 生成时间戳
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    return {
        "meta": {
            "timestamp": timestamp,
            "query_raw": query_text,
            "query_type": parsed["type"],
            "query_pubmed": pubmed_query,
            "search_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
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
    # 同时保存一份 latest 链接
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
    # 自然语言检索
    python literature_search.py --query "find papers by Gelinas JN"
    python literature_search.py --query "papers by Jennifer Gelinas about epilepsy"
    python literature_search.py --query "CRISPR gene editing in Nature"

    # 精确检索
    python literature_search.py --query "Gelinas JN"[Author] --years 2020-2025
    python literature_search.py --query "deep learning" --journals "Nature,Science" --max 30

    # 不含期刊和时间限制
    python literature_search.py --query "Gelinas JN"[Author] --journals "" --years ""
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

    # 摘要
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
   