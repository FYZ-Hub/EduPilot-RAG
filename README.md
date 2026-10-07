# 校园多源文档 RAG 学业规划助手（EduPilot RAG）

以完全虚构的「启明大学」模拟资料为基础，做**受证据约束**的问答与**确定性规则**学分缺口规划。
本仓库不抓取任何真实校内系统，也不内置真实学生数据。

- 前端：Vue 3 + Vite + TypeScript + Element Plus（`frontend/`）
- 后端：Python 3.12 + FastAPI + SQLAlchemy + SQLite + Chroma（`backend/`）
- 编排：`docker-compose.yml`（CPU，默认）+ `docker-compose.gpu.yml`（可选 GPU 叠加）
- 产品与验收口径：`docs/PRODUCT_SPEC.md`、`docs/UI_SPEC.md`、`docs/IMPLEMENTATION_PLAN.md`

## 1. 前置条件

- Docker Desktop（含 Compose v2，`docker compose version` 可用）
- Windows PowerShell 5.1 或更高（仓库自带的 `run.ps1` / `stop.ps1` 以 `#Requires -Version 5.1` 声明）
- 以下 Fake Provider smoke 不下载模型权重、不调用外部模型；首次构建 Docker 镜像时仍可能需要联网获取基础镜像和软件依赖。若这些构建资源均已在本机缓存，可离线运行。

## 2. 约 10 分钟 Smoke（Fake Provider；已缓存依赖时可离线）

Fake Provider 用来在不调用外部模型、不下载模型权重的前提下跑通端到端链路；**它不代表真实模型质量**。首次运行时间取决于镜像和依赖是否已缓存。

1. 复制配置样例：

   ```powershell
   Copy-Item .env.example .env
   ```

2. 在 `.env` 中把三个 Provider 都改为 fake（`.env.example` 已默认 `APP_ENV=development`；生产环境禁止 fake）：

   ```dotenv
   EMBEDDING_PROVIDER=fake
   RERANK_PROVIDER=fake
   LLM_PROVIDER=fake
   ```

3. 启动（CPU 模式）：

   ```powershell
   .\run.ps1 -Mode cpu
   ```

   脚本会依次做 Compose 配置校验、`up --build --detach`，并**同时等待**后端
   `http://localhost:8000/api/health` 与前端 `http://localhost:5173` 都返回 HTTP 200，
   两者都通过后才输出启动成功。

4. 打开 `http://localhost:5173` 首页，通过导航或能力卡片进入**知识库管理**页面（`/knowledge`），在「演示资料」面板中
   **显式点击「加载演示资料」按钮**。演示导入不会被自动触发；禁用状态下按钮文案会变为
   「演示资料不可用」并给出原因。

5. 等待任务完成后，到 **RAG 问答**页面（`/chat`）提问；规划页在 `/planning`。

## 3. 三种运行模式

| 维度 | 取值 | 含义 |
|---|---|---|
| `-Mode cpu` / `-Mode gpu` | Compose **硬件模式** | 是否叠加 GPU overlay（`gpus: all`、`*_DEVICE=cuda`） |
| `EMBEDDING_PROVIDER` | `local` / `api` / `fake` | Embedding **Provider 类型** |
| `RERANK_PROVIDER` | `local` / `api` / `fake` | Reranker **Provider 类型** |
| `LLM_PROVIDER` | `openai_compatible` / `fake` | 不提供本地 LLM |

**关键区别**：`-Mode cpu|gpu` 只决定 Compose 是否申请 GPU，**不等于** Provider 类型。
`-Mode cpu` 一样可以使用 `api` Provider；`-Mode gpu` 也不会自动把 Provider 变成 `local`。

## 4. API 模式所需字段

只列键名，值全部留空，按需自行填写（**不要**把真实密钥提交进 Git）：

```dotenv
EMBEDDING_PROVIDER=api
EMBEDDING_BASE_URL=
EMBEDDING_API_KEY=
EMBEDDING_MODEL=

RERANK_PROVIDER=api
RERANK_BASE_URL=
RERANK_API_KEY=
RERANK_MODEL=

LLM_PROVIDER=openai_compatible
LLM_BASE_URL=
LLM_API_KEY=
LLM_MODEL=
```

- 这些键已由 `docker-compose.yml` 以 `${VAR:-default}` 透传，可从 `.env` 进入容器。
- **缺少 Key / Base URL 时服务仍可正常启动**；只有在真正调用该能力时才会返回安全的
  503 错误（`EMBEDDING_PROVIDER_UNAVAILABLE`、`RERANK_PROVIDER_UNAVAILABLE`、
  `LLM_PROVIDER_UNAVAILABLE`）。响应与日志只含稳定错误码，不回显密钥。
- 配置了但未成功调用过时，健康检查不会谎报 ready（见第 8 节）。

## 5. 本地模型的真实边界

- 默认 `backend` 镜像**不安装** `backend/requirements-embedding-local.txt`
  （torch / sentence-transformers / transformers / huggingface-hub，体积为数 GB）。
  因此**默认镜像无法直接做本地 BGE-M3 / BGE-Reranker 推理**。
- 需要本地推理时：自行在该镜像中安装该依赖文件，并把权重预先放入宿主机的 `./data/models`
  （容器内映射为 `MODEL_CACHE_PATH=/app/data/models`）。
- `EMBEDDING_LOCAL_FILES_ONLY=true` / `RERANK_LOCAL_FILES_ONLY=true`（默认）：
  **不会隐式下载权重**，缺权重会直接失败，而不是悄悄联网拉取。
- 因此**不得声称本地 CPU 或 GPU 可以直接使用**；请按上面的步骤显式准备依赖与权重。

## 6. GPU 模式当前状态

- GPU 模式目前**只完成静态 Compose 校验**（`config --quiet` 通过），
  **尚未做真实 GPU 运行验证**；本仓库不声称 GPU 已完成验收。
- GPU 叠加文件会设置 `profiles: [gpu]`、`gpus: all`，并把 `EMBEDDING_DEVICE` /
  `RERANK_DEVICE` 固定为 `cuda`。启用前请先自行完成容器 GPU 验证（见 `docs/PRODUCT_SPEC.md` §4.2）。

## 7. 数据持久化与恢复

- `./data` 保存全部持久化状态：SQLite（业务记录、任务状态）、Chroma 向量、上传原文、
  模型缓存。
- `stop.ps1` 只执行 `down`，**不删除**任何数据卷；再次 `run.ps1` 可继续使用原有数据。
- 演示导入是 SQLite 持久化的异步任务：进程中断后重启，租约过期的 `running` 任务会被
  重新入队，已完成的文档会被跳过或复用，任务可以继续推进。
- 手动删除 `./data` 等价于清空知识库与模型缓存（不可恢复）。

## 8. 健康检查语义

- `GET http://localhost:8000/api/health` **返回 HTTP 200 只表示后端进程可访问**。
  `status` 根据三项能力前置条件动态计算：`documents`、`chat`、`planning` 全为 `ready` 时是
  `healthy`，否则是 `degraded`。`healthy` 不代表端到端模型调用、回答质量或引用正确性已验证。
- 具体能力看 `capabilities`（`documents` / `chat` / `planning`）；Provider 成功调用证据看
  `providers.embedding.ready`、`providers.reranker.ready`、`providers.llm.ready`。API Provider
  首次成功调用前为 `false`，**不表示已经故障**，也不参与 `status` 判定。
- 因此 **200 或 `healthy` 都不等于全部 Provider ready**；`chat` 在未配置 LLM 或没有可检索语料时会分别为
  `unconfigured` / `unavailable`。空知识库不影响 `documents=ready`，但会使 `chat=unavailable`。

## 9. 演示数据、上传与常用命令

- 内置演示资料由知识库页面**显式点击**加载，不自动导入。
- 可选上传：PDF / DOCX / XLSX，走同一套校验、解析、切片与索引管线（见 `docs/PRODUCT_SPEC.md`）。

CPU 模式：

```powershell
docker compose -f docker-compose.yml ps
docker compose -f docker-compose.yml logs -f backend
docker compose -f docker-compose.yml restart backend
docker compose -f docker-compose.yml exec backend python -V
.\stop.ps1 -Mode cpu
```

GPU 模式（**每条命令都必须完整携带两个 Compose 文件与 `--profile gpu`**）：

```powershell
docker compose -f docker-compose.yml -f docker-compose.gpu.yml --profile gpu ps
docker compose -f docker-compose.yml -f docker-compose.gpu.yml --profile gpu logs -f backend
docker compose -f docker-compose.yml -f docker-compose.gpu.yml --profile gpu restart backend
docker compose -f docker-compose.yml -f docker-compose.gpu.yml --profile gpu exec backend python -V
.\stop.ps1 -Mode gpu
```

## 10. 常见问题

| 现象 | 处理 |
|---|---|
| 端口 8000 / 5173 被占用 | 先停止其它占用进程或既有容器（`stop.ps1`），再重新启动 |
| 知识库写操作（上传 / 加载演示资料）被禁用 | 说明 `documents` 能力不是 `ready`，按页面提示原因排查 |
| 问答提示「暂无可检索资料」 | 先到知识库加载演示资料或上传文档 |
| 使用 fake Provider 启动失败 | `APP_ENV=production` 时禁止 fake（`EMBEDDING_PROVIDER_FORBIDDEN`），改回 `development` |
| 本地 Provider 报 Provider 不可用 | 见第 5 节：默认镜像缺本地依赖与权重 |

## 11. 停止

```powershell
.\stop.ps1 -Mode cpu
# 或
.\stop.ps1 -Mode gpu
```

停止只移除容器与网络，`./data` 中的数据完整保留。

## 12. 依赖清单与复现证据

所有运行依赖均由版本化清单管理；复现时不需要在宿主机全局安装 Node.js、pnpm 或 Python 包。

| 范围 | 依赖清单 | 用途 |
|---|---|---|
| 后端运行时 | `backend/requirements.txt` | FastAPI、SQLAlchemy、Chroma、文档解析与 API 客户端 |
| 后端测试 | `backend/requirements-dev.txt` | 在运行时依赖基础上固定 pytest |
| 可选本地模型 | `backend/requirements-embedding-local.txt` | 本地 Embedding / Reranker；默认镜像不安装 |
| 前端 | `frontend/package.json`、`frontend/pnpm-lock.yaml` | Vue 3、Vite、TypeScript、Element Plus 与 Vitest |
| 演示语料生成器 | `scripts/generator/requirements.txt` | 固定 PDF、DOCX、XLSX 生成与校验依赖 |

环境变量模板为根目录 `.env.example`。其中包含端口、持久化路径、三个 Provider、检索、切片、
上传限制和演示数据开关；真实 API Key 只允许写入未被 Git 跟踪的 `.env`。

提交 `25c971d` 对应的离线验收记录：

- backend：`1273 passed`；
- frontend：`449 passed / 38 files`，生产构建通过；
- 演示语料与运行脚本：`124 passed, 1 skipped`；唯一 skip 是 Linux 生成器镜像没有 PowerShell，
  同一参数转发 harness 已在 Windows PowerShell 5.1 通过；
- CPU 与 GPU Compose 均在禁用仓库 `.env`、仅使用 `.env.example` 时通过 `config --quiet`；
- CPU smoke 已真实验证双端 HTTP 200、无 GPU 申请、显式 demo seed、任务重启恢复和持久化复用。

更完整的阶段验收与命令记录见 `PROJECT_STATUS.md`。

## 13. 已知质量边界

- 正式语义评测 run `20261006-152530` 的 `citation_support_rate` 为 `34/41 = 0.8293`，
  低于项目内部 `0.90` 门槛，因此阶段 9B 保持 `incomplete`，不得表述为已通过。
- 离线 smoke 使用 fake Provider，只用于验证系统链路，不代表真实模型回答质量。
- GPU 仅完成静态 Compose 校验；默认镜像不含本地模型依赖和权重，均不得表述为已完成运行验收。

## 14. 获取代码

公开仓库：`https://github.com/FYZ-Hub/EduPilot-RAG`

```powershell
git clone https://github.com/FYZ-Hub/EduPilot-RAG.git
Set-Location EduPilot-RAG
Copy-Item .env.example .env
```

仓库保留完整、细粒度 Git 演进历史。比赛用 AI 对话快照、演示视频和技术 PDF 作为独立提交材料，
不放入代码仓库，避免大文件和敏感终端记录进入 Git 历史。
