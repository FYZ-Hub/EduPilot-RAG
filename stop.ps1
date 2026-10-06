#Requires -Version 5.1
<#
.SYNOPSIS
  停止校园 RAG 本地环境，保留全部数据卷。

.DESCRIPTION
  -Mode cpu|gpu 与 run.ps1 使用完全相同的 Compose 前缀：cpu 只用基础 Compose，
  gpu 先做叠加文件存在性预检，再统一叠加 docker-compose.gpu.yml 并启用 gpu Profile。
  只执行 down，不删除数据卷、镜像或任何持久化数据，因此 SQLite、Chroma、
  上传文件与模型缓存都会保留。
#>
[CmdletBinding()]
param(
    [ValidateSet("cpu", "gpu")]
    [string]$Mode = "cpu"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Set-Location -LiteralPath $PSScriptRoot

$ComposeArgs = @("compose", "-f", "docker-compose.yml")
if ($Mode -eq "gpu") {
    if (-not (Test-Path -LiteralPath "docker-compose.gpu.yml")) {
        throw "GPU 模式需要 docker-compose.gpu.yml，但该文件不存在。"
    }
    $ComposeArgs += @("-f", "docker-compose.gpu.yml", "--profile", "gpu")
}

Write-Host "校园 RAG 停止：Mode=$Mode"
& docker @ComposeArgs down
if ($LASTEXITCODE -ne 0) {
    throw "Docker 停止命令失败（退出码 $LASTEXITCODE）。"
}
Write-Host "容器与网络已移除；数据卷 ./data 保留，未删除任何数据。"
