# 可复现模拟资料与异步初始化规格

## 1. 数据集边界

- 模拟学校统一命名为“启明大学”，所有学校、院系、教师、学生、学号、课程安排和制度内容均为虚构，不影射真实机构。
- 每个 PDF、DOCX、XLSX 的封面、首页或首个工作表醒目标注“仅供系统演示的虚构资料”。
- 不得包含真实姓名、学号、身份证号、电话、邮箱、校徽、学校域名、内部系统截图或真实学校文件片段。
- 生成过程完全离线，不抓取网站、不访问教务系统、不调用外部 API 或大模型。
- 用户上传是可选正式功能，不得成为演示、测试或验收前置条件。

## 2. 首版语料组成

`demo/corpus/` 固化 12–18 个跨 PDF、DOCX、XLSX 的文件：

- 至少 2 个不同生效年份或版本的培养方案，用于版本选择和冲突提示。
- 至少 3 份课程大纲，包含课程代码、学分、类别、先修关系和考核方式。
- 选课、补考、重修和学分认定制度。
- 校历与至少 2 份考试通知，包含可精确检索的日期、地点和课程代码。
- 至少 2 份课表，包含工作表、表头、完整行范围和时间冲突样例。
- 至少 2 份匿名课程记录或成绩表，覆盖已修、在修、重修和不及格状态。
- 至少 1 份包含不可信指令文本的安全测试文档；该文本只能作为资料，不具有指令优先级。

所有文件共享一份代码内受控事实模型。课程代码、名称、学分、学期、日期、先修关系、培养方案规则和匿名学生记录必须跨文件一致。有意冲突必须在 manifest 明确声明，不能由生成错误偶然产生。

## 3. 确定性生成

`scripts/generate_demo_corpus.py` 必须：

1. 使用代码和 manifest 中记录的固定随机种子。
2. 只从代码内虚构事实模型生成 PDF、DOCX、XLSX。
3. 使用锁定的 Python 依赖和仓库内固定字体；推荐在固定 Docker 构建环境运行。
4. 固定作者、创建/修改时间、PDF 元数据、文档区域设置和 DOCX/XLSX ZIP 条目时间戳。
5. 先生成到显式指定的临时输出目录，校验通过后再由开发者更新 `demo/corpus/`；应用启动不得重写固化语料。
6. 为每个文件计算 SHA-256，生成字段顺序与数组顺序稳定的 `demo/manifest.json`。
7. 生成不少于 50 条 `demo/ground_truth.jsonl` 记录。

命令必须显式提供输出目录，例如：

```powershell
python scripts/generate_demo_corpus.py --output .tmp/demo-generated --seed 20260925
```

不得默认覆盖 `demo/corpus/`。相同生成器镜像、依赖锁、字体、区域设置和种子下，文件集合、文件字节、manifest 和 SHA-256 必须一致；跨平台只要求 manifest 描述的规范化文本与事实模型一致。字节级复现验收在固定生成器环境中执行。

## 4. Manifest 与 Ground Truth

`demo/manifest.json` 至少包含：

```text
dataset_name
dataset_version
school_name
fictional
seed
generator_version
generated_at
documents[]:
  path
  sha256
  file_type
  doc_category
  title
  version
  effective_from
  expected_pages_or_sheets
  intentional_conflicts
```

约束：

- `fictional` 固定为 `true`。
- `generated_at` 使用生成器版本定义的固定 UTC 值，不读取当前时间。
- `documents` 按 `path` 排序，路径必须位于 `demo/corpus/`，禁止绝对路径和 `..`。
- 每个 manifest 文件必须存在且 SHA-256 匹配；语料中不得存在未登记文件。

`demo/ground_truth.jsonl` 每行至少包含：

```text
id
category
question
expected_answer_facts
expected_source_paths
expected_locators
should_refuse
conflict_expected
planning_input
expected_planning_result
```

不适用字段使用 `null` 或空数组。评测记录只能引用 manifest 文件；学分期望值由确定性规则从同一事实模型生成。

## 5. 持久化模型

### 5.1 `demo_seed_jobs`

至少保存：

```text
id
dataset_version
manifest_sha256
pipeline_fingerprint
target_stage
status
total_count
imported_count
resumed_count
skipped_count
failed_count
current_stage
created_at
started_at
finished_at
lease_owner
lease_generation
lease_expires_at
error_code
error_message
```

`status` 只允许：

```text
queued
running
completed
completed_with_errors
failed
```

`target_stage` 是服务端能力常量，不由客户端提交：阶段 2 测试构建为 `parsed`，阶段 3 为 `vector_indexed`，阶段 4 及最终产品固定为 `completed`。job 的 `completed` 只表示全部非失败文档达到该 job 的 `target_stage`；数据集的 `loaded=true` 始终要求当前 manifest 全部文件达到最终 `completed`，不能因阶段性 job 完成而提前置真。

### 5.2 `demo_seed_job_documents`

每个 manifest 文件一条记录，至少保存：

```text
job_id
manifest_path
expected_sha256
doc_id
pipeline_state_id
status
last_completed_stage
failed_stage
retryable
attempt_count
error_code
error_message
```

文档任务 `status` 只允许 `pending`、`running`、`completed`、`failed`、`skipped`。`last_completed_stage` 只允许：

```text
none
validated
stored
parsed
chunked
vector_indexed
keyword_indexed
completed
```

### 5.3 `document_pipeline_state`

检查点必须独立于单次 job 跨任务保存；job document 只记录本次执行结果并引用该状态。至少保存：

```text
id
doc_id
source_type
source_key
file_sha256
last_completed_stage
parser_fingerprint
normalization_fingerprint
chunker_fingerprint
embedding_fingerprint
vector_schema_fingerprint
fts_schema_fingerprint
expected_chunk_count
vector_record_count
fts_record_count
failed_stage
error_code
error_message
updated_at
```

各阶段 fingerprint 分开存储，不能只保存一个总指纹；总 `pipeline_fingerprint` 用于任务身份和快速比较，分阶段 fingerprint 用于确定最早失效阶段。

### 5.4 来源与所有权

- 演示文件使用独立 `doc_id`、`source_type=demo` 和稳定 `source_key=dataset_version + manifest_path`。
- 用户上传使用 `source_type=upload` 和独立 `doc_id`。即使 SHA-256 相同，也不得跨来源复用文档行、Chroma 记录或 FTS5 记录。
- 去重只在同一来源键和同一流水线版本内执行。这样删除、改名或重新导入任一来源都不会改变另一来源。
- DELETE 按来源处理：upload 文档删除 `/app/data/uploads` 中的运行时原文件、索引和关联；demo 文档只删除运行时 materialization、SQLite、Chroma、FTS5 和关联，绝不修改只读 `/app/demo` 固化源文件。删除 demo 文档后，下次 seed 可以按 manifest 重新建立，不影响用户上传。

### 5.5 数据集版本激活与退役

- SQLite 保存唯一的 active demo dataset 指针，至少包含 `dataset_version`、`manifest_sha256`、`pipeline_fingerprint` 和激活时间。
- 新版本导入期间，文档保持 inactive，不参与检索；旧 active 版本继续提供服务。
- 只有新 manifest 的全部文件达到最终 `completed` 并通过 Chroma/FTS 对账，才在一个 SQLite 事务中切换 active 指针。
- 切换后，旧版本的 demo-only 文档标记为 inactive，Dense 与 FTS 检索立即过滤；其索引可以异步清理。清理只能作用于 `source_type=demo`，绝不删除 upload 文档或索引。
- 首次初始化未完整成功时不存在 active demo 版本；即使部分文档达到检查点，也不能参与正式检索。
- `loaded=true` 表示当前配置的 dataset/version/fingerprint 已成为 active 指针；仅完成导入但尚未激活不能返回 loaded。
- 这些内部状态必须投影到 Product Spec 的文档接口：新版本在整体激活前为 `activation_state=candidate, retrievable=false`；事务切换时新版本原子变为 `active, true`，旧版本同步变为 `inactive, false`。处理状态 `ready` 本身不得绕过 active 指针参与检索。

## 6. 流水线指纹与幂等

`pipeline_fingerprint` 至少由以下内容稳定序列化后计算 SHA-256：

```text
parser name and version
normalization version
chunker version and chunk settings
embedding provider/model/model revision/dimension
vector collection schema version
FTS schema/tokenizer version
```

逐文件决策：

1. 校验 manifest 路径、文件存在性和 SHA-256。
2. 若相同来源键、相同校验值，且已达到或超过当前 job 的 `target_stage`，该阶段要求的 fingerprints 与记录对账也一致，标记 `skipped`。
3. 若文件相同但任务未完成，从最后一个持久化成功阶段之后恢复，计入 `resumed`。
4. 若流水线组件版本变化，从受影响的最早阶段重建：解析器或规范化变化从 `parsed` 开始，切片配置变化从 `chunked` 开始，Embedding 指纹变化从 `vector_indexed` 开始，索引 schema 变化只重建对应索引。
5. 只有 Chroma 和 FTS5 均成功且记录数/`chunk_id` 集合一致，才能写入 `last_completed_stage=completed`。

“数据库中已有 document 行”不能作为跳过依据。每个阶段先完成目标存储的幂等写入，再提交 SQLite 检查点。失败重试不得产生重复 chunk、向量或 FTS 记录。

跳过判断以当前 job 的 `target_stage` 为边界：阶段 2 可以跳过已经达到 `parsed` 的工作，但不能把该文档视为最终就绪；阶段 3、4 必须继续处理同一 `document_pipeline_state`。最终产品中 `imported`、`resumed`、`skipped` 均以 `target_stage=completed` 统计。

## 7. 异步 HTTP API

### 7.1 `GET /api/demo/status`

返回当前数据集与装载概况：

```json
{
  "enabled": true,
  "state": "empty",
  "dataset_version": "2026.1",
  "manifest_sha256": "...",
  "pipeline_fingerprint": "...",
  "available_documents": 15,
  "ready_documents": 0,
  "failed_documents": 0,
  "loaded": false,
  "poll_after_seconds": 2,
  "active_dataset_version": null,
  "serving_previous_version": false,
  "active_job_id": null,
  "last_job_id": null,
  "reason": null
}
```

只有全部 manifest 文件对当前流水线指纹均为 `completed` 且该版本已成为 active demo dataset 时，`loaded` 才为 `true`。

`state` 只允许 `disabled`、`unavailable`、`empty`、`queued`、`running`、`partial`、`loaded`、`failed`，按以下优先级推导：配置关闭为 `disabled`；manifest/流水线不可用为 `unavailable`；存在活动任务时取 `queued` 或 `running`；全部就绪为 `loaded`；有就绪文档但未全部完成为 `partial`；无就绪文档且最近任务失败为 `failed`；其他情况为 `empty`。`reason` 仅在 disabled/unavailable/failed 时返回安全错误码和摘要。

若导入新版本失败但旧 active 版本仍可服务，`loaded=false`、`serving_previous_version=true`，并在 `active_dataset_version` 返回旧版本；前端必须说明当前仍使用旧版本。`reason` 非空时固定为 `{ "code": "...", "message": "...", "retryable": true }`。

### 7.2 `POST /api/demo/seed`

该接口只校验配置和 manifest、创建或复用持久化任务，不在请求内执行解析或索引。正常返回 HTTP `202 Accepted`，并设置 `Location: /api/demo/jobs/{job_id}` 与 `Retry-After: 2`：

```json
{
  "job_id": "uuid",
  "dataset_version": "2026.1",
  "target_stage": "completed",
  "status": "queued",
  "status_url": "/api/demo/jobs/uuid",
  "poll_after_seconds": 2,
  "reused_active_job": false
}
```

并发语义：

- 全局只允许一个 `queued` 或 `running` 的演示任务，通过 SQLite 部分唯一索引保证；不能因流水线指纹不同而并行启动第二条管线。
- 重复点击时返回现有任务和 `reused_active_job=true`，仍使用 HTTP 202，不创建重复任务。
- 若全部文档已就绪，仍创建一个可审计的快速任务；worker 将全部文档标记为 `skipped` 后完成。
- 不提供清空知识库或删除用户上传资料的演示重置接口。

POST 非 202 时使用 Product Spec 定义且包含 `request_id` 的统一错误体：配置禁用返回 409 `DEMO_DATASET_DISABLED`，manifest 不存在/非法或校验不匹配返回 422，流水线不可用返回 503。未知服务端错误返回 500，但不能泄漏路径或堆栈。

### 7.3 `GET /api/demo/jobs/{job_id}`

返回：

```json
{
  "job_id": "uuid",
  "dataset_version": "2026.1",
  "target_stage": "completed",
  "status": "running",
  "current_stage": "embedding",
  "total": 15,
  "imported": 4,
  "resumed": 1,
  "skipped": 2,
  "failed": 0,
  "processed": 7,
  "progress_percent": 46,
  "poll_after_seconds": 2,
  "created_at": "ISO-8601 UTC",
  "started_at": "ISO-8601 UTC",
  "finished_at": null,
  "documents": [
    {
      "manifest_path": "corpus/example.pdf",
      "file_name": "example.pdf",
      "doc_id": "uuid",
      "status": "completed",
      "last_completed_stage": "completed",
      "result": "imported",
      "error_code": null
    }
  ],
  "errors": []
}
```

`documents` 返回全部 12–18 个 manifest 项，不需要分页；`result` 只允许 `imported`、`resumed`、`skipped`、`failed`，运行中的条目为 `null`。`progress_percent` 由已终止的文档项数量计算，不能用模型估算。错误只返回 manifest 相对路径/安全显示名、错误码和安全摘要，不返回宿主机绝对路径或原文。

`current_stage` 只允许 `validating`、`storing`、`parsing`、`chunking`、`embedding`、`vector_indexing`、`keyword_indexing`、`activating` 或 null。`errors[]` 每项固定为 `{ "manifest_path": "...", "file_name": "...", "code": "...", "message": "...", "retryable": true }`。

统计字段互斥：`imported` 表示本任务从 `none` 开始并达到 `target_stage` 的文档，`resumed` 表示从既有检查点继续并达到 `target_stage` 的文档，`skipped` 表示任务开始前已达到 `target_stage` 且阶段指纹/记录对账一致的文档，`failed` 表示本任务终止时仍失败的文档。最终产品的 target 固定为 `completed`，此时上述完成含义才是完整就绪。终态必须满足 `processed = imported + resumed + skipped + failed = total`。

错误码至少包括：

```text
DEMO_DATASET_DISABLED
DEMO_MANIFEST_NOT_FOUND
DEMO_MANIFEST_INVALID
DEMO_FILE_CHECKSUM_MISMATCH
DEMO_JOB_NOT_FOUND
DEMO_PIPELINE_UNAVAILABLE
DEMO_DATASET_CHANGED
DEMO_PIPELINE_CHANGED
```

## 8. Worker、恢复与并发

- 使用 FastAPI lifespan 启动的应用内单 worker 加 SQLite 持久化队列，不增加 Redis、Celery 或第三个长期运行服务；Docker 固定一个 Uvicorn worker，阻塞解析和 Embedding 放入受控线程池，不能阻塞事件循环。
- 不得只使用 FastAPI `BackgroundTasks`；请求结束、进程崩溃或容器重启后任务状态必须保留。
- SQLite 启用 WAL 和 `busy_timeout`。worker 使用短事务原子领取任务并定期续租，不得在解析或 Embedding 期间持有写事务。
- worker 执行任何 SQLite/Chroma/FTS 写入前必须持有数据卷上的独占进程锁；Docker 和启动脚本禁止并行运行两个 backend。锁仍被旧进程持有时，新 worker 不得仅因数据库 lease 过期就开始外部写入。
- 所有 SQLite 任务更新都必须匹配 `lease_owner + lease_generation`；该条件只保护 SQLite 状态，不能被描述为单独阻止 Chroma 的迟到写入。独占进程锁负责保证外部存储只有一个写入者。
- 启动时把 lease 已过期的 `running` 任务重新置为 `queued`，并从文档检查点恢复。
- worker 领取或恢复任务前必须重新计算当前 manifest SHA-256 和流水线指纹。若与 job 快照不一致，把旧 job 标记为 `failed`，记录 `DEMO_DATASET_CHANGED` 或 `DEMO_PIPELINE_CHANGED`，不得用新代码结果更新旧指纹任务；随后才能创建当前版本的新 job。
- 同一后端进程一次只处理一个演示任务；数据库唯一约束防止存在多个活动任务。
- 单个文档失败不回滚已完成文档。存在部分失败时任务结束为 `completed_with_errors`；manifest、配置或数据库等任务级错误使用 `failed`。
- 文档级瞬时错误最多自动尝试 3 次；仍失败则记录失败并继续其他文档。用户再次 seed 时只恢复未完成或指纹过期项。
- 每个文件的 Chroma 与 FTS5 一致性验证通过后才标记完成。重试先按 expected chunk ID 和各阶段 fingerprint 对账：保留 ID 与版本均正确的记录，只删除多余、缺损或版本不匹配的记录，禁止按失败阶段整批清空正确向量。
- SQLite 与 Chroma 无法组成分布式事务，因此阶段操作允许至少一次执行，但通过确定性 ID、upsert、版本检查和集合对账保证数据效果恰好一次。若 Chroma upsert 后、SQLite 检查点前崩溃，恢复时复用 revision 正确的向量，不再次调用 Embedding。
- `parsed` 先持久化块再提交检查点；`chunked` 先持久化预期 chunk ID 集合；`vector_indexed` 先 upsert 并核对 Chroma；`keyword_indexed` 的规范化 chunk、FTS5 和 SQLite 检查点在同一事务更新；最终确认 SQLite、Chroma、FTS5 ID 集合完全一致后才写 `completed`。
- demo 文档只有同时满足当前 `pipeline_fingerprint + completed` 且属于 active dataset 指针时才能参与检索；upload 文档按自身就绪状态参与。Dense 与 FTS 结果都必须依据 SQLite 来源、就绪和激活状态过滤。
- 应用关闭时停止领取新任务，让当前步骤在超时内完成；未完成 lease 到期后由下次启动恢复。

## 9. 前端交互

本节只定义演示任务的行为语义；按钮、布局、文案层级和响应式呈现以 `docs/UI_SPEC.md` 为准。

- 知识库页先读取 `/api/demo/status`。
- 数据集不可用时禁用“加载演示资料”并显示安全错误信息。
- 点击后调用 `POST /api/demo/seed`，保存 `job_id`，按响应体 `poll_after_seconds` 轮询任务接口；字段缺失时使用 2 秒兜底。
- `queued`/`running` 期间禁用重复提交；刷新页面后通过 `active_job_id` 恢复进度展示。
- 完成后分别展示 imported、resumed、skipped、failed，并刷新文档列表。
- `completed_with_errors` 展示可重试操作；重试仍调用同一 POST，由后端从检查点恢复。
- 知识库为空时可以引导加载，但不得自动调用接口。

## 10. 验收测试

- 固定生成器环境中相同种子两次生成的文件集合、字节和 SHA-256 一致。
- manifest 文件存在性、路径安全、类型、版本和校验值全部匹配。
- 基于允许名单、禁止模式和人工复核清单确认无真实学校标识或个人信息。
- 首次 POST 立即返回 202，HTTP 请求不等待解析或模型执行。
- 并发两次 POST 返回同一活动任务；数据库只有一个活动 job。
- 首次任务把全部文件写入文档表、Chroma 和 FTS5；第二次全部 `skipped` 且三套存储无重复。
- 在 parsed、Chroma upsert 后但 vector 检查点前、vector_indexed 和 FTS 写入阶段分别模拟崩溃，重启后从正确检查点恢复；已有 revision 正确向量不得再次调用 FakeEmbedding。
- 改变 chunk 配置或 Embedding 指纹后，从正确的最早失效阶段重建，而不是错误跳过。
- 单文件失败产生 `completed_with_errors`，其他文件的完成检查点保留且不回滚，但不激活不完整的新版本；若已有旧 active 版本则继续检索旧版本。再次 POST 只恢复失败/过期项，全部成功后再原子切换 active 版本。
- 模拟资料与用户上传共存，初始化不覆盖、改名或删除用户数据。
- PDF 页码、DOCX 章节、XLSX 工作表及行范围均可追溯。
