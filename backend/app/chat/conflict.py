"""证据冲突的确定性检测（阶段 6）。

原则（PRODUCT_SPEC 7.3）：资料冲突时必须**并列展示冲突双方与版本信息**，
不得让模型替用户选择版本。检测完全基于检索到的证据文本与文档版本，**不读取**
``demo/ground_truth.jsonl``、``manifest.intentional_conflicts`` 或任何演示问题 ID，
也不针对具体问题硬编码。

两类信号：

1. **跨版本字段冲突**：同一字段名（如「毕业总学分」）在**不同 document_version**
   的证据里给出不同取值；
2. **同块内重复槽位冲突**：同一份证据内部，表格中「前两列（槽位）相同的两行」给出了
   不同的后续内容（例如同一课表同一时间段安排了两门不同课程）。既支持 pipe/制表符/
   连续空格的表格行，也支持 ``app.documents.blocks.key_value_row()`` 产出的
   ``字段: 值；字段: 值`` 真实行格式（中英文冒号/分号）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.core.hashing import stable_digest

# 单条证据：(服务端编号, 正文, document_version, doc_id)
EvidenceItem = tuple[int, str, str | None, str]

# 冲突信号类型（稳定标签，供安全诊断使用）
SIGNAL_CROSS_VERSION = "cross_version"
SIGNAL_ROW_SLOT = "row_slot"
CONFLICT_SIGNAL_TYPES = (SIGNAL_CROSS_VERSION, SIGNAL_ROW_SLOT)

# 冲突被抑制的稳定原因（按信号分别判断意图未满足）
SUPPRESSION_CROSS_VERSION_NOT_REQUESTED = "cross_version_not_requested"
SUPPRESSION_ROW_SLOT_NOT_REQUESTED = "row_slot_not_requested"
SUPPRESSION_CONFLICT_INTENT_NOT_REQUESTED = "conflict_intent_not_requested"
CONFLICT_SUPPRESSION_REASONS = (
    SUPPRESSION_CROSS_VERSION_NOT_REQUESTED,
    SUPPRESSION_ROW_SLOT_NOT_REQUESTED,
    SUPPRESSION_CONFLICT_INTENT_NOT_REQUESTED,
)

# 跨版本/跨文件「比较或冲突核对」意图的**明确**标记（归一化后做子串匹配）。
# 这些是「范围词 + 比较」的显式表达，出现即命中（第一层判定）。
CROSS_VERSION_INTENT_MARKERS = (
    # 不同版本 / 跨版本
    "不同版本",
    "各版本",
    "两个版本",
    "两版",
    "版本差异",
    "版本之间",
    "版本对比",
    "版本比较",
    "跨版本",
    # 不同文件 / 跨文件
    "不同文件",
    "各文件",
    "两个文件",
    "跨文件",
    "文件差异",
    "文件对比",
    "文件比较",
    # 不同文档 / 跨文档
    "不同文档",
    "各文档",
    "两个文档",
    "跨文档",
    "文档差异",
    "文档对比",
    "文档比较",
    # 不同资料 / 跨资料
    "不同资料",
    "各资料",
    "两个资料",
    "跨资料",
    "资料差异",
    "资料对比",
    "资料比较",
    # 不同来源 / 跨来源
    "不同来源",
    "各来源",
    "两个来源",
    "跨来源",
    "来源差异",
    "来源对比",
    "来源比较",
)

# 通用比较/冲突词（第二层判定）：**单独出现不算**意图；必须同时满足
# 「出现明确范围词（:data:`CROSS_VERSION_SCOPE_WORDS`）」或「≥2 个 ``YYYY.N`` 版本标记」。
CROSS_VERSION_COMPARISON_MARKERS = (
    "是否一致",
    "一致吗",
    "不一致",
    "有无冲突",
    "是否冲突",
    "冲突吗",
    "差异",
    "区别",
    "对比",
    "比较",
    "冲突",
)
# 第二层判定所需的明确范围词
CROSS_VERSION_SCOPE_WORDS = ("版本", "文件", "文档", "资料", "来源")
# ``YYYY.N`` 形式的版本标记；用 ``[.]``/``[0-9]`` 匹配，避免把 ``2026-2027`` 学年区间
# 解析为两个版本标记。
_VERSION_MARKER_RE = re.compile(r"(?<![0-9.])[0-9]{4}[.][0-9]{1,2}(?![0-9.])")

# 「排课/课表槽位冲突」的确定性意图标记（归一化后做子串匹配）。
# 只包含**明确的排课冲突表达**；"冲突""时间""课表""上课时间"等单词单独出现都不算。
ROW_SLOT_INTENT_MARKERS = (
    "同一时间段安排",
    "同一时段安排",
    "同时安排",
    "重复排课",
    "排课冲突",
    "课表冲突",
    "课程安排冲突",
)
# 「课表/排课」上下文（必须与坐标 + 查询表达共同出现才判定意图）
_ROW_SLOT_CONTEXT_MARKERS = ("课表", "排课")
# 查询「该槽位有哪些课程」的确定性表达
_ROW_SLOT_QUERY_MARKERS = (
    "哪些课程",
    "哪几门课",
    "哪些课",
    "什么课程",
    "什么课",
    "哪门课",
    "有哪些课",
    "上什么课",
    "哪些科目",
    "安排了哪些",
)
# 明确的时间坐标：星期几 或 第X(-Y)节
_WEEKDAY_RE = re.compile(r"(星期|周|礼拜)[一二三四五六日天]")
_PERIOD_RE = re.compile(r"第?\d+(?:-\d+)?节")

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
# key_value_row 行的字段分隔（中英文分号）；字段名与取值用中英文冒号分隔
_KV_SEGMENT_SPLIT = re.compile(r"[；;]")


@dataclass(frozen=True)
class ConflictHint:
    """冲突提示：涉及的证据编号（服务端编号）与给模型的中性说明。

    另附**稳定诊断**（供 9B 安全诊断读取）：

    - ``signal_types``：命中的信号类型（``cross_version`` / ``row_slot``）；
    - ``field_count``：冲突字段/槽位键的数量；
    - ``field_digest``：冲突字段名集合的稳定哈希——**只保存哈希，不保存字段名或取值**。
    """

    indices: tuple[int, ...]
    note: str
    signal_types: tuple[str, ...] = ()
    field_count: int = 0
    field_digest: str = ""

    @property
    def conflict_indices(self) -> tuple[int, ...]:
        """与 ``indices`` 同义的稳定命名（冲突涉及的证据编号）。"""
        return self.indices


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


def _key_value_pairs(line: str) -> list[tuple[str, str]]:
    """把 ``key_value_row()`` 真实格式的一行解析为有序 ``(字段名, 规范化取值)``。

    - 字段之间用中英文分号（``；``/``;``）分隔；字段名与取值用中英文冒号（``：``/``:``）
      分隔（只按**第一个**冒号切分，兼容取值内含冒号的时间等）；
    - 整行必须可解析为**至少 3 个**有序字段值对，且每个段落都必须是合法的
      「字段名: 取值」；否则返回空列表，避免把普通正文误判为表格行。
    """
    segments = [segment.strip() for segment in _KV_SEGMENT_SPLIT.split(line)]
    segments = [segment for segment in segments if segment]
    if len(segments) < 3:
        return []
    pairs: list[tuple[str, str]] = []
    for segment in segments:
        head, separator, tail = segment.partition("：")
        if not separator:
            head, separator, tail = segment.partition(":")
        if not separator:
            return []
        name = head.strip()
        value = _normalize_value(tail)
        if not name or not value:
            return []
        pairs.append((name, value))
    return pairs


def _rows(text: str) -> list[tuple[str, str, str]]:
    """把证据正文切成可比较的「行记录」``(slot_a, slot_b, body)``（顺序稳定）。

    每个非空行按两种格式之一解析，**pipe / 制表符 / 连续空格表格优先**（保持既有行为）：

    1. 表格行（``a | b | c`` / TAB / 连续空格）：``slot_a``、``slot_b`` 取前两列原始单元格，
       ``body`` 为其余列（``|`` 连接）；
    2. ``app.documents.blocks.key_value_row()`` 真实格式（``字段: 值；字段: 值``）：整行必须
       解析为 **≥3** 个有序字段值对，``slot_a``、``slot_b`` 取前两对的「字段名+规范化取值」，
       ``body`` 为其余字段值对。

    无法解析为上述任一格式的行被忽略。
    """
    records: list[tuple[str, str, str]] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        cells = [cell.strip() for cell in _CELL_SPLIT.split(stripped) if cell.strip()]
        if len(cells) >= 3:
            records.append((cells[0], cells[1], "|".join(cells[2:])))
            continue
        pairs = _key_value_pairs(stripped)
        if len(pairs) >= 3:
            slot_a = f"{pairs[0][0]}={pairs[0][1]}"
            slot_b = f"{pairs[1][0]}={pairs[1][1]}"
            body = ";".join(f"{name}={value}" for name, value in pairs[2:])
            records.append((slot_a, slot_b, body))
    return records


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


def _row_conflicts(
    evidence: Sequence[EvidenceItem],
) -> tuple[list[int], list[str]]:
    """同一文档内「槽位相同但后续内容不同」的行 → 涉及证据构成冲突。

    槽位：pipe 行为前两列原始单元格；key_value 行为前两对的「字段名+规范化取值」。
    槽位键**包含 doc_id**，因此不同文档的同名行绝不会被误判；相同行（槽位与内容都相同）
    也不构成冲突。允许冲突行分布在**同一文档的不同切片**上（切片器会把长表拆成多块）。
    返回 ``(涉及证据编号, 冲突槽位键)``；槽位键不含 doc_id 或路径。
    """
    seen: dict[tuple[str, str, str], dict[str, int]] = {}
    hits: set[int] = set()
    slot_keys: set[str] = set()
    for index, text, _version, doc_id in evidence:
        for slot_a, slot_b, body in _rows(text):
            key = (doc_id or "", slot_a, slot_b)
            bucket = seen.setdefault(key, {})
            for other_body, other_index in bucket.items():
                if other_body != body:
                    hits.add(other_index)
                    hits.add(index)
                    slot_keys.add(f"{slot_a}|{slot_b}")
            bucket.setdefault(body, index)
    return sorted(hits), sorted(slot_keys)


def conflict_field_keys(evidence: Sequence[EvidenceItem]) -> tuple[str, ...]:
    """稳定顺序的冲突字段/槽位键（**仅进程内诊断用**；调用方不得写入报告或外发）。

    跨版本字段名在前（按检测顺序去重），同槽位键在后（排序去重）。
    本函数只读、确定性；不改变 ``detect_conflicts`` 的任何判定结果。
    """
    keys: list[str] = []
    for _left, _right, key in _cross_version_conflicts(evidence):
        if key not in keys:
            keys.append(key)
    _hits, slot_keys = _row_conflicts(evidence)
    for key in slot_keys:
        if key not in keys:
            keys.append(key)
    return tuple(keys)


def _normalize_key(text: str) -> str:
    """字段/槽位键的规范化：去掉全部空白并小写（保证摘要稳定且与渲染无关）。"""
    return re.sub(r"\s+", "", text or "").lower()


def conflict_field_digest(keys: Sequence[str], slot_keys: Sequence[str]) -> str:
    """对**规范化后的真实冲突字段键集合与 row-slot 键集合**求稳定 SHA-256。

    只输出哈希，**不保存任何原始字段名或取值**。
    """
    normalized_cross = sorted({_normalize_key(key) for key in keys if _normalize_key(key)})
    normalized_slots = sorted({_normalize_key(key) for key in slot_keys if _normalize_key(key)})
    return stable_digest({"cross": normalized_cross, "row_slots": normalized_slots})


def detect_conflicts(evidence: Sequence[EvidenceItem]) -> ConflictHint | None:
    """返回冲突提示；无冲突返回 ``None``。结果只依赖证据内容与版本。"""
    if len(evidence) < 1:
        return None

    cross = _cross_version_conflicts(evidence)
    intra, slot_keys = _row_conflicts(evidence)
    if not cross and not intra:
        return None

    indices: set[int] = set(intra)
    keys: list[str] = []
    for left, right, key in cross:
        indices.add(left)
        indices.add(right)
        if key not in keys:
            keys.append(key)

    signals: list[str] = []
    if cross:
        signals.append(SIGNAL_CROSS_VERSION)
    if intra:
        signals.append(SIGNAL_ROW_SLOT)

    parts: list[str] = []
    if cross:
        parts.append("同一字段在不同文档版本中取值不一致：" + "、".join(sorted(keys)))
    if intra:
        parts.append("同一份文档内存在重复槽位但内容不同的记录（例如同一时间段安排了两门课程）")
    return ConflictHint(
        indices=tuple(sorted(indices)),
        note="；".join(parts) + "。",
        signal_types=tuple(signals),
        field_count=len(keys) + len(slot_keys),
        field_digest=conflict_field_digest(keys, slot_keys),
    )


def cross_version_intent(question: str) -> bool:
    """问题是否**明确表达**跨版本/跨文件比较或冲突核对意图（确定性纯函数）。

    两层判定（归一化＝去空白 + 小写）：

    A. 明确跨版本/跨文件表达（:data:`CROSS_VERSION_INTENT_MARKERS`，如「不同版本」「跨文件」）
       直接命中；
    B. 通用比较词（:data:`CROSS_VERSION_COMPARISON_MARKERS`，如「是否一致」「冲突」「比较」）
       必须**同时**满足：出现明确范围词（:data:`CROSS_VERSION_SCOPE_WORDS`：版本/文件/文档/
       资料/来源），或问题中存在**至少两个** ``YYYY.N`` 形式的版本标记。

    因此裸「是否一致」「冲突」「差异」「比较」、单一年份或单一版本号都**不**构成意图；
    ``2026-2027`` 之类的学年区间也不会被解析为两个版本标记。
    """
    normalized = re.sub(r"\s+", "", question or "").lower()
    if not normalized:
        return False
    if any(marker in normalized for marker in CROSS_VERSION_INTENT_MARKERS):
        return True
    if not any(marker in normalized for marker in CROSS_VERSION_COMPARISON_MARKERS):
        return False
    if any(scope in normalized for scope in CROSS_VERSION_SCOPE_WORDS):
        return True
    return len(set(_VERSION_MARKER_RE.findall(normalized))) >= 2


def row_slot_intent(question: str) -> bool:
    """问题是否**明确表达排课槽位（同一时段两门课）冲突意图**（确定性纯函数）。

    只做归一化（去空白 + 小写）后的判定，命中任一即真：

    1. 精确的排课冲突表达（:data:`ROW_SLOT_INTENT_MARKERS`，如「同一时间段安排」「排课冲突」）；
    2. 「课表/排课」上下文 **且** 明确星期/节次坐标 **且** 查询该槽位有哪些课程
       （三者同时满足）。

    「冲突」「时间」「课表」「上课时间」等单词单独出现**不**构成意图。
    """
    normalized = re.sub(r"\s+", "", question or "").lower()
    if not normalized:
        return False
    if any(marker in normalized for marker in ROW_SLOT_INTENT_MARKERS):
        return True
    if not any(context in normalized for context in _ROW_SLOT_CONTEXT_MARKERS):
        return False
    if not (_WEEKDAY_RE.search(normalized) or _PERIOD_RE.search(normalized)):
        return False
    return any(query in normalized for query in _ROW_SLOT_QUERY_MARKERS)


def should_activate_conflict(hint: ConflictHint | None, question: str) -> bool:
    """是否激活冲突 Prompt（在**不改变** ``detect_conflicts`` 判定结果的前提下）。

    **按信号分别判断意图**：

    - 仅 ``row_slot`` → 需要 :func:`row_slot_intent`；
    - 仅 ``cross_version`` → 需要 :func:`cross_version_intent`；
    - 混合（两类信号都有）→ 满足任一对应意图即可激活，否则抑制。

    因此不会用 ``cross_version_intent`` 去激活 row_slot-only 候选。
    """
    if hint is None:
        return False
    signals = hint.signal_types
    has_row = SIGNAL_ROW_SLOT in signals
    has_cross = SIGNAL_CROSS_VERSION in signals
    if has_row and has_cross:
        return row_slot_intent(question) or cross_version_intent(question)
    if has_row:
        return row_slot_intent(question)
    return cross_version_intent(question)


def conflict_suppression_reason(hint: ConflictHint | None, question: str) -> str | None:
    """候选冲突存在但未被激活时的稳定原因；否则 ``None``（按未满足的信号给出）。"""
    if hint is None or should_activate_conflict(hint, question):
        return None
    has_row = SIGNAL_ROW_SLOT in hint.signal_types
    has_cross = SIGNAL_CROSS_VERSION in hint.signal_types
    if has_row and has_cross:
        return SUPPRESSION_CONFLICT_INTENT_NOT_REQUESTED
    if has_row:
        return SUPPRESSION_ROW_SLOT_NOT_REQUESTED
    return SUPPRESSION_CROSS_VERSION_NOT_REQUESTED


__all__ = [
    "CONFLICT_SIGNAL_TYPES",
    "CONFLICT_SUPPRESSION_REASONS",
    "CROSS_VERSION_COMPARISON_MARKERS",
    "CROSS_VERSION_INTENT_MARKERS",
    "CROSS_VERSION_SCOPE_WORDS",
    "ConflictHint",
    "EvidenceItem",
    "ROW_SLOT_INTENT_MARKERS",
    "SIGNAL_CROSS_VERSION",
    "SIGNAL_ROW_SLOT",
    "SUPPRESSION_CONFLICT_INTENT_NOT_REQUESTED",
    "SUPPRESSION_CROSS_VERSION_NOT_REQUESTED",
    "SUPPRESSION_ROW_SLOT_NOT_REQUESTED",
    "conflict_field_digest",
    "conflict_field_keys",
    "conflict_suppression_reason",
    "cross_version_intent",
    "detect_conflicts",
    "extract_pairs",
    "row_slot_intent",
    "should_activate_conflict",
]
