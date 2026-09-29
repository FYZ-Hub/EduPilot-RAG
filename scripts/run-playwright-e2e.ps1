# 阶段 8 端到端验收运行脚本（Docker 主入口 / Host 预留入口）。
#
# 设计要点：
# - 每轮使用唯一 .tmp/e2e/<run-id>，data / test-results / snapshots / logs 全部隔离；
# - 默认 Docker 模式使用官方 Playwright 镜像；国内加速源只能经参数或环境变量覆盖；
# - 绝不读写默认 data 卷、绝不使用 -v 删除卷、绝不执行 git clean/reset；
# - try/finally：无论成功、失败还是异常，都会 down 掉 E2E overlay 并恢复默认 compose；
# - 退出码：主流程、恢复流程、快照一致性三者都成功才返回 0。
[CmdletBinding()]
param(
  [ValidateSet('Docker', 'Host')]
  [string]$Mode = 'Docker',

  [switch]$SkipBuild,

  [switch]$SmokeOnly,

  [string]$RunId,

  [string]$PlaywrightBaseImage
)

# 原生 docker 命令会向 stderr 输出进度；若把 ErrorActionPreference 设为 Stop，
# PowerShell 会把 stderr 当成终止错误。这里统一用 Continue，并对每个命令显式检查
# $LASTEXITCODE；REST/cmdlet 调用各自使用 -ErrorAction Stop 保持真实失败可见。
$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$overlay = @('-f', 'docker-compose.yml', '-f', 'docker-compose.e2e.yml')
$defaultApi = 'http://localhost:8000/api'
$defaultWeb = 'http://localhost:5173'

# ---------------------------------------------------------------- 工具函数

function Write-Step([string]$text) {
  Write-Host "== $text"
}

function Resolve-PlaywrightBaseImage {
  if (-not [string]::IsNullOrWhiteSpace($PlaywrightBaseImage)) { return $PlaywrightBaseImage }
  if (-not [string]::IsNullOrWhiteSpace($env:PLAYWRIGHT_BASE_IMAGE)) { return $env:PLAYWRIGHT_BASE_IMAGE }
  return 'mcr.microsoft.com/playwright:v1.49.1-jammy'
}

function Wait-HttpOk([string]$url, [int]$totalTimeoutSec = 120) {
  $deadline = (Get-Date).AddSeconds($totalTimeoutSec)
  while ((Get-Date) -lt $deadline) {
    try {
      $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
      if ($response.StatusCode -eq 200) { return $true }
    } catch {
      Start-Sleep -Seconds 3
    }
  }
  return $false
}

function Get-Json([string]$url) {
  $result = Invoke-RestMethod -Uri $url -TimeoutSec 15 -ErrorAction Stop
  if ($null -eq $result) { throw "接口返回为空：$url" }
  return $result
}

function Save-Snapshot([string]$dir, [string]$prefix) {
  $health = Get-Json "$defaultApi/health"
  $documents = Get-Json "$defaultApi/documents"
  $demo = Get-Json "$defaultApi/demo/status"
  $academic = Get-Json "$defaultApi/academic/options"

  $health | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 (Join-Path $dir "$prefix-health.json")
  $documents | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 (Join-Path $dir "$prefix-documents.json")
  $demo | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 (Join-Path $dir "$prefix-demo-status.json")
  $academic | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 (Join-Path $dir "$prefix-academic-options.json")

  # 规范化摘要：只保留业务状态、数量与 ID（忽略响应时长等易变字段）
  $summary = [ordered]@{
    capabilities      = $health.capabilities
    documents_total   = $documents.total
    document_ids      = @($documents.items | Sort-Object id | ForEach-Object { $_.id })
    document_statuses = @($documents.items | Sort-Object id | ForEach-Object { $_.status })
    counts            = $documents.counts
    demo_state        = $demo.state
    demo_loaded       = $demo.loaded
    demo_active_job   = $demo.active_job_id
    demo_last_job     = $demo.last_job_id
    demo_active_ver   = $demo.active_dataset_version
    record_sets       = @($academic.record_sets | Sort-Object id | ForEach-Object { $_.id })
    rule_sets         = @($academic.rule_sets | Sort-Object id | ForEach-Object { $_.id })
  }
  $summary | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 (Join-Path $dir "$prefix-summary.json")
  return $summary
}

function Test-SummaryEqual($left, $right) {
  $a = $left | ConvertTo-Json -Depth 8 -Compress
  $b = $right | ConvertTo-Json -Depth 8 -Compress
  if ($a -ne $b) {
    Write-Host "!! 默认环境快照不一致"
    Write-Host "before: $a"
    Write-Host "after : $b"
    return $false
  }
  return $true
}

# ---------------------------------------------------------------- 参数校验

if ($Mode -eq 'Host') {
  Write-Host 'Host 模式尚未在本脚本中实现（本轮只交付 Docker 主入口）。'
  Write-Host '如需在本机使用 Edge，请先确保 node/pnpm 已在当前进程 PATH 中，并等待后续轮次实现。'
  exit 2
}

if ([string]::IsNullOrWhiteSpace($RunId)) {
  $RunId = Get-Date -Format 'yyyyMMdd-HHmmss'
}

$runRoot = Join-Path '.tmp/e2e' $RunId
$dataDir = Join-Path $runRoot 'data'
$resultsDir = Join-Path $runRoot 'test-results'
$snapshotDir = Join-Path $runRoot 'snapshots'
$logDir = Join-Path $runRoot 'logs'
New-Item -ItemType Directory -Force -Path $dataDir, $resultsDir, $snapshotDir, $logDir | Out-Null

$resolvedImage = Resolve-PlaywrightBaseImage
$env:E2E_DATA_DIR = './' + ($dataDir -replace '\\', '/')
$env:E2E_RESULTS_DIR = './' + ($resultsDir -replace '\\', '/')
$env:PLAYWRIGHT_OUTPUT_DIR = '/app/test-results'
$env:PLAYWRIGHT_BASE_IMAGE = $resolvedImage

Write-Step "E2E run id: $RunId"
Write-Step "E2E data dir: $dataDir"
Write-Step "E2E results dir: $resultsDir"
Write-Step "Playwright base image: $resolvedImage"

$mainExit = 1
$restoreExit = 1
$snapshotsEqual = $false
$beforeSummary = $null
$failure = $null

try {
  # ------------------------------------------------------------ 0. 默认环境就绪
  Write-Step '等待默认环境就绪'
  if (-not (Wait-HttpOk "$defaultApi/health" 120)) { throw '默认 backend 未在 120 秒内就绪' }
  if (-not (Wait-HttpOk $defaultWeb 120)) { throw '默认 frontend 未在 120 秒内就绪' }

  # ------------------------------------------------------------ 1. 默认环境快照
  Write-Step '保存默认环境开始前快照'
  $beforeSummary = Save-Snapshot $snapshotDir 'before'

  # ------------------------------------------------------------ 2. overlay 配置校验
  Write-Step 'overlay config --quiet'
  docker compose @overlay config --quiet
  if ($LASTEXITCODE -ne 0) { throw 'overlay compose 配置无效' }
  docker compose @overlay config | Set-Content -Encoding utf8 (Join-Path $logDir 'e2e-compose-config.yaml')

  # ------------------------------------------------------------ 3. 构建
  if (-not $SkipBuild) {
    Write-Step '构建 E2E frontend 镜像'
    docker compose @overlay build frontend 2>&1 | Tee-Object -FilePath (Join-Path $logDir 'build.log')
    if ($LASTEXITCODE -ne 0) { throw 'E2E frontend 镜像构建失败' }
  }

  # ------------------------------------------------------------ 4. 启动隔离环境
  Write-Step '启动隔离 E2E backend / frontend'
  docker compose @overlay up -d --force-recreate backend frontend
  if ($LASTEXITCODE -ne 0) { throw '启动 E2E 服务失败' }

  Write-Step '等待 E2E backend 健康'
  if (-not (Wait-HttpOk "$defaultApi/health" 120)) { throw 'E2E backend 未在 120 秒内就绪' }

  Write-Step '等待 E2E frontend 返回 HTTP 200'
  if (-not (Wait-HttpOk $defaultWeb 120)) { throw 'E2E frontend 未在 120 秒内就绪' }

  # ------------------------------------------------------------ 5. E2E API 检查
  Write-Step '读取 E2E API 初始状态'
  $e2eHealth = Get-Json "$defaultApi/health"
  $e2eDocuments = Get-Json "$defaultApi/documents"
  $e2eDemo = Get-Json "$defaultApi/demo/status"
  $e2eAcademic = Get-Json "$defaultApi/academic/options"
  [ordered]@{
    capabilities = $e2eHealth.capabilities
    providers    = $e2eHealth.providers
    total        = $e2eDocuments.total
    counts       = $e2eDocuments.counts
    demo         = $e2eDemo
    academic     = $e2eAcademic
  } | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 (Join-Path $logDir 'e2e-initial-state.json')

  if ($e2eHealth.providers.embedding.provider -ne 'fake') { throw 'E2E embedding provider 不是 fake' }
  if ($e2eHealth.providers.reranker.provider -ne 'fake') { throw 'E2E rerank provider 不是 fake' }
  if ($e2eDemo.loaded -ne $false) { throw 'E2E 数据集不应处于 loaded 状态' }
  if ($e2eDemo.last_job_id) { throw 'E2E 数据库不是全新隔离库（存在历史 job）' }
  if ($e2eAcademic.record_sets.Count -ne 0 -or $e2eAcademic.rule_sets.Count -ne 0) {
    throw 'E2E 学业选项不是空集'
  }

  # ------------------------------------------------------------ 6. 容器内版本与可写性
  Write-Step '检查容器内 pnpm / Playwright 版本与产物目录可写性'
  $pnpmVersion = (docker compose @overlay exec -T frontend pnpm --version | Select-Object -Last 1).Trim()
  $playwrightVersion = (docker compose @overlay exec -T frontend pnpm exec playwright --version | Select-Object -Last 1).Trim()
  Write-Host "pnpm=$pnpmVersion playwright=$playwrightVersion"
  if ($pnpmVersion -notmatch '9\.12\.3') { throw "容器内 pnpm 版本不是 9.12.3：$pnpmVersion" }
  if ($playwrightVersion -notmatch '1\.49\.1') { throw "容器内 Playwright 版本不是 1.49.1：$playwrightVersion" }

  docker compose @overlay exec -T frontend sh -c "echo ok > /app/test-results/.write-probe"
  if ($LASTEXITCODE -ne 0) { throw '/app/test-results 不可写' }
  docker compose @overlay exec -T frontend sh -c "rm -f /app/test-results/.write-probe"
  if ($LASTEXITCODE -ne 0) { throw '无法清理 /app/test-results 探测文件' }

  # ------------------------------------------------------------ 7. 业务测试
  if ($SmokeOnly) {
    Write-Step 'SmokeOnly：跳过 Playwright 业务测试'
  } else {
    Write-Step '运行 Playwright 业务测试'
    docker compose @overlay exec -T frontend pnpm exec playwright test 2>&1 |
      Tee-Object -FilePath (Join-Path $logDir 'playwright.log')
    if ($LASTEXITCODE -ne 0) { throw "Playwright 业务测试失败（退出码 $LASTEXITCODE）" }
  }

  $mainExit = 0
} catch {
  $failure = $_
  Write-Host "!! 主流程失败：$($_.Exception.Message)"
  $mainExit = 1
} finally {
  # ------------------------------------------------------------ 8. 停止 E2E overlay
  Write-Step '停止 E2E overlay'
  docker compose @overlay down --remove-orphans 2>&1 | Tee-Object -FilePath (Join-Path $logDir 'overlay-down.log')
  $downExit = $LASTEXITCODE

  # ------------------------------------------------------------ 9. 恢复默认服务
  Write-Step '恢复默认 docker compose 服务'
  docker compose up -d --force-recreate 2>&1 | Tee-Object -FilePath (Join-Path $logDir 'restore.log')
  $upExit = $LASTEXITCODE

  $healthy = Wait-HttpOk "$defaultApi/health" 120
  $webOk = Wait-HttpOk $defaultWeb 120

  $restoreExit = 1
  if ($downExit -eq 0 -and $upExit -eq 0 -and $healthy -and $webOk) { $restoreExit = 0 }
  Write-Host "restore: down=$downExit up=$upExit health=$healthy web=$webOk"

  # ------------------------------------------------------------ 10. 恢复后快照与比较
  try {
    $afterSummary = Save-Snapshot $snapshotDir 'after'
    $snapshotsEqual = Test-SummaryEqual $beforeSummary $afterSummary
  } catch {
    Write-Host "!! 恢复后快照失败：$($_.Exception.Message)"
    $snapshotsEqual = $false
  }

  docker compose config --quiet
  docker compose ps | Tee-Object -FilePath (Join-Path $logDir 'restore-ps.log')
}

if ($failure) {
  Write-Host "主流程错误：$($failure.Exception.Message)"
}

if ($mainExit -eq 0 -and $restoreExit -eq 0 -and $snapshotsEqual) {
  Write-Step "完成：runDir=$runRoot"
  exit 0
}

Write-Host "!! 未通过（main=$mainExit restore=$restoreExit snapshotsEqual=$snapshotsEqual）；产物保留在 $runRoot"
exit 1
