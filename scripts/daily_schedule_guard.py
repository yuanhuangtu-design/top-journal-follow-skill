"""Retry scheduled daily reports until the saved report is fully verified."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

from retrieval_policy import fingerprint

ROOT = Path(__file__).resolve().parents[1]


def needs_daily_run(event, mode, report, pending, config, progress, today):
    if event != 'schedule' or mode != 'daily':
        return True, 'explicit_or_weekly_run'
    meta = report.get('meta', {})
    sync = meta.get('feishu_diag', {})
    if meta.get('report_date') != today:
        return True, 'today_report_missing'
    if meta.get('search_complete') is not True:
        return True, 'search_incomplete'
    if sync.get('verified') is not True or sync.get('status') not in ('ok', 'empty'):
        return True, 'sync_unverified'
    if sync.get('pending') != 0 or pending.get('papers'):
        return True, 'papers_pending'
    if meta.get('title_translation_diag', {}).get('missing_title_pmids') or sync.get('missing_titles', 0):
        return True, 'titles_pending'
    target = (datetime.fromisoformat(today).date() - timedelta(days=1)).isoformat()
    for group in config['journals']:
        state = progress.get('groups', {}).get(group, {})
        if state.get('fingerprint') != fingerprint(config, group) or state.get('through', '') < target:
            return True, 'coverage_pending'
    return False, 'today_report_verified'


def main():
    event = os.environ.get('GITHUB_EVENT_NAME', '')
    mode = os.environ.get('RUN_MODE', 'daily')
    today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    run, reason = True, 'explicit_or_weekly_run'
    if event == 'schedule' and mode == 'daily':
        try:
            def read(relative):
                return json.loads((ROOT / relative).read_text(encoding='utf-8-sig'))
            run, reason = needs_daily_run(event, mode, read('reports/latest.json'),
                                         read('reports/feishu_pending.json'), read('config/daily_config.json'),
                                         read('reports/search_progress.json'), today)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            run, reason = True, 'saved_state_unavailable'
    output = os.environ.get('GITHUB_OUTPUT')
    if output:
        with open(output, 'a', encoding='utf-8') as handle:
            handle.write(f'should_run={str(run).lower()}\n')
    result = {'report_date': today, 'should_run': run, 'reason': reason}
    print(json.dumps(result, ensure_ascii=False))
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a', encoding='utf-8') as handle:
            handle.write(f"日报日期 {today}\n\n" + ('执行检索与同步。' if run else '当日日报已核验完成，跳过重复执行。')
                         + f'\n\n检查结果 `{reason}`\n')


if __name__ == '__main__':
    main()
