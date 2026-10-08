"""Fail-closed PMID lookup and fill-only updates. Never reset reading state."""
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta


def text(value):
    if isinstance(value, list):
        return "".join(str(x.get("text", "")) if isinstance(x, dict) else str(x) for x in value)
    return str(value or "")


class Feishu:
    def __init__(self):
        self.app = os.environ["FEISHU_APP_TOKEN"]
        self.table = os.environ["FEISHU_TABLE_ID"]
        self.token = None
        auth = self.request("POST", "/auth/v3/tenant_access_token/internal", {
            "app_id": os.environ["FEISHU_APP_ID"], "app_secret": os.environ["FEISHU_APP_SECRET"]})
        self.token = auth["tenant_access_token"]
        self.base = f"/bitable/v1/apps/{self.app}/tables/{self.table}"

    def request(self, method, path, payload=None):
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request("https://open.feishu.cn/open-apis" + path,
            data=json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None,
            headers=headers, method=method)
        # Never retry a create after a timeout: the server may already have saved it.
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode())
        if result.get("code") != 0:
            raise RuntimeError(f"Feishu {method} failed: code {result.get('code')}")
        return result

    def list_all(self, resource):
        items, page, seen = [], None, set()
        while True:
            query = {"page_size": 100}
            if page:
                query["page_token"] = page
            result = self.request("GET", self.base + "/" + resource + "?" + urllib.parse.urlencode(query))["data"]
            items.extend(result.get("items", []))
            if not result.get("has_more"):
                return items
            page = result.get("page_token")
            if not page or page in seen:
                raise RuntimeError("Incomplete Feishu pagination; refusing writes")
            seen.add(page)

    def snapshot(self):
        fields = {x["field_name"]: x["type"] for x in self.list_all("fields")}
        if fields.get("PMID") != 1:
            raise RuntimeError("PMID must be a text field; refusing writes")
        records = {}
        for record in self.list_all("records"):
            pmid = text(record.get("fields", {}).get("PMID")).strip()
            if pmid:
                if pmid in records:
                    raise RuntimeError("Duplicate PMID in Feishu; refusing automatic writes")
                records[pmid] = record
        return fields, records

    def sync(self, report, snapshot=None, dry_run=False):
        fields, existing = snapshot or self.snapshot()
        summary = {"created": 0, "updated": 0, "unchanged": 0}
        for paper in report["papers"]:
            pmid = str(paper["pmid"])
            old = existing.get(pmid)
            desired = paper_fields(paper, report["meta"]["report_date"], fields)
            patch = fill_patch(old["fields"], desired) if old else desired
            if not patch:
                summary["unchanged"] += 1
                continue
            if not dry_run:
                if old:
                    self.request("PUT", self.base + "/records/" + old["record_id"], {"fields": patch})
                    old["fields"].update(patch)
                else:
                    result = self.request("POST", self.base + "/records", {"fields": patch})
                    existing[pmid] = result["data"]["record"]
            summary["updated" if old else "created"] += 1
        return summary


def fill_patch(old, desired):
    # Only fill absent machine fields. User changes, reading status and dates survive.
    protected = {"已读", "日报日期", "首次发现日期", "优先级", "相关度得分"}
    return {k: v for k, v in desired.items() if k not in protected and not old.get(k) and v}


def paper_fields(p, report_date, fields):
    date = p.get("first_seen", report_date)
    stamp = int(datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone(timedelta(hours=8))).timestamp() * 1000)
    values = {"PMID": str(p["pmid"]), "标题": p.get("title", ""), "中文标题": p.get("title_zh", ""),
              "英文摘要": p.get("abstract", ""), "中文摘要": p.get("abstract_zh", ""),
              "期刊": p.get("journal", ""), "作者": "；".join(p.get("authors", [])[:8]),
              "DOI": p.get("doi", ""), "优先级": p.get("priority", "浏览"),
              "相关度得分": p.get("relevance_score", 0), "命中检索": "、".join(p.get("hit_groups", [])),
              "日报日期": stamp, "首次发现日期": stamp, "已读": False}
    if p.get("url"):
        values["PubMed链接"] = {"link": p["url"], "text": p["url"]} if fields.get("PubMed链接") == 15 else p["url"]
    return {k: v for k, v in values.items() if k in fields and v != ""}
