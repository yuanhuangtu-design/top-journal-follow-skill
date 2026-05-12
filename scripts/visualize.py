#!/usr/bin/env python3
"""
可视化生成工具 — 从分析结果生成图表
==================================
基于 analysis_results.json 生成各类可视化图表。

用法:
    python3 visualize.py --input output/analysis_results.json --output output/
"""

import argparse
import json
import os
import sys

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.font_manager as fm
    import numpy as np
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False
    print("[WARNING] matplotlib 未安装，图表生成将跳过", file=sys.stderr)

try:
    from wordcloud import WordCloud
    HAS_WORDCLOUD = True
except ImportError:
    HAS_WORDCLOUD = False
    print("[WARNING] wordcloud 未安装，词云将跳过", file=sys.stderr)


def setup_chinese_font():
    """尝试设置中文字体"""
    font_candidates = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ]
    for font_path in font_candidates:
        if os.path.exists(font_path):
            font_prop = fm.FontProperties(fname=font_path)
            plt.rcParams["font.family"] = font_prop.get_name()
            return font_prop

    plt.rcParams["font.sans-serif"] = ["DejaVu Sans", "Arial", "Helvetica"]
    plt.rcParams["axes.unicode_minus"] = False
    return None


def draw_keyword_bar(keywords, output_dir):
    """关键词频率柱状图 (Top 20)"""
    if not keywords:
        return None
    fig, ax = plt.subplots(figsize=(12, 8))
    top_kw = keywords[:20]
    words = [k["word"] for k in top_kw][::-1]
    freqs = [k["freq"] for k in top_kw][::-1]
    colors = plt.cm.Blues(np.linspace(0.4, 0.9, len(words)))
    bars = ax.barh(range(len(words)), freqs, color=colors, edgecolor="grey", linewidth=0.5)
    ax.set_yticks(range(len(words)))
    ax.set_yticklabels(words, fontsize=10)
    ax.set_xlabel("Frequency", fontsize=12)
    ax.set_title("Top 20 Keywords", fontsize=14, fontweight="bold")
    for bar, freq in zip(bars, freqs):
        ax.text(bar.get_width() + max(freqs) * 0.01, bar.get_y() + bar.get_height() / 2,
                str(freq), va="center", fontsize=9)
    plt.tight_layout()
    path = os.path.join(output_dir, "keyword_bar.png")
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return path


def draw_journal_bar(journals, output_dir):
    """期刊分布柱状图"""
    if not journals:
        return None
    fig, ax = plt.subplots(figsize=(12, 7))
    names = [j["journal"] for j in journals]
    counts = [j["count"] for j in journals]
    colors = plt.cm.Set2(np.linspace(0, 1, len(names)))
    bars = ax.bar(range(len(names)), counts, color=colors, edgecolor="grey", linewidth=0.5)
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels([n[:25] + "..." if len(n) > 25 else n for n in names],
                       rotation=30, ha="right", fontsize=9)
    ax.set_ylabel("Number of Papers", fontsize=12)
    ax.set_title("Journal Distribution", fontsize=14, fontweight="bold")
    for bar, count in zip(bars, counts):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.1,
                str(count), ha="center", va="bottom", fontsize=8)
    plt.tight_layout()
    path = os.path.join(output_dir, "journal_bar.png")
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return path


def draw_author_bar(authors, output_dir):
    """作者活跃度排名 (Top 15)"""
    if not authors:
        return None
    fig, ax = plt.subplots(figsize=(10, 7))
    top_authors = authors[:15]
    names = [a["author"] for a in top_authors][::-1]
    counts = [a["count"] for a in top_authors][::-1]
    colors = plt.cm.Oranges(np.linspace(0.3, 0.9, len(names)))
    ax.barh(range(len(names)), counts, color=colors, edgecolor="grey", linewidth=0.5)
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=9)
    ax.set_xlabel("Number of Papers", fontsize=12)
    ax.set_title("Top Authors by Publication Frequency", fontsize=14, fontweight="bold")
    plt.tight_layout()
    path = os.path.join(output_dir, "author_bar.png")
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return path


def draw_yearly_bar(yearly, output_dir):
    """年度分布图"""
    if not yearly:
        return None
    fig, ax = plt.subplots(figsize=(8, 5))
    years = [y["year"] for y in yearly]
    counts = [y["count"] for y in yearly]
    ax.plot(range(len(years)), counts, marker="o", linewidth=2, color="steelblue", markersize=8)
    ax.fill_between(range(len(years)), counts, alpha=0.2, color="steelblue")
    ax.set_xticks(range(len(years)))
    ax.set_xticklabels(years, fontsize=10)
    ax.set_ylabel("Number of Papers", fontsize=12)
    ax.set_title("Yearly Publication Trend", fontsize=14, fontweight="bold")
    for i, c in enumerate(counts):
        ax.text(i, c + 0.3, str(c), ha="center", fontsize=10)
    plt.tight_layout()
    path = os.path.join(output_dir, "yearly_bar.png")
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return path


def draw_wordcloud(keywords, output_dir):
    """生成词云"""
    if not HAS_WORDCLOUD or not keywords:
        return None
    freq_dict = {k["word"]: k["freq"] for k in keywords[:80]}
    wc = WordCloud(width=1600, height=900, background_color="white",
                   max_words=100, colormap="viridis", prefer_horizontal=0.7,
                   min_font_size=10, max_font_size=120, random_state=42)
    wc.generate_from_frequencies(freq_dict)
    fig, ax = plt.subplots(figsize=(16, 9))
    ax.imshow(wc, interpolation="bilinear")
    ax.axis("off")
    ax.set_title("Keyword Cloud — Research Hotspots", fontsize=16, fontweight="bold", pad=20)
    plt.tight_layout()
    path = os.path.join(output_dir, "wordcloud.png")
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return path


def main():
    parser = argparse.ArgumentParser(description="顶刊文献可视化生成工具")
    parser.add_argument("--input", "-i", required=True, help="输入分析结果 JSON")
    parser.add_argument("--output", "-o", default="output", help="输出目录")
    parser.add_argument("--charts-dir", default="charts", help="图表子目录")

    args = parser.parse_args()

    if not HAS_MATPLOTLIB:
        print("[ERROR] matplotlib 未安装", file=sys.stderr)
        sys.exit(1)

    with open(args.input, "r", encoding="utf-8") as f:
        analysis = json.load(f)

    charts_dir = os.path.join(args.output, args.charts_dir)
    os.makedirs(charts_dir, exist_ok=True)

    setup_chinese_font()
    generated = []

    kw_list = analysis.get("keywords", {}).get("keyword_frequency", [])
    jl_list = analysis.get("journals", {}).get("journal_distribution", [])
    al_list = analysis.get("authors", {}).get("author_rankings", [])
    yl_list = analysis.get("yearly", {}).get("yearly_distribution", [])

    for fn, data in [
        (draw_keyword_bar, kw_list),
        (draw_journal_bar, jl_list),
        (draw_author_bar, al_list),
        (draw_yearly_bar, yl_list),
        (draw_wordcloud, kw_list),
    ]:
        path = fn(data, charts_dir)
        if path:
            generated.append(path)

    manifest = {"charts": generated, "charts_dir": charts_dir}
    with open(os.path.join(charts_dir, "charts_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"[SUMMARY] 共生成 {len(generated)} 个图表")
    for p in generated:
        print(f"  - {p}")


if __name__ == "__main__":
    main()
