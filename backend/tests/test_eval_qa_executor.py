"""阶段 9B executor 离线接线测试：SSE 解析、可注入 Judge 与正式判定接线（全程无 HTTP）。"""

from __future__ import annotations

import json

from app.llm.prompts import REFUSAL_TEXT_BY_REASON, SYSTEM_PROMPT

from eval_tools.qa.executor import (
    ChatOutcome,
    REFUSAL_REASONS,
    decide_citation,
    decide_injection,
    decide_refusal,
    parse_chat_sse,
)
from eval_tools.qa import executor as _executor_module
from eval_tools.qa.diagnostics import locator_digest, source_digest
from eval_tools.qa.judge import (
    JUDGE_CONTRACT_VERSION,
    JudgeContractError,
    JudgeFactResult,
    JudgeVerdict,
)
from eval_tools.qa.providers import JudgeTransportError
from eval_tools.qa.metrics import (
    GROUP_REFUSAL_CONTRACT,
    JUDGE_STATUS_INVALID,
    JUDGE_STATUS_NOT_CONFIGURED,
    JUDGE_STATUS_OK,
    JUDGE_STATUS_SKIPPED,
    JUDGE_STATUS_UNAVAILABLE,
    STAGE_CITATION_CONTRACT_FAILURE,
    STAGE_CITATION_FACT_UNSUPPORTED,
    STAGE_CITATION_JUDGE_INVALID,
    STAGE_CITATION_JUDGE_UNAVAILABLE,
    STAGE_CITATION_LOCATOR_MISS,
    STAGE_CITATION_OUTCOME_MISMATCH,
    STAGE_CITATION_SOURCE_MISS,
    STAGE_INJECTION_BAD_OUTCOME,
    STAGE_INJECTION_CONTRACT,
    STAGE_INJECTION_FACT_UNSUPPORTED,
    STAGE_INJECTION_JUDGE_INVALID,
    STAGE_INJECTION_JUDGE_UNAVAILABLE,
    STAGE_INJECTION_LOCATOR_MISS,
    STAGE_INJECTION_SIDE_EFFECT,
    STAGE_INJECTION_SOURCE_MISS,
    STAGE_INJECTION_SYSTEM_PROMPT_LEAK,
    STAGE_INJECTION_TEXT_MISMATCH,
    STAGE_REFUSAL_TEXT_MISMATCH,
    STAGE_REFUSAL_WRONG_REASON,
    STRICT_REFUSAL_REASONS,
    QaCaseResult,
    aggregate_qa,
    contains_system_prompt_leak,
)

CITATION_CASE = {
    "id": "gt-synth-citation-001",
    "question": "合成问题：某虚构课程的学分为多少？",
    "expected_source_paths": ["demo/corpus/synth/a.pdf"],
    "expected_locators": [{"path": "demo/corpus/synth/a.pdf", "page_number": 1}],
    "expected_answer_facts": ["某虚构课程的学分为 3 学分。"],
    "conflict_expected": False,
}


def _sse(*events: tuple[str, dict]) -> str:
    return "\n\n".join(
        f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}"
        for event, payload in events
    )


def _citation_event(
    chunk_id: str = "chunk-1",
    file_name: str = "a.pdf",
    quote: str = "合成引用原文",
    page_number: int | None = 1,
) -> tuple[str, dict]:
    return (
        "citation",
        {
            "chunk_id": chunk_id,
            "file_name": file_name,
            "quote": quote,
            "page_number": page_number,
            "sheet_name": None,
            "row_start": None,
            "row_end": None,
            "section_title": "三、学分要求",
        },
    )


def _answered_outcome(**overrides) -> ChatOutcome:
    base = dict(
        outcome="answered",
        reason_code=None,
        citation_count=1,
        citation_chunk_ids=("chunk-1",),
        citation_file_names=("a.pdf",),
        answer="依据资料：[1] 学分为 3 学分。",
        error_code=None,
        done=True,
        citation_records=(
            {
                "chunk_id": "chunk-1",
                "file_name": "a.pdf",
                "quote": "合成引用原文",
                "locator": {
                    "page_number": 1,
                    "sheet_name": None,
                    "row_start": None,
                    "row_end": None,
                    "section_title": "三、学分要求",
                },
            },
        ),
    )
    base.update(overrides)
    return ChatOutcome(**base)


class FakeJudge:
    """内存 Fake Judge：不发 HTTP，记录调用次数与请求，可注入契约错误或异常。"""

    def __init__(self, *, verdict: JudgeVerdict | None = None, error: Exception | None = None):
        self.calls: list = []
        self._verdict = verdict
        self._error = error

    def __call__(self, request):
        self.calls.append(request)
        if self._error is not None:
            raise self._error
        if self._verdict is not None:
            return self._verdict
        return JudgeVerdict(
            contract_version=JUDGE_CONTRACT_VERSION,
            fact_results=(JudgeFactResult(1, True, True, (1,)),),
            verdict="supported",
        )


# --- SSE 解析 ---------------------------------------------------------------


def test_parse_chat_sse_records_done_and_full_citation() -> None:
    outcome = parse_chat_sse(
        _sse(
            ("token", {"text": "依据资料"}),
            _citation_event(),
            ("token", {"text": "：[1]"}),
            ("done", {"outcome": "answered", "citation_count": 1}),
        )
    )
    assert outcome.done is True
    assert outcome.error_code is None
    assert outcome.outcome == "answered"
    assert outcome.answer == "依据资料：[1]"
    assert outcome.citation_chunk_ids == ("chunk-1",)
    assert outcome.citation_file_names == ("a.pdf",)
    record = outcome.citation_records[0]
    assert record["file_name"] == "a.pdf"
    assert record["quote"] == "合成引用原文"
    assert record["locator"]["page_number"] == 1
    assert record["locator"]["section_title"] == "三、学分要求"


def test_parse_chat_sse_error_is_never_done() -> None:
    outcome = parse_chat_sse(
        _sse(
            _citation_event(),
            ("error", {"code": "MODEL_TIMEOUT", "reason": "超时"}),
        )
    )
    assert outcome.done is False
    assert outcome.error_code == "MODEL_TIMEOUT"
    assert outcome.outcome is None
    assert outcome.citation_count == 0


def test_parse_chat_sse_without_done_event_is_not_done() -> None:
    outcome = parse_chat_sse(_sse(("token", {"text": "半截回答"})))
    assert outcome.done is False
    assert outcome.outcome is None
    assert outcome.answer == "半截回答"


def test_parse_chat_sse_records_whitelisted_error_reason() -> None:
    outcome = parse_chat_sse(
        _sse(
            ("error", {"code": "MODEL_RESPONSE_INVALID", "reason": "citation_marker_mismatch"})
        )
    )
    assert outcome.error_code == "MODEL_RESPONSE_INVALID"
    assert outcome.error_reason == "citation_marker_mismatch"
    assert outcome.done is False


def test_parse_chat_sse_normalizes_illegal_error_reason() -> None:
    sentinel = "恶意正文 https://api.example.invalid/v1 sk-deadbeef"
    outcome = parse_chat_sse(_sse(("error", {"code": "MODEL_RESPONSE_INVALID", "reason": sentinel})))
    assert outcome.error_reason == "unknown"
    assert sentinel not in repr(outcome)
    assert "api.example.invalid" not in repr(outcome)
    assert "sk-" not in repr(outcome)


def test_parse_chat_sse_error_without_reason_has_none() -> None:
    outcome = parse_chat_sse(_sse(("error", {"code": "MODEL_TIMEOUT"})))
    assert outcome.error_code == "MODEL_TIMEOUT"
    assert outcome.error_reason is None


def test_chat_outcome_normalizes_error_reason_by_whitelist() -> None:
    base = dict(
        outcome=None,
        reason_code=None,
        citation_count=0,
        citation_chunk_ids=(),
        citation_file_names=(),
        answer="",
        error_code="MODEL_RESPONSE_INVALID",
    )
    assert ChatOutcome(**base, error_reason="bad_outcome").error_reason == "bad_outcome"
    assert ChatOutcome(**base, error_reason="made_up_label").error_reason == "unknown"
    assert ChatOutcome(**base, error_reason="").error_reason is None
    assert ChatOutcome(**base).error_reason is None


# --- 引用：offline_fake（无 Judge）行为不变 ---------------------------------


def test_decide_citation_without_judge_keeps_legacy_behaviour() -> None:
    decision = decide_citation(CITATION_CASE, _answered_outcome())
    assert decision.observed_pass is True
    assert decision.citation_contract_ok is True
    assert decision.citation_source_hit is True
    assert decision.failure_stage is None
    # 不产出任何正式判定
    assert decision.formal_pass is None
    assert decision.formal_stage is None
    assert decision.judge_status == JUDGE_STATUS_NOT_CONFIGURED


# --- 引用：Judge 调用门槛 ---------------------------------------------------


def test_decide_citation_calls_judge_only_after_all_preconditions() -> None:
    judge = FakeJudge()
    decision = decide_citation(CITATION_CASE, _answered_outcome(), judge=judge)
    assert decision.formal_pass is True
    assert decision.judge_status == JUDGE_STATUS_OK
    assert len(judge.calls) == 1
    request = judge.calls[0]
    assert request.question == CITATION_CASE["question"]
    assert request.facts == ("某虚构课程的学分为 3 学分。",)
    assert request.answer == _answered_outcome().answer
    assert request.evidence[0][0] == 1
    assert request.evidence[0][1] == "合成引用原文"
    assert request.evidence[0][2]["page_number"] == 1


# --- ground truth required/supporting 分层：supporting 绝不参与正式判定 -------


SUPPORTING_CASE = {
    **CITATION_CASE,
    "supporting_answer_facts": ["参考事实：不应进入 Judge"],
    "supporting_source_paths": ["demo/corpus/synth/other.pdf"],
    "supporting_locators": [{"path": "demo/corpus/synth/other.pdf", "page_number": 9}],
}


def _outcome_with_citation(file_name: str, page_number: int) -> ChatOutcome:
    record = {
        "chunk_id": "chunk-1",
        "file_name": file_name,
        "quote": "合成引用原文",
        "locator": {
            "page_number": page_number,
            "sheet_name": None,
            "row_start": None,
            "row_end": None,
            "section_title": "三、学分要求",
        },
    }
    return _answered_outcome(citation_file_names=(file_name,), citation_records=(record,))


def test_supporting_fields_are_never_sent_to_judge() -> None:
    judge = FakeJudge()
    decision = decide_citation(SUPPORTING_CASE, _answered_outcome(), judge=judge)
    assert decision.formal_pass is True
    assert len(judge.calls) == 1
    # 只发送 required 事实；supporting 事实永不进入 Judge
    assert judge.calls[0].facts == ("某虚构课程的学分为 3 学分。",)
    assert all("参考事实" not in fact for fact in judge.calls[0].facts)


def test_unsatisfied_supporting_source_and_locator_do_not_fail_d1_d2() -> None:
    judge = FakeJudge()
    decision = decide_citation(SUPPORTING_CASE, _answered_outcome(), judge=judge)
    assert decision.formal_pass is True
    assert decision.formal_stage is None
    assert decision.judge_status == JUDGE_STATUS_OK


def test_missing_required_source_still_fails_formal() -> None:
    judge = FakeJudge()
    decision = decide_citation(SUPPORTING_CASE, _outcome_with_citation("other.pdf", 1), judge=judge)
    assert judge.calls == []
    assert decision.formal_pass is False
    assert decision.formal_stage == STAGE_CITATION_SOURCE_MISS


def test_missing_required_locator_still_fails_formal() -> None:
    judge = FakeJudge()
    decision = decide_citation(CITATION_CASE, _outcome_with_citation("a.pdf", 7), judge=judge)
    assert judge.calls == []
    assert decision.formal_pass is False
    assert decision.formal_stage == STAGE_CITATION_LOCATOR_MISS


# --- citation_selection 安全诊断（schema 3.19） ------------------------------


def test_citation_selection_maps_remapped_ranks_to_digests() -> None:
    items = (
        ("a.pdf", {"page_number": 1}),
        ("b.pdf", {"page_number": 2}),
        ("c.pdf", {"page_number": 3}),
    )
    rows = _executor_module._citation_selection((3, 1), items)
    assert [row["citation_index"] for row in rows] == [1, 2]
    assert [row["final_rank"] for row in rows] == [3, 1]
    assert rows[0]["source_digest"] == source_digest("c.pdf")
    assert rows[0]["locator_digest"] == locator_digest("c.pdf", {"page_number": 3})
    assert rows[1]["source_digest"] == source_digest("a.pdf")
    assert rows[1]["locator_digest"] == locator_digest("a.pdf", {"page_number": 1})


def test_citation_selection_is_json_safe_and_leaks_nothing() -> None:
    secret = "13-课程记录-匿名学生A.xlsx"
    items = ((secret, {"sheet_name": "课程记录", "row_start": 3, "row_end": 12}),)
    rows = _executor_module._citation_selection((1,), items)
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == {"citation_index", "final_rank", "source_digest", "locator_digest"}
    assert row["citation_index"] == 1 and row["final_rank"] == 1
    assert isinstance(row["source_digest"], str) and len(row["source_digest"]) == 12
    assert isinstance(row["locator_digest"], str) and len(row["locator_digest"]) == 12

    blob = json.dumps(rows, ensure_ascii=False)
    for forbidden in (secret, "课程记录", ".xlsx", "匿名", "corpus/", "quote", "sk-"):
        assert forbidden not in blob


def test_citation_selection_skips_out_of_range_ranks() -> None:
    items = (("a.pdf", {"page_number": 1}),)
    assert _executor_module._citation_selection((0, 5, -1), items) == ()
    assert _executor_module._citation_selection((), items) == ()


def test_decide_citation_skips_judge_on_contract_failure() -> None:
    judge = FakeJudge()
    outcome = _answered_outcome(done=False, outcome=None, citation_count=0)
    decision = decide_citation(CITATION_CASE, outcome, judge=judge)
    assert judge.calls == []
    assert decision.judge_status == JUDGE_STATUS_SKIPPED
    assert decision.formal_stage == STAGE_CITATION_CONTRACT_FAILURE
    assert decision.formal_pass is False


def test_decide_citation_skips_judge_on_outcome_mismatch() -> None:
    judge = FakeJudge()
    decision = decide_citation(CITATION_CASE, _answered_outcome(outcome="conflict"), judge=judge)
    assert judge.calls == []
    assert decision.formal_stage == STAGE_CITATION_OUTCOME_MISMATCH


def test_decide_citation_skips_judge_on_source_miss() -> None:
    """期望来源整体改为 other.pdf（paths / locators / groups 三者一致），引用仍为
    a.pdf：D1 失败且不调用 Judge。"""
    judge = FakeJudge()
    other = {"path": "demo/corpus/synth/other.pdf", "page_number": 1}
    case = {
        **CITATION_CASE,
        "expected_source_paths": ["demo/corpus/synth/other.pdf"],
        "expected_locators": [dict(other)],
        "required_evidence_groups": [[dict(other)]],
    }
    decision = decide_citation(case, _answered_outcome(), judge=judge)
    assert judge.calls == []
    assert decision.d1_sources_ok is False
    assert decision.formal_stage == STAGE_CITATION_SOURCE_MISS


def test_decide_citation_skips_judge_on_locator_mismatch() -> None:
    """同一来源（b.pdf）但 locator 不同（期望第 2 页、引用第 1 页）：D1 通过、
    D2 失败，且不调用 Judge。"""
    judge = FakeJudge()
    outcome = _answered_outcome(
        citation_file_names=("b.pdf",),
        citation_records=(
            {
                "chunk_id": "chunk-1",
                "file_name": "b.pdf",
                "quote": "同一文件的另一页内容",
                "locator": {
                    "page_number": 1,
                    "sheet_name": None,
                    "row_start": None,
                    "row_end": None,
                    "section_title": "三、学分要求",
                },
            },
        ),
    )
    expected = {"path": "demo/corpus/synth/b.pdf", "page_number": 2}
    case = {
        **CITATION_CASE,
        "expected_source_paths": ["demo/corpus/synth/b.pdf"],
        "expected_locators": [dict(expected)],
        "required_evidence_groups": [[dict(expected)]],
    }
    decision = decide_citation(case, outcome, judge=judge)
    assert judge.calls == []
    assert decision.d1_sources_ok is True
    assert decision.d2_locators_ok is False
    assert decision.formal_stage == STAGE_CITATION_LOCATOR_MISS


def test_decide_citation_marks_unsupported_facts() -> None:
    judge = FakeJudge(
        verdict=JudgeVerdict(
            contract_version=JUDGE_CONTRACT_VERSION,
            fact_results=(JudgeFactResult(1, True, False, ()),),
            verdict="unsupported",
        )
    )
    decision = decide_citation(CITATION_CASE, _answered_outcome(), judge=judge)
    assert judge.calls != []
    assert decision.formal_pass is False
    assert decision.formal_stage == STAGE_CITATION_FACT_UNSUPPORTED
    assert decision.judge_status == JUDGE_STATUS_OK


# --- 引用：Judge 失败安全 ---------------------------------------------------


def test_decide_citation_maps_contract_error_to_invalid_without_leak() -> None:
    judge = FakeJudge(error=JudgeContractError("bad_bool"))
    decision = decide_citation(CITATION_CASE, _answered_outcome(), judge=judge)
    assert decision.judge_status == JUDGE_STATUS_INVALID
    assert decision.formal_stage == STAGE_CITATION_JUDGE_INVALID
    assert decision.formal_pass is False
    # 决策结构不携带答案正文
    assert _answered_outcome().answer not in str(decision)


def test_decide_citation_maps_transport_error_to_unavailable_without_leak() -> None:
    sentinel = "SENTINEL_TRANSPORT_https://api.example.invalid/key"
    judge = FakeJudge(error=RuntimeError(sentinel))
    decision = decide_citation(CITATION_CASE, _answered_outcome(), judge=judge)
    assert decision.judge_status == JUDGE_STATUS_UNAVAILABLE
    assert decision.formal_stage == STAGE_CITATION_JUDGE_UNAVAILABLE
    assert decision.formal_pass is False
    assert sentinel not in str(decision)
    assert "api.example.invalid" not in str(decision)


def test_decide_citation_records_contract_reason() -> None:
    """契约错误必须保存其白名单 reason，且不泄漏任何正文。"""
    judge = FakeJudge(error=JudgeContractError("bad_fact_results"))
    decision = decide_citation(CITATION_CASE, _answered_outcome(), judge=judge)
    assert decision.judge_status == JUDGE_STATUS_INVALID
    assert decision.judge_reason == "bad_fact_results"


def test_decide_citation_transport_reason_is_cleaned_class_name() -> None:
    """传输异常只保存已清洗的异常类名，恶意正文绝不进入 judge_reason。"""
    sentinel = "SENTINEL_JUDGE_https://api.example.invalid/secret sk-abcdef"
    judge = FakeJudge(error=RuntimeError(sentinel))
    decision = decide_citation(CITATION_CASE, _answered_outcome(), judge=judge)
    assert decision.judge_reason == "RuntimeError"
    assert sentinel not in str(decision)
    assert "sk-" not in str(decision)


def test_decide_citation_records_cleaned_transport_error_reason() -> None:
    """JudgeTransportError 自带清洗后的 reason（异常类名）应被原样保留。"""
    judge = FakeJudge(error=JudgeTransportError("ConnectionError"))
    decision = decide_citation(CITATION_CASE, _answered_outcome(), judge=judge)
    assert decision.judge_status == JUDGE_STATUS_UNAVAILABLE
    assert decision.judge_reason == "ConnectionError"


def test_decide_citation_without_judge_has_no_judge_reason() -> None:
    decision = decide_citation(CITATION_CASE, _answered_outcome())
    assert decision.judge_reason is None


# --- 拒答 -------------------------------------------------------------------


def _refusal_outcome(text: str, reason: str = "no_evidence", **overrides) -> ChatOutcome:
    base = dict(
        outcome="refused",
        reason_code=reason,
        citation_count=0,
        citation_chunk_ids=(),
        citation_file_names=(),
        answer=text,
        error_code=None,
        done=True,
        citation_records=(),
    )
    base.update(overrides)
    return ChatOutcome(**base)


def test_decide_refusal_uses_production_refusal_text() -> None:
    text = REFUSAL_TEXT_BY_REASON["no_evidence"]
    decision = decide_refusal({}, _refusal_outcome(text))
    assert decision.observed_pass is True
    assert decision.formal_pass is True
    assert decision.formal_stage is None


def test_decide_refusal_formal_fails_on_text_mismatch() -> None:
    decision = decide_refusal({}, _refusal_outcome("编造的答案：共 160 学分。"))
    assert decision.formal_pass is False
    assert decision.formal_stage == STAGE_REFUSAL_TEXT_MISMATCH


def test_decide_refusal_illegal_reason_is_stable_wrong_reason() -> None:
    """非法 reason 在观测与正式两侧都落到稳定的 refusal_wrong_reason。"""
    text = REFUSAL_TEXT_BY_REASON["score_unavailable"]
    decision = decide_refusal({}, _refusal_outcome(text, reason="score_unavailable"))
    assert decision.observed_pass is False
    assert decision.failure_stage == STAGE_REFUSAL_WRONG_REASON
    assert decision.formal_pass is False
    assert decision.formal_stage == STAGE_REFUSAL_WRONG_REASON


def test_refusal_reasons_are_derived_from_single_authoritative_source() -> None:
    """REFUSAL_REASONS 必须由 metrics.STRICT_REFUSAL_REASONS 派生，不另立口径。"""
    assert REFUSAL_REASONS == set(STRICT_REFUSAL_REASONS)
    assert set(STRICT_REFUSAL_REASONS) == {
        "no_evidence",
        "below_score_threshold",
        "insufficient_evidence",
    }


def test_decide_refusal_accepts_every_strict_reason_observed_and_formal() -> None:
    """三种 STRICT_REFUSAL_REASONS 在固定文案 / 零引用 / done+refused 下观测与正式均通过。"""
    for reason in STRICT_REFUSAL_REASONS:
        text = REFUSAL_TEXT_BY_REASON[reason]
        decision = decide_refusal({}, _refusal_outcome(text, reason=reason))
        assert decision.observed_pass is True, reason
        assert decision.failure_stage is None, reason
        assert decision.formal_pass is True, reason
        assert decision.formal_stage is None, reason


def test_v6_model_declined_refusals_restore_observed_rate() -> None:
    """v6 的 insufficient_evidence 属于观测白名单：5 例不再产生 refusal_wrong_reason，
    aggregate 的 refusal_observed_rate 恢复 1.0。"""
    text = REFUSAL_TEXT_BY_REASON["insufficient_evidence"]
    results = []
    for index in range(1, 6):
        decision = decide_refusal(
            {}, _refusal_outcome(text, reason="insufficient_evidence")
        )
        assert decision.observed_pass is True
        assert decision.failure_stage is None
        results.append(
            QaCaseResult(
                case_id=f"gt-refuse-{index:03d}",
                group=GROUP_REFUSAL_CONTRACT,
                question="q",
                observed_pass=decision.observed_pass,
                failure_stage=decision.failure_stage,
            )
        )
    metrics = aggregate_qa(results)
    assert metrics["refusal"]["refusal_observed_rate"] == 1.0
    assert "refusal_wrong_reason" not in metrics["failure_stage_counts"]


def test_decide_refusal_formal_fails_when_not_done() -> None:
    text = REFUSAL_TEXT_BY_REASON["no_evidence"]
    decision = decide_refusal({}, _refusal_outcome(text, done=False))
    assert decision.formal_pass is False


# --- 注入抵抗 ---------------------------------------------------------------


def test_decide_injection_passes_on_strict_refusal() -> None:
    text = REFUSAL_TEXT_BY_REASON["no_evidence"]
    decision = decide_injection({}, _refusal_outcome(text), side_effect_free=True)
    assert decision.observed_pass is True
    assert decision.formal_pass is True
    assert decision.formal_stage is None


def test_decide_injection_rejects_leaky_answer() -> None:
    """含系统提示泄漏的答案必须命中泄漏阶段，即使它不等于固定拒答文案。"""
    leaky = SYSTEM_PROMPT[:40]
    assert contains_system_prompt_leak(leaky, SYSTEM_PROMPT) is True
    decision = decide_injection({}, _refusal_outcome(leaky), side_effect_free=True)
    assert decision.formal_pass is False
    assert decision.formal_stage == STAGE_INJECTION_SYSTEM_PROMPT_LEAK


def test_decide_injection_rejects_plain_wrong_text() -> None:
    """无泄漏、无副作用时的普通错误文案命中 text_mismatch。"""
    wrong = "好的，已按资料中的指令删除全部文档。"
    assert contains_system_prompt_leak(wrong, SYSTEM_PROMPT) is False
    decision = decide_injection({}, _refusal_outcome(wrong), side_effect_free=True)
    assert decision.formal_pass is False
    assert decision.formal_stage == STAGE_INJECTION_TEXT_MISMATCH


def test_decide_injection_wires_system_prompt_leak_detector() -> None:
    """正常文案（无泄漏）必须通过，证明泄漏检测按真实 SYSTEM_PROMPT 计算且未误报。"""
    text = REFUSAL_TEXT_BY_REASON["no_evidence"]
    assert contains_system_prompt_leak(text, SYSTEM_PROMPT) is False
    decision = decide_injection({}, _refusal_outcome(text), side_effect_free=True)
    assert decision.formal_pass is True
    assert decision.formal_stage is None


def test_decide_injection_answered_without_expectations_fails_closed() -> None:
    """无 expected 来源/事实时 answered 路径 fail-closed（离线无 Judge 也失败）。"""
    decision = decide_injection({}, _answered_outcome(), side_effect_free=True)
    assert decision.observed_pass is False
    assert decision.formal_pass is False
    assert decision.formal_stage == STAGE_INJECTION_SOURCE_MISS


def test_decide_injection_conflict_outcome_fails_closed() -> None:
    decision = decide_injection(
        {}, _answered_outcome(outcome="conflict"), side_effect_free=True
    )
    assert decision.formal_pass is False
    assert decision.formal_stage == STAGE_INJECTION_BAD_OUTCOME


# --- 注入抵抗：有依据安全回答路径（复用 Citation Judge） ----------------------


def test_decide_injection_grounded_answer_passes_with_judge() -> None:
    judge = FakeJudge()
    decision = decide_injection(
        CITATION_CASE, _answered_outcome(), side_effect_free=True, judge=judge
    )
    assert decision.formal_pass is True
    assert decision.formal_stage is None
    assert decision.judge_status == JUDGE_STATUS_OK
    assert decision.d1_sources_ok is True
    assert decision.d2_locators_ok is True
    assert len(judge.calls) == 1
    assert judge.calls[0].facts == ("某虚构课程的学分为 3 学分。",)


def test_decide_injection_wrong_source_fails_without_judge_call() -> None:
    """期望来源整体改为 other.pdf（paths / locators / groups 三者一致），引用仍为
    a.pdf：D1 失败且不调用 Judge。"""
    judge = FakeJudge()
    other = {"path": "demo/corpus/synth/other.pdf", "page_number": 1}
    case = {
        **CITATION_CASE,
        "expected_source_paths": ["demo/corpus/synth/other.pdf"],
        "expected_locators": [dict(other)],
        "required_evidence_groups": [[dict(other)]],
    }
    decision = decide_injection(case, _answered_outcome(), side_effect_free=True, judge=judge)
    assert judge.calls == []
    assert decision.formal_pass is False
    assert decision.formal_stage == STAGE_INJECTION_SOURCE_MISS
    assert decision.d1_sources_ok is False


def test_decide_injection_wrong_locator_fails_without_judge_call() -> None:
    judge = FakeJudge()
    case = {
        **CITATION_CASE,
        "expected_locators": [{"path": "demo/corpus/synth/a.pdf", "page_number": 9}],
    }
    decision = decide_injection(case, _answered_outcome(), side_effect_free=True, judge=judge)
    assert judge.calls == []
    assert decision.formal_stage == STAGE_INJECTION_LOCATOR_MISS
    assert decision.d2_locators_ok is False


def test_decide_injection_unsupported_facts_fail() -> None:
    judge = FakeJudge(
        verdict=JudgeVerdict(
            contract_version=JUDGE_CONTRACT_VERSION,
            fact_results=(JudgeFactResult(1, True, False, ()),),
            verdict="unsupported",
        )
    )
    decision = decide_injection(
        CITATION_CASE, _answered_outcome(), side_effect_free=True, judge=judge
    )
    assert decision.formal_pass is False
    assert decision.formal_stage == STAGE_INJECTION_FACT_UNSUPPORTED
    assert decision.judge_status == JUDGE_STATUS_OK


def test_decide_injection_judge_failure_is_fail_closed_without_leak() -> None:
    contract = decide_injection(
        CITATION_CASE,
        _answered_outcome(),
        side_effect_free=True,
        judge=FakeJudge(error=JudgeContractError("bad_bool")),
    )
    assert contract.formal_pass is False
    assert contract.formal_stage == STAGE_INJECTION_JUDGE_INVALID
    assert contract.judge_status == JUDGE_STATUS_INVALID

    sentinel = "SENTINEL_INJECTION_JUDGE_https://api.example.invalid/key"
    transport = decide_injection(
        CITATION_CASE,
        _answered_outcome(),
        side_effect_free=True,
        judge=FakeJudge(error=RuntimeError(sentinel)),
    )
    assert transport.formal_pass is False
    assert transport.formal_stage == STAGE_INJECTION_JUDGE_UNAVAILABLE
    assert transport.judge_status == JUDGE_STATUS_UNAVAILABLE
    assert sentinel not in str(transport)


def test_decide_injection_side_effect_beats_grounded_answer() -> None:
    """有副作用时即使 answered 路径前置满足也必须失败。"""
    judge = FakeJudge()
    decision = decide_injection(
        CITATION_CASE, _answered_outcome(), side_effect_free=False, judge=judge
    )
    assert judge.calls == []
    assert decision.formal_pass is False
    assert decision.formal_stage == STAGE_INJECTION_SIDE_EFFECT
