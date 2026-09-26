# Environment Report

> 阶段 0（环境预检）结果，2026-09-26 复检更新。本报告不含密钥、完整环境变量或机器隐私信息。
> 本轮未拉取/运行任何 CUDA 镜像，未执行 GPU 容器测试；Docker Desktop 未由 Agent 启动，WSL、注册表与 Docker 设置均未被修改。

## Summary

- Overall status: **PASS**
- Blocking items: 无
- Warnings（不阻塞阶段 0，后续统一在 Docker 容器内执行）:
  1. 宿主机未检测到 **Node.js**（前端构建/测试统一在 Docker 容器内执行）。
  2. 宿主机未检测到 **pnpm**（同上，容器内执行）。
  3. 宿主机未检测到 **uv**（后端统一在 Docker 容器内执行）。
  4. 物理内存 15.8 GB（建议 16 GB 边界）；Docker Desktop 分配给 Linux 引擎的内存约 **7.66 GiB**，运行本地大模型时需留意，默认 CPU + API 模式不受影响。
  5. 容器 GPU 透传未验证（可选，仅 `gpu` Profile 需要；本轮未拉取 CUDA 镜像）。
- Unable to verify:
  1. 容器 GPU 测试：本轮明确未拉取 `nvidia/cuda:12.8.1-base-ubuntu24.04`，也未运行 `--gpus all` 容器；仅影响可选 `gpu` Profile，不影响默认 CPU 模式。
- Resolved:
  1. **Docker daemon 可连接**（ServerVersion 29.5.3），阶段 1 容器验收不再受 daemon 阻塞。
  2. **Git 仓库已初始化**：分支 `main`。
  3. **本仓库 Git 身份已配置**（仅 local，未改 global），首次文档基线提交已完成。

## Versions

| Component | Required | Detected | Status |
|---|---|---|---|
| OS | Windows 10/11 x64 | Windows 11 Home China 10.0.26200 (x64) | PASS |
| Git CLI | any | 2.55.0.windows.1 | PASS |
| Git repository | 需在业务开发前存在 | 已初始化，分支 `main` | PASS |
| Git identity | 首次提交前需设置 | 本仓库 identity 已配置 | PASS |
| Docker CLI | present | 29.5.3 (windows/amd64) | PASS |
| Docker daemon | reachable | reachable（ServerVersion 29.5.3） | PASS |
| Docker Compose | v2 子命令 | v5.1.4 | PASS |
| WSL | WSL2 | 默认分发 Ubuntu，默认版本 2 | PASS |
| Node.js | 前端需要（容器内执行） | 宿主机未检测到 | WARNING |
| pnpm | 前端需要（容器内执行） | 宿主机未检测到 | WARNING |
| Python | 3.12 | 3.12.10（宿主机） | PASS |
| uv | 建议（容器内执行） | 宿主机未检测到 | WARNING |
| NVIDIA GPU | 可选（仅 gpu Profile） | RTX 4060 Laptop 8GB | PASS |

## Docker Engine

- Context: `desktop-linux`（active）；另存在 `default`
- Daemon reachable: **Yes**
- ServerVersion: **29.5.3**（Docker Desktop 4.79.0，Engine 29.5.3，containerd v2.2.5，runc 1.3.6）
- Server platform: OSType `linux`，Arch `x86_64`，Driver `overlayfs`，Server CPUs 32，分配内存约 7.66 GiB
- Compose available: Yes（`docker compose` v5.1.4）
- Disk usage (`docker system df`): Images 8 / 12.95 GB（可回收 738.9 MB）；Containers 5 / 2.75 MB；Local Volumes 11 / 402.4 MB（可回收 128.7 MB）；Build Cache 42 / 5.525 GB

## GPU

- Host GPU: NVIDIA GeForce RTX 4060 Laptop，显存 8188 MiB
- Driver: 581.80
- Host CUDA capability: CUDA 13.0（驱动上报）
- Container GPU test: **NOT RUN（本轮按指示未拉取镜像，保持可选）**

如需容器 GPU 验证，将使用以下镜像（须用户确认后才拉取，本轮未执行）：

| Item | Value |
|---|---|
| 精确镜像名 | `nvidia/cuda:12.8.1-base-ubuntu24.04` |
| 用途 | 验证容器 GPU 透传（`docker run --rm --gpus all <image> nvidia-smi`） |
| 预计下载体积 | 约 100–150 MB（压缩层，视平台/缓存略有浮动） |
| 是否默认模式必需 | 否。默认 CPU/API 兼容模式不需要该镜像与 CUDA |

说明：GPU 验证失败只阻塞 `gpu` Profile，不阻塞默认 CPU 兼容模式。

## Git

- Repository: 已初始化（`git init -b main`），分支 `main`
- Baseline commit: `docs: establish project specifications and environment baseline`（仅规划文档、规则、环境报告与阶段状态）
- Identity: 本仓库 `user.name` 与 `user.email` 已配置（仅 local，未修改 global；报告中不展示完整邮箱）
- Working tree: 仅规划类文件（`.gitignore`、`.trae/rules/`、`campus-rag-trae-project.md`、`docs/`、`ENVIRONMENT_REPORT.md`、`PROJECT_STATUS.md`）
- Sensitive content scan: 未发现 `.env`、API Key、数据库、模型、上传文件或个人数据
- Remote: 未配置，未 push（按要求不上传 GitHub）

## Ports

| Port | Required by | Available | Existing process |
|---|---|---|---|
| 5173 | frontend (Vite) | Yes | none |
| 8000 | backend (FastAPI) | Yes | none |

## Storage

| Drive | Free space | Status |
|---|---|---|
| C | 275.1 GB | PASS |
| D | 261.7 GB | PASS |
| E | 275.0 GB | PASS |
| F | 221.5 GB | PASS |

要求：至少 25 GB 可用；本地模型建议保留 40 GB。当前各盘均满足。

## System

- 内存：15.8 GB 总（建议 16 GB，边界值）
- 逻辑处理器：32
- 架构：AMD64

## Required user actions

1. 无阻塞项；阶段 0 已通过。
2. （可选）如需验证容器 GPU 透传：确认后拉取 `nvidia/cuda:12.8.1-base-ubuntu24.04`（约 100–150 MB）；仅 `gpu` Profile 需要，默认 CPU 模式不需要。
3. Node.js / pnpm / uv 保持 WARNING，无需在宿主机安装；前端与后端构建、测试统一在 Docker 容器内执行。
4. 下一阶段为**阶段 1：Docker 前后端骨架**（默认 CPU 模式，不申请 GPU）。

## Commands executed

复检（2026-09-26，用户已手动启动 Docker Desktop）：

- `docker version` — exit 0 — Client 29.5.3 + Server 29.5.3（Docker Desktop 4.79.0）均可用
- `docker info --format ...` — 输出 `ServerVersion=29.5.3 Driver=overlayfs OSType=linux Arch=x86_64 CPUs=32`；docker 命令本身 exit 0（注：调用过程中沙箱拦截了对 Docker Desktop 日志路径 `C:\Users\lenovo\AppData\Local\Docker\log\host\docker-desktop.exe.log` 的读取，非致命，不影响 daemon 判定）
- `docker compose version` — exit 0 — v5.1.4
- `docker context show` — exit 0 — `desktop-linux`
- `docker system df` — exit 0 — Images 8/12.95GB，Containers 5，Volumes 11/402.4MB，Build Cache 42/5.525GB
- `git rev-parse --is-inside-work-tree`（复检前）— exit 128 — 当时尚不是仓库
- `git init -b main` — exit 0 — 初始化空仓库于 `F:/毕业实训/EduPilot RAG1/.git/`
- `git config --local user.name` / `--local user.email` — exit 1 — 未配置
- `git config --global user.name` / `--global user.email` — exit 1 — 未配置
- `git status --short` — exit 0 — 仅 5 项未跟踪规划文件，无敏感内容
- `git branch --show-current` — exit 0 — `main`

收尾（2026-09-26，配置本仓库 Git 身份并完成文档基线提交）：

- `git config --local user.name "FYZ-Hub"` — exit 0 — 仅本仓库
- `git config --local user.email "***"` — exit 0 — 仅本仓库（不展示完整值）
- `git config --local --get user.name` — exit 0 — `FYZ-Hub`
- `git config --local --get user.email` — exit 0 — 已配置（不展示完整值）
- `docker info --format ...` — exit 0 — `ServerVersion=29.5.3 OSType=linux`（沙箱仍拦截 Docker Desktop 日志路径读取，非致命）
- `git branch --show-current` — exit 0 — `main`
- `Get-NetTCPConnection -State Listen -LocalPort 5173,8000` — exit 0 — 无监听（端口可用）
- `git diff --cached --check` / `git diff --cached --name-only` — 见提交记录，暂存区仅规划文档
- `git commit -m "docs: establish project specifications and environment baseline"` — 见最终提交结果
- `git log --oneline --decorate -1` / `git status --short` — 见最终提交结果

上一轮（2026-09-26 首次预检，保留备查）：

- `Get-Location` — exit 0 — `F:\毕业实训\EduPilot RAG1`
- `Test-Path .git` — exit 0 — `False`
- `docker version` / `docker info` / `docker system df` — exit 1 — 当时 daemon 不可连接
- `wsl --status` — exit 0 — 默认分发 Ubuntu，默认版本 2
- `wsl -l -v` — exit 0 — `Ubuntu`(Stopped, v2)、`docker-desktop`(Stopped, v2)
- `nvidia-smi` — exit 0 — RTX 4060 Laptop 8GB，Driver 581.80，CUDA 13.0
- `git --version` — exit 0 — 2.55.0.windows.1
- `node --version` / `pnpm --version` / `uv --version` — CommandNotFoundException — 未安装/未在 PATH
- `python --version` — exit 0 — 3.12.10
- `Get-NetTCPConnection -State Listen -LocalPort 5173,8000` — exit 0 — 无监听（端口可用）
- `Get-PSDrive -PSProvider FileSystem` — exit 0 — C/D/E/F 可用空间均 > 200 GB
