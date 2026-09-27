"""阶段 6 聊天编排：改写 → 检索 → 拒答/冲突判定 → 受证据约束生成 → SSE。

关键约束：

- **同步检索不阻塞事件循环**：Embedding / SQLite / Chroma / FTS / Reranker 都是同步
  调用，统一放进线程池执行，线程内**自建短 Session 并立即关闭**；只把已经脱离
  Session 的 ``RerankedResult`` 交给生成阶段。请求处理过程**不持有任何 Session**，
  也不使用 ``Depends(get_session)``。
- **先校验后发送**：在发送任何 token 之前完成 ``GroundedCompletion`` 的完整校验。
- **取消安全**：客户端断开 / ``asyncio.CancelledError`` 时停止读取上游、不再发送任何
  事件（包括 done/error），继续向上传播取消；``finally`` 中取消在途任务并标记资源已释放。
- **不泄漏**：SSE error 只包含稳定错误码、固定文案、retryable 与 request_id。
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
from app.chat.conflict import EvidenceItem, detect_conflicts
from app.chat.grounding import parse_grounded_completion, remap_citations
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
)
from app.rerank.base import RerankProvider
from app.runtime.coordinator import LocalModelCoordinator
from app.search.reranking import RerankingRetriever
from app.search.types import RetrievalFilters, RerankedResult, RetrievedChunk
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
        return encode_event(
            ERROR_EVENT,
            {
                "code": error.code,
                "message": error.message,
                "retryable": bool(error.retryable),
                "request_id": self.turn.request_id,
            },
        )

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

        results = await run_in_threadpool(self._retrieve, query)

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
        conflict = detect_conflicts(evidence)
        note = (
            f"{CONFLICT_DIRECTIVE}\n{conflict.note}"
            if conflict is not None
            else None
        )

        raw = await self._call_llm(
            SYSTEM_PROMPT,
            build_answer_user(self.turn.question, self._prompt_evidence(evidence, conflict), note),
        )
        completion = parse_grounded_completion(
            raw,
            evidence_count=len(results),
            answer_max_chars=settings.llm_answer_max_chars,
            required_indices=conflict.indices if conflict is not None else (),
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
        """线程池内执行：自建短 Session，检索完成后立即关闭。"""
        settings = self.runtime.settings
        with session_scope(self.runtime.session_factory) as session:
            retriever = RerankingRetriever(
                session,
                settings,
                self.runtime.vectors,
                self.runtime.embeddings,
                self.runtime.reranker,
                self.runtime.coordinator,
            )
            results, _diagnostics = retriever.search(
                query, self.turn.filters, top_k=settings.rerank_top_k
            )
        return results

    @staticmethod
    def _evidence(results: Sequence[RerankedResult]) -> list[EvidenceItem]:
        return [
            (position, item.chunk.text, item.chunk.document_version, item.chunk.doc_id)
            for position, item in enumerate(results, start=1)
        ]

    @staticmethod
    def _prompt_evidence(
        evidence: Sequence[EvidenceItem],
        conflict,
    ) -> list[tuple[int, str]]:
        """外发给模型的证据文本：冲突场景额外附带版本信息，其余保持最少。"""
        conflict_indices = set(conflict.indices) if conflict is not None else set()
        prompt_items: list[tuple[int, str]] = []
        for index, text, version, _doc_id in evidence:
            if index in conflict_indices and version:
                item = (index, f"{text}\n（document_version={version}）")
            else:
                item = (index, text)
            prompt_items.append(item)
        return prompt_items


__all__ = [
    "ChatRuntime",
    "ChatStreamRunner",
    "ChatTurn",
    "citation_payload",
    "planning_guard",
]
