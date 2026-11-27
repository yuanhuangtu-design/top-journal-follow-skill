import base64
from copy import deepcopy
import gzip
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import import_review
import journal_rankings as ranks
import retrieval_policy
import review_selection as selection
import run_daily

POLICY = {'cas_version': '2025', 'cas_major_partition': 1}
CATALOG = {'schema_version': 1, 'cas_version': '2025', 'jcr_release': '2026', 'jcr_data_year': '2025',
    'journals': [{'names': ['High Review'], 'issns': ['1111-1111'], 'cas': '2025版｜医学｜1区',
                  'jcr': '2026发布版（2025数据）｜NEUROSCIENCES：Q4'},
                 {'names': ['Other Review'], 'issns': ['2222-2222'], 'cas': '2025版｜医学｜2区',
                  'jcr': '2026发布版（2025数据）｜NEUROSCIENCES：Q1'}]}


class ReviewSelectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)/'candidates.json'
        self.encoded = base64.b64encode(gzip.compress(json.dumps(CATALOG).encode())).decode()
        env = patch.dict(os.environ, {'JOURNAL_RANKINGS_B64': self.encoded}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        ranks.load_catalog.cache_clear()
        self.addCleanup(ranks.load_catalog.cache_clear)

    def paper(self, pmid, journal='High Review'):
        return {'pmid': pmid, 'journal': journal, 'title': 'A review of epilepsy',
                'publication_types': ['Journal Article','Review']}

    def result(self, papers, complete=True, total=None):
        return {'papers': papers, 'retrieved': len(papers), 'complete': complete,
                'total_results': len(papers) if total is None else total}

    def select(self, result, **kwargs):
        return selection.select_group(result, 'ER', POLICY, '2026-11-27', self.path, **kwargs)

    def test_cas1_is_accepted_even_with_jcr_q4(self):
        self.assertEqual(selection.decision(self.paper('1'), POLICY), 'eligible')

    def test_jcr_q1_cannot_admit_cas2_or_spoofed_incoming_rankings(self):
        paper = {**self.paper('2','Other Review'), 'cas_partition': '2025版｜医学｜1区'}
        self.assertEqual(selection.decision(paper, POLICY), 'cas_not_1')

    def test_unverified_is_held_and_raw_incompleteness_is_preserved(self):
        result = self.select(self.result([self.paper('3','Unknown')], complete=False, total=20))
        self.assertEqual(result['papers'], [])
        self.assertFalse(result['complete'])
        self.assertEqual(result['ranking_selection']['source_total'], 20)
        self.assertEqual(selection.read_state(self.path)['papers']['3']['status'], 'cas_unverified')

    def test_candidate_survives_failure_and_is_promoted_once_after_verification(self):
        self.select(self.result([self.paper('3','Unknown')]))
        known = {'cas_partition': '2025版｜医学｜1区', 'jcr_quartile': '待核验'}
        with patch.object(selection,'paper_rankings',return_value=known):
            accepted = self.select(self.result([]))
            self.assertEqual(accepted['ranking_selection']['promoted_candidates'], 1)
            report = {'meta': {'report_date':'2026-11-28','feishu_diag': {'verified': False, 'pending': 1}},
                      'papers':[{'pmid':'3','hit_groups':['ER']}]}
            selection.mark_synced(report,self.path)
            self.assertEqual(len(self.select(self.result([]))['papers']), 1)
            report['meta']['feishu_diag'] = {'verified': True, 'pending': 0}
            selection.mark_synced(report,self.path)
            self.assertEqual(self.select(self.result([]))['papers'], [])

    def test_selected_import_does_not_promote_unrequested_candidates(self):
        self.select(self.result([self.paper('1')]))
        result = self.select(self.result([self.paper('2')]),retry_backlog=False)
        self.assertEqual([p['pmid'] for p in result['papers']], ['2'])

    def test_other_groups_remain_intact_when_review_gate_rejects_same_pmid(self):
        paper = self.paper('2','Other Review')
        groups = {'B1': self.result([paper]), 'ER': self.select(self.result([paper]))}
        merged = run_daily.merge_dedupe(groups)
        self.assertEqual(merged[0]['_hit_groups'], ['B1'])

    def test_split_catalog_is_loaded_and_missing_half_fails_closed(self):
        middle = len(self.encoded)//2
        with patch.dict(os.environ, {'JOURNAL_RANKINGS_B64': '',
            'JOURNAL_RANKINGS_CAS1_B64_PART1': self.encoded[:middle],
            'JOURNAL_RANKINGS_CAS1_B64_PART2': self.encoded[middle:]}):
            ranks.load_catalog.cache_clear()
            self.assertEqual(ranks.paper_rankings(self.paper('1'))['cas_partition'], '2025版｜医学｜1区')
            ranks.load_catalog.cache_clear()
            with patch.dict(os.environ, {'JOURNAL_RANKINGS_CAS1_B64_PART2': ''}):
                with self.assertRaises(ValueError):
                    ranks.load_catalog()

    def test_policy_change_invalidates_group_coverage(self):
        config = {'query':'epilepsy','journals':{'ER':''},'strategy_version':'v1'}
        before = retrieval_policy.fingerprint(config,'ER')
        config['group_ranking_filters'] = {'ER': POLICY}
        self.assertNotEqual(before,retrieval_policy.fingerprint(config,'ER'))

    def test_selected_collection_keeps_daily_report_and_progress_unchanged(self):
        root = Path(self.directory.name)
        (root/'reports').mkdir()
        for name in ('latest.json','search_progress.json'):
            (root/'reports'/name).write_text('sentinel',encoding='utf-8')
        config = {'group_queries':{'ER':'epilep*[Title/Abstract] AND Review[pt]'},
                  'group_ranking_filters':{'ER':POLICY}, 'group_labels':{'ER':'Reviews'},
                  'relevance_boost_words':[]}
        config_path = root/'config.json'
        config_path.write_text(json.dumps(config),encoding='utf-8')
        def translate_titles(papers, fn):
            for paper in papers:
                paper['_title_zh'] = '癫痫综述'
            return {'missing_title_pmids':[]}
        def filter_result(result, group, policy, date, **kwargs):
            return selection.select_group(result,group,policy,date,self.path,**kwargs)
        diag = {'verified':True,'status':'ok','pending':0}
        with patch.object(sys,'argv',['import_review.py','--pmids','1']), \
             patch.object(run_daily,'BASE_DIR',str(root)), \
             patch.object(run_daily,'CONFIG_PATH',str(config_path)), \
             patch.object(run_daily,'run_search',return_value=self.result([self.paper('1')])), \
             patch.object(run_daily,'translate_with_timeout',return_value='中文摘要'), \
             patch.object(import_review,'prepare_titles',side_effect=translate_titles), \
             patch.object(import_review,'select_group',side_effect=filter_result), \
             patch.object(import_review,'mark_synced') as mark, \
             patch.object(run_daily,'write_feishu',return_value=(1,1,diag)) as writer:
            import_review.main()
        self.assertEqual(writer.call_count,1)
        self.assertTrue((root/'reports/selected_reviews_latest.json').exists())
        for name in ('latest.json','search_progress.json'):
            self.assertEqual((root/'reports'/name).read_text(encoding='utf-8'),'sentinel')


if __name__ == '__main__':
    unittest.main()
