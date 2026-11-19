# 日报中的期刊分区

日报使用中科院2025大类分区和2026年发布的JCR学科分区（2025数据）。同一期刊的多个JCR学科分区全部显示。分区用于阅读参考，文章的优先级继续按主题相关度生成。

已核验的资料以压缩JSON保存在仓库的 `JOURNAL_RANKINGS_B64` Secret，自动任务运行时读取。完整来源文件保留在本机。每条资料包含刊名、简称、ISSN、中科院大类分区和JCR各学科分区。来源为高校保存的中科院2025数据，以及UEFISCDI公开的2026年6月JCR学科分区档案。

新增文献先用ISSN匹配，再用规范化的刊名或简称匹配。资料未覆盖的期刊显示“待核验”，可在日报JSON的 `meta.journal_rankings.unverified_pmids` 中查看。预印本、已核验的图书系列和没有JIF分区的期刊显示对应状态。

更新资料时扩展原有目录，保持明确的年份、来源和期刊标识，再替换仓库Secret。本地可用 `JOURNAL_RANKINGS_FILE` 指定相同格式的JSON文件进行检查。

在Actions手动运行中选择 `repair_rankings`，可以补齐飞书中的空白分区、更新自动生成的待核验标记，并为已保存的最近一期日报重新生成分区信息。已有人工填写的分区会保留。

来源页面

- https://cce.ncepu.edu.cn/xzzx/yjsjx1/7632005045644e61a0d12c7bbeff6204.htm
- https://uefiscdi.gov.ro/scientometrie-reviste
- https://www.elsevier.com/books-and-journals/book-series
