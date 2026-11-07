import copy
import json
import sys
import tempfile
import unittest
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import literature_search as search
import retrieval_policy as policy
import run_daily

ARTICLE = '''<PubmedArticle><MedlineCitation><PMID>42</PMID><Article>
<Journal><Title>Neurology</Title><ISOAbbreviation>Neurology</ISOAbbreviation><JournalIssue><PubDate><Year>2026</Year></PubDate></JournalIssue></Journal>
<ArticleTitle>New <i>epilepticus</i> report</ArticleTitle>
<AuthorList><Author><LastName>Smith</LastName><Initials>A</Initials></Author></AuthorList>
<PublicationTypeList><PublicationType>Editorial</PublicationType></PublicationTypeList>
</Article></MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="doi">10.1/own</ArticleId></ArticleIdList>
<ReferenceList><Reference><ArticleIdList><ArticleId IdType="doi">10.2/reference</ArticleId></ArticleIdList></Reference></ReferenceList>
</PubmedData></PubmedArticle>'''


class PubMedCoverageTests(unittest.TestCase):
    def test_mixed_article_and_updated_book_batch_is_complete(self):
        book='''<PubmedBookArticle><BookDocument><PMID>43</PMID>
          <Book><BookTitle>GeneReviews<sup>R</sup></BookTitle></Book><ArticleTitle>Genetic epilepsy</ArticleTitle>
          <AuthorList><Author><LastName>Jones</LastName><Initials>B</Initials></Author></AuthorList>
          <ContributionDate><Year>2013</Year><Month>4</Month><Day>4</Day></ContributionDate>
          <DateRevised><Year>2026</Year><Month>11</Month><Day>5</Day></DateRevised>
          <Abstract><AbstractText>Updated <i>review</i>.</AbstractText></Abstract>
          </BookDocument></PubmedBookArticle>'''
        with patch.object(search,'call_pubmed_xml',return_value='<PubmedArticleSet>'+ARTICLE+book+'</PubmedArticleSet>'),patch.object(search.time,'sleep'):
            papers=search.fetch_summaries(['42','43'])
        self.assertEqual([p['pmid'] for p in papers],['42','43'])
        self.assertEqual(papers[1]['publication_label'],'图书章节/更新')
        self.assertEqual(papers[1]['abstract'],'Updated review.')
        self.assertEqual(papers[1]['pubdate'],'2013-4-4（更新 2026-11-5）')

    def test_zero_results_is_complete_search_not_request_failure(self):
        with patch.object(search, 'call_pubmed_api', return_value={'esearchresult': {'count':'0','idlist':[]}}):
            self.assertEqual(search.search_pubmed('test', 500), ([], 0))
        with patch.object(search, 'call_pubmed_api', return_value=None):
            with self.assertRaises(search.SearchError): search.search_pubmed('test', 500)

    def test_pagination_fetches_beyond_one_hundred(self):
        calls=[]
        def api(url):
            query=urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            start=int(query['retstart'][0]); size=int(query['retmax'][0]); calls.append(start)
            return {'esearchresult':{'count':'235','idlist':list(map(str,range(start,min(start+size,235))))}}
        with patch.object(search, 'call_pubmed_api', side_effect=api), patch.object(search.time, 'sleep'):
            ids,total=search.search_pubmed('test',500)
        self.assertEqual((len(ids),total,calls),(235,235,[0,100,200]))

    def test_repeated_page_fails_closed(self):
        result={'esearchresult':{'count':'101','idlist':list(map(str,range(100)))}}
        with patch.object(search, 'call_pubmed_api', return_value=result), patch.object(search.time,'sleep'):
            with self.assertRaises(search.SearchError): search.search_pubmed('test',500)

    def test_declared_count_cannot_silently_lose_later_page(self):
        results=[{'esearchresult':{'count':'101','idlist':list(map(str,range(100)))}},
                 {'esearchresult':{'count':'101','idlist':[]}}]
        with patch.object(search,'call_pubmed_api',side_effect=results),patch.object(search.time,'sleep'):
            with self.assertRaises(search.SearchError): search.search_pubmed('test',500)

    def test_search_limit_is_explicitly_incomplete(self):
        with patch.object(search,'search_pubmed',return_value=(['42'],2)),patch.object(search,'fetch_summaries',return_value=[{'pmid':'42'}]):
            self.assertFalse(search.search('test',max_results=1)['complete'])

    def test_no_abstract_editorial_is_preserved_with_own_doi(self):
        article=search.parse_article(ET.fromstring(ARTICLE))
        self.assertEqual(article['abstract_status'],'not_provided')
        self.assertEqual(article['abstract'],'')
        self.assertEqual(article['publication_label'],'述评/社论')
        self.assertEqual(article['doi'],'10.1/own')
        self.assertEqual(article['title'],'New epilepticus report')

    def test_nested_abstract_text_is_not_lost(self):
        text=ARTICLE.replace('<AuthorList>', '<Abstract><AbstractText Label="METHODS">A <i>mixed</i> model with <sup>2</sup> levels.</AbstractText></Abstract><AuthorList>')
        self.assertEqual(search.parse_article(ET.fromstring(text))['abstract'],'METHODS: A mixed model with 2 levels.')

    def test_detail_response_must_include_all_requested_pmids(self):
        with patch.object(search,'call_pubmed_xml',return_value='<PubmedArticleSet>'+ARTICLE+'</PubmedArticleSet>'),patch.object(search.time,'sleep'):
            with self.assertRaises(search.SearchError): search.fetch_summaries(['42','43'])

    def test_missing_original_abstract_does_not_trigger_refetch(self):
        papers=[{'pmid':'42','abstract':'','abstract_status':'not_provided'}]
        with patch.object(run_daily.urllib.request,'urlopen') as network:
            run_daily.refetch_missing_abstracts(papers)
        network.assert_not_called()


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.config={'query':'epilepsy','journals':{'A':'Neurology','B':'Brain'},
                     'group_queries':{},'group_labels':{},'relevance_boost_words':[],'strategy_version':'v1'}
        self.state={'groups':{'A':{'through':'2026-10-20','fingerprint':policy.fingerprint(self.config,'A')}}}

    def test_outage_catchup_resumes_without_skipping_to_today(self):
        self.assertEqual(policy.coverage_window(self.config,'A',date(2026,11,6),self.state),
                         (date(2026,10,19),date(2026,10,25)))

    def test_first_ever_failure_does_not_lose_initial_window(self):
        state=policy.remember_attempt({'groups':{}},self.config,'A',date(2026,11,4))
        self.assertNotIn('through',state['groups']['A'])
        self.assertEqual(policy.coverage_window(self.config,'A',date(2026,11,15),state),
                         (date(2026,11,4),date(2026,11,10)))

    def test_query_change_uses_default_overlap_without_large_surprise_import(self):
        self.config['strategy_version']='v2'
        self.assertEqual(policy.coverage_window(self.config,'A',date(2026,11,6),self.state),
                         (date(2026,11,4),date(2026,11,6)))

    def test_only_fully_retrieved_and_synced_groups_advance(self):
        report={'group_records':[{'group':'A','status':'partial','edat_end':'2026-11-06'},
                                 {'group':'B','status':'ok','edat_end':'2026-11-06'}]}
        before=copy.deepcopy(self.state)
        self.assertEqual(policy.advance_progress(copy.deepcopy(before),self.config,report,False),before)
        after=policy.advance_progress(self.state,self.config,report,True)
        self.assertEqual(after['groups']['A'],before['groups']['A'])
        self.assertEqual(after['groups']['B']['through'],'2026-11-06')

    def test_historical_backfill_does_not_regress_cursor(self):
        r={'group_records':[{'group':'A','status':'ok','edat_end':'2026-10-19'}]}
        self.assertEqual(policy.advance_progress(self.state,self.config,r,True)['groups']['A']['through'],'2026-10-20')

    def test_invalid_or_one_sided_backfill_is_rejected(self):
        for start,end in [(date(2026,11,1),None),(date(2026,11,7),date(2026,11,7)),(date(2026,11,5),date(2026,11,4))]:
            with self.assertRaises(ValueError): policy.coverage_window(self.config,'A',date(2026,11,6),self.state,start,end)

    def test_partial_report_is_marked_as_partial(self):
        report=run_daily.build_report({'A':{'complete':False,'retrieved':1,'total_results':2,'papers':[{'pmid':'42'}]}},[],self.config,date(2026,11,7),date(2026,11,6))
        self.assertFalse(report['meta']['search_complete'])
        self.assertEqual(report['group_records'][0]['status'],'partial')

    def test_weekly_candidates_never_call_writers_or_progress_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            config=copy.deepcopy(self.config)
            config['group_queries']['A']='epilep*[Title/Abstract]'
            config_path=Path(directory)/'config.json'; config_path.write_text(json.dumps(config),encoding='utf-8')
            with patch.object(sys,'argv',['run_daily.py','--candidate-pool','--config',str(config_path)]), \
                 patch.object(run_daily,'BASE_DIR',directory),patch.object(run_daily,'load_progress',return_value={'groups':{}}), \
                 patch.object(run_daily,'run_search',return_value={'complete':True,'retrieved':0,'total_results':0,'papers':[]}), \
                 patch.object(run_daily,'write_feishu') as feishu,patch.object(run_daily,'write_notion') as notion, \
                 patch.object(run_daily,'save_progress') as progress:
                run_daily.main()
            feishu.assert_not_called(); notion.assert_not_called(); progress.assert_not_called()
            self.assertTrue(json.loads((Path(directory)/'reports/weekly_candidates.json').read_text(encoding='utf-8'))['meta']['candidate_only'])
            self.assertIn('每周文献候选',(Path(directory)/'reports/weekly_candidates.md').read_text(encoding='utf-8'))

    def test_keyword_score_cannot_assign_must_read(self):
        self.assertEqual(run_daily.grade_paper(100),'推荐')


if __name__=='__main__': unittest.main()
