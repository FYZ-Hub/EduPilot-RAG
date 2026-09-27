"""稳定序列化与摘要。

指纹、chunk_id 与集合对账都依赖“同样的输入 → 同样的字节 → 同样的哈希”，
因此统一在这里实现规范化 JSON 与 SHA-256，避免各模块各自实现导致漂移。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(payload: Any) -> str:
    """键顺序固定、无多余空白的 JSON 文本；中文不转义。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_digest(payload: Any) -> str:
    """对规范化 JSON 求 SHA-256（十六进制小写）。"""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def digest_text(payload: str) -> str:
    """对给定文本求 SHA-256。"""
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
