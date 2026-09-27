"""阶段 6 修复轮：冲突判定单元测试（BUG-6-05）。

只依赖 ``detect_conflicts`` 的纯函数语义，不读取任何演示 manifest / ground_truth。
"""

from __future__ import annotations

from app.chat.conflict import detect_conflicts

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
