"""来源标识与「期望来源路径」的确定性映射。

- 演示文档的 ``source_key`` 形如 ``<dataset_version>:corpus/<file>``；
- ``ground_truth.jsonl`` 的 ``expected_source_paths`` 形如 ``corpus/<file>``。

因此匹配规则只有两条，且都要求**完全相等**（不做模糊、大小写或子串匹配）：

1. ``source_key == f"{dataset_version}:{expected_path}"``；
2. ``file_name == expected_path`` 的 basename。

去重与排序使用「来源标识」（``source_key`` 优先，缺失时退回 ``file_name``），
保证同一来源的多个 chunk 只计一次。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from collections.abc import Sequence


@dataclass(frozen=True)
class RetrievedSource:
    """一条检索命中来源（去重前的 chunk 级来源）。"""

    source_key: str
    file_name: str

    @property
    def identity(self) -> str:
        return self.source_key or self.file_name


def expected_identity(dataset_version: str, expected_path: str) -> str:
    """期望来源路径对应的稳定来源标识（与 demo ``source_key`` 一致）。"""
    return f"{dataset_version}:{expected_path}"


def matches_expected(source: RetrievedSource, expected_path: str, dataset_version: str) -> bool:
    """判断一条命中是否属于某个期望来源（精确相等，不做模糊匹配）。"""
    if source.source_key == expected_identity(dataset_version, expected_path):
        return True
    return source.file_name == Path(expected_path).name


def canonicalize(
    retrieved: Sequence[RetrievedSource],
    expected_paths: Sequence[str],
    dataset_version: str,
) -> list[str]:
    """把命中序列映射为「期望路径 / 原始来源标识」序列，保持原顺序。

    命中期望来源的记为期望路径（便于按来源去重与计算召回），其余保留原始标识。
    """
    canonical: list[str] = []
    for source in retrieved:
        matched = next(
            (
                path
                for path in expected_paths
                if matches_expected(source, path, dataset_version)
            ),
            None,
        )
        canonical.append(matched if matched is not None else source.identity)
    return canonical


__all__ = [
    "RetrievedSource",
    "canonicalize",
    "expected_identity",
    "matches_expected",
]
