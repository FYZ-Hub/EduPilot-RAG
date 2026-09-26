"""隐私扫描：语料内容、PDF 全层、OOXML 容器、生成器源码与字体资产。"""

from __future__ import annotations

import re

from conftest import FONT_LICENSE_PATH, FONT_PATH, GENERATOR_SOURCES
from demo_corpus import facts
from demo_corpus.documents import _REPORTLAB_HEADER_LINE
from demo_corpus.validate import (
    FORBIDDEN_CONTENT_TOKENS,
    OFFICE_NAMESPACE_HOST_ALLOWLIST,
    collect_ooxml_hosts,
    corpus_texts,
    find_school_names,
    printable_strings,
    scan_ooxml_file,
    scan_pdf_file,
    scan_text,
    sha256_file,
)

# 生成器不得具备网络能力（外部下载依赖）
NETWORK_TOKENS = (
    "import requests",
    "from requests",
    "import urllib",
    "from urllib",
    "import socket",
    "from socket",
    "import httpx",
    "import ftplib",
    "import http.client",
    "urlopen(",
    "requests.get",
    "requests.post",
    "pip install",
)


def _documents(dataset, file_type):
    return [doc for doc in dataset["manifest"]["documents"] if doc["file_type"] == file_type]


def test_corpus_text_has_no_privacy_violations(dataset):
    problems = []
    for path, text in corpus_texts(dataset["root"], dataset["manifest"]).items():
        problems.extend(scan_text(text, path))
    assert problems == []


def test_corpus_text_contains_no_urls(dataset):
    for path, text in corpus_texts(dataset["root"], dataset["manifest"]).items():
        assert "http" not in text.lower(), path
        assert "www." not in text.lower(), path


def test_only_fictional_school_name_is_used(dataset):
    texts = corpus_texts(dataset["root"], dataset["manifest"])
    for path, text in texts.items():
        names = set(find_school_names(text))
        assert names <= {facts.SCHOOL_NAME}, (path, names)


def test_pdf_body_metadata_and_raw_strings_are_clean(dataset):
    """PDF 必须同时满足：正文、metadata、原始字节可打印字符串三层都无厂商域名与网络地址。"""
    root = dataset["root"]
    documents = _documents(dataset, "pdf")
    assert len(documents) == 5

    for doc in documents:
        path = root / doc["path"]
        assert scan_pdf_file(path) == [], doc["path"]

        raw = path.read_bytes()
        assert b"reportlab.com" not in raw.lower(), doc["path"]
        assert _REPORTLAB_HEADER_LINE not in raw, doc["path"]
        for text in printable_strings(raw):
            lowered = text.lower()
            for token in FORBIDDEN_CONTENT_TOKENS:
                assert token not in lowered, (doc["path"], token, text[:80])


def test_pdf_metadata_uses_local_fictional_values(dataset):
    from pypdf import PdfReader

    root = dataset["root"]
    for doc in _documents(dataset, "pdf"):
        metadata = {str(key): str(value) for key, value in (PdfReader(str(root / doc["path"])).metadata or {}).items()}
        assert metadata["/Author"] == facts.DOC_AUTHOR
        assert metadata["/Creator"] == facts.DOC_CREATOR
        assert metadata["/Producer"] == facts.DOC_PRODUCER
        assert metadata["/Title"] == doc["title"]
        for value in metadata.values():
            lowered = value.lower()
            for token in FORBIDDEN_CONTENT_TOKENS:
                assert token not in lowered, (doc["path"], token, value)


def test_ooxml_container_only_uses_allowlisted_namespaces(dataset):
    """DOCX/XLSX 容器只允许标准命名空间宿主名，且允许名单确实被命中。"""
    root = dataset["root"]
    observed_allowlisted = set()
    for doc in _documents(dataset, "docx") + _documents(dataset, "xlsx"):
        assert scan_ooxml_file(root / doc["path"]) == [], doc["path"]
        allowlisted, rejected = collect_ooxml_hosts(root / doc["path"])
        assert rejected == set(), (doc["path"], rejected)
        assert allowlisted <= set(OFFICE_NAMESPACE_HOST_ALLOWLIST), (doc["path"], allowlisted)
        observed_allowlisted |= allowlisted

    assert observed_allowlisted, "允许名单未被任何容器命中，扫描可能失效"
    assert observed_allowlisted == {
        "schemas.openxmlformats.org",
        "schemas.microsoft.com",
        "purl.org",
        "www.w3.org",
    }


def test_generator_sources_have_no_network_dependency():
    problems = []
    for source in GENERATOR_SOURCES:
        text = source.read_text(encoding="utf-8")
        for token in NETWORK_TOKENS:
            if token in text:
                problems.append(f"{source}: 出现网络调用标记 {token!r}")
    assert problems == []


def test_generator_sources_use_no_absolute_host_paths():
    for source in GENERATOR_SOURCES:
        text = source.read_text(encoding="utf-8")
        assert not re.search(r"[A-Za-z]:\\", text), source


def test_committed_font_matches_documented_sha256():
    documented = re.findall(
        r"SHA-256[^\n]*?`([0-9a-f]{64})`",
        (FONT_PATH.parent / "FONT_SOURCES.md").read_text(encoding="utf-8"),
    )
    assert documented, "FONT_SOURCES.md 未记录字体 SHA-256"
    assert sha256_file(FONT_PATH) == documented[0]
    assert documented[0] == "a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da"


def test_committed_font_license_is_ofl():
    license_text = FONT_LICENSE_PATH.read_text(encoding="utf-8")
    assert "SIL Open Font License, Version 1.1" in license_text
    assert "SIL OPEN FONT LICENSE Version 1.1" in license_text
