# 检索配置审查与综述接入（2026-11-25）

## 本次配置变更

- 新增每日 R 组：Nature Reviews Neuroscience × Review / Systematic Review，不要求癫痫关键词。
- 原有 A/B1/B2/C/D/E 组没有排除综述，继续保留。R 组不包含该刊仅被标为 Journal Article 的研究简讯。
- 修复 B2 的 J Neuroinflamm → J Neuroinflammation；原缩写在 PubMed 返回期刊短语无法识别。
- 保留 Neuron、Brain、Nature Neuroscience 的全主题周候选；本次没有扩大这些期刊的自动入表范围。
- 新增期刊的中科院 2025 大类分区、JCR 2026 发布版（2025 数据）随私有目录更新，不把完整分区源表上传到仓库。
- 每日保留 3 天重叠、PMID 查重、失败队列和原有阅读状态；配置升级不会自动补抓全部历史。

## 近 8 周 PubMed 入库量

统计窗口：2026-09-30—2026-11-24（56 天）。按 [EDAT] 入库日期，不限主题、包含所有发表类型。
每刊用 `"期刊缩写"[Journal] AND ("2026-09-30"[EDAT] : "2026-11-24"[EDAT])` 获取全部 PMID，核对总数后逐条读取发表类型。
8 个周区间为周三至周二；不是按印刷期号或文章发表年份计数。综述仅统计 PubMed 当前标为 Review / Systematic Review 的条目。

| 期刊 | 8 周全部条目 | 周均 | 单周范围 | 标为综述 | 有新条目的天数 / 56 | 单日最多入库 |
|---|---:|---:|---:|---:|---:|---:|
| Neuron | 59 | 7.4 | 3–15 | 12 | 29 | 11 |
| Brain | 79 | 9.9 | 4–17 | 0 | 30 | 10 |
| Nat Neurosci | 70 | 8.8 | 4–18 | 1 | 31 | 12 |
| Nat Rev Neurosci | 21 | 2.6 | 0–6 | 11 | 14 | 3 |
| Lancet Neurol | 42 | 5.2 | 0–24 | 6 | 6 | 22 |
| JAMA Neurol | 42 | 5.2 | 2–10 | 0 | 10 | 9 |
| Ann Neurol | 27 | 3.4 | 0–6 | 0 | 17 | 4 |
| Nature | 739 | 92.4 | 82–104 | 6 | 44 | 61 |
| Science | 327 | 40.9 | 36–47 | 7 | 9 | 46 |
| Cell | 112 | 14.0 | 4–27 | 3 | 27 | 24 |
| Nat Commun | 1633 | 204.1 | 176–218 | 6 | 55 | 81 |

Neuron、Brain、Nature Neuroscience 合计 208 条，平均 26 条/周，单周 12–36 条。加上 NRN 的综述为平均约 27.4 条标题/周。
NRN 的 21 条中仅 11 条当前标为综述，平均约 1.4 篇综述/周。Nature、Science、Cell、Nature Communications 的数值含所有学科，不能当作神经科学文章量。
这是 PubMed 可检索量，可能受期刊提交、索引延迟、发表类型后补影响，不能声称等于出版社官网完整目录。

## 是否每日更新

正式期次、文章提前在线、PubMed 入库是三个时间点。无需等整期出版才检查，也不应期待每天固定新增。
- [Neuron 的 NLM 官方目录](https://www.ncbi.nlm.nih.gov/nlmcatalog/8809320)：每月两期。
- [Nature Neuroscience 期刊介绍](https://www.nature.com/neuro/journal-information)：月刊。
- [Brain 官方出版日程](https://academic.oup.com/advertising-and-corporate-services/pages/brain-media-kit)：每月一期。
- [Nature Reviews Neuroscience 官方订阅信息](https://www.nature.com/nrn/subscribe)：每年 12 期。
本次 56 天里，前三刊分别只有 29、30、31 天出现 PubMed 新条目。建议系统每天检查，读者每周集中浏览一次全主题候选。

## 用户提供的 5 篇：对当前线上策略逐组实测

基线配置版本 2026-11-07-coverage-v1。用每篇 PMID 与 A/B1/B2/C/D/E 的完整实际查询相交，不加日期；另独立测试日期范围。
当前飞书表中按这 5 个 PMID 精确查询，返回 0 条（2026-11-25 检查）。这不等于检索式全部不匹配。

| 文献 / 链接 | 期刊 | 现有检索结果与原因 |
|---|---|---|
| [Hypothalamic volume and cell-type changes in ageing and Alzheimer's disease.](https://pubmed.ncbi.nlm.nih.gov/43052445/) | Nature | 不命中：Nature 已覆盖，但癫痫、脑电、闭环或兴奋性建模入口均不匹配。 |
| ['Light and sound' therapy for Alzheimer's flops in long-anticipated trial.](https://pubmed.ncbi.nlm.nih.gov/43045786/) | Nature | 不命中：Nature News，PubMed 已收录；标题没有现有主题词，也没有摘要供检索。不是 PubMed 来源缺失。 |
| [Mapping Lesions That Cause Psychosis to a Human Brain Circuit and Proposed Stimulation Target.](https://pubmed.ncbi.nlm.nih.gov/39937525/) | JAMA Psychiatry | 不命中：JAMA Psychiatry 未列入现有期刊组；主题是精神病性症状的病灶网络映射和刺激靶点，不满足闭环条件。 |
| [Investigating the methodological foundation of lesion network mapping.](https://pubmed.ncbi.nlm.nih.gov/41540261/) | Nat Neurosci | A 组命中：摘要含 epilepsy；也属于神经科学周候选的期刊。2026 年 1 月入库，超出当前滚动窗口。 |
| [Rescue of neuropsychiatric phenotypes in a mouse model of 16p11.2 duplication syndrome by genetic correction of an epilepsy network hub.](https://pubmed.ncbi.nlm.nih.gov/36808153/) | Nat Commun | A 组命中：题名含 epilepsy，Nature Communications 在名单中。2023 年入库，超出当前滚动窗口。 |

5 篇目前都不在当日日报的 2026-11-22—11-24 入库窗口。前两篇在最近一周窗口里，但主题仍不匹配；另三篇属于历史文献。
本次 R 组只解决综述入口，这 5 篇不会因为增加综述组而全部命中。

## 后续选择建议（尚未启用）

1. 保留核心主题日报；独立 R 组从 NRN 开始。
2. 三本核心神经科学刊全主题生成周候选，每周约 26 条先扫标题，选 5–8 篇看摘要、2–3 篇精读；全部候选可追溯，阅读预算不作为检索截断条件。
3. 如扩大临床视野，可再加入 Lancet Neurology、JAMA Neurology、Annals of Neurology：本窗口合计额外约 13.9 条/周。尚未自动扩大。
4. 对 Nature / Science / Cell / Nature Communications，单独设计神经科学主题发现入口，不要求 epilepsy；先量化该入口的量，再决定收录。仅扩核心三刊不能补上前两篇 Nature 和 JAMA Psychiatry 的例子。
5. 新闻单列展示。可以追踪负面试验结果，但不把新闻标为原始研究；无摘要条目不能依靠正文里的专业关键词命中。
6. 历史感兴趣文章用指定 PMID / DOI 手动收藏或单次补录，不盲目拉长所有日报的日期窗口。

原始逐条计数、检索返回和身份信息保存在本机 outputs/search-audit-2026-11-25；本报告仅上传汇总，不含凭据或完整许可分区表。
