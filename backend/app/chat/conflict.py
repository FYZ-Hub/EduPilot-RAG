"""证据冲突的确定性检测（阶段 6）。

原则（PRODUCT_SPEC 7.3）：资料冲突时必须**并列展示冲突双方与版本信息**，
不得让模型替用户选择版本。检测完全基于检索到的证据文本与文档版本，**不读取**
``demo/ground_truth.jsonl``、``manifest.intentional_conflicts`` 或任何演示问题 ID，
也不针对具体问题硬编码。

两类信号：

1. **跨版本字段冲突**：同一字段名（如「毕业总学分」）在**不同 document_version**
   的证据里给出不同取值；
2. **同块内重复槽位冲突**：同一份证据内部，表格中「前两列相同的两行」给出了
   不同的后续内容（例如同一课表同一时间段安排了两门不同课程）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

# 单条证据：(服务端编号, 正文, document_version, doc_id)
EvidenceItem = tuple[int, str, str | None, str]

# 元数据字段按定义就会随版本变化，不能当作「内容冲突」
_METADATA_KEYS = frozenset(
    {
        "文档版本",
        "生效日期",
        "文档类型",
        "更新时间",
        "适用学期",
        "适用专业",
        "适用年级",
        "专业代码",
        "招生年份",
        "学制",
        "所属学院",
        "授予学位",
    }
)

_PAIR = re.compile(r"(?P<key>[^\s：:，。；;、|｜]{2,12})[：:]\s*(?P<value>[^\n]{1,30})")
_VALUE_TERMINATORS = "。；;，,\u3000|｜"
_CELL_SPLIT = re.compile(r"\s*[|｜]\s*|\t+|\s{2,}")


@dataclass(frozen=True)
class ConflictHint:
    """冲突提示：涉及的证据编号（服务端编号）与给模型的中性说明。"""

    indices: tuple[int, ...]
    note: str


def _normalize_value(raw: str) -> str:
    value = raw.split(_VALUE_TERMINATORS)[0]
    return re.sub(r"\s+", "", value).lower()


def extract_pairs(text: str) -> list[tuple[str, str]]:
    """抽取 ``字段名：取值`` 文本对（顺序稳定）。"""
    pairs: list[tuple[str, str]] = []
    for match in _PAIR.finditer(text or ""):
        key = match.group("key").strip()
        value = _normalize_value(match.group("value"))
        if key and value and key not in _METADATA_KEYS:
            pairs.append((key, value))
    return pairs


def _rows(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in (text or "").splitlines():
        cells = [cell.strip() for cell in _CELL_SPLIT.split(line.strip()) if cell.strip()]
        if len(cells) >= 3:
            rows.append(cells)
    return rows


def _cross_version_conflicts(evidence: Sequence[EvidenceItem]) -> list[tuple[int, int, str]]:
    """返回 ``(左编号, 右编号, 字段名)``。

    语义：**同一规范化字段名**在不同 ``document_version`` 上给出**不同取值**才算冲突。
    同一版本内重复出现同一字段（含同一 chunk 内重复）不构成跨版本冲突。
    """
    # key -> version -> (规范化取值, 首次出现的证据编号)
    observed: dict[str, dict[str, tuple[str, int]]] = {}
    for index, text, version, _doc_id in evidence:
        for key, value in extract_pairs(text):
            observed.setdefault(key, {}).setdefault(version or "", (value, index))

    found: list[tuple[int, int, str]] = []
    seen: set[tuple[int, int, str]] = set()
    for key in sorted(observed):
        by_version = observed[key]
        versions = sorted(by_version)
        for position, left_version in enumerate(versions):
            left_value, left_index = by_version[left_version]
            for right_version in versions[position + 1 :]:
                right_value, right_index = by_version[right_version]
                if left_value == right_value or left_index == right_index:
                    continue
                pair = (min(left_index, right_index), max(left_index, right_index), key)
                if pair not in seen:
                    seen.add(pair)
                    found.append(pair)
    found.sort()
    return found


def _row_conflicts(evidence: Sequence[EvidenceItem]) -> list[int]:
    """同一文档内「前两列（槽位）相同但后续列不同」的行 → 涉及证据构成冲突。

    允许冲突行分布在**同一文档的不同切片**上（切片器会把长表拆成多块）。
    """
    seen: dict[tuple[str, str, str], dict[str, int]] = {}
    hits: set[int] = set()
    for index, text, _version, doc_id in evidence:
        for cells in _rows(text):
            key = (doc_id or "", cells[0], cells[1])
            body = "|".join(cells[2:])
            bucket = seen.setdefault(key, {})
            for other_body, other_index in bucket.items():
                if other_body != body:
                    hits.add(other_index)
                    hits.add(index)
            bucket.setdefault(body, index)
    return sorted(hits)


def detect_conflicts(evidence: Sequence[EvidenceItem]) -> ConflictHint | None:
    """返回冲突提示；无冲突返回 ``None``。结果只依赖证据内容与版本。"""
    if len(evidence) < 1:
        return None

    cross = _cross_version_conflicts(evidence)
    intra = _row_conflicts(evidence)
    if not cross and not intra:
        return None

    indices: set[int] = set(intra)
    keys: list[str] = []
    for left, right, key in cross:
        indices.add(left)
        indices.add(right)
        if key not in keys:
            keys.append(key)

    parts: list[str] = []
    if cross:
        parts.append("同一字段在不同文档版本中取值不一致：" + "、".join(sorted(keys)))
    if intra:
        parts.append("同一份文档内存在重复槽位但内容不同的记录（例如同一时间段安排了两门课程）")
    return ConflictHint(indices=tuple(sorted(indices)), note="；".join(parts) + "。")


__all__ = [
    "ConflictHint",
    "EvidenceItem",
    "detect_conflicts",
    "extract_pairs",
]
