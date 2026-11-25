import copy
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from daily_schedule_guard import needs_daily_run
from retrieval_policy import fingerprint


class DailyScheduleGuardTests(unittest.TestCase):
    def setUp(self):
        self.today = '2026-11-25'
        self.config = {'query': 'epilepsy', 'journals': {'A': 'Neurology'}, 'strategy_version': 'v1'}
        self.report = {'meta': {'report_date': self.today, 'search_complete': True,
                               'feishu_diag': {'verified': True, 'status': 'ok', 'pending': 0},
                               'title_translation_diag': {'missing_title_pmids': []}}}
        self.pending = {'papers': []}
        self.progress = {'groups': {'A': {'through': '2026-11-24',
                                        'fingerprint': fingerprint(self.config, 'A')}}}

    def decide(self, event='schedule', mode='daily'):
        return needs_daily_run(event, mode, self.report, self.pending, self.config,
                               self.progress, self.today)[0]

    def test_completed_report_skips_later_scheduled_attempts(self):
        self.assertFalse(self.decide())

    def test_successful_zero_new_papers_also_skips(self):
        self.report['meta']['feishu_diag']['status'] = 'empty'
        self.assertFalse(self.decide())

    def test_manual_requests_and_weekly_lists_always_run(self):
        self.assertTrue(self.decide(event='workflow_dispatch'))
        self.assertTrue(self.decide(mode='weekly_candidates'))

    def test_previous_day_report_needs_a_run(self):
        self.report['meta']['report_date'] = '2026-11-24'
        self.assertTrue(self.decide())

    def test_partial_search_or_failed_sync_retries(self):
        complete = copy.deepcopy(self.report)
        self.report['meta']['search_complete'] = False
        self.assertTrue(self.decide())
        self.report = complete
        self.report['meta']['feishu_diag']['verified'] = False
        self.assertTrue(self.decide())

    def test_pending_papers_or_translations_retry(self):
        self.pending['papers'] = [{'pmid': '42'}]
        self.assertTrue(self.decide())
        self.pending['papers'] = []
        self.report['meta']['title_translation_diag']['missing_title_pmids'] = ['42']
        self.assertTrue(self.decide())

    def test_changed_query_retries_even_with_same_version(self):
        self.config['query'] = 'seizure'
        self.assertTrue(self.decide())

    def test_missing_titles_in_the_table_trigger_a_retry(self):
        self.report['meta']['feishu_diag']['missing_titles'] = 1
        self.assertTrue(self.decide())

    def test_catchup_that_has_not_reached_yesterday_keeps_retrying(self):
        self.progress['groups']['A']['through'] = '2026-11-23'
        self.assertTrue(self.decide())

    def test_missing_saved_report_cannot_skip(self):
        self.report = {}
        self.assertTrue(self.decide())


if __name__ == '__main__':
    unittest.main()
