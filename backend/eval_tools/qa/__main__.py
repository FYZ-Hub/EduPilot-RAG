"""命令行入口：``python -m eval_tools.qa``。

示例（容器内）：

    python -m eval_tools.qa --output-dir /app/eval-run \
        --ground-truth /app/demo/ground_truth.jsonl --run-id <run-id>

退出码：

- ``0``：阶段 9B 全部必需指标具备资格且通过；
- ``1``：具备资格的必需指标未达标；
- ``2``：存在必需指标因 Provider 无语义能力而 ``deferred``（当前 offline fake 即此情况）。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.llm.factory import build_llm_provider

from eval_tools.ground_truth import load_ground_truth
from eval_tools.pipeline import build_eval_settings, open_eval_environment
from eval_tools.qa.executor import evaluate_cases
from eval_tools.qa.metrics import (
    aggregate_qa,
    build_qa_gate,
    resolve_llm_eligibility,
    resolve_qa_profile,
)
from eval_tools.qa.report import (
    QaReportContext,
    build_qa_cli_summary,
    build_qa_report,
    qa_exit_code,
    to_markdown,
)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="阶段 9B 问答与学业评测（离线、Fake Provider）")
    parser.add_argument("--output-dir", required=True, help="报告与隔离数据的输出目录")
    parser.add_argument("--work-dir", default=None, help="SQLite/Chroma 工作目录，默认 <output-dir>/data")
    parser.add_argument("--ground-truth", default="/app/demo/ground_truth.jsonl")
    parser.add_argument("--demo-dataset-path", default="/app/demo")
    parser.add_argument("--demo-dataset-version", default="2026.1")
    parser.add_argument("--run-id", default=None)
    return parser.parse_args(argv)


def _load_raw_cases(path: Path) -> list[dict]:
    """先做 9A 同款严格校验，再返回原始字典（9B 需要 planning / should_refuse 字段）。"""
    load_ground_truth(path)
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    output_dir = Path(args.output_dir)
    work_dir = Path(args.work_dir) if args.work_dir else output_dir / "data"
    run_id = args.run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)

    ground_truth_path = Path(args.ground_truth)
    cases = _load_raw_cases(ground_truth_path)

    settings = build_eval_settings(
        work_dir,
        demo_dataset_path=Path(args.demo_dataset_path),
        demo_dataset_version=args.demo_dataset_version,
    )

    descriptor_probe = build_llm_provider(settings)
    eligibility = resolve_llm_eligibility(
        descriptor_probe.descriptor.provider,
        descriptor_probe.descriptor.model,
        explicit_profile=False,
        verified_call=False,
        profile_missing_reason=(
            "本轮不接入真实 API 或本地模型，也未显式选择语义评测 profile；"
            "不因 provider 名称非 fake 就认定具备语义评测资格。"
        ),
    )
    descriptor_probe.close()

    with open_eval_environment(settings) as environment:
        environment.ingest_demo()
        results = evaluate_cases(environment, cases)
        metrics = aggregate_qa(results)
        profile = resolve_qa_profile(eligibility)
        gate = build_qa_gate(profile, metrics)
        report = build_qa_report(
            QaReportContext(
                run_id=run_id,
                ground_truth_name=ground_truth_path.name,
                dataset_version=args.demo_dataset_version,
                embedding_provider=environment.embedding_provider,
                rerank_provider=environment.rerank_provider,
                generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
            eligibility,
            gate,
            results,
            metrics,
            total_cases=len(cases),
        )

    json_path = output_dir / "qa-eval.json"
    markdown_path = output_dir / "qa-eval.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    markdown_path.write_text(to_markdown(report) + "\n", encoding="utf-8")

    summary = build_qa_cli_summary(
        report, json_report=json_path.name, markdown_report=markdown_path.name
    )
    print(json.dumps(summary, ensure_ascii=False))
    return qa_exit_code(report)


if __name__ == "__main__":
    sys.exit(main())
