# Project Status

- 项目：校园多源文档 RAG 学业规划助手（启明大学模拟资料）
- 当前运行模式：**默认 CPU**（不申请 GPU / CUDA；`gpu` Profile 保持关闭）
- 当前阶段：**阶段 5 已完成（Reranker）**
- 下一阶段：**阶段 6 — SSE 问答与引用**
- 最近更新：2026-09-27

## 阶段状态

| 阶段 | 名称 | 状态 |
|---|---|---|
| 0 | 环境预检 | completed |
| 1 | Docker 前后端骨架 | completed |
| 2 | 模拟语料、上传与解析 | completed |
| 3 | 语义切片与 Chroma | completed |
| 4 | FTS5 与混合检索 | completed |
| 5 | Reranker | completed |
| 6 | SSE 问答与引用 | not_started |
| 7 | 确定性学分规则引擎 | not_started |
| 8 | Vue 核心页面 | not_started |
| 9 | RAG 评测与安全测试 | not_started |
| 10 | 一键启动与复现 | not_started |

## 阶段 0 结论

- 环境闸门通过（Overall status: PASS），详见 `ENVIRONMENT_REPORT.md`。
- Docker daemon 可连接（ServerVersion 29.5.3）；Compose v5.1.4；context `desktop-linux`。
- 端口 5173、8000 可用；磁盘空间满足（各盘可用 > 200 GB）。
- Git 仓库已初始化（分支 `main`），本仓库 identity 已配置，文档基线提交已完成。
- 保留 WARNING（不阻塞）：宿主机 Node/pnpm/uv 缺失（统一在容器内执行）、可选 GPU 容器验证未执行。

## 阶段 1 结论

- 输出：Docker 化的 Vue 3 + FastAPI 骨架、`.env.example`、`.gitignore`、`docker-compose.yml`、`docker-compose.gpu.yml`。
- 后端：集中式 `Settings` 校验、CORS、`GET /api/health`；阶段 1 如实返回 `degraded` 与 `documents=unavailable`、`chat=unconfigured`、`planning=unavailable`，未加载或下载任何真实模型。
- 前端：`AppLayout`（左导航 / 顶栏 / 响应式 224px→72px→抽屉）、四条路由（`/`→`/knowledge`、`/chat`、`/planning`、404）、UI_SPEC 设计 Token、`health` store 读取真实健康接口、`PageHeader` 组件。
- 三个页面仅为空壳，明确标注后续阶段实现，不显示任何虚构文档数、进度、回答或学分。
- Compose 长期服务只有 `frontend` 与 `backend`；默认不申请 GPU，`EMBEDDING_DEVICE`/`RERANK_DEVICE` 固定 `cpu`，批次 4 / 2。
- `docker-compose.gpu.yml` 只覆盖同名 `backend`（`profiles: [gpu]`、`gpus: all`、`EMBEDDING_DEVICE=cuda`、`EMBEDDING_BATCH_SIZE=8`、`RERANK_DEVICE=cuda`、`RERANK_BATCH_SIZE=4`），未创建 `backend-gpu` 与 GPU 专用 env 文件；本轮只做静态校验，未启动 `gpu` Profile。
- 验收：`docker compose config`、GPU 合并 `config`、`up --build -d`、`ps`、`/api/health`、`/knowledge`、`backend pytest`（9 passed）、`frontend pnpm test`（11 passed）、`frontend pnpm build`、`logs` 全部通过。
- 浏览器实测：`/knowledge`、`/chat`、`/planning`、未知路由（404）均无横向溢出，控制台无错误。

## 阶段 2A 结论（可复现的虚构模拟语料）

- 输出：`scripts/generate_demo_corpus.py`、`scripts/demo_corpus/`（事实模型、生成组件、路径安全策略、校验与隐私扫描）、`scripts/generator/`（固定依赖与字体）、`scripts/tests/`（72 项测试）、`demo/corpus/`（15 个固化文件）、`demo/manifest.json`、`demo/ground_truth.jsonl`。
- 固定环境：一次性镜像 `campus-rag-generator:1.0.0`（`python:3.12.7-slim-bookworm@sha256:60d9996b…8b50d` + 完全锁定依赖），未加入 `docker-compose.yml` 长期服务；生成阶段使用 `--network none`。
- 固定字体：`scripts/generator/fonts/NotoSansSC-VF.ttf`（Noto Sans SC，SIL OFL 1.1），SHA-256 `a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da`；`.gitattributes` 显式声明为二进制（`-text -diff !eol`），有过滤器与无过滤器的 blob 计算一致。
- 语料：恰好 15 个文件（5 PDF / 5 DOCX / 5 XLSX），每份均标注“仅供系统演示的虚构资料”；`dataset_sha256 = d4a7a44a…d0a3e`，`manifest_sha256 = 8d150940…d4bc6`，`ground_truth.jsonl` 55 条。
- 确定性：相同镜像、相同种子下两次独立生成，文件集合、逐文件字节、SHA-256、manifest 与 ground truth 全部一致（byte diff = 0）。
- 刻意冲突 4 处（稳定 `conflict_id`）：`degree_plan_total_credits`、`degree_plan_required_courses`、`course_schedule_overlap_2026_2027_1`、`credit_recognition_cap`；ground truth 覆盖版本冲突、无答案拒答、提示注入、考试日期、规则计算与学业规划，且每条来源路径都有同路径 locator。
- 培养方案正文按各版本 `required_course_codes` 渲染：2025 版不含 QM-CS303（专业必修 7 门），2026 修订版含 QM-CS303（专业必修 8 门）；QM-CS401 软件工程统一为专业选修，不再与必修列表矛盾。
- 安全性：生成器不对任何用户目录做递归删除；`--output` 仅允许仓库 `.tmp/` 子目录且非空即安全失败，`--publish` 仅允许仓库 `demo/`，发布前后各校验一次；五个 PDF 的正文、metadata 与原始字节均不含 `reportlab.com` 或任何网络域名。
- 学业规划期望值由 `demo_corpus.facts.compute_planning_result` 确定性计算，未手工填写。
- 校验命令：`python -m pytest scripts`（72 passed，`--network none`）、`docker compose run --rm --no-deps backend python -m pytest`（9 passed）、`docker compose run --rm --no-deps frontend pnpm test`（11 passed）。
- 本轮未实现用户上传、解析管线、SQLite、worker、演示 API、向量与 FTS、RAG/LLM 和前端加载按钮；语料**尚未进入知识库**，`loaded` 保持 `false`，未修改健康接口能力值。

## 阶段 2B 结论（安全上传、解析与可恢复异步初始化）

- 输出：`backend/app/constants.py`、`backend/app/models.py`、`backend/app/db.py`、`backend/app/core/`（错误码与请求上下文）、`backend/app/documents/`（上传安全、容器校验、文件名策略、三种解析器、指纹、文档服务）、`backend/app/demo/`（只读 manifest 校验与服务）、`backend/app/worker/`（进程锁与单 worker）、`backend/app/api/`（`documents.py`、`demo.py`、`deps.py`）、`backend/tests/`（112 项测试，含 `tests/integration/`）。
- 新增固定依赖：`python-multipart==0.0.20`、`pymupdf==1.28.2`、`pymupdf4llm==0.0.17`、`python-docx==1.1.2`、`openpyxl==3.1.5`、`filelock==3.16.1`（全部固定版本，构建可复现；运行时解析不访问网络、不下载模型）。
- SQLite：启用 WAL、`busy_timeout`、`foreign_keys`、短事务；表 `documents`、`document_blocks`、`document_pipeline_state`、`demo_seed_jobs`、`demo_seed_job_documents`、`demo_active_datasets`；部分唯一索引 `uq_demo_seed_jobs_single_active` 保证全局最多一个 queued/running 演示任务（不依赖进程内锁）。
- 上传安全：单文件 multipart；扩展名 + 声明 MIME + 文件头/OOXML 容器 + `[Content_Types].xml` + 真实解析格式五重校验；流式落盘并同步计算 SHA-256 与大小；服务端生成存储名；拒绝空文件、损坏文件、扩展名/MIME 伪造、加密 PDF、宏（DOCM/XLSM/VBA）、OLE 嵌入、`externalLinks`、外部关系、ZIP 炸弹、超量条目、ZIP 路径穿越；显示名阻止路径分隔符、盘符、UNC、控制字符、保留设备名、BiDi 与分隔符伪装字符（并单独检查原始 `Content-Disposition`，防止解析器静默归一化盘符/UNC）。
- 解析：PDF 用 PyMuPDF4LLM（1-based 页码 + 标题），DOCX 用 python-docx（按 `body.iterchildren()` 保序、标题路径、表格行），XLSX 用 openpyxl（`data_only=False` 不执行公式、`sheet_name`、1-based `row_start`/`row_end`、表头与完整行、空表与合并单元格）；15 份固化语料全部解析为可追溯 `DocumentBlock`。
- 文档 API：`POST /api/documents`（202 created / 202 attached / 200 existing_ready / 202 retry_started / 409 DOCUMENT_RETRY_NOT_ALLOWED）、`GET /api/documents`、`GET /api/documents/{id}`、`GET .../status`、`GET .../preview`、`DELETE /api/documents/{id}`；响应与日志均不含 `storage_path` 或宿主机路径；统一错误体 `{code, message, details, request_id}`。
- Demo API：`GET /api/demo/status`、`POST /api/demo/seed`（202 + `Location` + `Retry-After`，客户端不能提交 `target_stage`，服务端固定 `parsed`，不在请求线程内解析）、`GET /api/demo/jobs/{job_id}`。
- Worker：FastAPI lifespan 启停单 worker；SQLite 持久队列 + `lease_owner`/`lease_generation`/`lease_expires_at` 条件更新；数据卷独占 `filelock`；启动时把遗留 running 任务恢复为 queued 并从逐文档检查点继续；单文档最多尝试 3 次、失败不影响其它文件；manifest SHA-256 与流水线指纹在 claim/恢复/处理前重新核对。
- 实测：首次 seed 15 份全部 `imported`（`validated → stored → parsed`），第二次 seed 15 份全部 `skipped`（`imported=0/resumed=0/failed=0`，不重复写 block）；并发 POST 复用同一活动任务（`reused_active_job=true`）；模拟崩溃后重启 `worker_recovered jobs=1` 并完成任务。
- 验收命令：`docker compose build backend`、`docker compose up -d`、`docker compose exec backend pytest`（112 passed）、`docker run --rm --network none ... python -m pytest scripts`（72 passed）、`docker compose exec frontend pnpm test`（11 passed）、`docker compose exec frontend pnpm build`、`docker compose config`、`docker compose ps`、`Invoke-RestMethod http://localhost:8000/api/health`、`docker compose logs --no-color --tail 200`。
- **边界**：本阶段 `target_stage` 仅为 `parsed`。15 份资料**仅达到 parsed**，`loaded` 始终为 `false`，`active_dataset_version` 为 `null`，`ready_documents=0`，`retrievable=false`；**未建立向量或 FTS 索引**，**未进入可检索知识库**，未写入 active demo dataset 指针。`/api/health` 继续 `degraded`，embedding/reranker `ready=false`，`chat=unconfigured`，`planning=unavailable`。演示 job 的 `completed` 只表示达到本任务 `target_stage=parsed`。

## 阶段 5 结论（Reranker）

- 输出：`backend/app/rerank/`（`base.py` 描述符与统一接口、`fake.py` / `local.py` / `api.py` 三个 Provider、`factory.py` 工厂）、`backend/app/runtime/coordinator.py`（进程级本地模型协调器）、`backend/app/search/reranking.py`（`RerankingRetriever` 重排层）、`app/search/types.py` 的 `RerankedResult` 与 `RerankDiagnostics`；新增测试 `test_rerank_provider.py`、`test_reranking_pipeline.py`、`test_model_coordinator.py`、`test_rerank_health.py`。
- **无新增第三方依赖**：Local Reranker 复用阶段 3 已固定的可选依赖清单 `backend/requirements-embedding-local.txt`（sentence-transformers / transformers / torch），默认镜像继续保持轻量。
- **Provider 契约**：输入只有 `query` 与按稳定顺序排列的候选文本，输出必须是与输入一一对应的有限浮点分数；Provider **不得**生成或重建 `chunk_id`、引用、locator 或业务元数据，因此重排不会让分数与引用错位。
- **FakeReranker**：完全离线；分数由 `(query, candidate_text)` 的 SHA-256 派生（**不使用 `hash()`**），同一输入在同进程、跨进程与任意 `PYTHONHASHSEED` 下完全一致，逐条与批量结果一致；不访问网络、不读取模型缓存、不导入 torch。`production` / `prod` 由工厂拒绝（`RERANK_PROVIDER_FORBIDDEN`）。
- **LocalReranker**：`BAAI/bge-reranker-v2-m3`，revision 固定为 Hugging Face 快照 **`953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`**（2024-06-24，Apache-2.0，已核对官方模型 API），绝不跟随可变 `main`；构造与健康检查都不导入 torch / 不加载模型；首次真实调用才延迟导入、延迟加载；`local_files_only=true`、`trust_remote_code=false`、`cache_dir` 显式使用 `MODEL_CACHE_PATH`（`/app/data/models`，**不使用宿主机默认 Hugging Face 缓存**）、固定 `max_length=1024`、`eval()` + `torch.no_grad()`、默认 `device=cpu` / `batch_size=2`（GPU Profile 覆盖为 `cuda` / `4`）。缺依赖、缺权重、CUDA 不可用或推理 OOM 一律返回安全的 `RERANK_PROVIDER_UNAVAILABLE`，**绝不静默回退 CPU、API 或 Fake**。
- **ApiReranker**：内部约定的 `POST {RERANK_BASE_URL}/rerank`，请求 `{model, query, documents, top_n}`、响应 `{results:[{index, relevance_score}]}`；Bearer 认证与显式 `RERANK_TIMEOUT_SECONDS`；`index` 必须唯一、完整、在范围内，服务端乱序时按 `index` 正确映射回输入；`relevance_score` 必须有限；只发送 query 与候选片段，错误与日志不泄漏 API Key、Base URL、请求/响应正文或候选全文。**`loaded` 恒为 `false`**：未做连通性探测前不得仅凭配置声称远端可用。基础测试仅使用 httpx mock，不访问真实 API。
- **重排管线**：`HybridRetriever` 保持纯粹的 Dense + Keyword + RRF（阶段 4 的「不执行 Reranker」测试原样保留、未改写）；新增独立 `RerankingRetriever`：RRF 顺序 → 取最多 **`RERANK_MAX_CANDIDATES=20`** 条候选 → 打分 → 稳定排序 → 输出前 **`RERANK_TOP_K=6`** 条。空候选不调用 Provider；候选少于 6 条时只返回实际数量。排序固定为「`rerank_score` 降序 → `fused_score` 降序 → `chunk_id` 升序」，不使用 `hash()`、随机数、UUID、时间或不稳定遍历顺序。
- **非法输出与降级边界**：分数数量不一致、NaN / Infinity、非数值、API index 重复/缺失/越界、非法 JSON、非法结构一律抛出 `RERANK_RESPONSE_INVALID`（内部不变量错误，**不降级吞掉**）；只有「Provider 明确不可用 / API 超时 / 协调器忙」才安全降级为原 RRF 顺序前 6 条，且必须 `rerank_applied=false`、`rerank_score=null`、记录白名单化的稳定 `degraded_reason`，**绝不把 `fused_score` 冒充 `rerank_score`**，health 也不会因此把 Reranker 标记为 ready。
- **指纹边界**：`RerankDescriptor`（provider / model / revision / implementation_version / score_kind / max_length）生成稳定的 `reranker_fingerprint` 并进入安全诊断，但**不进入** `documents/fingerprint.py` 的 `pipeline_fingerprint`、**不增加** `DocumentPipelineState` 检查点、不触发 SQLite / Chroma / FTS 重建、不改变 active dataset、不改变 demo `loaded` 状态、不触发重新 seed（已有回归测试证明「只改 Reranker 配置不会改变文档流水线指纹」与「重排不写任何索引」）。
- **显存/内存协调**：新增进程级共享 `LocalModelCoordinator`，worker Embedding、Dense 查询 Embedding 与 Local Reranker 三者共用同一有界互斥（默认 30s 有界等待，超时抛安全错误，**不允许无限等待**）。任何时刻最多只有一个本地大模型驻留；每个使用窗口在 `finally` 中释放，异常/超时/取消路径同样释放；应用关闭时 `reranker.close()` + `coordinator.close()` 幂等释放。`close/release` 不会为了清理而导入 torch：`empty_cuda_cache` 只在 torch **已加载且确实使用 CUDA** 时才清理。并发测试证明 Embedding 与 Reranker 的最大同时驻留数为 1。
- **诊断与健康**：`RerankDiagnostics` 在阶段 4 安全统计之上增加 `rerank_input_candidates`、`reranked_candidates`、`rerank_elapsed_ms`、`rerank_applied`、`reranker_provider`、`reranker_revision`、`reranker_fingerprint`、`rerank_score_kind`、`degraded_reason`、`rrf_version`；不包含 API Key、Base URL、query 原文、chunk 正文、绝对路径或完整外部响应。`/api/health` 不再固定 `reranker.ready=false`，改为读取 Provider **真实**的 `loaded`：Fake 为 true（离线确实可用），默认 Local 在未安装依赖/未放置权重时为 false，API 在未探测时为 false；健康检查本身不加载模型、不访问网络、不下载权重。整体仍为 `degraded`，`chat=unconfigured`、`planning=unavailable`，`documents` 仍只由可检索文档决定。
- **验收**：`docker compose exec backend pytest` → **301 passed**（阶段 4 为 243）；Reranker Provider 定向离线执行 `docker run --rm --network none campus-rag-backend:0.1.0 python -m pytest tests/test_rerank_provider.py -q` → **25 passed**；生成器 `--network none` → **72 passed**；前端 `pnpm test` → **11 passed**、`pnpm build` 成功；`docker compose config --quiet`、GPU 合并 `config`（`EMBEDDING_DEVICE=cuda`、`RERANK_DEVICE=cuda`、批次 8/4、`gpus`、`profiles`）、`ps`、`/api/health`、`logs` 全部符合预期。
- **资源保护**：默认 backend 镜像**未**新增 torch / sentence-transformers（镜像内 `find_spec` 均为 `None`），镜像内**没有**模型权重、`/app/data`、`/app/models`，也**没有**产生 Hugging Face 缓存（`/root/.cache` 不存在）；本轮**未下载任何模型权重、未运行真实模型、未启动 GPU Profile**。`./data/models` 仍为空。
- **边界**：阶段 5 只实现 Reranker 与查询时重排层，不含 Chat / SSE、LLM 调用、问题改写、答案生成、引用流、学分计算、前端业务页面与阶段 9 评测；未新增规范外的公共搜索或调试 API。**代码路径与 mock 已验证，但真实权重未下载、真实 Local 模型未运行**，因此默认 Local 环境的 `reranker.ready` 仍为 `false`。

## 阶段 5 修复（本地 Reranker 兼容性与外部模型隐私边界）

阶段 5 仍为 `completed`；本小节只记录对阶段 5 的独立加固修复，**未开始阶段 6**。

- 输出：`backend/app/core/privacy.py`（外部模型隐私清洗，新增）、`backend/app/rerank/{local,api,base}.py`、`backend/app/embedding/{api,base}.py`、`backend/app/search/reranking.py`、`backend/tests/test_stage5_fixes.py`、`backend/tests/test_rerank_provider.py`（严格签名 Stub）。
- **BUG-5-01（CrossEncoder 兼容性）**：锁定的 `sentence-transformers==3.3.1` 中 `CrossEncoder.__init__` 的真实参数名是 **`cache_dir`**（已核对官方 v3.3.1 源码），而实现此前传的是 `cache_folder`，会抛 `TypeError: got an unexpected keyword argument 'cache_folder'` 并导致本地重排 100% 加载失败。已改为 `cache_dir=MODEL_CACHE_PATH`，`revision` / `local_files_only=true` / `trust_remote_code=false` / `device` / `max_length` 全部保留。**不升级依赖来迁就错误参数**；测试改用**严格复刻 3.3.1 签名的 Stub（不使用 `**kwargs`）**，任何拼错的关键字都会直接失败而不是被吞掉。
- **BUG-5-02（外部模型隐私边界）**：此前 API Reranker 与 API Embedding 会把 query / 候选文本**原样**发送给外部服务。新增确定性的 `app/core/privacy.py`：只清洗**外发副本**，绝不修改 `RetrievedChunk` / `citation` / `quote` / SQLite / Chroma / FTS。
- **清洗规则与版本**：`PRIVACY_POLICY_VERSION = "external-privacy-v1"`；覆盖带「姓名 / 学生姓名 / 真实姓名」标签的姓名 → `[REDACTED_NAME]`、带「学号 / 学生编号 / 学籍号 / 学生证号 / 个人编号 / 人员编号」标签的个人编号 → `[REDACTED_STUDENT_ID]`、邮箱 → `[REDACTED_EMAIL]`、中国大陆手机号 → `[REDACTED_PHONE]`、15/18 位身份证格式 → `[REDACTED_ID]`。完全确定性、**幂等**、离线、不调用模型；课程代码（如 `QM-CS201`）等非个人信息不会被误清洗；日志不记录清洗前内容。
- **指纹边界**：`EmbeddingDescriptor` 新增可选 `privacy_policy_version`，**只有 API Embedding** 会带上该版本，因此切换清洗策略即改变 `embedding_fingerprint` 并按既有机制触发 API 向量重建；**Local / Fake 的指纹与 `pipeline_fingerprint` 完全不变**（字段为 `None` 时不进入 `as_dict()`）。Reranker 描述符同样记录清洗版本，但其指纹**仍不进入** `pipeline_fingerprint`，不触发任何文档索引重建（已有回归测试）。
- **Top 6 硬上限**：新增 `RERANK_TOP_K_MAX = 6` 与唯一决定点 `resolve_rerank_limit()`：RRF 输入仍是最多 20 条；显式 `top_k` 优先于配置；**任何情况下输出都不得超过 6 条**；`top_k=0` 或负数稳定返回 0 条（且不调用 Provider）；调用方仍可请求少于 6 条。
- **API 配置校验**：`ApiReranker` 与 `ApiEmbeddingProvider` 的 API 模式均校验 **Base URL + model + API Key**，缺失时返回固定的安全原因（`api_rerank_not_configured` / `api_embedding_not_configured`），不泄漏任何配置值。
- **API readiness 语义**：两者统一为**证据式** readiness —— 只有一次真实请求且响应结构校验成功后才 `loaded=true`；失败或 `close()` 后恢复 `false`；健康检查本身不联网、不加载模型，仅凭配置绝不谎报。
- **计时**：`rerank_elapsed_ms` 改为**只统计 Reranker 阶段**（从调用 Provider 前开始计时），不再把 Dense / FTS / RRF 耗时算进去；Hybrid 耗时仍保留在 `RetrievalDiagnostics.elapsed_ms`（有确定性可重复测试）。
- **验收**：`docker compose exec backend pytest` → **324 passed**（修复前 301）；`docker run --rm --network none … pytest tests/test_stage5_fixes.py tests/test_rerank_provider.py -q` → **48 passed**；生成器 `--network none` → **72 passed**；前端 `pnpm test` → **11 passed**、`pnpm build` 成功；`docker compose config --quiet`、GPU 合并 `config`（仅静态）、`ps`、`/api/health`、`logs` 全部符合预期。
- **资源保护**：默认 backend 镜像仍**没有** torch / sentence-transformers，镜像内无模型权重、无 `/app/data`、无 Hugging Face 缓存；`./data/models` 仍为空；未下载任何模型、未安装重型依赖、未启动 GPU Profile。
- **边界**：**真实 Local 模型仍未下载、未运行**，默认 Local 环境 `embedding.ready=false`、`reranker.ready=false`，整体仍为 `degraded`（`chat=unconfigured`、`planning=unavailable`）。阶段 6 仍未开始。

### 阶段 5 最终边界修复（BUG-5-03 / BUG-5-04）

阶段 5 仍为 `completed`；**未开始阶段 6**。

- 输出：`backend/app/core/privacy.py`（清洗策略 `external-privacy-v2`）、`backend/app/embedding/api.py`（空输入 no-op）、`backend/tests/test_stage5_fixes.py`（新增 14 项回归）。未新增依赖、未改数据库 schema、未改 Compose 结构。
- **BUG-5-03（隐私清洗覆盖不足）**：v1 只覆盖 `标签 + 冒号 + 无空格值`，因此 `姓名 张三`、`姓名：张 三`（只遮掉「张」残留「三」）、`| 姓名 | 张三 |`、`姓名<TAB>张三`、`学号 20260001`、`| 学号 | 20260001 |`、`电话 010-12345678`、`电话：(010) 12345678` 都会把个人信息原样发给外部模型。已在**同一个共享模块**中扩展（不在两个 Provider 里复制规则）：分隔符覆盖**冒号 / 等号 / 空白 / TAB / Markdown 与表格竖线**；标签值允许**内部空格**并清洗到稳定字段边界（遇到下一个 PII 标签、竖线、换行或中文句读即停）；新增**中国大陆固定电话**（`0xx`/`0xxx` 区号，含 `(010)` 括号形式）与带标签的电话规则。课程代码（`QM-CS201`）、课程名称、学分、学期、日期、普通数字**不被误清洗**。仍保持确定性、幂等、完全离线、无 NER、无第三方服务；只清洗外发副本，原 query / candidates / SQLite / Chroma / FTS / citation / quote 零改动。
- **版本与指纹影响**：`PRIVACY_POLICY_VERSION` 由 `external-privacy-v1` 提升为 **`external-privacy-v2`**（值语义变化必须提升版本）。API Embedding descriptor 带上该版本 ⇒ `embedding_fingerprint` 改变并**按既有机制触发 API 向量重建**；Local / Fake Embedding 仍为 `None` 且**不进入** `as_dict()` ⇒ 指纹与 `pipeline_fingerprint` 逐字节不变；Reranker 描述符记录该版本但**仍不进入** `pipeline_fingerprint`，**不触发任何文档索引重建**。
- **BUG-5-04（空 Embedding 输入伪造 readiness）**：`embed_documents([])` 此前会构造 HTTP Client（无请求）并返回 `[]`，随后把 `loaded` 从 `false` 置为 `true`，属于「没有真实请求却谎报 ready」。已改为**空输入直接 no-op**：返回 `[]`、不创建 Client、不发送请求，且**既不能伪造成功、也不清除既有成功证据**；只有非空输入经过真实请求并成功校验响应结构后才 `false → true`，失败或 `close()` 后仍恢复 `false`。ApiReranker 的空候选 no-op 行为保持不变（有回归测试）。
- **验收**：`docker compose exec backend pytest` → **338 passed**（修复前 324，+14）；`docker run --rm --network none … pytest tests/test_stage5_fixes.py tests/test_rerank_provider.py` → **62 passed**；生成器 `--network none` → **72 passed**；前端 `pnpm test` → **11 passed**、`pnpm build` 成功；`docker compose config --quiet`、`ps`、`/api/health`、`logs` 全部符合预期，日志隐私泄漏扫描无命中。
- **资源保护**：默认 backend 镜像仍**没有** torch / sentence-transformers，镜像内无模型权重、无 `/app/data`、无 Hugging Face 缓存；`./data/models` 仍为空；**未下载模型、未运行真实模型、未安装重型依赖、未访问真实外部 API**。
- **边界**：默认 Local 环境仍为 `degraded`，`embedding.ready=false`、`reranker.ready=false`、`chat=unconfigured`、`planning=unavailable`。阶段 6 仍未开始。

### 阶段 5 混合字段边界修复（BUG-5-05 / external-privacy-v3）

阶段 5 仍为 `completed`；**未开始阶段 6**。

- 输出：`backend/app/core/privacy.py`（清洗策略 `external-privacy-v3`）、`backend/tests/test_stage5_fixes.py`（新增 11 项回归，移除 1 项被取代的 v2 版本断言）。未新增依赖、未改数据库 schema、未改 Compose 结构、未改动两个 API Provider 的调用点（继续共用同一个 `privacy.py`）。
- **BUG-5-05（隐私清洗误删同一行学术字段）**：v2 的字段值只把「下一个 PII 标签 / 竖线 / 换行 / 句读」当作终止边界，因此 `姓名 张三 课程编号 QM-CS201 学分 3 学期 2026-2027-1` 会把**整行**当作姓名值吞掉（输出直接变成 `姓名：[REDACTED_NAME]`），学号行同理；`姓名：张 三 课程名称：数据结构 学分：3` 还会连带删掉「课程名称」并留下孤立冒号。
- **修复方案（稳定的字段边界策略）**：新增 `_FIELD_BOUNDARY_LABELS`，把常见的普通字段名识别为边界 —— 课程编号 / 课程代码 / 课程名称 / 课程类别 / 课程性质、学分 / 学期 / 成绩 / 绩点、专业 / 年级 / 班级 / 学院 / 培养层次、日期 / 时间 / 地点 / 教室 / 校区、状态 / 类型 / 备注 / 说明；并新增 `_GENERIC_KEY_BOUNDARY`，把任意「字段名 + 冒号或等号」也视为边界。这些字段**只用于截断 PII 值，自身永不被清洗**。
- **真实长度上限**：`_VALUE` 由「无界重复」改为**双重硬上限**（单 token ≤ `_VALUE_TOKEN_MAX_CHARS`=48 字符，token 数 ≤ 1+`_VALUE_EXTRA_TOKENS`=5），并导出 `MAX_FIELD_VALUE_CHARS`=240 供测试校验，杜绝「名为有界、实为无界」。PII 值在下一个字段开始前停止，**不吞掉字段间空白**与后续字段。
- **版本与指纹影响**：`PRIVACY_POLICY_VERSION` 由 `external-privacy-v2` 提升为 **`external-privacy-v3`**（外发文本语义再次变化）。API Embedding descriptor 携带该版本 ⇒ `embedding_fingerprint` 变化并**按既有机制触发 API 向量重建**；Local / Fake Embedding 仍为 `None` 且不进入 `as_dict()` ⇒ 指纹与 `pipeline_fingerprint` 逐字节不变；Reranker 记录该版本但**仍不进入** `pipeline_fingerprint`，**不触发任何文档索引重建**。
- **兼容性**：上一轮 8 种键值格式、TAB / 表格竖线、带内部空格的姓名、固定电话、幂等性、确定性、离线与「只清洗外发副本」全部继续成立；`3d3710b` 的空 Embedding readiness 修复未被破坏（空输入仍为 no-op）。
- **验收**：`docker compose exec backend pytest` → **348 passed**（修复前 338）；生成器 `--network none` → **72 passed**；前端 `pnpm test` → **11 passed**、`pnpm build` 成功；`docker compose config --quiet`、`ps`、`/api/health`、日志隐私泄漏扫描全部符合预期。
- **资源保护**：默认 backend 镜像仍无 torch / sentence-transformers，镜像内无模型权重、无 `/app/data`、无 Hugging Face 缓存；`./data/models` 仍为空；未下载模型、未运行真实模型、未安装新依赖、未访问真实外部 API。
- **边界**：默认 Local 环境仍为 `degraded`（`embedding.ready=false`、`reranker.ready=false`、`chat=unconfigured`、`planning=unavailable`）。阶段 6 仍未开始。

## 阶段 4 结论（FTS5、混合检索与演示数据集原子激活）

- 输出：`backend/app/search/`（`schema.py` FTS5 结构、`text.py` 确定性中文规范化与安全 MATCH 构造、`fts.py` 索引写入与精确对账、`eligibility.py` 可检索资格、`hydrate.py` 候选补全、`keyword.py` / `dense.py` / `hybrid.py` 三路检索）、`backend/app/demo/activation.py`（原子激活与退役）、`backend/app/api/retrieval.py`（`/api/sources/{chunk_id}`、`/api/retrieval/options`）、`backend/app/documents/categories.py`（类别中文标签）；`document_chunks.fts_rowid`、`demo_active_dataset.active_marker`；新增测试 `test_fts_index.py`、`test_keyword_retriever.py`、`test_retrieval_hybrid.py`、`test_reconciliation.py`、`test_activation.py`、`test_sources_api.py`。
- 无新增第三方依赖：FTS5 为 SQLite 原生能力（sqlite 3.40.1），未引入外部搜索服务。
- **FTS5**：虚拟表 `chunk_fts`，`tokenize = 'unicode61 remove_diacritics 2'`；`chunk_id`/`doc_id`/`fts_fingerprint` 为 `UNINDEXED`（身份字段不被分词）；索引 `title`、`body`（原始正文）、`course_code`、`file_name`、`doc_category`、`source_type`、`source_key`、`dataset_version`、`document_version`、`effective_from`、`major`、`grade_year`、`semester`。
- **中文检索策略**：`unicode61` 把整段 CJK 当作**一个 token**（「学分认定」不会被切成「学分」「认定」），因此新增**确定性 CJK 二元组辅助列** `body_ngram`（`学分认定` → `学分 分认 认定`），查询侧使用同一套规范化逻辑（`FTS_NORMALIZATION_VERSION=1.0.0`、`FTS_NGRAM_VERSION=cjk-bigram-v1`，均进入 `fts_schema_fingerprint`）；**原始 chunk 正文不被修改**，不依赖网络或第三方在线服务。短中文词用 AND（精确）、长中文短语用 OR（召回），关键词命中即 OR；两类规则均为确定性常量。
- **MATCH 构造**：所有 token 一律引号包裹（含单 token 查询词），空查询安全返回空、超长查询拒绝（`RETRIEVAL_QUERY_INVALID`）；引号/括号/减号/星号/冒号/`AND`/`OR`/`NOT`/`NEAR` 等 FTS 语法字符只能作为普通文本，SQL 注入与语法注入均不可能。实测未加引号的 `QM-CS201` 会触发 `no such column: CS201`，因此该规则是必需的。
- **KeywordRetriever**（`keyword_top_k=12`）：SQLite FTS5 + BM25，`ORDER BY bm25 ASC, chunk_id ASC`，返回 `chunk_id`、`keyword_score(=-bm25)`、正文与完整引用元数据。
- **DenseRetriever**（`dense_top_k=12`）：`EmbeddingProvider` + Chroma，查询向量维度必须与 Collection 一致（不一致抛 `EMBEDDING_DIMENSION_MISMATCH`）；候选先过采样再交 SQLite 复核，`dense_score = 1/(1+distance)`。
- **HybridRetriever 与 RRF**：`score = Σ 1/(rrf_k + rank)`，`RRF_K=60`、`RRF_VERSION=rrf-v1`（版本化常量并进入诊断）；同一 `chunk_id` 只出现一次；排序固定为「fused_score 降序 → 最佳单路 rank → chunk_id 升序」。本阶段**不执行 Reranker**，输出即阶段 5 的候选输入。诊断只含安全统计（各路候选数、过滤器、耗时、provider/schema 版本、rrf_k、match_mode），不含密钥、绝对路径或整份正文。
- **统一可检索资格**（Dense 与 Keyword 共用同一 SQL）：upload = `completed` + `ready` + `retrievable` + 未删除 + `activation_state IS NULL`；demo 需在此基础上 `activation_state='active'` 且 `dataset_version` 等于唯一 active 指针，且该指针的 `pipeline_fingerprint` 必须等于**当前运行配置**的流水线指纹——否则整套 demo 索引视为另一条流水线，必须重建而不可复用（切换 Provider 后 Fake 索引自动不可检索）。过滤字段固定为 `major` / `grade_year` / `semester` / `doc_category`，未知字段拒绝（`RETRIEVAL_FILTER_INVALID`）。
- **三方对账与 completed**：`target_stage` 提升为 `completed`，路径为 `parsed → chunked → vector_indexed → keyword_indexed → 三方对账 → completed → demo 整体原子激活`。写 `completed` 前必须 **ID 集合**（SQLite chunk = Chroma 向量 = FTS 行）与三个计数（`expected_chunk_count` / `vector_record_count` / `fts_record_count`）全部一致；**仅计数相同但 ID 不同会判定失败**（`DOCUMENT_INDEX_FAILED`，`reason=fts_set_mismatch`）。`keyword_indexed` 的规范化写入、多余记录精确删除、ID 对账、计数与检查点在**同一个 SQLite 事务**内提交，不会出现「检查点成功但 FTS 记录不完整」。
- **差异精确修复**：缺少 FTS 记录只补缺失、多余/孤儿 FTS 行只删多余（部分唯一索引 `uq_document_chunks_fts_rowid` 兜底）、`fts_schema_fingerprint` 变化只重建受影响记录且**不重新调用 Embedding**、Chroma 正确向量继续复用；其它文档记录不受影响；不做整库清空。
- **数据集原子激活与退役**：`demo_active_dataset` 用 `active_marker` + 部分唯一索引保证**任何时刻只有一个 active 指针**；只有当前 manifest 的 15 个文档全部 `completed`、三方对账一致、无失败文档时才在**单个事务**内切换：写入/更新唯一指针，新版本 `status=ready` / `activation_state=active` / `retrievable=true`，旧版本立即 `inactive` / 不可检索。任何前置条件不满足都直接返回，旧指针不变、旧版本继续提供检索、新版本保持 `candidate`。
- **upload 独立 ready**：upload 文档不依赖 demo 整体激活，单文档达到 `completed` 并对账后即 `ready` / `retrievable=true` / `activation_state=null`。
- **公共 API**：`GET /api/sources/{chunk_id}` 只返回当前可检索 chunk，对 candidate / inactive / deleted / 未知 / 非法 ID 返回**完全一致**的 `SOURCE_NOT_FOUND`（不泄漏存在与否、路径或内部状态）；`GET /api/retrieval/options` 只从当前 ready 且 retrievable 的文档聚合，去重、稳定排序、空值不返回、无数据时为空数组，`doc_categories` 返回稳定 `value` 与中文 `label`。本阶段不新增规范外的公共调试搜索接口。
- **幂等初始化增量迁移**：`init_database` 在 `create_all` 之后对**可加列**做幂等 `ALTER TABLE ADD COLUMN`（`demo_active_dataset.active_marker`、`document_chunks.fts_rowid`）并创建索引，使沿用旧版本数据卷的实例能够平滑升级而非启动崩溃。
- **验收**：`docker compose exec backend pytest` → **243 passed**；生成器 `--network none` → **72 passed**；前端 `pnpm test` → **11 passed**、`pnpm build` 成功；`docker compose config --quiet`、`ps`、`/api/health`、`/api/demo/status` 全部符合预期。
- **隔离 Fake 验收（临时 SQLite/Chroma/uploads + `APP_ENV=test` + `EMBEDDING_PROVIDER=fake` + `--network none`）**：首次 seed `completed`、`imported=15`；15 份文档全部 `completed`，**91 个稳定 chunk = 91 条 Chroma 向量 = 91 行 FTS**，三方集合与计数完全一致；`loaded=true`、`ready_documents=15`、`active_dataset_version=2026.1`、`serving_previous_version=false`；`/api/health` 的 `documents` 能力为 `ready`（整体仍 `degraded`，`chat=unconfigured`、`planning=unavailable`）；第二次 seed `imported=resumed=failed=0`、`skipped=15`，切片与 FTS 记录数仍为 91/91，无任何重复写入。
- **默认 Local 轻量环境（项目默认数据卷）**：`EMBEDDING_PROVIDER=local` 且未安装 torch / sentence-transformers，**未下载任何模型**；`loaded=false`、`ready_documents=0`、`active_dataset_version=null`、`serving_previous_version=false`、`retrieval/options` 全为空数组、`/api/health` 的 `documents` 能力仍为 `unavailable`。阶段 3 遗留的 91 条 Fake 向量被正确识别为**指纹不匹配、不可检索的候选数据**（`documents` 中 `ready=0`、`retrievable=0`），**未被激活、未被当作 Local 向量使用、也未被删除**；切换到 Local/API Provider 后必须按新指纹重建向量与 FTS，不能复用 Fake 索引（已有回归测试覆盖）。
- **边界**：阶段 4 不含 Reranker、Chat / SSE、LLM 调用、学分规划与前端业务页面；所有测试使用 Fake Provider，不访问网络、GPU、外部 API 或真实模型。

## 阶段 3 结论（确定性语义切片与可恢复 Chroma 向量索引）

- 输出：`backend/app/embedding/`（Provider 抽象与 Fake/Local/API 实现、描述符、工厂）、`backend/app/documents/chunking/`（确定性 chunker 与稳定 chunk_id）、`backend/app/documents/metadata.py`（可选标量元数据派生）、`backend/app/vector/`（Chroma 封装 + 显式关闭遥测）、`document_chunks` 表、worker 的 `chunked` / `vector_indexed` 检查点、`backend/tests/test_chunking.py`、`test_embedding_provider.py`、`test_vector_store.py`、`test_vector_pipeline.py`、`test_vector_ownership.py`。
- 新增固定依赖：`chromadb==0.5.23`（Apache-2.0）、`numpy==2.1.3`（BSD-3-Clause）、`httpx==0.28.1`（BSD-3-Clause，OpenAI 兼容 Embedding 客户端）。
- 本地 BGE-M3 运行时依赖**不进默认镜像**，单独固定在 `backend/requirements-embedding-local.txt`（torch BSD-3-Clause / sentence-transformers Apache-2.0 / transformers、huggingface-hub Apache-2.0），需显式安装并提供权重后才可用。
- 切片：`DocumentChunker` 版本 `1.0.0`，目标 550 字 / 重叠 100 字，Unicode NFC + 固定空白折叠；优先按标题、段落、表格行、工作表、中文句读切分，最后才按安全字符边界切分；表格行原子不可拆且自动补表头；定位组 `(page_number, sheet_name, section_title)` 变化即断章，课程代码/学分/日期不会被切断。
- `chunk_id = SHA256(document_checksum + canonical_locator + chunk_index + parser_version + chunker_version)`，其中 `document_checksum = SHA256(source_type + source_key + 文件 SHA-256)`。**说明**：仅使用文件 SHA-256 会让「同一文件同时存在于 demo 与 upload」产生相同 chunk_id，与「demo / upload 即使 SHA 相同也必须完全独立」冲突并在 SQLite 主键上真实碰撞；绑定来源键后保持公式形状、确定性，并保证跨来源互不复用。公式不含时间、UUID、绝对路径或数据库自增 ID。
- Embedding：`EmbeddingProvider` 统一接口（`embed_documents` / `dimension` / `provider_name` / `model_name` / `revision` / `fingerprint` / `close|release`）。Fake 为 SHA-256 派生确定性单位向量（1024 维，离线）；Local 延迟导入 + 延迟加载 `BAAI/bge-m3`，revision 固定为 `5617a9f61b028005a4858fdac845db406aefb181`，默认 `local_files_only=true`（不隐式下载权重），`cuda` 仅由 GPU 覆盖启用且不可用时明确失败；API 走 OpenAI 兼容接口，错误与指纹均不含密钥。`APP_ENV=production` 拒绝 Fake。
- Chroma：`PersistentClient`（`CHROMA_PATH`）、Collection 固定 `campus_chunks_v1`、显式关闭遥测（`chroma_product_telemetry_impl=app.vector.telemetry.NoopTelemetry`，实测不再尝试上报）、始终显式传入向量（占位 Embedding 函数禁止隐式调用）、metadata 仅标量且无 null；启动/首次使用时校验 collection 名称、schema 版本、维度与 provider/model/revision，不一致安全失败。
- SQLite：新增 `document_chunks`（`id` = 稳定 chunk_id、`chunk_index` 唯一、`locator`/`citation` JSON、parser/chunker 版本与指纹）；`document_pipeline_state` 的分阶段指纹只在对应阶段**实际完成时**写入（此前若提前刷新会让定向重建静默失效）。
- worker：路径变为 `parsed → chunked → embedding（非检查点） → Chroma upsert → 对账 → vector_indexed`；服务端 `target_stage` 提升为 `vector_indexed`。跳过判定 = 分阶段指纹一致 **且** 切片集合/向量集合对账一致；对账不通过时把阶段下调到对应的重建起点并真正收敛（不会“报成功但索引缺失”）。
- 崩溃恢复：Chroma upsert 后、检查点前崩溃时，重启复用 `embedding_fingerprint` / `vector_schema_fingerprint` 正确的既有向量，不再调用 Embedding；只补缺失、只删多余、只重建版本不符的记录。
- 指纹定向重建：解析器/规范化变化 → 回退 `stored`（重新解析）；切片配置或 chunker 版本变化 → 回退 `parsed`（保留块，重新切片）；Embedding 身份或向量 schema 变化 → 回退 `chunked`（保留 chunk，仅重建向量）。
- 删除：`DELETE /api/documents/{id}` 同步清理该 `doc_id` 的 SQLite 切片与 Chroma 向量；upload 额外删除运行时可执行文件，demo 绝不修改只读 `/app/demo`；不提供整库清空接口。
- 实测（Chroma / demo 验收按约定使用 FakeEmbeddingProvider，容器内 `--network none`）：首次 seed `completed`、`imported=15`；15 份文档 `last_completed_stage=vector_indexed`，91 个 chunk = 91 条向量，0 个文档对账不一致；第二次 seed `skipped=15`、`imported=resumed=failed=0`，无重复记录。
- 验收命令：`docker compose build backend`、`docker compose up -d`、`docker compose exec backend pytest`（170 passed）、`docker run --rm --network none … python -m pytest scripts`（72 passed）、`docker compose exec frontend pnpm test`（11 passed）、`docker compose exec frontend pnpm build`、`docker compose config --quiet`、`docker compose ps`、`Invoke-RestMethod /api/health`、`docker compose logs --no-color --tail 200`。
- **边界**：15 份资料**仅达到 `vector_indexed`**；`loaded=false`、`ready_documents=0`、`active_dataset_version=null`、`retrievable=false`；**未建立 FTS5**、**未实现混合检索**、**未写入 active dataset 指针**、**未把文档标记为 ready**；公共文档状态继续安全投影为 `queued`。`/api/health` 继续 `degraded`（`documents=unavailable`、`chat=unconfigured`、`planning=unavailable`），`embedding.ready` 与 `reranker.ready` 均为 `false`（本地权重未加载时不谎报）。默认 Compose 仍为 `EMBEDDING_PROVIDER=local`；在未安装本地可选依赖前，容器内演示导入会在 embedding 阶段以 `EMBEDDING_PROVIDER_UNAVAILABLE` 安全失败，不会静默回退。

## 备注

- 本文件仅用于阶段状态跟踪；业务实现、构建与测试均在 Docker 容器内执行。
- 阶段 1 未实现任何 RAG、解析、检索、学分能力；阶段 2A 固化模拟语料；阶段 2B 完成上传、解析与异步初始化；阶段 3 完成切片与向量索引；阶段 4 完成 FTS5、Dense/Keyword/RRF 混合检索、`completed` 检查点、三方对账与 demo 数据集原子激活；阶段 5 完成 Reranker（fake / local / api 三条 Provider 路径）与查询时重排层。至此 15 份资料在**隔离 Fake 环境**中已进入可检索知识库（`loaded=true`），并可在其上执行 RRF → 重排 → 前 6 条引用结果。
- **默认 Local 环境仍未加载任何模型、未下载权重、未激活数据集**：`loaded=false`、`retrievable` 计数为 0、`embedding.ready=false`、`reranker.ready=false`。切换 Provider 必须按新 `pipeline_fingerprint` 重建向量与 FTS，**不得复用 Fake 索引**；Reranker 配置变化**不**影响该指纹。
- 语料生成/校验固定命令：在 `campus-rag-generator:1.0.0` 镜像内以 `--network none` 执行 `python scripts/generate_demo_corpus.py --output <dir> --seed 20260925`；固化到 `demo/` 必须显式追加 `--publish demo`。
- 阶段 6 须按 `docs/IMPLEMENTATION_PLAN.md` 与 `docs/PRODUCT_SPEC.md` 实现 SSE 问答与引用（本阶段未开始），其输入即本阶段重排后的前 4–6 条证据。
