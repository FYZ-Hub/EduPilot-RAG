"""FTS5 索引结构定义（叶子模块，不导入应用内其它模块）。

FTS5 与业务表同处一个 SQLite 文件，因此索引写入可以参与同一个事务：
`keyword_indexed` 的规范化写入、多余记录精确删除、ID 对账、计数与检查点
全部在同一事务内提交，不会出现「检查点成功但 FTS 记录不完整」。
"""

from __future__ import annotations

FTS_TABLE_NAME = "chunk_fts"

# --- 版本常量（全部进入 fts_schema_fingerprint） -----------------------------
FTS_SCHEMA_VERSION = "1.0.0"
FTS_TOKENIZER = "unicode61 remove_diacritics 2"
FTS_NORMALIZATION_VERSION = "1.0.0"
# 中文检索规范化：确定性 CJK 二元组（bigram）辅助列；不修改原始 chunk 正文
FTS_NGRAM_VERSION = "cjk-bigram-v1"

# 身份字段不参与分词：避免 chunk_id / doc_id 被切碎后误命中
FTS_UNINDEXED_COLUMNS = ("chunk_id", "doc_id", "fts_fingerprint")
FTS_INDEXED_COLUMNS = (
    "title",
    "body",
    "body_ngram",
    "course_code",
    "file_name",
    "doc_category",
    "source_type",
    "source_key",
    "dataset_version",
    "document_version",
    "effective_from",
    "major",
    "grade_year",
    "semester",
)
FTS_COLUMNS = FTS_UNINDEXED_COLUMNS + FTS_INDEXED_COLUMNS

FTS_DDL = (
    f"CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE_NAME} USING fts5("
    + ", ".join(
        f"{name} UNINDEXED" if name in FTS_UNINDEXED_COLUMNS else name
        for name in FTS_COLUMNS
    )
    + f", tokenize = '{FTS_TOKENIZER}')"
)

# 唯一 active demo dataset 指针：全局最多一行
ACTIVE_DATASET_INDEX_SQL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_demo_active_dataset_single "
    "ON demo_active_dataset(active_marker) WHERE active_marker IS NOT NULL"
)

__all__ = [
    "ACTIVE_DATASET_INDEX_SQL",
    "FTS_COLUMNS",
    "FTS_DDL",
    "FTS_INDEXED_COLUMNS",
    "FTS_NGRAM_VERSION",
    "FTS_NORMALIZATION_VERSION",
    "FTS_SCHEMA_VERSION",
    "FTS_TABLE_NAME",
    "FTS_TOKENIZER",
    "FTS_UNINDEXED_COLUMNS",
]
