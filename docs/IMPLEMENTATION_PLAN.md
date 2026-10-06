# 分阶段实施、测试与验收计划

## 1. 执行方式

每轮只完成一个阶段，依次执行检查、方案、实施、验证、汇报，并更新 `PROJECT_STATUS.md`。阶段状态只允许 `not_started`、`in_progress`、`blocked`、`completed`。实际验收未通过不得标记完成。

禁止：

- 一次性生成整个项目，或未检查仓库就覆盖文件。
- 删除用户改动、测试或弱化断言来掩盖失败。
- 声称未运行的命令通过。
- 未经用户确认安装宿主机软件或下载大型镜像/模型。
- 把密钥、真实学生数据、运行数据库或模型写入 Git。

## 2. 阶段 0：环境预检

首次运行只执行本阶段。PowerShell 依次运行并记录退出码和关键结果；单项失败后继续其余只读检查：

```powershell
docker version
docker info
docker compose version
docker context show
docker system df
wsl --status
wsl -l -v
nvidia-smi
git --version
node --version
pnpm --version
python --version
uv --version
Get-NetTCPConnection -LocalPort 5173,8000 -ErrorAction SilentlyContinue
Get-PSDrive -PSProvider FileSystem
```

同时检查：

- 当前目录、Git 分支和未提交改动，不清理用户内容。
- 端口 5173、8000 及占用进程。
- Docker context、daemon 可达性和 Compose v2 子命令。
- Windows 10/11 x64、WSL2 Linux Containers、建议 16GB 内存、至少 25GB 空间；本地模型建议 40GB。

只有 daemon 可用后才考虑 GPU 验证。如果本机没有该镜像，必须先向用户说明精确镜像名 `nvidia/cuda:12.8.1-base-ubuntu24.04`、用于验证容器 GPU 透传、预计下载体积，并说明它不是 CPU/API 模式的必要条件；获得确认后才能拉取或运行：

```powershell
docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi
```

GPU 验证失败只阻塞 `gpu` Profile，不阻塞默认 CPU 兼容模式。

`ENVIRONMENT_REPORT.md` 使用：

```markdown
# Environment Report

## Summary
- Overall status: PASS | BLOCKED
- Blocking items:
- Warnings:
- Unable to verify:

## Versions
| Component | Required | Detected | Status |

## Docker Engine
- Context:
- Daemon reachable:
- Compose available:
- Disk usage:

## GPU
- Host GPU:
- Driver:
- Host CUDA capability:
- Container GPU test:

## Ports
| Port | Required by | Available | Existing process |

## Storage
| Drive | Free space | Status |

## Required user actions
1. ...

## Commands executed
- command
- exit code
- concise result
```

报告不得包含密钥、完整环境变量或不必要的机器隐私信息。

当前文档编写时的非权威基线：Windows x64；Docker CLI 29.5.3、Compose v5.1.4、context `desktop-linux`，daemon 当时不可连接；RTX 4060 Laptop 8GB；Git、Node、pnpm、Python 可用。Trae 必须重新检测，不能把此基线当成当前事实。

阶段输出：`ENVIRONMENT_REPORT.md`。Docker daemon 不可连接、必要端口冲突未处理或磁盘不足时停止阶段 1。

## 3. 阶段 1：Docker 前后端骨架

输出：符合 `docs/UI_SPEC.md` Token 和应用框架的 Vue 路由骨架、FastAPI `/api/health`、Dockerfile、Compose、`.env.example`、`.gitignore`、`PROJECT_STATUS.md`。阶段 1 只实现 AppLayout、导航、空页面壳和全局样式，不提前实现阶段 8 业务页面。

要求：

- 默认 Compose 不申请 GPU，Embedding/Reranker device 均为 `cpu`。
- `docker-compose.gpu.yml` 才为同名 `backend` 设置 `profiles: [gpu]`、`gpus: all`，并以 Compose environment 字面量覆盖 CUDA 设备与 8/4 批次；GPU 命令始终合并两个 Compose 文件，不能同时启动第二个后端，也不增加 GPU 专用 env 文件。
- 数据目录持久化，模拟源目录只读。
- 缺少 LLM API 配置时服务可启动并报告相关能力不可用，不能泄漏配置值。

验收：

```powershell
docker compose config
docker compose up --build -d
docker compose ps
Invoke-RestMethod http://localhost:8000/api/health
docker compose logs --no-color --tail 100
```

另用 `docker compose -f docker-compose.yml -f docker-compose.gpu.yml --profile gpu config` 静态验证 GPU 配置；除非阶段 0 的容器 GPU 检查通过，不实际启动 GPU Profile。

停止条件：服务不健康、默认配置要求 CUDA、端口不可访问、密钥或数据目录进入 Git。

提交建议：`feat: scaffold dockerized vue and fastapi application`

## 4. 阶段 2：模拟语料、上传与解析

输入：`docs/DEMO_DATA_SPEC.md` 的事实模型和三种格式。

输出：

- 确定性生成脚本、12–18 个固化文件、manifest、ground truth。
- 安全上传、SHA-256、文档状态、`DocumentBlock`、解析预览。
- `demo_seed_jobs`、逐文档任务、演示关联表与 worker lease 基础设施。
- 三个演示 API；服务端 job `target_stage` 当前固定为 `parsed`，任务可在该阶段正常终止，但 `loaded` 必须保持 false。阶段 3、4 依次把服务端目标扩展为 `vector_indexed`、`completed`，并复用同一 `document_pipeline_state`。

测试：确定性生成、manifest 完整性、允许名单/禁止模式隐私扫描、异步 202、并发 POST 复用任务、进程重启恢复、正常/空/损坏/伪造扩展名/重复/中文名文件、Excel 空表和合并单元格。

停止条件：生成不稳定、出现真实数据、路径穿越、manifest 不一致、接口内同步执行重任务、任务状态只保存在内存、解析定位丢失。

提交建议：`feat: add reproducible demo corpus and async ingestion`

## 5. 阶段 3：语义切片与 Chroma

输出：包含 parser/chunker 版本的确定性 chunk ID、批量 Embedding、Chroma 持久化、索引状态；worker 增加 `chunked`、`vector_indexed` 持久化检查点。Embedding 计算本身不是独立检查点，只有向量 upsert 并对账后才推进。

验收：FakeEmbedding 不下载模型；验证重复入库、更新、删除、容器重启、各阶段崩溃恢复、Embedding 指纹变化后的定向重建。已完成向量化的当前指纹文档不得重复生成向量。

停止条件：向量维度不一致、引用定位丢失、旧向量残留、仅因 document 行存在就错误跳过。

提交建议：`feat: persist resumable document vectors in chroma`

## 6. 阶段 4：FTS5 与混合检索

输出：FTS5、Dense/Keyword 召回、RRF 结果、诊断信息；worker 增加 `keyword_indexed` 和 `completed` 检查点。

验收：

- 课程代码、文件名、政策条款由关键词召回；语义问题由向量召回。
- Chroma 与 FTS5 的 `chunk_id` 集合一致。
- 首次演示任务完成全部文件；第二次全部 `skipped` 且无重复。
- 单文件失败为 `completed_with_errors`，重试只恢复失败或过期项。
- chunk、Embedding 或索引指纹变化时从正确阶段重建。

停止条件：两套索引无法对齐、过滤失效、任务提前标记 completed、重试产生重复记录。

提交建议：`feat: add hybrid retrieval and complete demo ingestion`

## 7. 阶段 5：Reranker

输入：RRF 候选。输出：默认前 10 条带分数和引用元数据的结果（硬上限 10，可显式请求更少）。

验收：FakeReranker 可重复；CPU 默认可运行；GPU Profile 批次受限；Embedding 与 Reranker 不无界占用显存；存在 API/CPU 降级路径。

停止条件：引用丢失、显存溢出、默认测试依赖 GPU 或网络。

提交建议：`feat: rerank hybrid retrieval candidates`

## 8. 阶段 6：SSE 问答与引用

输入：Product Spec 限定的当前标签页消息与 filters，以及重排后的至多 10 条证据。输出严格符合公共契约的 `token`、`citation`、`done`、`error`，包含拒答/冲突 outcome 和完整引用定位。

测试：正常回答、无结果拒答、API 超时、取消、流中断、引用对齐、提示注入、冲突文档。

停止条件：引用不存在的 chunk、文档提示能覆盖系统规则、无证据生成确定答案。

提交建议：`feat: stream grounded answers with citations`

## 9. 阶段 7：确定性学分规则引擎

输入：课程记录和培养方案规则。输出：可持久选择的 record/rule set、`GET /api/academic/options`、显式 ID 选择的 `PlanningResult` 与证据。

测试：重修、同课程多次成绩、在修、类别变化、版本冲突、总学分已满但缺必修课。

停止条件：LLM 参与数值计算、结果不可复现、无规则来源。

提交建议：`feat: calculate academic credit gaps`

## 10. 阶段 8：Vue 核心页面

实施前必须完整读取 `docs/UI_SPEC.md`，不得由实现者自行替换配色、布局、路由、状态映射或响应式行为。

只实现：

1. 知识库管理：加载演示资料、进度、上传、状态、预览、删除。
2. RAG 问答：流式回答、引用抽屉、原文定位、拒答。
3. 学业规划：学分、缺失必修、类别缺口、冲突警告。

演示加载交互严格遵循 `docs/DEMO_DATA_SPEC.md`：先读 status，POST 后轮询 job，刷新页面可恢复活动任务，完成后展示 imported/resumed/skipped/failed。所有页面具有 loading、empty、error、disabled 状态。

同时完成 UI Spec 中的设计 Token、桌面/平板/移动布局、引用证据面板、来源差异化删除提示、键盘可访问性以及 390×844、1024×768、1440×900 三档 Playwright 验收；不得使用虚构统计或占位业务数据。

验收：

```powershell
docker compose exec frontend pnpm test
docker compose exec frontend pnpm exec playwright test
docker compose exec frontend pnpm build
```

提交建议：`feat: add knowledge chat and academic planning views`

## 11. 阶段 9：RAG 评测与安全测试

使用 `demo/ground_truth.jsonl` 中不少于 50 条事实模型生成记录，覆盖单文档、跨文档、课程代码、日期/考试、学分、版本冲突、无答案和提示注入。

输出机器可读 JSON 和 Markdown 报告，包含 Recall@5、MRR、引用命中率、拒答正确率、学分正确率、P50/P95 延迟。

目标：

- Recall@5 ≥ 85%。
- 引用支持答案比例 ≥ 90%。
- 模拟事实模型生成的学分案例正确率 100%。
- 无依据问题明确拒答。

指标失败先按解析、切片、召回、融合、重排或生成分类，不得只改 Prompt 掩盖问题。

提交建议：`test: add rag evaluation and security dataset`

## 12. 阶段 10：一键启动与复现

输出 README、`run.ps1`、`stop.ps1`、健康检查、模拟语料生成/校验、异步初始化、可选上传、模型缓存和 CPU/API/GPU 模式说明。脚本接口固定为 `run.ps1 -Mode cpu|gpu` 和 `stop.ps1 -Mode cpu|gpu`，默认 `cpu`。

验收：

- `docker compose config`、前后端测试和构建通过。
- 默认 Compose 不申请 GPU；GPU Profile 在未验证时不会误启用。
- GPU 模式的 up/down/restart/logs/exec 全部使用 `docker compose -f docker-compose.yml -f docker-compose.gpu.yml --profile gpu <command>`，不会因后续命令遗漏 overlay 而回落到 CPU 配置。
- 数据在容器重启后保留；运行中的演示 job 可恢复。
- 缺少 API Key 时给出可理解错误而不是崩溃。
- 新环境不需要外部校园资料即可加载模拟语料。
- README 的启动流程在 10 分钟内可操作完成，首次镜像/模型下载时间单列。

提交建议：`docs: add ten-minute reproduction guide`

## 13. 完整验证命令

```powershell
function Wait-DemoJob([string]$JobId) {
    do {
        Start-Sleep -Seconds 2
        $result = Invoke-RestMethod ("http://localhost:8000/api/demo/jobs/{0}" -f $JobId)
    } while ($result.status -in @('queued', 'running'))
    return $result
}

docker compose config
docker compose up --build -d
docker compose ps
docker compose exec backend pytest
docker compose exec frontend pnpm test
docker compose exec frontend pnpm exec playwright test
docker compose exec frontend pnpm build
Invoke-RestMethod http://localhost:8000/api/health

$firstStart = Invoke-RestMethod -Method Post http://localhost:8000/api/demo/seed
$first = Wait-DemoJob $firstStart.job_id
if ($first.status -ne 'completed' -or $first.failed -ne 0) {
    throw "First demo seed did not complete successfully"
}

$dataset = Invoke-RestMethod http://localhost:8000/api/demo/status
if (-not $dataset.loaded -or $dataset.ready_documents -ne $dataset.available_documents) {
    throw "Demo dataset is not fully loaded"
}

$secondStart = Invoke-RestMethod -Method Post http://localhost:8000/api/demo/seed
$second = Wait-DemoJob $secondStart.job_id
if ($second.status -ne 'completed' -or $second.imported -ne 0 -or $second.resumed -ne 0 -or $second.failed -ne 0 -or $second.skipped -ne $second.total) {
    throw "Second demo seed was not a pure idempotent skip"
}

docker compose exec backend pytest tests/integration/test_demo_seed_idempotency.py -q
docker compose restart
Invoke-RestMethod http://localhost:8000/api/health
$afterRestart = Invoke-RestMethod http://localhost:8000/api/demo/status
if (-not $afterRestart.loaded) {
    throw "Loaded state was not preserved across restart"
}
```

`test_demo_seed_idempotency.py` 必须直接核对文档表、`document_pipeline_state`、Chroma 和 FTS5 的 ID/计数在第二次 seed 前后不变；恢复测试还要在任务运行中重启后端并确认同一 job 最终完成。

## 14. 质量与安全规则

- 后端测试默认不联网、不下载模型；外部调用通过 Fake Provider 注入。
- 解析、RAG 评测和端到端测试使用受版本管理的模拟数据集；损坏文件等低层边界使用最小 fixture。
- 每个 Bug 先增加复现测试再最小修复。
- Docker 与 Python/Node 依赖锁定，不使用未固定 `latest` 业务镜像。
- 健康检查区分进程存活和依赖/能力可用。
- 结构化日志记录 request/document/job ID，不记录完整原文和密钥。
- 上传内容中的宏、脚本、链接、命令和 Prompt 一律不执行。

## 15. 比赛留痕

每阶段保存用户 Prompt、Trae 方案、实际 Diff、测试输出、用户纠正和 Git 提交。至少形成三组完整 Prompt 链：

1. 模拟语料与多格式解析。
2. 混合 RAG、重排和原文引用。
3. 错误诊断、失败测试和学业规划修复。

推荐提交序列：

```text
feat: scaffold dockerized vue and fastapi application
feat: add reproducible demo corpus and async ingestion
feat: persist resumable document vectors in chroma
feat: add hybrid retrieval and complete demo ingestion
feat: rerank hybrid retrieval candidates
feat: stream grounded answers with citations
feat: calculate academic credit gaps
feat: add knowledge chat and academic planning views
test: add rag evaluation and security dataset
docs: add ten-minute reproduction guide
```

## 16. 最终完成定义

只有同时满足以下条件才能声称完成：

- Compose 启动健康前后端，默认 CPU 模式不要求 CUDA。
- GPU 覆盖仍使用同名 backend，CPU/GPU 后端不会同时启动或争用 8000 端口；CUDA 不可用时 GPU 模式明确失败且不静默降级。
- 固定环境和种子生成并校验 12–18 个纯虚构文件，manifest 与 ground truth 一致。
- 演示初始化立即返回异步 job，可轮询、重启恢复、并发去重并按指纹续跑。
- 重复加载不产生重复文档、向量或 FTS5 记录，也不覆盖用户上传。
- PDF、DOCX、XLSX 可解析、预览、切片和持久化，引用可打开定位。
- Chroma 与 FTS5 一致；无证据拒答；提示注入测试通过。
- 学分结果可重复且不依赖 LLM 心算。
- 三个页面可用并覆盖完整状态。
- 三个页面符合 UI Spec，在三档视口无整页横向溢出，引用、异步进度、规划证据和键盘操作通过验收。
- 测试、构建和 RAG 报告达标，README 可指导新环境复现。
- `.env`、模型、数据库、运行时上传和真实学生数据未进入 Git。
- Git 历史保持细粒度，并能提供至少三组包含 Prompt、方案、Diff、测试结果和用户纠正的完整迭代链。
