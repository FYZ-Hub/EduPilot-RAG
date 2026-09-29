"""离线检索评测的纯函数指标（无 I/O、无线程、无网络）。

固定口径（随 ``rag-retrieval-eval/1.0`` 版本化）：

- **Recall@k**：先去重（保留首次出现顺序）再取前 ``k`` 个来源，命中数 / ``|expected|``；
- **MRR**：``1 / rank``，``rank`` 是第一个相关来源在去重来源序列中的 1-based 位次，未命中记 0；
- **百分位**：线性插值（等价 numpy 默认 ``linear``），空样本返回 ``None``。

空 ``expected``（例如 ``should_refuse`` 用例）在本层视为非法输入并显式抛错，
以免把「无正例」误算成 0 或 1。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence


def dedupe_preserving_order(items: Iterable[str]) -> list[str]:
    """按首次出现顺序去重，保证稳定排序。"""
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def recall_at_k(expected: Sequence[str], retrieved: Sequence[str], k: int) -> float:
    """命中比例：``|top_k 去重来源 ∩ expected| / |expected|``。"""
    if k <= 0:
        raise ValueError("k 必须为正整数")
    expected_set = set(expected)
    if not expected_set:
        raise ValueError("expected 不能为空：无正例用例不参与召回指标")
    top_k = dedupe_preserving_order(retrieved)[:k]
    return len(expected_set.intersection(top_k)) / len(expected_set)


def reciprocal_rank(expected: Sequence[str], retrieved: Sequence[str]) -> float:
    """首个相关来源的倒数排名；不含相关来源时返回 0.0。"""
    expected_set = set(expected)
    if not expected_set:
        raise ValueError("expected 不能为空：无正例用例不参与 MRR 指标")
    for rank, source in enumerate(dedupe_preserving_order(retrieved), start=1):
        if source in expected_set:
            return 1.0 / rank
    return 0.0


def percentile(values: Sequence[float], p: float) -> float | None:
    """线性插值百分位；空样本返回 ``None``。"""
    if not 0.0 <= p <= 100.0:
        raise ValueError("百分位 p 必须落在 [0, 100]")
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = (p / 100.0) * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def mean(values: Sequence[float]) -> float | None:
    """算术平均；空序列返回 ``None``。"""
    if not values:
        return None
    return sum(float(value) for value in values) / len(values)


__all__ = [
    "dedupe_preserving_order",
    "mean",
    "percentile",
    "recall_at_k",
    "reciprocal_rank",
]
