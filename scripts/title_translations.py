"""Persist title translations and retry missing titles before long abstracts."""
import json
import re
from pathlib import Path

STATE_PATH = Path(__file__).resolve().parents[1] / 'reports' / 'title_translations.json'


def is_chinese(value):
    return isinstance(value, str) and bool(value.strip()) and len(re.findall(r'[\u4e00-\u9fff]', value)) / len(value) >= .15


def load_state(path=STATE_PATH):
    path = Path(path)
    state = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
    for key in ('translations', 'pending'):
        state.setdefault(key, {})
        if not isinstance(state[key], dict):
            raise ValueError('Invalid title translation state')
    return state


def save_state(state, path=STATE_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def prepare_titles(papers, translate, path=STATE_PATH, max_backlog=10):
    state = load_state(path)
    current = {str(p['pmid']): p for p in papers}
    for pmid, paper in current.items():
        source = paper.get('title', '')
        entry = state['translations'].get(pmid, {})
        if entry.get('title') == source and is_chinese(entry.get('title_zh')):
            state['pending'].pop(pmid, None)
        else:
            state['pending'][pmid] = {'title': source}
    save_state(state, path)
    # Today's titles go first; the backlog follows. Abstracts run afterwards.
    ordered = list(current) + [p for p in state['pending'] if p not in current][:max_backlog]
    for pmid in ordered:
        source = current[pmid].get('title', '') if pmid in current else state['pending'][pmid]['title']
        entry = state['translations'].get(pmid, {})
        translated = entry.get('title_zh', '') if entry.get('title') == source else ''
        if not is_chinese(translated):
            translated = translate(source) if source else ''
        if is_chinese(translated):
            state['translations'][pmid] = {'title': source, 'title_zh': translated}
            state['pending'].pop(pmid, None)
        else:
            translated = ''
            state['pending'][pmid] = {'title': source}
        if pmid in current:
            current[pmid]['_title_zh'] = translated
        # Persist each success, including if a later request or job times out.
        save_state(state, path)
    missing = [p for p, paper in current.items() if not is_chinese(paper.get('_title_zh'))]
    return {'status': 'partial' if missing else 'ok', 'missing_title_pmids': missing,
        'pending_titles': len(state['pending']), 'cached_titles': len(state['translations'])}
