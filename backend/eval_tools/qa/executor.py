"""9B 执行器：在隔离环境中用真实问答与学业规划实现跑完全部用例。

- 问答：直接调用生产 ``ChatStreamRunner``（与 ``POST /api/chat/stream`` 同一条编排：
  规划守卫 → 检索 → 拒答/冲突判定 → 受证据约束生成 → 引用重映射）；
- 学业规划：直接调用 ``app.academic.planning.build_plan``（与 ``POST /api/academic/plan``
  同一实现，数字只来自确定性规则引擎，不经过 LLM）；
- 全部使用 Fake Embedding / Reranker / LLM，完全离线，写入调用方给定的隔离目录。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from app.academic import options as academic_options
from app.academic import planning as academic_planning
from app.chat.service import ChatRuntime, ChatStreamRunner, ChatTurn
from app.llm.factory import build_llm_provider
from app.search.types import RetrievalFilters

from eval_tools.pipeline import EvalEnvironment
from eval_tools.qa.metrics import (
    GROUP_CITATION,
    GROUP_INJECTION_SAFETY,
    GROUP_PLANNING,
    GROUP_REFUSAL_CONTRACT,
    STAGE_CITATION_CONTRACT,
    STAGE_CITATION_SOURCE_MISS,
    STAGE_INJECTION_SEMANTICS,
    STAGE_PLANNING_MISMATCH,
    STAGE_REFUSAL_NOT_TRIGGERED,
    STAGE_REFUSAL_WRONG_REASON,
    QaCaseResult,
    classify_case_group,
)

# ground truth 的 record_set 键 -> options 显示名；rule_set 键 -> 真实 rule_version
RECORD_SET_NAMES = {
    "student_a": "匿名学生A · 课程记录",
    "student_b": "匿名学生B · 课程记录",
}
RULE_VERSIONS = {
    "QM-CS-2025-1": "2025.1",
    "QM-CS-2026-1": "2026.1",
    "QM-CS-2025.1": "2025.1",
    "QM-CS-2026.1": "2026.1",
}

# 拒答契约允许的稳定 reason_code（无依据问题必须落在其中之一）
REFUSAL_REASONS = {"no_evidence"}

# 期望「已带引用作答」的 outcome（冲突场景同样必须带引用）
ANSWERED_OUTCOMES = {"answered", "conflict"}


@dataclass(frozen=True)
class ChatOutcome:
    outcome: str | None
    reason_code: str | None
    citation_count: int
    citation_chunk_ids: tuple[str, ...]
    citation_file_names: tuple[str, ...]
    answer: str
    error_code: str | None = None


def _parse_sse(body: str) -> ChatOutcome:
    """解析 SSE 帧；只读取公开字段，不保留正文以外的敏感内容。"""
    outcome: str | None = None
    reason: str | None = None
    citation_count = 0
    chunk_ids: list[str] = []
    file_names: list[str] = []
    error_code: str | None = None
    tokens: list[str] = []

    for block in body.split("\n\n"):
        if not block.strip():
            continue
        event = ""
        payload: dict = {}
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                payload = json.loads(line[len("data: ") :])
        if event == "done":
            outcome = payload.get("outcome")
            reason = payload.get("reason_code")
            citation_count = int(payload.get("citation_count") or 0)
        elif event == "citation":
            chunk_ids.append(str(payload.get("chunk_id") or ""))
            file_names.append(str(payload.get("file_name") or ""))
        elif event == "error":
            error_code = payload.get("code")
        elif event == "token":
            tokens.append(str(payload.get("text") or ""))

    return ChatOutcome(
        outcome=outcome,
        reason_code=reason,
        citation_count=citation_count if outcome is not None else 0,
        citation_chunk_ids=tuple(chunk_ids),
        citation_file_names=tuple(file_names),
        answer="".join(tokens),
        error_code=error_code,
    )


def _run_chat(runtime: ChatRuntime, request_id: str, question: str) -> ChatOutcome:
    turn = ChatTurn(
        request_id=request_id,
        question=question,
        history=(),
        filters=RetrievalFilters(),
    )

    async def collect() -> str:
        runner = ChatStreamRunner(runtime, turn)
        chunks: list[str] = []
        async for frame in runner.stream():
            chunks.append(frame.decode("utf-8"))
        return "".join(chunks)

    return _parse_sse(asyncio.run(collect()))


def _expected_basenames(case: dict) -> set[str]:
    return {
        path.rsplit("/", 1)[-1] for path in (case.get("expected_source_paths") or [])
    }


def evaluate_chat_case(runtime: ChatRuntime, case: dict) -> QaCaseResult:
    """按组判定单个问答用例；失败时给出稳定阶段标签。"""
    group = classify_case_group(case)
    outcome = _run_chat(runtime, case["id"], case["question"])

    if group == GROUP_REFUSAL_CONTRACT:
        if outcome.outcome != "refused":
            return QaCaseResult(
                case_id=case["id"],
                group=group,
                question=case["question"],
                observed_pass=False,
                outcome=outcome.outcome,
                reason_code=outcome.reason_code,
                citation_count=outcome.citation_count,
                failure_stage=STAGE_REFUSAL_NOT_TRIGGERED,
                note=f"期望拒答，实际 outcome={outcome.outcome}",
            )
        if outcome.reason_code not in REFUSAL_REASONS:
            return QaCaseResult(
                case_id=case["id"],
                group=group,
                question=case["question"],
                observed_pass=False,
                outcome=outcome.outcome,
                reason_code=outcome.reason_code,
                citation_count=outcome.citation_count,
                failure_stage=STAGE_REFUSAL_WRONG_REASON,
                note=f"拒答 reason_code={outcome.reason_code}",
            )
        return QaCaseResult(
            case_id=case["id"],
            group=group,
            question=case["question"],
            observed_pass=True,
            outcome=outcome.outcome,
            reason_code=outcome.reason_code,
            citation_count=outcome.citation_count,
        )

    if group == GROUP_INJECTION_SAFETY:
        observed = outcome.outcome == "refused"
        return QaCaseResult(
            case_id=case["id"],
            group=group,
            question=case["question"],
            observed_pass=observed,
            outcome=outcome.outcome,
            reason_code=outcome.reason_code,
            citation_count=outcome.citation_count,
            failure_stage=None if observed else STAGE_INJECTION_SEMANTICS,
            note=None if observed else f"注入用例未被拒答：outcome={outcome.outcome}",
        )

    if group == GROUP_CITATION:
        # 结构接线：带引用作答，且每个引用都能解析到真实 chunk
        contract_ok = (
            outcome.outcome in ANSWERED_OUTCOMES
            and outcome.citation_count >= 1
            and len(outcome.citation_chunk_ids) == outcome.citation_count
            and all(chunk_id for chunk_id in outcome.citation_chunk_ids)
        )
        # 来源命中：至少一个引用来源属于 expected_source_paths
        expected = _expected_basenames(case)
        source_hit = bool(expected) and any(
            name in expected for name in outcome.citation_file_names
        )
        observed = contract_ok and source_hit
        if not contract_ok:
            stage, note = (
                STAGE_CITATION_CONTRACT,
                f"outcome={outcome.outcome} citation_count={outcome.citation_count} "
                f"chunks={len(outcome.citation_chunk_ids)}",
            )
        elif not source_hit:
            stage, note = STAGE_CITATION_SOURCE_MISS, "引用来源未包含期望来源"
        else:
            stage, note = None, None
        return QaCaseResult(
            case_id=case["id"],
            group=group,
            question=case["question"],
            observed_pass=observed,
            outcome=outcome.outcome,
            reason_code=outcome.reason_code,
            citation_count=outcome.citation_count,
            citation_contract_ok=contract_ok,
            citation_source_hit=source_hit,
            failure_stage=stage,
            note=note,
        )

    # planning / unscored 不出现在问答执行路径
    return QaCaseResult(
        case_id=case["id"],
        group=group,
        question=case["question"],
        observed_pass=False,
        failure_stage=STAGE_CITATION_CONTRACT,
        note=f"问答执行不支持的分组：{group}",
    )


def _planning_ids(environment: EvalEnvironment) -> tuple[dict[str, str], dict[str, str]]:
    with environment.session_factory() as session:
        payload = academic_options.academic_options(session)
    records = {item["name"]: item["id"] for item in payload["record_sets"]}
    rules = {item["rule_version"]: item["id"] for item in payload["rule_sets"]}
    return records, rules


def _planning_mismatches(payload: dict, expected: dict) -> list[str]:
    mismatches: list[str] = []
    for key in (
        "required_credits",
        "completed_credits",
        "in_progress_credits",
        "remaining_credits",
    ):
        if payload.get(key) != expected.get(key):
            mismatches.append(f"{key}: {payload.get(key)} != {expected.get(key)}")
    produced_courses = [
        (item["course_code"], item["course_name"], item["credits"], item["category"])
        for item in payload.get("missing_required_courses", [])
    ]
    wanted_courses = [
        (item["course_code"], item["course_name"], item["credits"], item["category"])
        for item in expected.get("missing_required_courses", [])
    ]
    if produced_courses != wanted_courses:
        mismatches.append("missing_required_courses 不一致")
    if payload.get("category_gaps") != expected.get("category_gaps"):
        mismatches.append("category_gaps 不一致")
    produced_codes = [item["code"] for item in payload.get("conflict_warnings", [])]
    wanted_codes = [item["code"] for item in expected.get("conflict_warnings", [])]
    if produced_codes[: len(wanted_codes)] != wanted_codes:
        mismatches.append(f"conflict_warnings 前缀不一致：{produced_codes}")
    return mismatches


def evaluate_planning_case(environment: EvalEnvironment, case: dict) -> QaCaseResult:
    plan_input = case["planning_input"]
    expected = case["expected_planning_result"]
    records, rules = _planning_ids(environment)
    record_name = RECORD_SET_NAMES.get(plan_input["record_set"], plan_input["record_set"])
    rule_version = RULE_VERSIONS.get(plan_input["rule_set"], plan_input["rule_set"])
    record_id = records.get(record_name)
    rule_id = rules.get(rule_version)
    if not record_id or not rule_id:
        return QaCaseResult(
            case_id=case["id"],
            group=GROUP_PLANNING,
            question=case["question"],
            observed_pass=False,
            failure_stage=STAGE_PLANNING_MISMATCH,
            note=f"缺少集合：record={record_name} rule={rule_version}",
        )

    with environment.session_factory() as session:
        result = academic_planning.build_plan(
            session, environment.settings, record_id, rule_id
        )
    from app.academic.types import planning_result_payload

    payload = planning_result_payload(result)
    mismatches = _planning_mismatches(payload, expected)
    return QaCaseResult(
        case_id=case["id"],
        group=GROUP_PLANNING,
        question=case["question"],
        observed_pass=not mismatches,
        failure_stage=None if not mismatches else STAGE_PLANNING_MISMATCH,
        note=None if not mismatches else "；".join(mismatches),
    )


def evaluate_cases(environment: EvalEnvironment, cases: list[dict]) -> list[QaCaseResult]:
    """按用例分组执行：planning 走规则引擎，其余走问答编排。"""
    llm = build_llm_provider(environment.settings)
    runtime = ChatRuntime(
        settings=environment.settings,
        session_factory=environment.session_factory,
        vectors=environment.worker.vectors,
        embeddings=environment.worker.embeddings,
        reranker=environment.reranker,
        coordinator=environment.worker.coordinator,
        llm=llm,
    )
    try:
        results: list[QaCaseResult] = []
        for case in cases:
            if classify_case_group(case) == GROUP_PLANNING:
                results.append(evaluate_planning_case(environment, case))
            else:
                results.append(evaluate_chat_case(runtime, case))
        return results
    finally:
        llm.close()


__all__ = [
    "ANSWERED_OUTCOMES",
    "ChatOutcome",
    "RECORD_SET_NAMES",
    "REFUSAL_REASONS",
    "RULE_VERSIONS",
    "evaluate_cases",
    "evaluate_chat_case",
    "evaluate_planning_case",
]
