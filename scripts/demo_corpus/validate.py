"""语料校验与隐私扫描（可读性抽样、manifest 一致性、ground truth 校验）。

同时被 ``generate_demo_corpus.py``（发布前 / 发布后）和 pytest 使用：
只有校验通过的数据集才允许固化到仓库 ``demo/``。

隐私扫描分两层：
- 内容层：DOCX 段落/表格、XLSX 单元格、PDF 正文与 metadata 中不得出现任何域名；
- 容器层：文件原始字节中的可打印字符串里，只有 OOXML 标准命名空间允许名单内的宿主名可以出现。
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
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

EXPECTED_DOCUMENT_COUNT = 15
EXPECTED_TYPE_COUNTS = {"pdf": 5, "docx": 5, "xlsx": 5}
EXTENSION_BY_TYPE = {"pdf": ".pdf", "docx": ".docx", "xlsx": ".xlsx"}

# 内容层与 PDF 全层禁止出现的字符串（含厂商域名与任何网络地址）
FORBIDDEN_CONTENT_TOKENS = ("reportlab.com", "http://", "https://", "www.")

# OOXML 标准命名空间允许名单：仅在“容器字节”层豁免，内容层仍然禁止任何域名。
# 取值来自实际生成文件（python-docx / openpyxl 的标准部件），
# 出现名单外宿主即视为失败，必须显式评审后再加入。
OFFICE_NAMESPACE_HOST_ALLOWLIST = (
    "schemas.openxmlformats.org",  # OOXML 主命名空间
    "schemas.microsoft.com",  # Office 扩展命名空间（DOCX 设置部件）
    "purl.org",  # Dublin Core 元数据命名空间
    "www.w3.org",  # XML / 关系描述命名空间
)

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
HOST_RE = re.compile(r"https?://([A-Za-z0-9._-]+)", re.IGNORECASE)
WWW_HOST_RE = re.compile(r"www\.([A-Za-z0-9._-]+)", re.IGNORECASE)
PRINTABLE_RE = re.compile(rb"[\x20-\x7e]{4,}")

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
    """内容层扫描：学校名、个人信息、域名与绝对路径。"""
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


def printable_strings(raw: bytes) -> list[str]:
    """文件原始字节中的 ASCII 可打印字符串（长度 >= 4）。"""
    return [match.group(0).decode("latin-1") for match in PRINTABLE_RE.finditer(raw)]


def find_url_hosts(text: str) -> set[str]:
    hosts = {match.group(1).lower() for match in HOST_RE.finditer(text)}
    hosts |= {f"www.{match.group(1).lower()}" for match in WWW_HOST_RE.finditer(text)}
    return hosts


def is_allowed_namespace_host(host: str) -> bool:
    return host in OFFICE_NAMESPACE_HOST_ALLOWLIST


def scan_pdf_file(path: Path) -> list[str]:
    """PDF 全层扫描：正文、metadata 与原始字节可打印字符串。"""
    problems: list[str] = []
    path = Path(path)
    raw = path.read_bytes()
    reader = PdfReader(str(path))
    metadata = {str(key): str(value) for key, value in (reader.metadata or {}).items()}
    surfaces = {
        "正文": "\n".join(page.extract_text() or "" for page in reader.pages),
        "metadata": "\n".join(f"{key}={metadata[key]}" for key in sorted(metadata)),
        "原始字节可打印字符串": "\n".join(printable_strings(raw)),
    }
    for surface, text in surfaces.items():
        lowered = text.lower()
        for token in FORBIDDEN_CONTENT_TOKENS:
            if token in lowered:
                problems.append(f"{path.name}: {surface}命中 {token!r}")
    return problems


def collect_ooxml_hosts(path: Path) -> tuple[set[str], set[str]]:
    """扫描 DOCX/XLSX 的原始字节与解压后的容器部件，返回 (允许名单宿主, 被拒宿主)。"""
    path = Path(path)
    allowlisted: set[str] = set()
    rejected: set[str] = set()
    surfaces = [path.read_bytes()]
    with zipfile.ZipFile(path) as archive:
        for name in sorted(archive.namelist()):
            surfaces.append(archive.read(name))
    for raw in surfaces:
        for text in printable_strings(raw):
            for host in find_url_hosts(text):
                if is_allowed_namespace_host(host):
                    allowlisted.add(host)
                else:
                    rejected.add(host)
    return allowlisted, rejected


def scan_ooxml_file(path: Path) -> list[str]:
    """DOCX/XLSX 容器扫描：只允许 OOXML 标准命名空间宿主名。"""
    _, rejected = collect_ooxml_hosts(path)
    return [f"{Path(path).name}: 容器内出现非许可域名 {host!r}" for host in sorted(rejected)]


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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# 文档读取缓存与定位器校验
# ---------------------------------------------------------------------------


class DocumentReader:
    """按路径缓存真实文件读取结果，供 locator 校验复用。"""

    def __init__(self, root: Path, documents: list[dict] | None = None):
        self.root = Path(root)
        self._documents = {doc.get("path"): doc for doc in (documents or [])}
        self._pdf: dict[str, list[str]] = {}
        self._docx: dict[str, dict] = {}
        self._xlsx: dict[str, dict] = {}

    def sections(self, rel: str) -> list[str]:
        return list((self._documents.get(rel) or {}).get("expected_sections") or [])

    def section_span(self, rel: str, section: str) -> tuple[int, int] | None:
        """章节覆盖的 1-based 页码区间。

        章节末尾与下一章节起始可能同处一页，因此区间取到下一章节标题所在页。
        """
        pages = self.pdf_pages(rel)
        sections = self.sections(rel)
        if section not in sections or not pages:
            return None
        start = next((index + 1 for index, page in enumerate(pages) if section in page), None)
        if start is None:
            return None
        end = len(pages)
        for later in sections[sections.index(section) + 1:]:
            later_start = next(
                (index + 1 for index, page in enumerate(pages[start:], start=start) if later in page), None
            )
            if later_start is not None and later_start > start:
                end = later_start
                break
        return start, end

    def pdf_pages(self, rel: str) -> list[str]:
        if rel not in self._pdf:
            self._pdf[rel] = read_pdf_pages(self.root / rel)
        return self._pdf[rel]

    def docx(self, rel: str) -> dict:
        if rel not in self._docx:
            self._docx[rel] = read_docx(self.root / rel)
        return self._docx[rel]

    def xlsx(self, rel: str) -> dict:
        if rel not in self._xlsx:
            self._xlsx[rel] = read_xlsx(self.root / rel)
        return self._xlsx[rel]

    def check_locator(self, rel: str, locator: dict, label: str, problems: list[str]) -> None:
        suffix = Path(rel).suffix.lower()
        if suffix == ".pdf":
            pages = self.pdf_pages(rel)
            page_number = locator.get("page_number")
            section = locator.get("section_title")
            if not isinstance(page_number, int) or not 1 <= page_number <= len(pages):
                problems.append(f"{label}: PDF 页码越界 {page_number}")
            elif section:
                span = self.section_span(rel, section)
                if span is None:
                    problems.append(f"{label}: PDF 中无法定位章节 {section!r}")
                elif not span[0] <= page_number <= span[1]:
                    problems.append(
                        f"{label}: PDF 第 {page_number} 页不在章节 {section!r} 范围 {span} 内"
                    )
        elif suffix == ".docx":
            section = locator.get("section_title")
            if section and section not in self.docx(rel)["headings"]:
                problems.append(f"{label}: DOCX 缺少章节 {section!r}")
        elif suffix == ".xlsx":
            cells = self.xlsx(rel)["cells"]
            sheet = locator.get("sheet_name")
            if sheet not in cells:
                problems.append(f"{label}: XLSX 缺少工作表 {sheet!r}")
                return
            max_row = len(cells[sheet])
            row_start = locator.get("row_start")
            row_end = locator.get("row_end")
            if not isinstance(row_start, int) or not isinstance(row_end, int):
                problems.append(f"{label}: XLSX 行范围必须为整数")
            elif row_start < 1 or row_end < row_start or row_end > max_row:
                problems.append(f"{label}: XLSX 行范围越界 {row_start}-{row_end}/{max_row}")
            elif not any(any(cell not in (None, "") for cell in row) for row in cells[sheet][row_start - 1:row_end]):
                problems.append(f"{label}: XLSX 行范围内没有任何内容")
        else:
            problems.append(f"{label}: 未知定位类型 {suffix}")


# ---------------------------------------------------------------------------
# manifest 校验
# ---------------------------------------------------------------------------


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
        "document_count",
        "dataset_sha256",
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
    if manifest.get("generator_version") != facts.GENERATOR_VERSION:
        problems.append("manifest.generator_version 与固定配置不一致")

    documents = manifest.get("documents", [])
    paths = [doc.get("path", "") for doc in documents]

    if paths != sorted(paths):
        problems.append("manifest.documents 未按 path 排序")
    if len(paths) != len(set(paths)):
        problems.append("manifest.documents 存在重复 path")
    if len(documents) != EXPECTED_DOCUMENT_COUNT:
        problems.append(f"语料文件数应为 {EXPECTED_DOCUMENT_COUNT}，实际 {len(documents)}")
    if manifest.get("document_count") != len(documents):
        problems.append("manifest.document_count 与实际文件数不一致")

    counts = {"pdf": 0, "docx": 0, "xlsx": 0}
    for doc in documents:
        doc_type = doc.get("file_type")
        if doc_type not in counts:
            problems.append(f"{doc.get('path')}: 未知 file_type {doc_type!r}")
        else:
            counts[doc_type] += 1
    if counts != EXPECTED_TYPE_COUNTS:
        problems.append(f"文件类型数量应为 {EXPECTED_TYPE_COUNTS}，实际 {counts}")

    document_paths = {doc.get("path", "") for doc in documents}
    reader = DocumentReader(root, documents)

    for doc in documents:
        rel = doc.get("path", "")
        doc_type = doc.get("file_type")
        if not rel.startswith("corpus/") or "\\" in rel or ".." in rel or Path(rel).is_absolute():
            problems.append(f"manifest 路径不安全：{rel!r}")
            continue
        if doc_type in EXTENSION_BY_TYPE and Path(rel).suffix.lower() != EXTENSION_BY_TYPE[doc_type]:
            problems.append(f"{rel}: file_type 与扩展名不一致")
        file_path = root / rel
        if not file_path.is_file():
            problems.append(f"manifest 登记文件缺失：{rel}")
            continue
        if sha256_file(file_path) != doc.get("sha256"):
            problems.append(f"{rel}: SHA-256 与 manifest 不一致")

        for key in (
            "doc_category",
            "title",
            "version",
            "effective_from",
            "expected_pages_or_sheets",
            "expected_sections",
            "intentional_conflicts",
        ):
            if key not in doc:
                problems.append(f"{rel}: manifest 缺少字段 {key}")
        if not isinstance(doc.get("intentional_conflicts"), list):
            problems.append(f"{rel}: intentional_conflicts 必须为数组")
            continue

        for conflict in doc["intentional_conflicts"]:
            expected = f"{rel} 冲突 {conflict.get('conflict_id')}"
            if not conflict.get("conflict_id") or not conflict.get("field") or not conflict.get("reason"):
                problems.append(f"{expected}: 缺少 conflict_id / field / reason")
            sources = conflict.get("sources") or []
            if len(sources) < 2:
                problems.append(f"{expected}: 冲突来源少于 2 个")
            values = set()
            for source in sources:
                source_path = source.get("path")
                if source_path not in document_paths:
                    problems.append(f"{expected}: 冲突来源路径不在 manifest 中：{source_path!r}")
                    continue
                if not str(source.get("value", "")).strip():
                    problems.append(f"{expected}: 冲突来源缺少取值")
                values.add(str(source.get("value")))
                locator = source.get("locator") or {}
                if locator.get("path") != source_path:
                    problems.append(f"{expected}: 冲突 locator 路径与来源路径不一致")
                reader.check_locator(source_path, locator, expected, problems)
            if len(values) < 2:
                problems.append(f"{expected}: 冲突双方取值必须不同")

        if doc_type == "pdf":
            pages = reader.pdf_pages(rel)
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
            data = reader.docx(rel)
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
            data = reader.xlsx(rel)
            if sorted(expected.get("names") or []) != sorted(data["names"]):
                problems.append(f"{rel}: 工作表名与 manifest 不一致")
            for name, rows in data["cells"].items():
                if not rows or facts.FICTION_MARKER not in str(rows[0][0]):
                    problems.append(f"{rel}#{name}: 工作表顶部缺少虚构资料标识")

    # 递归检查 corpus：不允许嵌套目录或未登记文件
    corpus_dir = root / "corpus"
    on_disk = set()
    for item in sorted(corpus_dir.rglob("*")):
        relative = item.relative_to(root).as_posix()
        if item.is_dir():
            problems.append(f"语料目录不应包含子目录：{relative}")
        elif item.is_file():
            on_disk.add(relative)
    if on_disk != document_paths:
        problems.append(
            "语料目录与 manifest 不一致："
            f"未登记={sorted(on_disk - document_paths)} 缺失={sorted(document_paths - on_disk)}"
        )

    recomputed = hashlib.sha256(
        "".join(f"{doc['path']}\n{doc.get('sha256', '')}\n" for doc in documents).encode("utf-8")
    ).hexdigest()
    if manifest.get("dataset_sha256") != recomputed:
        problems.append("manifest.dataset_sha256 与重新计算结果不一致")

    return manifest, problems


# ---------------------------------------------------------------------------
# ground truth 校验
# ---------------------------------------------------------------------------


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
    reader = DocumentReader(root, manifest.get("documents", []))

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

        source_paths = list(entry.get("expected_source_paths") or [])
        locators = list(entry.get("expected_locators") or [])
        for rel in source_paths:
            if rel not in manifest_paths:
                problems.append(f"{gt_id}: 引用路径不在 manifest 中：{rel}")

        source_set = set(source_paths)
        locator_paths = [locator.get("path") for locator in locators]
        if source_set != set(locator_paths):
            missing = sorted(source_set - set(locator_paths))
            extra = sorted(set(locator_paths) - source_set)
            problems.append(f"{gt_id}: 来源与定位不一致（缺少定位={missing} 多余定位={extra}）")
        if not source_paths and locators:
            problems.append(f"{gt_id}: 无来源的拒答记录不得携带定位")

        for locator in locators:
            rel = locator.get("path")
            if rel not in manifest_paths:
                problems.append(f"{gt_id}: locator 路径不在 manifest 中：{rel}")
                continue
            reader.check_locator(rel, locator, gt_id, problems)

        if entry.get("conflict_expected"):
            for rel in source_paths:
                for conflict in docs_by_path.get(rel, {}).get("intentional_conflicts", []):
                    conflicts_covered.add(conflict["conflict_id"])

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


# ---------------------------------------------------------------------------
# 培养方案版本语义回归
# ---------------------------------------------------------------------------


def validate_degree_plan_semantics(root: Path, manifest: dict) -> list[str]:
    """从实际 PDF 文本验证“必修课程按版本渲染”的语义。"""
    problems: list[str] = []
    reader = DocumentReader(root, manifest.get("documents", []))
    plan_docs = {
        doc["version"]: doc for doc in manifest.get("documents", []) if doc["doc_category"] == "degree_plan"
    }
    plans = [facts.DEGREE_PLAN_2025, facts.DEGREE_PLAN_2026]
    for plan in plans:
        if plan["version"] not in plan_docs:
            problems.append(f"缺少培养方案版本 {plan['version']}")
    if problems:
        return problems

    texts = {
        plan["version"]: "\n".join(reader.pdf_pages(plan_docs[plan["version"]]["path"])) for plan in plans
    }

    for plan in plans:
        version = plan["version"]
        text = texts[version]
        for category in facts.MANDATORY_CATEGORIES:
            codes = facts.plan_required_codes(plan, category)
            if f"{category}课程共 {len(codes)} 门" not in text:
                problems.append(f"{version}: 正文未声明 {category}课程共 {len(codes)} 门")
            for code in codes:
                if code not in text:
                    problems.append(f"{version}: 必修课程 {code} 未出现在正文")
        for course in facts.plan_optional_courses(plan):
            if course["course_code"] not in text:
                problems.append(f"{version}: 非必修课程 {course['course_code']} 未出现在正文")

    newer, older = facts.DEGREE_PLAN_2026, facts.DEGREE_PLAN_2025
    for code in facts.plan_added_required_codes(newer, older):
        if code in texts[older["version"]]:
            problems.append(f"{older['version']}: 旧版正文不应出现新版新增必修课程 {code}")
        if code not in texts[newer["version"]]:
            problems.append(f"{newer['version']}: 新版正文缺少新增必修课程 {code}")

    all_required = {
        code
        for plan in plans
        for category in facts.MANDATORY_CATEGORIES
        for code in facts.plan_required_codes(plan, category)
    }
    for course in facts.COURSES:
        is_mandatory = course["category"] in facts.MANDATORY_CATEGORIES
        in_required = course["course_code"] in all_required
        if is_mandatory and not in_required:
            problems.append(
                f"事实模型把 {course['course_code']} 标为 {course['category']}，但它不在任何培养方案必修列表中"
            )
        if in_required and not is_mandatory:
            problems.append(
                f"事实模型把 {course['course_code']} 标为 {course['category']}，但它出现在培养方案必修列表中"
            )

    software = facts.COURSE_BY_CODE.get("QM-CS401")
    if software is None:
        problems.append("事实模型缺少 QM-CS401")
    elif software["category"] in facts.MANDATORY_CATEGORIES:
        problems.append("QM-CS401 不属于任何版本的必修列表，不应标为必修类别")

    return problems


# ---------------------------------------------------------------------------
# 汇总校验
# ---------------------------------------------------------------------------


def validate_media_scan(root: Path, manifest: dict) -> list[str]:
    """PDF 与 OOXML 的原始字节 / metadata 域名扫描。"""
    problems: list[str] = []
    for doc in manifest.get("documents", []):
        path = root / doc["path"]
        if not path.is_file():
            continue
        if doc["file_type"] == "pdf":
            problems.extend(scan_pdf_file(path))
        elif doc["file_type"] in ("docx", "xlsx"):
            problems.extend(scan_ooxml_file(path))
    return problems


def validate_dataset(root: Path, *, check_privacy: bool = True) -> dict:
    root = Path(root)
    manifest, problems = validate_manifest(root)
    entries: list[dict] = []
    if manifest:
        entries, gt_problems = validate_ground_truth(root, manifest)
        problems.extend(gt_problems)
        problems.extend(validate_degree_plan_semantics(root, manifest))
        if check_privacy:
            for path, text in corpus_texts(root, manifest).items():
                problems.extend(scan_text(text, path))
            problems.extend(validate_media_scan(root, manifest))

    return {"problems": problems, "manifest": manifest, "ground_truth": entries}
