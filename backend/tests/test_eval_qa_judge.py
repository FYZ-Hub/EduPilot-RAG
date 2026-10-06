"""阶段 9B Judge 纯逻辑测试：版本化 Prompt 构造与严格 JSON 契约解析（不联网）。"""

from __future__ import annotations

import json

import pytest

from eval_tools.qa.judge import (
    CONTRACT_REASONS,
    JUDGE_CONTRACT_VERSION,
    JUDGE_SYSTEM_PROMPT,
    UNTRUSTED_NOTICE,
    VERDICT_PARTIAL,
    VERDICT_SUPPORTED,
    VERDICT_UNSUPPORTED,
    JudgeContractError,
    build_judge_payload,
    build_judge_request,
    build_judge_user,
    parse_judge_output,
)

QUESTION = "合成问题：虚构课程 QM-SYNTH-001 的学分为多少？"
FACTS = ["虚构课程 QM-SYNTH-001 的学分为 3 学分。", "该课程由虚构院系开设。"]
ANSWER = "依据检索到的资料：[1] 学分为 3 学分。"
EVIDENCE = [
    (1, "合成证据：学分为 3 学分。", {"page_number": 1, "section_title": "学分"}),
    (2, "合成证据：由虚构院系开设。", {"sheet_name": "课表", "row_start": 3, "row_end": 3}),
]


def _fact(index: int, expresses: bool = True, supports: bool = True, indices=(1,)) -> dict:
    return {
        "fact_index": index,
        "answer_expresses": expresses,
        "evidence_supports": supports,
        "evidence_indices": list(indices),
    }


def _raw(results: list, verdict: str) -> str:
    return json.dumps({"fact_results": results, "verdict": verdict}, ensure_ascii=False)


# --- Prompt 构造 ------------------------------------------------------------


def test_build_judge_payload_contains_all_required_inputs() -> None:
    payload = build_judge_payload(
        question=QUESTION, facts=FACTS, answer=ANSWER, evidence=EVIDENCE
    )
    assert payload["untrusted_notice"] == UNTRUSTED_NOTICE
    assert payload["question"] == QUESTION
    assert payload["answer"] == ANSWER
    assert [item["fact_index"] for item in payload["facts"]] == [1, 2]
    assert [item["fact"] for item in payload["facts"]] == FACTS
    assert [item["index"] for item in payload["evidence"]] == [1, 2]
    assert payload["evidence"][0]["quote"] == EVIDENCE[0][1]
    assert payload["evidence"][1]["locator"] == EVIDENCE[1][2]
    # locator 是副本，不与调用方共享可变对象
    assert payload["evidence"][1]["locator"] is not EVIDENCE[1][2]


def test_build_judge_user_is_strict_json_with_untrusted_notice() -> None:
    raw = build_judge_user(question=QUESTION, facts=FACTS, answer=ANSWER, evidence=EVIDENCE)
    decoded = json.loads(raw)
    assert set(decoded) == {"untrusted_notice", "question", "facts", "answer", "evidence"}
    assert "不可信数据" in decoded["untrusted_notice"]


def test_build_judge_request_declares_version_and_untrusted_data() -> None:
    system, user = build_judge_request(
        question=QUESTION, facts=FACTS, answer=ANSWER, evidence=EVIDENCE
    )
    assert system == JUDGE_SYSTEM_PROMPT
    assert JUDGE_CONTRACT_VERSION == "qa-citation-judge/2"
    assert JUDGE_CONTRACT_VERSION in system
    assert "不可信数据" in system
    assert json.loads(user)["question"] == QUESTION


# --- 严格解析：合法输出 -----------------------------------------------------


def test_parse_judge_output_accepts_supported_verdict() -> None:
    verdict = parse_judge_output(
        _raw([_fact(1, True, True, (1,)), _fact(2, True, True, (2,))], VERDICT_SUPPORTED),
        fact_count=2,
        evidence_count=2,
    )
    assert verdict.contract_version == JUDGE_CONTRACT_VERSION
    assert verdict.verdict == VERDICT_SUPPORTED
    assert verdict.all_supported is True
    assert [item.fact_index for item in verdict.fact_results] == [1, 2]
    assert verdict.fact_results[0].evidence_indices == (1,)


def test_parse_judge_output_accepts_partial_and_unsupported() -> None:
    partial = parse_judge_output(
        _raw([_fact(1, True, True, (1,)), _fact(2, False, True, (2,))], VERDICT_PARTIAL),
        fact_count=2,
        evidence_count=2,
    )
    assert partial.all_supported is False
    assert partial.verdict == VERDICT_PARTIAL

    unsupported = parse_judge_output(
        _raw([_fact(1, False, False, ()), _fact(2, False, False, ())], VERDICT_UNSUPPORTED),
        fact_count=2,
        evidence_count=2,
    )
    assert unsupported.all_supported is False


def test_parse_judge_output_tolerates_single_json_fence() -> None:
    body = _raw([_fact(1, True, True, (1,))], VERDICT_SUPPORTED)
    verdict = parse_judge_output(
        "```json\n" + body + "\n```", fact_count=1, evidence_count=1
    )
    assert verdict.all_supported is True


# --- 严格解析：安全失败 -----------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "fact_count", "evidence_count", "reason"),
    [
        ("", 1, 1, "not_json"),
        ("not-json", 1, 1, "not_json"),
        ("[1, 2, 3]", 1, 1, "not_object"),
        (json.dumps({}), 1, 1, "missing_keys"),
        (
            json.dumps(
                {"fact_results": [_fact(1)], "verdict": VERDICT_SUPPORTED, "extra": 1}
            ),
            1,
            1,
            "extra_keys",
        ),
        (
            json.dumps({"fact_results": [], "verdict": VERDICT_SUPPORTED}),
            1,
            1,
            "bad_fact_results",
        ),
        (
            json.dumps({"fact_results": ["x"], "verdict": VERDICT_SUPPORTED}),
            1,
            1,
            "bad_fact_result_item",
        ),
        (
            json.dumps(
                {"fact_results": [{"fact_index": 1, "answer_expresses": True, "evidence_supports": True}],
                 "verdict": VERDICT_SUPPORTED}
            ),
            1,
            1,
            "missing_fact_keys",
        ),
        (
            json.dumps(
                {"fact_results": [dict(_fact(1), extra=1)], "verdict": VERDICT_SUPPORTED}
            ),
            1,
            1,
            "extra_fact_keys",
        ),
        (_raw([_fact(0)], VERDICT_SUPPORTED), 1, 1, "bad_fact_index"),
        (_raw([_fact(2)], VERDICT_SUPPORTED), 1, 1, "bad_fact_index"),
        (
            json.dumps(
                {"fact_results": [dict(_fact(1), fact_index=True)], "verdict": VERDICT_SUPPORTED}
            ),
            1,
            1,
            "bad_fact_index",
        ),
        (_raw([_fact(1), _fact(1)], VERDICT_SUPPORTED), 2, 2, "duplicate_fact"),
        (_raw([_fact(1)], VERDICT_SUPPORTED), 2, 2, "fact_coverage"),
        (_raw([_fact(1, "true", True, (1,))], VERDICT_SUPPORTED), 1, 1, "bad_bool"),
        (_raw([_fact(1, 1, True, (1,))], VERDICT_SUPPORTED), 1, 1, "bad_bool"),
        (_raw([_fact(1, True, "yes", (1,))], VERDICT_SUPPORTED), 1, 1, "bad_bool"),
        (_raw([_fact(1, True, True, "1")], VERDICT_SUPPORTED), 1, 1, "bad_evidence_indices"),
        (_raw([_fact(1, True, True, (True,))], VERDICT_SUPPORTED), 1, 1, "bad_evidence_indices"),
        (_raw([_fact(1, True, True, (3,))], VERDICT_SUPPORTED), 1, 2, "evidence_out_of_range"),
        (_raw([_fact(1, True, True, (1, 1))], VERDICT_SUPPORTED), 1, 2, "duplicate_evidence_index"),
        (_raw([_fact(1, True, True, ())], VERDICT_SUPPORTED), 1, 1, "supported_without_evidence"),
        (_raw([_fact(1, True, False, (1,))], VERDICT_UNSUPPORTED), 1, 1, "unsupported_with_evidence"),
        (_raw([_fact(1, True, True, (1,))], "maybe"), 1, 1, "bad_verdict"),
    ],
)
def test_parse_judge_output_safe_failures(
    raw: str, fact_count: int, evidence_count: int, reason: str
) -> None:
    with pytest.raises(JudgeContractError) as error:
        parse_judge_output(raw, fact_count=fact_count, evidence_count=evidence_count)
    assert error.value.reason == reason
    assert error.value.reason in CONTRACT_REASONS


def test_parse_judge_output_rejects_non_positive_fact_count() -> None:
    with pytest.raises(JudgeContractError) as error:
        parse_judge_output(_raw([_fact(1)], VERDICT_SUPPORTED), fact_count=0, evidence_count=1)
    assert error.value.reason == "no_facts"


def test_contract_error_does_not_echo_raw_content() -> None:
    sentinel = "SENTINEL_SHOULD_NOT_APPEAR"
    raw = json.dumps(
        {
            "fact_results": [
                {
                    "fact_index": 1,
                    "answer_expresses": sentinel,
                    "evidence_supports": True,
                    "evidence_indices": [1],
                }
            ],
            "verdict": VERDICT_SUPPORTED,
        }
    )
    with pytest.raises(JudgeContractError) as error:
        parse_judge_output(raw, fact_count=1, evidence_count=1)
    assert sentinel not in str(error.value)
    assert sentinel not in error.value.reason


# --- 契约 v2：fact_results 唯一权威，verdict 由服务端派生 ---------------------


def test_judge_prompt_requires_only_fact_results() -> None:
    assert '"verdict"' not in JUDGE_SYSTEM_PROMPT
    assert "fact_results" in JUDGE_SYSTEM_PROMPT
    assert "服务端" in JUDGE_SYSTEM_PROMPT


def test_parse_accepts_output_without_legacy_verdict() -> None:
    raw = json.dumps({"fact_results": [_fact(1, True, True, (1,))]}, ensure_ascii=False)
    verdict = parse_judge_output(raw, fact_count=1, evidence_count=1)
    assert verdict.verdict == VERDICT_SUPPORTED
    assert verdict.all_supported is True


@pytest.mark.parametrize(
    ("results", "legacy", "expected"),
    [
        ([_fact(1, True, True, (1,))], VERDICT_PARTIAL, VERDICT_SUPPORTED),
        ([_fact(1, True, True, (1,))], VERDICT_UNSUPPORTED, VERDICT_SUPPORTED),
        ([_fact(1, False, False, ())], VERDICT_SUPPORTED, VERDICT_UNSUPPORTED),
        (
            [_fact(1, True, True, (1,)), _fact(2, False, False, ())],
            VERDICT_SUPPORTED,
            VERDICT_PARTIAL,
        ),
        (
            [_fact(1, True, True, (1,)), _fact(2, False, False, ())],
            VERDICT_UNSUPPORTED,
            VERDICT_PARTIAL,
        ),
    ],
)
def test_legacy_inconsistent_verdict_is_ignored(results, legacy, expected) -> None:
    """旧版顶层 verdict 与逐事实不一致时必须忽略，服务端派生值优先。"""
    verdict = parse_judge_output(
        _raw(results, legacy), fact_count=len(results), evidence_count=1
    )
    assert verdict.verdict == expected


def test_legacy_verdict_must_be_legal_enum_if_present() -> None:
    with pytest.raises(JudgeContractError) as error:
        parse_judge_output(_raw([_fact(1)], "unknown_enum"), fact_count=1, evidence_count=1)
    assert error.value.reason == "bad_verdict"


def test_missing_fact_results_is_rejected_even_with_verdict() -> None:
    raw = json.dumps({"verdict": VERDICT_SUPPORTED}, ensure_ascii=False)
    with pytest.raises(JudgeContractError) as error:
        parse_judge_output(raw, fact_count=1, evidence_count=1)
    assert error.value.reason == "missing_keys"


def test_other_extra_top_level_keys_are_still_rejected() -> None:
    raw = json.dumps(
        {"fact_results": [_fact(1)], "summary": "模型自作主张的总结"},
        ensure_ascii=False,
    )
    with pytest.raises(JudgeContractError) as error:
        parse_judge_output(raw, fact_count=1, evidence_count=1)
    assert error.value.reason == "extra_keys"


def test_verdict_mismatch_is_no_longer_a_contract_reason() -> None:
    assert "verdict_mismatch" not in CONTRACT_REASONS
