#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
顶刊文献日报生成器（GitHub Actions 每日运行）
============================================
流程：
  1. 计算北京日期：日报日期 = 今天，目标 PubMed EDAT = 昨天
  2. 组A（高影响力期刊 × Epilepsy）+ 组B1/B2（癫痫专科/神经科学 × Epilepsy）+ 组C（脑电方法学）检索
  3. 合并 → 按 PMID 全局去重
  4. 相关性打分 → 优先级分级（必读 / 推荐 / 浏览）
  5. 标题与摘要中英翻译（直接调用MyMemory API，失败自动重试，仍失败则降级为原文并告警）
  6. 生成结构化日报 JSON + 独立 HTML 日报
  7. 写入 Notion（提供 NOTION_TOKEN / NOTION_PARENT_PAGE_ID 时）
  8. 写入飞书多维表格（提供 FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_APP_TOKEN 时）

用法:
    python scripts/run_daily.py
环境变量:
    NOTION_TOKEN             Notion 集成 token（可选，缺省则跳过 Notion 写入）
    NOTION_PARENT_PAGE_ID    目标父页面 ID（可选）
    NOTION_PARENT_TYPE       page 或 database（默认 page）
    FEISHU_APP_ID            飞书自建应用 App ID（可选）
    FEISHU_APP_SECRET        飞书自建应用 App Secret（可选）
    FEISHU_APP_TOKEN         飞书多维表格 app_token（可选）
    FEISHU_TABLE_ID          飞书多维表格 table_id（可选，缺省取第一个表）
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


# ==================== 日期 ====================

def beijing_today():
    """GitHub runner 为 UTC，+8 得到北京日期。"""
    from datetime import timezone
    return (datetime.now(timezone.utc) + timedelta(hours=8)).date()


# ==================== 检索 ====================

def run_search(query, journals, outdir, max_results, retries=3):
    os.makedirs(outdir, exist_ok=True)
    cmd = [
        sys.executable,
        os.path.join(BASE_DIR, "scripts", "literature_search.py"),
        "--query", query,
        "--journals", journals,
        "--max", str(max_results),
        "--output", outdir,
    ]
    for attempt in range(1, retries + 1):
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if proc.stdout:
            print(proc.stdout)
        api_warning = "WARNING" in (proc.stderr or "")
        if proc.returncode == 0 and not api_warning:
            latest = os.path.join(outdir, "search_results_latest.json")
            if os.path.exists(latest):
                with open(latest, "r", encoding="utf-8") as f:
                    return json.load(f)
            print(f"[WARN] 未找到检索结果: {latest}", file=sys.stderr)
        else:
            if api_warning:
                print("[WARN] 检测到 PubMed API 请求异常（网络不稳定）", file=sys.stderr)
            print(proc.stderr, file=sys.stderr)
        if attempt < retries:
            print(f"[RETRY] 检索失败，{attempt * 10} 秒后重试（第 {attempt + 1}/{retries} 次）...")
            time.sleep(attempt * 10)
    return None


def refetch_missing_abstracts(papers):
    """摘要抓取偶发断连时补抓一次（NCBI 网络不稳定场景）。"""
    import xml.etree.ElementTree as ET
    for p in papers:
        if p.get("abstract"):
            continue
        for _ in range(2):
            url = ("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?"
                   f"db=pubmed&id={p['pmid']}&retmode=xml&rettype=abstract")
            req = urllib.request.Request(url, headers={"User-Agent": "TopJournalSkill/2.0 (research tool)"})
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    xml_text = resp.read().decode("utf-8")
                root = ET.fromstring(xml_text)
                parts = []
                for at in root.iter("AbstractText"):
                    label = at.get("Label", "")
                    text = (at.text or "")
                    for child in at:
                        if child.text:
                            text += " " + child.text
                        if child.tail:
                            text += " " + child.tail
                    parts.append((f"{label}: {text.strip()}" if label else text.strip()))
                if parts:
                    p["abstract"] = "\n".join(parts)
                    break
            except Exception:
                time.sleep(1.5)
    return papers


def merge_dedupe(groups):
    """合并多组检索结果，按 PMID 去重，记录每组命中情况。"""
    by_pmid = {}
    for gname, res in groups.items():
        if not res:
            continue
        for paper in res.get("papers", []):
            pmid = str(paper.get("pmid", "")).strip()
            if not pmid:
                continue
            if pmid not in by_pmid:
                paper["_hit_groups"] = []
                paper["_group_count"] = 0
                by_pmid[pmid] = paper
            by_pmid[pmid]["_hit_groups"].append(gname)
            by_pmid[pmid]["_group_count"] = len(by_pmid[pmid]["_hit_groups"])
    return list(by_pmid.values())


# ==================== 相关性分级 ====================

def relevance_score(paper, boost_words):
    title = (paper.get("title") or "").lower()
    abstract = (paper.get("abstract") or "").lower()
    text = title + " " + abstract
    score = 0
    hits = []
    for w in boost_words:
        c = text.count(w.lower())
        if c:
            score += c
            hits.append(w)
    for w in ("epilepsy", "seizure", "seizures", "epileptic"):
        if w in title:
            score += 5
    return score, hits


def grade_paper(score):
    if score >= 8:
        return "必读"
    if score >= 3:
        return "推荐"
    return "浏览"


# ==================== 翻译 ====================

def translate_text(text, target="zh-CN", retries=2):
    """英文 → 中文，直接调用 MyMemory API（不经过 deep-translator 库，避免限流）。"""
    if not text or len(text.strip()) < 5:
        return text
    for attempt in range(retries + 1):
        try:
            chunks = [text[i:i + 450] for i in range(0, len(text), 450)]
            parts = []
            for chunk in chunks:
                params = urllib.parse.urlencode({"q": chunk, "langpair": "en|zh-CN"})
                url = f"https://api.mymemory.translated.net/get?{params}"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=20) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                if data.get("responseStatus") == 200:
                    translated = data["responseData"]["translatedText"]
                    if translated and len(translated) > 5:
                        parts.append(translated)
                    else:
                        parts.append(chunk)
                else:
                    parts.append(chunk)
                time.sleep(0.8)
            result = "\n".join(parts)
            # 验证是否真的翻译成中文（中文字符占比>10%）
            chinese = len(re.findall(r"[\u4e00-\u9fff]", result))
            if chinese / max(len(result), 1) > 0.1:
                return result
            elif attempt < retries:
                time.sleep(3)
                continue
            else:
                return text
        except Exception:
            if attempt < retries:
                time.sleep(3)
                continue
            return text
    return text


def translate_with_timeout(text, timeout=60):
    """带超时保护的翻译：超时返回原文，防止网络阻塞拖垮每日任务。"""
    import concurrent.futures
    if not text or len(text.strip()) < 5:
        return text
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        future = ex.submit(translate_text, text)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            return text


# ==================== 输出 ====================

def build_report(groups, papers, config, report_date, edat_date):
    meta = {
        "report_date": str(report_date),
        "target_edat": str(edat_date),
        "pipeline": "PubMed E-utilities",
        "source": "NCBI PubMed（GitHub Actions 定时抓取）",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    group_records = []
    for gname, res in groups.items():
        label = config["group_labels"].get(gname, gname)
        if not res:
            group_records.append({"group": gname, "label": label, "count": 0, "pmids": [], "status": "failed"})
            continue
        pmids = [p.get("pmid") for p in res.get("papers", [])]
        group_records.append({
            "group": gname,
            "label": label,
            "count": res.get("retrieved", 0),
            "total": res.get("total_results", 0),
            "pmids": pmids,
            "status": "ok",
        })
    paper_list = []
    for idx, p in enumerate(papers, 1):
        score, hits = relevance_score(p, config["relevance_boost_words"])
        paper_list.append({
            "index": idx,
            "pmid": p.get("pmid", ""),
            "title": p.get("title", ""),
            "title_zh": p.get("_title_zh", ""),
            "journal": p.get("journal", ""),
            "iso_journal": p.get("iso_journal", ""),
            "authors": p.get("authors", []),
            "pubdate": p.get("pubdate", ""),
            "doi": p.get("doi", ""),
            "url": p.get("url", ""),
            "abstract": p.get("abstract", ""),
            "abstract_zh": p.get("_abstract_zh", ""),
            "hit_groups": p.get("_hit_groups", []),
            "group_count": p.get("_group_count", 0),
            "relevance_score": score,
            "relevance_hits": hits[:8],
            "priority": grade_paper(score),
        })
    return {
        "meta": meta,
        "group_records": group_records,
        "dedup_total": len(papers),
        "papers": paper_list,
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>顶刊文献日报｜{{REPORT_DATE}}</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif; background:#f4f6f9; color:#1a1a2e; line-height:1.65; }
.header { background:linear-gradient(135deg,#1a1a2e,#0f3460); color:#fff; padding:1.8rem 2rem 1.4rem; }
.header h1 { font-size:1.5rem; }
.header .sub { opacity:.85; font-size:.95rem; margin-top:.3rem; }
.meta { margin-top:1rem; display:flex; gap:.6rem; flex-wrap:wrap; }
.meta span { background:rgba(255,255,255,.12); padding:.25rem .8rem; border-radius:20px; font-size:.8rem; }
.container { max-width:960px; margin:0 auto; padding:1.2rem 1.5rem; }
.card { background:#fff; border-radius:10px; padding:1.2rem 1.4rem; margin-bottom:1.2rem; box-shadow:0 1px 5px rgba(0,0,0,.06); }
.card h2 { font-size:1.05rem; border-bottom:2px solid #eef1f5; padding-bottom:.5rem; margin-bottom:.8rem; color:#0f3460; }
table { width:100%; border-collapse:collapse; font-size:.85rem; }
th { background:#1a2a4a; color:#fff; padding:.45rem .6rem; text-align:left; }
td { padding:.45rem .6rem; border-bottom:1px solid #eef1f5; }
.paper { border:1px solid #e6eaf0; border-left:4px solid #0f3460; border-radius:8px; padding:1rem 1.1rem; margin-bottom:1rem; background:#fff; }
.paper .p-title { font-weight:700; font-size:.98rem; }
.paper .p-title-zh { color:#0f3460; margin:.3rem 0 .5rem; font-size:.92rem; }
.paper .p-meta { font-size:.8rem; color:#555; margin-bottom:.6rem; word-break:break-all; }
.paper .p-abs { font-size:.85rem; color:#333; margin-bottom:.5rem; }
.paper .p-abs-zh { font-size:.85rem; color:#0f3460; }
.badge { display:inline-block; padding:.1rem .55rem; border-radius:12px; font-size:.72rem; font-weight:600; color:#fff; margin-left:.4rem; }
.badge-must { background:#c0392b; }
.badge-rec { background:#e67e22; }
.badge-browse { background:#7f8c8d; }
.empty { color:#999; font-size:.9rem; padding:.6rem 0; }
.footer { text-align:center; padding:1.5rem; color:#999; font-size:.78rem; }
.pri { font-weight:700; }
</style>
</head>
<body>
<div class="header">
  <h1>顶刊文献日报</h1>
  <div class="sub">检索口径：PubMed Entry Date [EDAT]（{{TARGET_EDAT}} 当日新增）</div>
  <div class="meta">
    <span>日报日期 {{REPORT_DATE}}</span>
    <span>去重后 {{DEDUP_TOTAL}} 篇</span>
    <span>数据来源 {{SOURCE}}</span>
    <span>生成 {{GENERATED_AT}}</span>
  </div>
</div>
<div class="container">
  <div class="card">
    <h2>PubMed 检索记录</h2>
    <table>
      <tr><th>检索</th><th>范围</th><th>状态</th><th>Count</th><th>PMID</th></tr>
      {{GROUP_ROWS}}
    </table>
    <p style="font-size:.9rem;margin-top:.8rem;"><strong>全局去重后文献数：{{DEDUP_TOTAL}}</strong></p>
  </div>
  {{PAPER_SECTIONS}}
</div>
<div class="footer">由 top-journal-follow-skill + GitHub Actions 自动生成</div>
</body>
</html>
"""


def esc(s):
    if not s:
        return ""
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def render_html(report, groups):
    group_rows = []
    for gr in report["group_records"]:
        label = gr["label"]
        status = "✓" if gr["status"] == "ok" else "✗"
        count = gr.get("count", 0)
        pmid_str = ", ".join(str(x) for x in gr.get("pmids", [])[:15])
        if gr.get("total", 0) > len(gr.get("pmids", [])):
            pmid_str += " …"
        group_rows.append(
            f"<tr><td>{status}</td><td>{esc(label)}</td>"
            f"<td>{'完成' if gr['status']=='ok' else '失败'}</td>"
            f"<td>{count}</td><td style='font-size:.75rem;'>{esc(pmid_str) or '无'}</td></tr>"
        )
    sections = []
    for gr in report["group_records"]:
        label = gr["label"]
        papers_in_group = [p for p in report["papers"] if gr["group"] in p["hit_groups"]]
        html_papers = []
        for p in papers_in_group:
            badge = f'<span class="badge badge-must">{p["priority"]}</span>' if p["priority"] == "必读" else (
                f'<span class="badge badge-rec">{p["priority"]}</span>' if p["priority"] == "推荐" else
                f'<span class="badge badge-browse">{p["priority"]}</span>')
            authors = ", ".join(p["authors"][:4]) + (" et al." if len(p["authors"]) > 4 else "")
            links = f'<a href="{esc(p["url"])}" target="_blank">PubMed</a>'
            if p["doi"]:
                links += f' · <a href="https://doi.org/{esc(p["doi"])}" target="_blank">DOI</a>'
            meta_html = (
                f"PMID {p['pmid']} · {esc(p['journal'])} · {esc(p['pubdate'])}<br>"
                f"作者：{esc(authors)}<br>链接：{links}<br>"
                f"命中检索：{', '.join(report['group_records'][i]['label'] for i, g in enumerate(report['group_records']) if g['group'] in p['hit_groups'])}"
            )
            abs_html = f'<div class="p-abs"><strong>摘要：</strong>{esc(p["abstract"])}</div>' if p["abstract"] else ""
            abs_zh_html = f'<div class="p-abs-zh"><strong>中文摘要：</strong>{esc(p["abstract_zh"])}</div>' if p.get("abstract_zh") else ""
            title_zh = f'<div class="p-title-zh">{esc(p["title_zh"])}</div>' if p.get("title_zh") else ""
            html_papers.append(
                f'<div class="paper"><div class="p-title">{esc(p["title"])}{badge}</div>{title_zh}'
                f'<div class="p-meta">{meta_html}</div>{abs_html}{abs_zh_html}</div>'
            )
        body = "\n".join(html_papers) if html_papers else '<div class="empty">暂无文献更新</div>'
        sections.append(f'<div class="card"><h2>📄 {esc(label)}（{len(papers_in_group)} 篇）</h2>{body}</div>')

    html = HTML_TEMPLATE
    html = html.replace("{{REPORT_DATE}}", report["meta"]["report_date"])
    html = html.replace("{{TARGET_EDAT}}", report["meta"]["target_edat"])
    html = html.replace("{{DEDUP_TOTAL}}", str(report["dedup_total"]))
    html = html.replace("{{SOURCE}}", esc(report["meta"]["source"]))
    html = html.replace("{{GENERATED_AT}}", esc(report["meta"]["generated_at"]))
    html = html.replace("{{GROUP_ROWS}}", "\n".join(group_rows))
    html = html.replace("{{PAPER_SECTIONS}}", "\n".join(sections))
    return html


def save_json(report, report_date):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, f"daily_report_{report_date}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    latest = os.path.join(OUTPUT_DIR, "daily_report_latest.json")
    with open(latest, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return path


def save_html(html, report_date):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, f"daily_report_{report_date}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    latest = os.path.join(OUTPUT_DIR, "daily_report_latest.html")
    with open(latest, "w", encoding="utf-8") as f:
        f.write(html)
    return path


# ==================== Notion ====================

def notion_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def notion_request(method, path, token, payload=None):
    url = NOTION_API + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=notion_headers(token), method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"[NOTION-ERROR] HTTP {e.code}: {body}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[NOTION-ERROR] {e}", file=sys.stderr)
        return None


def text_block(block_type, content, heading=False):
    if not content:
        return None
    return {
        "object": "block",
        "type": block_type,
        block_type: {"rich_text": [{"type": "text", "text": {"content": content[:1950]}}]},
    }


def build_notion_children(report):
    blocks = []
    meta = report["meta"]
    for line in (
        f"日报日期：{meta['report_date']}",
        f"目标 PubMed EDAT：{meta['target_edat']}",
        "检索口径：PubMed Entry Date [EDAT]",
        f"数据来源：{meta['source']}",
        "抓取状态：PubMed 官方接口检索完成",
    ):
        b = text_block("paragraph", line)
        if b:
            blocks.append(b)

    blocks.append({"object": "block", "type": "heading_2",
                   "heading_2": {"rich_text": [{"type": "text", "text": {"content": "PubMed 检索记录"}}]}})
    for gr in report["group_records"]:
        label = gr["label"]
        if gr["status"] == "ok":
            pmid_str = ", ".join(str(x) for x in gr.get("pmids", [])) if gr.get("pmids") else "无"
            line = f"检索 {gr['group']}：{label} — Count：{gr.get('count', 0)} — PMID：{pmid_str}"
        else:
            line = f"检索 {gr['group']}：{label} — 失败"
        b = text_block("bulleted_list_item", line)
        if b:
            blocks.append(b)

    blocks.append({"object": "block", "type": "paragraph",
                   "paragraph": {"rich_text": [{"type": "text", "text": {"content": f"全局去重后文献数：{report['dedup_total']}"}}]}})

    for gr in report["group_records"]:
        label = gr["label"]
        papers = [p for p in report["papers"] if gr["group"] in p["hit_groups"]]
        blocks.append({"object": "block", "type": "heading_2",
                       "heading_2": {"rich_text": [{"type": "text", "text": {"content": f"{label}（{len(papers)} 篇）"}}]}})
        if not papers:
            b = text_block("paragraph", "暂无文献更新")
            if b:
                blocks.append(b)
        for p in papers:
            blocks.append({"object": "block", "type": "heading_3",
                           "heading_3": {"rich_text": [{"type": "text", "text": {"content": f"文献 {p['index']}：{p['title'][:190]}"}}]}})
            for line in (
                (f"中文标题：{p.get('title_zh')}" if p.get("title_zh") else None),
                f"PMID：{p['pmid']}",
                f"期刊：{p['journal']}",
                f"作者：{'；'.join(p['authors'][:6]) + (' 等' if len(p['authors']) > 6 else '')}",
                (f"DOI：{p['doi']}" if p.get("doi") else None),
                f"PubMed：{p['url']}",
                f"命中检索：{'、'.join(gr['label'] for gr in report['group_records'] if gr['group'] in p['hit_groups'])}",
                f"优先级：{p['priority']}（相关度得分 {p['relevance_score']}）",
            ):
                if line:
                    b = text_block("bulleted_list_item", line)
                    if b:
                        blocks.append(b)
            if p.get("abstract"):
                b = text_block("paragraph", "英文摘要：" + p["abstract"])
                if b:
                    blocks.append(b)
            if p.get("abstract_zh"):
                b = text_block("paragraph", "中文摘要：" + p["abstract_zh"])
                if b:
                    blocks.append(b)
    return blocks


def write_notion(report):
    token = os.environ.get("NOTION_TOKEN", "").strip()
    parent_id = os.environ.get("NOTION_PARENT_PAGE_ID", "").strip()
    if not token or not parent_id:
        print("[NOTION-SKIP] 未配置 NOTION_TOKEN / NOTION_PARENT_PAGE_ID，跳过 Notion 写入。")
        return None
    parent_type = os.environ.get("NOTION_PARENT_TYPE", "page").strip()
    children = build_notion_children(report)
    page_title = f"文献日报｜{report['meta']['report_date']}"
    payload = {
        "parent": {parent_type: parent_id},
        "properties": {"title": {"title": [{"type": "text", "text": {"content": page_title}}]}},
        "children": children[:100],
    }
    created = notion_request("POST", "/pages", token, payload)
    if not created:
        print("[NOTION-FAIL] 页面创建失败。", file=sys.stderr)
        return None
    page_id = created.get("id", "")
    rest = children[100:]
    while rest:
        chunk, rest = rest[:100], rest[100:]
        notion_request("PATCH", f"/blocks/{page_id}/children", token, {"children": chunk})
    print(f"[NOTION-OK] 已创建页面: {page_title} (id={page_id})")
    return created.get("url", page_id)


# ==================== 飞书多维表格 ====================

FEISHU_API = "https://open.feishu.cn/open-apis"


def feishu_request(method, path, token, payload=None):
    url = FEISHU_API + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"[FEISHU-ERROR] HTTP {e.code}: {body}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[FEISHU-ERROR] {e}", file=sys.stderr)
        return None


def feishu_tenant_token(app_id, app_secret):
    resp = feishu_request("POST", "/auth/v3/tenant_access_token/internal", None,
                          {"app_id": app_id, "app_secret": app_secret})
    if resp and resp.get("code") == 0:
        return resp.get("tenant_access_token")
    print(f"[FEISHU-ERROR] 获取 tenant_access_token 失败: {resp}", file=sys.stderr)
    return None


def feishu_list_fields(token, app_token, table_id):
    resp = feishu_request("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", token)
    if resp and resp.get("code") == 0:
        return {item["field_name"]: item.get("type") for item in resp.get("data", {}).get("items", [])}
    return {}


def write_feishu(report):
    """把日报文献逐条写入飞书多维表格。自动检测表中存在的字段，只写匹配列。"""
    app_id = os.environ.get("FEISHU_APP_ID", "").strip()
    app_secret = os.environ.get("FEISHU_APP_SECRET", "").strip()
    app_token = os.environ.get("FEISHU_APP_TOKEN", "").strip()
    table_id = os.environ.get("FEISHU_TABLE_ID", "").strip()
    if not (app_id and app_secret and app_token):
        print("[FEISHU-SKIP] 未配置 FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_APP_TOKEN，跳过飞书写入。")
        return None

    token = feishu_tenant_token(app_id, app_secret)
    if not token:
        return None

    if not table_id:
        tables = feishu_request("GET", f"/bitable/v1/apps/{app_token}/tables", token)
        if tables and tables.get("code") == 0:
            items = tables.get("data", {}).get("items", [])
            if items:
                table_id = items[0]["table_id"]
        if not table_id:
            print("[FEISHU-ERROR] 无法确定 table_id，请设置 FEISHU_TABLE_ID", file=sys.stderr)
            return None

    fields_map = feishu_list_fields(token, app_token, table_id)
    if not fields_map:
        print("[FEISHU-ERROR] 无法获取表字段，请确认飞书应用已被添加为该多维表格的协作者（可编辑）", file=sys.stderr)
        return None
    print(f"[FEISHU] 检测到表字段 ({len(fields_map)}): {list(fields_map.keys())}")

    try:
        report_date_ts = int(datetime.strptime(report["meta"]["report_date"], "%Y-%m-%d").timestamp() * 1000)
    except Exception:
        report_date_ts = None

    records = []
    for p in report["papers"]:
        f = {}
        if "标题" in fields_map:
            f["标题"] = (p.get("title") or "")[:1000]
        if "中文标题" in fields_map and p.get("title_zh"):
            f["中文标题"] = p["title_zh"][:1000]
        if "PMID" in fields_map:
            f["PMID"] = str(p.get("pmid", ""))
        if "期刊" in fields_map:
            f["期刊"] = p.get("journal", "") or ""
        if "作者" in fields_map:
            authors = p.get("authors", []) or []
            f["作者"] = "；".join(authors[:8]) + (" 等" if len(authors) > 8 else "")
        if "DOI" in fields_map and p.get("doi"):
            f["DOI"] = p["doi"]
        if "PubMed链接" in fields_map and p.get("url"):
            if fields_map.get("PubMed链接") == 15:
                f["PubMed链接"] = {"link": p["url"], "text": p["url"]}
            else:
                f["PubMed链接"] = p["url"]
        if "优先级" in fields_map:
            f["优先级"] = p.get("priority", "浏览")
        if "命中检索" in fields_map:
            labels = [gr["label"] for gr in report["group_records"] if gr["group"] in p.get("hit_groups", [])]
            f["命中检索"] = "、".join(labels)
        if "英文摘要" in fields_map and p.get("abstract"):
            f["英文摘要"] = p["abstract"][:5000]
        if "中文摘要" in fields_map and p.get("abstract_zh"):
            f["中文摘要"] = p["abstract_zh"][:5000]
        if "日报日期" in fields_map and report_date_ts:
            f["日报日期"] = report_date_ts
        if "相关度得分" in fields_map:
            f["相关度得分"] = p.get("relevance_score", 0)
        if "已读" in fields_map:
            f["已读"] = False
        records.append({"fields": f})

    if not records:
        print("[FEISHU] 无文献可写入")
        return 0

    created = 0
    for i in range(0, len(records), 50):
        chunk = records[i:i + 50]
        resp = feishu_request(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create",
            token, {"records": chunk},
        )
        if resp and resp.get("code") == 0:
            created += len(resp.get("data", {}).get("records", []))
        else:
            print(f"[FEISHU-ERROR] 批量写入第 {i//50 + 1} 批失败: {resp}", file=sys.stderr)
    print(f"[FEISHU-OK] 已写入 {created}/{len(records)} 条记录到飞书多维表格")
    return created


# ==================== 主流程 ====================

def main():
    parser = argparse.ArgumentParser(description="顶刊文献日报生成器")
    parser.add_argument("--date", default=None, help="指定日报日期 YYYY-MM-DD（默认今天，北京时区）")
    parser.add_argument("--no-translate", action="store_true", help="跳过中文翻译")
    parser.add_argument("--config", default=CONFIG_PATH, help="配置文件路径")
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        config = json.load(f)

    report_date = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else beijing_today()
    edat_date = report_date - timedelta(days=1)
    print(f"[INFO] 日报日期: {report_date}  目标 EDAT: {edat_date}")

    base_query = f'{config["query"]}[Title/Abstract] AND ("{edat_date}"[EDAT] : "{edat_date}"[EDAT])'
    group_queries = config.get("group_queries", {})
    print(f"[INFO] 默认检索式: {base_query}")

    groups = {}
    for gname, journals in config["journals"].items():
        print(f"\n{'='*60}\n[RUN] 组 {gname} 检索开始")
        gq = group_queries.get(gname)
        if gq:
            actual_query = f'({gq}) AND ("{edat_date}"[EDAT] : "{edat_date}"[EDAT])'
            print(f"[INFO] 组 {gname} 专用检索式: {actual_query}")
        else:
            actual_query = base_query
        groups[gname] = run_search(actual_query, journals, os.path.join(OUTPUT_DIR, f"group_{gname}"), config.get("max_results", 100))

    papers = merge_dedupe(groups)
    papers = refetch_missing_abstracts(papers)
    print(f"\n[INFO] 合并去重后文献数: {len(papers)}")

    if config.get("translate", True) and not args.no_translate:
        print("[INFO] 开始中文翻译（标题 + 摘要，单篇限时 60 秒，失败自动重试）...")
        translate_failures = []
        for i, p in enumerate(papers, 1):
            print(f"  [{i}/{len(papers)}] PMID {p.get('pmid')} 翻译中...")
            title_zh = translate_with_timeout(p.get("title", ""))
            abs_zh = translate_with_timeout(p.get("abstract", ""))
            p["_title_zh"] = title_zh
            p["_abstract_zh"] = abs_zh
            # 检测翻译是否失败（中文字符占比<10%说明仍是英文）
            if p.get("abstract"):
                zh_chars = len(re.findall(r"[\u4e00-\u9fff]", abs_zh))
                if zh_chars / max(len(abs_zh), 1) < 0.1:
                    translate_failures.append(p.get("pmid"))
            time.sleep(0.8)
        if translate_failures:
            print(f"[WARN] 以下 PMID 翻译可能失败（仍是英文）: {', '.join(translate_failures)}")
        else:
            print("[OK] 全部翻译成功")
    else:
        for p in papers:
            p["_title_zh"] = ""
            p["_abstract_zh"] = ""

    report = build_report(groups, papers, config, report_date, edat_date)
    json_path = save_json(report, report_date)
    print(f"[SUCCESS] JSON: {json_path}")

    html = render_html(report, groups)
    html_path = save_html(html, report_date)
    print(f"[SUCCESS] HTML: {html_path}")

    page_url = write_notion(report)
    print(f"[NOTION] 页面: {page_url or '未写入'}")

    feishu_count = write_feishu(report)
    print(f"[FEISHU] 写入记录: {feishu_count if feishu_count else '未写入'}")

    print("\n" + "=" * 60)
    print("  日报摘要")
    print("=" * 60)
    print(f"  日期: {report_date} | EDAT: {edat_date} | 去重后: {report['dedup_total']} 篇")
    for gr in report["group_records"]:
        print(f"  {gr['label']}: {gr.get('count', 0)} 篇 ({gr['status']})")
    for p in report["papers"]:
        print(f"  · [{p['priority']}] PMID {p['pmid']} — {p['title'][:60]}")


if __name__ == "__main__":
    main()
