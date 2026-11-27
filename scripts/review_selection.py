"""Apply the CAS major-category gate before translation and Feishu writes."""
from copy import deepcopy
import json
from pathlib import Path
import re

from journal_rankings import paper_rankings

STATE_PATH = Path(__file__).resolve().parents[1] / 'reports/review_candidates.json'


def read_state(path):
    path = Path(path)
    state = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'papers': {}}
    if not isinstance(state.get('papers'), dict):
        raise ValueError('Invalid review candidate state')
    return state


def save_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def decision(paper, policy):
    if policy != {'cas_version': '2025', 'cas_major_partition': 1}:
        raise ValueError('Unsupported review selection policy')
    value = paper_rankings(paper)['cas_partition']
    match = re.fullmatch(r'2025版｜[^｜]+｜([1-4])区', value)
    if match:
        return 'eligible' if match.group(1) == '1' else 'cas_not_1'
    return 'cas_unverified'


def select_group(result, group, policy, observed_date, path=STATE_PATH, retry_backlog=True):
    """Keep the raw completeness signal and retain every withheld candidate."""
    if result is None:
        return None
    state = read_state(path)
    current = {str(p['pmid']): deepcopy(p) for p in result.get('papers', [])}
    backlog = {pmid: deepcopy(row['paper']) for pmid, row in state['papers'].items()
               if retry_backlog and row['group'] == group and row['status'] != 'admitted' and pmid not in current}
    accepted, counts = [], {'cas_not_1': 0, 'cas_unverified': 0}
    promoted = 0
    for pmid, paper in {**backlog, **current}.items():
        prior = state['papers'].get(pmid, {})
        outcome = decision(paper, policy)
        first_seen = prior.get('first_seen', observed_date)
        state['papers'][pmid] = {**prior, 'group': group, 'paper': paper, 'first_seen': first_seen,
            'last_checked': observed_date,
            'status': 'admitted' if prior.get('status') == 'admitted' else outcome}
        if outcome == 'eligible':
            paper['first_seen'] = first_seen
            accepted.append(paper)
            promoted += pmid in backlog
        elif pmid in current:
            counts[outcome] += 1
    save_state(path, state)
    filtered = {**result, 'papers': accepted, 'retrieved': len(accepted),
                'total_results': result.get('total_results', len(current))+promoted}
    filtered['ranking_selection'] = {'policy': policy,
        'source_retrieved': result.get('retrieved', len(current)),
        'source_total': result.get('total_results', len(current)),
        'qualified_current': len(accepted)-promoted, 'promoted_candidates': promoted, **counts}
    return filtered


def mark_synced(report, path=STATE_PATH):
    """Remove a held item from future retries only after verified delivery."""
    diag = report.get('meta', {}).get('feishu_diag', {})
    if not diag.get('verified') or diag.get('pending') != 0:
        return
    state = read_state(path)
    for paper in report['papers']:
        row = state['papers'].get(paper['pmid'])
        if row and row['group'] in paper.get('hit_groups', []) and row['status'] == 'eligible':
            row['status'] = 'admitted'
            row['admitted_on'] = report['meta']['report_date']
    if state['papers']:
        save_state(path, state)
