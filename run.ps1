#Requires -Version 5.1
<#
.SYNOPSIS
  以固定模式构建并启动校园 RAG 本地环境。

.DESCRIPTION
  -Mode cpu（缺省）：所有命令只使用基础 Compose。
  -Mode gpu：所有命令统一叠加 docker-compose.gpu.yml 并启用 gpu Profile。
  流程：配置校验（config --quiet）→ up --build --detach → 就绪等待 → 输出前后端地址。
  就绪等待要求后端健康端点与前端首页同时返回 HTTP 200，轮询有界，超时明确指出失败端。
  不输出任何密钥或环境变量解析结果，不自动导入演示数据，GPU 失败时绝不回退 CPU。
#>
[CmdletBinding()]
param(
    [ValidateSet("cpu", "gpu")]
    [string]$Mode = "cpu"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Set-Location -LiteralPath $PSScriptRoot

# 唯一命令前缀：GPU 的每一项操作都必须携带叠加文件与 profile。
$ComposeArgs = @("compose", "-f", "docker-compose.yml")
if ($Mode -eq "gpu") {
    if (-not (Test-Path -LiteralPath "docker-compose.gpu.yml")) {
        throw "GPU 模式需要 docker-compose.gpu.yml，但该文件不存在。"
    }
    $ComposeArgs += @("-f", "docker-compose.gpu.yml", "--profile", "gpu")
}

$BackendHealthUrl = "http://localhost:8000/api/health"
$FrontendUrl = "http://localhost:5173"
$TimeoutSeconds = 180
$PollIntervalSeconds = 3

function Invoke-Compose {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$ComposeCommand)
    & docker @ComposeArgs @ComposeCommand
    if ($LASTEXITCODE -ne 0) {
        throw "Docker 命令失败（退出码 $LASTEXITCODE）；请检查上方输出后重试。"
    }
}

function Test-HttpReady {
    param([Parameter(Mandatory = $true)][string]$Url)
    try {
        $Response = Invoke-WebRequest -Uri $Url -Method Get -TimeoutSec 3 -UseBasicParsing
        return ($Response.StatusCode -eq 200)
    } catch {
        return $false
    }
}

Write-Host "校园 RAG 启动：Mode=$Mode"
Write-Host "停止请执行 stop.ps1 -Mode $Mode"
Write-Host ""

# 1) 配置校验：--quiet 抑制解析后的环境变量输出，避免泄漏密钥
Invoke-Compose config --quiet
Write-Host "[1/3] Compose 配置校验通过。"

# 2) 构建镜像并后台启动（必须使用长开关 --detach：单字母短开关会被 PowerShell 当作函数参数吞掉）
Invoke-Compose up --build --detach
Write-Host "[2/3] 容器已后台启动。"

# 3) 就绪等待：后端健康端点与前端首页都必须返回 HTTP 200，轮询有界
$Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$BackendReady = $false
$FrontendReady = $false
while ((Get-Date) -lt $Deadline) {
    if (-not $BackendReady) {
        $BackendReady = Test-HttpReady -Url $BackendHealthUrl
    }
    if (-not $FrontendReady) {
        $FrontendReady = Test-HttpReady -Url $FrontendUrl
    }
    if ($BackendReady -and $FrontendReady) {
        break
    }
    Start-Sleep -Seconds $PollIntervalSeconds
}

if (-not $BackendReady -or -not $FrontendReady) {
    $Failed = @()
    if (-not $BackendReady) { $Failed += "后端 $BackendHealthUrl" }
    if (-not $FrontendReady) { $Failed += "前端 $FrontendUrl" }
    throw "在 $TimeoutSeconds 秒内未就绪（失败端：$($Failed -join '；')）。"
}
Write-Host "[3/3] 后端健康端点与前端首页均返回 HTTP 200。"

Write-Host ""
Write-Host "启动成功（Mode=$Mode）。"
Write-Host "前端：$FrontendUrl"
Write-Host "后端：$BackendHealthUrl"
