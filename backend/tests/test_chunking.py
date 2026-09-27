"""确定性切片：稳定 chunk 集合与 chunk_id、边界、定位保留。"""

from __future__ import annotations

import unicodedata

import pytest

from tests.conftest import DEMO_DATASET_PATH
from app import constants
from app.demo.manifest import load_manifest
from app.documents.chunking import ChunkSourceBlock, chunk_blocks, compute_chunk_id
from app.documents.chunking.chunker import normalize_chunk_text
from app.documents.fingerprint import CHUNKER_VERSION, PARSER_VERSION
from app.documents.parsing import parse_document

TARGET = 550
OVERLAP = 100


def _blocks(*specs) -> list[ChunkSourceBlock]:
    return [ChunkSourceBlock(**spec) for spec in specs]


def _paragraph(index: int, text: str, **overrides) -> dict:
    values = {
        "block_index": index,
        "text": text,
        "block_type": "paragraph",
    }
    values.update(overrides)
    return values


# --- 稳定性 -----------------------------------------------------------------


def test_same_input_produces_identical_chunks_and_ids() -> None:
    source = _blocks(
        _paragraph(0, "第一条 本规定适用于启明大学全体本科生。"),
        _paragraph(1, "第二条 课程代码 QM-CS201 的学分为 4.0 学分。"),
    )
    first = chunk_blocks(source, target_chars=TARGET, overlap_chars=OVERLAP)
    second = chunk_blocks(source, target_chars=TARGET, overlap_chars=OVERLAP)

    assert first == second

    checksum = "a" * 64
    first_ids = [
        compute_chunk_id(
            document_checksum=checksum,
            locator=draft.locator,
            chunk_index=draft.chunk_index,
            parser_version=PARSER_VERSION,
            chunker_version=CHUNKER_VERSION,
        )
        for draft in first
    ]
    second_ids = [
        compute_chunk_id(
            document_checksum=checksum,
            locator=draft.locator,
            chunk_index=draft.chunk_index,
            parser_version=PARSER_VERSION,
            chunker_version=CHUNKER_VERSION,
        )
        for draft in second
    ]
    assert first_ids == second_ids
    assert len(set(first_ids)) == len(first_ids)


def test_chunk_id_changes_with_each_input_component() -> None:
    base = {
        "document_checksum": "a" * 64,
        "locator": {"page_number": 1, "block_start": 0, "block_end": 0},
        "chunk_index": 0,
        "parser_version": "1.0.0",
        "chunker_version": "1.0.0",
    }
    reference = compute_chunk_id(**base)

    assert compute_chunk_id(**{**base, "document_checksum": "b" * 64}) != reference
    assert compute_chunk_id(**{**base, "chunk_index": 1}) != reference
    assert compute_chunk_id(**{**base, "parser_version": "1.0.1"}) != reference
    assert compute_chunk_id(**{**base, "chunker_version": "1.0.1"}) != reference
    assert (
        compute_chunk_id(**{**base, "locator": {"page_number": 2, "block_start": 0, "block_end": 0}})
        != reference
    )


def test_normalisation_is_nfc_and_deterministic() -> None:
    decomposed = "e\u0301"
    assert normalize_chunk_text(decomposed) == unicodedata.normalize("NFC", decomposed)
    assert normalize_chunk_text("a\t\tb\u3000c") == "a b c"


def test_document_identity_scopes_chunk_ids_across_sources() -> None:
    """同一份文件同时存在于 demo 与 upload 时，chunk_id 必须不同（否则会跨来源复用向量）。"""
    from app.documents.chunking import document_checksum

    file_sha = "a" * 64
    locator = {"page_number": 1, "sheet_name": None, "row_start": None, "row_end": None,
               "section_title": "一、总则", "block_start": 0, "block_end": 1}

    demo = document_checksum(
        source_type="demo", source_key="2026.1:corpus/01-培养方案.pdf", file_sha256=file_sha
    )
    upload = document_checksum(
        source_type="upload", source_key=f"upload:{file_sha}", file_sha256=file_sha
    )
    assert demo != upload

    demo_id = compute_chunk_id(
        document_checksum=demo,
        locator=locator,
        chunk_index=0,
        parser_version=PARSER_VERSION,
        chunker_version=CHUNKER_VERSION,
    )
    upload_id = compute_chunk_id(
        document_checksum=upload,
        locator=locator,
        chunk_index=0,
        parser_version=PARSER_VERSION,
        chunker_version=CHUNKER_VERSION,
    )
    assert demo_id != upload_id


# --- 长度与重叠 -------------------------------------------------------------


def test_target_length_and_overlap_are_respected() -> None:
    sentences = "".join(f"第{index}条 这是用于测试长度与重叠的句子内容。" for index in range(1, 61))
    source = _blocks(_paragraph(0, sentences, page_number=1, section_title="第一章 总则"))

    drafts = chunk_blocks(source, target_chars=TARGET, overlap_chars=OVERLAP)
    assert len(drafts) > 1
    for draft in drafts:
        assert len(draft.text) <= TARGET

    # 同一定位组内的相邻 chunk 必须共享上下文（≈ overlap 字符）
    for previous, current in zip(drafts, drafts[1:]):
        overlap = len(set(previous.text) & set(current.text))
        assert overlap > 0
        assert previous.locator["page_number"] == current.locator["page_number"]


def test_short_paragraph_produces_single_small_chunk() -> None:
    drafts = chunk_blocks(
        _blocks(_paragraph(0, "短句。")), target_chars=TARGET, overlap_chars=OVERLAP
    )
    assert len(drafts) == 1
    assert drafts[0].text == "短句。"
    assert len(drafts[0].text) < TARGET


def test_long_paragraph_is_split_without_breaking_tokens() -> None:
    token = "QM-CS201"
    body = "".join(f"课程 {token} 的考核说明需要足够长以保证必须硬切。 " for _ in range(40))
    drafts = chunk_blocks(
        _blocks(_paragraph(0, body, page_number=3)), target_chars=TARGET, overlap_chars=OVERLAP
    )
    assert len(drafts) > 1
    for draft in drafts:
        assert len(draft.text) <= TARGET
        # 每个 chunk 内出现的课程代码都必须是完整的
        assert draft.text.count(token) == draft.text.count(f"{token} ") + draft.text.count(f"{token}。")
    assert token in drafts[0].text


def test_empty_blocks_are_skipped() -> None:
    drafts = chunk_blocks(
        _blocks(_paragraph(0, "   "), _paragraph(1, "第一条 有效内容。")),
        target_chars=TARGET,
        overlap_chars=OVERLAP,
    )
    assert len(drafts) == 1
    assert drafts[0].text == "第一条 有效内容。"
    assert drafts[0].locator["block_start"] == 1


def test_no_chunkable_text_raises_safe_error() -> None:
    from app.core.errors import ApiError

    with pytest.raises(ApiError) as error:
        chunk_blocks(_blocks(_paragraph(0, "   ")), target_chars=TARGET, overlap_chars=OVERLAP)
    assert error.value.code == "DOCUMENT_CHUNK_FAILED"


# --- 语义边界与定位 ---------------------------------------------------------


def test_chinese_sentence_boundaries_are_preferred() -> None:
    text = "第一句内容。" * 20
    drafts = chunk_blocks(
        _blocks(_paragraph(0, text)), target_chars=120, overlap_chars=20
    )
    for draft in drafts[:-1]:
        assert draft.text.endswith("。")


def test_different_locators_are_never_merged() -> None:
    source = _blocks(
        _paragraph(0, "第一页内容。", page_number=1, section_title="一、总则"),
        _paragraph(1, "第二页内容。", page_number=2, section_title="一、总则"),
        _paragraph(2, "另一章节内容。", page_number=2, section_title="二、附则"),
    )
    drafts = chunk_blocks(source, target_chars=TARGET, overlap_chars=OVERLAP)
    assert len(drafts) == 3
    assert [draft.locator["page_number"] for draft in drafts] == [1, 2, 2]
    assert [draft.locator["section_title"] for draft in drafts] == ["一、总则", "一、总则", "二、附则"]


def test_table_rows_keep_header_and_never_split_rows() -> None:
    source = _blocks(
        {
            "block_index": 0,
            "text": "课程代码 | 课程名称 | 学分",
            "block_type": "table_header",
            "page_number": 1,
            "row_start": 1,
            "row_end": 1,
        },
        *[
            {
                "block_index": index,
                "text": f"课程代码: QM-CS2{index:02d}；课程名称: 课程{index}；学分: 3.0",
                "block_type": "table_row",
                "page_number": 1,
                "row_start": index + 1,
                "row_end": index + 1,
            }
            for index in range(1, 21)
        ],
    )
    drafts = chunk_blocks(source, target_chars=TARGET, overlap_chars=OVERLAP)
    assert len(drafts) > 1
    for draft in drafts:
        # 每个含表格行的 chunk 都带表头，且每一行的键值对完整
        assert "课程代码 | 课程名称 | 学分" in draft.text
        for line in draft.text.splitlines():
            if line.startswith("课程代码: QM"):
                assert "学分: 3.0" in line


def test_locator_preserves_rows_and_block_range() -> None:
    source = _blocks(
        {
            "block_index": 0,
            "text": "课程代码 | 学分",
            "block_type": "table_header",
            "sheet_name": "课程记录",
            "row_start": 2,
            "row_end": 2,
        },
        {
            "block_index": 1,
            "text": "课程代码: QM-CS201；学分: 4.0",
            "block_type": "table_row",
            "sheet_name": "课程记录",
            "row_start": 3,
            "row_end": 3,
        },
    )
    drafts = chunk_blocks(source, target_chars=TARGET, overlap_chars=OVERLAP)
    locator = drafts[0].locator
    assert locator["sheet_name"] == "课程记录"
    assert locator["row_start"] == 2
    assert locator["row_end"] == 3
    assert locator["block_start"] == 0
    assert locator["block_end"] == 1


def test_course_codes_dates_and_credits_stay_intact() -> None:
    text = (
        "QM-CS201 数据结构的学分为 4.0 学分，开课学期为 2026-2027-1，"
        "考试日期为 2026-01-12 08:00-09:40。"
    )
    drafts = chunk_blocks(
        _blocks(_paragraph(0, text)), target_chars=TARGET, overlap_chars=OVERLAP
    )
    joined = "".join(draft.text for draft in drafts)
    for token in ("QM-CS201", "4.0", "2026-2027-1", "2026-01-12", "08:00-09:40"):
        assert token in joined


# --- 固化语料 ---------------------------------------------------------------


def test_all_demo_documents_yield_stable_chunks() -> None:
    manifest = load_manifest(DEMO_DATASET_PATH, "2026.1")
    assert len(manifest.documents) == 15

    for document in manifest.documents:
        blocks = parse_document(DEMO_DATASET_PATH / document.path, document.file_type)
        source = [
            ChunkSourceBlock(
                block_index=index,
                text=block.text,
                block_type=block.block_type,
                page_number=block.page_number,
                sheet_name=block.sheet_name,
                row_start=block.row_start,
                row_end=block.row_end,
                section_title=block.section_title,
            )
            for index, block in enumerate(blocks)
        ]
        first = chunk_blocks(source, target_chars=TARGET, overlap_chars=OVERLAP)
        second = chunk_blocks(source, target_chars=TARGET, overlap_chars=OVERLAP)
        assert first == second, document.path
        assert first, document.path
        for draft in first:
            assert draft.text.strip(), document.path
            # 目标长度 550；表格行是原子单位不可拆散，允许在规格上限 700 字内超出
            assert len(draft.text) <= 700, document.path

        if document.file_type == constants.FILE_TYPE_PDF:
            assert all(draft.locator["page_number"] for draft in first), document.path
        if document.file_type == constants.FILE_TYPE_XLSX:
            assert all(draft.locator["sheet_name"] for draft in first), document.path
