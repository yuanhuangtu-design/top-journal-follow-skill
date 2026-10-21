import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from feishu_sync import Feishu, SyncError, merge_pending, paper_fields, sync


class FakeFeishu:
    base = '/test/table'

    def __init__(self):
        self.records = {'1': {'record_id': 'old', 'fields': {'PMID': '1', '已读': True, '中文标题': '人工标题'}}}
        self.writes = []
        self.ambiguous = False

    def snapshot(self):
        return {'PMID': 1, '标题': 1, '已读': 7, '日报日期': 5}, copy.deepcopy(self.records)

    def request(self, method, path, payload):
        self.writes.append((method, path, payload))
        for row in payload['records']:
            self.records[row['fields']['PMID']] = {'fields': row['fields']}
        if self.ambiguous:
            raise SyncError('network_or_invalid_response')
        return {'data': {}}


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'pending.json'
        self.report = {'meta': {'report_date': '2026-10-21'}, 'papers': [
            {'pmid': '1', 'title': 'Existing'}, {'pmid': '2', 'title': 'New'}]}
        self.env = patch.dict(os.environ, {'FEISHU_REQUIRED': 'true'}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_adds_only_missing_and_preserves_read_state(self):
        client = FakeFeishu()
        before = copy.deepcopy(client.records['1'])
        created, expected, diag = sync(self.report, self.path, lambda: client)
        self.assertEqual((created, expected, diag['verified']), (1, 1, True))
        self.assertEqual(client.records['1'], before)
        self.assertEqual(client.writes[0][0], 'POST')
        self.assertEqual(json.loads(self.path.read_text())['papers'], [])

    def test_timeout_after_server_save_is_not_retried_or_duplicated(self):
        client = FakeFeishu()
        client.ambiguous = True
        _, _, first = sync(self.report, self.path, lambda: client)
        self.assertEqual(first['status'], 'failed')
        self.assertEqual(len(client.writes), 1)
        self.assertEqual(len(json.loads(self.path.read_text())['papers']), 1)
        client.ambiguous = False
        _, _, second = sync(self.report, self.path, lambda: client)
        self.assertEqual(second['status'], 'empty')
        self.assertEqual(len(client.writes), 1)
        self.assertEqual(json.loads(self.path.read_text())['papers'], [])

    def test_auth_failure_retains_papers_without_claiming_zero_expected(self):
        def denied():
            raise SyncError(10014)
        _, expected, diag = sync(self.report, self.path, denied)
        self.assertIsNone(expected)
        self.assertEqual(diag['code'], 10014)
        self.assertEqual(len(json.loads(self.path.read_text())['papers']), 2)

    def test_lookup_failure_prevents_all_writes(self):
        client = FakeFeishu()
        with patch.object(client, 'snapshot', side_effect=SyncError(91403)):
            _, _, diag = sync(self.report, self.path, lambda: client)
        self.assertEqual(diag['code'], 91403)
        self.assertFalse(client.writes)

    def test_post_write_lookup_failure_keeps_unverified_papers(self):
        client = FakeFeishu()
        with patch.object(client, 'snapshot', side_effect=[client.snapshot(), SyncError(91403)]):
            _, _, diag = sync(self.report, self.path, lambda: client)
        self.assertFalse(diag['verified'])
        self.assertEqual(diag['stage'], 'verify')
        self.assertEqual(len(json.loads(self.path.read_text())['papers']), 1)

    def test_missing_explicit_table_is_configuration_failure(self):
        with patch.dict(os.environ, {'FEISHU_APP_ID': 'new', 'FEISHU_APP_SECRET': 'secret', 'FEISHU_APP_TOKEN': 'base'}):
            with self.assertRaises(SyncError) as caught:
                Feishu()
        self.assertEqual(caught.exception.code, 'incomplete_config')

    def test_old_app_is_rejected_before_network(self):
        config = dict(zip(('FEISHU_APP_ID', 'FEISHU_APP_SECRET', 'FEISHU_APP_TOKEN', 'FEISHU_TABLE_ID'), ('old', 'secret', 'base', 'table')))
        config['FEISHU_EXPECTED_APP_ID'] = 'new'
        with patch.dict(os.environ, config), patch('urllib.request.urlopen') as network:
            with self.assertRaises(SyncError) as caught:
                Feishu()
        self.assertEqual(caught.exception.code, 'app_id_mismatch')
        network.assert_not_called()

    def test_incomplete_pagination_refuses_partial_snapshot(self):
        client = object.__new__(Feishu)
        client.base = '/test'
        with patch.object(client, 'request', return_value={'data': {'items': [], 'has_more': True}}):
            with self.assertRaises(SyncError):
                client.list_all('records')

    def test_repeated_page_token_is_rejected(self):
        client = object.__new__(Feishu)
        client.base = '/test'
        with patch.object(client, 'request', return_value={'data': {'items': [], 'has_more': True, 'page_token': 'same'}}):
            with self.assertRaises(SyncError):
                client.list_all('records')

    def test_duplicate_rich_text_pmids_are_rejected(self):
        client = object.__new__(Feishu)
        with patch.object(client, 'list_all', side_effect=[
            [{'field_name': 'PMID', 'type': 1}, {'field_name': '标题', 'type': 1}],
            [{'fields': {'PMID': [{'text': '1'}]}}, {'fields': {'PMID': '1'}}]]):
            with self.assertRaises(SyncError):
                client.snapshot()

    def test_pending_keeps_earliest_date_and_group_labels(self):
        previous = {'papers': [{'pmid': '2', 'title': 'Old', 'first_seen': '2026-10-15', 'hit_groups': ['A']}]}
        report = {'meta': {'report_date': '2026-10-21'}, 'papers': [{'pmid': '2', 'title': 'New', 'hit_groups': ['D']}],
            'group_records': [{'group': 'A', 'label': 'Epilepsy'}, {'group': 'D', 'label': 'Closed loop'}]}
        paper = merge_pending(previous, report)['2']
        self.assertEqual(paper['first_seen'], '2026-10-15')
        self.assertEqual(paper['hit_groups'], ['A', 'D'])
        self.assertEqual(paper['hit_group_labels'], ['Closed loop', 'Epilepsy'])

    def test_beijing_date_and_english_translation_fallback(self):
        result = paper_fields({'pmid': '2', 'first_seen': '2026-10-21', 'title_zh': 'English fallback'},
            {'PMID': 1, '日报日期': 5, '中文标题': 1})
        self.assertEqual(result['日报日期'], 1792512000000)
        self.assertNotIn('中文标题', result)


if __name__ == '__main__':
    unittest.main()
