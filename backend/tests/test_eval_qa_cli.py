"""阶段 9B CLI（``python -m eval_tools.qa``）接线测试。

全部使用 Fake / monkeypatch：不联网、不读 ``.env``、不触发任何真实 Provider 调用。
覆盖：profile 默认值、强制隔离目录、一次性守卫、退出码 0/1/2、无 fake 回退与错误输出安全。
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import pytest

from app.config import Settings

from eval_tools.qa import __main__ as cli
from eval_tools.qa.metrics import (
    GROUP_CITATION,
    GROUP_INJECTION_SAFETY,
    GROUP_PLANNING,
    GROUP_REFUSAL_CONTRACT,
    PROVIDER_NAMES,
    QaCaseResult,
    new_provider_audit,
    record_provider_call,
)
from eval_tools.qa.sideeffects import (
    SNAPSHOT_OK,
    SNAPSHOT_UNAVAILABLE,
    IsolationBaseline,
    IsolationSnapshot,
)


# --- Fake 组件 --------------------------------------------------------------


class _FakeMeter:
    def __init__(self, audit, *, calls: int, ok: int, failed: int) -> None:
        self.audit = audit
        self.calls = calls
        self.ok = ok
        self.failed = failed


class _FakeRegistry:
    def __init__(self, meters: dict) -> None:
        self._meters = meters
        self.embedding = meters["embedding"]
        self.rerank = meters["rerank"]
        self.llm = meters["llm"]
        self.judge = meters["judge"]

    def snapshots(self):
        return [self._meters[name].audit for name in PROVIDER_NAMES]


class _FakeEnvironment:
    def __init__(
        self,
        *,
        registry,
        isolation=None,
        snapshotter=None,
        embedding_provider: str = "api",
        rerank_provider: str = "api",
    ) -> None:
        self.audit = registry
        self.isolation = isolation
        self.snapshotter = snapshotter
        self.settings = _semantic_settings()
        self.embedding_provider = embedding_provider
        self.rerank_provider = rerank_provider
        self.ingested = False

    def ingest_demo(self) -> None:
        self.ingested = True


class _Probe:
    """离线路径用的 /llm 描述符探针替身。"""

    class _Descriptor:
        provider = "fake"
        model = "fake-llm"

    descriptor = _Descriptor()

    def close(self) -> None:
        pass


def _semantic_settings() -> Settings:
    return Settings(
        app_env="eval",
        log_level="WARNING",
        embedding_provider="api",
        embedding_base_url="https://embed.example.invalid",
        embedding_model="bge-m3",
        embedding_api_key="test-key",
        rerank_provider="api",
        rerank_base_url="https://rerank.example.invalid",
        rerank_model="bge-reranker",
        rerank_api_key="test-key",
        llm_provider="openai_compatible",
        llm_base_url="https://llm.example.invalid",
        llm_model="deepseek-chat",
        llm_api_key="test-key",
    )


def _snapshot(*, vectors: int = 1) -> IsolationSnapshot:
    digest = "0" * 32
    return IsolationSnapshot(
        documents_count=1,
        documents_digest=digest,
        status_digest=digest,
        chunks_count=1,
        chunks_digest=digest,
        vectors_count=vectors,
        vectors_digest=digest,
        fts_count=1,
        fts_digest=digest,
    )


def _audits(*, verified: bool = True, rerank_failed: bool = False):
    audits = []
    for name in PROVIDER_NAMES:
        succeeded = verified and not (name == "rerank" and rerank_failed)
        audits.append(
            record_provider_call(new_provider_audit(name, configured=True), succeeded=succeeded)
        )
    return audits


def _registry(*, verified: bool = True, rerank_failed: bool = False) -> _FakeRegistry:
    meters = {}
    for audit in _audits(verified=verified, rerank_failed=rerank_failed):
        ok = 1 if audit.verified else 0
        meters[audit.name] = _FakeMeter(
            audit, calls=1, ok=ok, failed=0 if audit.verified else 1
        )
    return _FakeRegistry(meters)


def _cases(*, formal: bool = True) -> list[QaCaseResult]:
    return [
        QaCaseResult(
            case_id="p1", group=GROUP_PLANNING, question="q", observed_pass=formal, formal_pass=formal
        ),
        QaCaseResult(
            case_id="c1",
            group=GROUP_CITATION,
            question="q",
            observed_pass=formal,
            citation_contract_ok=True,
            citation_source_hit=True,
            formal_pass=formal,
        ),
        QaCaseResult(
            case_id="r1",
            group=GROUP_REFUSAL_CONTRACT,
            question="q",
            observed_pass=formal,
            formal_pass=formal,
        ),
        QaCaseResult(
            case_id="i1",
            group=GROUP_INJECTION_SAFETY,
            question="q",
            observed_pass=formal,
            formal_pass=formal,
        ),
    ]


@pytest.fixture()
def semantic_dirs(tmp_path, monkeypatch):
    """把强制隔离目录重定向到 tmp，保证测试彼此隔离。"""
    output_dir = tmp_path / "eval-run"
    work_dir = output_dir / "data"
    monkeypatch.setattr(cli, "SEMANTIC_OUTPUT_DIR", output_dir)
    monkeypatch.setattr(cli, "SEMANTIC_WORK_DIR", work_dir)
    monkeypatch.setattr(cli, "_load_raw_cases", lambda path: [{"id": "c", "category": "x"}])
    monkeypatch.setattr(cli, "Settings", _semantic_settings)
    return output_dir, work_dir


def _install_semantic(
    monkeypatch,
    *,
    registry=None,
    isolation=None,
    snapshotter=None,
    results=None,
    opener=None,
):
    calls: list[dict] = []
    environment = _FakeEnvironment(
        registry=registry if registry is not None else _registry(),
        isolation=isolation if isolation is not None else IsolationBaseline(status=SNAPSHOT_OK, snapshot=_snapshot()),
        snapshotter=snapshotter if snapshotter is not None else (lambda: _snapshot()),
    )

    def _open(base_settings, work_dir, **kwargs):
        calls.append({"settings": base_settings, "work_dir": work_dir, "kwargs": kwargs})
        if opener is not None:
            return opener(environment)
        return contextlib.nullcontext(environment)

    monkeypatch.setattr(cli, "open_semantic_eval_environment", _open)
    monkeypatch.setattr(
        cli, "evaluate_cases", lambda environment, cases, **kwargs: results if results is not None else _cases()
    )
    monkeypatch.setattr(
        cli,
        "open_eval_environment",
        lambda *args, **kwargs: pytest.fail("semantic_api 不得回退到 offline 环境"),
    )
    monkeypatch.setattr(
        cli, "build_eval_settings", lambda *args, **kwargs: pytest.fail("semantic_api 不得构建离线配置")
    )
    return calls, environment


def _semantic_args(dirs, **overrides) -> list[str]:
    output_dir, work_dir = dirs
    args = {
        "--profile": cli.CLI_PROFILE_SEMANTIC,
        "--output-dir": str(output_dir),
        "--work-dir": str(work_dir),
    }
    args.update(overrides)
    return [item for pair in args.items() for item in pair]


# --- profile 选择 -----------------------------------------------------------


def test_profile_defaults_to_offline_fake() -> None:
    assert cli._parse_args(["--output-dir", "/tmp/x"]).profile == "offline_fake"
    assert cli._parse_args(["--profile", "semantic_api", "--output-dir", "/tmp/x"]).profile == (
        "semantic_api"
    )


def test_unknown_profile_is_rejected() -> None:
    with pytest.raises(SystemExit):
        cli._parse_args(["--profile", "semantic", "--output-dir", "/tmp/x"])


# --- 强制隔离目录 -----------------------------------------------------------


def test_semantic_requires_forced_output_dir(semantic_dirs, monkeypatch, tmp_path, capsys) -> None:
    calls, _ = _install_semantic(monkeypatch)

    code = cli.main(
        ["--profile", "semantic_api", "--output-dir", str(tmp_path / "elsewhere")]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 2
    assert payload["error"] == "semantic_output_dir_required"
    assert payload["output_dir"] == str(cli.SEMANTIC_OUTPUT_DIR)
    assert payload["work_dir"] == str(cli.SEMANTIC_WORK_DIR)
    assert calls == []  # 未做任何 Provider 侧装配


# --- 一次性守卫 -------------------------------------------------------------


def test_semantic_second_run_refused_before_any_provider_call(
    semantic_dirs, monkeypatch, capsys
) -> None:
    calls, _ = _install_semantic(monkeypatch)

    first = cli.main(_semantic_args(semantic_dirs))
    capsys.readouterr()
    second = cli.main(_semantic_args(semantic_dirs))
    payload = json.loads(capsys.readouterr().out)

    assert first == 0
    assert second == 2
    assert payload["error"] == "semantic_run_already_started"
    assert len(calls) == 1  # 第二次未触发任何装配/调用
    assert (cli.SEMANTIC_OUTPUT_DIR / cli.RUN_MARKER_NAME).exists()


# --- 退出码 0/1/2 -----------------------------------------------------------


def test_semantic_exit_0_when_ready_and_all_pass(semantic_dirs, monkeypatch, capsys) -> None:
    _install_semantic(monkeypatch)

    code = cli.main(_semantic_args(semantic_dirs))
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["ok"] is True
    assert payload["cli_profile"] == "semantic_api"
    assert payload["stage9b_passed"] is True
    assert payload["exit_code"] == 0
    assert payload["citation_support_rate"] == 1.0
    assert payload["semantic_readiness"]["ready"] is True


def test_semantic_exit_1_when_formal_metric_below_threshold(
    semantic_dirs, monkeypatch, capsys
) -> None:
    _install_semantic(monkeypatch, results=_cases(formal=False))

    code = cli.main(_semantic_args(semantic_dirs))
    payload = json.loads(capsys.readouterr().out)

    assert code == 1
    assert payload["exit_code"] == 1
    # 未达标同样使阶段完成门禁为 incomplete（不得视为通过）
    assert payload["stage_completion"] == "incomplete"
    assert payload["stage9b_passed"] is False
    assert payload["not_yet_evaluated"] == []
    assert payload["citation_support_rate"] == 0.0
    assert payload["planning_correctness"] == 0.0


def test_semantic_exit_2_when_rerank_degraded(semantic_dirs, monkeypatch, capsys) -> None:
    _install_semantic(monkeypatch, registry=_registry(rerank_failed=True))

    code = cli.main(_semantic_args(semantic_dirs))
    payload = json.loads(capsys.readouterr().out)

    assert code == 2
    assert payload["stage_completion"] == "incomplete"
    assert payload["semantic_readiness"]["rerank_degraded"] is True
    assert "rerank_degraded" in payload["semantic_readiness"]["rejections"]
    assert payload["citation_support_rate"] is None


def test_semantic_exit_2_when_providers_not_verified(semantic_dirs, monkeypatch, capsys) -> None:
    _install_semantic(monkeypatch, registry=_registry(verified=False))

    code = cli.main(_semantic_args(semantic_dirs))
    payload = json.loads(capsys.readouterr().out)

    assert code == 2
    assert "providers_not_verified" in payload["semantic_readiness"]["rejections"]


def test_semantic_exit_2_when_isolation_changed(semantic_dirs, monkeypatch, capsys) -> None:
    # 基线与运行后快照不同 → 判定为发生变化
    _install_semantic(
        monkeypatch,
        isolation=IsolationBaseline(status=SNAPSHOT_OK, snapshot=_snapshot(vectors=1)),
        snapshotter=lambda: _snapshot(vectors=2),
    )

    code = cli.main(_semantic_args(semantic_dirs))
    payload = json.loads(capsys.readouterr().out)

    assert code == 2
    assert "isolation_changed" in payload["semantic_readiness"]["rejections"]
    assert payload["isolation"]["assessment"]["side_effect_free"] is False


def test_semantic_exit_2_when_isolation_snapshot_unavailable(
    semantic_dirs, monkeypatch, capsys
) -> None:
    _install_semantic(
        monkeypatch,
        isolation=IsolationBaseline(status=SNAPSHOT_UNAVAILABLE, reason="RuntimeError"),
    )

    code = cli.main(_semantic_args(semantic_dirs))
    payload = json.loads(capsys.readouterr().out)

    assert code == 2
    assert "isolation_snapshot_unavailable" in payload["semantic_readiness"]["rejections"]
    assert payload["isolation"]["assessment"]["status"] == "unavailable"


# --- 配置、安全与不回退 -----------------------------------------------------


def test_semantic_uses_process_env_settings_and_forced_work_dir(
    semantic_dirs, monkeypatch, capsys
) -> None:
    calls, _ = _install_semantic(monkeypatch)

    assert cli.main(_semantic_args(semantic_dirs)) == 0
    capsys.readouterr()

    assert len(calls) == 1
    assert calls[0]["work_dir"] == cli.SEMANTIC_WORK_DIR
    # 配置来自进程环境（此处由 monkeypatch 的 Settings 代填），且非 fake
    assert calls[0]["settings"].embedding_provider == "api"
    assert calls[0]["settings"].llm_provider == "openai_compatible"


def test_semantic_config_error_reports_keys_only(semantic_dirs, monkeypatch, capsys) -> None:
    def _boom(*args, **kwargs):
        raise cli.SemanticConfigError("provider_kind_mismatch", ("embedding_provider",))

    _install_semantic(monkeypatch, opener=_boom)

    code = cli.main(_semantic_args(semantic_dirs))
    payload = json.loads(capsys.readouterr().out)

    assert code == 2
    assert payload["error"] == "semantic_config_invalid"
    assert payload["reason"] == "provider_kind_mismatch"
    assert payload["keys"] == ["embedding_provider"]


def test_semantic_error_output_hides_secrets_and_traceback(
    semantic_dirs, monkeypatch, capsys
) -> None:
    def _boom(*args, **kwargs):
        raise RuntimeError("https://llm.example.invalid api_key=sk-secret-123 body=原文")

    _install_semantic(monkeypatch, opener=_boom)

    code = cli.main(_semantic_args(semantic_dirs))
    stdout = capsys.readouterr().out

    assert code == 2
    payload = json.loads(stdout)
    assert payload["error"] == "semantic_run_failed"
    assert payload["reason"] == "RuntimeError"
    assert "sk-secret-123" not in stdout
    assert "https://" not in stdout
    assert "原文" not in stdout
    assert "Traceback" not in stdout


def test_semantic_report_marks_network_access_true(semantic_dirs, monkeypatch, capsys) -> None:
    _install_semantic(monkeypatch)

    assert cli.main(_semantic_args(semantic_dirs)) == 0
    capsys.readouterr()

    report = json.loads((cli.SEMANTIC_OUTPUT_DIR / "qa-eval.json").read_text(encoding="utf-8"))
    markdown = (cli.SEMANTIC_OUTPUT_DIR / "qa-eval.md").read_text(encoding="utf-8")

    assert report["config"]["network_access"] is True
    assert "- 联网=True" in markdown
    payload = json.dumps(report, ensure_ascii=False)
    assert "sk-secret" not in payload and "example.invalid" not in payload
    assert report["providers"] and len(report["providers"]) == 4


# --- 离线路径保持 -----------------------------------------------------------


def test_offline_profile_still_returns_exit_code_2(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "_load_raw_cases", lambda path: [{"id": "c", "category": "x"}])
    monkeypatch.setattr(cli, "build_eval_settings", lambda *args, **kwargs: _semantic_settings())
    monkeypatch.setattr(cli, "build_llm_provider", lambda settings: _Probe())

    class _OfflineEnvironment:
        settings = _semantic_settings()
        embedding_provider = "fake"
        rerank_provider = "fake"

        def ingest_demo(self) -> None:
            pass

    monkeypatch.setattr(
        cli,
        "open_eval_environment",
        lambda settings: contextlib.nullcontext(_OfflineEnvironment()),
    )
    monkeypatch.setattr(cli, "evaluate_cases", lambda environment, cases, **kwargs: _cases())
    monkeypatch.setattr(
        cli,
        "open_semantic_eval_environment",
        lambda *args, **kwargs: pytest.fail("offline_fake 不得走 semantic 环境"),
    )

    code = cli.main(["--output-dir", str(tmp_path / "offline")])
    payload = json.loads(capsys.readouterr().out)

    assert code == 2
    assert payload["cli_profile"] == "offline_fake"
    assert payload["profile"] == "offline_fake_qa"
    assert payload["stage9b_passed"] is False
    assert payload["not_yet_evaluated"] == [
        "citation_support_rate",
        "refusal_correctness",
        "injection_resistance",
    ]
    # 离线不创建一次性标记
    assert not (tmp_path / "offline" / cli.RUN_MARKER_NAME).exists()


def test_offline_profile_does_not_touch_semantic_dirs(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "SEMANTIC_OUTPUT_DIR", tmp_path / "forced")
    monkeypatch.setattr(cli, "SEMANTIC_WORK_DIR", tmp_path / "forced" / "data")
    monkeypatch.setattr(cli, "_load_raw_cases", lambda path: [{"id": "c", "category": "x"}])
    monkeypatch.setattr(cli, "build_eval_settings", lambda *args, **kwargs: _semantic_settings())
    monkeypatch.setattr(cli, "build_llm_provider", lambda settings: _Probe())

    class _OfflineEnvironment:
        settings = _semantic_settings()
        embedding_provider = "fake"
        rerank_provider = "fake"

        def ingest_demo(self) -> None:
            pass

    monkeypatch.setattr(
        cli,
        "open_eval_environment",
        lambda settings: contextlib.nullcontext(_OfflineEnvironment()),
    )
    monkeypatch.setattr(cli, "evaluate_cases", lambda environment, cases, **kwargs: _cases())

    assert cli.main(["--output-dir", str(tmp_path / "offline")]) == 2
    capsys.readouterr()
    assert not Path(tmp_path / "forced").exists()
