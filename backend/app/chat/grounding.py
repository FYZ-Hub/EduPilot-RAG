"""受证据约束的生成契约（``GroundedCompletion``）与严格校验。

模型**只能**引用服务端给出的证据编号；``chunk_id`` / ``doc_id`` / ``file_name`` /
``page_number`` / ``sheet_name`` / 行范围 / ``section_title`` / ``quote`` 等引用字段
一律由服务端从原始 ``RerankedResult`` 构造，模型返回的同类字段一律忽略。

任何未知、越界、重复、缺失或与正文不一致的引用都判为 ``MODEL_RESPONSE_INVALID``，
并且在**发送任何 token 之前**完成全部校验。

``outcome`` 与 ``reason_code`` 严格绑定：``answered``→``null``、``refused``→
``insufficient_evidence``、``conflict``→``version_conflict``；四种服务端确定性早退
reason（``no_evidence`` / ``below_score_threshold`` / ``score_unavailable`` /
``planning_unavailable``）**不允许**出现在模型输出中——服务端在调用回答 LLM 前已保证
「结果非空、分数可用且超过阈值」。不符合绑定的一律归为稳定白名单 reason
``reason_code_outcome_mismatch``，绝不回显模型返回的原文。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app import constants
from app.core.errors import MODEL_RESPONSE_INVALID, ApiError

_MARKER = re.compile(r"\[(\d{1,3})\]")

# ``MODEL_RESPONSE_INVALID`` 的全部稳定细分原因白名单（唯一权威来源）。
# - 这是**唯一**允许进入 SSE error ``reason`` 与评测安全字段 ``error_reason`` 的取值集合；
# - 任何不在此集合内的值都不得外发，评测侧一律归一为 ``unknown``/``null``；
# - 校验规则本身不因本白名单而放宽或收紧（仅用于安全外发与诊断归一）。
GROUNDING_REASONS = (
    "not_json",
    "not_object",
    "bad_outcome",
    "bad_reason_code",
    "conflict_without_server_evidence",
    "reason_code_outcome_mismatch",
    "empty_answer",
    "answer_too_long",
    "bad_citation_list",
    "citation_out_of_range",
    "duplicate_citation",
    "refused_with_citations",
    "missing_citation",
    "missing_required_citation",
    "citation_marker_mismatch",
)

# 模型侧 outcome ↔ reason_code 的**严格绑定**：
# - 服务端在调用回答 LLM 前已保证「结果非空、分数可用且超过阈值」，
#   因此 no_evidence / below_score_threshold / score_unavailable / planning_unavailable
#   这四种**服务端确定性早退** reason 不允许出现在模型输出中；
# - answered 必须 null、refused 必须 insufficient_evidence、conflict 必须 version_conflict；
# - 任何不符合的取值都归为稳定白名单 reason ``reason_code_outcome_mismatch``（绝不回显正文）。
_MODEL_REASON_BY_OUTCOME = {
    constants.CHAT_OUTCOME_ANSWERED: None,
    constants.CHAT_OUTCOME_REFUSED: constants.REASON_MODEL_DECLINED,
    constants.CHAT_OUTCOME_CONFLICT: constants.REASON_VERSION_CONFLICT,
}


@dataclass(frozen=True)
class GroundedCompletion:
    """服务端最终采用的受证据约束结果。"""

    outcome: str
    reason_code: str | None
    answer: str
    citation_indices: tuple[int, ...]


def _invalid(reason: str) -> ApiError:
    return ApiError(MODEL_RESPONSE_INVALID, details={"reason": reason})


def _strip_code_fence(raw: str) -> str:
    text = (raw or "").strip()
    if not text.startswith("```"):
        return text
    text = text[3:]
    if text[:4].lower() == "json":
        text = text[4:]
    text = text.strip()
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def parse_grounded_completion(
    raw: str,
    *,
    evidence_count: int,
    answer_max_chars: int,
    required_indices: tuple[int, ...] = (),
) -> GroundedCompletion:
    """解析并完整校验模型输出；任何不合规都抛出 ``MODEL_RESPONSE_INVALID``。"""
    try:
        payload = json.loads(_strip_code_fence(raw))
    except Exception as error:  # noqa: BLE001
        raise _invalid("not_json") from error
    if not isinstance(payload, dict):
        raise _invalid("not_object")

    outcome = payload.get("outcome")
    if outcome not in constants.CHAT_OUTCOMES:
        raise _invalid("bad_outcome")

    reason_code = payload.get("reason_code")
    if reason_code is not None and reason_code not in constants.CHAT_REASON_CODES:
        raise _invalid("bad_reason_code")

    # 冲突判定权属于服务端：没有服务端冲突证据时，模型不得自行声明 conflict
    if outcome == constants.CHAT_OUTCOME_CONFLICT and not required_indices:
        raise _invalid("conflict_without_server_evidence")

    # outcome 与 reason_code 的严格绑定（answered→null / refused→insufficient_evidence /
    # conflict→version_conflict）；不符合时用稳定白名单 reason，绝不回显模型正文
    if reason_code != _MODEL_REASON_BY_OUTCOME[outcome]:
        raise _invalid("reason_code_outcome_mismatch")

    answer = payload.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        raise _invalid("empty_answer")
    if len(answer) > answer_max_chars:
        raise _invalid("answer_too_long")

    raw_indices = payload.get("citation_indices")
    if not isinstance(raw_indices, list):
        raise _invalid("bad_citation_list")
    indices: list[int] = []
    for value in raw_indices:
        number = _integer(value)
        if number is None or not (1 <= number <= evidence_count):
            raise _invalid("citation_out_of_range")
        if number in indices:
            raise _invalid("duplicate_citation")
        indices.append(number)

    if outcome == constants.CHAT_OUTCOME_REFUSED:
        if indices:
            raise _invalid("refused_with_citations")
    elif not indices:
        raise _invalid("missing_citation")

    for required in required_indices:
        if required not in indices:
            raise _invalid("missing_required_citation")

    markers = {int(match.group(1)) for match in _MARKER.finditer(answer)}
    if markers != set(indices):
        raise _invalid("citation_marker_mismatch")

    return GroundedCompletion(
        outcome=outcome,
        reason_code=reason_code,
        answer=answer,
        citation_indices=tuple(indices),
    )


def remap_citations(completion: GroundedCompletion) -> tuple[str, tuple[int, ...]]:
    """把引用的证据编号重映射为从 1 开始的连续编号，并同步重写正文标记。

    返回 ``(重写后的正文, 按新引用顺序排列的**原始**证据编号)``：
    调用方用原始编号回查检索结果拿到引用字段，用位置（1..k）作为对外的
    ``citation_index``，从而保证「模型引用的编号」与「最终引用列表」严格对齐。
    """
    order = list(completion.citation_indices)
    mapping = {old: new for new, old in enumerate(order, start=1)}

    def _replace(match: re.Match[str]) -> str:
        old = int(match.group(1))
        return f"[{mapping[old]}]" if old in mapping else match.group(0)

    return _MARKER.sub(_replace, completion.answer), tuple(order)


__all__ = [
    "GROUNDING_REASONS",
    "GroundedCompletion",
    "parse_grounded_completion",
    "remap_citations",
]
