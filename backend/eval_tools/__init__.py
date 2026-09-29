"""阶段 9A：离线 RAG 检索评测工具（独立于生产运行链路）。

范围严格限定为检索质量基线：``Recall@5``、``MRR`` 与检索 ``P50/P95`` 时延。
本轮**不评测**生成质量、拒答正确率、学分正确率与安全攻击，也不调用任何真实 LLM。

设计约束：

- 使用真实 SQLite + Chroma + FTS5 检索链，Embedding / Reranker 一律使用 Fake Provider；
- 数据、索引与报告全部写入隔离目录（``.tmp/eval/<run-id>/``），不触碰默认 ``data/``；
- 不联网、不下载模型、不读改写死的答案；
- 输出机器可读 JSON 与 Markdown 报告。
"""

from __future__ import annotations

# 报告与指标口径的版本号；任何口径变化都必须同步提升该版本。
SCHEMA_VERSION = "rag-retrieval-eval/2.0"

__all__ = ["SCHEMA_VERSION"]
