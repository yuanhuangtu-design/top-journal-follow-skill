#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
顶刊文献日报生成器（GitHub Actions 每日运行）
============================================
流程：
  1. 计算北京日期：日报日期 = 今天，目标 PubMed EDAT = 最近三天 + 持久化补抓日期
  2. 组A（高影响力期刊 × Epilepsy）+ 组B1/B2（癫痫专科/神经科学 × Epilepsy）+ 组C（脑电方法学）检索
  3. 合并 → 按 PMID 全局去重
  4. 相关性打分 → 优先级分级（必读 / 推荐 / 浏览）
  5. 标题与摘要中英翻译（直接调用MyMemory API，按句分段、缓存、限时，失败留待后续补翻）
  6. 生成结构化日报 JSON + 独立 HTML 日报
  7. 写入 Notion（提供 NOTION_TOKEN / NOTION_PARENT_PAGE_ID 时）
  8. 写入飞书多维表格（提供 FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_APP_TOKEN 时）

用法:
    python scripts/run_daily.py
环境变量:
    NOTION_TOKEN             Notion 集成 token（可选，缺省则跳过 Notion 写入）
    NOTION_PARENT_PAGE_ID    目标父页面 ID（可选）
    NOTION_PARENT_TYPE       page 或 database（默认 page）
    FEISHU_APP_ID            飞书自建应用 App ID（同步时必填）
    FEISHU_APP_SECRET        飞书自建应用 App Secret（同步时必填）
    FEISHU_APP_TOKEN         飞书多维表格 app_token（同步时必填）
    FEISHU_TABLE_ID          飞书多维表格 table_id（同步时必填）
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "daily_config.json")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def beijing_today():
    from datetime import timezone
    return (datetime.now(timezone.utc) + timedelta(hours=8)).date()


def merge_dedupe(groups):
    by_pmid = {}
    for gname, res in groups.items():
        if not res: continue
        for paper in res.get("papers", []):
            pmid = str(paper.get("pmid", "")).strip()
            if not pmid: continue
            if pmid not in by_pmid:
                paper["_hit_groups"] = []; paper["_group_count"] = 0; by_pmid[pmid] = paper
            by_pmid[pmid]["_hit_groups"].append(gname)
            by_pmid[pmid]["_group_count"] = len(by_pmid[pmid]["_hit_groups"])
    return list(by_pmid.values())


def relevance_score(paper, boost_words):
    title = (paper.get("title") or "").lower()
    text = title + " " + (paper.get("abstract") or "").lower()
    concepts = {"seizures": "seizure", "epileptic": "epilepsy", "convulsive": "convulsion"}
    seen, hits, score = set(), [], 0
    title_bonus = False
    for word in boost_words:
        concept = concepts.get(word, word)
        if concept in seen:
            continue
        aliases = [w for w in boost_words if concepts.get(w, w) == concept]
        patterns = [r"\b" + re.escape(w) + (r"\w*\b" if w == "electroencephalogr" else r"\b") for w in aliases]
        pattern = "(?:" + "|".join(patterns) + ")"
        matches = re.findall(pattern, text)
        if matches:
            score += min(len(matches), 2)
            hits.append(concept)
            seen.add(concept)
        if concept in ("epilepsy", "seizure") and re.search(pattern, title):
            title_bonus = True
    if title_bonus:
        score += 5
    return score, hits


def grade_paper(score):
    if score >= 8: return "必读"
    if score >= 3: return "推荐"
    return "浏览"


def build_report(groups, papers, config, report_date, edat_date):
    meta = {"report_date": str(report_date), "target_edat": str(edat_date), "pipeline": "PubMed E-utilities",
            "source": "NCBI PubMed（GitHub Actions 定时抓取）", "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    group_records = []
    for gname, res in groups.items():
        label = config["group_labels"].get(gname, gname)
        if not res:
            group_records.append({"group": gname, "label": label, "count": 0, "pmids": [], "status": "failed"}); continue
        pmids = [p.get("pmid") for p in res.get("papers", [])]
        group_records.append({"group": gname, "label": label, "count": res.get("retrieved", 0),
                              "total": res.get("total_results", 0), "pmids": pmids, "status": res.get("status", "ok")})
    paper_list = []
    for idx, p in enumerate(papers, 1):
        score, hits = relevance_score(p, config["relevance_boost_words"])
        paper_list.append({"index": idx, "pmid": p.get("pmid", ""), "title": p.get("title", ""),
            "title_zh": p.get("_title_zh", ""), "journal": p.get("journal", ""), "iso_journal": p.get("iso_journal", ""),
            "authors": p.get("authors", []), "pubdate": p.get("pubdate", ""), "doi": p.get("doi", ""),
            "url": p.get("url", ""), "abstract": p.get("abstract", ""), "abstract_zh": p.get("_abstract_zh", ""),
            "abstract_status": p.get("abstract_status", "pending"), "first_seen": p.get("first_seen", str(report_date)),
            "hit_groups": p.get("_hit_groups", []), "group_count": p.get("_group_count", 0),
            "relevance_score": score, "relevance_hits": hits[:8], "priority": grade_paper(score)})
    return {"meta": meta, "group_records": group_records, "dedup_total": len(papers), "papers": paper_list}

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>顶刊文献日报｜{{REPORT_DATE}}</title>
<style>*{margin:0;padding:0;box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;background:#f4f6f9;color:#1a1a2e;line-height:1.65}.header{background:linear-gradient(135deg,#1a1a2e,#0f3460);color:#fff;padding:1.8rem 2rem 1.4rem}.header h1{font-size:1.5rem}.header .sub{opacity:.85;font-size:.95rem;margin-top:.3rem}.meta{margin-top:1rem;display:flex;gap:.6rem;flex-wrap:wrap}.meta span{background:rgba(255,255,255,.12);padding:.25rem .8rem;border-radius:20px;font-size:.8rem}.container{max-width:960px;margin:0 auto;padding:1.2rem 1.5rem}.card{background:#fff;border-radius:10px;padding:1.2rem 1.4rem;margin-bottom:1.2rem;box-shadow:0 1px 5px rgba(0,0,0,.06)}.card h2{font-size:1.05rem;border-bottom:2px solid #eef1f5;padding-bottom:.5rem;margin-bottom:.8rem;color:#0f3460}table{width:100%;border-collapse:collapse;font-size:.85rem}th{background:#1a2a4a;color:#fff;padding:.45rem .6rem;text-align:left}td{padding:.45rem .6rem;border-bottom:1px solid #eef1f5}.paper{border:1px solid #e6eaf0;border-left:4px solid #0f3460;border-radius:8px;padding:1rem 1.1rem;margin-bottom:1rem;background:#fff}.paper .p-title{font-weight:700;font-size:.98rem}.paper .p-title-zh{color:#0f3460;margin:.3rem 0 .5rem;font-size:.92rem}.paper .p-meta{font-size:.8rem;color:#555;margin-bottom:.6rem;word-break:break-all}.paper .p-abs{font-size:.85rem;color:#333;margin-bottom:.5rem}.paper .p-abs-zh{font-size:.85rem;color:#0f3460}.badge{display:inline-block;padding:.1rem .55rem;border-radius:12px;font-size:.72rem;font-weight:600;color:#fff;margin-left:.4rem}.badge-must{background:#c0392b}.badge-rec{background:#e67e22}.badge-browse{background:#7f8c8d}.empty{color:#999;font-size:.9rem;padding:.6rem 0}.footer{text-align:center;padding:1.5rem;color:#999;font-size:.78rem}</style>
</head><body><div class="header"><h1>顶刊文献日报</h1><div class="sub">检索口径：PubMed Entry Date [EDAT]（{{TARGET_EDAT}}）</div>
<div class="meta"><span>日报日期 {{REPORT_DATE}}</span><span>去重后 {{DEDUP_TOTAL}} 篇</span><span>数据来源 {{SOURCE}}</span><span>生成 {{GENERATED_AT}}</span></div></div>
<div class="container"><div class="card">{{RUN_SUMMARY}}</div><div class="card"><h2>PubMed 检索记录</h2><table><tr><th>检索</th><th>范围</th><th>状态</th><th>Count</th><th>PMID</th></tr>{{GROUP_ROWS}}</table>
<p style="font-size:.9rem;margin-top:.8rem;"><strong>全局去重后文献数：{{DEDUP_TOTAL}}</strong></p></div>{{PAPER_SECTIONS}}</div>
<div class="footer">由 top-journal-follow-skill + GitHub Actions 自动生成</div></body></html>"""

def esc(s):
    if not s: return ""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def render_html(report, groups):
    group_rows = []
    for gr in report["group_records"]:
        label = gr["label"]; status = "✓" if gr["status"] == "ok" else "✗"; count = gr.get("count", 0)
        pmid_str = ", ".join(str(x) for x in gr.get("pmids", [])[:15])
        if gr.get("total", 0) > len(gr.get("pmids", [])): pmid_str += " …"
        group_rows.append(f"<tr><td>{status}</td><td>{esc(label)}</td><td>{'完成' if gr['status']=='ok' else '部分完成，待重试' if gr['status']=='partial' else '失败'}</td><td>{count}</td><td style='font-size:.75rem;'>{esc(pmid_str) or '无'}</td></tr>")
    sections = []
    for gr in report["group_records"]:
        label = gr["label"]; papers_in_group = [p for p in report["papers"] if gr["group"] in p["hit_groups"]]
        html_papers = []
        for p in papers_in_group:
            badge = f'<span class="badge badge-must">{p["priority"]}</span>' if p["priority"] == "必读" else (f'<span class="badge badge-rec">{p["priority"]}</span>' if p["priority"] == "推荐" else f'<span class="badge badge-browse">{p["priority"]}</span>')
            authors = ", ".join(p["authors"][:4]) + (" et al." if len(p["authors"]) > 4 else "")
            links = f'<a href="{esc(p["url"])}" target="_blank">PubMed</a>' + (f' · <a href="https://doi.org/{esc(p["doi"])}" target="_blank">DOI</a>' if p["doi"] else "")
            meta_html = f"PMID {p['pmid']} · {esc(p['journal'])} · {esc(p['pubdate'])}<br>作者：{esc(authors)}<br>链接：{links}<br>命中检索：{', '.join(report['group_records'][i]['label'] for i, g in enumerate(report['group_records']) if g['group'] in p['hit_groups'])}"
            abs_html = f'<div class="p-abs"><strong>摘要：</strong>{esc(p["abstract"])}</div>' if p["abstract"] else ('<div class="empty">PubMed 未提供摘要</div>' if p.get("abstract_status") == "not_provided" else '<div class="empty">摘要待补全</div>')
            abs_zh_html = f'<div class="p-abs-zh"><strong>中文摘要：</strong>{esc(p["abstract_zh"])}</div>' if p.get("abstract_zh") else ""
            title_zh = f'<div class="p-title-zh">{esc(p["title_zh"])}</div>' if p.get("title_zh") else ""
            html_papers.append(f'<div class="paper"><div class="p-title">{esc(p["title"])}{badge}</div>{title_zh}<div class="p-meta">{meta_html}</div>{abs_html}{abs_zh_html}</div>')
        body = "\n".join(html_papers) if html_papers else '<div class="empty">暂无文献更新</div>'
        sections.append(f'<div class="card"><h2>📄 {esc(label)}（{len(papers_in_group)} 篇）</h2>{body}</div>')
    html = HTML_TEMPLATE.replace("{{REPORT_DATE}}", report["meta"]["report_date"]).replace("{{TARGET_EDAT}}", report["meta"]["target_edat"]).replace("{{DEDUP_TOTAL}}", str(report["dedup_total"])).replace("{{SOURCE}}", esc(report["meta"]["source"])).replace("{{GENERATED_AT}}", esc(report["meta"]["generated_at"])).replace("{{GROUP_ROWS}}", "\n".join(group_rows)).replace("{{PAPER_SECTIONS}}", "\n".join(sections))
    status = esc(report.get("run_status", "unknown"))
    summary = f"运行状态：{status}；待补详情：{report.get('pending_details', 0)}；待翻译文献：{report.get('pending_translation', 0)}。"
    return html.replace("{{RUN_SUMMARY}}", summary)

def save_json(report, report_date):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, f"daily_report_{report_date}.json")
    with open(path, "w", encoding="utf-8") as f: json.dump(report, f, ensure_ascii=False, indent=2)
    latest = os.path.join(OUTPUT_DIR, "daily_report_latest.json")
    with open(latest, "w", encoding="utf-8") as f: json.dump(report, f, ensure_ascii=False, indent=2)
    return path

def save_html(html, report_date):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, f"daily_report_{report_date}.html")
    with open(path, "w", encoding="utf-8") as f: f.write(html)
    latest = os.path.join(OUTPUT_DIR, "daily_report_latest.html")
    with open(latest, "w", encoding="utf-8") as f: f.write(html)
    return path

def notion_headers(token):
    return {"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION, "Content-Type": "application/json"}

def notion_request(method, path, token, payload=None):
    url = NOTION_API + path; data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=notion_headers(token), method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp: return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e: print(f"[NOTION-ERROR] HTTP {e.code}", file=sys.stderr); return None
    except Exception as e: print(f"[NOTION-ERROR] {e}", file=sys.stderr); return None

def text_block(block_type, content):
    if not content: return None
    return {"object": "block", "type": block_type, block_type: {"rich_text": [{"type": "text", "text": {"content": content[:1950]}}]}}

def build_notion_children(report):
    blocks = []; meta = report["meta"]
    for line in (f"日报日期：{meta['report_date']}", f"目标 PubMed EDAT：{meta['target_edat']}", "检索口径：PubMed Entry Date [EDAT]", f"数据来源：{meta['source']}", f"抓取状态：{report.get('run_status', 'unknown')}"):
        b = text_block("paragraph", line)
        if b: blocks.append(b)
    blocks.append({"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "text": {"content": "PubMed 检索记录"}}]}})
    for gr in report["group_records"]:
        label = gr["label"]
        if gr["status"] == "ok":
            pmid_str = ", ".join(str(x) for x in gr.get("pmids", [])) if gr.get("pmids") else "无"
            line = f"检索 {gr['group']}：{label} — Count：{gr.get('count', 0)} — PMID：{pmid_str}"
        else: line = f"检索 {gr['group']}：{label} — 失败"
        b = text_block("bulleted_list_item", line)
        if b: blocks.append(b)
    blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": f"全局去重后文献数：{report['dedup_total']}"}}]}})
    for gr in report["group_records"]:
        label = gr["label"]; papers = [p for p in report["papers"] if gr["group"] in p["hit_groups"]]
        blocks.append({"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "text": {"content": f"{label}（{len(papers)} 篇）"}}]}})
        if not papers:
            b = text_block("paragraph", "暂无文献更新")
            if b: blocks.append(b)
        for p in papers:
            blocks.append({"object": "block", "type": "heading_3", "heading_3": {"rich_text": [{"type": "text", "text": {"content": f"文献 {p['index']}：{p['title'][:190]}"}}]}})
            for line in ((f"中文标题：{p.get('title_zh')}" if p.get("title_zh") else None), f"PMID：{p['pmid']}", f"期刊：{p['journal']}", f"作者：{'；'.join(p['authors'][:6]) + (' 等' if len(p['authors']) > 6 else '')}", (f"DOI：{p['doi']}" if p.get("doi") else None), f"PubMed：{p['url']}", f"命中检索：{'、'.join(gr['label'] for gr in report['group_records'] if gr['group'] in p['hit_groups'])}", f"优先级：{p['priority']}（相关度得分 {p['relevance_score']}）"):
                if line:
                    b = text_block("bulleted_list_item", line)
                    if b: blocks.append(b)
            if p.get("abstract"):
                b = text_block("paragraph", "英文摘要：" + p["abstract"])
                if b: blocks.append(b)
            if p.get("abstract_zh"):
                b = text_block("paragraph", "中文摘要：" + p["abstract_zh"])
                if b: blocks.append(b)
    return blocks

def write_notion(report):
    token = os.environ.get("NOTION_TOKEN", "").strip(); parent_id = os.environ.get("NOTION_PARENT_PAGE_ID", "").strip()
    if not token or not parent_id: print("[NOTION-SKIP] 未配置Notion，跳过"); return None
    parent_type = os.environ.get("NOTION_PARENT_TYPE", "page").strip()
    children = build_notion_children(report); page_title = f"文献日报｜{report['meta']['report_date']}"
    payload = {"parent": {parent_type: parent_id}, "properties": {"title": {"title": [{"type": "text", "text": {"content": page_title}}]}}, "children": children[:100]}
    created = notion_request("POST", "/pages", token, payload)
    if not created: print("[NOTION-FAIL] 页面创建失败", file=sys.stderr); return None
    page_id = created.get("id", ""); rest = children[100:]
    while rest:
        chunk, rest = rest[:100], rest[100:]
        if not notion_request("PATCH", f"/blocks/{page_id}/children", token, {"children": chunk}):
            return None
    print(f"[NOTION-OK] 已创建页面: {page_title}")
    return created.get("url", page_id)

def main():
    from daily_pipeline import run
    return run(sys.modules[__name__])


if __name__ == "__main__":
    sys.exit(main())
