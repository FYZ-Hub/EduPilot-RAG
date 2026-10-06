"""9B citation 失败的最小安全诊断（评测专用纯函数）。

设计边界：

- **只复用本次真实检索与引用结果**：本模块不做任何检索、网络或 Provider 调用，输入都是
  调用方已经拿到的进程内数据；
- 原始来源名只在**进程内**参与匹配，落盘的只有 :func:`source_digest`（带域分隔的单向摘要），
  报告里不出现文件名或路径；
- 所有输出仅含摘要、布尔、计数与白名单标签；**不含** answer、quote、section 原文、
  Judge 原文、URL 或密钥；
- 本模块只服务评测报告，**不进入 SSE / API**，也不参与指标、阈值与门禁计算。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence

from app.search.reranking import RETRIEVAL_STAGES, STAGE_FINAL, STAGE_RERANKED

from eval_tools.qa.metrics import (
    FAILED_FIELD_SOURCE,
    cite_parts,
    locator_failed_fields,
    locator_matches,
    normalize_section_title,
)

# 摘要域分隔前缀：避免与其它 sha256 用途产生相同取值空间
SOURCE_DIGEST_PREFIX = "campus-rag-qa-source:"
SOURCE_DIGEST_LENGTH = 12


def _basename(value: object) -> str:
    """来源归一化：只取最后一段并去空白（与判定口径一致）。"""
    return str(value or "").rsplit("/", 1)[-1].strip()


def source_digest(name: object) -> str:
    """来源名的单向摘要（固定长度十六进制）；报告只出现该摘要。"""
    payload = (SOURCE_DIGEST_PREFIX + _basename(name)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:SOURCE_DIGEST_LENGTH]


# locator 摘要：域分隔前缀 + 与 D2 白名单一致的字段顺序
LOCATOR_DIGEST_PREFIX = "campus-rag-qa-locator:"
LOCATOR_DIGEST_FIELDS = (
    "page_number",
    "sheet_name",
    "row_start",
    "row_end",
    "section_title",
)


def locator_digest(name: object, locator: Mapping) -> str:
    """locator 的单向摘要（固定长度十六进制）。

    只由来源摘要 + 规范化 locator 字段派生，**不落盘**原始来源、路径或 locator 取值；
    ``section_title`` 复用 D2 的规范化口径，保证「判定相等 ⇒ 摘要相等」。
    """
    fields: list[str] = []
    for key in LOCATOR_DIGEST_FIELDS:
        value = locator.get(key)
        if key == "section_title":
            value = normalize_section_title(value) or None
        fields.append("" if value is None else str(value))
    payload = (LOCATOR_DIGEST_PREFIX + source_digest(name) + "\x1f").encode("utf-8")
    payload += "\x1f".join(fields).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:SOURCE_DIGEST_LENGTH]


def _matched_expected_indices(
    parts: tuple[str | None, dict], expected_locators: Sequence[dict]
) -> tuple[int, ...]:
    """该候选块命中了哪些期望 locator（1 起序号）；复用 D2 的判定与归因。"""
    source, locator = parts
    return tuple(
        index
        for index, expected in enumerate(expected_locators, start=1)
        if not locator_failed_fields(expected, locator, cited_source=source)
    )


def stage_composition(
    stages: Mapping[str, Sequence[object]],
    expected_locators: Sequence[dict],
    stage_names: Sequence[str] = (STAGE_RERANKED, STAGE_FINAL),
) -> dict[str, tuple[dict, ...]]:
    """逐阶段的**候选构成**：每项只含 ``rank``、``source_digest``、``locator_digest``、
    ``matched_expected_indices``。

    用于离线模拟「去重 / 多样性选择」的收益：可以据此算出哪些槽位是同来源或同 locator
    的重复。不新增任何检索或 Provider 调用，也不改动排序与输出。
    """
    composition: dict[str, tuple[dict, ...]] = {}
    for name in stage_names:
        rows: list[dict] = []
        for rank, candidate in enumerate(stages.get(name) or (), start=1):
            parts = cite_parts(candidate)
            source, locator = parts
            rows.append(
                {
                    "rank": rank,
                    "source_digest": source_digest(source),
                    "locator_digest": locator_digest(source, locator),
                    "matched_expected_indices": _matched_expected_indices(
                        parts, expected_locators
                    ),
                }
            )
        composition[name] = tuple(rows)
    return composition


def composition_summary(rows: Sequence[dict]) -> dict:
    """构成汇总：不同来源数、不同 locator 数、**重复槽位数**。

    ``duplicate_slot_count`` = 其 ``(source_digest, locator_digest)`` 在更早槽位已出现过的
    槽位数量，即可被去重腾出的名额上界。
    """
    source_keys: set[str] = set()
    locator_keys: set[tuple[str, str]] = set()
    duplicate_slots = 0
    for row in rows:
        source_key = str(row.get("source_digest") or "")
        locator_key = (source_key, str(row.get("locator_digest") or ""))
        source_keys.add(source_key)
        if locator_key in locator_keys:
            duplicate_slots += 1
        else:
            locator_keys.add(locator_key)
    return {
        "distinct_source_count": len(source_keys),
        "distinct_locator_count": len(locator_keys),
        "duplicate_slot_count": duplicate_slots,
    }


def _ordered_sources(sources: Iterable[object]) -> list[str]:
    """去重并稳定排序，保证同一输入总是产出相同行序。"""
    return sorted({_basename(item) for item in sources if _basename(item)})


def _cited_indices(cited_sources: Sequence[object]) -> dict[str, list[int]]:
    """来源名 → 它在引用序列中的位置（1 起，可能有多个）。"""
    indices: dict[str, list[int]] = {}
    for position, name in enumerate(cited_sources, start=1):
        key = _basename(name)
        if key:
            indices.setdefault(key, []).append(position)
    return indices


def _retrieved_ranks(retrieved_sources: Sequence[object]) -> dict[str, int]:
    """来源名 → 它在本次检索序列中的最好（最小）名次（1 起）。"""
    ranks: dict[str, int] = {}
    for position, name in enumerate(retrieved_sources, start=1):
        key = _basename(name)
        if key and key not in ranks:
            ranks[key] = position
    return ranks


def retrieval_coverage(
    expected_sources: Sequence[object], retrieved_sources: Sequence[object]
) -> tuple[dict, ...]:
    """逐期望来源：是否**被本次真实检索召回**，以及最好名次。

    这是区分「检索 top-k 未召回」与「已召回但模型未引用」的唯一依据。
    """
    ranks = _retrieved_ranks(retrieved_sources)
    return tuple(
        {
            "source_digest": source_digest(name),
            "recalled": name in ranks,
            "best_rank": ranks.get(name),
        }
        for name in _ordered_sources(expected_sources)
    )


def group_coverage(
    groups: Sequence[Sequence[dict]], cited_items: Sequence[object]
) -> tuple[dict, ...]:
    """逐**证据组**的安全覆盖诊断（组间 AND、组内 OR）。

    每组只输出：``group_index``（1 起）、``covered``（是否有任一备选被同来源引用块命中）、
    ``matched_alternative``（命中的备选序号，1 起；未命中为 ``None``）。不含来源名、路径、
    正文或 quote；与判定共用 :func:`locator_matches` 口径，不会漂移。
    """
    parts = [cite_parts(item) for item in cited_items]
    rows: list[dict] = []
    for group_index, group in enumerate(groups, start=1):
        matched: int | None = None
        for alternative_index, alternative in enumerate(group, start=1):
            if any(
                locator_matches(alternative, locator, cited_source=source)
                for source, locator in parts
            ):
                matched = alternative_index
                break
        rows.append(
            {
                "group_index": group_index,
                "covered": matched is not None,
                "matched_alternative": matched,
            }
        )
    return tuple(rows)


def citation_coverage(
    expected_sources: Sequence[object], cited_sources: Sequence[object]
) -> tuple[dict, ...]:
    """逐期望来源：是否**被引用**，以及出现在引用的第几条（1 起）。"""
    indices = _cited_indices(cited_sources)
    return tuple(
        {
            "source_digest": source_digest(name),
            "cited": name in indices,
            "citation_indices": tuple(indices.get(name, ())),
        }
        for name in _ordered_sources(expected_sources)
    )


def _nearest_locator_rows(
    expected_locators: Sequence[dict], parts: Sequence[tuple[str | None, dict]]
) -> list[tuple[bool, int | None, tuple[str, ...]]]:
    """逐期望 locator：``(是否命中, 最早命中名次 1 起, 未命中时的最近失败字段)``。

    未命中时取「失败字段最少」的那个块作为归因对象（相同则取序列中靠前者）；没有任何块
    可比对时归因到 ``source``。D2（对引用块）与 locator 召回（对检索块）共用本函数，
    因此两者的判定与归因语义不可能漂移。
    """
    rows: list[tuple[bool, int | None, tuple[str, ...]]] = []
    for expected in expected_locators:
        matched = False
        best_rank: int | None = None
        nearest: tuple[str, ...] | None = None
        for rank, (source, locator) in enumerate(parts, start=1):
            failed = locator_failed_fields(expected, locator, cited_source=source)
            if not failed:
                matched = True
                best_rank = rank
                break
            if nearest is None or len(failed) < len(nearest):
                nearest = failed
        rows.append(
            (
                matched,
                best_rank,
                () if matched else (nearest or (FAILED_FIELD_SOURCE,)),
            )
        )
    return rows


def locator_diagnostics(
    expected_locators: Sequence[dict], cited_items: Sequence[object]
) -> tuple[dict, ...]:
    """D2：逐期望 locator 是否被**引用块**命中，以及未命中时的白名单字段标签。

    标签只含字段名，不含任何取值。
    """
    parts = [cite_parts(item) for item in cited_items]
    return tuple(
        {
            "expected_index": index,
            "matched": matched,
            "failed_fields": failed,
        }
        for index, (matched, _rank, failed) in enumerate(
            _nearest_locator_rows(expected_locators, parts), start=1
        )
    )


def locator_retrieval_coverage(
    expected_locators: Sequence[dict], retrieved_items: Sequence[object]
) -> tuple[dict, ...]:
    """逐期望 locator：**本次真实检索结果**里是否已有可命中的 chunk。

    这是区分「正确 chunk 未召回」与「已召回但未引用」的依据：``recalled_match`` 为真表示
    检索结果本身已包含能通过 D2 判定的块，``best_rank`` 为其最早名次（1 起）；未命中时
    ``failed_fields`` 说明最接近的检索块为何不满足（复用 D2 白名单），没有可比对块时归因到
    ``source``。输出只含序号、布尔、名次与字段名——不含来源名、路径、section 取值或原始 locator。
    """
    parts = [cite_parts(item) for item in retrieved_items]
    return tuple(
        {
            "expected_index": index,
            "recalled_match": matched,
            "best_rank": rank,
            "failed_fields": failed,
        }
        for index, (matched, rank, failed) in enumerate(
            _nearest_locator_rows(expected_locators, parts), start=1
        )
    )


def locator_count_summary(rows: Sequence[dict]) -> dict:
    """汇总：期望 locator 总数、其中已被本次检索命中的数量。"""
    return {
        "expected_locator_count": len(rows),
        "recalled_locator_count": sum(1 for row in rows if row.get("recalled_match")),
    }


def staged_locator_coverage(
    expected_locators: Sequence[dict],
    stages: Mapping[str, Sequence[object]],
) -> tuple[dict, ...]:
    """逐期望 locator 的**分阶段**覆盖：``fused`` / ``reranked`` / ``final``。

    三个阶段都复用同一套 D2 判定与归因（:func:`_nearest_locator_rows`），因此同一输入下
    各阶段的 ``failed_fields`` 口径完全一致。输出每行只含：

    - ``expected_index``（1 起）
    - ``{stage}_recalled`` / ``{stage}_best_rank`` / ``{stage}_failed_fields``

    不含来源名、路径、section 取值、正文或分数。
    """
    per_stage = {
        name: _nearest_locator_rows(
            expected_locators, [cite_parts(item) for item in (stages.get(name) or ())]
        )
        for name in RETRIEVAL_STAGES
    }
    rows: list[dict] = []
    for index in range(len(expected_locators)):
        row: dict = {"expected_index": index + 1}
        for name in RETRIEVAL_STAGES:
            matched, rank, failed = per_stage[name][index]
            row[f"{name}_recalled"] = matched
            row[f"{name}_best_rank"] = rank
            row[f"{name}_failed_fields"] = failed
        rows.append(row)
    return tuple(rows)


def judge_fact_diagnostics(fact_results: Sequence[object] | None) -> tuple[dict, ...]:
    """Judge 逐事实：仅 fact ordinal、两个布尔判断与 evidence 序号。"""
    rows: list[dict] = []
    for item in fact_results or ():
        rows.append(
            {
                "ordinal": int(getattr(item, "fact_index", 0)),
                "answer_expresses": bool(getattr(item, "answer_expresses", False)),
                "evidence_supports": bool(getattr(item, "evidence_supports", False)),
                "evidence_indices": tuple(
                    int(value) for value in (getattr(item, "evidence_indices", ()) or ())
                ),
            }
        )
    return tuple(rows)


def source_count_summary(
    expected_sources: Sequence[object],
    retrieved_sources: Sequence[object],
    cited_sources: Sequence[object],
) -> dict:
    """汇总**期望来源**中：总数 / 已被检索召回数 / 已被引用数。"""
    expected = _ordered_sources(expected_sources)
    ranks = _retrieved_ranks(retrieved_sources)
    indices = _cited_indices(cited_sources)
    return {
        "expected_source_count": len(expected),
        "recalled_source_count": sum(1 for name in expected if name in ranks),
        "cited_source_count": sum(1 for name in expected if name in indices),
    }


# 常见标点统一（NFKC 之后仍保留作为安全网）：把全角/变体标点折到 ASCII
_PUNCT_TRANSLATION = str.maketrans(
    {
        "：": ":",
        "，": ",",
        "。": ".",
        "、": ",",
        "；": ";",
        "（": "(",
        "）": ")",
        "％": "%",
        "－": "-",
        "–": "-",
        "—": "-",
        "﹣": "-",
        "／": "/",
        "．": ".",
        "＊": "*",
        "＋": "+",
        "＝": "=",
        "！": "!",
        "？": "?",
        "＜": "<",
        "＞": ">",
        "｜": "|",
    }
)
_DATE_RE = re.compile(r"\d{4}[-/.]\d{1,2}(?:[-/.]\d{1,2})?")
_TIME_RE = re.compile(r"\d{1,2}:\d{2}(?::\d{2})?")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_ANCHOR_TOKEN_RE = re.compile(
    r"\d{4}[-/.]\d{1,2}(?:[-/.]\d{1,2})?"
    r"|\d{1,2}:\d{2}(?::\d{2})?"
    r"|[a-z0-9][a-z0-9._-]*"
)


def _normalize_for_match(text: object) -> str:
    """匹配用规范化：NFKC → 常规标点统一 → 大小写折叠 → 去全部空白。"""
    value = unicodedata.normalize("NFKC", str(text or ""))
    value = value.translate(_PUNCT_TRANSLATION).casefold()
    return re.sub(r"\s+", "", value)


def _is_anchor_token(token: str) -> bool:
    """单个 token 是否属于确定性锚点：日期 / 时间 / 含字母与数字的代码 / 纯数字。"""
    if _DATE_RE.fullmatch(token) or _TIME_RE.fullmatch(token):
        return True
    if any(char.isalpha() for char in token) and any(char.isdigit() for char in token):
        return True
    return bool(_NUMBER_RE.fullmatch(token))


def fact_anchors(text: object) -> tuple[str, ...]:
    """提取文本中的**确定性锚点**（顺序稳定、按出现位置去重）。

    锚点 = 数字（含小数）/ 日期 / 时间 / 同时含字母与数字的代码类 token。
    返回的是规范化后的锚点值，**仅供进程内匹配**，调用方不得写入报告。
    """
    normalized = _normalize_for_match(text)
    anchors: list[str] = []
    for match in _ANCHOR_TOKEN_RE.finditer(normalized):
        token = match.group()
        if not _is_anchor_token(token):
            continue
        if token not in anchors:
            anchors.append(token)
    return tuple(anchors)


def fact_anchor_diagnostics(
    facts: Sequence[object], answer: object, cited_quotes: Sequence[object]
) -> dict:
    """答案事实锚点安全诊断（纯函数，无 I/O、无 Provider 调用）。

    输入都是调用方**已在内存中**的数据（``expected_answer_facts`` / 答案原文 / 已引用 quote）。
    输出只含布尔与计数：

    - ``facts``：逐事实 ``{ordinal, answer_exact_match, evidence_exact_match}``
      （整条事实规范化后是否为答案 / 任一引用 quote 的子串）；
    - ``anchor_count`` / ``answer_anchor_match_count`` / ``evidence_anchor_match_count``：
      锚点总数与在答案 / 证据中命中的数量；
    - ``all_answer_anchors_matched`` / ``all_evidence_anchors_matched``：是否**全部**锚点命中
      （无锚点时为 ``False``，不虚报通过）；
    - ``all_*_anchors_matched_fact_count``：锚点全部命中答案 / 证据的**事实数**。

    **绝不输出** fact、answer、quote、锚点值、文件名或路径，也不保存其任何摘要。
    """
    normalized_answer = _normalize_for_match(answer)
    normalized_quotes = tuple(_normalize_for_match(quote) for quote in (cited_quotes or ()))

    fact_rows: list[dict] = []
    anchor_count = 0
    answer_matched = 0
    evidence_matched = 0
    answer_all_facts = 0
    evidence_all_facts = 0

    for ordinal, fact in enumerate(facts or (), start=1):
        normalized_fact = _normalize_for_match(fact)
        fact_rows.append(
            {
                "ordinal": ordinal,
                "answer_exact_match": bool(normalized_fact)
                and normalized_fact in normalized_answer,
                "evidence_exact_match": bool(normalized_fact)
                and any(normalized_fact in quote for quote in normalized_quotes),
            }
        )
        anchors = fact_anchors(fact)
        if not anchors:
            continue
        anchor_count += len(anchors)
        answer_hits = sum(1 for anchor in anchors if anchor in normalized_answer)
        evidence_hits = sum(
            1 for anchor in anchors if any(anchor in quote for quote in normalized_quotes)
        )
        answer_matched += answer_hits
        evidence_matched += evidence_hits
        if answer_hits == len(anchors):
            answer_all_facts += 1
        if evidence_hits == len(anchors):
            evidence_all_facts += 1

    return {
        "facts": tuple(fact_rows),
        "anchor_count": anchor_count,
        "answer_anchor_match_count": answer_matched,
        "evidence_anchor_match_count": evidence_matched,
        "all_answer_anchors_matched": anchor_count > 0 and answer_matched == anchor_count,
        "all_evidence_anchors_matched": anchor_count > 0
        and evidence_matched == anchor_count,
        "all_answer_anchors_matched_fact_count": answer_all_facts,
        "all_evidence_anchors_matched_fact_count": evidence_all_facts,
    }


__all__ = [
    "SOURCE_DIGEST_LENGTH",
    "SOURCE_DIGEST_PREFIX",
    "citation_coverage",
    "composition_summary",
    "fact_anchor_diagnostics",
    "fact_anchors",
    "group_coverage",
    "judge_fact_diagnostics",
    "locator_count_summary",
    "locator_diagnostics",
    "locator_digest",
    "locator_retrieval_coverage",
    "retrieval_coverage",
    "source_count_summary",
    "source_digest",
    "stage_composition",
    "staged_locator_coverage",
]
