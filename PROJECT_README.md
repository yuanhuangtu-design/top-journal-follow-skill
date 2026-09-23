# 顶刊文献日报系统 — 项目说明文档

> 本文档供其他 AI / 协作者快速理解系统全貌、接手维护。

## 1. 项目目标

为癫痫（epilepsy）/ 神经科学 / 脑电信号分析方向的研究者搭建**每日文献自动化追踪系统**：
- 每天定时检索 PubMed 最新文献
- 按三组期刊范围筛选（高影响力刊 × epilepsy、癫痫专科刊 × epilepsy、脑电方法学刊 × EEG 关键词）
- 去重、相关性打分、优先级分级
- 中英双语标题和摘要
- 自动写入飞书多维表格，辅助长期定量文献阅读

## 2. 系统架构

```
GitHub Actions (cron 每日 UTC 00:00 = 北京 08:00)
    │
    ├─ 运行 scripts/run_daily.py
    │   ├─ 调用 scripts/literature_search.py 检索 PubMed E-utilities
    │   ├─ 三组独立检索（A / B / C）
    │   ├─ 按 PMID 全局去重
    │   ├─ 相关性打分 + 优先级分级（必读/推荐/浏览）
    │   ├─ 中文翻译（MyMemory API，失败降级为原文）
    │   ├─ 生成 JSON + HTML 日报
    │   └─ 写入飞书多维表格（batch_create API）
    │
    └─ 产出物
        ├─ output/daily_report_YYYY-MM-DD.json
        ├─ output/daily_report_YYYY-MM-DD.html
        └─ 飞书多维表格记录
```

## 3. 仓库信息

- **GitHub 账号**：`yuanhuangtu-design`
- **仓库**：`yuanhuangtu-design/top-journal-follow-skill`
- **默认分支**：`main`
- **Workflow 文件**：`.github/workflows/daily_literature_report.yml`
- **定时**：`cron: "0 0 * * *"`（UTC 00:00 = 北京 08:00）
  - ⚠️ GitHub 免费账户 cron 实际延迟约 3 小时，通常在**北京时间上午 11:00 左右**执行

## 4. 三组期刊与检索配置

配置文件：`config/daily_config.json`

### 基础检索词
`epilepsy`（A/B 组共用）

### A 组：高影响力期刊 × Epilepsy（20 本）
```
Nature, Science, Cell, Nat Med, Nat Neurosci, Neuron, Brain, Lancet Neurol,
JAMA Neurol, Ann Neurol, Neurology, J Neurol Neurosurg Psychiatry, Stroke,
Mol Neurodegener, Acta Neuropathol, Nat Commun, Sci Adv, Cell Rep Med,
Transl Neurodegener, eLife
```

检索式：
```
epilepsy[Title/Abstract] AND ("<EDAT>"[EDAT] : "<EDAT>"[EDAT])
AND (20本期刊名[Journal] OR ...)
```

### B 组：癫痫专科与神经科学 × Epilepsy（11 本）
```
Epilepsia, Epilepsy Behav, Seizure, Epilepsy Res, Epileptic Disord,
Epilepsia Open, Brain Stimul, J Neurosci, Mol Psychiatry,
Brain Behav Immun, J Neuroinflamm
```

检索式：同 A 组，期刊过滤为 11 本。

### C 组：脑电信号分析方法（10 本，独立检索词，不限制 epilepsy）
```
Clin Neurophysiol, J Neurophysiol, J Neural Eng, NeuroImage, Brain Topogr,
J Clin Neurophysiol, Neuromodulation, J Neurosci Methods,
J Neuroeng Rehabil, IEEE Trans Neural Syst Rehabil Eng
```

独立检索式：
```
(electroencephalograph*[Title/Abstract] OR intracranial EEG[Title/Abstract]
 OR SEEG[Title/Abstract] OR "local field potential"[Title/Abstract] OR LFP[Title/Abstract]
 OR electrocorticograph*[Title/Abstract] OR ECoG[Title/Abstract]
 OR "high-frequency oscillation"[Title/Abstract] OR HFO[Title/Abstract]
 OR "multi-unit activity"[Title/Abstract] OR MUA[Title/Abstract]
 OR "neural signal"[Title/Abstract] OR "signal processing"[Title/Abstract]
 OR ("deep learning"[Title/Abstract] AND EEG[Title/Abstract]))
AND ("<EDAT>"[EDAT] : "<EDAT>"[EDAT])
AND (10本期刊名[Journal] OR ...)
```

### 相关性打分词（39 个）
```
epilepsy, epileptic, epileptiform, seizure, seizures, convulsion, convulsive,
status epilepticus, drug-resistant, refractory, intractable, antiepileptic,
anticonvulsant, ictal, interictal, electroencephalogr, eeg, sudep,
temporal lobe, focal, generalized, tonic-clonic, absence, mesial,
hippocampal, kindling, antiseizure, seeg, ecog, lfp, intracranial,
electrocorticography, high-frequency oscillation, hfo, multi-unit activity,
mua, deep learning, machine learning, neural network, signal processing
```

### 分级阈值
- 得分 ≥ 8 → **必读**
- 得分 ≥ 3 → **推荐**
- 其余 → **浏览**

## 5. 飞书多维表格配置

- **应用名称**：文献日报助手
- **App ID**：`cli_aa23f35593381be5`
- **App Secret**：见 GitHub Secrets `FEISHU_APP_SECRET`（不在此文档明文存储）
- **多维表格 app_token**：`DWh0bt8gyaNAcos9HNEcBQV9nqc`
- **table_id**：`tblRegWvkknu1MnT`
- **表格 URL**：https://my.feishu.cn/base/DWh0bt8gyaNAcos9HNEcBQV9nqc

### 表格字段（14 个）
| 字段名 | 类型 | 说明 |
|---|---|---|
| 标题 | 文本 | 英文标题 |
| 中文标题 | 文本 | 翻译后的中文标题 |
| PMID | 文本 | PubMed ID |
| 期刊 | 文本 | 期刊全称 |
| 作者 | 文本 | 前8位作者 |
| DOI | 文本 | DOI |
| PubMed链接 | 超链接 | PubMed 页面 |
| 优先级 | 单选 | 必读/推荐/浏览 |
| 命中检索 | 文本 | 命中的组（A/B/C） |
| 英文摘要 | 多行文本 | 英文摘要 |
| 中文摘要 | 多行文本 | 翻译后的中文摘要 |
| 日报日期 | 日期 | 报告日期（=检索日+1） |
| 相关度得分 | 数字 | 打分 |
| 已读 | 复选框 | 阅读状态 |

### GitHub Secrets（已配置 4 个）
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_APP_TOKEN`（即 app_token）
- `FEISHU_TABLE_ID`

Notion 相关 3 个 Secrets 未配置，代码自动跳过。

## 6. 检索口径

- **日报日期** = 北京今天
- **目标 PubMed EDAT** = 北京昨天
- 即：每天检索前一天新入库的文献
- EDAT = Entry Date，文献进入 PubMed 的日期

## 7. 已知问题与解决方案

### 问题 1：B 组偶发检索失败（failed）
- **现象**：B 组（11 本期刊）检索偶发返回失败，导致当天癫痫专科刊文献漏抓
- **根因**：11 本期刊名 OR 拼接的检索式较长，PubMed E-utilities API 偶发超时/不稳定
- **已做修复**：重试次数从 2 次增到 3 次，间隔从 5 秒增到 10 秒
- **建议进一步优化**：将 B 组拆分为 B1（6 本癫痫专科刊）和 B2（5 本神经科学刊），降低单次检索复杂度

### 问题 2：GitHub Actions cron 延迟
- **现象**：设定北京 08:00 运行，实际在 10:50-11:05 才执行
- **根因**：GitHub 免费账户的 cron 任务在高峰期排队延迟
- **影响**：无实质影响，只是晚 3 小时出结果
- **无法通过代码修复**

### 问题 3：中文翻译限流
- **现象**：MyMemory 免费 API 有调用次数限制，批量翻译时后几篇返回原文
- **已做**：单篇翻译超时 20 秒自动降级为原文；Google 翻译直连被墙已跳过
- **建议**：如需稳定翻译，可配置 DeepL API key 或使用其他翻译服务

### 问题 4：多数日期 0 篇文献
- **现象**：A 组经常 0 篇，整体多天无新增
- **根因**：目标为 41 本顶刊/专科刊/方法学刊，每天新增的 epilepsy/EEG 相关文献本身稀疏
- **正常范围**：每周约 10-30 篇，不是系统问题

## 8. 手动补跑方法

当某天自动化 failed 或漏抓时，手动补跑步骤：

```bash
# 克隆仓库
git clone https://github.com/yuanhuangtu-design/top-journal-follow-skill.git
cd top-journal-follow-skill

# 安装依赖
pip install deep-translator

# 设置飞书环境变量（从 GitHub Secrets 获取，不在此明文）
export FEISHU_APP_ID="cli_aa23f35593381be5"
export FEISHU_APP_SECRET="<你的飞书App Secret>"
export FEISHU_APP_TOKEN="DWh0bt8gyaNAcos9HNEcBQV9nqc"
export FEISHU_TABLE_ID="tblRegWvkknu1MnT"

# 补跑指定日期（日报日期，EDAT 自动为前一天）
python3 scripts/run_daily.py --date 2026-09-20

# 跳过翻译（批量补跑时推荐，避免限流）
python3 scripts/run_daily.py --date 2026-09-20 --no-translate
```

⚠️ 飞书表格无自动去重，重复运行会写入重复记录。补跑前应先查询已有 PMID 并过滤。

## 9. 代码文件说明

| 文件 | 作用 |
|---|---|
| `scripts/run_daily.py` | 主流程：检索→去重→打分→翻译→输出→飞书写入 |
| `scripts/literature_search.py` | PubMed 检索子脚本：解析检索式→esearch→esummary→efetch 摘要 |
| `config/daily_config.json` | 三组期刊、检索词、打分词、标签配置 |
| `.github/workflows/daily_literature_report.yml` | GitHub Actions 定时任务定义 |
| `output/` | 每日 JSON + HTML 日报产出（git 跟踪） |

## 10. 运行历史摘要

- 系统上线时间：2026-09-15 左右
- Actions 运行：#1-#10+，#6 起均为 Scheduled 定时触发
- 2026-09-23 补跑：一次性补入 9/16-9/22 漏掉的 29 篇文献
- 截至 2026-09-23，飞书表格共 43 条记录

## 11. 待办事项

- [ ] 将 B 组拆分为 B1/B2 两个子检索，降低 failed 率
- [ ] 加失败自动补跑机制（每天检查前 3 天 failed 的组并补跑）
- [ ] 飞书表格增加阅读量化字段（阅读状态、精读日期、可引用点、笔记链接）
- [ ] 为补跑的 29 篇文献分批补中文摘要
- [ ] 考虑加飞书消息提醒（每天有新文献时推送通知）
