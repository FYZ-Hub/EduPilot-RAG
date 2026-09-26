"""生成器测试公共夹具。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from demo_corpus import facts
from demo_corpus.build import build_dataset

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SCRIPTS_DIR.parent
FONT_PATH = SCRIPTS_DIR / "generator" / "fonts" / "NotoSansSC-VF.ttf"
FONT_LICENSE_PATH = SCRIPTS_DIR / "generator" / "fonts" / "OFL.txt"
GENERATOR_SOURCES = sorted((SCRIPTS_DIR / "demo_corpus").glob("*.py")) + [
    SCRIPTS_DIR / "generate_demo_corpus.py"
]


def generate(root: Path) -> dict:
    """按固定种子与固定字体生成整套语料。"""
    return build_dataset(Path(root), facts.DEFAULT_SEED, FONT_PATH)


def read_manifest(root: Path) -> dict:
    return json.loads((Path(root) / "manifest.json").read_text(encoding="utf-8"))


def read_ground_truth(root: Path) -> list[dict]:
    text = (Path(root) / "ground_truth.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def relative_files(root: Path) -> list[str]:
    root = Path(root)
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())


@pytest.fixture(scope="session")
def dataset(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("demo-corpus")
    result = generate(root)
    return {
        "root": root,
        "result": result,
        "manifest": read_manifest(root),
        "entries": read_ground_truth(root),
    }
