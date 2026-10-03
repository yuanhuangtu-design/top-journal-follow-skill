"""Durable daily queue: discovery, enrichment and delivery have independent state."""
import argparse
import json
import os
from pathlib import Path
from datetime import date, timedelta

from pubmed_client import CLIENT
from literature_search import build_query_from_parsed, parse_natural_query
from feishu_sync import Feishu, text
from translation import Translator, good


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def run(ui):
    parser = argparse.ArgumentParser(description="Reliable daily literature pipeline")
    parser.add_argument("--date")
    parser.add_argument("--start-date", help="Backfill EDAT inclusively from this date")
    parser.add_argument("--no-translate", action="store_true")
    parser.add_argument("--no-sync", action="store_true", help="No external writes")
    parser.add_argument("--config", default=ui.CONFIG_PATH)
    parser.add_argument("--state", default=str(Path(ui.BASE_DIR) / "reports/pipeline_state.json"))
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    today = date.fromisoformat(args.date) if args.date else ui.beijing_today()
    end = today - timedelta(days=1)
    start = date.fromisoformat(args.start_date) if args.start_date else end - timedelta(days=2)
    if start > end:
        parser.error("start-date must precede report date")
    state_path = Path(args.state)
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    state.setdefault("groups", {})
    state.setdefault("papers", {})
    state.setdefault("translations", {})
    state.setdefault("pending_pmids", {})
    errors, groups = [], {}
    # Cache contains only public paper metadata/translation, never Feishu records or tokens.
    pending, failed = CLIENT.fetch(list(state["pending_pmids"])) if state["pending_pmids"] else ([], [])
    for paper in pending:
        paper["_hit_groups"] = state["pending_pmids"].pop(paper["pmid"])
        state["papers"][paper["pmid"]] = dict(paper, first_seen=str(today))
    for name, journals in config["journals"].items():
        previous = state["groups"].get(name, {})
        cursor = min(start, date.fromisoformat(previous["through"]) + timedelta(days=1)) if previous.get("through") else (start if args.start_date else min(start, date.fromisoformat(config.get("initial_backfill_date", str(start)))))
        failed_dates = set(previous.get("failed_dates", []))
        if failed_dates:
            cursor = min(cursor, date.fromisoformat(min(failed_dates)))
        # Bound work per invocation, but keep the oldest unprocessed date durable.
        until = min(end, cursor + timedelta(days=29))
        group_papers, group_status, total = {}, "ok", 0
        contiguous = True
        through = previous.get("through")
        while cursor <= until:
            topic = config.get("group_queries", {}).get(name, config["query"] + "[Title/Abstract]")
            query = f'({topic}) AND ("{cursor}"[EDAT] : "{cursor}"[EDAT])'
            query = build_query_from_parsed(parse_natural_query(query), journals)
            result = CLIENT.search(query)
            total += result["total_results"]
            if result["status"] != "ok":
                contiguous = False
                failed_dates.add(str(cursor))
                group_status = "partial"
                errors.append(f"{name}:{cursor}:{result['status']}")
            else:
                failed_dates.discard(str(cursor))
                if contiguous:
                    through = max(through or str(cursor), str(cursor))
            for pmid in result.get("pending_pmids", []):
                hits = state["pending_pmids"].setdefault(pmid, [])
                if name not in hits:
                    hits.append(name)
            for paper in result["papers"]:
                pmid = paper["pmid"]
                old = state["papers"].get(pmid, {})
                hits = list(dict.fromkeys(old.get("_hit_groups", []) + [name]))
                state["papers"][pmid] = dict(old, **paper, _hit_groups=hits, first_seen=old.get("first_seen", str(today)))
                state["pending_pmids"].pop(pmid, None)
                group_papers[pmid] = state["papers"][pmid]
            cursor += timedelta(days=1)
        if until < end:
            errors.append(f"{name}:backlog_remaining")
            group_status = "partial"
        state["groups"][name] = {"failed_dates": sorted(failed_dates)}
        if through:
            state["groups"][name]["through"] = through
        groups[name] = {"papers": list(group_papers.values()), "retrieved": len(group_papers),
                        "total_results": total, "status": group_status}
        atomic_json(state_path, state)
    sync = {"status": "skipped"}
    client = snapshot = None
    if not args.no_sync:
        required = ["FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_APP_TOKEN", "FEISHU_TABLE_ID"]
        if not all(os.getenv(k) for k in required):
            errors.append("feishu:missing_configuration")
        else:
            try:
                client = Feishu()
                snapshot = client.snapshot()
            except Exception as exc:
                errors.append("feishu:lookup_failed:" + type(exc).__name__)
    # Enrichment retries from the durable corpus even after the discovery window moves on.
    translator = Translator(state["translations"], config.get("translation_budget_seconds", 120))
    for paper in state["papers"].values():
        existing = snapshot[1].get(paper["pmid"], {}).get("fields", {}) if snapshot else {}
        for source, dest, field in [("title", "_title_zh", "中文标题"), ("abstract", "_abstract_zh", "中文摘要")]:
            original = paper.get(source, "")
            prior = text(existing.get(field))
            if good(original, prior):
                paper[dest] = prior
            if not args.no_translate and config.get("translate", True) and original and not good(original, paper.get(dest, "")):
                paper[dest] = translator.translate(original)
        paper["_group_count"] = len(paper.get("_hit_groups", []))
    papers = list(state["papers"].values())
    delivery = ui.build_report(groups, papers, config, today, f"{start} ~ {end}")
    for result, paper in zip(delivery["papers"], papers):
        result["first_seen"] = paper["first_seen"]
        result["abstract_status"] = paper.get("abstract_status", "pending")
    if client and snapshot:
        try:
            sync = dict(client.sync(delivery, snapshot), status="ok")
        except Exception as exc:
            errors.append("feishu:write_failed:" + type(exc).__name__)
            sync = {"status": "failed"}
    current_ids = {p["pmid"] for group in groups.values() for p in group["papers"]}
    report = ui.build_report(groups, [p for p in papers if p["pmid"] in current_ids], config, today, f"{start} ~ {end}")
    translation_pending = sum(bool(p.get("title") and not good(p["title"], p.get("_title_zh", ""))) or
                              bool(p.get("abstract") and not good(p["abstract"], p.get("_abstract_zh", ""))) for p in papers)
    report["run_status"] = "partial" if errors or state["pending_pmids"] else "ok"
    report["errors"] = errors
    report["delivery"] = sync
    report["pending_details"] = len(state["pending_pmids"])
    report["pending_translation"] = translation_pending
    if not args.no_sync and os.getenv("NOTION_TOKEN") and os.getenv("NOTION_PARENT_PAGE_ID"):
        try:
            if not ui.write_notion(report):
                errors.append("notion:write_failed")
        except Exception:
            errors.append("notion:write_failed")
        if errors:
            report["run_status"] = "partial"
    state["last_run"] = {"date": str(today), "status": report["run_status"], "errors": errors}
    atomic_json(state_path, state)
    ui.save_json(report, today)
    ui.save_html(ui.render_html(report, groups), today)
    print(json.dumps({k: report[k] for k in ("run_status", "dedup_total", "delivery", "pending_details", "pending_translation", "errors")}, ensure_ascii=False))
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as out:
            out.write(f"## 文献日报 {today}\n\n状态：{report['run_status']}；检索窗口去重 {report['dedup_total']} 篇；待补详情 {report['pending_details']}；待翻译 {translation_pending}。\n\n入库：`{json.dumps(sync, ensure_ascii=False)}`\n")
            for error in errors:
                out.write(f"\n- {error}\n")
    return 1 if report["run_status"] != "ok" else 0
