import io
import json
import sys
import tempfile
import unittest
import urllib.error
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from pubmed_client import PubMedClient, PubMedError, parse_articles
from feishu_sync import Feishu, fill_patch, text
from translation import chunks, good, Translator
import run_daily
import daily_pipeline


class Response(io.BytesIO):
    pass


class ReliabilityTests(unittest.TestCase):
    def test_rate_limit_and_429_retry(self):
        now, calls = [0.0], []
        def sleep(delay):
            now[0] += delay
        def open_request(req, timeout):
            calls.append(now[0])
            if len(calls) == 1:
                raise urllib.error.HTTPError(req.full_url, 429, "rate", {"Retry-After": "3"}, None)
            return Response(b'{"ok":true}')
        client = PubMedClient(open_request, sleep, lambda: now[0])
        client.request("esearch.fcgi", {})
        client.request("esearch.fcgi", {})
        self.assertGreaterEqual(calls[1] - calls[0], 3)
        self.assertGreaterEqual(calls[2] - calls[1], .399)

    def test_pagination_past_100(self):
        client = PubMedClient()
        with patch.object(client, "request", side_effect=[
            {"esearchresult": {"count": "230", "idlist": [str(i) for i in range(200)]}},
            {"esearchresult": {"count": "230", "idlist": [str(i) for i in range(200,230)]}}
        ]) as req:
            ids, total = client.ids("query")
        self.assertEqual((len(ids), total), (230, 230))
        self.assertEqual(req.call_args_list[1].args[1]["retstart"], 200)

    def test_partial_preserves_success(self):
        client = PubMedClient()
        with patch.object(client, "ids", return_value=(["1", "2"], 2)), patch.object(client, "fetch", return_value=([{"pmid": "1"}], ["2"])):
            result = client.search("query")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["papers"], [{"pmid": "1"}])
        self.assertEqual(result["pending_pmids"], ["2"])

    def test_search_failure_is_not_zero_success(self):
        with patch.object(PubMedClient, "ids", side_effect=PubMedError("failed")):
            self.assertEqual(PubMedClient().search("query")["status"], "failed")

    def test_xml_nested_text_and_no_abstract(self):
        xml = ET.fromstring('<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>1</PMID><Article><ArticleTitle>A <i>nested</i> title</ArticleTitle><Abstract><AbstractText Label="RESULTS">A <i>x<sup>2</sup></i> result</AbstractText></Abstract></Article></MedlineCitation></PubmedArticle><PubmedArticle><MedlineCitation><PMID>2</PMID><Article><ArticleTitle>Letter</ArticleTitle></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>')
        papers = list(parse_articles(xml))
        self.assertEqual(papers[0]["abstract"], "RESULTS: A x2 result")
        self.assertEqual(papers[1]["abstract_status"], "not_provided")

    def test_fill_preserves_manual_read_and_translation(self):
        old = {"已读": True, "中文标题": "人工修改", "日报日期": 123, "相关度得分": 0}
        patch_fields = fill_patch(old, {"已读": False, "中文标题": "自动翻译", "日报日期": 456, "相关度得分": 8, "英文摘要": "new"})
        self.assertEqual(patch_fields, {"英文摘要": "new"})

    def test_failed_lookup_prevents_any_writes(self):
        client = object.__new__(Feishu)
        with patch.object(client, "snapshot", side_effect=RuntimeError("lookup failed")), patch.object(client, "request") as request:
            with self.assertRaises(RuntimeError):
                client.sync({"papers": [{"pmid": "1"}]})
            request.assert_not_called()

    def test_pagination_failure_does_not_return_partial_records(self):
        client = object.__new__(Feishu)
        client.base = "/table"
        with patch.object(client, "request", side_effect=[{"data": {"items": [{"id": "1"}], "has_more": True, "page_token": "next"}}, RuntimeError("failed")]):
            with self.assertRaises(RuntimeError):
                client.list_all("records")

    def test_sync_repeat_is_idempotent(self):
        client = object.__new__(Feishu)
        client.base = "/table"
        snapshot = ({"PMID": 1, "中文标题": 1, "已读": 7}, {})
        report = {"meta": {"report_date": "2026-10-03"}, "papers": [{"pmid": "1", "title_zh": "测试"}]}
        def create(method, path, payload):
            return {"data": {"record": {"record_id": "record1", "fields": payload["fields"]}}}
        with patch.object(client, "request", side_effect=create) as request:
            self.assertEqual(client.sync(report, snapshot)["created"], 1)
            self.assertEqual(client.sync(report, snapshot)["unchanged"], 1)
            self.assertEqual(request.call_count, 1)

    def test_zero_results_is_success(self):
        with patch.object(PubMedClient, "ids", return_value=([], 0)):
            self.assertEqual(PubMedClient().search("query")["status"], "ok")

    def test_partial_translation_is_cached_for_retry(self):
        source = "This sentence describes a method. " * 25
        cache = {}
        def response(req, timeout):
            return Response(json.dumps({"responseStatus": 200, "responseData": {"translatedText": "这一句介绍了一种研究方法。"}}).encode())
        with patch("urllib.request.urlopen", side_effect=[response(None, None), OSError("unavailable")]):
            self.assertEqual(Translator(cache).translate(source), "")
        self.assertTrue(cache)
        with patch("urllib.request.urlopen", side_effect=response) as request:
            self.assertTrue(Translator(cache).translate(source))
            self.assertLess(request.call_count, len(chunks(source)))

    def test_text_normalization(self):
        self.assertEqual(text([{"text": "123", "type": "text"}]), "123")

    def test_no_phantom_ecog(self):
        score, hits = run_daily.relevance_score({"title": "Recognition recognition"}, ["ecog"])
        self.assertEqual((score, hits), (0, []))

    def test_synonym_and_title_bonus_once(self):
        score, hits = run_daily.relevance_score({"title": "seizures"}, ["seizure", "seizures"])
        self.assertEqual((score, hits), (6, ["seizure"]))

    def test_translation_chunks_preserve_words_and_bytes(self):
        source = "A sentence about electroencephalography. " * 30
        result = chunks(source)
        self.assertTrue(all(len(c.encode()) <= 450 for c in result))
        self.assertEqual(" ".join(result), source.strip())

    def test_translation_numeric_and_english_guard(self):
        self.assertTrue(good("Accuracy 80.69%", "准确率为80.69%"))
        self.assertFalse(good("Accuracy 80.69%", "准确率为90.69%"))
        self.assertFalse(good("English", "中文" + " this is a long sequence of untranslated english words"))

    def test_translation_cache_without_network(self):
        import hashlib
        key = hashlib.sha256("mymemory-zh-v2:Accuracy 80.69%".encode()).hexdigest()
        with patch("urllib.request.urlopen") as open_request:
            self.assertEqual(Translator({key: "准确率80.69%"}, budget=0).translate("Accuracy 80.69%"), "准确率80.69%")
            open_request.assert_not_called()

    def test_failed_dates_persist_and_retry_outside_window(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            config = folder / "config.json"
            config.write_text(json.dumps({"query": "epilepsy", "journals": {"A": "Brain"}, "group_labels": {"A": "A"}, "relevance_boost_words": [], "translate": False}))
            state = folder / "state.json"
            state.write_text(json.dumps({"groups": {"A": {"through": "2026-09-30", "failed_dates": ["2026-09-20"]}}}))
            args = ["run_daily.py", "--date", "2026-10-03", "--config", str(config), "--state", str(state), "--no-sync", "--no-translate"]
            def result(query):
                return {"papers": [], "total_results": 0, "retrieved": 0, "pending_pmids": [], "status": "failed" if '"2026-09-20"' in query else "ok"}
            with patch.object(sys, "argv", args), patch.object(run_daily, "OUTPUT_DIR", str(folder)), patch.object(daily_pipeline.CLIENT, "search", side_effect=result) as search:
                self.assertEqual(daily_pipeline.run(run_daily), 1)
            self.assertIn("2026-09-20", search.call_args_list[0].args[0])
            saved = json.loads(state.read_text())
            self.assertEqual(saved["groups"]["A"]["failed_dates"], ["2026-09-20"])


if __name__ == "__main__":
    unittest.main()
