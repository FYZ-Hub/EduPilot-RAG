"""命令行入口：``python -m eval_tools.qa``。

两种 profile：

- ``--profile offline_fake``（默认）：完全离线（Fake Provider），沿用既有口径与退出码
  （``planning_correctness`` 参与门禁，三项正式语义指标 ``deferred``，退出码 2）。
- ``--profile semantic_api``：显式语义评测。强制 ``--output-dir /app/eval-run`` 与
  ``--work-dir /app/eval-run/data``；用**进程环境**创建 ``Settings``
  （``Settings.model_config`` 为 ``env_file=None``，不会读取 ``.env``），再经
  :func:`open_semantic_eval_environment` 装配真实 Provider 审计；**绝不回退 fake**。

退出码：

- ``0``：全部必需指标具备资格且达标；
- ``1``：具备资格的必需指标未达标；
- ``2``：存在必需指标 ``deferred``——配置不全、Provider 未验证/失败、rerank 降级、
  隔离快照异常或发生变化。

错误输出约束：只允许稳定标签与**配置键名**，绝不输出 URL、密钥、正文或异常原文。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings
from app.llm.factory import build_llm_provider

from eval_tools.ground_truth import load_ground_truth
from eval_tools.pipeline import (
    SemanticConfigError,
    build_eval_settings,
    open_eval_environment,
    open_semantic_eval_environment,
)
from eval_tools.qa.executor import evaluate_cases
from eval_tools.qa.metrics import (
    EXIT_DEFERRED,
    aggregate_qa,
    build_qa_gate,
    resolve_llm_eligibility,
    resolve_qa_profile,
    resolve_semantic_readiness,
)
from eval_tools.qa.report import (
    QaReportContext,
    build_qa_cli_summary,
    build_qa_report,
    qa_exit_code,
    to_markdown,
)
from eval_tools.qa.sideeffects import SNAPSHOT_OK, assess_side_effects

CLI_PROFILE_OFFLINE = "offline_fake"
CLI_PROFILE_SEMANTIC = "semantic_api"

# semantic_api 强制隔离目录（测试可 monkeypatch 这两个模块级常量以保持隔离）
SEMANTIC_OUTPUT_DIR = Path("/app/eval-run")
SEMANTIC_WORK_DIR = Path("/app/eval-run/data")
RUN_MARKER_NAME = ".run-started"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="阶段 9B 问答与学业评测")
    parser.add_argument(
        "--profile",
        choices=(CLI_PROFILE_OFFLINE, CLI_PROFILE_SEMANTIC),
        default=CLI_PROFILE_OFFLINE,
        help="offline_fake=完全离线（默认）；semantic_api=显式语义评测（强制 /app/eval-run）",
    )
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


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def _fail(label: str, **extra: object) -> int:
    """安全失败：只输出稳定标签、配置键名与异常类名，绝不含 URL/密钥/正文/异常原文。"""
    _emit({"ok": False, "error": label, **extra})
    return EXIT_DEFERRED


def _acquire_run_marker(output_dir: Path) -> bool:
    """以 ``O_EXCL`` 独占创建一次性标记；已存在即拒绝。

    调用方必须在本函数返回 ``False`` 时**在任何 Provider 调用之前**终止本次运行。
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        handle = os.open(output_dir / RUN_MARKER_NAME, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    os.close(handle)
    return True


def _write_reports(report: dict, output_dir: Path, cli_profile: str) -> int:
    json_path = output_dir / "qa-eval.json"
    markdown_path = output_dir / "qa-eval.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    markdown_path.write_text(to_markdown(report) + "\n", encoding="utf-8")

    summary = build_qa_cli_summary(
        report, json_report=json_path.name, markdown_report=markdown_path.name
    )
    summary["cli_profile"] = cli_profile
    summary["ok"] = True
    _emit(summary)
    return qa_exit_code(report)


def _run_offline(args: argparse.Namespace) -> int:
    """既有离线口径：行为与退出码保持不变。"""
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

    return _write_reports(report, output_dir, args.profile)


def _run_semantic(args: argparse.Namespace) -> int:
    """显式语义评测：强制隔离目录 + 一次性守卫 + 真实 Provider 审计（无 fake 回退）。"""
    output_dir = Path(args.output_dir) if args.output_dir else SEMANTIC_OUTPUT_DIR
    work_dir = Path(args.work_dir) if args.work_dir else SEMANTIC_WORK_DIR
    if output_dir != SEMANTIC_OUTPUT_DIR or work_dir != SEMANTIC_WORK_DIR:
        return _fail(
            "semantic_output_dir_required",
            output_dir=str(SEMANTIC_OUTPUT_DIR),
            work_dir=str(SEMANTIC_WORK_DIR),
        )

    # 一次性守卫：必须早于 Settings/Provider/环境装配
    if not _acquire_run_marker(output_dir):
        return _fail("semantic_run_already_started")

    run_id = args.run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    ground_truth_path = Path(args.ground_truth)
    try:
        cases = _load_raw_cases(ground_truth_path)
    except Exception as error:  # noqa: BLE001 - 只暴露稳定标签与类名
        return _fail("ground_truth_invalid", reason=type(error).__name__)

    try:
        base_settings = Settings()
    except Exception as error:  # noqa: BLE001 - 只暴露稳定标签与类名
        return _fail("settings_unavailable", reason=type(error).__name__)

    try:
        with open_semantic_eval_environment(
            base_settings,
            work_dir,
            demo_dataset_path=Path(args.demo_dataset_path),
            demo_dataset_version=args.demo_dataset_version,
        ) as environment:
            environment.ingest_demo()
            results = evaluate_cases(environment, cases)

            audits = environment.audit.snapshots()
            rerank_degraded = environment.audit.rerank.failed > 0
            isolation = environment.isolation
            assessment = (
                assess_side_effects(isolation, environment.snapshotter)
                if environment.snapshotter is not None
                else None
            )
            readiness = resolve_semantic_readiness(
                audits,
                rerank_degraded=rerank_degraded,
                isolation_available=bool(isolation is not None and isolation.usable),
                isolation_unchanged=bool(
                    assessment is not None
                    and assessment.status == SNAPSHOT_OK
                    and assessment.side_effect_free
                ),
            )
            metrics = aggregate_qa(results, semantic_ready=readiness.ready)
            eligibility = resolve_llm_eligibility(
                environment.settings.llm_provider,
                environment.settings.llm_model,
                explicit_profile=True,
                verified_call=environment.audit.llm.ok > 0,
                profile_missing_reason=(
                    "已显式选择 semantic_api profile，但本进程尚无成功的真实 LLM 调用。"
                ),
            )
            gate = build_qa_gate(resolve_qa_profile(eligibility, readiness=readiness), metrics)
            report = build_qa_report(
                QaReportContext(
                    run_id=run_id,
                    ground_truth_name=ground_truth_path.name,
                    dataset_version=args.demo_dataset_version,
                    embedding_provider=environment.embedding_provider,
                    rerank_provider=environment.rerank_provider,
                    generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    network_access=True,
                ),
                eligibility,
                gate,
                results,
                metrics,
                total_cases=len(cases),
                providers=audits,
                isolation_snapshot=None if isolation is None else isolation.snapshot,
                isolation_assessment=assessment,
                rerank_degraded=rerank_degraded,
            )
    except SemanticConfigError as error:
        return _fail("semantic_config_invalid", reason=error.reason, keys=list(error.keys))
    except Exception as error:  # noqa: BLE001 - 只暴露稳定标签与类名
        return _fail("semantic_run_failed", reason=type(error).__name__)

    return _write_reports(report, output_dir, args.profile)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.profile == CLI_PROFILE_SEMANTIC:
        return _run_semantic(args)
    return _run_offline(args)


if __name__ == "__main__":
    sys.exit(main())
