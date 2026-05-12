#!/usr/bin/env python3
"""
文献分析工具 v2 — 关键词提取、词频统计、多维分析
============================================
- 扩充医学/学术停用词表，过滤干扰词
- TF-IDF 排序，突出领域特有术语
- 带时间戳输出，不覆盖之前结果

用法:
    python3 analyze.py --input output/search_results_latest.json
    python3 analyze.py --input output/search_results_20260512_143022.json --output output/
"""

import argparse
import json
import math
import os
import re
import sys
from collections import Counter
from datetime import datetime


# ======================== 停用词 ========================
# 通用英语停用词
ENGLISH_STOP_WORDS = """
a about above after again against all am an and any are as at be because
been before being below between both but by cannot could did do does done
each few for from further had has have having here how however if in into
is it its just like many may more most much no nor not of on once one only
or other ought our out over own per pre put rather said same should show
shown shows side since so some still such take than that the their them
then there these they this those through too under up upon very was way were
what when where whether which while who why will with would yet you your
""".split()

# 学术论文通用词（大量扩充）
ACADEMIC_STOP_WORDS = """
study studies research results method methods analysis approach data
based using used show found also well significant demonstrate demonstrates
suggest suggests indicate indicates provide provides potential novel new
recent current important key role play plays including related associated
increased reduced compared observed model models system systems process
level effect effects impact understanding reveal reveals findings finding
conclusion conclude implication implications future further review article
summary overview introduction discussion conclusion limitation limitations
strength strengths weakness weaknesses hypothesis hypotheses rationale
aim aims objective objectives goal goals purpose background context
framework perspective insight concept conceptual theoretical empirical
evidence implication implications relevance significance contribution
advance advancement progress development discovery finding observation
pattern patterns feature features characteristic characteristics
""".split()

# 生物医学领域停用词（核心扩充）
BIOMEDICAL_STOP_WORDS = """
patient patients clinical trial trials therapy therapeutic treatment
diagnosis diagnostic disease diseases disorder syndrome symptom
drug drugs medication medicine medical surgical surgery procedure
hospital physician doctor nurse healthcare health outcome outcomes
risk factor factors cohort population sample samples group groups
control controlled randomized blinding blind placebo baseline
demographic age gender male female adult children child infant
pediatric elderly prospective retrospective longitudinal cross-sectional
observational experimental intervention intervention treated untreated
p-value confidence interval p = p < p > statistical significance
significant significantly non-significant ns ns correlation correlated
association associated variable variables parameter parameters
protocol enrollment enrolled recruit recruitment eligibility eligible
exclusion criteria inclusion criteria screen screening consent
approved ethical institutional review board irb approval ethics
compliance adhere adherence compliance consent informed consent
written consent standard care standard-of-care usual care standard therapy
first-line second-line third-line dose dosing dosage regimen
administration administered oral intravenous iv subcutaneous
injection injected infusion bolus topical local systemic
adverse event adverse events ae ae safety tolerability toxicity
side effect side-effects efficacy effectiveness efficiency
responder non-responder response rate remission recovery relapse
recurrence survival mortality morbidity complication complications
infection infections pain bleeding hemorrhage thrombosis
inflammation inflammatory biomarker biomarkers endpoint endpoints
primary endpoint secondary endpoint surrogate endpoint
across among within between during through after before previous
previously prior subsequent subsequently following initial
final total overall cumulative annual monthly weekly daily
baseline follow-up followup long-term short-term mid-term
one-year two-year five-year year month week day hour minute
number range mean median average standard deviation sd sem
interquartile range iqr confidence interval ci or hr rr
hazard ratio odds ratio relative risk risk ratio adjusted
unadjusted multivariate univariate regression logistic linear
cox proportional hazards kaplan-meier propensity score matching
psm subgroup sub-group subcategory sensitivity analysis
missing data dropout lost follow-up imputation multiple imputation
""".split()

ALL_STOP_WORDS = set(ENGLISH_STOP_WORDS + ACADEMIC_STOP_WORDS + BIOMEDICAL_STOP_WORDS)


# ======================== 学科关键术语（这些词具有区分度，保留）===================
# 注意：这些词虽然单看普通，但在文献语境下具有学科特征，不加入停用词


# ======================== 文本处理 ========================

def tokenize(text):
    """将文本分词并过滤停用词"""
    if not text:
        return []
    text = text.lower()
    words = re.findall(r"[a-z][a-z0-9\-]{2,}", text)
    return [w for w in words if w not in ALL_STOP_WORDS and len(w) > 2]


def compute_tf(words):
    """计算词频 (Term Frequency)"""
    counter = Counter(words)
    total = len(words)
    if total == 0:
        return {}
    return {w: c / total for w, c in counter.items()}


def compute_idf(all_docs_words):
    """计算逆文档频率 (Inverse Document Frequency)"""
    N = len(all_docs_words)
    doc_freq = Counter()
    for doc_words in all_docs_words:
        unique = set(doc_words)
        for w in unique:
            doc_freq[w] += 1
    idf = {}
    for w, df in doc_freq.items():
        idf[w] = math.log((N + 1) / (df + 1)) + 1
    return idf


def compute_tfidf(all_docs_words):
    """计算 TF-IDF"""
    idf = compute_idf(all_docs_words)
    tfidf_scores = Counter()
    for doc_words in all_docs_words:
        tf = compute_tf(doc_words)
        for w, tf_val in tf.items():
            if w in idf:
                tfidf_scores[w] += tf_val * idf[w]
    return tfidf_scores


# ======================== 分析功能 ========================

def analyze_keywords(papers):
    """
    关键词分析 - 标题词3倍权重 + TF-IDF 排序
    过滤通用学术词（已通过停用词实现）
    """
    all_title_words = []
    all_abstract_words = []
    all_docs_title = []
    all_docs_abstract = []

    for paper in papers:
        tw = tokenize(paper.get("title", ""))
        aw = tokenize(paper.get("abstract", ""))
        all_title_words.extend(tw)
        all_abstract_words.extend(aw)
        if tw:
            all_docs_title.append(tw)
        if aw:
            all_docs_abstract.append(aw)

    # 标题权重3倍 + 摘要权重1倍
    combined_counter = Counter()
    for w in all_title_words:
        combined_counter[w] += 3
    for w in all_abstract_words:
        combined_counter[w] += 1

    # TF-IDF 排名（使用标题+摘要）
    tfidf = compute_tfidf(all_docs_title + all_docs_abstract)

    # 合并：取 Top 50 混合排序
    all_combined = set(list(combined_counter.keys()) + list(tfidf.keys()))
    scored = []
    for w in all_combined:
        freq_score = combined_counter.get(w, 0)
        tfidf_score = tfidf.get(w, 0)
        # 综合得分 = 频率得分 * (1 + log(1 + TF-IDF))
        composite = freq_score * (1 + math.log(1 + tfidf_score)) if tfidf_score > 0 else freq_score
        scored.append({"word": w, "freq": freq_score, "tfidf": round(tfidf_score, 4), "score": round(composite, 2)})

    scored.sort(key=lambda x: x["score"], reverse=True)

    top_keywords = scored[:50]

    # 二元词组
    all_words = all_title_words + all_abstract_words
    bigrams = Counter()
    for i in range(len(all_words) - 1):
        bg = f"{all_words[i]} {all_words[i+1]}"
        bigrams[bg] += 1
    top_bigrams = [{"bigram": bg, "freq": c}
                   for bg, c in bigrams.most_common(30) if c >= 2]

    return {
        "keyword_frequency": top_keywords,
        "bigrams": top_bigrams,
        "total_unique_keywords": len(set(all_title_words + all_abstract_words)),
        "total_words": len(all_title_words) + len(all_abstract_words),
    }


def analyze_journals(papers):
    journal_counter = Counter()
    for paper in papers:
        journal = paper.get("journal", "").strip()
        if journal:
            journal_counter[journal] += 1

    total = len(papers)
    journal_dist = []
    for j, c in journal_counter.most_common():
        journal_dist.append({
            "journal": j,
            "count": c,
            "percentage": round(c / total * 100, 1) if total > 0 else 0,
        })
    return {
        "journal_distribution": journal_dist,
        "unique_journals": len(journal_counter),
        "total_papers": total,
    }


def analyze_authors(papers):
    author_counter = Counter()
    for paper in papers:
        for author in paper.get("authors", []):
            if author:
                author_counter[author] += 1
    author_rank = [{"author": a, "count": c}
                   for a, c in author_counter.most_common(30)]
    return {
        "author_rankings": author_rank,
        "unique_authors": len(author_counter),
    }


def analyze_yearly_distribution(papers):
    year_counter = Counter()
    for paper in papers:
        pubdate = paper.get("pubdate", "")
        year_match = re.search(r"(\d{4})", pubdate)
        if year_match:
            year_counter[year_match.group(1)] += 1
    yearly = [{"year": y, "count": c}
              for y, c in sorted(year_counter.items())]
    return {"yearly_distribution": yearly}


def analyze(papers):
    return {
        "meta": {
            "total_papers": len(papers),
            "analyzed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
        "keywords": analyze_keywords(papers),
        "journals": analyze_journals(papers),
        "authors": analyze_authors(papers),
        "yearly": analyze_yearly_distribution(papers),
    }


def main():
    parser = argparse.ArgumentParser(description="顶刊文献文本分析 v2")
    parser.add_argument("--input", "-i", required=True, help="输入 JSON (search_results)")
    parser.add_argument("--output", "-o", default="output", help="输出目录")

    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"[ERROR] 文件不存在: {args.input}", file=sys.stderr)
        sys.exit(1)

    with open(args.input, "r", encoding="utf-8") as f:
        search_results = json.load(f)

    papers = search_results.get("papers", [])
    if not papers:
        print("[WARNING] 无文献数据", file=sys.stderr)
        papers = []

    print(f"[INFO] 分析 {len(papers)} 篇文献...", file=sys.stderr)
    analysis = analyze(papers)

    os.makedirs(args.output, exist_ok=True)
    timestamp = search_results.get("meta", {}).get("timestamp",
                    datetime.now().strftime("%Y%m%d_%H%M%S"))
    filename = f"analysis_results_{timestamp}.json"
    output_path = os.path.join(args.output, filename)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(analysis, f, ensure_ascii=False, indent=2)
    # 同步 latest
    latest_path = os.path.join(args.output, "analysis_results_latest.json")
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(analysis, f, ensure_ascii=False, indent=2)

    print(f"[SUCCESS] 分析结果已保存: {output_path}")
    print(f"[SUCCESS] 最新结果: {latest_path}")

    # 摘要
    kw = analysis["keywords"]
    jl = analysis["journals"]
    al = analysis["authors"]

    print(f"\n分析摘要:")
    print(f"  文献总数: {len(papers)}")
    print(f"  关键词数: {kw['total_unique_keywords']} 个")
    print(f"  期刊种类: {jl['unique_journals']} 种")
    print(f"  作者数量: {al['unique_authors']} 位")

    if kw["keyword_frequency"]:
        print(f"  Top 15 关键词 (TF-IDF 排序):")
        for item in kw["keyword_frequency"][:15]:
            print(f"    · {item['word']} (频率:{item['freq']}, TF-IDF:{item['tfidf']})")

    if al["author_rankings"]:
        print(f"  核心作者:")
        for item in al["author_rankings"][:10]:
            print(f"    · {item['author']}: {item['count']}篇")


if __name__ == "__main__":
    main()
