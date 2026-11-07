"""Explicit coverage windows and versioned, per-group progress."""
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

STATE_PATH = Path(__file__).resolve().parents[1] / 'reports/search_progress.json'


def load_progress(path=STATE_PATH):
    path = Path(path)
    state = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
    if not isinstance(state, dict) or not isinstance(state.get('groups', {}), dict):
        raise ValueError('Invalid search progress')
    state.setdefault('groups', {})
    return state


def save_progress(state, path=STATE_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def group_query(config, group):
    if config.get('group_queries', {}).get(group):
        return config['group_queries'][group]
    query = config['query']
    return query if '[' in query or any(x in query.upper().split() for x in ['OR', 'AND', 'NOT']) else query+'[Title/Abstract]'


def fingerprint(config, group):
    return hashlib.sha256(json.dumps({'query': group_query(config, group),
        'journals': config['journals'][group], 'strategy_version': config.get('strategy_version', '')},
        sort_keys=True).encode()).hexdigest()


def coverage_window(config, group, target_end, state, start=None, end=None):
    if (start is None) != (end is None):
        raise ValueError('Both EDAT bounds are required for backfill')
    if start is not None:
        if start > end or end > target_end:
            raise ValueError('Invalid EDAT range or future end date')
        return start, end
    overlap = int(config.get('overlap_days', 3))
    begin = target_end - timedelta(days=overlap-1)
    prior = state['groups'].get(group, {})
    if prior.get('fingerprint') == fingerprint(config, group):
        if prior.get('through'):
            through = date.fromisoformat(prior['through'])
            begin = min(begin, through-timedelta(days=overlap-2))
        elif prior.get('pending_from'):
            begin = min(begin, date.fromisoformat(prior['pending_from']))
    limit = int(config.get('max_catchup_days', 7))
    return begin, min(target_end, begin+timedelta(days=limit-1))


def remember_attempt(state, config, group, begin):
    """Retain the initial start even if the first ever request fails for days."""
    prior = state['groups'].get(group, {})
    if prior.get('fingerprint') != fingerprint(config, group):
        state['groups'][group] = {'pending_from': str(begin), 'fingerprint': fingerprint(config, group)}
    return state


def advance_progress(state, config, report, sync_verified):
    if not sync_verified:
        return state
    for group in report['group_records']:
        if group['status'] != 'ok':
            continue
        key = group['group']
        previous = state['groups'].get(key, {})
        through = group['edat_end']
        if previous.get('fingerprint') == fingerprint(config, key):
            through = max(through, previous.get('through', through))
        state['groups'][key] = {'through': through, 'fingerprint': fingerprint(config, key)}
    return state
