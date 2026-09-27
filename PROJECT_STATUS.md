# Project Status

- 项目：校园多源文档 RAG 学业规划助手（启明大学模拟资料）
- 当前运行模式：**默认 CPU**（不申请 GPU / CUDA；`gpu` Profile 保持关闭）
- 当前阶段：**阶段 7 进行中（7A 与 7B-1 已完成，下一步 7B-2）**
- 下一阶段：**阶段 7B-2 — records/rules 导入接口与 GET /api/academic/options**，随后 7C — 规划 API 与全量验收
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
| 6 | SSE 问答与引用 | completed |
| 7 | 确定性学分规则引擎 | in_progress |
| 8 | Vue 核心页面 | not_started |
| 9 | RAG 评测与安全测试 | not_started |
| 10 | 一键启动与复现 | not_started |

## 阶段 7A 结论（确定性学分规则引擎 · 持久化与纯计算）

阶段 7 为 `in_progress`：本轮只完成 7A（持久化模型 + 增量建表 + Decimal 确定性纯计算引擎 + 核心单测）。**7B / 7C 只设计接口，尚未实现**；阶段 8 保持 `not_started`。**学分完全由确定性规则计算，LLM 未参与任何数字计算。**

### 持久化模型与迁移方式

- 新增规范化 SQLite 表（全部挂在同一个 `Base` 上）：`academic_record_sets`、`course_records`、`academic_rule_sets`、`degree_rules`、`degree_rule_courses`、`academic_projections`。
- **迁移方式**：沿用既有 `app.db.init_database`（`Base.metadata.create_all` + 幂等 `ALTER TABLE ADD COLUMN`）**增量创建新表**；不引入 Alembic，不删除、不重建任何既有表，既有数据保持不变（有回归测试）。
- `credits` / `minimum_credits` 一律以**一位小数字符串**落库：SQLite 的 NUMERIC 走浮点会引入漂移，字符串往返后由 `Decimal` 精确解析，保证重启后计算结果逐字节一致。
- 追溯关系：`source_doc_id` 外键指向真实 `documents.id`（有 FK 约束测试），`source_chunk_id` 指向真实 `document_chunks.id`；无法建立真实来源时不得伪造 ID。
- 幂等：`academic_record_sets` 唯一键 `(source_type, source_key, content_hash)`；`academic_rule_sets` 唯一键 `(source_type, source_key, major, rule_version, content_hash)`；`degree_rules` 唯一键 `(rule_set_id, category)`；`degree_rule_courses` 唯一键 `(rule_set_id, course_code)`；`academic_projections` 唯一键 `(source_type, source_key, dataset_version)`。demo 与 upload 通过 `source_type` / `source_key` 保持独立所有权。
- 级联：删除规则集合会级联删除其类别与课程目录行（有测试）；`course_records` 随 `academic_record_sets` 级联删除。

### 确定性计算引擎（`app/academic/engine.py`）

纯函数 `compute_plan(records, rule, ...)`，只接收已规范化的 `CourseRecord` 与 `DegreeRuleSet`，**不访问数据库、文件、网络、LLM、Embedding 或 Reranker**（有源码级依赖守卫测试）。

- 内部全程 `Decimal`，对外统一量化到一位小数；拒绝 NaN / Infinity / 负学分 / 未知状态 / 空课程代码。
- `passed` 计入 `completed_credits`；`in_progress` 计入 `in_progress_credits`；`failed` 不计分。
- 同一 `course_code` 的正考 / 补考 / 重修**只计一次**；重复通过、重复在修同样只计一次。
- `passed` 与 `in_progress` 互斥，`passed` 优先；`COURSE_RECORD_CONTRADICTION` 覆盖三种矛盾组合（`failed`+`passed`、`failed`+`in_progress`、`passed`+`in_progress`），同一批矛盾只产生**一个**稳定 warning 并按课程代码排序。
- 学分与类别**优先取用户显式选择的 rule set 课程目录**，不混入其它版本；记录类别与规则不一致时按规则计分（不双重计分）并产生 `COURSE_CATEGORY_MISMATCH` warning。
- `remaining_credits = max(required - completed - in_progress, 0)`；每个 `CategoryGap.remaining_credits` 同样钳制在 0，**任何路径都不产生负数**。
- 必修课程：`passed` 或 `in_progress` 即视为已覆盖、不列入 missing；`failed` 仍列入；总学分缺口为 0 时仍保留未满足的 missing 列表。
- 未知必修课程不做猜测：以课程代码占位、学分 0，并产生 `REQUIRED_COURSE_UNKNOWN` warning。
- 冲突 warning（稳定顺序）：`DEGREE_PLAN_VERSION_CONFLICT`、`COURSE_TIME_CONFLICT`、`COURSE_CATEGORY_MISMATCH`、`COURSE_RECORD_CONTRADICTION`、`COURSE_NOT_IN_RULE_CATALOG`、`REQUIRED_COURSE_UNKNOWN`；`severity` 只取 `warning` / `blocking`。
- `PlanningEvidence` 严格 11 个白名单字段，按 `chunk_id` 去重后以 `(doc_id, chunk_id)` 稳定排序，不含路径、分数、`source_key` 或内部诊断。
- `planning_result_payload()` 输出顺序固定、数字一位小数，相同输入产生逐字节一致的结果。

### 数据结构（严格对齐 PRODUCT_SPEC 5.2）

`CourseRecord`、`DegreeRule`、`PlanningResult`、`MissingRequiredCourse`、`CategoryGap`、`ConflictWarning`、`PlanningEvidence` 字段与 `PRODUCT_SPEC.md` 5.2 逐一核对，**未增删任何字段**；`PlanningResult` 严格为 8 项，`planning_result_payload()` 顶层 key 亦严格为这 8 项（有回归测试断言字段名与 key 列表逐一相等）。所选培养方案的展示信息（专业 / 规则版本）**不属于** `PlanningResult`，后续由 options 数据或外层响应元数据承担。

### 投影职责边界（7A 只定义接口与纯函数）

- 业务字段**只从正式解析后的 `DocumentBlock`** 提取；`SourceBlock.block_type` 必须属于表格类，**传入 chunk 文本会直接报错**，杜绝「从带重叠的 chunk 重复生成课程记录导致重复计分」。
- `DocumentChunk` 仅用于建立 `source_chunk_id` 与 `PlanningEvidence` 定位。
- 提供 `project_course_records()`（表头别名 + 状态别名 + 空行跳过 + 非法取值报错）、`build_rule_set()`、`record_set_fingerprint()`、`rule_set_fingerprint()` 等纯函数。
- 真实演示资料的投影落库安排在 7B；`POST /api/academic/plan`、真实证据映射、冲突证据与 health planning 状态安排在 7C。

### 本轮验收

- 新增 `tests/test_academic_engine.py`（确定性引擎与投影纯函数）与 `tests/test_academic_models.py`（持久化与增量建表），共 **+49** 项用例：计分规则、重修 / 在修 / 重复记录、类别优先级、missing、缺口钳制、Decimal 精度、确定性、非法输入、投影纯函数、隔离性守卫，以及新表创建、增量迁移、唯一约束、级联、外键、精确小数往返与重启恢复。
- `docker compose exec backend pytest` → **519 passed**（阶段 6 为 470）；生成器 `--network none` → 72 passed；前端 `pnpm test` → 11 passed、`pnpm build` 成功；`docker compose config --quiet`、`ps`、`/api/health`（`degraded`、`planning=unavailable`）均符合预期。
- 在**真实运行数据卷**上执行 `init_database` 验证：6 张新表已增量创建，`documents` / `document_chunks` / `demo_active_dataset` / `document_pipeline_state` 等既有表全部保留。
- 提交内容扫描：8 个文件，无 `.env` / 数据库 / uploads / 日志 / 模型 / 缓存 / `__pycache__` / `dist`。
- 未调用任何真实 API、未下载或加载模型、未启动 GPU Profile；默认镜像仍无 torch / sentence-transformers。

### 阶段 7B-1 结论（active demo 学业资料确定性投影）

阶段 7 仍为 `in_progress`。本轮只完成 **7B-1**：active demo 学业资料的确定性投影落库。
**尚未实现** `POST /api/academic/records/import`、`POST /api/academic/rules/import`、
`GET /api/academic/options`、`POST /api/academic/plan`；`/api/health` 的 `planning`
仍为 `unavailable`（未改动）。

**投影来源与职责边界**

- 业务字段（专业、招生年份、规则版本、生效日期、毕业总学分、各类别最低学分、必修课程代码、课程目录、成绩记录）**只**从正式解析后的 `DocumentBlock` 提取；`DocumentChunk` **只**用于建立真实 `source_chunk_id`。
- 真实块形态：成绩 XLSX 为 `table_header` + `table_row`，行文本形如 `序号: 1；课程代码: QM-CS101；…；状态: 通过`（「汇总」表无 `课程代码` 标签，被确定性排除，不会重复计分）；培养方案 PDF 无 table 块，规则来自 `heading`/`paragraph` 中的 `专业名称：…`、`招生年份：2025`、`文档版本：2025.1 生效日期：2025-09-01`、`毕业总学分：155.0 学分。`、`· 专业必修：58.0 学分` 以及课程目录行 `QM-CS102 高等数学（一） 5.0 公共必修 第1学期 无`。
- 有回归测试证明：把全部 `document_chunks.text` 破坏后重新投影，业务字段逐项不变。

**投影结果（实测）**

- 2 个 record set：`匿名学生A · 课程记录`、`匿名学生B · 课程记录`；2 个 rule set：`2025.1`、`2026.1`（两个版本同时保留，**不静默选择最新**）。
- 匿名学生A：已修 20.5 学分、在修 9.0 学分（`QM-CS102` 正考不及格 + 重修通过只计一次）；培养方案 2025.1 毕业总学分 155.0、类别最低学分 公共必修 52.0 / 专业必修 58.0 / 专业选修 20.0 / 通识选修 15.0 / 实践环节 10.0，必修代码按代码排序、类别来自课程目录。
- 全部 `source_doc_id` / `source_chunk_id` 都是真实引用，且切片与文档同属一份文档。

**原子性与幂等**

- 先在内存完成并校验 4 份投影，全部通过后才在单事务内写入 6 张表；任一文档失败即整体回滚（有模拟失败回归测试证明零写入）。
- `source_chunk_id` 映射使用固定排序：完全匹配 locator → 覆盖行范围最窄 → `chunk_index` 升序 → `chunk_id` 升序；无合法候选即明确失败，绝不伪造 ID；跨文档引用被拒绝。
- 投影指纹覆盖「投影算法版本 + 来源文档 SHA-256 + 稳定顺序的 block 类型/文本/locator + 规范化投影内容」：指纹相同直接跳过（重复投影零新增行），指纹变化则原子替换旧投影，不继续提供陈旧规则。
- 触发点在 demo 任务完成且激活无错时执行**幂等对账**：即使本次 seed 全部 `skipped` 也会运行，可补建升级前已 active 数据集的缺失投影；重复 seed 后各表行数不变。

**隔离与隐私**

- 只投影唯一 active demo dataset 中 `ready` 且可检索的文档；`source_type`/`source_key` 保持 demo 与 upload 独立所有权，upload 集合不因 demo 切换被删除或失效。
- `source_key` 是模型内部的来源所有权字段，允许并必须落库；显示名只用安全文件名/真实提取字段推导，不含宿主绝对路径、uploads 存储路径、学号或姓名。

**外键删除语义与旧库迁移（回归修复 + BUG-7B-04）**

- 学业表的 `source_doc_id → documents.id` 使用 `ON DELETE CASCADE`（投影随来源文档消亡）；`source_chunk_id → document_chunks.id` 使用 `ON DELETE SET NULL`（切片是来源定位而非所有权）。
- 这是必需的：否则一旦存在学业投影，既有的文档删除 / 重新解析（会先删 `document_chunks`）就会被外键挡住，报 `FOREIGN KEY constraint failed`。已有阶段 1–7A 回归测试覆盖该路径。
- **旧库迁移**：SQLAlchemy 的 `create_all` **不会**修改既有表的外键，阶段 7A 建库时 `source_doc_id` / `source_chunk_id` 仍是 `NO ACTION`。`init_database` 现在会按 SQLite 官方推荐流程**幂等重建**这 6 张学业表（关闭外键 → 打开 `legacy_alter_table` → 改旧表名 → 按当前模型建表 → 拷数据 → 删旧表 → 重建索引），整个过程在**单个事务**内完成，先执行 `PRAGMA foreign_key_check`（必须为空）再提交，失败即回滚；不删除数据库、不要求重建数据卷、不丢任何 record / rule / projection 行。
- 迁移后 `PRAGMA foreign_key_list` 与全新数据库**逐列一致**，重复执行两次结果不变、行数不变。
- `source_chunk_id` 保存的是**真实存在的切片 ID**，切片被置空后由下一次投影对账重建，不伪造 ID。

### 阶段 7B-1 收尾修复轮（BUG-7B-04 / BUG-7B-05）

阶段 7 仍为 `in_progress`，`/api/health` 的 `planning` 仍为 `unavailable`；本轮只修复两个已复现缺陷，**未实现** `POST /api/academic/records/import`、`POST /api/academic/rules/import`、`GET /api/academic/options`、`POST /api/academic/plan`，未进入 7B-2。

- **BUG-7B-04（旧 SQLite 数据库外键未迁移）**：`init_database` 此前只有 `ALTER TABLE ADD COLUMN` 增量迁移，`create_all` 又不会改写既有外键，因此沿用阶段 7A 数据卷的实例中 `source_doc_id` / `source_chunk_id` 仍为 `NO ACTION`，文档删除 / 重新解析会被外键挡住。已新增上述幂等表重建迁移，并在**真实开发数据卷**上实测：迁移前五张表均为 `NO ACTION`，迁移后 `source_doc_id=CASCADE`、`source_chunk_id=SET NULL`，`PRAGMA foreign_key_check` 为空，各表行数不变（迁移前后均为 0）。
- **BUG-7B-05（demo 学业集合缺少 active / inactive 状态）**：投影落库时未写 `activation_state`，也没有退役旧 demo 版本，同一来源可能同时存在多个「可用」集合。现在投影落库与状态对账处于**同一事务**：唯一 active dataset 的 demo record / rule set 写为 `active`，其它 demo 版本统一为 `inactive`，`upload` 来源完全不参与；旧集合只退役**不删除**，不静默选择某个 rule set，且即使本次 seed 全部 `skipped` 也会执行状态对账。
- 两个缺陷均先补**修复前失败测试**（各 4 项失败、退出码 1）再修复，修复后定向与全量测试退出码 0。
- **测试编辑更正（非产品缺陷）**：新增迁移测试最初的期望表把 `record_set_id: CASCADE` 也套用到 `academic_projections`，而模型对投影的定义是 `ON DELETE SET NULL`；已改为按表声明期望值，并在修复前重新采集失败证据。

### 阶段 7A 修复轮（契约与冲突 warning）

阶段 7 仍为 `in_progress`；本轮只修复两个已复现的 7A 缺陷，**未开始 7B**（未实现导入、options API、plan API、health planning ready 与前端功能）。

- **BUG-7A-01（`PlanningResult` 固定字段契约漂移）**：7A 首版在 `PlanningResult` 上额外加了 `major` / `rule_version`，并在 `planning_result_payload()` 中输出，违反 PRODUCT_SPEC 5.2「字段固定」。已恢复为**严格 8 字段**，payload 顶层 key 亦严格为这 8 项；未修改 `docs/PRODUCT_SPEC.md`。新增回归测试断言 `dataclasses.fields()` 与 payload key 列表逐一相等（**不允许只从 payload 隐藏字段**）。
- **BUG-7A-02（`passed` 与 `in_progress` 并存漏报冲突）**：首版先删除被 `passed` 覆盖的 `in_progress`，再计算矛盾集合，导致该组合缺少 `COURSE_RECORD_CONTRADICTION`。已改为**在删除之前采集二者交集**，矛盾判定覆盖 `failed`+`passed`、`failed`+`in_progress`、`passed`+`in_progress` 三种组合；同一课程无论多少条矛盾记录只产生一个稳定 warning（按课程代码排序）；`passed` 优先、重复课程只计一次、Decimal 计算等既有行为未变。
- 修复前证据：新回归测试 **4 failed / 退出码 1**（字段契约 2 项 + 冲突 warning 2 项）；修复后 7A 定向测试与全量 `pytest` 全部通过，退出码 0。

## 阶段 6 结论（SSE 问答与引用）

阶段 6 为 `completed`；阶段 7 仍为 `not_started`。本轮**只实现后端问答能力**，未实现 Chat 前端页面、SSE 前端解析、学分规则引擎与 RAG 评测。

### API 与 SSE 契约

- 新增 `POST /api/chat/stream`（`text/event-stream`），请求体仅含 `messages`（1–10 条）与 `filters`（`major` / `grade_year` / `semester` / `doc_category`）。
- 严格校验：role 只允许 `user` / `assistant`；最后一条必须是非空 user；单条 ≤ 4000 字符、总计 ≤ 12000 字符；`messages` / `message` / `filters` 一律 `extra="forbid"`。校验失败在**开流前**返回统一 JSON 422（`{code,message,details,request_id}`），`details` 只含字段位置与错误类型，**不回显用户原文**。
- SSE 只允许四种事件：`token` / `citation` / `done` / `error`。标准帧 `event: <name>\ndata: <单行JSON>\n\n`，UTF-8，`Cache-Control: no-cache`、`X-Accel-Buffering: no`。
- `citation` 字段严格为 PRODUCT_SPEC 6.3 的 13 个字段（`citation_index` 从 1 连续、流内唯一），**不含**分数、路径或内部诊断；`citation_count` 等于实际 citation 事件数；每条流恰好一个 `done` 或 `error`，之后立即结束；拒答与冲突用 `done` 而**不是** `error`。
- `request_id` 在创建 StreamingResponse **之前**从 `request.state` 读取并显式传入流生成器（中间件会在 `call_next` 返回后重置 ContextVar），`X-Request-ID` 响应头与 `done`/`error.request_id` 完全一致。
- 开流前的已知错误（schema、Provider 未配置、生产环境 Fake）走统一 JSON 4xx/5xx；响应头发出后由流生成器自行收敛为唯一 SSE `error`，绝不泄漏 traceback、异常文本、请求正文或配置值。稳定错误码：`LLM_PROVIDER_UNAVAILABLE`、`LLM_PROVIDER_FORBIDDEN`、`MODEL_TIMEOUT`、`MODEL_RESPONSE_INVALID`、`MODEL_STREAM_INTERRUPTED`、`CHAT_QUERY_REWRITE_FAILED`。

### LLM Provider 与健康语义

- 新增 `app/llm/`：`base`（`LLMDescriptor` / `LLMProvider`）、`fake`、`api`（OpenAI 兼容，使用现有 httpx，**不引入 OpenAI SDK**）、`factory`、`prompts`。**不提供本地 LLM**。
- Fake：完全确定性（SHA-256/纯文本派生，不使用 `hash()`），跨进程与任意 `PYTHONHASHSEED` 一致，零网络、零模型、不读 `ground_truth`、不针对演示问题硬编码；`production` 由工厂拒绝。
- API：`POST {LLM_BASE_URL}/chat/completions` + Bearer + 显式 timeout + 有界响应体；Base URL / model / API Key 缺一即安全失败；构造与健康检查不联网；所有测试使用 `httpx.AsyncClient` 替身。
- `loaded` 为**证据式**语义：API Provider 首次成功且响应结构校验通过后才为 true，失败或 `close` 后恢复 false；Fake 可直接为 true。
- `capabilities.chat` 与 `providers.llm.loaded` **严格分离**：`chat` 表示前端能否发起请求（未配置 → `unconfigured`；无当前可检索文档 → `unavailable`；否则 `ready`），**不绑定** `loaded`，避免「chat != ready 就禁止建立 SSE，而没有第一次 SSE 就永远无法 ready」的死锁。可检索文档统计复用检索侧 `RetrievalScope` + eligibility 口径，candidate / inactive / 旧 pipeline 指纹文档都不计入。

### 问题改写、拒答、冲突与引用映射

- 单轮直接使用最后一条 user 消息；**多轮才**调用 LLM 改写为独立查询，历史只用于改写、**不作为事实证据**；改写结果必须非空且 ≤ `LLM_REWRITE_MAX_CHARS`，失败返回稳定错误（不静默使用可能改变语义的查询）；`filters` 直接映射为 `RetrievalFilters`，模型无法增删过滤条件。
- 检索固定走 `RerankingRetriever`，候选 ≤ 20、输出 ≤ 6；证据不足 6 条时只使用实际数量。
- 同步检索（Embedding / SQLite / Chroma / FTS / Reranker）统一放入线程池，线程内**自建短 Session 并立即关闭**，只把脱离 Session 的 `RerankedResult` 交给生成阶段；请求处理过程**不持有任何 Session**，也不使用 `Depends(get_session)`。
- 拒答：零结果 → `no_evidence`；显式 `RETRIEVAL_SCORE_THRESHOLD` 下优先比较 `rerank_score`，reranker 降级（`rerank_score` 缺失）时返回 `score_unavailable`，**绝不用 `fused_score` 顶替比较**；阈值默认为空，不自造默认值。以上三条路径**都不调用生成 LLM**，`citation_count=0`。
- 规划类问题（阶段 7 未实现）由通用意图守卫拒绝，`reason_code=planning_unavailable`，不让模型心算学分或生成规划数字。
- 冲突：`app/chat/conflict.py` 只依据**证据文本与文档版本**做确定性检测（跨版本同字段取值不一致；同一文档内重复槽位但内容不同），**不读取** `demo/ground_truth.jsonl`、`manifest.intentional_conflicts` 或演示问题 ID。命中后服务端强制 `outcome=conflict`、`reason_code=version_conflict`，并要求引用覆盖冲突双方（跨版本），citation 中并列 `document_version` / `effective_from`，**不得替用户选择版本**。
- 引用安全：模型只能引用服务端编号；`chunk_id` / `doc_id` / `file_name` / `page_number` / `sheet_name` / 行范围 / `section_title` / `quote` 一律由服务端从原始 `RerankedResult` 构造。`GroundedCompletion` 在**发送任何 token 之前**完整校验（outcome 合法、reason_code 白名单、answer 非空且有长度上限、编号为整数且唯一且在范围内、正文 `[n]` 与结构化引用一致），任何未知/越界/重复/缺失/不一致都返回 `MODEL_RESPONSE_INVALID`；非连续编号安全重映射为从 1 开始的连续 `citation_index` 并同步重写正文标记；`citation.quote` 使用**原始** `chunk.text`。

### Privacy v4 外发边界

- 所有发往外部 LLM 的文本（当前问题、历史、证据片段、冲突版本行）都在 Provider 边界调用共享的 `app/core/privacy.py`，当前策略 `external-privacy-v4`。
- 只发送回答所需的最少证据；**不发送**绝对路径、`storage_path`、`chunk_id`、`doc_id`、locator、文件系统路径、检索分数、API 配置或不必要的文件名与内部诊断（`doc_id` 仅用于服务端冲突检测，不进提示词）。
- 只清洗外发副本：原 `messages` / query / `RetrievedChunk` / SQLite / Chroma / FTS / `citation.quote` / locator 零改动。
- LLM descriptor 记录 `privacy_policy_version` / `prompt_version` / `response_schema_version` 并生成稳定 fingerprint，但**不进入** `pipeline_fingerprint`，也不触发任何索引重建。

### 取消与资源释放

- 使用异步 StreamingResponse 与异步 HTTP；客户端断开或 `asyncio.CancelledError` 时停止读取上游、不再发送任何事件（含 `done`/`error`）、继续向上传播取消。
- `finally` 中取消在途任务并标记资源释放；上游 response / `AsyncClient` 由 `async with` 保证关闭；进程级 Provider 由 lifespan 幂等 `aclose`。测试**直接驱动异步生成器**并断言 finally 执行、取消后零事件。
- 线程池中已开始的同步检索不虚假声称被杀死；断开后不再生成或发送任何 SSE。

### 测试与默认环境状态

- 新增 `tests/test_llm_provider.py`、`test_chat_contract.py`、`test_chat_flow.py`、`test_chat_resilience.py`、`test_chat_health.py`；覆盖请求边界、原始 SSE framing、引用压力、拒答、冲突、多轮改写、提示注入、Privacy v4 外发、上游错误、取消与资源释放、Fake 确定性与健康语义。
- 全量 `docker compose exec backend pytest` → **445 passed**；生成器镜像 `--network none` → **72 passed**；前端 `pnpm test` → **11 passed**、`pnpm build` 成功；`docker compose config --quiet`、`ps`、`/api/health`、日志泄漏扫描全部通过。
- 默认 backend 镜像仍**没有** torch / sentence-transformers / 本地 LLM / 模型权重 / Hugging Face 缓存；`./data/models` 仍为空；**未下载或运行任何真实模型，未调用真实外部 API**。
- 默认 Local 环境仍为 `degraded`：`embedding.ready=false`、`reranker.ready=false`、`llm=unconfigured`、`planning=unavailable`。

### 阶段 6 修复轮（BUG-6-05 与外部 LLM 配置接线）

阶段 6 继续保持 `completed`；阶段 7 保持 `not_started`。**本修复轮没有调用任何真实外部 API**（全部使用假配置与 MockTransport 验证）。

- **BUG-6-05（跨版本冲突判定反向）**：`app/chat/conflict.py::_cross_version_conflicts()` 原先按**取值**索引旧记录，导致「同字段、不同版本、不同取值」不被识别，而「同字段、不同版本、相同取值」反被判为冲突。实测探针：`different=None`（应为冲突）、`same=ConflictHint(...)`（应无冲突）。已改为按 **字段名 → 版本 → 取值** 归集：只有「规范化字段名相同 + `document_version` 不同 + 取值不同」才构成跨版本冲突；同一版本内（含同一 chunk 内）重复字段不制造伪冲突；多版本多取值时 indices 排序、去重且与证据顺序无关；元数据字段（文档版本 / 生效日期 / 文档类型 / 适用学期 …）按定义就会不同，永不判为冲突。同文档跨切片课表冲突检测**保持不变**。
- **冲突判定权归服务端**：模型自行返回 `outcome=conflict` 而服务端 `detect_conflicts()` 未发现冲突时，判为 `MODEL_RESPONSE_INVALID`（唯一 error，不向客户端输出 conflict）；服务端检测到冲突时强制 `outcome=conflict`、`reason_code=version_conflict`；冲突回答漏引任意一方的证据同样判为非法；`answered` 不允许携带 `reason_code`；`refused` 不允许携带 citation。BUG-6-02 的引用重映射修复未被破坏。
- **外部 LLM 配置接线**：`docker-compose.yml` 通过 Compose 环境变量插值把 `LLM_PROVIDER` / `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` / `LLM_TIMEOUT_SECONDS` / `LLM_ANSWER_MAX_CHARS` / `LLM_REWRITE_MAX_CHARS` 与 `RETRIEVAL_SCORE_THRESHOLD` 传入 backend，默认值均为空或安全默认，**默认环境不指向任何真实外部 API**；`.env.example` 补齐 `LLM_ANSWER_MAX_CHARS` / `LLM_REWRITE_MAX_CHARS`；未提交任何真实 `.env` 或 API Key。
- **阈值解析**：`RETRIEVAL_SCORE_THRESHOLD` 的空字符串（含纯空白）安全解析为 `None`（阶段 6 不创造默认阈值），非法值仍然报错、不静默回退；空值 / 合法浮点值 / 非法值三条路径均有测试。
- **CORS 与 SSE 回归**：CORS 显式 `expose_headers=["X-Request-ID"]`（`allow_headers` 不等于 `expose_headers`），带 Origin 的请求可读到 `Access-Control-Expose-Headers`；新增 SSE 注入回归 —— 正文或 `quote` 中含换行与伪造的 `event: error\ndata: {...}` 时只能作为 JSON 字符串内容，事件行仍只有 `citation` / `token` / `done`，每个事件恰好一行 `data`，终止后零字节。
- **验收**：新增 `tests/test_chat_conflict.py`、`tests/test_chat_config.py` 与 6 项 Chat 回归；`docker compose exec backend pytest` → **470 passed**；生成器 `--network none` → **72 passed**；前端 `pnpm test` → 11 passed、`pnpm build` 成功；`docker compose config --quiet` 使用假值通过且未回显密钥；默认环境 `GET /api/health` 仍为 `degraded`、`chat=unconfigured`、`llm.ready=false`；未下载模型、未访问真实 API、未启动 GPU Profile。

### 未实现范围

- 阶段 7 学分规则引擎、阶段 8 Vue Chat 页面与前端 SSE 解析、阶段 9 RAG 评测、问题改写之外的任何 LLM 能力（答案生成以外的引用流/多轮记忆持久化）；服务端**不保存**聊天历史，也没有新增数据库表。

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

- 输出：`backend/app/core/privacy.py`（当时策略 `external-privacy-v2`，**现为历史版本**）、`backend/app/embedding/api.py`（空输入 no-op）、`backend/tests/test_stage5_fixes.py`（新增 14 项回归）。未新增依赖、未改数据库 schema、未改 Compose 结构。
- **BUG-5-03（隐私清洗覆盖不足）**：v1 只覆盖 `标签 + 冒号 + 无空格值`，因此 `姓名 张三`、`姓名：张 三`（只遮掉「张」残留「三」）、`| 姓名 | 张三 |`、`姓名<TAB>张三`、`学号 20260001`、`| 学号 | 20260001 |`、`电话 010-12345678`、`电话：(010) 12345678` 都会把个人信息原样发给外部模型。已在**同一个共享模块**中扩展（不在两个 Provider 里复制规则）：分隔符覆盖**冒号 / 等号 / 空白 / TAB / Markdown 与表格竖线**；标签值允许**内部空格**并清洗到稳定字段边界（遇到下一个 PII 标签、竖线、换行或中文句读即停）；新增**中国大陆固定电话**（`0xx`/`0xxx` 区号，含 `(010)` 括号形式）与带标签的电话规则。课程代码（`QM-CS201`）、课程名称、学分、学期、日期、普通数字**不被误清洗**。仍保持确定性、幂等、完全离线、无 NER、无第三方服务；只清洗外发副本，原 query / candidates / SQLite / Chroma / FTS / citation / quote 零改动。
- **版本与指纹影响**：`PRIVACY_POLICY_VERSION` 由 `external-privacy-v1` 提升为 **`external-privacy-v2`**（值语义变化必须提升版本）。API Embedding descriptor 带上该版本 ⇒ `embedding_fingerprint` 改变并**按既有机制触发 API 向量重建**；Local / Fake Embedding 仍为 `None` 且**不进入** `as_dict()` ⇒ 指纹与 `pipeline_fingerprint` 逐字节不变；Reranker 描述符记录该版本但**仍不进入** `pipeline_fingerprint`，**不触发任何文档索引重建**。
- **BUG-5-04（空 Embedding 输入伪造 readiness）**：`embed_documents([])` 此前会构造 HTTP Client（无请求）并返回 `[]`，随后把 `loaded` 从 `false` 置为 `true`，属于「没有真实请求却谎报 ready」。已改为**空输入直接 no-op**：返回 `[]`、不创建 Client、不发送请求，且**既不能伪造成功、也不清除既有成功证据**；只有非空输入经过真实请求并成功校验响应结构后才 `false → true`，失败或 `close()` 后仍恢复 `false`。ApiReranker 的空候选 no-op 行为保持不变（有回归测试）。
- **验收**：`docker compose exec backend pytest` → **338 passed**（修复前 324，+14）；`docker run --rm --network none … pytest tests/test_stage5_fixes.py tests/test_rerank_provider.py` → **62 passed**；生成器 `--network none` → **72 passed**；前端 `pnpm test` → **11 passed**、`pnpm build` 成功；`docker compose config --quiet`、`ps`、`/api/health`、`logs` 全部符合预期，日志隐私泄漏扫描无命中。
- **资源保护**：默认 backend 镜像仍**没有** torch / sentence-transformers，镜像内无模型权重、无 `/app/data`、无 Hugging Face 缓存；`./data/models` 仍为空；**未下载模型、未运行真实模型、未安装重型依赖、未访问真实外部 API**。
- **边界**：默认 Local 环境仍为 `degraded`，`embedding.ready=false`、`reranker.ready=false`、`chat=unconfigured`、`planning=unavailable`。阶段 6 仍未开始。

### 阶段 5 混合字段边界修复（BUG-5-05 / external-privacy-v3）

阶段 5 仍为 `completed`；**未开始阶段 6**。

- 输出：`backend/app/core/privacy.py`（当时策略 `external-privacy-v3`，**现为历史版本**）、`backend/tests/test_stage5_fixes.py`（新增 11 项回归，移除 1 项被取代的 v2 版本断言）。未新增依赖、未改数据库 schema、未改 Compose 结构、未改动两个 API Provider 的调用点（继续共用同一个 `privacy.py`）。
- **BUG-5-05（隐私清洗误删同一行学术字段）**：v2 的字段值只把「下一个 PII 标签 / 竖线 / 换行 / 句读」当作终止边界，因此 `姓名 张三 课程编号 QM-CS201 学分 3 学期 2026-2027-1` 会把**整行**当作姓名值吞掉（输出直接变成 `姓名：[REDACTED_NAME]`），学号行同理；`姓名：张 三 课程名称：数据结构 学分：3` 还会连带删掉「课程名称」并留下孤立冒号。
- **修复方案（稳定的字段边界策略）**：新增 `_FIELD_BOUNDARY_LABELS`，把常见的普通字段名识别为边界 —— 课程编号 / 课程代码 / 课程名称 / 课程类别 / 课程性质、学分 / 学期 / 成绩 / 绩点、专业 / 年级 / 班级 / 学院 / 培养层次、日期 / 时间 / 地点 / 教室 / 校区、状态 / 类型 / 备注 / 说明；并新增 `_GENERIC_KEY_BOUNDARY`，把任意「字段名 + 冒号或等号」也视为边界。这些字段**只用于截断 PII 值，自身永不被清洗**。
- **真实长度上限**：`_VALUE` 由「无界重复」改为**双重硬上限**（单 token ≤ `_VALUE_TOKEN_MAX_CHARS`=48 字符，token 数 ≤ 1+`_VALUE_EXTRA_TOKENS`=5），并导出 `MAX_FIELD_VALUE_CHARS`=240 供测试校验，杜绝「名为有界、实为无界」。PII 值在下一个字段开始前停止，**不吞掉字段间空白**与后续字段。
- **版本与指纹影响**：`PRIVACY_POLICY_VERSION` 由 `external-privacy-v2` 提升为 **`external-privacy-v3`**（外发文本语义再次变化）。API Embedding descriptor 携带该版本 ⇒ `embedding_fingerprint` 变化并**按既有机制触发 API 向量重建**；Local / Fake Embedding 仍为 `None` 且不进入 `as_dict()` ⇒ 指纹与 `pipeline_fingerprint` 逐字节不变；Reranker 记录该版本但**仍不进入** `pipeline_fingerprint`，**不触发任何文档索引重建**。
- **兼容性**：上一轮 8 种键值格式、TAB / 表格竖线、带内部空格的姓名、固定电话、幂等性、确定性、离线与「只清洗外发副本」全部继续成立；`3d3710b` 的空 Embedding readiness 修复未被破坏（空输入仍为 no-op）。
- **验收**：`docker compose exec backend pytest` → **348 passed**（修复前 338）；生成器 `--network none` → **72 passed**；前端 `pnpm test` → **11 passed**、`pnpm build` 成功；`docker compose config --quiet`、`ps`、`/api/health`、日志隐私泄漏扫描全部符合预期。
- **资源保护**：默认 backend 镜像仍无 torch / sentence-transformers，镜像内无模型权重、无 `/app/data`、无 Hugging Face 缓存；`./data/models` 仍为空；未下载模型、未运行真实模型、未安装新依赖、未访问真实外部 API。
- **边界**：默认 Local 环境仍为 `degraded`（`embedding.ready=false`、`reranker.ready=false`、`chat=unconfigured`、`planning=unavailable`）。阶段 6 仍未开始。

### 阶段 5 Unicode 隐私格式收尾（BUG-5-06 / external-privacy-v4）

阶段 5 仍为 `completed`；**未开始阶段 6**。

- **隐私清洗策略版本状态**：`external-privacy-v1` / `v2` / `v3` 均为**历史版本**；**当前有效版本为 `external-privacy-v4`**（`PRIVACY_POLICY_VERSION`）。上文各修复小节中的 v2 / v3 均为当时的版本记录，不代表当前值。
- 输出：`backend/app/core/privacy.py`（策略 v4）、`backend/tests/test_stage5_fixes.py`（新增 8 项 Unicode 回归）。未新增依赖、未改数据库 schema、未改 Compose 结构、未改动两个 API Provider 的调用点（继续共用同一个 `privacy.py`）。
- **BUG-5-06（Unicode 横向空白 / 全角标点未覆盖）**：v3 的分隔符、字段值与电话规则只认 ASCII 空格 / TAB 与 ASCII 括号 / 连字符，因此 `姓名　张三`（U+3000 全角空格）、`姓名 张三`（U+00A0 NBSP）、`学号　20260001` 完全不生效；`电话：（010）12345678`（全角括号）、`电话：010－12345678`（全角连字符）、`电话：010–12345678`（en dash）与 `电话 010  12345678`（连续空格）也都不会被识别 —— 个人信息会原样发给外部模型。
- **修复方案**：新增 `_HSPACE_CHARS`（空格 / TAB / NBSP / Ogham 空格 / U+2000–U+200A / U+202F / U+205F / U+3000），并让**标签**（`姓…名`、`学…号`）、**键值分隔符**、**字段边界**、**`_GENERIC_KEY_BOUNDARY`** 与**值内连接符**统一使用它；`_HYPHEN_CHARS` 覆盖 ASCII 连字符 / 全角连字符 / en dash / em dash，`_OPEN_PAREN`/`_CLOSE_PAREN` 覆盖 ASCII 与全角括号，`_PHONE_GAP` 允许区号与号码之间**一个或多个横向空白或连接符**。
- **不跨行保证**：`_HSPACE_CHARS` 刻意**不含** CR、LF、VT、FF 与 U+2028 / U+2029，且字段值字符类排除 `\s`，因此 Unicode 空白匹配**绝不把下一行吞入当前字段**（有专门回归测试）。
- **版本与指纹影响**：版本提升为 **`external-privacy-v4`** ⇒ API Embedding `embedding_fingerprint` 改变（与 v1 / v2 / v3 均不同）并按既有机制**触发 API 向量重建**；Local / Fake Embedding 该字段为 `None` ⇒ 指纹与 `pipeline_fingerprint` **逐字节不变**；Reranker 仍**不进入** `pipeline_fingerprint`，**不触发任何文档索引重建**。
- **兼容性**：v2 的 8 种键值格式、v3 的混合字段边界与 `MAX_FIELD_VALUE_CHARS` 真实长度上限、`3d3710b` 的空 Embedding readiness no-op 全部无回归；确定性、幂等、离线、无 NER、无新依赖、只清洗外发副本全部继续成立。
- **验收**：`docker compose exec backend pytest` → **356 passed**（修复前 348）；生成器 `--network none` → **72 passed**；前端 `pnpm test` → **11 passed**、`pnpm build` 成功；`docker compose config --quiet`、`ps`、`/api/health`、日志隐私泄漏扫描全部符合预期。
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
