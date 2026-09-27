"""稳定 chunk_id。

``chunk_id = SHA256(document_checksum + canonical_locator + chunk_index + parser_version + chunker_version)``
其中：
- ``document_checksum`` 是**文档身份校验值**：``source_type + source_key + 文件 SHA-256`` 的摘要。
  仅使用文件 SHA-256 会让「同一份文件同时存在于 demo 与 upload」两种情况生成相同 chunk_id，
  与「demo / upload 即使 SHA 相同也必须完全独立」直接冲突（且会在 SQLite 主键上真实碰撞）。
  绑定来源键后既保持公式形状与确定性，又保证跨来源互不复用。
- ``canonical_locator`` 为键顺序固定的 JSON（``sort_keys``）
- 不包含当前时间、UUID、绝对路径或数据库自增 ID
- ``chunk_index`` 为稳定顺序号
因此同一输入重复切片必定得到字节级相同的 chunk_id 集合。
"""

from __future__ import annotations

from typing import Any

from app.core.hashing import canonical_json, digest_text

# 固定拼接分隔符：任何一段内容都不含该字符，保证拼接无歧义
_SEPARATOR = "\x1f"


def canonical_locator(locator: dict[str, Any]) -> str:
    return canonical_json(locator)


def document_checksum(*, source_type: str, source_key: str, file_sha256: str) -> str:
    """文档身份校验值：把来源与文件校验值绑定为稳定摘要。"""
    return digest_text(_SEPARATOR.join((source_type, source_key, file_sha256)))


def compute_chunk_id(
    *,
    document_checksum: str,
    locator: dict[str, Any],
    chunk_index: int,
    parser_version: str,
    chunker_version: str,
) -> str:
    payload = _SEPARATOR.join(
        (
            document_checksum,
            canonical_locator(locator),
            str(chunk_index),
            parser_version,
            chunker_version,
        )
    )
    return digest_text(payload)


__all__ = ["canonical_locator", "compute_chunk_id", "document_checksum"]
