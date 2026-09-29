# 阶段 9A 离线 RAG 检索评测入口（Docker 隔离运行）。
#
# 设计要点：
# - 每轮使用唯一隔离目录 ./.tmp/eval/<run-id>/，SQLite / Chroma / 报告全部写在其中；
# - 通过 `docker compose run --rm` 启动一次性 backend 容器，不占用默认 data 卷，不发布端口；
# - 只挂载评测输出目录；demo 语料沿用服务的只读挂载；
# - Embedding / Reranker 固定 fake，离线运行，不联网、不下载模型、不调用真实 LLM。
[CmdletBinding()]
param(
  [string]$RunId,
  [int]$TopK = 5,
  [double]$MinRecall = 0.85,
  [switch]$SkipBuild
)

$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

if ([string]::IsNullOrWhiteSpace($RunId)) {
  $RunId = Get-Date -Format 'yyyyMMdd-HHmmss'
}

$runRoot = Join-Path '.tmp/eval' $RunId
New-Item -ItemType Directory -Force -Path $runRoot | Out-Null

Write-Host "== 阶段 9A 检索评测 run id: $RunId"
Write-Host "== 隔离输出目录: $runRoot"
Write-Host "== top_k=$TopK min_recall=$MinRecall"

if (-not $SkipBuild) {
  Write-Host '== 构建 backend 镜像'
  docker compose build backend
  if ($LASTEXITCODE -ne 0) { throw 'backend 镜像构建失败' }
}

$mount = "./.tmp/eval/${RunId}:/app/eval-run"
docker compose run --rm --no-deps -v $mount backend python -m eval_tools `
  --output-dir /app/eval-run `
  --ground-truth /app/demo/ground_truth.jsonl `
  --top-k $TopK `
  --min-recall $MinRecall `
  --run-id $RunId

$exit = $LASTEXITCODE
Write-Host "== 评测退出码: $exit"
Write-Host "== 报告: $runRoot"
exit $exit
