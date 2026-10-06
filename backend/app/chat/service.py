"""阶段 6 聊天编排：改写 → 检索 → 拒答/冲突判定 → 受证据约束生成 → SSE。

关键约束：

- **同步检索不阻塞事件循环**：Embedding / SQLite / Chroma / FTS / Reranker 都是同步
  调用，统一放进线程池执行，线程内**自建短 Session 并立即关闭**；只把已经脱离
  Session 的 ``RerankedResult`` 交给生成阶段。请求处理过程**不持有任何 Session**，
  也不使用 ``Depends(get_session)``。
- **先校验后发送**：在发送任何 token 之前完成 ``GroundedCompletion`` 的完整校验。
- **取消安全**：客户端断开 / ``asyncio.CancelledError`` 时停止读取上游、不再发送任何
  事件（包括 done/error），继续向上传播取消；``finally`` 中取消在途任务并标记资源已释放。
- **不泄漏**：SSE error 只包含稳定错误码、固定文案、retryable 与 request_id；
  仅当错误码为 ``MODEL_RESPONSE_INVALID`` 且 ``details.reason`` 命中 grounding 白名单时，
  额外附带该稳定 ``reason``——其余任何 ``details`` 一律不输出。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, replace

from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from app import constants
from app.chat.conflict import (
    EvidenceItem,
    conflict_field_keys,
    conflict_suppression_reason,
    cross_version_intent,
    detect_conflicts,
    row_slot_intent,
    should_activate_conflict,
)
from app.chat.grounding import GROUNDING_REASONS, parse_grounded_completion, remap_citations
from app.chat.sse import (
    CITATION_EVENT,
    DONE_EVENT,
    ERROR_EVENT,
    TOKEN_EVENT,
    encode_event,
    iter_answer_tokens,
)
from app.config import Settings
from app.core.errors import (
    CHAT_QUERY_REWRITE_FAILED,
    DOCUMENT_EMBEDDING_FAILED,
    EMBEDDING_DIMENSION_MISMATCH,
    EMBEDDING_PROVIDER_UNAVAILABLE,
    MODEL_RESPONSE_INVALID,
    MODEL_STREAM_INTERRUPTED,
    MODEL_TIMEOUT,
    ApiError,
)
from app.db import session_scope
from app.embedding.base import EmbeddingProvider
from app.llm.base import LLMProvider
from app.llm.prompts import (
    CONFLICT_DIRECTIVE,
    REFUSAL_TEXT_BY_REASON,
    REWRITE_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_answer_user,
    build_rewrite_user,
    format_evidence_metadata,
)
from app.rerank.base import RerankProvider
from app.runtime.coordinator import LocalModelCoordinator
from app.search.reranking import RerankingRetriever
from app.search.types import (
    RerankDiagnostics,
    RetrievalFilters,
    RerankedResult,
    RetrievedChunk,
)
from app.vector.store import ChromaVectorStore

logger = logging.getLogger("app.chat")

# 规划/计算类问题的通用意图标记：阶段 7 尚未实现，必须拒绝而不是让模型心算
_PLANNING_MARKERS = (
    "还差",
    "差多少",
    "缺多少",
    "缺几门",
    "如何规划",
    "怎么规划",
    "规划建议",
    "规划方案",
    "能否毕业",
    "能不能毕业",
    "我的学分",
)

_WHITESPACE = re.compile(r"\s+")


def planning_guard(question: str) -> str | None:
    """返回稳定 reason_code；不需要规则计算时返回 ``None``。"""
    text = question or ""
    if any(marker in text for marker in _PLANNING_MARKERS):
        return constants.REASON_PLANNING_UNAVAILABLE
    return None


def citation_payload(citation_index: int, chunk: RetrievedChunk) -> dict[str, object]:
    """严格按 PRODUCT_SPEC 6.3 构造引用字段；不加入分数、路径或诊断。"""
    return {
        "citation_index": citation_index,
        "chunk_id": chunk.chunk_id,
        "doc_id": chunk.doc_id,
        "file_name": chunk.file_name,
        "document_version": chunk.document_version,
        "effective_from": chunk.effective_from,
        "dataset_version": chunk.dataset_version,
        "page_number": chunk.page_number,
        "sheet_name": chunk.sheet_name,
        "row_start": chunk.row_start,
        "row_end": chunk.row_end,
        "section_title": chunk.section_title,
        "quote": chunk.text,
    }


def error_reason_for(error: ApiError) -> str | None:
    """返回可安全外发的 ``MODEL_RESPONSE_INVALID`` 细分原因，否则 ``None``。

    只有错误码恰为 ``MODEL_RESPONSE_INVALID`` **且** ``details.reason`` 命中
    :data:`app.chat.grounding.GROUNDING_REASONS` 白名单时才返回该 reason；
    其它错误码、缺失/非法 reason、以及任何其它 ``details`` 一律不输出。
    """
    if error.code != MODEL_RESPONSE_INVALID:
        return None
    reason = error.details.get("reason")
    return reason if reason in GROUNDING_REASONS else None


def _empty_conflict_trace() -> dict[str, object]:
    """冲突诊断 trace 的初始值（**仅进程内评测读取**，绝不进入 SSE/API）。"""
    return {
        "detected": False,
        "activated": False,
        "cross_version_intent": False,
        "row_slot_intent": False,
        "suppression_reason": None,
        "signal_types": [],
        "conflict_indices": [],
        "field_count": 0,
        "field_digest": None,
        "top_k": 0,
        "version_count": 0,
        "model_outcome": None,
        "forced_conflict": False,
        "question_field_exact_match": False,
    }


def _empty_rerank_trace() -> dict[str, object]:
    """重排诊断 trace 的初始值（**仅进程内评测读取**，绝不进入 SSE/API）。

    未执行任何检索时 ``applied`` 与 ``degraded`` 均为 ``False``。
    """
    return {"applied": False, "degraded": False, "degraded_reason": None}


def _rerank_trace(diagnostics: RerankDiagnostics) -> dict[str, object]:
    """把**同一次**检索的 :class:`RerankDiagnostics` 映射为进程内安全诊断。

    降级判定**只以 ``degraded_reason`` 为准**：Reranker 明确不可用/超时时才会带稳定
    ``degraded_reason``；空候选或显式请求 0 条时 ``rerank_applied=False`` 但
    ``degraded_reason=None``，**不得误判为降级**。不额外调用任何 Provider。
    """
    return {
        "applied": bool(diagnostics.rerank_applied),
        "degraded": diagnostics.degraded_reason is not None,
        "degraded_reason": diagnostics.degraded_reason,
    }


# 与「检索期查询向量化」相关的稳定错误码；只有这些才被认定为 Embedding 失败。
EMBEDDING_FAILURE_CODES = frozenset(
    {DOCUMENT_EMBEDDING_FAILED, EMBEDDING_PROVIDER_UNAVAILABLE, EMBEDDING_DIMENSION_MISMATCH}
)


def _empty_embedding_trace() -> dict[str, object]:
    """Embedding 失败诊断 trace 的初始值（**仅进程内评测读取**，绝不进入 SSE/API）。"""
    return {"failed": False, "failure_reason": None}


def _embedding_trace_from_error(error: ApiError) -> dict[str, object] | None:
    """把**检索期** Embedding 失败映射为进程内安全诊断；非 Embedding 错误返回 ``None``。

    只有错误码属于 :data:`EMBEDDING_FAILURE_CODES` 时才返回 ``failed=True``；
    ``failure_reason`` 只取 ``details.reason`` 原始字符串（非法值由 ``QaCaseResult``
    构造时再归一为 ``unknown``），绝不读取或记录异常原文 / URL / 密钥。
    """
    if error.code not in EMBEDDING_FAILURE_CODES:
        return None
    reason = error.details.get("reason") if isinstance(error.details, dict) else None
    return {"failed": True, "failure_reason": reason if isinstance(reason, str) else None}


def conflict_force_applied(active: bool, model_outcome: str | None) -> bool:
    """服务端是否**实际改变**了模型 outcome（只有在冲突**已激活**时才可能为真）。

    仅当冲突已激活**且模型原本输出的不是** ``conflict`` 时才为真；
    模型原本就是 ``conflict`` 时返回 ``False``（服务端没有改变它）。
    """
    return bool(active) and model_outcome != constants.CHAT_OUTCOME_CONFLICT


def distinct_document_versions(evidence: Sequence[EvidenceItem]) -> int:
    """只统计**非空** ``document_version`` 的数量（``None`` / 空串不计入）。"""
    return len({version for _index, _text, version, _doc_id in evidence if version})


def _normalize_question_text(text: str) -> str:
    return _WHITESPACE.sub("", text or "").lower()


def question_mentions_conflict_field(question: str, field_keys: Sequence[str]) -> bool:
    """问题是否**精确出现**某个冲突字段/槽位键。

    纯字符串归一化匹配，确定性、离线、无副作用；**只用于诊断**，
    不参与也不改变 ``detect_conflicts`` 的任何判定结果。
    """
    normalized = _normalize_question_text(question)
    if not normalized:
        return False
    return any(
        token and token in normalized
        for token in (_normalize_question_text(key) for key in field_keys)
    )


@dataclass(frozen=True)
class ChatRuntime:
    """一次请求所需的进程级资源（不含任何 Session）。"""

    settings: Settings
    session_factory: sessionmaker[Session]
    vectors: ChromaVectorStore
    embeddings: EmbeddingProvider
    reranker: RerankProvider
    coordinator: LocalModelCoordinator
    llm: LLMProvider


@dataclass(frozen=True)
class ChatTurn:
    request_id: str
    question: str
    history: tuple[tuple[str, str], ...]
    filters: RetrievalFilters

    @property
    def multi_turn(self) -> bool:
        return bool(self.history)


@dataclass(frozen=True)
class _Outcome:
    """终止前的全部帧与 done 负载；done/error 之外不会再有字节。"""

    frames: list[bytes]
    done: dict[str, object]


class ChatStreamRunner:
    """把一次 Chat 请求编码为一条恰好以 done 或 error 终止的 SSE 流。"""

    def __init__(self, runtime: ChatRuntime, turn: ChatTurn):
        self.runtime = runtime
        self.turn = turn
        self.closed = False
        self.cancelled = False
        self.frames_sent = 0
        self._pending: asyncio.Task[str] | None = None
        # 仅供进程内评测读取的冲突诊断；不进入 SSE 帧、不进入任何 API 响应
        self.conflict_trace: dict[str, object] = _empty_conflict_trace()
        # 仅供进程内评测读取的检索指纹：**本次真实检索**结果的来源与定位，按重排后的顺序
        # 排列（下标 + 1 即 best_rank）。只记录 file_name 与 5 个定位字段，**不记录正文，
        # 也不记录任何分数**。它是同一批 ``results`` 的只读旁路记录，不做二次检索、不增加
        # 任何 Provider 调用；不进入 SSE 帧、不进入任何 API 响应，也不影响回答。
        self.retrieval_trace: list[dict[str, object]] = []
        # 仅供进程内评测读取的**分阶段**检索顺序（fused / reranked / final）：同样只是
        # 同一批结果的只读旁路，不记录正文与分数，不进入 SSE 帧、不进入任何 API 响应。
        self.retrieval_stages: dict[str, list[dict[str, object]]] = {}
        # 仅供进程内评测读取的重排降级诊断：直接来自**同一次** search 的 RerankDiagnostics，
        # 只有 applied / degraded / degraded_reason 三个安全字段；不增加任何 Provider 调用，
        # 不进入 SSE 帧、不进入任何 API 响应，也不影响回答。
        self.rerank_trace: dict[str, object] = _empty_rerank_trace()
        # 仅供进程内评测读取的 Embedding 失败诊断：检索期查询向量化失败时记录
        # failed / failure_reason（稳定小写标签）；不增加任何 Provider 调用、
        # 不改变 SSE/API 帧与错误码，也不影响任何回答内容。
        self.embedding_trace: dict[str, object] = _empty_embedding_trace()
        # 仅供进程内评测读取的引用选择诊断：``remap_citations`` 之后，**实际发出**的引用
        # 对应的 **final 原始名次**（1 起，按引用发出顺序排列；下标 + 1 即 citation_index）。
        # 只是同一批 ``results`` 的只读旁路，不含来源名、路径、正文、quote、chunk_id 或分数，
        # 不进入 SSE 帧、不进入任何 API 响应，也不影响回答。
        self.citation_trace: list[int] = []

    # -- 对外入口 ---------------------------------------------------------

    async def stream(self) -> AsyncIterator[bytes]:
        try:
            outcome = await self._prepare()
        except asyncio.CancelledError:
            # 取消：不发送 done/error，继续向上传播
            self.cancelled = True
            raise
        except ApiError as error:
            yield self._error_frame(error)
            return
        except Exception:  # noqa: BLE001 - 不得向客户端泄漏堆栈
            logger.exception("chat_stream_failed request_id=%s", self.turn.request_id)
            yield self._error_frame(ApiError(MODEL_STREAM_INTERRUPTED))
            return
        finally:
            await self._cleanup()

        try:
            for frame in outcome.frames:
                self.frames_sent += 1
                yield frame
            self.frames_sent += 1
            yield encode_event(DONE_EVENT, outcome.done)
        finally:
            await self._cleanup()

    # -- 内部实现 ---------------------------------------------------------

    async def _cleanup(self) -> None:
        """取消在途任务并标记资源释放；可重复调用。"""
        pending = self._pending
        self._pending = None
        if pending is not None and not pending.done():
            pending.cancel()
            with contextlib.suppress(BaseException):
                await pending
        self.closed = True

    def _error_frame(self, error: ApiError) -> bytes:
        payload: dict[str, object] = {
            "code": error.code,
            "message": error.message,
            "retryable": bool(error.retryable),
            "request_id": self.turn.request_id,
        }
        # 仅 MODEL_RESPONSE_INVALID 且 reason 命中白名单时才附带稳定细分原因；
        # 其它情况不输出任何 details，避免泄漏原始正文/URL/密钥。
        reason = error_reason_for(error)
        if reason is not None:
            payload["reason"] = reason
        return encode_event(ERROR_EVENT, payload)

    def _done(self, outcome: str, reason_code: str | None, citation_count: int) -> dict[str, object]:
        return {
            "request_id": self.turn.request_id,
            "outcome": outcome,
            "reason_code": reason_code,
            "citation_count": citation_count,
        }

    async def _prepare(self) -> _Outcome:
        settings = self.runtime.settings

        guard_reason = planning_guard(self.turn.question)
        if guard_reason is not None:
            return self._refusal(guard_reason)

        query = self.turn.question
        if self.turn.multi_turn:
            query = await self._rewrite(query)

        try:
            results = await run_in_threadpool(self._retrieve, query)
        except ApiError as error:
            # 检索期 Embedding 失败：仅在进程内记录诊断后按原样上抛，
            # SSE/API 的错误码、文案与既有行为完全不变。
            trace = _embedding_trace_from_error(error)
            if trace is not None:
                self.embedding_trace = trace
            raise
        # 只读旁路：记录本次真实检索的**来源 + 定位**（不含正文与分数），供进程内评测区分
        # 「正确 chunk 未召回」与「已召回但未引用」。早退路径（无结果 / 阈值不足）也留下记录，
        # 否则无法区分「未召回」与「未引用」。
        self.retrieval_trace = [
            {
                "file_name": item.chunk.file_name,
                "locator": {
                    "page_number": item.chunk.page_number,
                    "sheet_name": item.chunk.sheet_name,
                    "row_start": item.chunk.row_start,
                    "row_end": item.chunk.row_end,
                    "section_title": item.chunk.section_title,
                },
            }
            for item in results
        ]

        threshold = settings.retrieval_score_threshold
        if not results:
            return self._refusal(constants.REASON_NO_EVIDENCE)
        if threshold is not None:
            scores = [item.rerank_score for item in results]
            if any(score is None for score in scores):
                # Reranker 降级：没有可比较分数，绝不用 fused_score 顶替比较
                return self._refusal(constants.REASON_SCORE_UNAVAILABLE)
            if max(scores) < float(threshold):  # type: ignore[type-var]
                return self._refusal(constants.REASON_BELOW_THRESHOLD)

        evidence = self._evidence(results)
        self.conflict_trace["top_k"] = len(results)
        self.conflict_trace["version_count"] = distinct_document_versions(evidence)
        candidate = detect_conflicts(evidence)
        activated = should_activate_conflict(candidate, self.turn.question)
        self._record_conflict_trace(evidence, candidate, activated)
        # 只有**已激活**的冲突才进入 Prompt 路由与 outcome 改写；候选本身不改变任何行为
        conflict = candidate if activated else None
        note = (
            f"{CONFLICT_DIRECTIVE}\n{conflict.note}"
            if conflict is not None
            else None
        )

        raw = await self._call_llm(
            SYSTEM_PROMPT,
            build_answer_user(
                self.turn.question,
                self._prompt_evidence(results),
                note,
                conflict_indices=conflict.indices if conflict is not None else (),
            ),
        )
        completion = parse_grounded_completion(
            raw,
            evidence_count=len(results),
            answer_max_chars=settings.llm_answer_max_chars,
            required_indices=conflict.indices if conflict is not None else (),
        )
        self.conflict_trace["model_outcome"] = completion.outcome
        self.conflict_trace["forced_conflict"] = conflict_force_applied(
            bool(self.conflict_trace["activated"]), completion.outcome
        )

        if conflict is not None:
            completion = replace(
                completion,
                outcome=constants.CHAT_OUTCOME_CONFLICT,
                reason_code=constants.REASON_VERSION_CONFLICT,
            )
        elif completion.outcome == constants.CHAT_OUTCOME_REFUSED:
            reason = completion.reason_code or constants.REASON_MODEL_DECLINED
            return self._refusal(reason)

        answer, original_indices = remap_citations(completion)
        # 只读旁路：记录**实际发出**的引用对应的 final 原始名次（顺序即 citation_index）。
        self.citation_trace = [int(index) for index in original_indices]
        frames: list[bytes] = []
        for position, original_index in enumerate(original_indices, start=1):
            frames.append(
                encode_event(
                    CITATION_EVENT,
                    citation_payload(position, results[original_index - 1].chunk),
                )
            )
        frames.extend(
            encode_event(TOKEN_EVENT, {"text": token}) for token in iter_answer_tokens(answer)
        )
        return _Outcome(
            frames=frames,
            done=self._done(completion.outcome, completion.reason_code, len(original_indices)),
        )

    def _record_conflict_trace(
        self, evidence: Sequence[EvidenceItem], candidate, activated: bool
    ) -> None:
        """写入检测阶段可确定的冲突诊断。

        ``candidate`` 是**原始候选**（``detect_conflicts`` 的结果，不受激活判定影响）；
        ``activated`` 决定是否真正进入冲突 Prompt 路由。``model_outcome`` 与
        ``forced_conflict`` 在模型输出解析后回填（forced 只基于**已激活**冲突）。
        """
        detected = candidate is not None
        self.conflict_trace.update(
            {
                "detected": detected,
                "activated": bool(activated),
                "cross_version_intent": cross_version_intent(self.turn.question),
                "row_slot_intent": row_slot_intent(self.turn.question),
                "suppression_reason": conflict_suppression_reason(
                    candidate, self.turn.question
                ),
                "signal_types": list(candidate.signal_types) if detected else [],
                "conflict_indices": list(candidate.conflict_indices) if detected else [],
                "field_count": candidate.field_count if detected else 0,
                "field_digest": candidate.field_digest if detected else None,
                "question_field_exact_match": (
                    question_mentions_conflict_field(
                        self.turn.question, conflict_field_keys(evidence)
                    )
                    if detected
                    else False
                ),
            }
        )

    def _refusal(self, reason_code: str) -> _Outcome:
        text = REFUSAL_TEXT_BY_REASON.get(reason_code, REFUSAL_TEXT_BY_REASON["insufficient_evidence"])
        frames = [encode_event(TOKEN_EVENT, {"text": token}) for token in iter_answer_tokens(text)]
        return _Outcome(
            frames=frames,
            done=self._done(constants.CHAT_OUTCOME_REFUSED, reason_code, 0),
        )

    async def _rewrite(self, question: str) -> str:
        raw = await self._call_llm(
            REWRITE_SYSTEM_PROMPT,
            build_rewrite_user(self.turn.history, question),
        )
        cleaned = _WHITESPACE.sub(" ", raw or "").strip().strip('"').strip("“”").strip()
        if not cleaned:
            raise ApiError(CHAT_QUERY_REWRITE_FAILED, details={"reason": "empty_rewrite"})
        if len(cleaned) > self.runtime.settings.llm_rewrite_max_chars:
            raise ApiError(CHAT_QUERY_REWRITE_FAILED, details={"reason": "rewrite_too_long"})
        return cleaned

    async def _call_llm(self, system: str, user: str) -> str:
        task = asyncio.ensure_future(
            asyncio.wait_for(
                self.runtime.llm.generate(system=system, user=user),
                timeout=self.runtime.settings.llm_timeout_seconds,
            )
        )
        self._pending = task
        try:
            raw = await task
        except TimeoutError as error:
            self._pending = None
            raise ApiError(MODEL_TIMEOUT) from error
        return raw

    def _retrieve(self, query: str) -> list[RerankedResult]:
        """线程池内执行：自建短 Session，检索完成后立即关闭。

        ``retrieval_stages`` 由同一次 ``RerankingRetriever.search`` 旁路记录 fused / reranked /
        final 三个阶段的顺序，仅供进程内评测读取；**不额外调用** Embedding / Reranker，
        也不改变返回的 top-10 与其排序。
        """
        settings = self.runtime.settings
        stages: dict[str, list[dict[str, object]]] = {}
        with session_scope(self.runtime.session_factory) as session:
            retriever = RerankingRetriever(
                session,
                settings,
                self.runtime.vectors,
                self.runtime.embeddings,
                self.runtime.reranker,
                self.runtime.coordinator,
            )
            results, diagnostics = retriever.search(
                query,
                self.turn.filters,
                top_k=settings.rerank_top_k,
                stage_sink=stages,
            )
        self.retrieval_stages = stages
        # 同一次 search 的重排诊断旁路：不额外调用 Provider，不影响 results 与排序
        self.rerank_trace = _rerank_trace(diagnostics)
        return results

    @staticmethod
    def _evidence(results: Sequence[RerankedResult]) -> list[EvidenceItem]:
        return [
            (position, item.chunk.text, item.chunk.document_version, item.chunk.doc_id)
            for position, item in enumerate(results, start=1)
        ]

    @staticmethod
    def _source_labels(results: Sequence[RerankedResult]) -> dict[str, str]:
        """按 final 中**首次出现**的 doc_id 顺序生成 S1/S2…；同一文档必然同标签。"""
        labels: dict[str, str] = {}
        for item in results:
            doc_id = item.chunk.doc_id
            if doc_id and doc_id not in labels:
                labels[doc_id] = f"S{len(labels) + 1}"
        return labels

    @classmethod
    def _prompt_evidence(
        cls,
        results: Sequence[RerankedResult],
    ) -> list[tuple[int, str]]:
        """外发给模型的证据：正文 + 一行**白名单 JSON 元数据**。

        元数据只含 :data:`app.llm.prompts.EVIDENCE_METADATA_FIELDS` 中的字段，因此
        ``file_name``、``doc_id``、``chunk_id``、``source_key``、路径与分数都不可能外发；
        来源标签只由 final 首次出现的 doc_id 顺序决定，同一文档在整段证据中标签恒定。
        正文、顺序与编号均保持不变。
        """
        labels = cls._source_labels(results)
        prompt_items: list[tuple[int, str]] = []
        for index, item in enumerate(results, start=1):
            chunk = item.chunk
            # 课程代码只来自 citation，且**仅非空字符串**允许外发（避免混入 ID/路径等其它键）
            course_code = chunk.citation.get("course_code")
            if not isinstance(course_code, str) or not course_code.strip():
                course_code = None
            metadata: dict[str, object] = {
                "source_label": labels.get(chunk.doc_id),
                "course_code": course_code,
                "doc_category": chunk.doc_category,
                "document_version": chunk.document_version,
                "effective_from": chunk.effective_from,
                "page_number": chunk.page_number,
                "sheet_name": chunk.sheet_name,
                "row_start": chunk.row_start,
                "row_end": chunk.row_end,
                "section_title": chunk.section_title,
            }
            prompt_items.append(
                (index, f"{chunk.text}\n{format_evidence_metadata(metadata)}")
            )
        return prompt_items


__all__ = [
    "ChatRuntime",
    "ChatStreamRunner",
    "ChatTurn",
    "citation_payload",
    "conflict_force_applied",
    "distinct_document_versions",
    "error_reason_for",
    "planning_guard",
    "question_mentions_conflict_field",
]
