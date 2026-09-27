# Project Status

- 项目：校园多源文档 RAG 学业规划助手（启明大学模拟资料）
- 当前运行模式：**默认 CPU**（不申请 GPU / CUDA；`gpu` Profile 保持关闭）
- 当前阶段：**阶段 3 已完成（确定性语义切片与可恢复 Chroma 向量索引）**
- 下一阶段：**阶段 4 — FTS5 与混合检索**
- 最近更新：2026-09-27

## 阶段状态

| 阶段 | 名称 | 状态 |
|---|---|---|
| 0 | 环境预检 | completed |
| 1 | Docker 前后端骨架 | completed |
| 2 | 模拟语料、上传与解析 | completed |
| 3 | 语义切片与 Chroma | completed |
| 4 | FTS5 与混合检索 | not_started |
| 5 | Reranker | not_started |
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
- 阶段 1 未实现任何 RAG、解析、检索、学分能力；阶段 2A 固化模拟语料；阶段 2B 完成上传、解析与异步初始化；阶段 3 完成切片与向量索引，但 15 份资料与上传文档**仍未进入可检索知识库**（无 FTS、无混合检索、未激活数据集、`loaded=false`）。
- 语料生成/校验固定命令：在 `campus-rag-generator:1.0.0` 镜像内以 `--network none` 执行 `python scripts/generate_demo_corpus.py --output <dir> --seed 20260925`；固化到 `demo/` 必须显式追加 `--publish demo`。
- 阶段 4 须按 `docs/IMPLEMENTATION_PLAN.md` 与 `docs/DEMO_DATA_SPEC.md` 实现 FTS5、Dense/Keyword 召回与 RRF 融合，并把 `target_stage` 提升为 `completed`。
