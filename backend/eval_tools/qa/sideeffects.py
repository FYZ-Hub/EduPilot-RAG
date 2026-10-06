"""9B 隔离状态快照与副作用判定。

职责边界（必须如实声明）：

- **只**检查本次隔离评测所用的 SQLite（``documents`` / ``document_chunks``）、Chroma 向量与
  FTS5（``chunk_fts``）；
- **不**扫描主机其他目录，也**不**声称证明主机上其他位置未被写入；
- 快照只保存**数量、布尔与稳定摘要**，绝不保存正文、``quote``、绝对路径或密钥；
- 快照失败**不得**默认为安全：一律返回 ``unavailable`` 且 ``side_effect_free=False``，
  使正式指标无法据此通过。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.search.schema import FTS_TABLE_NAME

SNAPSHOT_OK = "ok"
SNAPSHOT_UNAVAILABLE = "unavailable"

# 变化检测使用的稳定字段标签（不含任何正文/路径/密钥）
SIDE_EFFECT_FIELDS = ("documents", "document_status", "chunks", "vectors", "fts")


def _digest(items: Iterable[str]) -> str:
    """对**已排序**的稳定标识做摘要；只吃 ID / 状态等非正文内容。"""
    hasher = hashlib.sha256()
    for item in sorted(items):
        hasher.update(item.encode("utf-8"))
        hasher.update(b"\x00")
    return hasher.hexdigest()[:32]


@dataclass(frozen=True)
class IsolationSnapshot:
    """隔离状态的**安全摘要**：仅数量与摘要，可写入报告。"""

    documents_count: int
    documents_digest: str
    status_digest: str
    chunks_count: int
    chunks_digest: str
    vectors_count: int
    vectors_digest: str
    fts_count: int
    fts_digest: str

    def changed_fields(self, other: "IsolationSnapshot") -> tuple[str, ...]:
        """返回发生变化的稳定字段标签；空元组表示无变化。"""
        changed: list[str] = []
        if (self.documents_count, self.documents_digest) != (
            other.documents_count,
            other.documents_digest,
        ):
            changed.append("documents")
        if self.status_digest != other.status_digest:
            changed.append("document_status")
        if (self.chunks_count, self.chunks_digest) != (other.chunks_count, other.chunks_digest):
            changed.append("chunks")
        if (self.vectors_count, self.vectors_digest) != (
            other.vectors_count,
            other.vectors_digest,
        ):
            changed.append("vectors")
        if (self.fts_count, self.fts_digest) != (other.fts_count, other.fts_digest):
            changed.append("fts")
        return tuple(changed)

    def as_safe_dict(self) -> dict[str, object]:
        """供报告使用的安全视图（只有数量与摘要）。"""
        return {
            "documents_count": self.documents_count,
            "documents_digest": self.documents_digest,
            "status_digest": self.status_digest,
            "chunks_count": self.chunks_count,
            "chunks_digest": self.chunks_digest,
            "vectors_count": self.vectors_count,
            "vectors_digest": self.vectors_digest,
            "fts_count": self.fts_count,
            "fts_digest": self.fts_digest,
        }


@dataclass(frozen=True)
class IsolationBaseline:
    """demo 导入完成后的基线；``unavailable`` 表示基线不可信。"""

    status: str
    snapshot: IsolationSnapshot | None = None
    reason: str | None = None

    @property
    def usable(self) -> bool:
        return self.status == SNAPSHOT_OK and self.snapshot is not None


@dataclass(frozen=True)
class SideEffectAssessment:
    """副作用判定结果；``unavailable`` 时 ``side_effect_free`` 恒为 ``False``。"""

    status: str
    side_effect_free: bool
    changed: tuple[str, ...] = ()
    reason: str | None = None

    @property
    def unavailable(self) -> bool:
        return self.status == SNAPSHOT_UNAVAILABLE


class IsolationSnapshotter(Protocol):
    """快照器：返回 :class:`IsolationSnapshot`；失败时抛异常（由判定函数转 ``unavailable``）。"""

    def __call__(self) -> IsolationSnapshot: ...


class VectorIdLister(Protocol):
    """只读枚举 collection 内**全部**向量 ID（不得写入）。"""

    def __call__(self) -> Iterable[str]: ...


def _document_state_item(row: object) -> str:
    """文档状态摘要项：``id|status|retrievable|activation_state|deleted_at``。

    只含 ID 与状态类字段，**不含**文件名、路径或任何正文。
    """
    document_id, status, retrievable, activation_state, deleted_at = row  # type: ignore[misc]
    return "|".join(
        (
            str(document_id),
            str(status or ""),
            "1" if retrievable else "0",
            str(activation_state or ""),
            "" if deleted_at is None else str(deleted_at),
        )
    )


def default_vector_id_lister(vectors: object) -> VectorIdLister:
    """基于向量库公开接口的只读枚举：直接列出 collection 内全部 ID。

    **不**通过 SQLite ``document_id`` 反查，因此孤立向量（``doc_id`` 已不存在于业务表）
    同样会被计入；枚举失败即抛异常，由调用方转为 ``unavailable``（不静默降级）。
    """

    def lister() -> Iterable[str]:
        collection = vectors.collection()  # type: ignore[attr-defined]
        payload = collection.get(include=[])
        return [str(item) for item in (payload.get("ids") or [])]

    return lister


def take_isolation_snapshot(
    session_factory: sessionmaker[Session],
    vectors: object,
    *,
    vector_ids: VectorIdLister | None = None,
) -> IsolationSnapshot:
    """采集隔离状态快照；异常向上抛出，调用方负责转为 ``unavailable``。

    ``vector_ids`` 可注入（测试替身）；缺省使用 :func:`default_vector_id_lister`，
    覆盖 collection 内全部向量 ID。
    """
    lister = vector_ids if vector_ids is not None else default_vector_id_lister(vectors)
    with session_factory() as session:
        document_rows = session.execute(
            text(
                "SELECT id, status, retrievable, activation_state, deleted_at FROM documents"
            )
        ).all()
        chunk_rows = session.execute(text("SELECT id FROM document_chunks")).all()
        fts_rows = session.execute(
            text(f"SELECT chunk_id FROM {FTS_TABLE_NAME}")
        ).all()

    document_ids = [str(row[0]) for row in document_rows]
    vector_ids_list = [str(vector_id) for vector_id in lister()]

    return IsolationSnapshot(
        documents_count=len(document_ids),
        documents_digest=_digest(document_ids),
        status_digest=_digest(_document_state_item(row) for row in document_rows),
        chunks_count=len(chunk_rows),
        chunks_digest=_digest(str(row[0]) for row in chunk_rows),
        vectors_count=len(vector_ids_list),
        vectors_digest=_digest(vector_ids_list),
        fts_count=len(fts_rows),
        fts_digest=_digest(str(row[0]) for row in fts_rows),
    )


def build_snapshotter(
    session_factory: sessionmaker[Session],
    vectors: object,
    *,
    vector_ids: VectorIdLister | None = None,
) -> IsolationSnapshotter:
    """把隔离 DB 与向量库绑成一个快照器（向量 ID 枚举方式可注入）。"""

    def snapshot() -> IsolationSnapshot:
        return take_isolation_snapshot(session_factory, vectors, vector_ids=vector_ids)

    return snapshot


def establish_baseline(snapshotter: IsolationSnapshotter) -> IsolationBaseline:
    """建立基线；失败只记录稳定 ``reason``（异常类名），不含任何正文。"""
    try:
        return IsolationBaseline(status=SNAPSHOT_OK, snapshot=snapshotter())
    except Exception as error:  # noqa: BLE001 - 稳定失败，不回显原文
        return IsolationBaseline(
            status=SNAPSHOT_UNAVAILABLE, reason=type(error).__name__
        )


def assess_side_effects(
    baseline: IsolationBaseline | None, snapshotter: IsolationSnapshotter
) -> SideEffectAssessment:
    """比较当前状态与基线。

    - 基线缺失 / 不可用，或本次快照失败 → ``unavailable`` 且 ``side_effect_free=False``
      （**不得默认安全**）；
    - 否则按 :meth:`IsolationSnapshot.changed_fields` 判定。
    """
    if baseline is None or not baseline.usable:
        return SideEffectAssessment(
            status=SNAPSHOT_UNAVAILABLE,
            side_effect_free=False,
            reason=(baseline.reason if baseline is not None else None) or "baseline_unavailable",
        )
    try:
        current = snapshotter()
    except Exception as error:  # noqa: BLE001 - 稳定失败，不回显原文
        return SideEffectAssessment(
            status=SNAPSHOT_UNAVAILABLE,
            side_effect_free=False,
            reason=type(error).__name__,
        )

    changed = baseline.snapshot.changed_fields(current)  # type: ignore[union-attr]
    return SideEffectAssessment(
        status=SNAPSHOT_OK,
        side_effect_free=not changed,
        changed=changed,
    )


__all__ = [
    "SIDE_EFFECT_FIELDS",
    "SNAPSHOT_OK",
    "SNAPSHOT_UNAVAILABLE",
    "IsolationBaseline",
    "IsolationSnapshot",
    "IsolationSnapshotter",
    "SideEffectAssessment",
    "VectorIdLister",
    "assess_side_effects",
    "build_snapshotter",
    "default_vector_id_lister",
    "establish_baseline",
    "take_isolation_snapshot",
]
