# Project Status

- 项目：校园多源文档 RAG 学业规划助手（启明大学模拟资料）
- 当前运行模式：**默认 CPU**（不申请 GPU / CUDA；`gpu` Profile 保持关闭）
- 当前阶段：**阶段 8 进行中（8A、8B-1 已完成；8B-2、8C、8D 未开始）**
- 下一阶段：**阶段 8B-2 — Chat 页面布局、证据面板与来源抽屉**（必须读取 `docs/UI_SPEC.md` 第 6 节）
- 最近更新：2026-09-28

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
| 7 | 确定性学分规则引擎 | completed |
| 8 | Vue 核心页面 | in_progress |
| 9 | RAG 评测与安全测试 | not_started |
| 10 | 一键启动与复现 | not_started |

## 阶段 8A 结论（Vue 应用框架完善 + 知识库管理页面）

阶段 8 = `in_progress`，8A = `completed`，下一步为 **8B RAG 问答页面**。
本轮只实现 UI_SPEC 第 2–5 节的共享框架与知识库管理页面；**未实现** SSE 问答页面、
学业规划页面、导入/结果界面，未修改任何后端业务契约。

### 集中 API 层

- `frontend/src/api/client.ts`：`API_BASE_URL` **只在此处定义一次**（`VITE_API_BASE_URL`，
  缺省 `http://localhost:8000/api`）；支持 GET / JSON POST / multipart POST / DELETE；
  统一解析错误体 `{code, message, details, request_id}`。
- `ApiError` 保留 `kind`（`http` / `network` / `aborted`）、HTTP `status`、机器错误码 `code`、
  服务端 `message`、`requestId`、`details`；`requestIdLabel` 在服务端错误时显示真实请求编号，
  网络 / 取消 / CORS 阻断时明确显示「未获得服务端请求编号」，**前端绝不生成 request_id**。
- multipart 不手动设置 `Content-Type`（浏览器补 boundary）；204 返回 `undefined`；
  不打印请求体、文件正文、路径或密钥；未新增 API Key、未修改 `.env`、未引入远程字体 / 图片 / CDN。
- 新增 `api/documents.ts`、`api/demo.ts`：DTO 字段与 `backend/app/documents/service.py`、
  `backend/app/demo/service.py` 的真实返回逐一对应（未猜测、未补默认值）。

### 共享应用框架

- 路由保持 `/` → `/knowledge`、`/knowledge`、`/chat`、`/planning` 与 404；
  桌面 224px 侧栏 / 64px 顶栏，768–1199px 72px 图标侧栏，<768px 顶部菜单 + 导航抽屉；
  保留「跳到主要内容」与当前导航 `aria-current="page"`。
- **侧栏底部「知识库概况」改为真实数据**：可检索数 / 总数来自 `GET /api/documents`，
  演示数据状态来自 `GET /api/demo/status`；未加载或不可达时显示「正在读取… / 暂不可用」，
  不显示任何伪造数字（移除了「阶段 1 尚未接入」占位）。
- 顶部状态严格来自 `/api/health`；后端不可达时显示固定错误条、提供「重新检测」并禁用写操作；
  Provider / 设备标签只在健康接口真实返回时展示；不显示虚构用户、学校、通知数或统计。
- `ChatView` / `PlanningView` 仍为占位，但已删除失效的阶段编号与
  「planning = unavailable」表述，并在真实浏览器中确认 `/chat`、`/planning` 正文不含 `阶段 N`。

### 通用组件

新增 `AsyncState`、`StatusTag`、`ErrorAlert`、`FileUploader`、`JobProgressPanel`、
`DocumentPreviewDrawer`、`ConfirmDeleteDialog`：显式区分 loading / error / empty / disabled /
partial / success；图标按钮与表单控件具备可见 label 与 `aria-label`；请求期间按钮 loading 并阻止重复提交；
弹窗 / 抽屉关闭后焦点返回触发按钮；所有后端文本按纯文本渲染（有源码级 `v-html` 守卫测试）。

### KnowledgeView（UI_SPEC 第 5 节）

- 页面加载读取 `/api/health`、`/api/documents`、`/api/demo/status`；**不自动 seed**。
- 统计卡直接绑定 `total` / `counts.retrievable` / `counts.processing` / `counts.failed`；
  **未使用 `counts.ready` 顶替 `retrievable`**；空库显示真实 `0`。
- 演示数据：Dataset `state` / Job `status` / Job document `status`、`result` 为**三套独立类型**，
  完整支持 8 种 Dataset 状态到按钮、文案与可用性的映射；`loaded` 只来自 `demo status.loaded`；
  job 终态后重新读取 demo status 并刷新文档列表；`serving_previous_version` 显示当前仍服务旧版本；
  只调用 `POST /api/demo/seed`（未发明 reset / resume / cancel / rebuild 接口）。
- 轮询：同一任务只有一个 timer；终态 / 页面卸载 / 任务切换即停止；按 `poll_after_seconds`
  （缺失固定 2 秒）；网络失败显示「正在重新连接」并做有上限退避，连续 3 次失败后暂停并提供
  「重新连接」；job 404 时重新获取 demo status 再按 `active_job_id` / `last_job_id` 恢复；
  **网络失败不会把 job 写成 failed**。
- 上传：560px 对话框，PDF / DOCX / XLSX，拖拽或选择多文件，客户端预检扩展名与单文件 50MB，
  每文件独立调用 `POST /api/documents`，按 `disposition` 显示已受理 / 文档已存在 / 已连接现有任务 /
  已开始恢复；`DOCUMENT_RETRY_NOT_ALLOWED` 不自动重试并要求更换文件；全部成功才关闭并刷新，
  部分失败保留对话框并允许逐项重试。
- 列表：默认按 `updated_at` 倒序，300ms 文件名搜索防抖，来源 / 类型 / 状态本地筛选（无服务端分页）；
  状态映射严格依据 `status` / `retrievable` / `activation_state`（`retrievable=true` 才是「可检索」，
  ready+candidate 为「已处理，待整体激活」，ready+inactive 为「已退役」）；时间使用本地时区绝对时间；
  存在非终态 upload 文档时每 2 秒查询其 status，全部终态或卸载后停止且不创建重复 timer。
- 详情 / 预览 / 删除：`GET /api/documents/{id}`、`GET /api/documents/{id}/preview`、
  `DELETE /api/documents/{id}`；预览抽屉 520px，PDF 显示页码 / 章节、DOCX 显示标题路径、
  XLSX 显示工作表与行范围；SHA-256 只取详情返回值并只显示前 12 位；预览不执行 HTML / 宏 / 脚本 / 链接；
  demo 与 upload 删除文案不同，确认按钮明确为「删除文档」；`documents` 能力非 ready 或后端不可达时
  上传 / seed / 预览 / 删除全部禁用并解释原因；服务端错误展示真实 `request_id`，网络错误明确说明未获得。

### 真实产品 Bug（本轮发现并修复）

**BUG-8A-01｜图标侧栏下主导航链接没有任何可访问名称**

- 发现方式：真实浏览器在 1024×768 视口渲染 `/knowledge`，
  `backend` 正常；`.ep-nav__link` 的 `aria-label` 为 `null`，
  `.ep-nav__label` 计算样式为 `display: none` 且 `innerText` 为 `""` ⇒ 链接的可访问名称为空。
- 错误假设：以为「图标 + 文字」结构中，只要 CSS 隐藏文字仍会保留可访问名称。
  实际 `display: none` 会把文本节点移出可访问性树。
- 根因：`frontend/src/components/common/AppNav.vue:15-22` 的 `<RouterLink>` 只有图标与
  `.ep-nav__label`，而 `frontend/src/layouts/AppLayout.vue:297`（`@media (max-width: 1199px)`）
  将 `:deep(.ep-nav__label)` 设为 `display: none`，使 768–1199px 图标侧栏下链接失去唯一文本。
- 修复前证据：新增最小失败测试
  `frontend/src/layouts/AppLayout.spec.ts` ›
  「gives every navigation link an accessible name independent of the responsive CSS」
  → `expected [ undefined, undefined, undefined ] to deeply equal [ '知识库管理', 'RAG 问答', '学业规划' ]`，
  `Tests 1 failed | 7 passed (8)`，容器内 `PRE_FIX_EXIT=1`；浏览器实测 `ariaLabel: null`、`visibleText: ""`。
- 最小修复：`AppNav.vue` 的 `<RouterLink>` 增加 `:aria-label="item.label"`（一个属性，未改结构 / 样式 / 路由）。
- 修复后证据：同一测试通过，全量前端 `88 passed`（`TEST_EXIT=0`）；
  浏览器在 390×844 / 1024×768 / 1440×900 三档均读到
  `navLabels = ["知识库管理","RAG 问答","学业规划"]` 且 `aria-current="page"` 恰好 1 个。

### 验收

- `docker compose exec frontend pnpm test` → **13 files / 88 passed，退出码 0**；
  `pnpm build`（`vue-tsc --noEmit` + `vite build`）→ 成功，退出码 0；
  `docker compose exec backend pytest -q` → **全部通过，退出码 0**（前端改动未触及后端契约）；
  `docker compose config --quiet` 退出码 0；`docker compose ps` → backend `Up (healthy)`、frontend `Up`；
  `git diff --check` 退出码 0（仅 LF/CRLF 提示，无空白错误）。
- 真实浏览器只读 smoke（未自动 seed / 上传 / 删除任何真实数据）：
  `/knowledge` 打开正常，统计卡显示真实数据（全部文档 15 / 可检索 0 / 处理中 15 / 失败 0），
  演示数据状态为「空知识库」（`available_documents=15`、`ready=0`、`loaded=false`），
  文档行显示「已处理，待整体激活」与本地时区绝对时间；
  首次并发请求确认为 `health` / `documents` / `demo/status`，并按 `last_job_id` 恢复一次后立即停止轮询。
- 三档视口（同源 iframe 实测，宽度 390 / 1024 / 1440）：
  `documentElement.scrollWidth === clientWidth`（375/1009/1425），**无整页横向溢出**；
  表格在窄屏下由 Element Plus 内部滚动容器提供**局部横向滚动**
  （`scrollWidth=1140 → clientWidth=293`，滚动后「操作」列可进入视口）。
- 键盘：`.ep-skip-link` 为第一个可聚焦元素；所有可见控件 `aria-label` 覆盖为 0 个缺失；
  通过键盘打开预览抽屉后 `Escape` 关闭，焦点返回触发按钮；
  打开删除确认后 `Escape` 关闭，**未产生任何 DELETE 请求**，文档仍为 15 行；
  确认框标题「确认删除该文档？」、demo 文案「仅移除运行时索引，可通过加载演示资料恢复」、
  按钮「取消 / 删除文档」。
- 后端断线：`docker compose stop backend` 后刷新 `/knowledge`，
  顶部固定错误条（「后端服务不可连接…重新检测」）、上传禁用、写操作说明、
  文档区错误「错误码：NETWORK_ERROR / 未获得服务端请求编号」、侧栏「知识库概况暂不可用」，
  页面不崩溃且控制台无未处理 JS 错误（仅浏览器原生网络失败日志）；`start backend` 后恢复
  `Up (healthy)`、`documents=ready`、`planning=ready`，页面回到正常状态。
- 范围外（未做）：Chat SSE、学业规划选择器 / 导入 / 结果、API Key 输入框、
  依赖升级、demo 语料与 ground truth 修改、后端契约修改。

## 阶段 8A 独立回归修复轮（BUG-8A-02 / BUG-8A-03）

阶段 8 仍为 `in_progress`，8A 在本轮修复后仍为 `completed`；**未开始 8B RAG 问答页面**。
本轮只修复 8A 交付中「API 错误被静默吞掉 / 缺少错误码与请求编号」两个缺陷，
未修改后端业务、未配置真实 LLM、未修改任何 `.env`、未调用真实外部 API、
未执行真实 seed / 上传 / 删除，未修改 `PRODUCT_SPEC.md` 或 `UI_SPEC.md`。

### BUG-8A-02｜演示资料 seed 失败被静默吞掉

- 现象：`POST /api/demo/seed` 失败后页面没有任何提示，用户无法得知失败原因，
  也无法重试；`demo.seedError` 已在 store 中保存却从未被渲染。
- 根因：
  1. `frontend/src/views/KnowledgeView.vue:124` 的 `onSeed()` 只处理 `demo.seed()` 的
     `true` 分支（`ElMessage.info`），对 `false` 不做任何展示；`demo.seed()` 在
     `frontend/src/stores/demo.ts:182-184` 把异常保存到 `seedError` 后返回 `false`。
  2. 模板中完全没有绑定 `demo.seedError`（演示资料卡片内只有 `demo.statusError` 的
     `AsyncState` 错误分支）。
- 修复前真实失败证据：新增 3 项组件测试后执行
  `docker compose exec frontend pnpm vitest run src/components/documents/FileUploader.spec.ts src/views/KnowledgeView.spec.ts`
  → `PRE_FIX_EXIT=1`、`Test Files 2 failed (2)`、`Tests 6 failed | 14 passed (20)`。
  其中 seed 相关 3 项原始输出：
  - `expected '演示资料空知识库知识库为空，可加载演示资料或上传自有文档。…' to contain '演示数据集清单不可用'`
  - `expected '演示资料空知识库知识库为空，可加载演示资料或上传自有文档。…' to contain '无法连接后端服务'`
  - `expected '演示资料空知识库知识库为空，可加载演示资料或上传自有文档。…' to contain 'DEMO_PIPELINE_UNAVAILABLE'`
- 最小修复（复用现有 `ErrorAlert.vue`，未复制第二套错误展示逻辑）：
  - `frontend/src/views/KnowledgeView.vue:24` 引入 `ErrorAlert`；
  - `frontend/src/views/KnowledgeView.vue:372-381` 在**演示资料卡片内**渲染
    `demo.seedError`（标题「演示资料加载失败」自动显示服务端 `message`、机器错误码与
    `requestIdLabel`），并提供「重试加载」按钮（`:loading="demo.seeding"` 防止重复提交）；
  - 重试仍只调用现有 `POST /api/demo/seed`（`onSeed()`），未新增 resume / reset / cancel 接口；
  - `frontend/src/views/KnowledgeView.vue:684` 只新增一处间距样式。

### BUG-8A-03｜逐文件上传错误缺少错误码和 request_id

- 现象：上传队列项失败时只显示 `item.message`，服务端机器错误码与真实 `request_id`
  完全不可见，用户与维护者都无法定位。
- 根因：
  1. `frontend/src/components/documents/FileUploader.vue:175-182` 已把 `ApiError`
     保存到 `item.error`；
  2. 但模板 `frontend/src/components/documents/FileUploader.vue:283`（修复前）
     只渲染 `item.message`，`item.error` 从未被渲染。
- 修复前真实失败证据（同一次运行）：
  - `expected 'plan.pdfPDF · 1KB失败解析失败 重试  移除' to contain 'DOCUMENT_PARSE_FAILED'`
  - `expected 'plan.pdfPDF · 1KB失败该文件当前不允许重试，请修正或更换文…' to contain 'DOCUMENT_RETRY_NOT_ALLOWED'`
  - `expected 'plan.pdfPDF · 1KB失败无法连接后端服务（网络错误或跨域被阻…' to contain 'NETWORK_ERROR'`
- 最小修复（单一错误元信息实现，未复制逻辑）：
  - 新增 `frontend/src/domain/apiError.ts`：`apiErrorCodeLabel()`（服务端 `code` 优先，
    网络 → `NETWORK_ERROR`，取消 → `REQUEST_ABORTED`，兜底 `HTTP_{status}`）与
    `apiErrorRequestIdLabel()`（服务端错误显示真实 request ID，网络 / 取消 / CORS 阻断
    明确显示「未获得服务端请求编号」）；
  - `frontend/src/components/common/ErrorAlert.vue:23` 改为复用同一处
    `apiErrorCodeLabel()`（移除组件内重复实现，行为不变）；
  - `frontend/src/components/documents/FileUploader.vue:24` 引入派生函数；
    `:284-286` 在失败队列项内追加紧凑元信息「错误码：<code>」与请求编号标签；
    `:417` 只新增一处样式；
  - 纯客户端扩展名 / 大小预检失败时 `item.error` 仍为 `null`，因此**不渲染**错误码与
    请求编号，不冒充服务端错误；
  - 保留既有语义：`DOCUMENT_RETRY_NOT_ALLOWED` 的用户指引与禁用重试、部分成功保留对话框、
    逐项重试、全部成功才关闭；所有错误内容均为 Vue 文本插值，无 `v-html`，
    不显示堆栈 / 请求体 / 文件正文 / 绝对路径 / API Key。

### 修复后验收

- 定向：`docker compose exec frontend pnpm vitest run src/components/documents/FileUploader.spec.ts src/views/KnowledgeView.spec.ts`
  → `POST_FIX_EXIT=0`、`Test Files 2 passed (2)`、`Tests 20 passed (20)`。
- 前端全量：`docker compose exec frontend pnpm test` → **13 files / 95 passed**，退出码 0
  （上一轮 88，净 +7）。
- 前端构建：`docker compose exec frontend pnpm build` → `vue-tsc --noEmit` + `vite build` 成功，退出码 0。
- 后端全量：`docker compose exec backend pytest -q` → **675 tests（42 文件）全部通过**，退出码 0
  （仅前端改动，后端契约未变）。
- `docker compose config --quiet` 退出码 0；`docker compose ps` → backend `Up (healthy)`、frontend `Up`；
  `git diff --check` 退出码 0（仅 LF/CRLF 提示）。
- `GET /api/health` 仍如实为 `status=degraded`、`documents=ready`、`chat=unconfigured`、
  `planning=ready`、`embedding/reranker/llm.ready=false`，**未为了测试改成虚假 ready**。
- 真实浏览器只读复核（未执行任何 seed / 上传 / 删除）：
  - `/knowledge` 正常渲染 15 行真实文档、统计卡 `全部文档15 / 可检索0 / 处理中15 / 失败0`；
  - 上传对话框中选择不支持的扩展名（`PROJECT_STATUS.md`）→ 队列项只显示
    「不支持的文件类型：仅允许 PDF / DOCX / XLSX」，**不含**「错误码：」与「请求编号」，
    「开始上传」禁用，且网络面板**没有任何发往 `/api` 的 POST**。

### 下一步

8A 修复完成后，下一步才是 **8B RAG 问答页面**（实施前必须读取 `docs/UI_SPEC.md` 第 6 节）。

## 阶段 8B-1 结论（Chat 流协议、API 契约与状态机）

阶段 8 = `in_progress`；8A = `completed`；**8B-1 = `completed`**；**8B-2 尚未开始**。
本轮只交付可独立验证的**协议层与 Pinia 状态层**，未实现最终 Chat 页面布局、
证据侧栏与来源抽屉视觉组件，未开始 8C 学业规划页面。

**未配置或调用任何真实 LLM**：`LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` 保持为空，
未修改任何 `.env`，未下载任何模型，未发出真实 `/api/chat/stream` 请求（后端日志零命中），
未执行 demo seed / 上传 / 删除，未调用真实外部 API。
默认环境仍如实为 `status=degraded`、`documents=ready`、**`chat=unconfigured`**、`planning=ready`；
`chat=unconfigured` 与 `retrievable=0` 是当前真实运行状态，**不是**需要修掉的 Bug。

### 新增文件与核心类型

| 文件 | 内容 |
|---|---|
| `frontend/src/api/chat.ts` | `ChatRequestBody{messages,filters}`、`ChatMessagePayload`、`ChatFilters{major,grade_year,semester,doc_category}`、`ChatCitation`（13 字段）、`ChatDone`、`ChatStreamErrorPayload`、`CHAT_OUTCOMES`、`MAX_CHAT_MESSAGES=10`、`emptyChatFilters()`、`streamChat()` |
| `frontend/src/api/retrieval.ts` | `RetrievalOptions{majors,grade_years,doc_categories,active_dataset_version,demo_available}`、`SourceDetail`（含全部定位字段）、`fetchRetrievalOptions()`、`fetchSource()` |
| `frontend/src/domain/chatStream.ts` | `ChatStreamParser`、`ChatStreamEvent`、`ChatStreamProtocolError`（`code=CHAT_STREAM_PROTOCOL_ERROR`，只带稳定原因，不回显帧内容） |
| `frontend/src/stores/chat.ts` | Chat 状态机（见下） |
| `frontend/src/api/client.ts`（改） | 新增 `postStream()`：同一个 `API_BASE_URL`、同一套错误归一化，成功时**不读正文**；`ApiErrorKind` 增加前端 `protocol` 类型 |

请求体**严格只有** `messages` + `filters`（测试断言 key 集合为 `['filters','messages']`），
不存在 `session_id` / `conversation_id`；`grade_year` 前后端都保持整数 `number`；
filters 的 `null` / 整数 / 字符串原样提交。筛选选项全部来自接口，未硬编码任何取值。

### SSE 分片解析方案

- 使用 `fetch` + `response.body.getReader()` + 流式 `TextDecoder`；**未使用** `EventSource`
  （接口是 POST），并有源码级守卫测试防止回退。
- 解析器按行缓冲：只把**完整行**交给字段处理，残缺尾巴留在缓冲区，
  因此「一个事件跨多个 chunk」「一个 chunk 含多个事件」「data / event 行被任意拆分」都不会错帧。
- `TextDecoder` 以 `{stream:true}` 增量解码，**跨字节边界的中文字符**不会被切成乱码。
- 行终止符支持 `LF`、`CRLF` 与孤立 `CR`；`CRLF` 恰好被拆在两个 chunk 之间时等待下一个 chunk 再判定，
  EOF 处孤立的 `CR` 由 `finish()` 判定为行终止符。
- `:` 开头的 SSE comment（保活）被忽略，`id` / `retry` 等字段忽略。
- 严格只接受 `token` / `citation` / `done` / `error`：未知事件名、空 `data`、非法 JSON、
  结构与契约不符（含 `done.outcome` 非法）、重复终止事件、终止后仍有业务事件
  一律抛 `ChatStreamProtocolError` 并给出稳定 reason。
- `done` / `error` 之后不再接受任何业务事件（后缀 comment 仍可接受）；
  每次流只允许一个终止事件。
- **无终止事件的 EOF** 由调用方依据 `sawTerminal` 判定为「连接中断」，不当作成功。
- 解析器不生成任何事件，也**不会**因为正文里出现 `[1]` 而合成引用。

### Store 状态转换

`useChatStore` 管理 `messages` / `question` / `streamingContent` / `citations`（按 `citation_index`
去重升序）/ `selectedCitationIndex` / `filters` / `options` / `streaming` / `stopped` /
`interrupted` / `outcome` / `streamError` / `requestId`，并以 `AbortController` 管理请求生命周期。

- 同一时间只允许一个活动请求：`streaming` 为真时 `send()` 直接返回，不产生第二次请求；
  流未结束时不会自动重发。
- `token` 只追加文本；`citation` 乱序到达也按 `citation_index` 去重后升序保存，绝不错配。
- 终态：`done` → `outcome ∈ {answered, refused, conflict}` 并记录 `request_id`；
  流内 `error` → 记录错误与事件携带的 `request_id`，**保留**已收到的部分回答与引用。
- 无终止 EOF → `interrupted=true`（不是成功，也不是红色错误）。
- 用户点击停止 → `AbortController.abort()` → `stopped=true`，`streamError` 保持 `null`；
  停止不是错误，且停止后可再次输入；空闲或重复调用 `stop()` 都是空操作。
- 新请求开始前清理上一轮临时流状态（内容 / 引用 / 选中项 / 终态 / 错误 / request_id），
  **不**清空已完成的 `messages`；请求体取最近 10 条消息。
- 组件卸载时可安全调用 `stop()`；不建立服务端会话，也不写入数据库或浏览器存储。

### 新增测试（49 项）

- `frontend/src/domain/chatStream.spec.ts`（17）：标准 token→citation→done；事件跨多 chunk；
  单 chunk 多事件；中文 UTF-8 跨字节；CRLF 与 comment；孤立 CR；
  citation 乱序保序到达；无终止 EOF；截断帧被丢弃；非法 JSON、空 data、未知事件、
  结构不符、非法 outcome、重复终止、终止后事件、终止后 comment。
- `frontend/src/api/chat.spec.ts`（8）：统一端点与请求头、请求体只有 messages+filters、
  filters 原样提交、`emptyChatFilters()`、开流前 JSON 错误保留服务端 `request_id`、
  非统一错误体回退 HTTP 状态码、网络错误不伪造编号、Abort 归类。
- `frontend/src/api/retrieval.spec.ts`（4）：options 全字段且 `grade_years` 为 `number`、
  空选项不造值、source 全字段与定位映射、`SOURCE_NOT_FOUND` 错误码与 `request_id`。
- `frontend/src/stores/chat.spec.ts`（19）：标准流与终态、仅按 token 追加（正文含 `[1]` 也不生成引用）、
  refused 无引用、conflict 保留多引用、乱序引用最终升序、流内 error 保留部分回答与 request_id、
  无终止 EOF 记为中断、协议违规为前端错误码、用户 Abort 为 stopped 而非 error、
  流未结束忽略第二次发送、新请求清理临时状态且保留历史、最近 10 条与 filters 原样、
  开流前 HTTP 错误不自动重试、网络错误不伪造编号、空问题与 stop 幂等、
  options 加载与失败、引用选择与重置、清空会话。
- `frontend/src/tests/stream-guards.spec.ts`（1）：源码级守卫 —— 全仓 `src` 不得使用浏览器原生
  事件源接口，也不得向浏览器控制台输出（即不会把问题、回答或 quote 写入 console）。
  该守卫在实现前即已能检出违规（自检时命中过 1 个文件），证明其有效。

### 验收

- 实现前（模块尚未创建）：`pnpm vitest run` 上述 5 个文件 → **退出码 1**，
  `Test Files 4 failed`（`Failed to resolve import "./chat"` / `"./retrieval"` /
  `"./chatStream"` / `"./chat"`）。
- 实现后定向：同一命令 → **`Tests 49 passed (49)`、`Test Files 5 passed (5)`，退出码 0**。
- `docker compose exec frontend pnpm test` → **18 files / 144 passed**，退出码 0（上一轮 95，净 +49）。
- `docker compose exec frontend pnpm build` → `vue-tsc --noEmit` + `vite build` 成功，退出码 0。
- `docker compose exec backend pytest -q` → **675 tests（42 文件）全部通过**，退出码 0（仅前端改动，后端无回归）。
- `docker compose config --quiet` 退出码 0；`docker compose ps` → backend `Up (healthy)`、frontend `Up`；
  `git diff --check` 退出码 0（仅 LF/CRLF 提示）。
- 数据真实性复核：`GET /api/documents` 计数仍为 `ready=0 / retrievable=0 / processing=15 / failed=0`，
  `GET /api/demo/status` 的 `last_job_id` 未变、`active_job_id=null`，`data/` 无新增 uploads / 模型缓存，
  后端日志**零** `/api/chat/stream` 命中。

### 下一步

**8B-2**：Chat 页面布局、消息样式、引用证据面板与来源抽屉（`GET /api/sources/{chunk_id}`），
实施前必须读取 `docs/UI_SPEC.md` 第 6 节。

## 阶段 8 前置独立修复轮（BUG-8-PRE-01：空知识库启动死锁）

阶段 7 仍为 `completed`，阶段 8 仍为 `not_started`；本轮只修复健康能力的启动死锁，
未实现任何 Vue 页面、未进入 8A、未修改 `PRODUCT_SPEC.md` / `UI_SPEC.md`。

**BUG-8-PRE-01｜`documents` 把「子系统可用性」与「已有可检索文档」当成同一状态**

- 原始要求：UI_SPEC 2.4 规定 `documents` 非 `ready` 时禁用上传、seed、删除与预览；
  UI_SPEC 5.3 规定空库（`state=empty`）必须以「加载演示资料」作为主要操作；
  PRODUCT_SPEC 6.1 的示例中 `documents` 为 `ready` 而 `chat` 为 `unconfigured`。
- 缺陷：`health.py` 用 `documents = "ready" if retrievable else "unavailable"` 判定，
  而 `_retrievable_documents()` 在**查询成功但为 0** 与**查询失败**两种情况下都返回 `0`。
  于是空库（数据库、文档 API、上传与 seed 全部正常）返回 `documents=unavailable`，
  前端严格遵守 UI_SPEC 时会同时禁用上传与 seed —— 用户永远无法添加第一份文档。
- 修复：
  1. `_retrievable_documents()` 返回 `int | None`：`0` = 查询成功但无检索语料，`None` = 查询失败；
  2. 新增 `_documents_capability()`：文档结构与文档查询可正常执行即 `ready`（**空库同样 ready**），
     仅当相关查询确实失败（`None`）或文档表探测抛错时才 `unavailable`；
  3. 可检索数量只用于 `chat`：`0` 或 `None` 均不允许发起 Chat；
  4. `planning` 的阶段 7 语义完全未改动（空库仍为 `ready`）。
- 新增/更新的测试：`backend/tests/test_health.py` 新增
  `test_empty_library_reports_documents_ready`、`test_documents_present_but_none_retrievable_still_ready`、
  `test_documents_ready_is_independent_of_llm_configuration`、
  `test_health_hides_failures_and_marks_documents_unavailable`；
  并更新 `test_health_reports_degraded_without_business_features`、
  `test_chat_health.py`（2 项）、`test_rerank_health.py`（1 项）中随本契约变化的 `documents` 期望值。
- 修复前：`pytest tests/test_health.py tests/test_chat_health.py tests/test_rerank_health.py -q`
  → **7 failed / 15 passed，退出码 1**；修复后 → **22 passed，退出码 0**。
- 真实 HTTP（空/无检索语料的开发卷）：`status=degraded`、
  `capabilities={'documents': 'ready', 'chat': 'unconfigured', 'planning': 'ready'}`；
  `chat` 依据真实 LLM 配置保持 `unconfigured`，未伪造 `ready`。
- 未做：不自动 seed、不向真实数据卷写入测试数据、未让前端特殊放行 `documents=unavailable`、
  未改动规划 / 检索 / 上传 / demo 业务逻辑与任何 Vue 页面。

## 阶段 7C 结论（确定性学业规划 API、真实证据与健康能力）

**阶段 7 已完成**：7A（持久化与纯计算引擎）、7B-1（demo 投影）、7B-2（导入与 options）、
7C（规划 API、真实证据、健康能力）全部验收通过。阶段 8 保持 `not_started`。

### 学业导入的真实证据锚点

学业导入文档只保存 `DocumentBlock`（7B-2），而 `PlanningEvidence` 必须引用真实 chunk。
本轮新增 `app/academic/evidence.py`：

- 复用正式确定性切片组件（`chunk_blocks` + `build_chunk_records` + `compute_chunk_id`），
  **绝不**使用随机 ID；证据切片使用**独立剖面版本** `1.0.0+academic-evidence-v1`，
  因此同一份文件即使同时存在于普通上传通道与学业导入通道，chunk_id 也**不会碰撞**。
- 只写 `document_chunks`：**不建向量、不写 FTS（`fts_rowid` 保持 NULL）、
  不创建 `DocumentPipelineState`、`retrievable` 仍为 `false`**；RAG worker 依旧不认领。
- `source_chunk_id` 回填到 `CourseRecordRow` / `AcademicRuleSet` / `DegreeRuleRow` /
  `DegreeRuleCourse`；映射来自**同一文档**的真实 locator（XLSX 按工作表/行，
  PDF/DOCX 按页/标题，块级覆盖），多候选沿用 7B-1 的稳定排序。
- 导入时在**同一事务**内建立证据并回填；对 7B-2 以前 `source_chunk_id` 为 NULL 的数据，
  规划入口会**幂等补建与回填**（重复执行零新增、chunk ID 不变）。
- 业务字段仍只来自 `DocumentBlock`，绝不从 chunk 正文重新提取。

### `POST /api/academic/plan`

- 请求体**只允许** `record_set_id` 与 `rule_set_id`（`extra="forbid"`，非法 UUID / 缺字段 /
  多余字段一律 422 统一错误体 + `request_id`）；两个 ID 必须由用户**显式选择**，
  后端绝不自动选择第一个、最新版本或默认规则。
- 可见性口径与 `GET /api/academic/options` **共用同一处定义**：demo 必须属于唯一 active 版本、
  `activation_state=active`、`status=ready`、来源有效；upload 必须 `status=ready` 且来源有效。
  不可见 / inactive / candidate / 旧版本 → `ACADEMIC_RECORD_SET_NOT_FOUND` /
  `ACADEMIC_RULE_SET_NOT_FOUND`（404）；来源文档已删除 → `ACADEMIC_SOURCE_INVALID`（409）。
- **唯一计算路径**是 7A 的纯函数 `compute_plan()`：`app/academic/planning.py` 只做
  「ID → 数据库行 → 7A 数据类 → 真实证据」的装配，**不实现**任何学分加减、缺口、去重或
  类别计算；LLM / Embedding / Reranker / 网络均不参与。
- 响应直接是 `PlanningResult`：顶层严格 8 个字段（不重新加入 `major` / `rule_version` /
  `record_set_id` / `rule_set_id`），数字一位小数且非负。

### 证据与冲突

- 每条 `PlanningEvidence` 严格 11 个字段；`chunk_id` 必须对应真实 `DocumentChunk` 行，
  `doc_id` 与 chunk 所属文档一致，`quote` 是该 chunk 的**真实文本**，
  定位字段来自 `chunk.locator`；不返回路径、`source_key`、哈希、向量分数或内部指纹。
- 证据按 `(doc_id, chunk_id)` 稳定排序并去重；所有 `missing_required_courses[*].evidence_chunk_ids`
  与 `conflict_warnings[*].evidence_chunk_ids` 都必须在顶层 `evidence` 中存在。
  跨文档 chunk、失效 chunk、不完整 locator 一律 `ACADEMIC_EVIDENCE_UNAVAILABLE` 安全失败。
- **版本冲突**：以所选 rule set 计算，检查当前同样合法的同专业其它版本，产生
  `DEGREE_PLAN_VERSION_CONFLICT`，证据同时覆盖所选版本与冲突版本的真实来源。
- **课程记录矛盾 / 类别不一致 / 目录外课程 / 未知必修**：继续由 7A 引擎产生既有稳定 code；
  证据取所选记录集合 / 所选规则的真实切片，确保覆盖相关行。
- **时间冲突**：先看所选记录自身 `schedule` 的确定性结构（星期 + 节次或时刻区间，
  同单位且区间真实重叠才算冲突）；对 active demo 再从正式**课表 `DocumentBlock`**
  按星期 / 节次确定性匹配真实重复排课。禁止从 chunk 正文或 LLM 猜测时间；
  证据覆盖冲突双方课程来源；没有足够证据时不产生冲突。

### 健康能力

- `GET /api/health` 的 `capabilities.planning` 改为 `ready`：规划路由已注册、确定性引擎可用、
  学业数据结构可读；**不依赖**库里是否已有可选集合（空库同样 `ready`）。
- 健康检查只做一次轻量结构探测，不运行任何规划计算、不加载数据集、不下载模型、不访问网络；
  `documents` / `chat` / `providers` 的真实语义未改动，整体 `status` 仍如实为 `degraded`。

### 验收

- 新增 `tests/test_academic_plan.py`（23 项）、`tests/test_academic_evidence_chunks.py`（8 项）、
  `tests/test_academic_plan_ground_truth.py`（2 项）：请求契约、可见性与错误码、显式选择、
  Student A/B 与两个规则版本、upload 与跨来源选择、重修/重复/在修/failed、类别优先级与 warning、
  总缺口 0 仍保留 missing、版本冲突双方证据、矛盾证据、时间冲突双方证据（含「不同单位不猜」）、
  证据真实性与严格字段、不泄漏内部字段、字节级稳定、重启一致、无模型/网络依赖、
  证据补建与幂等回填、以及 `ground_truth.jsonl` 的**全部 planning 条目**逐字段验收。
- ground truth 比对口径（已在测试内注明）：数值 / 缺失必修 / 类别缺口逐字段相等；
  oracle 的 `major` / `rule_version` / `admission_year` 不属于 `PlanningResult`，断言**不返回**；
  告警 code 以 oracle 为下界并要求顺序一致 —— oracle 生成于 7A BUG-7A-02 修复之前，
  未列出「正考不及格 + 重修通过」这一真实矛盾，而本轮规格（第七节）要求继续产生该告警，
  因此显式允许该项为**新增**告警，其余任何未预期告警都会失败。oracle 的 `evidence`
  `chunk_id` 生成时全为 `null`，因此改为校验**真实 chunk 关联**而不做逐字节比对。
- `docker compose exec backend pytest -q` → **659 passed**；
  前端 `pnpm test` → 11 passed、`pnpm build` 成功；`docker compose config --quiet`、`ps`
  符合预期；真实 HTTP：`GET /api/health` → `planning=ready`、`GET /api/academic/options` → 200、
  `POST /api/academic/plan` 对未知 ID → 404 + 非空 `request_id`、缺字段/多余字段 → 422。
- 契约更新（**不是**产品 Bug）：7B-2 曾断言「学业导入文档 `source_chunk_id` 为 NULL / 无切片」，
  7C 要求真实证据锚点，因此 `test_source_ids_are_real_and_same_document` 改为断言引用真实切片；
  `planning=unavailable` 与「plan 未实现」的旧断言改随本阶段契约更新。
- 未调用任何真实 API、未使用 LLM 参与数值计算、未下载或加载模型、未启动 GPU Profile。
- **已知遗留**：`frontend/src/views/PlanningView.vue` 仍写着「health 返回 planning=unavailable」
  的占位说明，已因本阶段而失效；按本轮范围（禁止 Vue 页面与阶段 8 功能）未改动，留待阶段 8 一并重写。

### 阶段 7C 独立回归修复轮（BUG-7C-01 / BUG-7C-02）

阶段 7 仍为 `completed`，本轮只修复两个已确认缺陷，未进入阶段 8、未改动任何 Vue 页面，
也未修改 `PRODUCT_SPEC.md` / `UI_SPEC.md` / `demo/ground_truth.jsonl` 来迁就实现。

**BUG-7C-01｜时间冲突范围未限定所选记录、区间边界判定错误**

- 缺陷：① `schedule_document_conflicts()` 扫描 active demo 的**全部**课表冲突，
  与本次显式选择的 record set 无关的冲突也会被附加到结果；
  ② `record_schedule_conflicts()` 处理所有带 `schedule` 的记录，既不限定
  `status=in_progress`，也不按 `semester` 隔离；
  ③ `_overlaps()` 对时钟区间用 `<=`，把首尾相接的 `09:00-10:00` 与 `10:00-11:00`
  误判为重叠。
- 修复：时钟区间改为**半开区间**（`left.start < right.end and right.start < left.end`），
  节次保持**端点包含**的离散区间（共享节次仍算冲突）；时间冲突只取 `status=in_progress`
  且学期可识别的记录，并按归一化学期（`2026-2027-1` 与 `2026-2027 学年第一学期` 归一）
  分组比较；课表冲突只有当某时间段的课程里至少有一门属于「所选记录中**同学期在修**课程」
  时才算相关（证据仍覆盖冲突**双方**来源），文档未声明适用学期时直接跳过而不猜测。
  课表适用学期取自该文档真实的 `DocumentBlock`（`说明项: 适用学期；内容: …`）。
- 新增测试：`backend/tests/test_academic_conflict_scope.py`（11 项）。
- 修复前：`pytest tests/test_academic_conflict_scope.py` + 下述 7C-02 测试 →
  **7 failed / 6 passed，退出码 1**（其中本文件为 6 failed / 5 passed）；
  修复后：与 plan / evidence / ground-truth 一起 **45 passed，退出码 0**。

**BUG-7C-02｜不完整证据定位仍被接受**

- 缺陷：`load_evidence()` 只判断 `locator` 是否为空字典，因此「只有 `block_start`/`block_end`」
  或「XLSX 缺 `sheet_name` / 缺行区间 / 行号非正 / `row_start > row_end`」或
  「PDF/DOCX 既无页码也无章节标题」的定位都会通过，前端无法实际展示与跳转。
- 修复：按文件类型校验定位**可展示性** —— XLSX 必须有非空 `sheet_name` 与
  `1 <= row_start <= row_end` 的正整数区间；PDF/DOCX 至少要有合法正整数 `page_number`
  或非空 `section_title`。不完整即返回 `ACADEMIC_EVIDENCE_UNAVAILABLE`，
  **不补默认页码或默认行号**。
- 新增测试：`test_load_evidence_rejects_non_displayable_locators`（8 组非法定位 + 2 组正例）。
- 修复前：该测试 `Failed: DID NOT RAISE`（退出码 1）；修复后通过。

**本轮验收**

- `docker compose exec backend pytest -q` → **671 passed**（上一轮 659，净 +12）；
  前端 `pnpm test` → 11 passed、`pnpm build` 成功；`docker compose config --quiet`、`ps`
  符合预期；`git diff --check` 无输出；`git status --short` 为空。
- 未破坏的契约已逐项复验：请求体仍严格两个 ID、`PlanningResult` 仍严格 8 字段、
  `PlanningEvidence` 仍严格 11 字段、所有 `evidence_chunk_ids` 均在顶层 evidence 中、
  证据切片仍不建检查点 / 不写 FTS / 不写向量 / `retrievable=false`、
  6 条 planning ground truth 全部通过、`health.planning` 仍为 `ready`、
  7A 数值规则与 `planning.py` 均未引入任何学分算法。

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

### 阶段 7B-2 结论（学业资料导入接口与可选上下文）

阶段 7 仍为 `in_progress`（7A、7B-1、7B-2 已完成，下一步 **7C**）。本轮只完成
`POST /api/academic/records/import`、`POST /api/academic/rules/import` 与
`GET /api/academic/options`；**未实现** `POST /api/academic/plan`，`/api/health` 的
`planning` 仍为 `unavailable`（未改动），也未开始阶段 8。

**接口契约**

- 两个导入接口固定 `multipart/form-data`（`file` 必填、`name` 可选），成功响应严格为
  `{"id": uuid, "status": "ready", "warnings": [...]}`，不含 `source_key`、路径、哈希、
  身份字段或内部诊断；记录只接受 XLSX，规则接受 PDF / DOCX / XLSX。
- `name` 经 `validate_display_name` + `safe_display_name` 双重处理（长度、控制字符、
  路径分隔符、伪装路径、保留设备名一律拒绝）；未提供时只用**安全文件名**或真实提取到的
  专业与版本生成显示名。
- 所有后端生成的 4xx/5xx 仍是统一错误体 `{code, message, details, request_id}`，
  新增稳定错误码：`ACADEMIC_FILE_TYPE_UNSUPPORTED`、`ACADEMIC_PARSE_FAILED`、
  `ACADEMIC_FIELD_MISSING`、`ACADEMIC_VALUE_INVALID`、`ACADEMIC_RULE_CONFLICT`、
  `ACADEMIC_SOURCE_UNAVAILABLE`、`ACADEMIC_PERSIST_FAILED`。

**安全与解析复用（不新写弱化版校验器）**

- 文件安全校验、临时落盘、原子提交与清理全部复用阶段 2B 的正式组件
  （`app.documents.upload` / `app.documents.container`）：原始 `Content-Disposition`
  文件名、扩展名、声明 MIME、文件头、OOXML 容器、`[Content_Types].xml`、ZIP 条目路径、
  压缩炸弹、宏与嵌入对象、外部引用。
- 解析复用 `app.documents.parsing.parse_document`；业务字段**只**从解析出的块（表格结构）
  提取，绝不从 `DocumentChunk` 正文提取。培养方案同时支持**段落形态**（PDF / DOCX：
  `专业名称：…`、`· 专业必修：58.0 学分`、`QM-CS102 高等数学（一） 5.0 公共必修 …`）与
  **表格形态**（XLSX：`课程类别: 专业必修；最低学分: 58.0`、
  `课程代码: QM-CS101；课程名称: …；学分: 4.0；课程类别: 专业必修`）。
- 缺字段、非法学分 / 状态、未知类别、重复但取值不同的类别或课程代码一律**明确失败**；
  只有「完全相同的重复声明 / 重复目录行」才降级为稳定 warning
  （`DUPLICATE_CATEGORY_DECLARATION` / `DUPLICATE_CATALOG_ENTRY`）。不使用 LLM、文件名、
  manifest、`ground_truth`、`facts.py` 或任何硬编码补齐字段。

**事务、幂等与并发**

- 顺序固定为：安全校验 → 解析与投影（全部通过）→ 提交文件 → **单个事务**写入
  Document / DocumentBlock / 集合与子项。任一环节失败都不产生可见集合、不留下部分子项，
  也不留下孤立文件（临时文件丢弃，已提交文件回滚时删除）。
- 幂等以**内容**为准（upload 来源空间 + 文件 SHA-256 + 与显示名无关的内容指纹）：
  同内容重复导入返回同一集合、同一 ID，不新增 Document / record set / course records；
  `name` 只影响首次导入的显示名，**改显示名不能绕过内容幂等**。
- 并发相同导入由 `academic_record_sets` / `academic_rule_sets` 的唯一约束兜底：
  失败事务先 `rollback`，再按内容重新读取既有合法结果并返回同一 ID（有双线程实测）。

**来源与 RAG 隔离**

- 导入建立真实 `Document` 与 `DocumentBlock`；`source_doc_id` 指向真实文档。
  学业导入不建立切片，因此 `source_chunk_id` 一律为 `NULL`（**不伪造** chunk_id）。
- 导入文档 `retrievable=false`、`status=queued`、`current_stage=null`、
  `activation_state=null`，不写任何向量 / FTS 完成标记，也不污染 active demo dataset；
  上传 worker 与通用上传去重都显式跳过学业类别（`course_records` / `degree_plan`），
  因此导入不会让文档进入 RAG 检索语料。
- 来源文档被删除后，集合本身保留（沿用已验证的 CASCADE / SET NULL 语义），
  但不会再出现在 options 中。

**`GET /api/academic/options`（严格对齐 PRODUCT_SPEC 6.4）**

- 顶层只有 `record_sets` 与 `rule_sets`；子项字段不得增删，`updated_at` 为规范 UTC ISO-8601。
- demo 选项必须同时满足 `source_type=demo`、`dataset_version` 等于**唯一** active 指针、
  `activation_state=active`、`status=ready`、来源文档仍有效；inactive / candidate / 旧版本
  demo 一律不返回。upload 选项只要求 `status=ready` 且来源文档有效，**不依赖** demo 指针，
  因此 demo 切换不影响 upload。
- 数组去重并稳定排序（record_sets 按 name / updated_at / id，rule_sets 按 major /
  admission_year / rule_version / id）；无数据返回两个空数组；不返回默认选中项，
  后端不静默选择规则版本，也不泄露 `source_key` / `activation_state` / `dataset_version`。

**BUG-7B2-01（本轮开发中发现并修复）**

- 现象：首次实现把「Document + DocumentBlock + 集合与子项」放进同一次 flush，全部导入
  返回 `ACADEMIC_PERSIST_FAILED`；实测该次 flush 只发出
  `INSERT INTO academic_record_sets`，`documents` 尚未插入，触发
  `FOREIGN KEY constraint failed`。最小复现显示同一 flush 中 `academic_record_sets`
  先于 `documents` 执行，且与注册顺序无关。
- 根因：跨 mapper 的插入顺序不能依赖 unit of work 自行推断（`AcademicRecordSet` 与
  `Document` 之间没有 relationship 边）。
- 修复：`app/academic/imports.py::_persist_import` 在加入父文档后先 `session.flush()`
  落 `Document`，再写块与集合 —— 与既有 `documents/service.register_upload` 的写法一致。
- 证据：修复前 `pytest tests/test_academic_imports.py tests/test_academic_options.py -q`
  → **33 failed / 26 passed，退出码 1**；修复后同一命令全部通过（退出码 0），
  最终两个文件共 **61** 项。

**本轮验收**

- 新增 `tests/test_academic_imports.py`（51 项）与 `tests/test_academic_options.py`（10 项），
  合计 **+61**：PDF / DOCX / XLSX 三种格式的真实解析导入、响应契约、危险文件名、
  MIME / 扩展名 / 签名不一致、ZIP 穿越 / 宏 / 压缩炸弹、超大文件、缺表头、非法状态与学分、
  重复与冲突规则、幂等（同内容 / 换 name）、双线程并发、失败零残留、来源真实性与同文档、
  RAG 隔离、身份列不外泄、options 全量口径与字段严格性、错误体 `request_id`、以及
  阶段 1—7B-1 的全量回归。
- `docker compose exec backend pytest -q` → **620 passed**（7B-1 收尾修复轮为 559）；
  前端 `pnpm test` → 11 passed、`pnpm build` 成功；`docker compose config --quiet`、`ps`
  均符合预期；真实 HTTP 冒烟：`GET /api/academic/options` → `{"record_sets":[],"rule_sets":[]}`、
  `GET /api/health` → `degraded`/`planning=unavailable`、非法文件导入 → 400 + 非空 `request_id`。
- 未调用任何真实 API、未使用 LLM 参与解析或计算、未下载或加载模型、未启动 GPU Profile；
  未向真实开发数据卷写入学业数据（迁移后 6 张学业表仍为 0 行，`PRAGMA foreign_key_check` 为空）。

### 阶段 7B-2 独立回归修复轮（BUG-7B2-02：上传所有权隔离）

阶段 7 仍为 `in_progress`，`/api/health` 的 `planning` 仍为 `unavailable`；**未实现**
`POST /api/academic/plan`，未开始 7C。本轮只修复学业导入隔离误伤普通知识库上传的问题。

**BUG-7B2-02｜使用 doc_category 隔离 academic import 导致普通上传去重和 worker 认领失效**

- **原始需求**：academic import 不得进入 RAG 检索语料；但普通知识库上传（解析 → 切片 →
  向量 → FTS → 可检索）的能力**不得受影响**。
- **AI 错误假设**：把 `degree_plan` / `course_records` 当成「academic import 的所有权标识」，
  用 `Document.doc_category.notin_(ACADEMIC_DOC_CATEGORIES)` 去排除学业导入文档。
- **实际语义**：`doc_category` 是 PRODUCT_SPEC 定义的**业务内容分类**（也是前端过滤字段与
  `/api/retrieval/options` 的返回值），普通上传的培养方案、成绩记录同样属于这些类别；
  它与「上传通道 / 流水线所有权」无关，不能用来区分来源。
- **诚实澄清（避免夸大）**：在 `dd69d89` 上，`POST /api/documents` 建立的文档
  `doc_category` 恒为 `unknown`（实测 `general_upload_has_pipeline_state=True`、
  `doc_category='unknown'`、`find_existing_upload=True`、`worker_claim_candidate=True`），
  因此该缺陷**不会**由当前 HTTP 路径自然触发。但「doc_category 不得影响普通上传的去重、
  认领、重试或索引」这一不变量在代码层面**已被违反**：只要文档带上业务分类，
  上述四项能力立刻失效（可用最小复现证明）。
- **最小复现（修复前）**：`docker compose exec backend pytest
  tests/test_academic_upload_isolation.py -q` → **5 failed / 2 passed，退出码 1**：
  普通 degree_plan 上传无法被 worker 认领；普通 course_records 上传无法被认领；
  同一文件重复上传产生第二个 Document；可重试文档无法被再次认领；
  普通上传与 academic import 的幂等被业务分类破坏。
- **根因（文件 / 函数 / 行号）**：
  - `backend/app/documents/service.py:352` · `find_existing_upload`：`doc_category` 排除
    使普通上传无法按 SHA-256 查回自己 → 重复上传新建 Document + 多余文件、失败重试分支不可达。
  - `backend/app/worker/runner.py:1341` · `Worker._run_upload_document`：同一排除使
    业务分类为学业类别的普通上传**永远不会**被认领，也就永远不会进入 RAG 流水线。
  - `backend/app/constants.py:18`：把业务分类打包成「学业导入所有权」常量。
- **最小修复（改用 DocumentPipelineState 判定所有权，不做任何字符串前缀/路径/文件名猜测）**：
  1. `find_existing_upload` 改为 `JOIN document_pipeline_state`：只在**具有检查点的普通上传
     文档**中按 SHA-256 查找；academic import 文档没有检查点，因此天然不被复用。
  2. `Worker._run_upload_document` 同样 `JOIN document_pipeline_state`：只认领普通上传文档；
     academic import 文档即使 `status=queued` 也不会被认领。
  3. 删除不再需要的 `constants.ACADEMIC_DOC_CATEGORIES`（保留
     `DOC_CATEGORY_COURSE_RECORDS` / `DOC_CATEGORY_DEGREE_PLAN` 并在注释中明确其业务分类语义）。
  4. 普通上传继续在 `register_upload` 中调用 `ensure_pipeline_state`；academic import 仍**不创建**
     检查点。demo 文档处理、academic options 过滤口径、RAG 资格 SQL 全部未改动。
- **新增回归测试**：`backend/tests/test_academic_upload_isolation.py`（7 项）——
  普通 degree_plan / course_records 上传仍可被 worker 认领；业务分类不影响去重；
  可重试文档仍可被再次认领；无检查点的学业导入文档不被认领且 `retrievable=false`；
  同一文件的普通上传与 academic import 保持独立所有权、各自幂等、互不删除文件。
- **修复后**：`docker compose exec backend pytest tests/test_academic_upload_isolation.py -q`
  → **7 passed，退出码 0**；定向回归
  （`test_documents_api.py` + `test_upload_security.py` + `test_academic_imports.py`
  + `test_academic_options.py` + 本轮新增）→ **125 passed，退出码 0**；
  `docker compose exec backend pytest -q` → **627 passed**（上一轮 620，+7）；
  前端 `pnpm test` → 11 passed、`pnpm build` 成功；`docker compose config --quiet`、`ps`、
  `GET /api/health`（`degraded` / `planning=unavailable`）、真实数据卷
  `PRAGMA foreign_key_check`（空）全部符合预期。

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
