"""阶段 6：问答编排测试（正常回答 / 引用对齐 / 拒答 / 冲突 / 改写 / 注入 / 隐私）。

检索结果通过替换 ``ChatStreamRunner._retrieve`` 精确控制，从而逐条验证
引用映射、拒答与冲突判定；真实语料端到端检查在别处用隔离 Fake 环境完成。
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re

import pytest

from app import constants
from app.chat.service import ChatRuntime, ChatStreamRunner, ChatTurn
from app.chat.sse import CITATION_FIELDS
from app.core.errors import MODEL_RESPONSE_INVALID
from app.llm.base import LLMDescriptor, LLMProvider
from app.llm.prompts import REWRITE_SYSTEM_PROMPT, SYSTEM_PROMPT
from app.search.types import RetrievalFilters
from tests.conftest import build_settings, parse_sse

_EVIDENCE_LINE = re.compile(r"^\[(\d+)\]", re.MULTILINE)


class _StubLLM(LLMProvider):
    """可编程 LLM：按 system 提示词区分改写与生成，并记录全部外发文本。"""

    def __init__(self, *, answer: str = "", rewrite: str = ""):
        self.answer = answer
        self.rewrite_text = rewrite
        self.calls: list[tuple[str, str]] = []
        self.descriptor = LLMDescriptor(
            provider="fake",
            model="stub-llm",
            revision="1",
            implementation_version="1",
            prompt_version="stub",
            response_schema_version="stub",
        )

    @property
    def loaded(self) -> bool:
        return True

    async def complete(self, *, system: str, user: str) -> str:
        self.calls.append((system, user))
        if system == REWRITE_SYSTEM_PROMPT:
            return self.rewrite_text
        return self.answer

    @property
    def generation_calls(self) -> int:
        return sum(1 for system, _ in self.calls if system == SYSTEM_PROMPT)


class _EchoLLM(_StubLLM):
    """引用传入的全部证据编号；用于冲突场景（outcome 由服务端判定）。"""

    async def complete(self, *, system: str, user: str) -> str:
        self.calls.append((system, user))
        if system == REWRITE_SYSTEM_PROMPT:
            return self.rewrite_text
        indices = [int(value) for value in _EVIDENCE_LINE.findall(user)]
        answer = "".join(f"[{index}] " for index in indices).strip() or "无证据"
        return _completion("answered", answer, indices)


def _completion(outcome: str, answer: str, indices: list[int], reason=None) -> str:
    return json.dumps(
        {
            "outcome": outcome,
            "reason_code": reason,
            "answer": answer,
            "citation_indices": indices,
        },
        ensure_ascii=False,
    )


async def _collect(runner: ChatStreamRunner) -> list[bytes]:
    return [frame async for frame in runner.stream()]


def _run(runtime: ChatRuntime, turn: ChatTurn) -> tuple[list[bytes], ChatStreamRunner]:
    runner = ChatStreamRunner(runtime, turn)
    frames = asyncio.run(_collect(runner))
    return frames, runner


def _turn(question: str = "毕业总学分是多少？", **kwargs) -> ChatTurn:
    return ChatTurn(
        request_id="req-123",
        question=question,
        history=kwargs.pop("history", ()),
        filters=kwargs.pop("filters", RetrievalFilters()),
    )


def _plan_results(evidence):
    """取两个不同版本的培养方案 chunk，用于跨版本冲突。"""
    found: dict[str, object] = {}
    for query in ("毕业总学分", "专业必修 学分 培养方案", "培养方案 毕业总学分 专业必修"):
        for item in evidence(query, top_k=6):
            if "培养方案" not in (item.chunk.file_name or ""):
                continue
            found.setdefault(item.chunk.document_version or "", item)
        if len(found) >= 2:
            break
    ordered = [found[key] for key in sorted(found)]
    assert len(ordered) >= 2, f"未检索到两个版本的培养方案：{sorted(found)}"
    return ordered[:2]


def _schedule_results(evidence):
    """取到同时覆盖冲突双方（QM-GE101 / QM-CS201）的课表切片。"""
    items: list = []
    for query in (
        "课表 星期二 第3-4节 QM-GE101 大学写作",
        "课表 星期二 第3-4节 QM-CS201 数据结构",
        "课表 时间 教室 周次 课程名称",
    ):
        for item in evidence(query, top_k=6):
            if "课表" not in (item.chunk.file_name or ""):
                continue
            if all(item.chunk.chunk_id != other.chunk.chunk_id for other in items):
                items.append(item)
    texts = " ".join(item.chunk.text for item in items)
    assert "QM-GE101" in texts and "QM-CS201" in texts, "未取到冲突双方所在的课表切片"
    return items[:6]


# --- 正常回答与引用 ---------------------------------------------------------


def test_answered_stream_emits_tokens_citations_and_done(chat_runtime, evidence, monkeypatch) -> None:
    results = evidence("学分认定", top_k=3)[:1]
    monkeypatch.setattr(ChatStreamRunner, "_retrieve", lambda self, query: list(results))
    llm = _StubLLM(answer=_completion("answered", "依据资料 [1]。", [1]))
    runtime = dataclasses.replace(chat_runtime, llm=llm)

    frames, runner = _run(runtime, _turn())
    events = parse_sse(b"".join(frames))

    assert events[-1][0] == "done"
    done = events[-1][1]
    assert done["outcome"] == constants.CHAT_OUTCOME_ANSWERED
    assert done["reason_code"] is None
    assert done["request_id"] == "req-123"
    assert done["citation_count"] == 1

    citations = [payload for name, payload in events if name == "citation"]
    assert len(citations) == 1
    assert set(citations[0]) == set(CITATION_FIELDS)
    assert citations[0]["quote"] == results[0].chunk.text
    assert citations[0]["chunk_id"] == results[0].chunk.chunk_id
    assert citations[0]["doc_id"] == results[0].chunk.doc_id
    assert citations[0]["file_name"] == results[0].chunk.file_name
    assert runner.closed is True

    text = "".join(payload["text"] for name, payload in events if name == "token")
    assert text == "依据资料 [1]。"
    assert llm.generation_calls == 1


def test_citations_stay_aligned_when_rerank_order_is_reversed(chat_runtime, evidence, monkeypatch) -> None:
    results = list(evidence("学分认定", top_k=3)[:2])
    reversed_results = list(reversed(results))
    monkeypatch.setattr(ChatStreamRunner, "_retrieve", lambda self, query: list(reversed_results))
    llm = _StubLLM(answer=_completion("answered", "见 [1] 与 [2]。", [1, 2]))
    runtime = dataclasses.replace(chat_runtime, llm=llm)

    frames, _runner = _run(runtime, _turn())
    events = parse_sse(b"".join(frames))
    citations = [payload for name, payload in events if name == "citation"]

    assert [item["chunk_id"] for item in citations] == [
        item.chunk.chunk_id for item in reversed_results
    ]
    for payload, result in zip(citations, reversed_results):
        assert payload["quote"] == result.chunk.text


def test_non_contiguous_citations_are_remapped_and_markers_rewritten(
    chat_runtime, evidence, monkeypatch
) -> None:
    results = list(evidence("学分认定", top_k=4)[:3])
    monkeypatch.setattr(ChatStreamRunner, "_retrieve", lambda self, query: list(results))
    llm = _StubLLM(answer=_completion("answered", "甲 [1] 乙 [3]。", [1, 3]))
    runtime = dataclasses.replace(chat_runtime, llm=llm)

    frames, _runner = _run(runtime, _turn())
    events = parse_sse(b"".join(frames))

    citations = [payload for name, payload in events if name == "citation"]
    assert [item["citation_index"] for item in citations] == [1, 2]
    assert citations[0]["chunk_id"] == results[0].chunk.chunk_id
    assert citations[1]["chunk_id"] == results[2].chunk.chunk_id
    text = "".join(payload["text"] for name, payload in events if name == "token")
    assert text == "甲 [1] 乙 [2]。"


# --- 拒答 -------------------------------------------------------------------


def test_zero_results_refuse_without_calling_the_llm(chat_runtime, monkeypatch) -> None:
    monkeypatch.setattr(ChatStreamRunner, "_retrieve", lambda self, query: [])
    llm = _StubLLM(answer=_completion("answered", "不应被调用 [1]。", [1]))
    runtime = dataclasses.replace(chat_runtime, llm=llm)

    frames, _runner = _run(runtime, _turn())
    events = parse_sse(b"".join(frames))

    assert events[-1][0] == "done"
    assert events[-1][1]["outcome"] == constants.CHAT_OUTCOME_REFUSED
    assert events[-1][1]["reason_code"] == constants.REASON_NO_EVIDENCE
    assert events[-1][1]["citation_count"] == 0
    assert not [name for name, _ in events if name == "citation"]
    assert llm.calls == []


def test_explicit_threshold_refuses_low_scores_without_calling_the_llm(
    chat_runtime, evidence, monkeypatch, tmp_path
) -> None:
    results = evidence("学分认定", top_k=3)[:2]
    monkeypatch.setattr(ChatStreamRunner, "_retrieve", lambda self, query: list(results))
    llm = _StubLLM(answer=_completion("answered", "不应被调用 [1]。", [1]))
    settings = build_settings(tmp_path, retrieval_score_threshold=0.999999)
    runtime = dataclasses.replace(chat_runtime, settings=settings, llm=llm)

    frames, _runner = _run(runtime, _turn())
    events = parse_sse(b"".join(frames))
    assert events[-1][1]["outcome"] == constants.CHAT_OUTCOME_REFUSED
    assert events[-1][1]["reason_code"] == constants.REASON_BELOW_THRESHOLD
    assert events[-1][1]["citation_count"] == 0
    assert llm.calls == []


def test_threshold_with_degraded_reranker_refuses_without_score_substitution(
    chat_runtime, evidence, monkeypatch, tmp_path
) -> None:
    results = list(evidence("学分认定", top_k=3)[:1])
    degraded = [dataclasses.replace(item, rerank_applied=False, rerank_score=None) for item in results]
    monkeypatch.setattr(ChatStreamRunner, "_retrieve", lambda self, query: list(degraded))
    llm = _StubLLM(answer=_completion("answered", "不应被调用 [1]。", [1]))
    settings = build_settings(tmp_path, retrieval_score_threshold=0.5)
    runtime = dataclasses.replace(chat_runtime, settings=settings, llm=llm)

    frames, _runner = _run(runtime, _turn())
    events = parse_sse(b"".join(frames))
    assert events[-1][1]["reason_code"] == constants.REASON_SCORE_UNAVAILABLE
    assert llm.calls == []


def test_planning_questions_are_refused_with_a_stable_reason(chat_runtime, evidence, monkeypatch) -> None:
    results = evidence("学分认定", top_k=3)[:1]
    monkeypatch.setattr(ChatStreamRunner, "_retrieve", lambda self, query: list(results))
    llm = _StubLLM(answer=_completion("answered", "不应被调用 [1]。", [1]))
    runtime = dataclasses.replace(chat_runtime, llm=llm)

    frames, _runner = _run(runtime, _turn("我还差多少学分才能毕业？"))
    events = parse_sse(b"".join(frames))
    assert events[-1][1]["outcome"] == constants.CHAT_OUTCOME_REFUSED
    assert events[-1][1]["reason_code"] == constants.REASON_PLANNING_UNAVAILABLE
    assert llm.calls == []


# --- 冲突 -------------------------------------------------------------------


def test_cross_version_conflict_reports_both_versions(chat_runtime, evidence, monkeypatch) -> None:
    results = _plan_results(evidence)
    monkeypatch.setattr(ChatStreamRunner, "_retrieve", lambda self, query: list(results))
    llm = _StubLLM(
        answer=_completion("answered", "见 [1] 与 [2]。", [1, 2], reason=None)
    )
    runtime = dataclasses.replace(chat_runtime, llm=llm)

    frames, _runner = _run(runtime, _turn())
    events = parse_sse(b"".join(frames))
    citations = [payload for name, payload in events if name == "citation"]

    assert events[-1][1]["outcome"] == constants.CHAT_OUTCOME_CONFLICT
    assert events[-1][1]["reason_code"] == constants.REASON_VERSION_CONFLICT
    assert len(citations) >= 2
    versions = {payload["document_version"] for payload in citations}
    assert len(versions) >= 2, "冲突必须并列展示双方版本"
    assert all(payload["effective_from"] for payload in citations)
    # 冲突提示必须写入提示词，要求并列展示且不得替用户选择版本
    assert "冲突" in llm.calls[-1][1]
    assert "不得替用户选择" in llm.calls[-1][1]


def test_schedule_conflict_is_detected(chat_runtime, evidence, monkeypatch) -> None:
    results = _schedule_results(evidence)
    monkeypatch.setattr(ChatStreamRunner, "_retrieve", lambda self, query: list(results))
    llm = _EchoLLM()
    runtime = dataclasses.replace(chat_runtime, llm=llm)

    frames, _runner = _run(runtime, _turn("课表中星期二第3-4节有哪些课程？"))
    events = parse_sse(b"".join(frames))

    assert events[-1][1]["outcome"] == constants.CHAT_OUTCOME_CONFLICT
    assert events[-1][1]["reason_code"] == constants.REASON_VERSION_CONFLICT
    citations = [payload for name, payload in events if name == "citation"]
    assert citations
    quoted = " ".join(payload["quote"] for payload in citations)
    assert "QM-GE101" in quoted and "QM-CS201" in quoted, "必须并列两项矛盾安排"
    assert "不得替用户选择" in llm.calls[-1][1]


# --- 引用压力与非法结构 -----------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        _completion("answered", "见 [0]。", [0]),
        _completion("answered", "见 [99]。", [99]),
        _completion("answered", "见 [1] 与 [1]。", [1, 1]),
        _completion("answered", "见 [1]。", []),
        _completion("answered", "见 [2]。", [1]),
        _completion("answered", "没有任何标记。", [1]),
        _completion("nonsense", "见 [1]。", [1]),
        _completion("answered", "", [1]),
        "不是 JSON",
        json.dumps({"outcome": "refused", "reason_code": "made_up", "answer": "x", "citation_indices": []}),
    ],
)
def test_invalid_model_structures_become_a_single_error(
    chat_runtime, evidence, monkeypatch, payload
) -> None:
    results = list(evidence("学分认定", top_k=2)[:2])
    monkeypatch.setattr(ChatStreamRunner, "_retrieve", lambda self, query: list(results))
    llm = _StubLLM(answer=payload)
    runtime = dataclasses.replace(chat_runtime, llm=llm)

    frames, _runner = _run(runtime, _turn())
    events = parse_sse(b"".join(frames))

    assert len(events) == 1
    assert events[0][0] == "error"
    assert events[0][1]["code"] == MODEL_RESPONSE_INVALID
    assert events[0][1]["request_id"] == "req-123"
    assert not [name for name, _ in events if name == "token"], "非法结构不得先流 token"


# --- 多轮改写 ---------------------------------------------------------------


def test_multi_turn_rewrites_query_and_never_adds_filters(chat_runtime, evidence, monkeypatch) -> None:
    captured: dict[str, object] = {}
    results = evidence("学分认定", top_k=2)[:1]

    def _fake_retrieve(self, query):
        captured["query"] = query
        return list(results)

    monkeypatch.setattr(ChatStreamRunner, "_retrieve", _fake_retrieve)
    llm = _StubLLM(
        answer=_completion("answered", "见 [1]。", [1]),
        rewrite="计算机科学与技术专业毕业总学分是多少",
    )
    runtime = dataclasses.replace(chat_runtime, llm=llm)
    turn = ChatTurn(
        request_id="req-9",
        question="那专业必修呢？",
        history=(("user", "计算机科学与技术专业毕业总学分是多少？"), ("assistant", "见方案。")),
        filters=RetrievalFilters(doc_category="degree_plan"),
    )

    frames, _runner = _run(runtime, turn)
    events = parse_sse(b"".join(frames))

    assert captured["query"] == "计算机科学与技术专业毕业总学分是多少"
    rewrite_calls = [user for system, user in llm.calls if system == REWRITE_SYSTEM_PROMPT]
    assert len(rewrite_calls) == 1
    assert "计算机科学与技术专业毕业总学分是多少？" in rewrite_calls[0]
    assert events[-1][1]["outcome"] == constants.CHAT_OUTCOME_ANSWERED


def test_single_turn_skips_rewrite(chat_runtime, evidence, monkeypatch) -> None:
    captured: dict[str, object] = {}
    results = evidence("学分认定", top_k=2)[:1]

    def _fake_retrieve(self, query):
        captured["query"] = query
        return list(results)

    monkeypatch.setattr(ChatStreamRunner, "_retrieve", _fake_retrieve)
    llm = _StubLLM(answer=_completion("answered", "见 [1]。", [1]), rewrite="不应使用")
    runtime = dataclasses.replace(chat_runtime, llm=llm)

    _run(runtime, _turn("学分认定上限是多少？"))
    assert captured["query"] == "学分认定上限是多少？"
    assert not [system for system, _ in llm.calls if system == REWRITE_SYSTEM_PROMPT]


def test_failed_rewrite_ends_with_a_stable_error(chat_runtime, evidence, monkeypatch) -> None:
    results = evidence("学分认定", top_k=2)[:1]
    monkeypatch.setattr(ChatStreamRunner, "_retrieve", lambda self, query: list(results))
    llm = _StubLLM(answer=_completion("answered", "见 [1]。", [1]), rewrite="   ")
    runtime = dataclasses.replace(chat_runtime, llm=llm)
    turn = ChatTurn(
        request_id="req-7",
        question="那专业必修呢？",
        history=(("user", "毕业总学分是多少？"),),
        filters=RetrievalFilters(),
    )

    frames, _runner = _run(runtime, turn)
    events = parse_sse(b"".join(frames))
    assert len(events) == 1
    assert events[0][0] == "error"
    assert events[0][1]["code"] == "CHAT_QUERY_REWRITE_FAILED"
    assert llm.generation_calls == 0
