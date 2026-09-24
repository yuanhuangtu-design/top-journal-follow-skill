# 顶刊文献日报系统 — 项目说明文档

> 本文档供其他 AI / 协作者快速理解系统全貌、接手维护。

## 1. 项目目标

为癫痫（epilepsy）/ 神经科学 / 脑电信号分析方向的研究者搭建**每日文献自动化追踪系统**：
- 每天定时检索 PubMed 最新文献
- 按四组期刊范围筛选（高影响力刊 × epilepsy、癫痫专科刊 × epilepsy、神经科学刊 × epilepsy、脑电方法学刊 × EEG 关键词）
- 去重、相关性打分、优先级分级
- 中英双语标题和摘要
- 自动写入飞书多维表格，辅助长期定量文献阅读

## 2. 系统架构

```
GitHub Actions (cron 每日 UTC 21:00 = 北京 05:00)
    │
    ├─ 运行 scripts/run_daily.py
    │   ├─ 调用 scripts/literature_search.py 检索 PubMed E-utilities
    │   ├─ 四组独立检索（A / B1 / B2 / C）
    │   ├─ 检索口径：最近3天 EDAT（避免 PubMed 某天断更漏文献）
    │   ├─ 按 PMID 全局去重
    │   ├─ 相关性打分 + 优先级分级（必读/推荐/浏览）
    │   ├─ 中文翻译（直接调用 MyMemory API，重试3次+二次补翻）
    │   ├─ 生成 JSON + HTML 日报
    │   └─ 写入飞书多维表格（写入前按 PMID 查重，避免重复）
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
- **定时**：`cron: "0 21 * * *"`（UTC 21:00 = 北京 05:00）
  - ⚠️ GitHub 免费账户 cron 实际延迟约 3 小时，通常在**北京时间上午 08:00 左右**执行完毕

## 4. 四组期刊与检索配置

配置文件：`config/daily_config.json`

### 基础检索词
`epilepsy`（A/B1/B2 组共用）

### A 组：高影响力期刊 × Epilepsy（20 本）
```
Nature, Science, Cell, Nat Med, Nat Neurosci, Neuron, Brain, Lancet Neurol,
JAMA Neurol, Ann Neurol, Neurology, J Neurol Neurosurg Psychiatry, Stroke,
Mol Neurodegener, Acta Neuropathol, Nat Commun, Sci Adv, Cell Rep Med,
Transl Neurodegener, eLife
```

### B1 组：癫痫专科刊 × Epilepsy（6 本）
```
Epilepsia, Epilepsy Behav, Seizure, Epilepsy Res, Epileptic Disord, Epilepsia Open
```

### B2 组：神经科学刊 × Epilepsy（5 本）
```
Brain Stimul, J Neurosci, Mol Psychiatry, Brain Behav Immun, J Neuroinflamm
```

> B 组原 11 本因检索式太长偶发 PubMed API 超时，已拆分为 B1/B2 降低单次检索复杂度。

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
AND ("<EDAT_start>"[EDAT] : "<EDAT_end>"[EDAT])
AND (10本期刊名[Journal] OR ...)
```

### 检索口径：最近 3 天 EDAT

- **日报日期** = 北京今天
- **EDAT 范围** = 北京昨天往前推 3 天（即前天、昨天、今天凌晨）
- 例：9/25 跑的时候，检索 EDAT 为 9/22-9/24
- **为什么用 3 天**：PubMed 偶发整天不更新 EDAT（如 9/24、9/25 连续两天 0 篇），用单天会永久漏文献；用 3 天范围可自动补回
- **去重保障**：写入飞书前查询表中已有 PMID，已存在的自动跳过

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
| PMID | 文本 | PubMed ID（去重依据） |
| 期刊 | 文本 | 期刊全称 |
| 作者 | 文本 | 前8位作者 |
| DOI | 文本 | DOI |
| PubMed链接 | 超链接 | PubMed 页面 |
| 优先级 | 单选 | 必读/推荐/浏览 |
| 命中检索 | 文本 | 命中的组（A/B1/B2/C） |
| 英文摘要 | 多行文本 | 英文摘要 |
| 中文摘要 | 多行文本 | 翻译后的中文摘要 |
| 日报日期 | 日期 | 报告日期 |
| 相关度得分 | 数字 | 打分 |
| 已读 | 复选框 | 阅读状态 |

### GitHub Secrets（已配置 4 个）
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_APP_TOKEN`（即 app_token）
- `FEISHU_TABLE_ID`

Notion 相关 3 个 Secrets 未配置，代码自动跳过。

## 6. 翻译机制

### 翻译源
- **MyMemory API**（直接 HTTP 调用，不经过 deep-translator 库）
- Google 翻译直连被墙，已跳过

### 质量保障（三层）
1. **单篇重试 3 次**：每次失败后等待 3 秒重试
2. **质量校验**：翻译结果中文字符占比 >15% 才算成功，否则重试
3. **二次补翻**：第一轮全部翻译完后，对质量不达标的文献再翻一轮

### 已知限制
- MyMemory 免费 API 匿名用户每日有字符配额（约 2000-5000 字符/天）
- 批量翻译超过配额时，后续文献会返回原文，日志会标记 `[WARN]`
- 超时 60 秒自动降级为原文，避免拖垮整个每日任务

## 7. 已知问题与解决方案

### 问题 1：PubMed 偶发整天不更新 EDAT
- **现象**：某些日期（如 9/24、9/25）EDAT 全天 0 篇（所有文献都 0 篇，不只是 epilepsy）
- **根因**：PubMed 数据更新延迟或系统维护
- **已修复**：检索口径从单天改为最近 3 天，断更后自动补回

### 问题 2：GitHub Actions cron 延迟
- **现象**：设定北京 05:00 运行，实际在 08:00 左右才执行完毕
- **根因**：GitHub 免费账户的 cron 任务在高峰期排队延迟约 3 小时
- **影响**：无实质影响，只是晚 3 小时出结果
- **无法通过代码修复**

### 问题 3：MyMemory 翻译限流
- **现象**：批量翻译超过每日配额时，后几篇返回原文
- **已做**：重试 3 次 + 二次补翻 + 质量校验 + 超时降级
- **建议**：如需 100% 翻译成功率，可配置 DeepL API key 或其他付费翻译服务

### 问题 4：多数日期 0 篇文献
- **现象**：A 组经常 0 篇，整体某些天无新增
- **根因**：目标为 41 本顶刊/专科刊/方法学刊，每天新增的 epilepsy/EEG 相关文献本身稀疏
- **正常范围**：每周约 10-30 篇，不是系统问题

## 8. 手动补跑方法

当某天自动化 failed 或漏抓时，手动补跑步骤：

```bash
# 克隆仓库
git clone https://github.com/yuanhuangtu-design/top-journal-follow-skill.git
cd top-journal-follow-skill

# 安装依赖
pip install requests

# 设置飞书环境变量（从 GitHub Secrets 获取，不在此明文）
export FEISHU_APP_ID="cli_aa23f35593381be5"
export FEISHU_APP_SECRET="<你的飞书App Secret>"
export FEISHU_APP_TOKEN="DWh0bt8gyaNAcos9HNEcBQV9nqc"
export FEISHU_TABLE_ID="tblRegWvkknu1MnT"

# 补跑指定日期（日报日期，EDAT 自动为前3天范围）
python3 scripts/run_daily.py --date 2026-09-20

# 跳过翻译（批量补跑时推荐，避免限流）
python3 scripts/run_daily.py --date 2026-09-20 --no-translate
```

✅ 飞书表格**已有自动去重**：写入前查询表中已有 PMID，已存在的自动跳过，重复运行不会产生重复记录。

## 9. 代码文件说明

| 文件 | 作用 |
|---|---|
| `scripts/run_daily.py` | 主流程：检索→去重→打分→翻译→输出→飞书写入（含查重） |
| `scripts/literature_search.py` | PubMed 检索子脚本：解析检索式→esearch→esummary→efetch 摘要 |
| `config/daily_config.json` | 四组期刊、检索词、打分词、标签配置 |
| `.github/workflows/daily_literature_report.yml` | GitHub Actions 定时任务定义 |
| `output/` | 每日 JSON + HTML 日报产出（git 跟踪） |
| `PROJECT_README.md` | 本文档 |

## 10. 运行历史摘要

- 系统上线时间：2026-09-15 左右
- Actions 运行：#1-#13+，#6 起均为 Scheduled 定时触发
- 2026-09-23 补跑：一次性补入 9/16-9/22 漏掉的 29 篇文献
- 2026-09-24 稳定性改进：B 组拆分 B1/B2、翻译改用直接 API、cron 改到北京 05:00
- 2026-09-25 重大改进：检索口径改最近 3 天 EDAT、飞书写入前 PMID 查重、翻译修复长度检查 bug + 二次补翻
- 截至 2026-09-25，飞书表格共 43+ 条记录

## 11. 待办事项

- [ ] 加飞书消息提醒（每天跑完推送结果卡片，0 篇也通知）
- [ ] 飞书表格增加阅读量化字段（阅读状态、精读日期、可引用点、笔记链接）
- [ ] 考虑配置 DeepL API 提升翻译稳定性
- [ ] 加失败自动补跑机制（每天检查前 3 天 failed 的组并补跑）
