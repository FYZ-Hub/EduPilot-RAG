"""隐私扫描：语料内容、生成器源码、字体资产。"""

from __future__ import annotations

import re

from conftest import FONT_LICENSE_PATH, FONT_PATH, GENERATOR_SOURCES
from demo_corpus import facts
from demo_corpus.validate import corpus_texts, find_school_names, scan_text, sha256_file

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


def test_committed_font_license_is_ofl():
    license_text = FONT_LICENSE_PATH.read_text(encoding="utf-8")
    assert "SIL Open Font License, Version 1.1" in license_text
    assert "SIL OPEN FONT LICENSE Version 1.1" in license_text
