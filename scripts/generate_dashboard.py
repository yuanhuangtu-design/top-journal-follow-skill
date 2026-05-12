#!/usr/bin/env python3
"""
交互式 HTML 看板生成器
======================
读取检索和分析结果，生成一个独立、交互式、可视化的文献分析 HTML 报告。

用法:
    python3 generate_dashboard.py --search output/search_results.json --analysis output/analysis_results.json --output output/
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>顶刊文献分析看板 — {{TOPIC}}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f0f2f5; color: #1a1a2e; line-height: 1.6; }
.header { background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%); color: white; padding: 2rem 2rem 1.5rem; }
.header h1 { font-size: 1.8rem; margin-bottom: 0.3rem; }
.header .subtitle { opacity: 0.85; font-size: 1rem; }
.header .meta { margin-top: 1rem; display: flex; gap: 1.5rem; flex-wrap: wrap; }
.header .meta-item { background: rgba(255,255,255,0.1); padding: 0.4rem 1rem; border-radius: 20px; font-size: 0.85rem; }
.stats-bar { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 1rem; padding: 1.5rem 2rem; background: white; border-bottom: 1px solid #e0e0e0; }
.stat-card { text-align: center; }
.stat-card .number { font-size: 2rem; font-weight: 700; color: #0f3460; }
.stat-card .label { font-size: 0.85rem; color: #666; margin-top: 0.2rem; }
.container { max-width: 1400px; margin: 0 auto; padding: 1.5rem 2rem; }
.section { background: white; border-radius: 12px; padding: 1.5rem; margin-bottom: 1.5rem; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
.section h2 { font-size: 1.3rem; color: #1a1a2e; margin-bottom: 1rem; padding-bottom: 0.5rem; border-bottom: 2px solid #f0f2f5; display: flex; align-items: center; gap: 0.5rem; }
.section h2 .badge { background: #0f3460; color: white; font-size: 0.7rem; padding: 0.15rem 0.6rem; border-radius: 10px; }
.charts-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; }
.charts-grid .full-width { grid-column: 1 / -1; }
.chart-box { background: #fafbfc; border-radius: 8px; padding: 1rem; border: 1px solid #eaeef2; }
.chart-box h3 { font-size: 0.95rem; color: #333; margin-bottom: 0.8rem; }
.chart-box canvas { width: 100% !important; max-height: 350px; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
thead { background: #1a1a2e; color: white; }
th { padding: 0.6rem 0.8rem; text-align: left; font-weight: 600; white-space: nowrap; }
td { padding: 0.5rem 0.8rem; border-bottom: 1px solid #eaeef2; }
tr:hover { background: #f8f9ff; }
.search-box { margin-bottom: 1rem; }
.search-box input { width: 100%; padding: 0.6rem 1rem; border: 1px solid #ddd; border-radius: 8px; font-size: 0.9rem; }
.keyword-tag { display: inline-block; background: #e8f0fe; color: #0f3460; padding: 0.2rem 0.6rem; border-radius: 12px; font-size: 0.75rem; margin: 0.15rem; }
.query-box { background: #f8f9ff; border-left: 4px solid #0f3460; padding: 1rem; border-radius: 4px; font-family: monospace; font-size: 0.85rem; overflow-x: auto; margin-bottom: 1rem; }
.footer { text-align: center; padding: 1.5rem; color: #999; font-size: 0.8rem; }
@media (max-width: 900px) { .charts-grid { grid-template-columns: 1fr; } .stats-bar { grid-template-columns: repeat(2, 1fr); } }
</style>
</head>
<body>
<div class="header">
    <h1>顶刊文献分析看板</h1>
    <div class="subtitle">{{TOPIC}}</div>
    <div class="meta">
        <span class="meta-item">📚 {{TOTAL_PAPERS}} 篇文献</span>
        <span class="meta-item">📰 {{UNIQUE_JOURNALS}} 种期刊</span>
        <span class="meta-item">👥 {{UNIQUE_AUTHORS}} 位作者</span>
        <span class="meta-item">⏰ {{YEAR_RANGE}}</span>
        <span class="meta-item">📅 生成 {{GENERATED_AT}}</span>
    </div>
</div>
<div class="stats-bar">
    <div class="stat-card"><div class="number">{{TOTAL_PAPERS}}</div><div class="label">文献总数</div></div>
    <div class="stat-card"><div class="number">{{UNIQUE_JOURNALS}}</div><div class="label">期刊种类</div></div>
    <div class="stat-card"><div class="number">{{UNIQUE_AUTHORS}}</div><div class="label">作者数量</div></div>
    <div class="stat-card"><div class="number">{{UNIQUE_KEYWORDS}}</div><div class="label">关键词数量</div></div>
</div>
<div class="container">
{{QUERY_SECTION}}
<div class="charts-grid">{{CHARTS}}</div>
<div class="section">
    <h2>🏷️ 高频关键词 Top 30</h2>
    <div class="keyword-cloud">{{KEYWORD_TAGS}}</div>
</div>
<div class="section" id="papers">
    <h2>📋 文献明细表 <span class="badge">{{TOTAL_PAPERS}}篇</span></h2>
    <div class="search-box"><input type="text" id="paperSearch" placeholder="搜索标题、作者、期刊..." oninput="filterPapers()"></div>
    <div class="table-wrap">
        <table id="paperTable">
            <thead><tr><th>#</th><th>标题</th><th>期刊</th><th>年份</th><th>作者</th><th>DOI / 链接</th></tr></thead>
            <tbody>{{PAPER_ROWS}}</tbody>
        </table>
    </div>
</div>
</div>
<div class="footer">由 顶刊文献追踪技能 自动生成 | {{GENERATED_AT}}</div>
<script>
const chartInstances = {};
function createChart(id, type, labels, dataset, options) {
    const ctx = document.getElementById(id).getContext('2d');
    if (chartInstances[id]) chartInstances[id].destroy();
    chartInstances[id] = new Chart(ctx, { type, data: { labels, datasets: [dataset] }, options: Object.assign({ responsive: true, maintainAspectRatio: true, plugins: { legend: { display: !!dataset.label, position: 'bottom' } } }, options || {}) });
}
function filterPapers() {
    const q = document.getElementById('paperSearch').value.toLowerCase();
    document.querySelectorAll('#paperTable tbody tr').forEach(r => { r.style.display = r.textContent.toLowerCase().includes(q) ? '' : 'none'; });
}
window.addEventListener('load', function() { {{CHART_SCRIPTS}} });
</script>
</body>
</html>
"""


def generate_chart_scripts(analysis):
    scripts = []
    kw_list = analysis.get("keywords", {}).get("keyword_frequency", [])
    jl_list = analysis.get("journals", {}).get("journal_distribution", [])
    al_list = analysis.get("authors", {}).get("author_rankings", [])
    yl_list = analysis.get("yearly", {}).get("yearly_distribution", [])

    if kw_list:
        top = kw_list[:20]
        scripts.append(f"""createChart('chart_kw','bar',{json.dumps([k['word'] for k in top][::-1])},{{label:'Frequency',data:{json.dumps([k['freq'] for k in top][::-1])},backgroundColor:'rgba(15,52,96,0.7)',borderWidth:1}},{{indexAxis:'y',scales:{{y:{{ticks:{{fontSize:10}}}}}}}});""")
    if jl_list:
        labs = json.dumps([(j['journal'][:20]+'...') if len(j['journal'])>20 else j['journal'] for j in jl_list])
        dat = json.dumps([j['count'] for j in jl_list])
        scripts.append(f"""createChart('chart_jl','doughnut',{labs},{{data:{dat},backgroundColor:['#0f3460','#16213e','#1a5276','#2980b9','#3498db','#5dade2','#85c1e9','#aed6f1','#d4e6f1','#eaf2f8'],borderWidth:1}},{{plugins:{{legend:{{position:'right',labels:{{fontSize:10,boxWidth:12}}}}}}}});""")
    if al_list:
        top = al_list[:15]
        scripts.append(f"""createChart('chart_al','bar',{json.dumps([a['author'] for a in top][::-1])},{{label:'Papers',data:{json.dumps([a['count'] for a in top][::-1])},backgroundColor:'rgba(230,126,34,0.7)',borderWidth:1}},{{indexAxis:'y',scales:{{y:{{ticks:{{fontSize:9}}}}}}}});""")
    if yl_list and len(yl_list) > 1:
        scripts.append(f"""createChart('chart_yl','line',{json.dumps([y['year'] for y in yl_list])},{{label:'Papers per Year',data:{json.dumps([y['count'] for y in yl_list])},borderColor:'#0f3460',backgroundColor:'rgba(15,52,96,0.1)',fill:true,tension:0.3,pointBackgroundColor:'#0f3460',pointRadius:5}},{{scales:{{y:{{beginAtZero:true,ticks:{{stepSize:1}}}}}}}});""")
    return '\n'.join(scripts)


def generate_keyword_tags(kw_list, max_items=30):
    if not kw_list:
        return "<p>暂无关键词数据</p>"
    tags = []
    max_freq = max(k["freq"] for k in kw_list[:max_items])
    for k in kw_list[:max_items]:
        scale = 0.8 + (k["freq"] / max_freq) * 1.2
        opacity = 0.5 + (k["freq"] / max_freq) * 0.5
        tags.append(f'<span class="keyword-tag" style="font-size:{scale:.2f}rem;opacity:{opacity:.2f};">{k["word"]}</span>')
    return " ".join(tags)


def generate_paper_rows(papers):
    if not papers:
        return "<tr><td colspan='6'>暂无文献数据</td></tr>"
    rows = []
    for i, p in enumerate(papers, 1):
        title = p.get("title", "Untitled").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        journal = p.get("journal", "N/A")
        year = re.search(r"(\d{4})", p.get("pubdate", ""))
        year_str = year.group(1) if year else "N/A"
        authors = p.get("authors", [])
        author_str = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
        doi = p.get("doi", "")
        pmid = p.get("pmid", "")
        link = p.get("url", f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/")
        link_text = f'DOI: <a href="https://doi.org/{doi}" target="_blank">{doi[:25]}</a>' if doi else f'<a href="{link}" target="_blank">PMID: {pmid}</a>' if pmid else "N/A"
        rows.append(f"<tr><td>{i}</td><td><strong>{title}</strong></td><td>{journal}</td><td>{year_str}</td><td>{author_str}</td><td style='font-size:0.8rem;'>{link_text}</td></tr>")
    return "\n".join(rows)


def generate_query_section(sr):
    # 兼容 v1 和 v2 格式
    meta = sr.get("meta", {})
    query = meta.get("query_pubmed") or meta.get("query_raw") or sr.get("query", "N/A")
    query_raw = meta.get("query_raw", "")
    query_type = meta.get("query_type", "")
    total = sr.get("total_results", 0)
    retrieved = sr.get("retrieved", 0)
    journals = ", ".join(sr.get("journals_searched", [])) or "未指定"
    years = sr.get("years_range", "") or "未指定"
    type_tag = f'<span style="background:#e8f0fe;padding:0.2rem 0.6rem;border-radius:10px;font-size:0.8rem;">{query_type}</span>' if query_type else ""
    raw_tag = f'<p style="font-size:0.85rem;color:#888;margin-top:0.3rem;">原始输入: {query_raw}</p>' if query_raw else ""
    return f"""<div class="section"><h2>🔍 检索策略 {type_tag}</h2><div class="query-box">{query}</div>{raw_tag}<div style="display:flex;gap:2rem;font-size:0.9rem;color:#555;flex-wrap:wrap;"><span><strong>目标期刊：</strong>{journals}</span><span><strong>时间范围：</strong>{years}</span><span><strong>命中总数：</strong>{total} 篇</span><span><strong>已获取：</strong>{retrieved} 篇</span></div></div>"""


def generate_charts_html(analysis):
    charts = []
    if analysis.get("keywords", {}).get("keyword_frequency"):
        charts.append('<div class="chart-box"><h3>🔑 关键词频率 Top 20</h3><canvas id="chart_kw"></canvas></div>')
    if analysis.get("journals", {}).get("journal_distribution"):
        charts.append('<div class="chart-box"><h3>📰 期刊分布</h3><canvas id="chart_jl"></canvas></div>')
    if analysis.get("authors", {}).get("author_rankings"):
        charts.append('<div class="chart-box"><h3>👥 核心作者 Top 15</h3><canvas id="chart_al"></canvas></div>')
    if len(analysis.get("yearly", {}).get("yearly_distribution", [])) > 1:
        charts.append('<div class="chart-box"><h3>📈 年度发文趋势</h3><canvas id="chart_yl"></canvas></div>')
    return "\n".join(charts)


def main():
    parser = argparse.ArgumentParser(description="顶刊文献交互式 HTML 看板生成器")
    parser.add_argument("--search", "-s", required=True, help="检索结果 JSON")
    parser.add_argument("--analysis", "-a", required=True, help="分析结果 JSON")
    parser.add_argument("--output", "-o", default="output", help="输出目录")
    parser.add_argument("--topic", "-t", default="文献检索分析报告", help="看板主题")

    args = parser.parse_args()

    try:
        with open(args.search, "r", encoding="utf-8") as f:
            sr = json.load(f)
    except Exception as e:
        print(f"[ERROR] 无法读取检索结果文件: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(args.analysis, "r", encoding="utf-8") as f:
            an = json.load(f)
    except Exception as e:
        print(f"[ERROR] 无法读取分析结果文件: {e}", file=sys.stderr)
        sys.exit(1)

    papers = sr.get("papers", [])
    kw_data = an.get("keywords", {})
    jl_data = an.get("journals", {})
    al_data = an.get("authors", {})

    # 获取时间戳（兼容 v1/v2）
    ts = sr.get("meta", {}).get("timestamp") or datetime.now().strftime("%Y%m%d_%H%M%S")

    html = HTML_TEMPLATE.replace("{{TOPIC}}", args.topic)
    html = html.replace("{{TOTAL_PAPERS}}", str(len(papers)))
    html = html.replace("{{UNIQUE_JOURNALS}}", str(jl_data.get("unique_journals", 0)))
    html = html.replace("{{UNIQUE_AUTHORS}}", str(al_data.get("unique_authors", 0)))
    html = html.replace("{{UNIQUE_KEYWORDS}}", str(kw_data.get("total_unique_keywords", 0)))
    html = html.replace("{{YEAR_RANGE}}", str(sr.get("years_range", "N/A")))
    html = html.replace("{{GENERATED_AT}}", datetime.now().strftime("%Y-%m-%d %H:%M"))
    html = html.replace("{{QUERY_SECTION}}", generate_query_section(sr))
    html = html.replace("{{CHARTS}}", generate_charts_html(an))
    html = html.replace("{{CHART_SCRIPTS}}", generate_chart_scripts(an))
    html = html.replace("{{KEYWORD_TAGS}}", generate_keyword_tags(kw_data.get("keyword_frequency", [])))
    html = html.replace("{{PAPER_ROWS}}", generate_paper_rows(papers))

    os.makedirs(args.output, exist_ok=True)
    filename = f"literature_dashboard_{ts}.html"
    path = os.path.join(args.output, filename)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        # 同步 latest（与 Web UI 兼容）
        late