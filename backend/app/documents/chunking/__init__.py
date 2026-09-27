"""确定性切片：DocumentChunker 与稳定 chunk_id。"""

from app.documents.chunking.chunker import (
    CHUNKER_NAME,
    ChunkDraft,
    ChunkSourceBlock,
    ChunkUnit,
    chunk_blocks,
    normalize_chunk_text,
)
from app.documents.chunking.ids import canonical_locator, compute_chunk_id, document_checksum

__all__ = [
    "CHUNKER_NAME",
    "ChunkDraft",
    "ChunkSourceBlock",
    "ChunkUnit",
    "canonical_locator",
    "chunk_blocks",
    "compute_chunk_id",
    "document_checksum",
    "normalize_chunk_text",
]
