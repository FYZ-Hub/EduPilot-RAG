"""语料校验与隐私扫描（可读性抽样、manifest 一致性、ground truth 校验）。

同时被 ``generate_demo_corpus.py --publish`` 和 pytest 使用：
只有校验通过的数据集才允许固化到仓库 ``demo/``。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from docx import Document
from openpyxl import load_workbook
from pypdf import PdfReader

from . import facts

GROUND_TRUTH_FIELDS = [
    "id",
    "category",
    "question",
    "expected_answer_facts",
    "expected_source_paths",
    "expected_locators",
    "should_refuse",
    "conflict_expected",
    "planning_input",
    "expected_planning_result",
]

REQUIRED_CATEGORIES = {
    "single_doc",
    "cross_doc",
    "course_code",
    "exam_date",
    "rule_calculation",
    "version_conflict",
    "unanswerable",
    "prompt_injection",
    "planning",
}

MIN_GROUND_TRUTH = 50

# ---------------------------------------------------------------------------
# 隐私扫描规则
# ---------------------------------------------------------------------------

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
MOBILE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
LANDLINE_RE = re.compile(r"(?<!\d)0\d{2,3}-\d{7,8}(?!\d)")
ID_CARD_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
DOMAIN_RE = re.compile(r"\b[\w-]+\.(?:com|cn|edu|org|net|gov)\b", re.IGNORECASE)
ABS_PATH_RE = re.compile(r"[A-Za-z]:\\")

ALLOWED_UNIVERSITY = facts.SCHOOL_NAME

# 真实高校关键词禁止名单（允许名单为“启明大学”）
REAL_SCHOOL_KEYWORDS = (
    "清华",
    "北京大学",
    "复旦大学",
    "浙江大学",
    "上海交通大学",
    "南京大学",
    "武汉大学",
    "中山大学",
    "哈尔滨工业大学",
    "西安交通大学",
    "中国科学技术大学",
    "华中科技大学",
    "同济大学",
    "南开大学",
    "天津大学",
    "四川大学",
    "山东大学",
    "厦门大学",
    "吉林大学",
    "东南大学",
    "湖南大学",
    "重庆大学",
    "兰州大学",
    "中国人民大学",
    "北京师范大学",
    "中国科学院",
)


def find_school_names(text: str) -> list[str]:
    """提取“<两个汉字>大学”形式的学校名。

    “大学英语”“大学写作”这类课程名前的字符不是汉字，因此不会被误判为学校名。
    """
    names: list[str] = []
    for match in re.finditer("大学", text):
        start = match.start()
        prefix = text[max(0, start - 2):start]
        if len(prefix) == 2 and all("\u4e00" <= char <= "\u9fa5" for char in prefix):
            names.append(prefix + "大学")
    return names


def scan_text(text: str, label: str) -> list[str]:
    problems: list[str] = []
    for name in sorted(set(find_school_names(text))):
        if name != ALLOWED_UNIVERSITY:
            problems.append(f"{label}: 出现非允许学校名 {name!r}")
    for keyword in REAL_SCHOOL_KEYWORDS:
        if keyword in text:
            problems.append(f"{label}: 命中真实学校关键词 {keyword!r}")
    for name, pattern in (
        ("邮箱", EMAIL_RE),
        ("手机号", MOBILE_RE),
        ("固定电话", LANDLINE_RE),
        ("身份证号", ID_CARD_RE),
        ("URL", URL_RE),
        ("域名", DOMAIN_RE),
        ("Windows 绝对路径", ABS_PATH_RE),
    ):
        found = pattern.findall(text)
        if found:
            problems.append(f"{label}: 命中{name} {found[:3]}")
    return problems


# ---------------------------------------------------------------------------
# 可读性抽样
# ---------------------------------------------------------------------------


def read_pdf_pages(path: Path) -> list[str]:
    reader = PdfReader(str(path))
    return [(page.extract_text() or "") for page in reader.pages]


def read_docx(path: Path) -> dict:
    document = Document(str(path))
    headings = [p.text for p in document.paragraphs if p.style.name.startswith("Heading")]
    lines = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            lines.append("\t".join(cell.text for cell in row.cells))
    return {"headings": headings, "text": "\n".join(lines)}


def read_xlsx(path: Path) -> dict:
    workbook = load_workbook(str(path))
    cells: dict[str, list[list]] = {}
    for name in workbook.sheetnames:
        sheet = workbook[name]
        cells[name] = [list(row) for row in sheet.iter_rows(values_only=True)]
    return {"names": list(workbook.sheetnames), "cells": cells}


def extract_document_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "\n".join(read_pdf_pages(path))
    if suffix == ".docx":
        return read_docx(path)["text"]
    if suffix == ".xlsx":
        data = read_xlsx(path)
        return "\n".join(
            "\t".join("" if cell is None else str(cell) for cell in row)
            for rows in data["cells"].values()
            for row in rows
        )
    raise ValueError(f"unsupported file type: {suffix}")


def corpus_texts(root: Path, manifest: dict) -> dict[str, str]:
    texts = {}
    for doc in manifest["documents"]:
        path = root / doc["path"]
        texts[doc["path"]] = extract_document_text(path)
    return texts


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_manifest(root: Path) -> tuple[dict, list[str]]:
    problems: list[str] = []
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        return {}, ["manifest.json 不存在"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    for key in (
        "dataset_name",
        "dataset_version",
        "school_name",
        "fictional",
        "seed",
        "generator_version",
        "generated_at",
        "documents",
    ):
        if key not in manifest:
            problems.append(f"manifest 缺少字段 {key}")
    if manifest.get("fictional") is not True:
        problems.append("manifest.fictional 必须为 true")
    if manifest.get("school_name") != facts.SCHOOL_NAME:
        problems.append("manifest.school_name 与固定配置不一致")
    if manifest.get("seed") != facts.DEFAULT_SEED:
        problems.append("manifest.seed 与固定配置不一致")
    if manifest.get("dataset_version") != facts.DATASET_VERSION:
        problems.append("manifest.dataset_version 与固定配置不一致")
    if manifest.get("generated_at") != facts.GENERATED_AT:
        problems.append("manifest.generated_at 必须为固定 UTC 值")

    documents = manifest.get("documents", [])
    paths = [doc.get("path", "") for doc in documents]

    if paths != sorted(paths):
        problems.append("manifest.documents 未按 path 排序")
    if len(paths) != len(set(paths)):
        problems.append("manifest.documents 存在重复 path")
    if not 12 <= len(documents) <= 18:
        problems.append(f"manifest 文件数应在 12-18 之间，实际 {len(documents)}")

    counts = {"pdf": 0, "docx": 0, "xlsx": 0}
    for doc in documents:
        doc_type = doc.get("file_type")
        if doc_type not in counts:
            problems.append(f"{doc.get('path')}: 未知 file_type {doc_type!r}")
        else:
            counts[doc_type] += 1

    for doc in documents:
        rel = doc.get("path", "")
        doc_type = doc.get("file_type")
        if not rel.startswith("corpus/") or "\\" in rel or ".." in rel or Path(rel).is_absolute():
            problems.append(f"manifest 路径不安全：{rel!r}")
            continue
        file_path = root / rel
        if not file_path.is_file():
            problems.append(f"manifest 登记文件缺失：{rel}")
            continue
        actual = sha256_file(file_path)
        if actual != doc.get("sha256"):
            problems.append(f"{rel}: SHA-256 与 manifest 不一致")

        for key in ("doc_category", "title", "version", "effective_from", "expected_pages_or_sheets", "expected_sections"):
            if key not in doc:
                problems.append(f"{rel}: manifest 缺少字段 {key}")

        if doc_type == "pdf":
            pages = read_pdf_pages(file_path)
            expected = doc.get("expected_pages_or_sheets") or {}
            if expected.get("kind") != "pages" or expected.get("count") != len(pages):
                problems.append(f"{rel}: PDF 页数与 manifest 不一致")
            if doc.get("expected_sections") is None:
                problems.append(f"{rel}: PDF 应提供 expected_sections")
            full_text = "\n".join(pages)
            for section in doc.get("expected_sections") or []:
                if section not in full_text:
                    problems.append(f"{rel}: 未找到章节 {section!r}")
            if facts.FICTION_MARKER not in (pages[0] if pages else ""):
                problems.append(f"{rel}: 首页缺少虚构资料标识")
        elif doc_type == "docx":
            if doc.get("expected_pages_or_sheets") is not None:
                problems.append(f"{rel}: DOCX 的 expected_pages_or_sheets 必须为 null")
            data = read_docx(file_path)
            if doc.get("expected_sections") != data["headings"]:
                problems.append(f"{rel}: DOCX 章节与 manifest 不一致")
            if facts.FICTION_MARKER not in data["text"].splitlines()[0]:
                problems.append(f"{rel}: 正文首页缺少虚构资料标识")
        elif doc_type == "xlsx":
            expected = doc.get("expected_pages_or_sheets") or {}
            if expected.get("kind") != "sheets":
                problems.append(f"{rel}: XLSX 的 expected_pages_or_sheets.kind 必须为 sheets")
            if doc.get("expected_sections") is not None:
                problems.append(f"{rel}: XLSX 的 expected_sections 必须为 null")
            data = read_xlsx(file_path)
            if sorted(expected.get("names") or []) != sorted(data["names"]):
                problems.append(f"{rel}: 工作表名与 manifest 不一致")
            for name, rows in data["cells"].items():
                if not rows or facts.FICTION_MARKER not in str(rows[0][0]):
                    problems.append(f"{rel}#{name}: 工作表顶部缺少虚构资料标识")

    on_disk = {f"corpus/{item.name}" for item in (root / "corpus").iterdir() if item.is_file()}
    if on_disk != set(paths):
        missing = sorted(on_disk - set(paths))
        absent = sorted(set(paths) - on_disk)
        problems.append(f"语料目录与 manifest 不一致：未登记={missing} 缺失={absent}")

    return manifest, problems


def validate_ground_truth(root: Path, manifest: dict) -> tuple[list[dict], list[str]]:
    problems: list[str] = []
    gt_path = root / "ground_truth.jsonl"
    if not gt_path.is_file():
        return [], ["ground_truth.jsonl 不存在"]

    entries = [json.loads(line) for line in gt_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(entries) < MIN_GROUND_TRUTH:
        problems.append(f"ground truth 条数应不少于 {MIN_GROUND_TRUTH}，实际 {len(entries)}")

    ids = [entry.get("id") for entry in entries]
    if len(ids) != len(set(ids)):
        problems.append("ground truth 存在重复 id")

    manifest_paths = {doc["path"] for doc in manifest.get("documents", [])}
    docs_by_path = {doc["path"]: doc for doc in manifest.get("documents", [])}
    page_texts: dict[str, list[str]] = {}
    docx_headings: dict[str, list[str]] = {}
    sheet_dims: dict[str, dict] = {}

    categories = set()
    conflict_ids_in_manifest = {
        conflict["conflict_id"]
        for doc in manifest.get("documents", [])
        for conflict in doc.get("intentional_conflicts", [])
    }
    conflicts_covered: set[str] = set()

    for entry in entries:
        gt_id = entry.get("id")
        if list(entry.keys()) != GROUND_TRUTH_FIELDS:
            problems.append(f"{gt_id}: 字段集合或顺序不符合规范")
        categories.add(entry.get("category"))
        if not entry.get("question"):
            problems.append(f"{gt_id}: 缺少 question")

        for rel in entry.get("expected_source_paths") or []:
            if rel not in manifest_paths:
                problems.append(f"{gt_id}: 引用路径不在 manifest 中：{rel}")

        for locator in entry.get("expected_locators") or []:
            rel = locator.get("path")
            if rel not in manifest_paths:
                problems.append(f"{gt_id}: locator 路径不在 manifest 中：{rel}")
                continue
            suffix = Path(rel).suffix.lower()
            if suffix == ".pdf":
                if rel not in page_texts:
                    page_texts[rel] = read_pdf_pages(root / rel)
                pages = page_texts[rel]
                page_number = locator.get("page_number")
                section = locator.get("section_title")
                if not isinstance(page_number, int) or not (1 <= page_number <= len(pages)):
                    problems.append(f"{gt_id}: PDF 页码越界 {page_number}")
                elif section and section not in pages[page_number - 1]:
                    problems.append(f"{gt_id}: PDF 第 {page_number} 页缺少章节 {section!r}")
            elif suffix == ".docx":
                if rel not in docx_headings:
                    docx_headings[rel] = read_docx(root / rel)["headings"]
                section = locator.get("section_title")
                if section and section not in docx_headings[rel]:
                    problems.append(f"{gt_id}: DOCX 缺少章节 {section!r}")
            elif suffix == ".xlsx":
                if rel not in sheet_dims:
                    sheet_dims[rel] = read_xlsx(root / rel)["cells"]
                sheet = locator.get("sheet_name")
                if sheet not in sheet_dims[rel]:
                    problems.append(f"{gt_id}: XLSX 缺少工作表 {sheet!r}")
                else:
                    max_row = len(sheet_dims[rel][sheet])
                    row_start = locator.get("row_start")
                    row_end = locator.get("row_end")
                    if not isinstance(row_start, int) or not isinstance(row_end, int):
                        problems.append(f"{gt_id}: XLSX 行范围必须为整数")
                    elif row_start < 1 or row_end < row_start or row_end > max_row:
                        problems.append(f"{gt_id}: XLSX 行范围越界 {row_start}-{row_end}/{max_row}")
                    else:
                        rows = sheet_dims[rel][sheet][row_start - 1:row_end]
                        if not any(any(cell not in (None, "") for cell in row) for row in rows):
                            problems.append(f"{gt_id}: XLSX 行范围内没有任何内容")

        if entry.get("conflict_expected"):
            for conflict in (docs_by_path.get(rel, {}).get("intentional_conflicts", []) for rel in (entry.get("expected_source_paths") or [])):
                for item in conflict:
                    conflicts_covered.add(item["conflict_id"])

        planning_input = entry.get("planning_input")
        planning_result = entry.get("expected_planning_result")
        if entry.get("category") == "planning":
            if not planning_input or not planning_result:
                problems.append(f"{gt_id}: planning 记录必须包含输入与期望结果")
            else:
                problems.extend(_check_planning_invariants(gt_id, planning_result))
        elif planning_input is not None or planning_result is not None:
            problems.append(f"{gt_id}: 非 planning 记录的规划字段应为 null")

    missing_categories = REQUIRED_CATEGORIES - categories
    if missing_categories:
        problems.append(f"ground truth 缺少类别：{sorted(missing_categories)}")
    if not any(entry.get("should_refuse") for entry in entries):
        problems.append("ground truth 缺少 should_refuse=true 的样例")
    if not any(entry.get("conflict_expected") for entry in entries):
        problems.append("ground truth 缺少 conflict_expected=true 的样例")
    if conflicts_covered != conflict_ids_in_manifest:
        problems.append(f"conflict_expected 未覆盖全部冲突：{sorted(conflict_ids_in_manifest - conflicts_covered)}")

    return entries, problems


def _check_planning_invariants(gt_id: str, result: dict) -> list[str]:
    problems: list[str] = []
    required = result["required_credits"]
    completed = result["completed_credits"]
    ongoing = result["in_progress_credits"]
    remaining = result["remaining_credits"]
    if completed < 0 or ongoing < 0 or required < 0:
        problems.append(f"{gt_id}: 学分不得为负")
    if remaining != round(max(required - completed - ongoing, 0.0), 1):
        problems.append(f"{gt_id}: 总学分缺口计算不符合不变量")
    if round(sum(gap["completed_credits"] for gap in result["category_gaps"]), 1) != completed:
        problems.append(f"{gt_id}: 类别已修学分与总已修学分不一致")
    if round(sum(gap["in_progress_credits"] for gap in result["category_gaps"]), 1) != ongoing:
        problems.append(f"{gt_id}: 类别在修学分与总在修学分不一致")
    for gap in result["category_gaps"]:
        expected = round(max(gap["required_credits"] - gap["completed_credits"] - gap["in_progress_credits"], 0.0), 1)
        if gap["remaining_credits"] != expected:
            problems.append(f"{gt_id}: 类别 {gap['category']} 剩余学分不符合不变量")
    return problems


def validate_dataset(root: Path, *, check_privacy: bool = True) -> dict:
    root = Path(root)
    manifest, problems = validate_manifest(root)
    entries: list[dict] = []
    if manifest:
        entries, gt_problems = validate_ground_truth(root, manifest)
        problems.extend(gt_problems)

    if check_privacy and manifest:
        for path, text in corpus_texts(root, manifest).items():
            problems.extend(scan_text(text, path))

    return {"problems": problems, "manifest": manifest, "ground_truth": entries}
