"""流水线指纹。

``pipeline_fingerprint`` 由「解析 / 规范化 / 切片 / Embedding / 向量 schema / FTS schema」
稳定序列化后计算 SHA-256；各阶段指纹分开保存，用于确定「最早失效阶段」。
Embedding 身份只来自 ``app.embedding.descriptor_for``，避免多处重复拼装导致漂移。
"""

from __future__ import annotations

from typing import Any

import docx
import openpyxl
import pymupdf4llm

from app.config import Settings
from app.core.hashing import stable_digest
from app.embedding.base import descriptor_for
from app.search.schema import (
    FTS_NGRAM_VERSION,
    FTS_NORMALIZATION_VERSION,
    FTS_SCHEMA_VERSION,
    FTS_TOKENIZER,
)

PARSER_NAME = "campus-rag-parser"
PARSER_VERSION = "1.0.0"
NORMALIZATION_VERSION = "1.0.0"
CHUNKER_VERSION = "1.0.0"
VECTOR_COLLECTION_NAME = "campus_chunks_v1"
VECTOR_SCHEMA_VERSION = "1.0.0"

PARSER_COMPONENTS: dict[str, str] = {
    "pdf": f"pymupdf4llm-{pymupdf4llm.__version__}",
    "docx": f"python-docx-{getattr(docx, '__version__', 'unknown')}",
    "xlsx": f"openpyxl-{openpyxl.__version__}",
}


def fingerprint_payload(settings: Settings) -> dict[str, Any]:
    """指纹的稳定序列化内容；顺序无关（``sort_keys``）。"""
    return {
        "parser": {"name": PARSER_NAME, "version": PARSER_VERSION, "components": PARSER_COMPONENTS},
        "normalization": {"version": NORMALIZATION_VERSION},
        "chunker": {
            "version": CHUNKER_VERSION,
            "target_chars": settings.chunk_target_chars,
            "overlap_chars": settings.chunk_overlap_chars,
        },
        "embedding": descriptor_for(settings).as_dict(),
        "vector_schema": {
            "collection": VECTOR_COLLECTION_NAME,
            "version": VECTOR_SCHEMA_VERSION,
        },
        "fts_schema": {
            "version": FTS_SCHEMA_VERSION,
            "tokenizer": FTS_TOKENIZER,
            # 中文检索规范化与 bigram 辅助列版本；变化即触发只重建 FTS
            "normalization": FTS_NORMALIZATION_VERSION,
            "ngram": FTS_NGRAM_VERSION,
        },
    }


def stage_fingerprints(settings: Settings) -> dict[str, str]:
    """返回分阶段指纹，键名与 ``document_pipeline_state`` 列名一致。"""
    payload = fingerprint_payload(settings)
    return {
        "parser_fingerprint": stable_digest(payload["parser"]),
        "normalization_fingerprint": stable_digest(payload["normalization"]),
        "chunker_fingerprint": stable_digest(payload["chunker"]),
        "embedding_fingerprint": stable_digest(payload["embedding"]),
        "vector_schema_fingerprint": stable_digest(payload["vector_schema"]),
        "fts_schema_fingerprint": stable_digest(payload["fts_schema"]),
    }


def pipeline_fingerprint(settings: Settings) -> str:
    """任务身份指纹：任一阶段组件变化都会改变该值。"""
    return stable_digest(fingerprint_payload(settings))
