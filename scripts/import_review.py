"""Collect explicitly selected qualifying reviews without advancing daily coverage."""
import argparse
from datetime import timedelta
import json
from pathlib import Path
import re

import run_daily as daily
from review_selection import select_group, mark_synced
from title_translations import prepare_titles


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pmids', required=True)
    args = parser.parse_args()
    pmids = list(dict.fromkeys(args.pmids.split(',')))
    if not pmids or len(pmids)>20 or any(not re.fullmatch(r'\d+', p) for p in pmids):
        parser.error('Provide 1–20 comma-separated PMIDs')
    config = json.loads(Path(daily.CONFIG_PATH).read_text(encoding='utf-8'))
    group = 'ER'
    policy = config['group_ranking_filters'][group]
    today = daily.beijing_today()
    query = '('+config['group_queries'][group]+') AND ('+' OR '.join(p+'[PMID]' for p in pmids)+')'
    raw = daily.run_search(query, '', str(Path(daily.OUTPUT_DIR)/'selected_reviews'), len(pmids))
    if not raw or raw.get('complete') is not True:
        raise RuntimeError('Selected review lookup is incomplete')
    selected = select_group(raw, group, policy, str(today), retry_backlog=False)
    if set(p['pmid'] for p in selected['papers']) != set(pmids):
        raise RuntimeError('Selected PMID does not meet epilepsy-review / CAS-1 requirements; held for review')
    groups = {group: selected}
    papers = daily.merge_dedupe(groups)
    title_diag = prepare_titles(papers, daily.translate_with_timeout)
    if title_diag['missing_title_pmids']:
        raise RuntimeError('Selected Chinese titles are pending; no new records created')
    for paper in papers:
        paper['_abstract_zh'] = daily.translate_with_timeout(paper.get('abstract', ''))
    report = daily.build_report(groups, papers, config, today, today-timedelta(days=1))
    report['meta'].update(selected_pmids=pmids, selected_only=True,
                          source='PubMed 指定 PMID 补录（不限入库日期）',
                          title_translation_diag=title_diag)
    created, expected, diag = daily.write_feishu(report)
    report['meta']['feishu_diag'] = diag
    mark_synced(report)
    # Keep the complete daily report and per-group search progress unchanged.
    path = Path(daily.BASE_DIR)/'reports/selected_reviews_latest.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'selected_pmids': pmids, 'created': created, 'expected': expected,
                      'feishu_diag': diag}, ensure_ascii=False))
    if not diag.get('verified') or diag.get('pending') or diag.get('status') not in ('ok','empty'):
        raise RuntimeError('Selected review delivery was not verified')


if __name__ == '__main__':
    main()
