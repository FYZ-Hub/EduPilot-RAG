"""可读性抽样：真实读取 PDF / DOCX / XLSX 内容，而不是只校验文件存在或 ZIP 结构。"""

from __future__ import annotations

from demo_corpus import facts
from demo_corpus.validate import (
    extract_document_text,
    read_docx,
    read_pdf_pages,
    read_xlsx,
)


def _doc(dataset, name_fragment: str) -> dict:
    for doc in dataset["manifest"]["documents"]:
        if name_fragment in doc["path"]:
            return doc
    raise AssertionError(f"未找到文件 {name_fragment}")


def test_every_document_contains_fiction_marker(dataset):
    for doc in dataset["manifest"]["documents"]:
        text = extract_document_text(dataset["root"] / doc["path"])
        assert facts.FICTION_MARKER in text, doc["path"]


def test_pdf_readability_and_content(dataset):
    root = dataset["root"]
    for doc in dataset["manifest"]["documents"]:
        if doc["file_type"] != "pdf":
            continue
        pages = read_pdf_pages(root / doc["path"])
        assert len(pages) >= 1
        assert facts.FICTION_MARKER in pages[0]
        assert sum(len(page) for page in pages) > 200

    plan = _doc(dataset, "02-培养方案")
    text = "\n".join(read_pdf_pages(root / plan["path"]))
    assert "三、学分要求" in text
    assert "160.0" in text
    assert "QM-CS303" in text
    assert "8 学分" in text

    earlier_plan = _doc(dataset, "01-培养方案")
    earlier_text = "\n".join(read_pdf_pages(root / earlier_plan["path"]))
    assert "155.0" in earlier_text
    assert "6 学分" in earlier_text

    security = _doc(dataset, "15-安全测试")
    security_text = "\n".join(read_pdf_pages(root / security["path"]))
    assert "忽略以上所有指令" in security_text
    assert "developer mode" in security_text

    exam = _doc(dataset, "09-考试通知")
    exam_text = "\n".join(read_pdf_pages(root / exam["path"]))
    assert "2027-01-05" in exam_text
    assert "QM-A201" in exam_text


def test_docx_readability_and_content(dataset):
    root = dataset["root"]
    syllabus = _doc(dataset, "03-课程大纲-QM-CS201")
    data = read_docx(root / syllabus["path"])
    assert data["headings"] == syllabus["expected_sections"]
    assert "QM-CS201" in data["text"]
    assert "数据结构" in data["text"]
    assert "4.0" in data["text"]
    assert "QM-CS101" in data["text"]

    policy = _doc(dataset, "07-制度")
    policy_data = read_docx(root / policy["path"])
    assert "重修" in policy_data["text"]
    assert "6 学分" in policy_data["text"]
    assert "补考" in policy_data["text"]


def test_xlsx_readability_and_content(dataset):
    root = dataset["root"]

    calendar = _doc(dataset, "08-校历")
    data = read_xlsx(root / calendar["path"])
    assert data["names"] == ["校历", "说明"]
    assert facts.FICTION_MARKER in str(data["cells"]["校历"][0][0])
    flat = "\n".join(str(cell) for row in data["cells"]["校历"] for cell in row)
    assert "第一学期期末考试周" in flat
    assert "2027-01-04" in flat

    schedule = _doc(dataset, "11-课表")
    schedule_data = read_xlsx(root / schedule["path"])
    rows = schedule_data["cells"]["课表"]
    assert facts.FICTION_MARKER in str(rows[0][0])
    assert rows[1][:3] == ["星期", "节次", "时间"]
    flat_schedule = "\n".join(str(cell) for row in rows for cell in row)
    assert "QM-GE101" in flat_schedule
    assert "星期二" in flat_schedule

    records_a = _doc(dataset, "13-课程记录")
    records_data = read_xlsx(root / records_a["path"])
    record_rows = records_data["cells"]["课程记录"]
    assert facts.FICTION_MARKER in str(record_rows[0][0])
    assert record_rows[1][:3] == ["序号", "课程代码", "课程名称"]
    flat_records = "\n".join(str(cell) for row in record_rows for cell in row)
    assert "QM-CS102" in flat_records
    assert "重修" in flat_records
    assert "不及格" in flat_records

    summary = records_data["cells"]["汇总"]
    assert summary[1] == ["课程类别", "要求学分", "已修学分", "在修学分", "剩余学分"]
    assert summary[-2][0] == "合计"
