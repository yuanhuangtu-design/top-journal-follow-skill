#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
飞书连接诊断脚本（只读探测，不写入业务数据）
================================================
在 GitHub Actions 中用与正式任务相同的应用身份（FEISHU_APP_* secrets），
逐步检测：环境变量是否存在 -> tenant_access_token -> 列数据表 -> 读字段 -> 读记录 -> 写权限探针。
结果写入 reports/feishu_diag.json 并打印，供 Actions 日志与仓库文件双重排查。
"""
import json
import os
import urllib.request
import urllib.error
import datetime

FEISHU_API = "https://open.feishu.cn/open-apis"


def req(method, path, token, payload=None):
    url = FEISHU_API + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")
        return {"_http_error": e.code, "_body": body[:500]}
    except Exception as e:
        return {"_exception": str(e)}


def finish(diag):
    print(json.dumps(diag, ensure_ascii=False, indent=2))
    os.makedirs("reports", exist_ok=True)
    with open("reports/feishu_diag.json", "w", encoding="utf-8") as fh:
        json.dump(diag, fh, ensure_ascii=False, indent=2)


def main():
    diag = {"run_at_utc": datetime.datetime.utcnow().isoformat() + "Z", "steps": {}}
    app_id = os.environ.get("FEISHU_APP_ID", "").strip()
    app_secret = os.environ.get("FEISHU_APP_SECRET", "").strip()
    app_token = os.environ.get("FEISHU_APP_TOKEN", "").strip()
    table_id = os.environ.get("FEISHU_TABLE_ID", "").strip()

    # 不打印 secret，仅报告是否存在及长度
    diag["env"] = {
        "FEISHU_APP_ID_set": bool(app_id),
        "FEISHU_APP_ID_len": len(app_id),
        "FEISHU_APP_SECRET_set": bool(app_secret),
        "FEISHU_APP_SECRET_len": len(app_secret),
        "FEISHU_APP_TOKEN_set": bool(app_token),
        "FEISHU_APP_TOKEN": app_token,
        "FEISHU_TABLE_ID_set": bool(table_id),
        "FEISHU_TABLE_ID": table_id,
    }

    # 1) tenant_access_token
    tok = req("POST", "/auth/v3/tenant_access_token/internal", None,
              {"app_id": app_id, "app_secret": app_secret})
    token = tok.get("tenant_access_token") if isinstance(tok, dict) and tok.get("code") == 0 else None
    diag["steps"]["tenant_token"] = {
        "code": tok.get("code") if isinstance(tok, dict) else None,
        "msg": tok.get("msg") if isinstance(tok, dict) else str(tok)[:300],
        "token_ok": bool(token),
    }
    if not token:
        finish(diag)
        return

    # 2) 列出该 base 下的数据表
    t = req("GET", f"/bitable/v1/apps/{app_token}/tables", token)
    if isinstance(t, dict) and t.get("code") == 0:
        diag["steps"]["list_tables"] = {
            "code": 0,
            "tables": [{"id": x.get("table_id"), "name": x.get("name")}
                       for x in t.get("data", {}).get("items", [])],
        }
    else:
        diag["steps"]["list_tables"] = {"code": t.get("code"), "msg": t.get("msg")} if isinstance(t, dict) else {"raw": str(t)[:300]}

    # 3) 读目标表字段
    f = req("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", token)
    if isinstance(f, dict) and f.get("code") == 0:
        diag["steps"]["list_fields"] = {
            "code": 0,
            "field_count": len(f.get("data", {}).get("items", [])),
            "fields": [x.get("field_name") for x in f.get("data", {}).get("items", [])],
        }
    else:
        diag["steps"]["list_fields"] = {"code": f.get("code"), "msg": f.get("msg")} if isinstance(f, dict) else {"raw": str(f)[:300]}

    # 4) 读记录（读权限）
    r = req("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/records?page_size=1", token)
    if isinstance(r, dict):
        diag["steps"]["list_records"] = {
            "code": r.get("code"), "msg": r.get("msg"),
            "total": r.get("data", {}).get("total"),
        }

    # 5) 写权限探针：故意使用不存在的字段名。
    #    有写权限 -> 返回字段类错误（如 1254040/1254000）；无写权限 -> 返回权限/禁止错误（如 1254030/1254005）。
    #    字段名非法，绝不会真正写入任何业务记录。
    probe = req("POST",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create",
                token, {"records": [{"fields": {"__perm_probe_nonexistent_field__": "x"}}]})
    if isinstance(probe, dict):
        diag["steps"]["write_perm_probe"] = {"code": probe.get("code"), "msg": probe.get("msg")}

    finish(diag)


if __name__ == "__main__":
    main()
