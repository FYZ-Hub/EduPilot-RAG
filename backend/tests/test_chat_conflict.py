"""阶段 6 修复轮：冲突判定单元测试（BUG-6-05）。

只依赖 ``detect_conflicts`` 的纯函数语义，不读取任何演示 manifest / ground_truth。
"""

from __future__ import annotations

import pytest

from app.chat.conflict import (
    SIGNAL_CROSS_VERSION,
    SIGNAL_ROW_SLOT,
    SUPPRESSION_CONFLICT_INTENT_NOT_REQUESTED,
    SUPPRESSION_CROSS_VERSION_NOT_REQUESTED,
    SUPPRESSION_ROW_SLOT_NOT_REQUESTED,
    ConflictHint,
    conflict_field_digest,
    conflict_field_keys,
    conflict_suppression_reason,
    cross_version_intent,
    detect_conflicts,
    row_slot_intent,
    should_activate_conflict,
)
from app.documents.blocks import key_value_row

NAME_KEY = "毕业总学分"
REQUIRED_KEY = "专业必修"


def _item(index: int, text: str, version: str, doc_id: str):
    return (index, text, version, doc_id)


def test_different_values_across_versions_are_a_conflict() -> None:
    hint = detect_conflicts(
        [
            _item(1, "毕业总学分：155.0 学分。", "2025.1", "d1"),
            _item(2, "毕业总学分：160.0 学分。", "2026.1", "d2"),
        ]
    )
    assert hint is not None, "不同版本、不同取值必须判为跨版本冲突"
    assert hint.indices == (1, 2)
    assert NAME_KEY in hint.note


def test_same_value_across_versions_is_not_a_conflict() -> None:
    hint = detect_conflicts(
        [
            _item(1, "毕业总学分：160.0 学分。", "2025.1", "d1"),
            _item(2, "毕业总学分：160.0 学分。", "2026.1", "d2"),
        ]
    )
    assert hint is None, "不同版本但取值相同不得判为冲突"


def test_same_version_repeated_field_is_not_a_cross_version_conflict() -> None:
    hint = detect_conflicts(
        [
            _item(1, "毕业总学分：155.0 学分。", "2025.1", "d1"),
            _item(2, "毕业总学分：155.0 学分。", "2025.1", "d1"),
        ]
    )
    assert hint is None


def test_repeated_field_inside_one_chunk_does_not_create_a_false_conflict() -> None:
    text = "毕业总学分：160.0 学分。\n毕业总学分：160.0 学分。"
    assert detect_conflicts([_item(1, text, "2026.1", "d1")]) is None


def test_metadata_fields_are_never_treated_as_conflicts() -> None:
    hint = detect_conflicts(
        [
            _item(1, "文档版本：2025.1　生效日期：2025-09-01", "2025.1", "d1"),
            _item(2, "文档版本：2026.1　生效日期：2026-09-01", "2026.1", "d2"),
        ]
    )
    assert hint is None, "版本/日期等元数据按定义就会不同，不属于内容冲突"


def test_conflict_result_is_stable_and_deduplicated_under_reordering() -> None:
    first = _item(1, "毕业总学分：155.0 学分。", "2025.1", "d1")
    second = _item(2, "毕业总学分：160.0 学分。", "2026.1", "d2")
    third = _item(3, f"{REQUIRED_KEY}：58.0 学分", "2025.1", "d1")
    fourth = _item(4, f"{REQUIRED_KEY}：60.0 学分", "2026.1", "d2")

    forward = detect_conflicts([first, second, third, fourth])
    backward = detect_conflicts([fourth, third, second, first])

    assert forward is not None and backward is not None
    assert forward.indices == (1, 2, 3, 4)
    assert backward.indices == forward.indices
    assert forward.note == backward.note
    assert len(set(forward.indices)) == len(forward.indices), "indices 必须去重"


def test_three_versions_keep_indices_sorted_and_deduplicated() -> None:
    hint = detect_conflicts(
        [
            _item(3, "毕业总学分：150.0 学分。", "2027.1", "d3"),
            _item(1, "毕业总学分：155.0 学分。", "2025.1", "d1"),
            _item(2, "毕业总学分：160.0 学分。", "2026.1", "d2"),
        ]
    )
    assert hint is not None
    assert hint.indices == (1, 2, 3)


def test_schedule_row_conflict_still_detected_across_chunks() -> None:
    """保留同文档跨切片课表冲突检测（不在本修复中回退）。"""
    header = "星期 | 节次 | 时间 | 课程代码 | 课程名称"
    first = f"{header}\n星期二 | 第3-4节 | 10:00-11:40 | QM-GE101 | 大学写作"
    second = f"{header}\n星期二 | 第3-4节 | 10:00-11:40 | QM-CS201 | 数据结构"

    hint = detect_conflicts(
        [_item(1, first, "2026.1", "sched"), _item(2, second, "2026.1", "sched")]
    )
    assert hint is not None
    assert hint.indices == (1, 2)
    assert "重复槽位" in hint.note


def test_no_evidence_yields_no_conflict() -> None:
    assert detect_conflicts([]) is None


# --- 稳定诊断（供 9B 安全诊断；不保存字段值或证据正文） -----------------------


def test_conflict_hint_exposes_stable_diagnostics_without_values() -> None:
    hint = detect_conflicts(
        [
            _item(1, "毕业总学分：155.0 学分。", "2025.1", "d1"),
            _item(2, "毕业总学分：160.0 学分。", "2026.1", "d2"),
        ]
    )
    assert hint is not None
    assert hint.signal_types == (SIGNAL_CROSS_VERSION,)
    assert hint.conflict_indices == hint.indices == (1, 2)
    assert hint.field_count == 1
    assert len(hint.field_digest) == 64
    # 诊断只保存哈希与计数：字段取值不得出现在其中
    assert "155" not in hint.field_digest and "160" not in hint.field_digest
    assert "毕业总学分" not in hint.field_digest


def test_row_slot_conflict_reports_row_signal() -> None:
    header = "星期 | 节次 | 时间 | 课程代码 | 课程名称"
    first = f"{header}\n星期二 | 第3-4节 | 10:00-11:40 | QM-GE101 | 大学写作"
    second = f"{header}\n星期二 | 第3-4节 | 10:00-11:40 | QM-CS201 | 数据结构"
    hint = detect_conflicts(
        [_item(1, first, "2026.1", "sched"), _item(2, second, "2026.1", "sched")]
    )
    assert hint is not None
    assert hint.signal_types == (SIGNAL_ROW_SLOT,)
    assert hint.field_count == 1
    assert len(hint.field_digest) == 64


def test_conflict_diagnostics_are_order_stable_and_deduplicated() -> None:
    a = _item(1, "毕业总学分：155.0 学分。", "2025.1", "d1")
    b = _item(2, "毕业总学分：160.0 学分。", "2026.1", "d2")
    c = _item(3, f"{REQUIRED_KEY}：58.0 学分", "2025.1", "d1")
    d = _item(4, f"{REQUIRED_KEY}：60.0 学分", "2026.1", "d2")

    forward = detect_conflicts([a, b, c, d])
    backward = detect_conflicts([d, c, b, a])

    assert forward is not None and backward is not None
    assert forward.field_count == backward.field_count == 2
    assert forward.field_digest == backward.field_digest
    assert forward.signal_types == backward.signal_types == (SIGNAL_CROSS_VERSION,)


def test_conflict_field_keys_are_deterministic_and_process_local() -> None:
    a = _item(1, "毕业总学分：155.0 学分。", "2025.1", "d1")
    b = _item(2, "毕业总学分：160.0 学分。", "2026.1", "d2")
    assert conflict_field_keys([a, b]) == (NAME_KEY,)
    assert conflict_field_keys([b, a]) == (NAME_KEY,)
    assert conflict_field_keys([]) == ()


def test_different_row_slots_produce_different_digests() -> None:
    header = "星期 | 节次 | 时间 | 课程代码 | 课程名称"
    tue_a = f"{header}\n星期二 | 第3-4节 | 10:00-11:40 | QM-GE101 | 大学写作"
    tue_b = f"{header}\n星期二 | 第3-4节 | 10:00-11:40 | QM-CS201 | 数据结构"
    wed_a = f"{header}\n星期三 | 第5-6节 | 14:00-15:40 | QM-CS301 | 数据库"
    wed_b = f"{header}\n星期三 | 第5-6节 | 14:00-15:40 | QM-CS302 | 操作系统"

    first = detect_conflicts([_item(1, tue_a, "2026.1", "s1"), _item(2, tue_b, "2026.1", "s1")])
    second = detect_conflicts([_item(1, wed_a, "2026.1", "s2"), _item(2, wed_b, "2026.1", "s2")])

    assert first is not None and second is not None
    assert first.field_count == second.field_count == 1
    assert first.field_digest != second.field_digest, "不同 row-slot 集合必须得到不同摘要"


def test_conflict_field_digest_hashes_real_keys_not_counts() -> None:
    """摘要必须随键内容变化，而不能只反映数量。"""
    one = conflict_field_digest([], ["星期二|第3-4节"])
    other_same_count = conflict_field_digest([], ["星期三|第5-6节"])
    assert len(one) == len(other_same_count) == 64
    assert one != other_same_count
    # 与「两个键」也不同
    assert conflict_field_digest([], ["星期二|第3-4节", "星期三|第5-6节"]) != one


def test_conflict_field_digest_normalizes_and_never_stores_raw_keys() -> None:
    assert conflict_field_digest([" 毕业总学分 "], []) == conflict_field_digest([NAME_KEY], [])
    digest = conflict_field_digest([NAME_KEY], [])
    assert len(digest) == 64
    assert NAME_KEY not in digest


# --- 跨版本比较意图与冲突激活 ------------------------------------------------


def _cross_hint() -> ConflictHint:
    hint = detect_conflicts(
        [
            _item(1, "毕业总学分：155.0 学分。", "2025.1", "d1"),
            _item(2, "毕业总学分：160.0 学分。", "2026.1", "d2"),
        ]
    )
    assert hint is not None
    return hint


def _row_hint() -> ConflictHint:
    header = "星期 | 节次 | 时间 | 课程代码 | 课程名称"
    first = f"{header}\n星期二 | 第3-4节 | 10:00-11:40 | QM-GE101 | 大学写作"
    second = f"{header}\n星期二 | 第3-4节 | 10:00-11:40 | QM-CS201 | 数据结构"
    hint = detect_conflicts(
        [_item(1, first, "2026.1", "sched"), _item(2, second, "2026.1", "sched")]
    )
    assert hint is not None
    return hint


@pytest.mark.parametrize(
    "question",
    [
        "2025.1 和 2026.1 两个版本的总学分有什么差异？",
        "这两份文件的毕业总学分是否一致？",
        "不同版本的培养方案有什么区别？",
        "请对比这两份资料",
        "两个文件的数据是否冲突",
        "跨版本的总学分是多少",
        "是否存在不同版本？",
        "不同版本的毕业总学分有什么差异？",
        "不同文件中的规定是否一致？",
        "两个文档是否冲突？",
        "培养方案 2025.1 和 2026.1 有什么区别？",
    ],
)
def test_cross_version_intent_recognizes_explicit_comparison(question) -> None:
    assert cross_version_intent(question) is True


@pytest.mark.parametrize(
    "question",
    [
        "2026.1 版本的总学分是多少？",
        "毕业总学分是多少？",
        "2026 年的培养方案要求多少学分？",
        "专业必修有哪些课程？",
        "",
        # 通用比较词但无版本/文件范围，且无两个 YYYY.N 版本标记 → 不构成意图
        "2026-2027 学年第一学期期末考试周与 QM-CS201 的考试日期是否一致？",
        "选课管理办法如何处理两门课程的上课时间冲突？",
        "两门课程有什么区别？",
        "A 与 B 比较",
        "冲突",
        "是否一致",
        "差异",
        "2026-2027 学年",
    ],
)
def test_cross_version_intent_rejects_plain_questions(question) -> None:
    assert cross_version_intent(question) is False


def test_should_activate_conflict_requires_intent_for_cross_version_only() -> None:
    hint = _cross_hint()
    assert hint.signal_types == (SIGNAL_CROSS_VERSION,)
    assert should_activate_conflict(hint, "不同版本的总学分是否一致？") is True
    assert should_activate_conflict(hint, "毕业总学分是多少？") is False
    assert should_activate_conflict(None, "不同版本是否一致？") is False


ROW_SLOT_QUESTION = "课表中星期二第3-4节有哪些课程？"
ROW_SLOT_MARKER_QUESTION = "课表中是否存在同一时间段安排两门课程？"


def test_row_slot_only_activation_requires_row_slot_intent() -> None:
    """row_slot-only 只能用 row_slot 意图激活，不能用其它问题误激活。"""
    row = _row_hint()
    assert row.signal_types == (SIGNAL_ROW_SLOT,)
    assert should_activate_conflict(row, ROW_SLOT_QUESTION) is True
    assert should_activate_conflict(row, ROW_SLOT_MARKER_QUESTION) is True
    assert should_activate_conflict(row, "毕业总学分是多少？") is False
    assert should_activate_conflict(row, "不同版本的总学分是否一致？") is False
    assert should_activate_conflict(None, ROW_SLOT_QUESTION) is False


def test_mixed_signals_require_any_matching_intent() -> None:
    """混合信号：满足任一对应意图即激活，两类都不相关则抑制。"""
    mixed = ConflictHint(
        indices=(1, 2),
        note="x",
        signal_types=(SIGNAL_CROSS_VERSION, SIGNAL_ROW_SLOT),
    )
    assert should_activate_conflict(mixed, ROW_SLOT_QUESTION) is True
    assert should_activate_conflict(mixed, "不同版本的总学分是否一致？") is True
    assert should_activate_conflict(mixed, "毕业总学分是多少？") is False


def test_suppression_reason_is_per_signal() -> None:
    row = _row_hint()
    assert (
        conflict_suppression_reason(row, "毕业总学分是多少？")
        == SUPPRESSION_ROW_SLOT_NOT_REQUESTED
    )
    assert conflict_suppression_reason(row, ROW_SLOT_QUESTION) is None

    mixed = ConflictHint(
        indices=(1, 2),
        note="x",
        signal_types=(SIGNAL_CROSS_VERSION, SIGNAL_ROW_SLOT),
    )
    assert (
        conflict_suppression_reason(mixed, "毕业总学分是多少？")
        == SUPPRESSION_CONFLICT_INTENT_NOT_REQUESTED
    )
    assert conflict_suppression_reason(mixed, ROW_SLOT_QUESTION) is None

    cross = _cross_hint()
    assert (
        conflict_suppression_reason(cross, "毕业总学分是多少？")
        == SUPPRESSION_CROSS_VERSION_NOT_REQUESTED
    )
    assert conflict_suppression_reason(cross, "不同版本是否一致？") is None
    assert conflict_suppression_reason(None, "毕业总学分是多少？") is None


@pytest.mark.parametrize(
    "question",
    [
        "课表中是否存在同一时间段安排两门课程？",
        "课表中星期二第3-4节有哪些课程？",
        "这学期有没有重复排课？",
        "课表冲突在哪里？",
        "请核对课程安排冲突",
        "排课冲突的地方有哪些？",
        "课表里星期三第5-6节安排了哪些课程？",
        "排课中周一的第1节有哪些课？",
    ],
)
def test_row_slot_intent_recognizes_explicit_scheduling_conflict(question) -> None:
    assert row_slot_intent(question) is True


@pytest.mark.parametrize(
    "question",
    [
        "选课管理办法如何处理两门课程的上课时间冲突？",
        "某课程在课表中的上课时间是什么？",
        "毕业总学分是多少？",
        "期末考试安排在什么时候？",
        "下学期校历是怎样的？",
        "上课时间是什么时候？",
        "有哪些课程？",
        "",
    ],
)
def test_row_slot_intent_rejects_single_word_or_unrelated(question) -> None:
    """「冲突」「时间」「课表」「上课时间」等单词单独出现不得构成排课冲突意图。"""
    assert row_slot_intent(question) is False


# --- key_value_row 真实表格行（app.documents.blocks） ------------------------

_SHEET_HEADER = ["星期", "节次", "时间", "课程代码", "课程名称"]


def _schedule_row(weekday, period, time, code, name) -> str:
    """用生产解析器的真实格式构造一行课表：``字段: 值；字段: 值``。"""
    return key_value_row(_SHEET_HEADER, [weekday, period, time, code, name])


def test_key_value_row_builds_semicolon_joined_pairs() -> None:
    """确认 key_value_row 的真实格式，作为解析目标的单一事实来源。"""
    assert _schedule_row("星期二", "第3-4节", "10:00-11:40", "QM-CS201", "数据结构") == (
        "星期: 星期二；节次: 第3-4节；时间: 10:00-11:40；"
        "课程代码: QM-CS201；课程名称: 数据结构"
    )


def test_key_value_row_same_block_conflict_is_row_slot() -> None:
    """同一 chunk 内两行同槽位、不同课程 → row_slot，且 evidence 编号去重为单个。"""
    text = "\n".join(
        [
            _schedule_row("星期二", "第3-4节", "10:00-11:40", "QM-GE101", "大学写作"),
            _schedule_row("星期二", "第3-4节", "10:00-11:40", "QM-CS201", "数据结构"),
        ]
    )
    hint = detect_conflicts([_item(1, text, "2026.1", "sched")])
    assert hint is not None
    assert hint.signal_types == (SIGNAL_ROW_SLOT,)
    assert hint.indices == (1,), "同块冲突必须去重为单个编号"


def test_key_value_row_cross_block_same_document_conflict() -> None:
    first = _schedule_row("星期二", "第3-4节", "10:00-11:40", "QM-GE101", "大学写作")
    second = _schedule_row("星期二", "第3-4节", "10:00-11:40", "QM-CS201", "数据结构")
    hint = detect_conflicts(
        [_item(1, first, "2026.1", "sched"), _item(2, second, "2026.1", "sched")]
    )
    assert hint is not None
    assert hint.indices == (1, 2)
    assert hint.signal_types == (SIGNAL_ROW_SLOT,)


def test_key_value_row_identical_rows_are_not_a_conflict() -> None:
    row = _schedule_row("星期二", "第3-4节", "10:00-11:40", "QM-CS201", "数据结构")
    assert detect_conflicts([_item(1, row, "2026.1", "sched"), _item(2, row, "2026.1", "sched")]) is None


def test_key_value_row_different_slots_are_not_a_conflict() -> None:
    tue = _schedule_row("星期二", "第3-4节", "10:00-11:40", "QM-CS201", "数据结构")
    wed = _schedule_row("星期三", "第5-6节", "14:00-15:40", "QM-CS301", "数据库")
    assert detect_conflicts([_item(1, tue, "2026.1", "s"), _item(2, wed, "2026.1", "s")]) is None


def test_key_value_row_same_row_different_documents_are_not_a_conflict() -> None:
    row = _schedule_row("星期二", "第3-4节", "10:00-11:40", "QM-CS201", "数据结构")
    assert detect_conflicts([_item(1, row, "2026.1", "d1"), _item(2, row, "2026.1", "d2")]) is None


def test_key_value_row_accepts_chinese_and_english_punctuation() -> None:
    fullwidth = "星期：星期二；节次：第3-4节；课程：数据结构"
    ascii_form = "星期: 星期二; 节次: 第3-4节; 课程: 数据结构"
    for text in (fullwidth, ascii_form):
        other = text.replace("数据结构", "大学写作")
        hint = detect_conflicts(
            [_item(1, text, "2026.1", "s"), _item(2, other, "2026.1", "s")]
        )
        assert hint is not None and hint.signal_types == (SIGNAL_ROW_SLOT,), text


def test_key_value_row_requires_at_least_three_pairs() -> None:
    """只有 2 个字段值对不构成 key-value 表格行，不得误报。"""
    left = "星期: 星期二；节次: 第3-4节"
    right = "星期: 星期二；节次: 第5-6节"
    assert detect_conflicts([_item(1, left, "2026.1", "s"), _item(2, right, "2026.1", "s")]) is None


def test_pipe_row_detection_is_not_regressed_by_key_value_support() -> None:
    header = "星期 | 节次 | 时间 | 课程代码 | 课程名称"
    first = f"{header}\n星期二 | 第3-4节 | 10:00-11:40 | QM-GE101 | 大学写作"
    second = f"{header}\n星期二 | 第3-4节 | 10:00-11:40 | QM-CS201 | 数据结构"
    hint = detect_conflicts(
        [_item(1, first, "2026.1", "sched"), _item(2, second, "2026.1", "sched")]
    )
    assert hint is not None
    assert hint.indices == (1, 2)
    assert hint.signal_types == (SIGNAL_ROW_SLOT,)
