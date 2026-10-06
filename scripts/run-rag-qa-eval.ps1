# 阶段 9B 问答与学业评测入口（Docker 隔离运行）。
#
# - 每轮唯一隔离目录 ./.tmp/eval-qa/<run-id>/（SQLite / Chroma / 报告全部写在其中）；
# - 两种 profile：
#     offline_fake（默认）：Fake Provider，完全离线，不联网、不下载模型；
#     semantic_api：真实 DeepSeek / SiliconFlow 语义评测，用 --env-file 注入 .env，
#                   强制隔离目录 /app/eval-run，不挂载默认 data 卷；
# - 退出码原样透传：0=全部达标、1=具备资格的指标未达标、2=deferred（配置/Provider/降级/隔离异常）。
#   脚本不把 1/2 改写为成功，也不重试。
[CmdletBinding()]
param(
  [ValidateSet('offline_fake', 'semantic_api')]
  [string]$Profile = 'offline_fake',
  [string]$RunId,
  [switch]$SkipBuild
)

$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

if ([string]::IsNullOrWhiteSpace($RunId)) {
  $RunId = Get-Date -Format 'yyyyMMdd-HHmmss'
}

# 绝对路径由脚本自身位置推导（兼容 Windows、空格与中文；不写死本机路径）
$runRoot = Join-Path (Join-Path $repo '.tmp/eval-qa') $RunId
$demoRoot = Join-Path $repo 'demo'
$envFile = Join-Path $repo '.env'
$backendImage = 'campus-rag-backend:0.1.0'

Write-Host "== 阶段 9B 评测 profile: $Profile"
Write-Host "== 阶段 9B 评测 run id: $RunId"
Write-Host "== 隔离输出目录: $runRoot"

if ($Profile -eq 'semantic_api') {
  # 立即停机条件（在任何容器/Provider 调用之前）；不读取也不回显 .env 内容
  if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
    Write-Host '== 停止：semantic_api 需要根目录 .env 注入真实 Provider 配置（未读取其内容）'
    exit 2
  }
  if (Test-Path -LiteralPath $runRoot) {
    Write-Host '== 停止：该 run id 目录已存在，semantic_api 每轮必须使用新目录'
    exit 2
  }
}

New-Item -ItemType Directory -Force -Path $runRoot | Out-Null

if (-not $SkipBuild) {
  Write-Host '== 构建 backend 镜像'
  docker compose build backend
  if ($LASTEXITCODE -ne 0) { throw 'backend 镜像构建失败' }
}

if ($Profile -eq 'semantic_api') {
  # 只挂载本轮隔离目录与只读 demo；不挂载默认 data 卷；.env 仅经 --env-file 注入
  docker run --rm `
    --env-file $envFile `
    -v "${runRoot}:/app/eval-run" `
    -v "${demoRoot}:/app/demo:ro" `
    $backendImage python -m eval_tools.qa `
    --profile semantic_api `
    --output-dir /app/eval-run `
    --work-dir /app/eval-run/data `
    --ground-truth /app/demo/ground_truth.jsonl `
    --run-id $RunId
} else {
  $mount = "${runRoot}:/app/eval-run"
  docker compose run --rm --no-deps -v $mount backend python -m eval_tools.qa `
    --output-dir /app/eval-run `
    --ground-truth /app/demo/ground_truth.jsonl `
    --run-id $RunId
}

$exit = $LASTEXITCODE
Write-Host "== 评测退出码: $exit"
Write-Host "== 报告: $runRoot"
exit $exit
