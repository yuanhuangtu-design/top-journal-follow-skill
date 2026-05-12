#!/usr/bin/env python3
"""
顶刊文献追踪 — Web UI (Flask)
==============================
提供图形化界面，支持自然语言和PubMed检索式，
每次搜索独立保存为项目，方便回顾和对比。

启动:
    python app.py
    然后打开浏览器访问 http://localhost:5000
"""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.parse
import webbrowser
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_from_directory

app = Flask(__name__)

# ============ 配置 ============
BASE_DIR = Path(__file__).parent.resolve()
PROJECTS_DIR = BASE_DIR / "projects"
SCRIPTS_DIR = BASE_DIR / "scripts"

PROJECTS_DIR.mkdir(exist_ok=True)


# ============ 项目管理 ============

def sanitize_filename(text):
    """将文本转为安全的文件夹名"""
    text = text.strip()[:40]
    text = re.sub(r'[\\/:*?"<>|]', '_', text)
    text = re.sub(r'\s+', '_', text)
    return text


def create_project(query_raw, journals, years, max_results):
    """创建新的项目文件夹并保存元数据"""
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    date_str = now.strftime("%Y-%m-%d %H:%M:%S")

    # 从查询中提取简短描述作为项目名
    short_desc = sanitize_filename(query_raw[:30])
    if not short_desc:
        short_desc = "untitled"
    project_name = f"{timestamp}_{short_desc}"
    project_dir = PROJECTS_DIR / project_name
    project_dir.mkdir(parents=True, exist_ok=True)

    # 保存元数据
    meta = {
        "project_name": project_name,
        "created_at": date_str,
        "timestamp": timestamp,
        "query_raw": query_raw,
        "query_type": "auto",
        "journals": journals,
        "years": years,
        "max_results": max_results,
        "status": "pending",
    }
    with open(project_dir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    return project_name, project_dir


def get_project_info(project_name):
    """获取项目信息"""
    project_dir = PROJECTS_DIR / project_name
    if not project_dir.exists():
        return None

    meta_path = project_dir / "meta.json"
    meta = {}
    if meta_path.exists():
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

    # 兼容新旧两种 dashboard 文件名
    dashboard_candidates = [
        project_dir / "literature_dashboard.html",
        project_dir / "literature_dashboard_latest.html",
    ]
    dashboard_path = None
    for p in dashboard_candidates:
        if p.exists():
            dashboard_path = p
            break
    has_dashboard = dashboard_path is not None
    dashboard_filename = dashboard_path.name if dashboard_path else "literature_dashboard.html"

    search_path = project_dir / "search_results_latest.json"
    has_results = search_path.exists()

    return {
        "name": project_name,
        "meta": meta,
        "has_dashboard": has_dashboard,
        "has_results": has_results,
        "dashboard_filename": dashboard_filename,
        "dashboard_url": f"/project/{project_name}/{dashboard_filename}" if has_dashboard else None,
    }


def list_projects():
    """列出所有项目（按时间倒序）"""
    projects = []
    if not PROJECTS_DIR.exists():
        return projects

    for d in sorted(PROJECTS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if d.is_dir():
            info = get_project_info(d.name)
            if info:
                projects.append(info)
    return projects


# ============ 流水线执行 ============

def run_pipeline(project_name, query_raw, journals, years, max_results):
    """在后台执行完整流水线"""
    project_dir = PROJECTS_DIR / project_name
    output_dir = str(project_dir)

    def _run():
        try:
            # 更新状态
            _update_meta(project_dir, "status", "searching")

            # Step 1: 文献检索
            search_cmd = [
                sys.executable,
                str(SCRIPTS_DIR / "literature_search.py"),
                "--query", query_raw,
                "--max", str(max_results),
                "--output", output_dir,
            ]
            if journals:
                search_cmd.extend(["--journals", journals])
            if years:
                search_cmd.extend(["--years", years])

            result = subprocess.run(search_cmd, capture_output=True, text=True, timeout=120)
            _update_meta(project_dir, "search_log", result.stdout + result.stderr)

            if result.returncode != 0:
                _update_meta(project_dir, "status", "search_failed")
                _update_meta(project_dir, "error", result.stderr)
                return

            # Step 2: 分析
            _update_meta(project_dir, "status", "analyzing")
            search_latest = os.path.join(output_dir, "search_results_latest.json")
            if os.path.exists(search_latest):
                analyze_cmd = [
                    sys.executable,
                    str(SCRIPTS_DIR / "analyze.py"),
                    "--input", search_latest,
                    "--output", output_dir,
                ]
                result = subprocess.run(analyze_cmd, capture_output=True, text=True, timeout=60)
                _update_meta(project_dir, "analyze_log", result.stdout + result.stderr)

                if result.returncode != 0:
                    _update_meta(project_dir, "status", "analyze_failed")
                    _update_meta(project_dir, "error", result.stderr)
                    return

            # Step 3: 生成看板
            _update_meta(project_dir, "status", "generating_dashboard")
            analysis_latest = os.path.join(output_dir, "analysis_results_latest.json")
            if os.path.exists(analysis_latest):
                dash_cmd = [
                    sys.executable,
                    str(SCRIPTS_DIR / "generate_dashboard.py"),
                    "--search", search_latest,
                    "--analysis", analysis_latest,
                    "--output", output_dir,
                    "--topic", query_raw[:50],
                ]
                result = subprocess.run(dash_cmd, capture_output=True, text=True, timeout=60)
                _update_meta(project_dir, "dashboard_log", result.stdout + result.stderr)

                if result.returncode != 0:
                    _update_meta(project_dir, "status", "dashboard_failed")
                    _update_meta(project_dir, "error", result.stderr)
                    return
            else:
                _update_meta(project_dir, "status", "dashboard_failed")
                _update_meta(project_dir, "error", "分析结果文件不存在，无法生成看板")
                return

            _update_meta(project_dir, "status", "completed")

        except subprocess.TimeoutExpired as e:
            _update_meta(project_dir, "status", "failed")
            _update_meta(project_dir, "error", f"执行超时（{e.cmd[2] if len(e.cmd) > 2 else 'unknown'}）")
        except Exception as e:
            _update_meta(project_dir, "status", "failed")
            _update_meta(project_dir, "error", str(e))

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread


def _update_meta(project_dir, key, value):
    """更新项目的 meta.json 中的某个字段"""
    meta_path = project_dir / "meta.json"
    meta = {}
    if meta_path.exists():
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    meta[key] = value
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


# ============ 路由 ============

@app.route("/")
def index():
    projects = list_projects()
    return render_template("index.html", projects=projects)


@app.route("/api/projects")
def api_projects():
    return jsonify(list_projects())


@app.route("/api/project/<project_name>")
def api_project(project_name):
    info = get_project_info(project_name)
    if not info:
        return jsonify({"error": "not found"}), 404
    return jsonify(info)


@app.route("/api/project/<project_name>/meta")
def api_project_meta(project_name):
    """获取项目的 meta.json（用于轮询状态）"""
    project_dir = PROJECTS_DIR / project_name
    meta_path = project_dir / "meta.json"
    if not meta_path.exists():
        return jsonify({"error": "not found"}), 404
    with open(meta_path, "r", encoding="utf-8") as f:
        return jsonify(json.load(f))


@app.route("/api/run", methods=["POST"])
def api_run():
    """启动新的检索任务"""
    data = request.get_json()
    if not data or "query" not in data:
        return jsonify({"error": "请提供检索词"}), 400

    query_raw = data["query"].strip()
    journals = data.get("journals", "").strip()
    years = data.get("years", "").strip()
    max_results = int(data.get("max_results", 20))

    if not query_raw:
        return jsonify({"error": "检索词不能为空"}), 400

    project_name, project_dir = create_project(query_raw, journals, years, max_results)
    run_pipeline(project_name, query_raw, journals, years, max_results)

    return jsonify({
        "project_name": project_name,
        "status": "started",
        "project_url": f"/project/{project_name}",
    })


@app.route("/project/<project_name>/")
def project_page(project_name):
    """项目详情页"""
    info = get_project_info(project_name)
    if not info:
        return "项目不存在", 404

    # 读取 dashboard 内容（兼容新旧文件名）
    dashboard_html = ""
    dashboard_candidates = [
        PROJECTS_DIR / project_name / "literature_dashboard.html",
        PROJECTS_DIR / project_name / "literature_dashboard_latest.html",
    ]
    dashboard_path = None
    for p in dashboard_candidates:
        if p.exists():
            dashboard_path = p
            break
    if dashboard_path:
        with open(dashboard_path, "r", encoding="utf-8") as f:
            dashboard_html = f.read()

    return render_template("project.html",
                         project=info,
                         dashboard_html=dashboard_html)


@app.route("/project/<project_name>/<path:filename>")
def project_file(project_name, filename):
    """提供项目文件夹内的文件"""
    project_dir = PROJECTS_DIR / project_name
    return send_from_directory(str(project_dir), filename)


# ============ 启动 ============

if __name__ == "__main__":
    print("=" * 50)
    print(f"  Web UI: http://localhost:5000")
    print(f"  项目目录: {PROJECTS_DIR}")
    print(f"  按 Ctrl+C 停止")
    print("=" * 50)

    # 自动打开浏览器
    threading.Timer(1.5, lambda: webbrowser.open("http://localhost:5000")).start()

    app.run(debug=True, host="127.0.0.1", port=5000)
