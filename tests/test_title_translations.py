import copy
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from title_translations import load_state, prepare_titles, save_state
from feishu_sync import Feishu, SyncError, fill_blank_titles
import run_daily


class TitleTranslationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'titles.json'

    def test_today_titles_precede_old_backlog(self):
        save_state({'translations': {}, 'pending': {'1': {'title': 'Yesterday'}}}, self.path)
        calls = []
        def translate(source):
            calls.append(source)
            return '这是中文标题'
        papers = [{'pmid': '2', 'title': 'Today A'}, {'pmid': '3', 'title': 'Today D'}]
        result = prepare_titles(papers, translate, self.path)
        self.assertEqual(calls, ['Today A', 'Today D', 'Yesterday'])
        self.assertEqual(result['pending_titles'], 0)

    def test_cached_translation_avoids_network_and_persists(self):
        save_state({'translations': {'1': {'title': 'Original', 'title_zh': '原始中文标题'}}, 'pending': {}}, self.path)
        papers = [{'pmid': '1', 'title': 'Original'}]
        with patch('builtins.print'), patch('urllib.request.urlopen') as network:
            result = prepare_titles(papers, lambda _: self.fail('Cached title was translated again'), self.path)
        network.assert_not_called()
        self.assertEqual(papers[0]['_title_zh'], '原始中文标题')
        self.assertEqual(result['status'], 'ok')

    def test_source_change_never_reuses_old_translation(self):
        save_state({'translations': {'1': {'title': 'Old', 'title_zh': '旧的中文标题'}}, 'pending': {}}, self.path)
        papers = [{'pmid': '1', 'title': 'Changed'}]
        result = prepare_titles(papers, lambda _: '', self.path)
        self.assertEqual(papers[0]['_title_zh'], '')
        self.assertEqual(result['missing_title_pmids'], ['1'])
        self.assertEqual(load_state(self.path)['pending']['1']['title'], 'Changed')

    def test_failure_is_queued_and_retried_on_later_run(self):
        papers = [{'pmid': '1', 'title': 'An English title'}]
        prepare_titles(papers, lambda _: 'An English title', self.path)
        self.assertIn('1', load_state(self.path)['pending'])
        result = prepare_titles([], lambda _: '合格的中文标题', self.path)
        self.assertEqual(result['pending_titles'], 0)
        self.assertEqual(load_state(self.path)['translations']['1']['title_zh'], '合格的中文标题')

    def test_quota_response_stops_retrying_provider(self):
        response = io.BytesIO(json.dumps({'responseStatus': 429, 'quotaFinished': True}).encode())
        with patch.object(run_daily, 'TRANSLATION_QUOTA_EXHAUSTED', False), patch('urllib.request.urlopen', return_value=response) as network:
            self.assertEqual(run_daily.translate_text('First English title'), '')
            self.assertEqual(run_daily.translate_text('Second English title'), '')
            self.assertEqual(network.call_count, 1)

    def test_old_backlog_is_bounded_and_unprocessed_titles_are_kept(self):
        save_state({'translations': {}, 'pending': {str(i): {'title': 'Old ' + str(i)} for i in range(20)}}, self.path)
        calls = []
        result = prepare_titles([{'pmid': '100', 'title': 'Today'}],
            lambda source: calls.append(source) or '正确的中文标题', self.path, max_backlog=3)
        self.assertEqual(calls[0], 'Today')
        self.assertEqual(len(calls), 4)
        self.assertEqual(result['pending_titles'], 17)

    def test_http_quota_error_stops_retrying_provider(self):
        error = urllib.error.HTTPError('https://example.invalid', 429, 'Too Many Requests', {}, None)
        with patch.object(run_daily, 'TRANSLATION_QUOTA_EXHAUSTED', False), patch('urllib.request.urlopen', side_effect=error) as network:
            self.assertEqual(run_daily.translate_text('First English title'), '')
            self.assertEqual(run_daily.translate_text('Second English title'), '')
            self.assertEqual(network.call_count, 1)


class TitleRepairTests(unittest.TestCase):
    def setUp(self):
        self.record = {'record_id': 'row', 'fields': {'PMID': '1', '标题': 'Original',
            '中文标题': '', '已读': True, '优先级': '人工优先级', '笔记': '我的笔记'}}
        self.entry = {'1': {'title': 'Original', 'title_zh': '补齐后的中文标题'}}

    def client(self, changed=None):
        class Client:
            base = '/test'
            def __init__(inner):
                inner.record = copy.deepcopy(self.record)
                inner.writes = []
            def request(inner, method, path, payload=None):
                if method == 'GET':
                    if changed:
                        inner.record['fields'].update(changed)
                    return {'data': {'record': copy.deepcopy(inner.record)}}
                inner.writes.append(payload)
                inner.record['fields'].update(payload['fields'])
                return {'code': 0}
        return Client()

    def test_patch_contains_only_chinese_title(self):
        client = self.client()
        original = copy.deepcopy(client.record['fields'])
        result = fill_blank_titles(client, {'1': self.record}, self.entry)
        self.assertEqual(result, {'1': '补齐后的中文标题'})
        self.assertEqual(client.writes, [{'fields': {'中文标题': '补齐后的中文标题'}}])
        for key in ('标题', 'PMID', '已读', '优先级', '笔记'):
            self.assertEqual(client.record['fields'][key], original[key])

    def test_manual_title_added_after_snapshot_is_preserved(self):
        client = self.client({'中文标题': '刚刚手写的中文标题'})
        self.assertEqual(fill_blank_titles(client, {'1': self.record}, self.entry), {})
        self.assertFalse(client.writes)

    def test_changed_pmid_or_source_title_prevents_update(self):
        for changed in ({'PMID': '2'}, {'标题': 'Different title'}):
            client = self.client(changed)
            self.assertEqual(fill_blank_titles(client, {'1': self.record}, self.entry), {})
            self.assertFalse(client.writes)

    def test_existing_title_is_never_replaced(self):
        self.record['fields']['中文标题'] = '已有的中文标题'
        client = self.client()
        self.assertEqual(fill_blank_titles(client, {'1': self.record}, self.entry), {})
        self.assertFalse(client.writes)

    def test_timeout_after_saved_title_reads_back_without_second_write(self):
        client = self.client()
        original = client.request
        def timeout_after_save(method, path, payload=None):
            result = original(method, path, payload)
            if method == 'PUT':
                raise SyncError('network_or_invalid_response')
            return result
        client.request = timeout_after_save
        with patch('feishu_sync.time.sleep'):
            result = fill_blank_titles(client, {'1': self.record}, self.entry)
        self.assertEqual(result, {'1': '补齐后的中文标题'})
        self.assertEqual(len(client.writes), 1)

    def test_failed_write_rechecks_manual_title_before_retry(self):
        client = self.client()
        original = client.request
        def timeout_then_manual_edit(method, path, payload=None):
            if method == 'PUT':
                client.record['fields']['中文标题'] = '刚刚人工填写的标题'
                raise SyncError('network_or_invalid_response')
            return original(method, path, payload)
        client.request = timeout_then_manual_edit
        with patch('feishu_sync.time.sleep'):
            self.assertEqual(fill_blank_titles(client, {'1': self.record}, self.entry), {})
        self.assertEqual(client.record['fields']['中文标题'], '刚刚人工填写的标题')
        self.assertFalse(client.writes)

    def test_reads_retry_but_create_does_not_retry(self):
        client = object.__new__(Feishu)
        for method, path, calls in [('GET', '/records/row', 3), ('POST', '/records/search?page_size=100', 3), ('POST', '/records/batch_create', 1)]:
            with patch.object(client, '_request_once', side_effect=SyncError('network_or_invalid_response')) as once, patch('feishu_sync.time.sleep'):
                with self.assertRaises(SyncError):
                    client.request(method, path)
                self.assertEqual(once.call_count, calls)


if __name__ == '__main__':
    unittest.main()
