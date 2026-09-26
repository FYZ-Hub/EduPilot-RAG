#!/usr/bin/env python3
"""确定性生成启明大学虚构模拟语料、manifest 与 ground truth。

完全离线运行：不访问网络、不调用外部 API，也不调用任何大模型。
必须显式提供 ``--output``（数据集根目录）；默认不会写入 ``demo/``，
固化到仓库需要额外显式传入 ``--publish``。

规范命令（在固定 Docker 生成器环境内）：

    python scripts/generate_demo_corpus.py --output .tmp/demo-generated --seed 20260925
"""

from __future__ import annotations

import argparse
import json
import locale
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from demo_corpus import facts  # noqa: E402
from demo_corpus.build import build_dataset, publish_dataset  # noqa: E402
from demo_corpus.validate import validate_dataset  # noqa: E402

DEFAULT_FONT_PATH = Path(__file__).resolve().parent / "generator" / "fonts" / "NotoSansSC-VF.ttf"


def _configure_environment() -> None:
    """固定区域与时区，避免生成结果受宿主机环境影响。"""
    os.environ.setdefault("TZ", "UTC")
    if hasattr(time, "tzset"):
        time.tzset()
    try:
        locale.setlocale(locale.LC_ALL, "C.UTF-8")
    except locale.Error:
        pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="generate_demo_corpus.py",
        description="确定性生成启明大学虚构模拟语料（完全离线）。",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="数据集根目录，其下生成 corpus/、manifest.json 和 ground_truth.jsonl（必填）。",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=facts.DEFAULT_SEED,
        help=f"固定随机种子（默认 {facts.DEFAULT_SEED}）。",
    )
    parser.add_argument(
        "--font",
        type=Path,
        default=DEFAULT_FONT_PATH,
        help="仓库内固定的开源中文字体路径。",
    )
    parser.add_argument(
        "--publish",
        type=Path,
        default=None,
        help="生成并校验通过后，把产物固化到该目录（例如 demo）。不传则不写入。",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_environment()

    font_path = Path(args.font)
    if not font_path.is_file():
        print(f"字体文件不存在：{font_path}", file=sys.stderr)
        return 2

    result = build_dataset(Path(args.output), int(args.seed), font_path)

    validation = validate_dataset(Path(args.output))
    problems = validation["problems"]
    if problems:
        print("语料校验未通过，已中止：", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    summary = {
        "output": result["root"],
        "document_count": len(result["documents"]),
        "ground_truth_count": result["ground_truth_count"],
        "validation": "passed",
        "dataset_sha256": result["dataset_sha256"],
        "manifest_sha256": result["manifest_sha256"],
        "ground_truth_sha256": result["ground_truth_sha256"],
    }

    published = None
    if args.publish is not None:
        publish_dataset(Path(args.output), Path(args.publish))
        published = str(Path(args.publish))
        summary["published_to"] = published

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
