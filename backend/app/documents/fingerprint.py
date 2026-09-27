"""流水线指纹。

``pipeline_fingerprint`` 由「解析 / 规范化 / 切片 / Embedding / 向量 schema / FTS schema」
稳定序列化后计算 SHA-256；各阶段指纹分开保存，用于确定最早失效阶段。
阶段 2B 只推进到 ``parsed``，切片与索引组件以固定版本常量参与指纹。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import docx
import openpyxl
import pymupdf4llm

from app.config import Settings

PARSER_NAME = "campus-rag-parser"
NORMALIZATION_VERSION = "1.0.0"
CHUNKER_VERSION = "1.0.0"
# 阶段 3 接入真实模型后需替换为实际快照 revision；在此之前保持固定值以保证指纹稳定
EMBEDDING_REVISION = "1.0.0"
VECTOR_COLLECTION_NAME = "campus_chunks_v1"
VECTOR_SCHEMA_VERSION = "1.0.0"
FTS_SCHEMA_VERSION = "1.0.0"
FTS_TOKENIZER = "unicode61"

PARSER_COMPONENTS: dict[str, str] = {
    "pdf": f"pymupdf4llm-{pymupdf4llm.__version__}",
    "docx": f"python-docx-{getattr(docx, '__version__', 'unknown')}",
    "xlsx": f"openpyxl-{openpyxl.__version__}",
}


def _digest(payload: Any) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def fingerprint_payload(settings: Settings) -> dict[str, Any]:
    """指纹的稳定序列化内容；顺序无关（``sort_keys``）。"""
    return {
        "parser": {"name": PARSER_NAME, "components": PARSER_COMPONENTS},
        "normalization": {"version": NORMALIZATION_VERSION},
        "chunker": {
            "version": CHUNKER_VERSION,
            "target_chars": settings.chunk_target_chars,
            "overlap_chars": settings.chunk_overlap_chars,
        },
        "embedding": {
            "provider": settings.embedding_provider,
            "model": settings.embedding_model,
            "revision": EMBEDDING_REVISION,
            "dimension": settings.embedding_dimension,
        },
        "vector_schema": {
            "collection": VECTOR_COLLECTION_NAME,
            "version": VECTOR_SCHEMA_VERSION,
        },
        "fts_schema": {"version": FTS_SCHEMA_VERSION, "tokenizer": FTS_TOKENIZER},
    }


def stage_fingerprints(settings: Settings) -> dict[str, str]:
    """返回分阶段指纹，键名与 ``document_pipeline_state`` 列名一致。"""
    payload = fingerprint_payload(settings)
    return {
        "parser_fingerprint": _digest(payload["parser"]),
        "normalization_fingerprint": _digest(payload["normalization"]),
        "chunker_fingerprint": _digest(payload["chunker"]),
        "embedding_fingerprint": _digest(payload["embedding"]),
        "vector_schema_fingerprint": _digest(payload["vector_schema"]),
        "fts_schema_fingerprint": _digest(payload["fts_schema"]),
    }


def pipeline_fingerprint(settings: Settings) -> str:
    """任务身份指纹：任一阶段组件变化都会改变该值。"""
    return _digest(fingerprint_payload(settings))
