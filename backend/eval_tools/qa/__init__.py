"""阶段 9B：问答 / 学业评测（引用支持率、拒答、学分规划、提示注入）。

与 9A 检索评测分离：本子包评测**回答层与规划层**，不重复检索指标。

关键前提：**先判定 LLM 是否具备语义评测资格**。``fake`` LLM 的回答完全由传入证据文本
拼装，既不判断证据是否支持结论，也不具备任何指令遵循/抵抗能力，因此它是
**非语义测试替身**：依赖 LLM 语义的指标只能作为诊断，**不得作为质量门禁**。
"""

from __future__ import annotations

# 本子包报告与口径的版本号；任何口径变化都必须同步提升该版本。
# 3.0：正式语义指标（引用支持率 / 拒答正确率 / 注入抵抗）接入 readiness 门禁与聚合。
# 3.1：新增冲突安全诊断字段（conflict_* 稳定标签/计数与 question_field_exact_match）。
# 3.2：仅 cross_version 候选需问题明确表达比较意图才激活；新增激活/抑制诊断字段。
# 3.3：Judge 契约升为 qa-citation-judge/2（verdict 由服务端派生），报告记录 judge_contract_version。
# 3.4：injection_resistance 正式判据改为「严格拒答 或 有依据安全回答」双路径。
# 3.5：D2 section_title 判定规范空白与 ">" 层级；叶级期望可匹配完整标题路径的最后一段。
# 3.6：citation 组补充最小安全诊断（retrieval/citation coverage、D2 白名单失败字段、
#      Judge 逐事实布尔与来源数量汇总）；全部只读旁路，不改变指标、阈值与门禁。
# 3.7：新增检索侧 locator 覆盖（retrieval_locator_coverage 与 locator 数量汇总），
#      用于区分「正确 chunk 未召回」与「已召回但未引用」；同样只读旁路。
# 3.8：D2 的叶级 section 期望可匹配实际标题路径中的任一非根段（父章节 locator 命中
#      子章节 chunk）；根标题、子串与模糊匹配仍不允许。
# 3.9：新增检索分阶段 locator 覆盖（fused / reranked / final 各自 recalled/best_rank/
#      failed_fields）；同一批检索结果的只读旁路，不改变输出条数与排序。
# 3.10：新增 reranked / final 候选构成（rank、source_digest、locator_digest、
#       matched_expected_indices）与构成汇总（不同来源数、不同 locator 数、重复槽位数），
#       用于离线模拟 top-10 选择算法的收益；同样只读。
# 3.11：新增重排降级安全诊断：逐案例 rerank_applied / rerank_degraded /
#       rerank_degraded_reason（稳定小写标签，非法归 unknown），顶层汇总
#       degraded_case_count / reason_counts / case_ids；空候选 applied=false 不算降级。
# 3.12：新增 Embedding 失败安全诊断：逐案例 embedding_failed / embedding_failure_reason
#       （稳定小写标签，非法归 unknown），顶层汇总 failed_case_count / reason_counts /
#       case_ids；仅在进程内记录，不进入 SSE/API，不改变错误码、门禁与指标。
# 3.13：Provider 审计新增全局 failure_reason_counts（覆盖含 demo 导入的全部真实调用；
#       ApiError 只取 details.reason 的稳定小写标签，其它异常只取安全异常类名，非法归
#       unknown）；calls/ok/failed、失败粘滞、readiness、门禁与指标全部不变。
# 3.14：冲突激活改为按信号分别判断意图：新增 row_slot_intent 稳定诊断与
#       row_slot_not_requested / conflict_intent_not_requested 抑制原因；
#       detect_conflicts 判定、schema 之外的门禁与指标不变。
# 3.15：新增答案事实锚点安全诊断 fact_anchor_diagnostics（逐事实 ordinal/两个
#       布尔 + 确定性数字/日期/时间/代码锚点的命中计数与"全部命中"事实数）；
#       只复用内存数据、不加 Provider 调用、不落盘正文或摘要，指标与门禁不变。
# 3.16：拒答观测口径与正式口径统一：executor.REFUSAL_REASONS 由
#       metrics.STRICT_REFUSAL_REASONS 派生（no_evidence / below_score_threshold /
#       insufficient_evidence），不再单独硬编码；修复 v6 契约下模型拒答
#       insufficient_evidence 被判 refusal_wrong_reason、refusal_observed_rate 归零的
#       观测漂移。正式 refusal_case_verdict、injection 双路径、Prompt/grounding、
#       指标阈值与门禁均不变。
# 3.17：cross_version 冲突激活意图收紧：cross_version_intent 由「通用比较词直接命中」
#       改为「必须有版本/文件/文档/资料/来源范围词，或 ≥2 个 YYYY.N 版本标记」；
#       裸「是否一致/冲突/差异/比较」与单一年份/单一版本号不再激活。
#       detect_conflicts、候选 indices/摘要、row_slot 意图与按信号激活矩阵均不变。
# 3.18：ground truth 引入 required/supporting 分层：expected_answer_facts /
#       expected_source_paths / expected_locators 仍是正式必需项（参与 D1/D2/J、
#       指标与门禁）；新增 supporting_answer_facts / supporting_source_paths /
#       supporting_locators 作为参考项，仅供记录，不参与任何判定、指标或门禁。
# 3.19：新增 citation_selection 安全诊断：把**实际发出**的引用与本次真实检索序列对齐，
#       逐引用输出 citation_index / final_rank / source_digest / locator_digest（只有序号
#       与单向摘要，不含来源名、路径、正文、quote、chunk_id 或分数）；citation 组为列表，
#       非 citation 组为 null。仅新增诊断字段，不改回答、引用映射、SSE/API、指标与门禁。
# 3.20：ground truth 引入 required_evidence_groups（组间 AND、组内 OR）：D1 改为「每组至少
#       一个备选来源被引用」、D2 改为「每组至少一个备选 locator 被同来源命中」，两者同源派生；
#       无证据组时退回既有扁平判定（等价于每个 expected locator 一个单元素组），
#       expected_locators 为全部备选的扁平并集。新增 citation 组安全诊断 group_coverage
#       （仅 group_index、covered 布尔与命中的备选序号）。Judge、事实、指标、阈值与门禁不变。
# 3.21：两处经语料审计确认的**等价证据**修正（仅 ground truth，不改判定逻辑）：
#       gt-cross-007 第二组改为「2026 方案三、学分要求 OR 学生B 汇总（公共必修 55 / 依据
#       版本 2026.1）」；gt-conflict-002 第二组改为「学分认定办法第四章 OR 2025 方案三、学分
#       要求（同为 6 学分）」，并把 fact#2 改为中性表述「另一份文件规定：交流课程单次最多认定
#       6 学分」（8 学分事实与"两处规定冲突"不变）。expected_* 同步为备选并集。
SCHEMA_VERSION = "rag-qa-eval/3.21"

__all__ = ["SCHEMA_VERSION"]
