#!/bin/bash
# ===============================================
# 顶刊文献追踪技能 — 完整流水线
# 一键执行：检索 → 分析 → 可视化 → HTML看板
# ===============================================
# 用法:
#   bash run_pipeline.sh --query "CRISPR gene editing" --journals "Nature,Science,Cell" --years 2024-2025
# ===============================================

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTPUT_DIR="$SCRIPT_DIR/output"

QUERY=""; JOURNALS="Nature,Science,Cell"; YEARS="2024-2025"; MAX=20; TOPIC=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --query|-q) QUERY="$2"; shift 2;;
        --journals|-j) JOURNALS="$2"; shift 2;;
        --years|-y) YEARS="$2"; shift 2;;
        --max|-m) MAX="$2"; shift 2;;
        --topic|-t) TOPIC="$2"; shift 2;;
        --help|-h) echo "用法: bash run_pipeline.sh --query '关键词' [--journals ...] [--years ...] [--max N]"; exit 0;;
        *) echo "未知参数: $1"; exit 1;;
    esac
done

[ -z "$QUERY" ] && { echo "错误: --query 是必填参数"; exit 1; }
[ -z "$TOPIC" ] && TOPIC="顶刊文献追踪: ${QUERY}"

mkdir -p "$OUTPUT_DIR" "$OUTPUT_DIR/charts"

echo "=========================================="
echo "  顶刊文献追踪流水线"
echo "  检索词:    $QUERY"
echo "  目标期刊:  $JOURNALS"
echo "  时间范围:  $YEARS"
echo "=========================================="

echo "[1/4] 🔍 正在检索文献..."
python3 "$SCRIPT_DIR/scripts/literature_search.py" --query "$QUERY" --journals "$JOURNALS" --years "$YEARS" --max "$MAX" --output "$OUTPUT_DIR"

echo "[2/4] 📊 正在分析文献..."
python3 "$SCRIPT_DIR/scripts/analyze.py" --input "$OUTPUT_DIR/search_results.json" --output "$OUTPUT_DIR"

echo "[3/4] 📈 正在生成可视化图表..."
python3 "$SCRIPT_DIR/scripts/visualize.py" --input "$OUTPUT_DIR/analysis_results.json" --output "$OUTPUT_DIR" --charts-dir "charts" 2>/dev/null || echo "  (图表生成跳过: 请安装 matplotlib)"

echo "[4/4] 🎨 正在生成 HTML 看板..."
python3 "$SCRIPT_DIR/scripts/generate_dashboard.py" --search "$OUTPUT_DIR/search_results.json" --analysis "$OUTPUT_DIR/analysis_results.json" --output "$OUTPUT_DIR" --topic "$TOPIC"

echo ""
echo "=========================================="
echo "  ✅ 完成！"
echo "  HTML 看板: $OUTPUT_DIR/literature_dashboard.html"
echo "=========================================="
