"""命令行入口：``python -m eval_tools``。

示例（容器内）：

    python -m eval_tools --output-dir /app/eval-run \
        --ground-truth /app/demo/ground_truth.jsonl --top-k 5 --min-recall 0.85

退出码：Recall@k ≥ ``--min-recall`` 时 0，否则 1（不修改、不放宽命中规则）。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from eval_tools.ground_truth import load_ground_truth, select_cases
from eval_tools.pipeline import build_eval_settings, open_eval_environment
from eval_tools.report import (
    ReportContext,
    build_cli_summary,
    build_report,
    exit_code,
    to_markdown,
)
from eval_tools.runner import (
    DEFAULT_CANDIDATE_K,
    DEFAULT_MIN_RECALL,
    DEFAULT_TOP_K,
    aggregate,
    evaluate_cases,
    resolve_profile,
)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="离线 RAG 检索评测（Recall@5 / MRR / P50-P95）")
    parser.add_argument("--output-dir", required=True, help="报告与隔离数据的输出目录")
    parser.add_argument("--work-dir", default=None, help="SQLite/Chroma 工作目录，默认 <output-dir>/data")
    parser.add_argument("--ground-truth", default="/app/demo/ground_truth.jsonl")
    parser.add_argument("--demo-dataset-path", default="/app/demo")
    parser.add_argument("--demo-dataset-version", default="2026.1")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--candidate-k", type=int, default=DEFAULT_CANDIDATE_K)
    parser.add_argument("--min-recall", type=float, default=DEFAULT_MIN_RECALL)
    parser.add_argument("--run-id", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    output_dir = Path(args.output_dir)
    work_dir = Path(args.work_dir) if args.work_dir else output_dir / "data"
    run_id = args.run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)

    ground_truth_path = Path(args.ground_truth)
    cases = load_ground_truth(ground_truth_path)
    selection = select_cases(cases)

    settings = build_eval_settings(
        work_dir,
        demo_dataset_path=Path(args.demo_dataset_path),
        demo_dataset_version=args.demo_dataset_version,
    )

    with open_eval_environment(settings) as environment:
        environment.ingest_demo()
        profile = resolve_profile(
            environment.rerank_score_kind, threshold=args.min_recall
        )
        results = evaluate_cases(
            selection.included,
            environment.chain,
            top_k=args.top_k,
            candidate_k=args.candidate_k,
            dataset_version=args.demo_dataset_version,
        )
        metrics = aggregate(results, top_k=args.top_k, candidate_k=args.candidate_k)
        report = build_report(
            ReportContext(
                run_id=run_id,
                ground_truth_name=ground_truth_path.name,
                dataset_version=args.demo_dataset_version,
                top_k=args.top_k,
                candidate_k=args.candidate_k,
                embedding_provider=environment.embedding_provider,
                rerank_provider=environment.rerank_provider,
                profile=profile,
                generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
            selection,
            results,
            metrics,
        )

    json_path = output_dir / "retrieval-eval.json"
    markdown_path = output_dir / "retrieval-eval.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    markdown_path.write_text(to_markdown(report) + "\n", encoding="utf-8")

    summary = build_cli_summary(
        report, json_report=json_path.name, markdown_report=markdown_path.name
    )
    print(json.dumps(summary, ensure_ascii=False))
    return exit_code(report)


if __name__ == "__main__":
    sys.exit(main())
