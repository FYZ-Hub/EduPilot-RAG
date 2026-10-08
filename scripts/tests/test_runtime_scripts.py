"""运行 / 停止脚本与 Compose 配置的离线契约测试。

只做纯静态检查：不启动容器、不访问网络、不读取 ``.env`` 的真实值。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import REPO_ROOT

RUN_SCRIPT = REPO_ROOT / "run.ps1"
STOP_SCRIPT = REPO_ROOT / "stop.ps1"
COMPOSE = REPO_ROOT / "docker-compose.yml"
GPU_OVERLAY = REPO_ROOT / "docker-compose.gpu.yml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
PRODUCT_SPEC = REPO_ROOT / "docs" / "PRODUCT_SPEC.md"
GITATTRIBUTES = REPO_ROOT / ".gitattributes"
README = REPO_ROOT / "README.md"

BACKEND_HEALTH_URL = "http://localhost:8000/api/health"
FRONTEND_URL = "http://localhost:5173"

MODE_BRANCH = 'if ($Mode -eq "gpu")'
# 只有这两类行才是对 docker 的调用（排除注释与错误提示文本）
DOCKER_INVOCATION = re.compile(r"&\s*docker\b|docker\s+compose\b")
# 只能在 gpu 分支内追加的 Compose 前缀片段
GPU_PREFIX_APPEND = re.compile(r'@\("(-f|--profile)"')
ENV_ASSIGNMENT = re.compile(r"^([A-Z][A-Z0-9_]*)=", re.MULTILINE)
API_KEY_ASSIGNMENT = re.compile(r"^([A-Z0-9_]*API_KEY)=(.*)$", re.MULTILINE)
SERVICES_ENTRY = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$", re.MULTILINE)
DOTENV_BLOCK = re.compile(r"```dotenv\n(.*?)```", re.DOTALL)
STANDALONE_SHORT_V = re.compile(r"(?<![\w-])-v(?![\w-])")
HELP_BLOCK = re.compile(r"<#.*?#>", re.DOTALL)
COMMENT_LINE = re.compile(r"(?m)^\s*#.*$")
SINGLE_LF = re.compile(rb"(?<!\r)\n")
STANDALONE_RM = re.compile(r"(?<![\w-])--rm(?![\w-])")
# 独立单字母短开关 -d：会被 PowerShell 当成函数参数吞掉，必须禁止
STANDALONE_SHORT_D = re.compile(r"(?<![\w-])-d(?![\w-])")
FUNCTION_BLOCK = re.compile(r"(?s)function Invoke-Compose \{.*?\n\}")
UP_CALL_LINE = re.compile(r"(?m)^\s*Invoke-Compose\s+up\b.*$")
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")

# api 模式必须能从 .env 进入容器的公开配置
REQUIRED_PASSTHROUGH = (
    "EMBEDDING_PROVIDER",
    "EMBEDDING_BASE_URL",
    "EMBEDDING_API_KEY",
    "EMBEDDING_MODEL",
    "EMBEDDING_DEVICE",
    "EMBEDDING_LOCAL_FILES_ONLY",
    "RERANK_PROVIDER",
    "RERANK_BASE_URL",
    "RERANK_API_KEY",
    "RERANK_MODEL",
    "RERANK_DEVICE",
    "RERANK_LOCAL_FILES_ONLY",
    "LLM_PROVIDER",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
    "CHUNK_TARGET_CHARS",
    "CHUNK_OVERLAP_CHARS",
    "DENSE_TOP_K",
    "KEYWORD_TOP_K",
    "RERANK_TOP_K",
    "RETRIEVAL_SCORE_THRESHOLD",
)


def _read(path: Path) -> str:
    # PowerShell 脚本按仓库约定存为 UTF-8 BOM，读取时统一去掉 BOM
    return path.read_text(encoding="utf-8-sig")


def _strip_comments(text: str) -> str:
    """去掉帮助块与整行注释，只保留可执行代码（避免注释文本干扰断言）。"""
    return COMMENT_LINE.sub("", HELP_BLOCK.sub("", text))


def _split_mode_branch(raw: str) -> tuple[str, str]:
    """按 gpu 分支标记切分代码：前者为 CPU 前缀，后者为 GPU 叠加片段。"""
    code = _strip_comments(raw)
    assert MODE_BRANCH in code, "脚本必须显式区分 cpu/gpu 分支"
    return code.split(MODE_BRANCH, 1)


def test_mode_parameter_is_cpu_or_gpu_only() -> None:
    for path in (RUN_SCRIPT, STOP_SCRIPT):
        text = _read(path)
        assert '[ValidateSet("cpu", "gpu")]' in text
        assert '[string]$Mode = "cpu"' in text
        # 不静默回退：模式只在参数缺省处赋值一次
        assert text.count('$Mode = "cpu"') == 1


def test_cpu_prefix_uses_base_compose_only() -> None:
    for path in (RUN_SCRIPT, STOP_SCRIPT):
        cpu_part, _ = _split_mode_branch(_read(path))
        assert '"compose", "-f", "docker-compose.yml"' in cpu_part
        assert "--profile" not in cpu_part
        assert "docker-compose.gpu.yml" not in cpu_part


def test_gpu_prefix_adds_overlay_and_profile() -> None:
    for path in (RUN_SCRIPT, STOP_SCRIPT):
        _, gpu_part = _split_mode_branch(_read(path))
        assert '"-f", "docker-compose.gpu.yml", "--profile", "gpu"' in gpu_part


def test_every_docker_invocation_reuses_the_shared_prefix() -> None:
    for path in (RUN_SCRIPT, STOP_SCRIPT):
        for line in _read(path).splitlines():
            if DOCKER_INVOCATION.search(line):
                assert "ComposeArgs" in line, line


def test_gpu_extra_prefix_is_appended_only_inside_the_gpu_branch() -> None:
    for path in (RUN_SCRIPT, STOP_SCRIPT):
        code = _strip_comments(_read(path))
        gpu_index = code.index(MODE_BRANCH)
        assert GPU_PREFIX_APPEND.search(code) is not None
        for match in GPU_PREFIX_APPEND.finditer(code):
            assert match.start() > gpu_index, "叠加文件与 profile 只能在 gpu 分支内追加"


def test_stop_script_never_removes_volumes() -> None:
    text = _read(STOP_SCRIPT)
    assert "down" in text
    assert "--volumes" not in text
    assert STANDALONE_SHORT_V.search(text) is None
    assert "--rmi" not in text


def test_base_compose_declares_no_gpu_and_keeps_two_services() -> None:
    text = _read(COMPOSE)
    assert "gpus" not in text
    assert "profiles" not in text
    assert "cuda" not in text
    assert "backend-gpu" not in text
    assert set(SERVICES_ENTRY.findall(text)) == {"backend", "frontend"}


def test_gpu_overlay_is_the_only_gpu_source() -> None:
    text = _read(GPU_OVERLAY)
    assert "profiles:" in text
    assert "gpu" in text
    assert "gpus: all" in text
    assert "EMBEDDING_DEVICE: cuda" in text
    assert "RERANK_DEVICE: cuda" in text


def test_api_credentials_and_models_can_reach_the_container() -> None:
    text = _read(COMPOSE)
    for key in REQUIRED_PASSTHROUGH:
        assert f"${{{key}:-" in text, key


def test_env_example_fields_are_all_wired_into_compose() -> None:
    env_text = _read(ENV_EXAMPLE)
    compose_text = _read(COMPOSE)
    env_keys = set(ENV_ASSIGNMENT.findall(env_text))
    assert env_keys
    assert sorted(key for key in env_keys if key not in compose_text) == []
    assert "RERANK_TOP_K=10" in env_text
    assert "${RERANK_TOP_K:-10}" in compose_text


def test_product_spec_config_block_matches_compose_and_top_k_ten() -> None:
    spec = _read(PRODUCT_SPEC)
    blocks = DOTENV_BLOCK.findall(spec)
    assert blocks, "PRODUCT_SPEC 必须保留 .env.example 片段"
    compose_text = _read(COMPOSE)
    spec_keys = set(ENV_ASSIGNMENT.findall(blocks[0]))
    assert spec_keys
    assert sorted(key for key in spec_keys if key not in compose_text) == []
    assert "RERANK_TOP_K=10" in spec
    assert "RERANK_TOP_K=6" not in spec


def test_scripts_never_print_secrets_and_never_seed() -> None:
    # run.ps1 负责配置校验，必须是静默模式（--quiet 不打印解析后的环境变量）
    run_code = _strip_comments(_read(RUN_SCRIPT))
    assert "config --quiet" in run_code
    for path in (RUN_SCRIPT, STOP_SCRIPT):
        raw = _read(path)
        for line in _strip_comments(raw).splitlines():
            if "config" in line:
                assert "--quiet" in line, line
        # 不读取 .env，也不展开容器环境变量
        assert ".env" not in raw
        assert "Get-Content" not in raw
        assert "$env:" not in raw
        # 不自动导入演示数据
        assert "seed" not in raw.lower()
        assert "generate_demo_corpus" not in raw


def test_configuration_files_contain_no_real_secret() -> None:
    for path in (ENV_EXAMPLE, COMPOSE):
        text = _read(path)
        assert "sk-" not in text
        for match in API_KEY_ASSIGNMENT.finditer(text):
            assert match.group(2).strip() == "", match.group(0)


def test_run_waits_for_both_backend_and_frontend() -> None:
    code = _strip_comments(_read(RUN_SCRIPT))
    assert f'$BackendHealthUrl = "{BACKEND_HEALTH_URL}"' in code
    assert f'$FrontendUrl = "{FRONTEND_URL}"' in code
    assert "$BackendReady = $false" in code
    assert "$FrontendReady = $false" in code
    # 只有两项都就绪才跳出轮询
    assert "if ($BackendReady -and $FrontendReady)" in code
    assert code.count("Test-HttpReady -Url $BackendHealthUrl") == 1
    assert code.count("Test-HttpReady -Url $FrontendUrl") == 1


def test_success_output_comes_after_both_readiness_checks() -> None:
    code = _strip_comments(_read(RUN_SCRIPT))
    guard = code.index("if (-not $BackendReady -or -not $FrontendReady)")
    assert code.index("启动成功") > guard
    assert code.index("前端：$FrontendUrl") > guard
    assert code.index("后端：$BackendHealthUrl") > guard


def test_readiness_polling_is_bounded_and_names_the_failed_endpoint() -> None:
    code = _strip_comments(_read(RUN_SCRIPT))
    assert "$Deadline = (Get-Date).AddSeconds($TimeoutSeconds)" in code
    assert "while ((Get-Date) -lt $Deadline)" in code
    assert "while ($true)" not in code
    # 超时必须点名失败端
    assert "失败端：" in code
    assert "后端 $BackendHealthUrl" in code
    assert "前端 $FrontendUrl" in code
    # 200 判定 + Windows PowerShell 5.1 兼容的取回方式
    assert "$Response.StatusCode -eq 200" in code
    assert "-UseBasicParsing" in code


def test_stop_script_prechecks_gpu_overlay() -> None:
    code = _strip_comments(_read(STOP_SCRIPT))
    _, gpu_part = _split_mode_branch(_read(STOP_SCRIPT))
    assert 'Test-Path -LiteralPath "docker-compose.gpu.yml"' in gpu_part
    # 仍然只执行 down，且不删除卷 / 镜像 / 容器数据
    assert "& docker @ComposeArgs down" in code
    assert "--rmi" not in code
    assert STANDALONE_RM.search(code) is None
    assert "--volumes" not in code
    assert STANDALONE_SHORT_V.search(code) is None


def test_powershell_scripts_are_bom_with_consistent_line_endings() -> None:
    """锁定 BOM、换行一致性与末尾单个逻辑换行。

    工作区物理 EOL 可能被编辑器自动保存改写（CRLF ↔ LF），因此不再要求工作区必须是
    CRLF；CRLF 的仓库级保证由 ``.gitattributes`` 的 ``text eol=crlf`` 负责。
    PowerShell 5.1 对中文脚本的解析依赖 UTF-8 BOM，由本断言与 Parser 语法检查共同覆盖。
    """
    for path in (RUN_SCRIPT, STOP_SCRIPT):
        data = path.read_bytes()
        assert data.startswith(b"\xef\xbb\xbf"), path.name
        crlf = data.count(b"\r\n")
        lf = data.count(b"\n")
        lone_lf = len(SINGLE_LF.findall(data))
        assert lf > 0, path.name
        # 换行必须一致：全部 LF，或全部 CRLF，不允许混用
        assert lone_lf in (0, lf), f"{path.name} 混用了 LF 与 CRLF"
        if lone_lf == 0:
            assert crlf == lf, path.name
        else:
            assert crlf == 0, path.name
        # 末尾恰好一个逻辑换行，且没有 EOF 空行
        assert data.endswith(b"\n"), path.name
        assert not data.endswith(b"\r\n\r\n"), path.name
        assert not data.endswith(b"\n\n"), path.name


def test_gitattributes_pins_crlf_for_root_scripts() -> None:
    text = _read(GITATTRIBUTES)
    for name in ("run.ps1", "stop.ps1"):
        assert f"/{name} text eol=crlf" in text


def test_readme_exists_and_is_substantial() -> None:
    assert README.is_file()
    assert len(_read(README)) > 1000


def test_readme_documents_offline_fake_smoke_with_explicit_demo_load() -> None:
    text = _read(README)
    for token in (
        ".env.example",
        "EMBEDDING_PROVIDER=fake",
        "RERANK_PROVIDER=fake",
        "LLM_PROVIDER=fake",
        ".\\run.ps1 -Mode cpu",
        "加载演示资料",
    ):
        assert token in text, token


def test_readme_documents_persistence_and_model_cache_boundary() -> None:
    text = _read(README)
    for token in (
        "requirements-embedding-local.txt",
        "MODEL_CACHE_PATH",
        "./data/models",
        "LOCAL_FILES_ONLY",
        "stop.ps1",
        "./data",
    ):
        assert token in text, token


def test_readme_is_honest_about_local_models_and_gpu() -> None:
    text = _read(README)
    # 必须明确默认镜像不安装本地模型依赖
    assert "不安装" in text
    assert "requirements-embedding-local.txt" in text
    # 必须如实区分 GPU 运行验收与本地模型 GPU 推理验收
    assert "NVIDIA GeForce RTX 4060 Laptop" in text
    assert "Docker GPU 透传与项目 GPU 模式启动通过" in text
    assert ".\\run.ps1 -Mode gpu" in text
    assert "本地模型 GPU 推理" in text
    assert "不得表述为“本地模型 GPU 推理已验证”" in text


def test_readme_gpu_examples_always_carry_overlay_and_profile() -> None:
    lines = [
        line.strip()
        for line in _read(README).splitlines()
        if line.strip().startswith("docker compose ")
    ]
    assert lines, "README 必须给出 docker compose 示例"
    gpu_lines = [line for line in lines if "docker-compose.gpu.yml" in line]
    assert gpu_lines, "README 必须给出 GPU 模式示例"
    for line in gpu_lines:
        assert "-f docker-compose.yml -f docker-compose.gpu.yml --profile gpu" in line, line
    joined = " ".join(gpu_lines)
    for subcommand in ("ps", "logs", "restart", "exec"):
        assert subcommand in joined, subcommand
    cpu_lines = [line for line in lines if "docker-compose.gpu.yml" not in line]
    assert cpu_lines, "README 必须给出 CPU 模式示例"
    for line in cpu_lines:
        assert "-f docker-compose.yml" in line, line
        assert "--profile" not in line, line


def test_readme_contains_no_real_secret_or_absolute_path() -> None:
    text = _read(README)
    assert "sk-" not in text
    for match in API_KEY_ASSIGNMENT.finditer(text):
        assert match.group(2).strip() == "", match.group(0)
    assert re.search(r"[A-Za-z]:\\", text) is None
    for host_prefix in ("/home/", "/Users/", "C:/", "D:/"):
        assert host_prefix not in text, host_prefix


def _up_call_lines(code: str) -> list[str]:
    return [line.strip() for line in UP_CALL_LINE.findall(code)]


def _forwarding_harness() -> str:
    """生成一段**纯离线**的 PowerShell stub 脚本，用于捕获 Invoke-Compose 的真实转发参数。

    stub 只把参数记进内存；不执行真实 Docker、不发 HTTP 请求、不读取任何 .env。
    """
    code = _strip_comments(_read(RUN_SCRIPT))
    function_block = FUNCTION_BLOCK.search(code)
    assert function_block is not None, "run.ps1 必须定义 Invoke-Compose"
    up_calls = _up_call_lines(code)
    assert len(up_calls) == 1, up_calls
    return "\n".join(
        [
            "Set-StrictMode -Version Latest",
            "$LASTEXITCODE = 0",
            "$script:Captured = New-Object System.Collections.ArrayList",
            "function docker { [void]$script:Captured.Add(($args -join '|')) }",
            "$ComposeArgs = @('compose', '-f', 'docker-compose.yml')",
            function_block.group(0),
            up_calls[0],
            "'CAPTURED=' + $script:Captured[0]",
        ]
    )


def test_run_script_uses_long_form_detach_switch() -> None:
    code = _strip_comments(_read(RUN_SCRIPT))
    up_calls = _up_call_lines(code)
    assert up_calls, "run.ps1 必须通过 Invoke-Compose 启动"
    for line in up_calls:
        assert "--build" in line, line
        assert "--detach" in line, line
    # 不得再出现独立单字母短开关，否则会被 PowerShell 当作函数参数吞掉
    assert STANDALONE_SHORT_D.search(code) is None


def test_config_invocation_always_uses_quiet() -> None:
    code = _strip_comments(_read(RUN_SCRIPT))
    config_lines = [line.strip() for line in code.splitlines() if "config" in line]
    assert config_lines, "run.ps1 必须做配置校验"
    for line in config_lines:
        assert "--quiet" in line, line


def test_forwarding_harness_is_offline_and_stubbed() -> None:
    harness = _forwarding_harness()
    assert "function docker" in harness
    for forbidden in ("Invoke-WebRequest", "Invoke-RestMethod", "Get-Content", ".env"):
        assert forbidden not in harness, forbidden


@pytest.mark.skipif(
    POWERSHELL is None,
    reason="当前环境没有 PowerShell，无法执行转发 harness（生成器镜像内跳过，已在宿主机用同一 harness 验证）",
)
def test_invoke_compose_forwards_up_build_detach(tmp_path: Path) -> None:
    harness_path = tmp_path / "forwarding-harness.ps1"
    # PowerShell 5.1 需要 BOM 才能正确解析中文脚本内容
    harness_path.write_text(_forwarding_harness(), encoding="utf-8-sig")
    completed = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(harness_path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    captured = [line for line in completed.stdout.splitlines() if line.startswith("CAPTURED=")]
    assert len(captured) == 1, completed.stdout
    assert captured[0].split("=", 1)[1] == "compose|-f|docker-compose.yml|up|--build|--detach"


def test_test_module_is_offline() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    # 本模块不得引入任何 HTTP 客户端（全部断言只读仓库文本）
    assert re.search(r"(?m)^\s*import\s+requests\b", source) is None
    assert re.search(r"(?m)^\s*(import|from)\s+urllib\b", source) is None
    assert re.search(r"(?m)^\s*(import|from)\s+httpx\b", source) is None
