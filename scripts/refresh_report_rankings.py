"""Refresh the saved report without repeating PubMed retrieval or translation."""

import copy
import json
from pathlib import Path

from journal_rankings import enrich_report
from run_daily import render_html, save_html, save_json


def main():
    root = Path(__file__).resolve().parents[1]
    path = root / 'reports/latest.json'
    report = json.loads(path.read_text(encoding='utf-8'))
    before = copy.deepcopy(report)
    enrich_report(report)
    save_json(report, report['meta']['report_date'])
    save_html(render_html(report, {}), report['meta']['report_date'])
    if [paper['pmid'] for paper in before['papers']] != [paper['pmid'] for paper in report['papers']]:
        raise ValueError('Report paper list changed')
    print(json.dumps({'report_date': report['meta']['report_date'], 'papers': len(report['papers']),
                      **report['meta']['journal_rankings']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
