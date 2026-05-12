# 顶刊文献追踪技能 · Top Journal Literature Tracker

> 面向科研人员的 PubMed 文献检索、分析与可视化工具。支持自然语言检索、自动关键词提取、多维统计分析和交互式 HTML 看板。

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-2.3%2B-lightgrey)](https://flask.palletsprojects.com)
[![Chart.js](https://img.shields.io/badge/Chart.js-4.4-ff6384)](https://www.chartjs.org)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![PubMed](https://img.shields.io/badge/PubMed-E--utilities-orange)](https://www.ncbi.nlm.nih.gov/home/develop/api/)

---

## 目录

- [概述](#概述)
- [核心功能](#核心功能)
- [架构设计](#架构设计)
- [快速开始](#快速开始)
- [使用指南](#使用指南)
  - [Web UI 模式（推荐）](#web-ui-模式推荐)
  - [命令行模式](#命令行模式)
- [项目结构](#项目结构)
- [技术栈](#技术栈)
- [FAQ](#faq)
- [Roadmap](#roadmap)
- [许可证](#许可证)

---

## 概述

做科研、追文献、看趋势，这套工具帮你一条龙搞定。输入研究主题或一段自然语言，工具自动：

1. 解析检索意图，生成 PubMed 检索式
2. 调用 PubMed E-utilities API 抓取文献元数据和摘要
3. 提取关键词，做 TF-IDF 排序，过滤无意义的通用学术词
4. 统计期刊分布、作者活跃度、年度发文趋势
5. 输出一份**独立的交互式 HTML 看板**，所有图表可交互

每次搜索独立保存为一个"项目"，方便回顾和对比不同研究方向的分析结果。

---

## 核心功能

### 检索
- **自然语言解析** — 支持 "find papers by Gelinas JN about epilepsy" 这类日常表达
- **PubMed 原生语法** — 也支持 `Gelinas JN[Author] AND epilepsy[Title]` 精确检索
- **期刊限定** — 按 Nature、Science、Cell 等指定期刊筛选
- **年份范围** — 2021-2025 任意区间

### 分析
- **TF-IDF 关键词排名** — 标题词 3 倍权重，自动过滤 400+ 医学/学术停用词
- **期刊分布统计** — 哪些期刊发文最多，一目了然
- **核心作者识别** — 谁是该领域的活跃研究者
- **年度趋势分析** — 发文量随时间的变化曲线

### 可视化
- **交互式 HTML 看板** — 独立文件，双击就能打开，无需服务器
- **5 种图表** — 关键词柱状图、期刊饼图、作者排名、年度趋势、关键词标签云
- **可搜索文献表** — 输入关键词实时过滤，快速定位目标文献
- **项目化管理** — 每次检索独立存储，历史记录可回溯

---

## 架构设计

```
用户输入 (自然语言 / PubMed语法)
        │
        ▼
┌───────────────────┐
│  Flask Web UI     │  ← 图形界面 / CLI 入口
│  (app.py)         │
└────────┬──────────┘
         │ 后台流水线
         ▼
┌───────────────────┐     ┌──────────────────┐
│ literature_search │────▶│  PubMed API      │
│ .py               │     │  (E-utilities)   │
└────────┬──────────┘     └──────────────────┘
         │ search_results.json
         ▼
┌───────────────────┐
│ analyze.py        │  TF-IDF + 400+ 停用词过滤
└────────┬──────────┘
         │ analysis_results.json
         ▼
┌───────────────────┐
│ generate_dashboard│  Chart.js 交互看板
│ .py               │  独立 HTML 文件
└────────┬──────────┘
         │ literature_dashboard.html
         ▼
    ┌──────────┐
    │  HTML     │  双击打开 or Flask 内嵌展示
    │  看板     │
    └──────────┘
```

### 数据流

```
PubMed E-utilities API
       │ (esearch → esummary → efetch)
       ▼
search_results_{timestamp}.json   ← 原始检索结果
       │
       ▼
analysis_results_{timestamp}.json  ← 关键词/期刊/作者分析
       │
       ▼
literature_dashboard_{timestamp}.html  ← 交互式看板
       │
       ▼ (同步最新版本)
literature_dashboard.html  ← Web UI 指向的当前版本
```

---

## 快速开始

### 克隆

```bash
git clone https://github.com/your-username/top-journal-follow-skill.git
cd top-journal-follow-skill
```

### 前置条件

- Python 3.8+
- pip（Python 包管理器）
- 网络连接（用于 PubMed API 调用）

### 安装

```bash
# 1. 克隆或进入项目目录
cd top-journal-follow-skill

# 2. 安装依赖（只需要 Flask）
pip install flask
```

> 核心检索和分析功能使用 Python 标准库（`urllib`、`json`、`re`、`collections`），**无需额外安装科学计算包**。仅 Web UI 需要 Flask。

---

## 使用指南

### Web UI 模式（推荐）

```bash
python app.py
```

浏览器自动打开 `http://localhost:5000`，界面分为左右两栏：

**左侧 — 搜索面板**
- 在文本框中输入检索词（支持自然语言和 PubMed 语法）
- 可选：限定期刊（逗号分隔）和年份范围
- 点击"开始检索"，后台自动执行流水线
- 实时显示进度：检索中 → 分析中 → 生成看板

**右侧 — 看板展示**
- 检索完成后自动打开交互式看板
- 如果之前有检索项目，点击左侧历史记录可直接查看

#### 检索示例

| 输入类型 | 示例 | 说明 |
|---------|------|------|
| 自然语言 | `find papers by Gelinas JN about epilepsy` | 自动识别作者+主题 |
| 自然语言 | `CRISPR gene editing` | 主题搜索 |
| PubMed 语法 | `Gelinas JN[Author] AND epilepsy[Title]` | 精确检索 |
| 混合 | `deep learning` + 期刊限定 "Nature,Science" | 指定期刊 |

### 命令行模式

如果你更习惯终端操作，也可以直接运行各个脚本：

```bash
# Step 1: 检索文献
python scripts/literature_search.py \
  --query "CRISPR gene editing" \
  --journals "Nature,Science,Cell" \
  --years 2024-2025 \
  --max 20 \
  --output output/

# Step 2: 分析关键词
python scripts/analyze.py \
  --input output/search_results_latest.json \
  --output output/

# Step 3: 生成看板
python scripts/generate_dashboard.py \
  --search output/search_results_latest.json \
  --analysis output/analysis_results_latest.json \
  --output output/ \
  --topic "CRISPR 基因编辑文献追踪"
```

完成后用浏览器打开 `output/literature_dashboard.html` 即可查看。

---

## 项目结构

```
top-journal-follow-skill/
│
├── app.py                          # Flask Web UI 主程序
├── requirements.txt                # Python 依赖
├── run_pipeline.sh                 # Bash 一键流水线（Linux/macOS）
├── SKILL.md                        # 技能提示词文档
│
├── scripts/
│   ├── literature_search.py        # PubMed 检索引擎
│   ├── analyze.py                  # TF-IDF 关键词 + 多维分析
│   └── generate_dashboard.py       # HTML 交互看板生成器
│
├── templates/
│   ├── index.html                  # Web UI 主页（搜索 + 项目列表）
│   └── project.html                # 项目详情页
│
├── projects/                       # 检索项目存储（自动生成）
│   └── {timestamp}_{query}/
│       ├── meta.json               # 项目元数据
│       ├── search_results_*.json   # 检索结果
│       ├── search_results_latest.json
│       ├── analysis_results_*.json # 分析结果
│       ├── analysis_results_latest.json
│       ├── literature_dashboard_*.html
│       └── literature_dashboard.html  # Web UI 引用的看板
│
└── output/                         # CLI 模式输出目录（可选）
```

### 核心脚本说明

| 脚本 | 功能 | 依赖 |
|------|------|------|
| `literature_search.py` | PubMed API 检索，支持自然语言解析 | 标准库 |
| `analyze.py` | TF-IDF 关键词提取 + 期刊/作者/年度分析 | 标准库 |
| `generate_dashboard.py` | 生成独立交互式 HTML 看板（Chart.js） | 标准库 |
| `app.py` | Flask Web UI，项目化管理 | Flask |

---

## 技术栈

| 层次 | 技术 | 用途 |
|------|------|------|
| 检索 | PubMed E-utilities API | 文献元数据 + 摘要 |
| 解析 | 正则表达式（标准库 `re`） | 自然语言检索式解析 |
| 分析 | TF-IDF（自实现，标准库 `math`） | 关键词重要性排序 |
| 停用词 | 400+ 医学/学术/通用英语词表 | 过滤无意义高频词 |
| 后端 | Flask 2.3+ | Web 服务器 + 路由 |
| 前端 | Chart.js 4.4 (CDN) | 交互式图表 |
| 前端 | Vanilla JavaScript | 看板交互 + API 轮询 |
| 存储 | JSON 文件系统 | 项目化数据持久化 |

### 为什么不依赖 NumPy / scikit-learn？

所有分析和计算（TF-IDF、词频统计、排序）都使用 Python 标准库实现。这样做的目的是：
- **零科学计算依赖** — `pip install flask` 就能跑
- **跨平台兼容** — 不需要处理 NumPy 的 C 扩展编译问题
- **轻量快速** — 对 10-100 篇文献的分析，标准库性能完全够用

---

## FAQ

### Q: 需要 PubMed API Key 吗？
不需要。PubMed E-utilities 无需 API Key 即可使用，免费、无需注册。如果注册了 API Key，可在 `literature_search.py` 中添加 `&api_key=your_key` 参数提高请求频率限制。

### Q: 最大能检索多少篇？
默认 20 篇，可在界面的"篇数"输入框中调整（5-100）。PubMed API 单次最多返回 100 篇。

### Q: 看板是离线的吗？
看板文件（`literature_dashboard.html`）是独立的 HTML 文件，但需要联网加载 Chart.js 库（CDN）。如果需要在无网络环境使用，可以将 Chart.js 下载到本地并修改 `<script>` 标签的 `src` 路径。

### Q: 检索结果保存在哪里？
所有数据按项目存储在 `projects/` 目录下，每个项目一个文件夹，命名格式为 `{时间戳}_{检索词}`。每个项目包含完整的原始数据、分析结果和看板文件。

### Q: 如何删除旧项目？
直接删除 `projects/` 下对应的文件夹即可。

---

## Roadmap

- [x] PubMed 自然语言检索
- [x] TF-IDF 关键词分析
- [x] 交互式 HTML 看板
- [x] Flask Web UI
- [x] 项目化存储管理
- [ ] Scopus / Web of Science 支持
- [ ] 文献导出（RIS / BibTeX）
- [ ] 多语言摘要（Google Translate API）
- [ ] Docker 一键部署
- [ ] 批量历史趋势对比

---

## 许可证

本项目基于 MIT 许可证开源。详见 [LICENSE](LICENSE) 文件。

---

## 致谢

- [PubMed E-utilities API](https://www.ncbi.nlm.nih.gov/home/develop/api/) — 提供文献数据
- [Chart.js