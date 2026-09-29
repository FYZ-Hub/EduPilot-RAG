# 阶段 9B 问答与学业评测入口（Docker 隔离运行）。
#
# - 每轮唯一隔离目录 ./.tmp/eval-qa/<run-id>/（SQLite / Chroma / 报告全部写在其中）；
# - `docker compose run --rm` 启动一次性 backend 容器，不占用默认 data 卷，不发布端口；
# - Embedding / Reranker / LLM 全部 fake，完全离线，不联网、不下载模型。
[CmdletBinding()]
param(
  [string]$RunId,
  [switch]$SkipBuild
)

$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

if ([string]::IsNullOrWhiteSpace($RunId)) {
  $RunId = Get-Date -Format 'yyyyMMdd-HHmmss'
}

$runRoot = Join-Path '.tmp/eval-qa' $RunId
New-Item -ItemType Directory -Force -Path $runRoot | Out-Null

Write-Host "== 阶段 9B 评测 run id: $RunId"
Write-Host "== 隔离输出目录: $runRoot"

if (-not $SkipBuild) {
  Write-Host '== 构建 backend 镜像'
  docker compose build backend
  if ($LASTEXITCODE -ne 0) { throw 'backend 镜像构建失败' }
}

$mount = "./.tmp/eval-qa/${RunId}:/app/eval-run"
docker compose run --rm --no-deps -v $mount backend python -m eval_tools.qa `
  --output-dir /app/eval-run `
  --ground-truth /app/demo/ground_truth.jsonl `
  --run-id $RunId

$exit = $LASTEXITCODE
Write-Host "== 评测退出码: $exit"
Write-Host "== 报告: $runRoot"
exit $exit
