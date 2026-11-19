import base64
import copy
from datetime import date
import gzip
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import journal_rankings as rankings
from feishu_sync import fill_blank_rankings, paper_fields
import run_daily


CATALOG = {'schema_version': 1, 'cas_version': '2025', 'jcr_release': '2026', 'jcr_data_year': '2025',
           'journals': [{'names': ['Seizure'], 'issns': ['1059-1311'],
                         'cas': '2025版｜医学｜3区',
                         'jcr': '2026发布版（2025数据）｜CLINICAL NEUROLOGY：Q2；NEUROSCIENCES：Q3'}]}


class JournalRankingTests(unittest.TestCase):
    def setUp(self):
        encoded = base64.b64encode(gzip.compress(json.dumps(CATALOG).encode())).decode()
        self.env = patch.dict(os.environ, {'JOURNAL_RANKINGS_B64': encoded,
                                           'JOURNAL_RANKINGS_REQUIRED': 'true'}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        rankings.load_catalog.cache_clear()
        self.addCleanup(rankings.load_catalog.cache_clear)

    def test_matching_issn_preserves_all_subject_quartiles(self):
        result = rankings.paper_rankings({'journal': 'a different spelling', 'issns': ['1059-1311']})
        self.assertEqual(result['cas_partition'], CATALOG['journals'][0]['cas'])
        self.assertEqual(result['jcr_quartile'], CATALOG['journals'][0]['jcr'])

    def test_unknown_journal_is_pending_instead_of_assigned_a_quartile(self):
        self.assertEqual(rankings.paper_rankings({'journal': 'Unseen'})['ranking_status'], 'unverified')

    def test_preprints_and_books_have_explicit_not_applicable_values(self):
        for publication_type, value in [('Preprint', '预印本'), ('Book Chapter', '图书章节')]:
            result = rankings.paper_rankings({'journal': 'Unseen', 'publication_types': [publication_type]})
            self.assertEqual(result['cas_partition'], f'不适用（{value}）')
            self.assertEqual(result['ranking_status'], 'not_applicable')

    def test_required_catalog_cannot_be_silently_omitted(self):
        with patch.dict(os.environ, {'JOURNAL_RANKINGS_B64': ''}):
            with self.assertRaises(ValueError):
                rankings.load_catalog()

    def test_wrong_catalog_year_is_rejected(self):
        catalog = {**CATALOG, 'jcr_data_year': '2024'}
        encoded = base64.b64encode(gzip.compress(json.dumps(catalog).encode())).decode()
        with patch.dict(os.environ, {'JOURNAL_RANKINGS_B64': encoded}):
            with self.assertRaises(ValueError):
                rankings.load_catalog()

    def test_old_queued_paper_gets_rankings_during_creation(self):
        result = paper_fields({'pmid': '42', 'journal': 'Seizure', 'first_seen': '2026-11-19'},
                              {'PMID': 1, '中科院分区': 1, 'JCR分区': 1})
        self.assertEqual(result['中科院分区'], CATALOG['journals'][0]['cas'])
        self.assertEqual(result['JCR分区'], CATALOG['journals'][0]['jcr'])

    def test_daily_json_html_and_weekly_markdown_contain_same_rankings(self):
        paper = {'pmid': '42', 'journal': 'Seizure', 'title': 'Example', '_hit_groups': ['A']}
        config = {'group_labels': {'A': 'Examples'}, 'relevance_boost_words': []}
        group = {'A': {'papers': [paper], 'complete': True, 'retrieved': 1, 'total_results': 1}}
        report = run_daily.build_report(group, [paper], config, date(2026,11,19), date(2026,11,18),
                                        {'A': (date(2026,11,16), date(2026,11,18))})
        self.assertEqual(report['papers'][0]['jcr_quartile'], CATALOG['journals'][0]['jcr'])
        self.assertIn('NEUROSCIENCES：Q3', run_daily.render_html(report, group))
        self.assertIn('2025版｜医学｜3区', run_daily.candidate_markdown(report))
        self.assertEqual(report['papers'][0]['priority'], '浏览')

    def test_repair_preserves_manual_values_and_other_columns(self):
        original = {'PMID': '42', '期刊': 'Seizure', '中科院分区': '人工指定分区',
                    '已读': True, '优先级': '必读', '中文标题': '人工标题'}
        class Client:
            base = '/table'
            writes = []
            def request(self, method, path, payload=None):
                if method == 'GET':
                    return {'data': {'record': {'fields': copy.deepcopy(original)}}}
                self.writes.append(payload)
        client = Client()
        records = {'42': {'record_id': 'record', 'fields': copy.deepcopy(original)}}
        result = fill_blank_rankings(client, {'中科院分区': 1, 'JCR分区': 1}, records)
        self.assertEqual(client.writes, [{'fields': {'JCR分区': CATALOG['journals'][0]['jcr']}}])
        self.assertEqual(result['42'], client.writes[0]['fields'])

    def test_repair_rechecks_record_identity_before_writing(self):
        class Client:
            base = '/table'
            def request(self, method, path, payload=None):
                if method != 'GET':
                    raise AssertionError('Must not write changed record')
                return {'data': {'record': {'fields': {'PMID': '42', '期刊': 'Different journal'}}}}
        records = {'42': {'record_id': 'record', 'fields': {'PMID': '42', '期刊': 'Seizure'}}}
        self.assertEqual(fill_blank_rankings(Client(), {'中科院分区': 1, 'JCR分区': 1}, records), {})


if __name__ == '__main__':
    unittest.main()
