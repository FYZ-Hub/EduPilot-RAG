"""FakeLLMProvider：完全确定性、完全离线的 LLM 替身（仅测试与显式开发测试模式）。

约束：

- 同一输入在同一进程、跨进程、任意 ``PYTHONHASHSEED`` 下输出完全一致；
- 绝不使用 ``hash()``、随机数、时间或集合迭代顺序（集合迭代受哈希种子影响）；
- 不访问网络、不读取模型权重、不读取 ``demo/ground_truth.jsonl``；
- 不针对任何具体演示问题硬编码答案：回答文本完全由**本次传入的证据文本**派生；
- ``production`` / ``prod`` 由工厂拒绝。
"""

from __future__ import annotations

import json
import re

from app import constants
from app.llm.base import (
    FAKE_LLM_MODEL_NAME,
    FAKE_LLM_REVISION,
    LLM_IMPLEMENTATION_VERSION,
    PROMPT_VERSION,
    RESPONSE_SCHEMA_VERSION,
    LLMDescriptor,
    LLMProvider,
)
from app.llm.prompts import (
    CONFLICT_MARKER,
    EVIDENCE_MARKER,
    QUESTION_MARKER,
    REWRITE_HISTORY_MARKER,
    REWRITE_QUESTION_MARKER,
    REWRITE_SYSTEM_PROMPT,
)

_EVIDENCE_LINE = re.compile(r"^\[(\d+)\]\s?(.*)$")
_EXCERPT_CHARS = 40
_MAX_CITATIONS = 3
_REWRITE_MAX_CHARS = 200


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _section_after(user: str, marker: str) -> str:
    """取 ``marker`` 之后、下一个 ``#`` 标记之前的整段文本。"""
    lines = user.splitlines()
    try:
        start = lines.index(marker)
    except ValueError:
        return ""
    collected: list[str] = []
    for line in lines[start + 1 :]:
        if line.strip().startswith("#"):
            break
        collected.append(line)
    return "\n".join(collected).strip()


def _parse_question(user: str) -> str:
    return _section_after(user, QUESTION_MARKER)


def _parse_evidence(user: str) -> list[tuple[int, str]]:
    """按出现顺序解析 ``[n] text`` 证据行（顺序稳定，与哈希种子无关）。"""
    inside = False
    items: list[tuple[int, str]] = []
    for line in user.splitlines():
        stripped = line.strip()
        if stripped == EVIDENCE_MARKER:
            inside = True
            continue
        if inside and stripped.startswith("#"):
            break
        if not inside:
            continue
        match = _EVIDENCE_LINE.match(stripped)
        if match:
            items.append((int(match.group(1)), match.group(2).strip()))
    items.sort(key=lambda item: item[0])
    return items


class FakeLLMProvider(LLMProvider):
    """确定性文本替身：回答只能由本次证据文本派生。"""

    def __init__(self) -> None:
        self.descriptor = LLMDescriptor(
            provider=constants.LLM_PROVIDER_FAKE,
            model=FAKE_LLM_MODEL_NAME,
            revision=FAKE_LLM_REVISION,
            implementation_version=LLM_IMPLEMENTATION_VERSION,
            prompt_version=PROMPT_VERSION,
            response_schema_version=RESPONSE_SCHEMA_VERSION,
        )

    @property
    def loaded(self) -> bool:
        """Fake 离线可用，因此真实 ready 为 True。"""
        return True

    @property
    def configured(self) -> bool:
        return True

    async def rewrite(self, *, system: str, user: str) -> str:
        """把「上一轮用户问题 + 当前问题」拼成自洽查询；无历史时原样返回。"""
        question = _collapse(_section_after(user, REWRITE_QUESTION_MARKER))
        if not question:
            return ""
        history = _section_after(user, REWRITE_HISTORY_MARKER)
        previous = ""
        for line in history.splitlines():
            if line.startswith("user: "):
                previous = line[len("user: ") :].strip()
        if not previous or previous in question:
            return question[:_REWRITE_MAX_CHARS]
        return f"{previous}｜{question}"[:_REWRITE_MAX_CHARS]

    async def complete(self, *, system: str, user: str) -> str:
        if system == REWRITE_SYSTEM_PROMPT:
            return await self.rewrite(system=system, user=user)
        return self._grounded_json(user)

    def _grounded_json(self, user: str) -> str:
        evidence = _parse_evidence(user)
        is_conflict = CONFLICT_MARKER in user

        if not evidence:
            payload = {
                "outcome": constants.CHAT_OUTCOME_REFUSED,
                "reason_code": constants.REASON_NO_EVIDENCE,
                "answer": "没有可用的证据。",
                "citation_indices": [],
            }
            return json.dumps(payload, ensure_ascii=False)

        indices = [index for index, _text in evidence]
        if is_conflict:
            payload = {
                "outcome": constants.CHAT_OUTCOME_CONFLICT,
                "reason_code": constants.REASON_VERSION_CONFLICT,
                "answer": self._compose(
                    "检索到的资料存在版本或安排冲突，不替你选择版本：", evidence, indices
                ),
                "citation_indices": indices,
            }
        else:
            chosen = indices[:_MAX_CITATIONS]
            selected = [(index, text) for index, text in evidence if index in chosen]
            payload = {
                "outcome": constants.CHAT_OUTCOME_ANSWERED,
                "reason_code": None,
                "answer": self._compose("依据检索到的资料：", selected, chosen),
                "citation_indices": chosen,
            }
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _compose(prefix: str, evidence: list[tuple[int, str]], indices: list[int]) -> str:
        by_index = {index: text for index, text in evidence}
        parts = [
            f"[{index}] {_collapse(by_index.get(index, ''))[:_EXCERPT_CHARS]}"
            for index in sorted(indices)
        ]
        return prefix + "；".join(parts) + "。"


__all__ = ["FakeLLMProvider"]
