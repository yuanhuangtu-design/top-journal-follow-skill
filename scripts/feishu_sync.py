"""Create-only Feishu sync with complete PMID checks and a durable retry queue."""
import argparse
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from title_translations import is_chinese, load_state

ROOT = Path(__file__).resolve().parents[1]
CONFIG_KEYS = ('FEISHU_APP_ID', 'FEISHU_APP_SECRET', 'FEISHU_APP_TOKEN', 'FEISHU_TABLE_ID')


class SyncError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__('Feishu sync failed: ' + str(code))


def text(value):
    if isinstance(value, list):
        return ''.join(str(item.get('text', '')) if isinstance(item, dict) else str(item) for item in value)
    return str(value or '')


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


class Feishu:
    def __init__(self):
        values = {key: os.environ.get(key, '').strip() for key in CONFIG_KEYS}
        if not all(values.values()):
            raise SyncError('incomplete_config')
        expected = os.environ.get('FEISHU_EXPECTED_APP_ID', '').strip()
        if expected and values['FEISHU_APP_ID'] != expected:
            raise SyncError('app_id_mismatch')
        self.base = '/bitable/v1/apps/{}/tables/{}'.format(values['FEISHU_APP_TOKEN'], values['FEISHU_TABLE_ID'])
        self.token = None
        result = self.request('POST', '/auth/v3/tenant_access_token/internal', {
            'app_id': values['FEISHU_APP_ID'], 'app_secret': values['FEISHU_APP_SECRET']})
        self.token = result.get('tenant_access_token')
        if not self.token:
            raise SyncError('missing_token')

    def request(self, method, path, payload=None):
        safe_read = method == 'GET' or path.split('?')[0].endswith('/records/search') or path == '/auth/v3/tenant_access_token/internal'
        attempts = 3 if safe_read else 1
        for attempt in range(attempts):
            try:
                return self._request_once(method, path, payload)
            except SyncError as exc:
                if not transient_error(exc) or attempt + 1 == attempts:
                    raise
                time.sleep(2 * (attempt + 1))

    def _request_once(self, method, path, payload=None):
        headers = {'Content-Type': 'application/json; charset=utf-8'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        request = urllib.request.Request('https://open.feishu.cn/open-apis' + path,
            data=json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None,
            headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                code = json.load(exc).get('code', exc.code)
            except Exception:
                code = exc.code
            raise SyncError(code) from None
        except (OSError, ValueError):
            # Never retry a create: a timeout can occur after the server saves it.
            raise SyncError('network_or_invalid_response') from None
        if not isinstance(result, dict) or result.get('code') != 0:
            raise SyncError(result.get('code', 'invalid_response') if isinstance(result, dict) else 'invalid_response')
        return result

    def list_all(self, resource):
        items, seen, page = [], set(), None
        while True:
            query = {'page_size': 100}
            if page:
                query['page_token'] = page
            # Search and list require base:record:retrieve; base:record:read
            # only retrieves a specific record by its known record_id.
            endpoint = '/' + resource + ('/search' if resource == 'records' else '')
            response = self.request('POST' if resource == 'records' else 'GET',
                self.base + endpoint + '?' + urllib.parse.urlencode(query),
                {} if resource == 'records' else None)
            data = response.get('data', {})
            if not isinstance(data.get('items'), list):
                raise SyncError('incomplete_page')
            items.extend(data['items'])
            if not data.get('has_more'):
                return items
            page = data.get('page_token')
            if not page or page in seen:
                raise SyncError('incomplete_pagination')
            seen.add(page)

    def snapshot(self):
        fields = {item['field_name']: item['type'] for item in self.list_all('fields')}
        if fields.get('PMID') != 1 or fields.get('标题') != 1:
            raise SyncError('required_text_fields_missing')
        if '已读' in fields and fields['已读'] != 7:
            raise SyncError('read_state_field_type')
        records = {}
        for record in self.list_all('records'):
            pmid = text(record.get('fields', {}).get('PMID')).strip()
            if not pmid:
                continue
            if not pmid.isdigit() or pmid in records:
                raise SyncError('invalid_or_duplicate_pmid')
            records[pmid] = record
        return fields, records


def paper_fields(paper, fields):
    stamp = int(datetime.strptime(paper['first_seen'], '%Y-%m-%d').replace(
        tzinfo=timezone(timedelta(hours=8))).timestamp() * 1000)
    values = {'PMID': paper['pmid'], '标题': paper.get('title', ''), '期刊': paper.get('journal', ''),
        '作者': '；'.join(paper.get('authors', [])[:8]), 'DOI': paper.get('doi', ''),
        '英文摘要': paper.get('abstract', ''), '命中检索': '、'.join(paper.get('hit_group_labels') or paper.get('hit_groups', [])),
        '优先级': paper.get('priority', '浏览'), '相关度得分': paper.get('relevance_score', 0),
        '日报日期': stamp, '首次发现日期': stamp, '已读': False,
        '发表形式（自动）': paper.get('publication_label', '')}
    for source, target in [('title_zh', '中文标题'), ('abstract_zh', '中文摘要')]:
        translated = paper.get(source, '')
        if translated and len(re.findall(r'[\u4e00-\u9fff]', translated)) / len(translated) >= .15:
            values[target] = translated
    if paper.get('url'):
        values['PubMed链接'] = {'link': paper['url'], 'text': paper['url']} if fields.get('PubMed链接') == 15 else paper['url']
    return {key: value for key, value in values.items() if key in fields and value not in (None, '')}


def merge_pending(previous, report):
    merged = {}
    labels = {group['group']: group['label'] for group in report.get('group_records', [])}
    for original in previous.get('papers', []) + report.get('papers', []):
        pmid = str(original.get('pmid', '')).strip()
        if not pmid.isdigit():
            raise SyncError('invalid_candidate_pmid')
        prior = merged.get(pmid, {})
        paper = {**prior, **{key: value for key, value in original.items() if value not in (None, '', [])}, 'pmid': pmid}
        dates = [d for d in [prior.get('first_seen'), original.get('first_seen'), report.get('meta', {}).get('report_date')] if d]
        paper['first_seen'] = min(dates)
        groups = sorted(set(prior.get('hit_groups', []) + list(original.get('hit_groups', []))))
        paper['hit_groups'] = groups
        paper['hit_group_labels'] = sorted(set(prior.get('hit_group_labels', []) + original.get('hit_group_labels', []) +
            [labels[group] for group in groups if group in labels]))
        merged[pmid] = paper
    return merged


def transient_error(exc):
    return exc.code in ('network_or_invalid_response', 429, 500, 502, 503, 504)


def fill_blank_titles(client, records, translations, updated=None):
    """Update only an empty Chinese title after re-reading its current record."""
    updated = {} if updated is None else updated
    for pmid, entry in translations.items():
        record = records.get(pmid)
        if not record or text(record['fields'].get('中文标题')).strip() or not is_chinese(entry.get('title_zh')):
            continue
        if text(record['fields'].get('标题')) != entry.get('title'):
            continue
        for attempt in range(2):
            fresh = client.request('GET', client.base + '/records/' + record['record_id'])['data']['record']
            fields = fresh['fields']
            if text(fields.get('PMID')).strip() != pmid or text(fields.get('标题')) != entry['title']:
                break
            chinese = text(fields.get('中文标题')).strip()
            if attempt and chinese == entry['title_zh']:
                # A timeout may occur after the first PUT was already saved.
                updated[pmid] = entry['title_zh']
                break
            if chinese:
                break
            try:
                client.request('PUT', client.base + '/records/' + record['record_id'],
                    {'fields': {'中文标题': entry['title_zh']}})
                updated[pmid] = entry['title_zh']
                break
            except SyncError as exc:
                if not transient_error(exc) or attempt:
                    raise
                # Never blindly retry PUT: re-read the title and identity first.
                time.sleep(2)
    return updated


def title_health(records):
    missing = [pmid for pmid, r in records.items() if not text(r['fields'].get('中文标题')).strip()]
    closed = [pmid for pmid, r in records.items() if 'Closed-loop' in text(r['fields'].get('命中检索'))
        or '闭环' in text(r['fields'].get('命中检索'))]
    return {'missing_titles': len(missing), 'closed_loop_papers': len(closed),
        'closed_loop_missing_titles': sum(pmid in missing for pmid in closed)}


def sync(report, pending_path=None, client_factory=Feishu):
    path = Path(pending_path or os.environ.get('FEISHU_PENDING_PATH', ROOT / 'reports' / 'feishu_pending.json'))
    diag = {'status': 'unknown', 'stage': 'queue', 'searched': len(report.get('papers', [])),
        'created': 0, 'expected': None, 'verified': False}
    try:
        previous = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {'papers': []}
        pending = merge_pending(previous, report)
        save(path, {'papers': list(pending.values())})
        diag['candidates'] = len(pending)
        required = os.environ.get('FEISHU_REQUIRED', '').lower() == 'true'
        if not required and not any(os.environ.get(key, '').strip() for key in CONFIG_KEYS):
            diag.update(status='skipped', pending=len(pending))
            return None, None, diag
        diag['stage'] = 'authenticate'
        client = client_factory()
        diag['stage'] = 'lookup'
        fields, existing = client.snapshot()
        needed = [paper for pmid, paper in pending.items() if pmid not in existing]
        diag.update(expected=len(needed), dedup_skipped=len(pending) - len(needed))
        save(path, {'papers': needed})
        diag['stage'] = 'create'
        for offset in range(0, len(needed), 50):
            batch = needed[offset:offset + 50]
            diag['attempting_pmids'] = [paper['pmid'] for paper in batch]
            client.request('POST', client.base + '/records/batch_create',
                {'records': [{'fields': paper_fields(paper, fields)} for paper in batch]})
        diag['stage'] = 'verify'
        _, after = client.snapshot() if needed else (fields, existing)
        if os.environ.get('FEISHU_FILL_MISSING_TITLES', '').lower() == 'true':
            diag['stage'] = 'fill_titles'
            translations = load_state()['translations']
            translations.update({p['pmid']: {'title': p.get('title'), 'title_zh': p.get('title_zh')}
                for p in pending.values() if is_chinese(p.get('title_zh'))})
            updated = {}
            try:
                fill_blank_titles(client, after, translations, updated)
            finally:
                diag['titles_updated'] = len(updated)
            if updated:
                _, after = client.snapshot()
                if any(text(after.get(p, {}).get('fields', {}).get('中文标题')) != zh for p, zh in updated.items()):
                    raise SyncError('title_readback_mismatch')
            diag.update(titles_updated=len(updated), **title_health(after))
        remaining = [paper for paper in needed if paper['pmid'] not in after]
        save(path, {'papers': remaining})
        diag.pop('attempting_pmids', None)
        diag.update(created=len(needed) - len(remaining), pending=len(remaining), verified=not remaining,
            status='partial' if remaining else ('ok' if needed else 'empty'), stage='complete')
    except (SyncError, OSError, ValueError, KeyError, TypeError) as exc:
        diag.update(status='failed', code=getattr(exc, 'code', 'invalid_local_state'))
    return diag['created'], diag['expected'], diag


def main():
    parser = argparse.ArgumentParser(description='Read-only Feishu connection check')
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--check', action='store_true')
    modes.add_argument('--flush-pending', action='store_true')
    modes.add_argument('--fill-titles', action='store_true')
    args = parser.parse_args()
    if args.fill_titles:
        result = {'status': 'failed', 'titles_updated': 0}
        updates = {}
        try:
            if os.environ.get('FEISHU_FILL_MISSING_TITLES', '').lower() != 'true':
                raise SyncError('title_updates_not_enabled')
            client = Feishu()
            _, before = client.snapshot()
            fill_blank_titles(client, before, load_state()['translations'], updates)
            _, after = client.snapshot()
            if any(text(after.get(p, {}).get('fields', {}).get('中文标题')) != zh for p, zh in updates.items()):
                raise SyncError('title_readback_mismatch')
            result.update(status='ok', titles_updated=len(updates), verified=True, **title_health(after))
        except SyncError as exc:
            result.update(code=exc.code, titles_updated=len(updates))
        save(ROOT / 'output' / 'feishu_titles.json', result)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result['status'] == 'ok' else 1
    if args.flush_pending:
        today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
        _, _, result = sync({'meta': {'report_date': today}, 'papers': []})
        save(ROOT / 'output' / 'feishu_sync.json', result)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result['status'] in ('ok', 'empty') and result['verified'] else 1
    try:
        fields, records = Feishu().snapshot()
        result = {'status': 'ok', 'records': len(records), 'fields': len(fields),
            'read_count': sum(record['fields'].get('已读') is True for record in records.values()), 'writes': 0,
            **title_health(records)}
    except SyncError as exc:
        result = {'status': 'failed', 'code': exc.code, 'writes': 0}
    save(ROOT / 'output' / 'feishu_check.json', result)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] == 'ok' else 1


if __name__ == '__main__':
    raise SystemExit(main())
