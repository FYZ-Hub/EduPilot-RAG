import {
  API_BASE,
  BROKEN_FIXTURE,
  applicationIssues,
  corpusFile,
  documentRow,
  expect,
  test,
  waitForJobTerminal,
} from './fixtures'

/**
 * UI_SPEC 11.2 的 8 个端到端场景（真实 frontend + 真实 backend + SQLite + Chroma + FTS）。
 *
 * 唯一使用 Playwright ``route`` 的地方是场景 7 的故障注入；其余成功路径全部走真实接口。
 * 数据卷是隔离的 ./.tmp/e2e/current，默认 ./data 不参与本套件。
 */
test.describe.configure({ mode: 'serial' })

const RECORD_FILE = '14-课程记录-匿名学生B.xlsx'
const RULE_FILE = '02-培养方案-计算机科学与技术-2026修订版.pdf'
const PDF_FILE = '01-培养方案-计算机科学与技术-2025版.pdf'
const DOCX_FILE = '03-课程大纲-QM-CS201-数据结构.docx'
const XLSX_FILE = '12-课表-计算机科学与技术-2026-2027-2.xlsx'

test('场景1 空知识库 → 加载演示资料 → 刷新恢复 → 完成', async ({ page, issues }) => {
  await page.goto('/knowledge')
  await expect(page.getByRole('heading', { name: '知识库管理' })).toBeVisible()

  // 初始为空库：统计卡必须来自真实接口
  await expect(page.locator('.ep-stat').filter({ hasText: '全部文档' }).locator('.ep-stat__value')).toHaveText('0')
  await expect(page.locator('.ep-stat').filter({ hasText: '可检索' }).locator('.ep-stat__value')).toHaveText('0')
  await expect(page.locator('.ep-demo__counts')).toContainText('15')

  await page.getByRole('button', { name: '加载演示资料' }).click()

  // 任务被受理后立即刷新，验证刷新后仍能恢复任务状态
  await expect(page.getByRole('heading', { name: '演示资料加载进度' })).toBeVisible()
  const jobUrl = await page.request.get(`${API_BASE}/demo/status`)
  expect(jobUrl.ok()).toBe(true)
  await page.reload()
  await expect(page.getByRole('heading', { name: '演示资料加载进度' })).toBeVisible()

  await waitForJobTerminal(page)

  // 15 份版本化演示文档全部可见且可检索
  await expect(page.locator('.ep-table-scroll tbody tr')).toHaveCount(15)
  await expect(page.locator('.ep-stat').filter({ hasText: '可检索' }).locator('.ep-stat__value')).toHaveText('15')
  await expect(page.locator('.ep-stat').filter({ hasText: '处理中' }).locator('.ep-stat__value')).toHaveText('0')
  await expect(page.locator('.ep-table-scroll .ep-status-tag').first()).toHaveText('可检索')
  await expect(page.locator('.ep-table-scroll .ep-status-tag').filter({ hasText: '可检索' })).toHaveCount(15)

  // 页面不写死任何数据库内部 ID：行内文本只包含文件名等业务字段
  const firstRowText = await page.locator('.ep-table-scroll tbody tr').first().innerText()
  expect(firstRowText).not.toMatch(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}/)
  expect(applicationIssues(issues)).toEqual([])
})

test('场景2 重复加载幂等：全部 skipped 且行数不增加', async ({ page, issues }) => {
  await page.goto('/knowledge')
  await expect(page.locator('.ep-table-scroll tbody tr')).toHaveCount(15)

  // 已加载状态下按钮文案为“重新校验”，走同一个 seed 接口
  await page.getByRole('button', { name: '重新校验' }).click()
  await waitForJobTerminal(page)

  const skipped = page.locator('.ep-job__counts div').filter({ hasText: '已跳过' }).locator('dd')
  await expect(skipped).toHaveText('15')
  await expect(page.locator('.ep-job__counts div').filter({ hasText: '已导入' }).locator('dd')).toHaveText('0')
  await expect(page.locator('.ep-job__counts div').filter({ hasText: '失败' }).locator('dd')).toHaveText('0')

  // 文档行数与可检索数不增加，也没有重复行
  await expect(page.locator('.ep-table-scroll tbody tr')).toHaveCount(15)
  await expect(page.locator('.ep-stat').filter({ hasText: '全部文档' }).locator('.ep-stat__value')).toHaveText('15')
  await expect(page.locator('.ep-table-scroll .ep-doc-name')).toHaveCount(15)
  expect(applicationIssues(issues)).toEqual([])
})

test('场景3 上传 PDF / DOCX / XLSX 并查看不同定位预览', async ({ page, issues }) => {
  await page.goto('/knowledge')

  const uploads = [PDF_FILE, DOCX_FILE, XLSX_FILE]
  await page.getByRole('button', { name: '上传文件' }).click()
  await page.getByLabel('选择要上传的文件').setInputFiles(uploads.map((name) => corpusFile(name)))
  await page.getByRole('button', { name: '开始上传' }).click()

  // 上传通过正常 documents 接口进入 RAG 流水线，等待三份都成为可检索
  for (const name of uploads) {
    await expect(documentRow(page, name, '用户上传').locator('.ep-status-tag')).toHaveText('可检索', {
      timeout: 280_000,
    })
  }
  await expect(page.getByRole('button', { name: '开始上传' })).toBeHidden()

  // PDF：页码 / 章节
  await documentRow(page, PDF_FILE, '用户上传').getByRole('button', { name: `预览 ${PDF_FILE}` }).click()
  await expect(page.locator('.ep-preview__name')).toHaveText(PDF_FILE)
  await expect(page.locator('.ep-preview__locators .el-tag').filter({ hasText: '页码：' }).first()).toBeVisible()
  await expect(page.locator('.ep-preview__locators .el-tag').filter({ hasText: '章节：' }).first()).toBeVisible()
  await page.keyboard.press('Escape')

  // DOCX：标题路径
  await documentRow(page, DOCX_FILE, '用户上传').getByRole('button', { name: `预览 ${DOCX_FILE}` }).click()
  await expect(page.locator('.ep-preview__name')).toHaveText(DOCX_FILE)
  await expect(page.locator('.ep-preview__locators .el-tag').filter({ hasText: '标题路径：' }).first()).toBeVisible()
  await page.keyboard.press('Escape')

  // XLSX：工作表 / 行范围
  await documentRow(page, XLSX_FILE, '用户上传').getByRole('button', { name: `预览 ${XLSX_FILE}` }).click()
  await expect(page.locator('.ep-preview__name')).toHaveText(XLSX_FILE)
  await expect(page.locator('.ep-preview__locators .el-tag').filter({ hasText: '工作表：' }).first()).toBeVisible()
  await expect(page.locator('.ep-preview__locators .el-tag').filter({ hasText: '行范围：' }).first()).toBeVisible()

  // 不断言宿主机绝对路径
  const drawerText = await page.locator('.ep-preview__meta').innerText()
  expect(drawerText).not.toMatch(/[A-Za-z]:\\|\/app\/data|\/Users\//)
  await page.keyboard.press('Escape')

  await expect(page.locator('.ep-table-scroll tbody tr')).toHaveCount(18)
  expect(applicationIssues(issues)).toEqual([])
})

test('场景4 正常问答 → 流式回答 → 引用 → 原文', async ({ page, issues }) => {
  const sourceRequests: string[] = []
  page.on('request', (request) => {
    if (request.url().includes('/api/sources/')) {
      sourceRequests.push(request.url())
    }
  })

  await page.goto('/chat')
  const composer = page.getByLabel('输入问题')
  await expect(composer).toBeEnabled()

  // 流式链路必须真的建立过：POST /api/chat/stream（不使用 route.fulfill 伪造）
  const streamRequests: string[] = []
  page.on('request', (request) => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/chat/stream') {
      streamRequests.push(request.url())
    }
  })

  await composer.fill('课程代码 QM-CS201 的学分是多少？')
  await page.getByRole('button', { name: '发送' }).click()

  // 最终 answered：出现“复制回答”，且没有拒答 / 中断提示
  await expect(page.getByRole('button', { name: '复制回答' })).toBeVisible({ timeout: 60_000 })
  await expect(page.getByText('当前知识库没有足够依据')).toBeHidden()
  expect(streamRequests).toHaveLength(1)

  // 引用按钮与证据卡
  const citation = page.getByRole('button', { name: /^查看引用 \d+$/ }).first()
  await expect(citation).toBeVisible()
  const citationCount = await page.getByRole('button', { name: /^查看引用 \d+$/ }).count()
  expect(citationCount).toBeGreaterThan(0)
  expect(sourceRequests).toEqual([])

  await citation.click()
  await expect(page.locator('.ep-evidence-card.is-selected')).toHaveCount(1)

  // 只有点击“查看原文”后才请求 /api/sources/{chunk_id}
  await page.locator('.ep-evidence-card').first().getByRole('button', { name: '查看原文' }).click()
  await expect(page.locator('.ep-source__file')).toBeVisible({ timeout: 30_000 })
  expect(sourceRequests).toHaveLength(1)
  await expect(page.locator('.ep-source__locator .el-tag').first()).toBeVisible()
  await expect(page.locator('.ep-source__text')).not.toBeEmpty()

  expect(applicationIssues(issues)).toEqual([])
})

test('场景5 无依据拒答：真实过滤器范围 + 真实 backend 拒答', async ({ page, issues }) => {
  await page.goto('/chat')
  await expect(page.getByLabel('输入问题')).toBeEnabled()

  // 确定性无证据范围：培养方案类文档（doc_category=degree_plan）本身不带学期，
  // 因此「doc_category=degree_plan AND semester=<任意真实值>」的 AND 组合必定为空。
  // 选项全部来自真实 /api/retrieval/options；缺失即显式失败并输出选项，绝不静默回退。
  const optionsResponse = await page.request.get(`${API_BASE}/retrieval/options`)
  expect(optionsResponse.ok()).toBe(true)
  const retrievalOptions = (await optionsResponse.json()) as {
    doc_categories?: unknown[]
    semesters?: string[]
  }
  const dump = JSON.stringify(retrievalOptions)
  // 契约：semesters 是 string[]；doc_categories 兼容字符串数组或 { value } 对象数组
  const categories = (retrievalOptions.doc_categories ?? []).map((item) =>
    typeof item === 'string' ? item : ((item as { value: string }).value ?? ''),
  )
  const semesters = retrievalOptions.semesters ?? []
  expect(categories, `真实 options 缺少 degree_plan：${dump}`).toContain('degree_plan')
  expect(semesters.length, `真实 options 缺少学期：${dump}`).toBeGreaterThan(0)

  await page.getByText('检索范围', { exact: true }).click()
  await expect(page.getByLabel('文档类型')).toBeVisible()
  await page.getByLabel('文档类型').selectOption('degree_plan')
  await page.getByLabel('学期').selectOption(semesters[0])

  // 该范围内没有可命中的资料：真实 backend + Fake Provider 给出 refused / no_evidence
  await page.getByLabel('输入问题').fill('量子纠缠补贴的报销标准是多少？')
  await page.getByRole('button', { name: '发送' }).click()

  // 拒答消息仍包含说明正文，因此按 UI_SPEC「done 后显示复制回答与检索详情」，
  // 该条助手消息内应同时存在“复制回答”和“检索详情”，但不得有引用与证据。
  const assistant = page.locator('.ep-msg--assistant').last()
  await expect(assistant.getByText('当前知识库没有足够依据')).toBeVisible({ timeout: 60_000 })
  await expect(assistant.getByRole('button', { name: '复制回答' })).toBeVisible()
  await expect(assistant.getByRole('button', { name: /检索详情/ })).toBeVisible()
  await expect(assistant.getByRole('button', { name: /^查看引用 \d+$/ })).toHaveCount(0)
  await expect(assistant.locator('.ep-evidence-card')).toHaveCount(0)
  await expect(page.locator('.ep-evidence-card')).toHaveCount(0)
  await expect(page.locator('.ep-evidence__empty')).toContainText('点击回答中的引用查看原文')

  // 真实协议取证：必须通过同一条助手消息的“检索详情”读取服务端 outcome / reason_code，
  // 不得根据文案推断；本场景不做任何请求拦截。
  await assistant.getByRole('button', { name: /检索详情/ }).click()
  const details = page.getByRole('dialog')
  await expect(details).toBeVisible()
  await expect(details).toContainText('refused')
  await expect(details.locator('.ep-details__reason')).toHaveText('no_evidence')
  await page.keyboard.press('Escape')
  await expect(details).toBeHidden()

  expect(applicationIssues(issues)).toEqual([])
})

test('场景6 导入课程记录与培养规则 → 计算 → 结果与证据', async ({ page, issues }) => {
  await page.goto('/planning')
  await expect(page.getByRole('heading', { name: '学业规划' })).toBeVisible()
  await expect(page.getByText('结果由确定性规则引擎计算；AI 仅负责解释')).toBeVisible()

  // 课程记录（academic 端点，绝不走普通上传）
  await page.getByRole('button', { name: '导入课程记录' }).click()
  await page.getByLabel(/选择要导入的课程记录文件/).setInputFiles(corpusFile(RECORD_FILE))
  const [recordResponse] = await Promise.all([
    page.waitForResponse(
      (response) =>
        response.url().includes('/api/academic/records/import') && response.request().method() === 'POST',
    ),
    page.getByRole('button', { name: '开始导入' }).click(),
  ])
  expect(recordResponse.status()).toBe(200)
  const recordSetId = ((await recordResponse.json()) as { id: string }).id
  await expect(page.getByRole('button', { name: '开始导入' })).toBeHidden()

  // 培养规则
  await page.getByRole('button', { name: '导入培养规则' }).click()
  await page.getByLabel(/选择要导入的培养方案规则文件/).setInputFiles(corpusFile(RULE_FILE))
  const [ruleResponse] = await Promise.all([
    page.waitForResponse(
      (response) =>
        response.url().includes('/api/academic/rules/import') && response.request().method() === 'POST',
    ),
    page.getByRole('button', { name: '开始导入' }).click(),
  ])
  expect(ruleResponse.status()).toBe(200)
  const ruleSetId = ((await ruleResponse.json()) as { id: string }).id
  await expect(page.getByRole('button', { name: '开始导入' })).toBeHidden()

  // 明确选择刚导入的两个集合（用稳定 ID 定位，避免 getByLabel 模糊命中多个元素）
  const recordSelect = page.locator('select#ep-record-set')
  const ruleSelect = page.locator('select#ep-rule-set')
  await recordSelect.selectOption(recordSetId)
  await ruleSelect.selectOption(ruleSetId)
  await expect(recordSelect).toHaveValue(recordSetId)
  await expect(ruleSelect).toHaveValue(ruleSetId)

  const [planResponse] = await Promise.all([
    page.waitForResponse(
      (response) => response.url().endsWith('/api/academic/plan') && response.request().method() === 'POST',
    ),
    page.getByRole('button', { name: '开始计算' }).click(),
  ])
  expect(planResponse.status()).toBe(200)
  const plan = (await planResponse.json()) as {
    required_credits: number
    completed_credits: number
    in_progress_credits: number
    remaining_credits: number
    missing_required_courses: unknown[]
    category_gaps: unknown[]
    conflict_warnings: unknown[]
    evidence: { chunk_id: string }[]
  }

  // 四个数字严格等于服务端返回
  const expected: Array<[string, number]> = [
    ['required', plan.required_credits],
    ['completed', plan.completed_credits],
    ['in_progress', plan.in_progress_credits],
    ['remaining', plan.remaining_credits],
  ]
  for (const [key, value] of expected) {
    await expect(page.locator(`[data-credit="${key}"] .ep-credit-card__value`)).toHaveText(value.toFixed(1))
  }
  await expect(page.getByRole('progressbar', { name: '总学分完成情况' })).toBeVisible()

  // 结果来源显示的是结果所属的集合，而不是当前选择
  await expect(page.locator('.ep-planning__result-context')).toContainText('课程记录')
  await expect(page.locator('.ep-planning__result-invalid')).toHaveCount(0)

  // 类别缺口 / 缺失必修课 / 冲突：与服务端结构一致（空数组展示空态，不虚构）
  if (plan.category_gaps.length > 0) {
    await expect(page.locator('.ep-gap-item')).toHaveCount(plan.category_gaps.length)
  } else {
    await expect(page.locator('.ep-gap-list__empty')).toContainText('没有类别学分缺口')
  }
  if (plan.missing_required_courses.length > 0) {
    await expect(page.locator('.ep-missing__row')).toHaveCount(plan.missing_required_courses.length)
  } else {
    await expect(page.locator('.ep-missing__empty')).toContainText('已满足当前规则中的必修课要求')
  }
  if (plan.conflict_warnings.length > 0) {
    await expect(page.locator('.ep-conflict-item')).toHaveCount(plan.conflict_warnings.length)
  } else {
    await expect(page.locator('.ep-conflicts__ok')).toContainText('未发现规则冲突')
  }

  // 桌面：证据面板常驻
  await expect(page.locator('.ep-planning__aside')).toBeVisible()
  await expect(page.locator('.ep-planning__aside .ep-planning-evidence')).toBeVisible()
  expect(plan.evidence.length).toBeGreaterThan(0)
  await expect(page.locator('.ep-evidence-card')).toHaveCount(plan.evidence.length)

  // 窄屏：点击证据芯片后抽屉首次打开即高亮并滚动到目标
  await page.setViewportSize({ width: 1024, height: 768 })
  await expect(page.locator('.ep-planning__aside')).toBeHidden()
  const chip = page.locator('.ep-evidence-chip').first()
  await expect(chip).toBeVisible()
  const focusedChunkId = await chip.getAttribute('data-chunk-id')
  await chip.click()
  const drawer = page.getByRole('dialog')
  await expect(drawer).toBeVisible()
  const focusedCard = page.locator(`.ep-evidence-card.is-focused[data-chunk-id="${focusedChunkId}"]`)
  await expect(focusedCard).toBeVisible()

  // 目标卡片进入可视滚动区域
  const box = await focusedCard.boundingBox()
  const viewport = page.viewportSize()
  expect(box).not.toBeNull()
  expect(viewport).not.toBeNull()
  expect(box!.y).toBeGreaterThanOrEqual(0)
  expect(box!.y).toBeLessThan(viewport!.height)

  // 查看原文：真实 locator
  await focusedCard.getByRole('button', { name: '查看原文' }).click()
  await expect(page.locator('.ep-source__file')).toBeVisible({ timeout: 30_000 })
  await expect(page.locator('.ep-source__locator .el-tag').first()).toBeVisible()
  expect(applicationIssues(issues)).toEqual([])
})

test('场景7 离线 / 超时 / 单文件失败：不崩溃且提供恢复操作', async ({ page, issues }) => {
  // ① health 网络失败：不崩溃、写操作禁用、提供重新检测
  //    只拦截 GET /api/health，绝不误伤 documents / options / demo / sources
  await page.route('**/api/health', (route) => {
    const request = route.request()
    const isHealth =
      request.method() === 'GET' && new URL(request.url()).pathname === '/api/health'
    return isHealth ? route.abort() : route.continue()
  })
  const writeBlockedNotice = page.locator('.ep-notice__text')

  await page.goto('/knowledge')
  await expect(writeBlockedNotice).toHaveText('后端服务不可连接，写操作已禁用，只读页面仍可使用。')
  await expect(page.getByRole('button', { name: /上传文件已禁用/ })).toBeDisabled()
  await expect(page.getByRole('button', { name: /加载演示资料已禁用|重新校验/ })).toBeVisible()

  await page.unroute('**/api/health')
  await page.getByRole('button', { name: '重新检测' }).first().click()
  await expect(writeBlockedNotice).toBeHidden()
  await expect(page.getByRole('button', { name: '上传文件' })).toBeEnabled()

  // ② chat 流返回稳定 MODEL_TIMEOUT 契约（网络模拟，仅用于故障注入）
  //    只拦截 POST /api/chat/stream
  await page.route('**/api/chat/stream', (route) => {
    const request = route.request()
    const isStream =
      request.method() === 'POST' && new URL(request.url()).pathname === '/api/chat/stream'
    if (!isStream) {
      return route.continue()
    }
    return route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({
        code: 'MODEL_TIMEOUT',
        message: '模型响应超时',
        details: {},
        request_id: 'req-e2e-timeout',
      }),
    })
  })
  await page.goto('/chat')
  await expect(page.getByLabel('输入问题')).toBeEnabled()
  await page.getByLabel('输入问题').fill('课程代码 QM-CS201 的学分是多少？')
  await page.getByRole('button', { name: '发送' }).click()

  const errorAlert = page.locator('.ep-error-alert')
  await expect(errorAlert).toBeVisible()
  await expect(errorAlert).toContainText('MODEL_TIMEOUT')
  await expect(errorAlert).toContainText('请求编号：req-e2e-timeout')
  await expect(page.getByRole('button', { name: '重试' })).toBeVisible()
  await page.unroute('**/api/chat/stream')

  // ③ 上传：一份合法 + 一份损坏 → 部分失败保留对话框与真实错误
  await page.goto('/knowledge')
  await page.getByRole('button', { name: '上传文件' }).click()
  await page
    .getByLabel('选择要上传的文件')
    .setInputFiles([corpusFile('07-制度-补考重修与学分认定办法.docx'), BROKEN_FIXTURE])

  // 只统计本场景「上传窗口」内新增的 console.error，此前注入的网络日志不计入本段
  const uploadIssueStart = issues.length
  const [rejectedUpload] = await Promise.all([
    page.waitForResponse(
      (response) =>
        response.request().method() === 'POST' &&
        new URL(response.url()).pathname === '/api/documents' &&
        response.status() === 400,
    ),
    page.getByRole('button', { name: '开始上传' }).click(),
  ])
  const rejectedBody = (await rejectedUpload.json()) as { code: string; request_id: string }
  expect(rejectedBody.code).toBe('DOCUMENT_CONTENT_TYPE_MISMATCH')
  expect(rejectedBody.request_id).not.toBe('')

  await expect(page.locator('.ep-uploader__partial')).toBeVisible({ timeout: 120_000 })
  const failedItem = page.locator('.ep-uploader__item').filter({ hasText: 'broken-upload.pdf' })
  await expect(failedItem).toBeVisible()
  await expect(failedItem).toContainText('错误码：')
  await expect(failedItem).not.toContainText('堆栈')
  await expect(failedItem).not.toContainText('/app/data')
  await expect(page.getByRole('button', { name: '开始上传' })).toBeVisible()

  const bodyText = await page.locator('body').innerText()
  expect(bodyText).not.toMatch(/Traceback|at Object\.|sk-[A-Za-z0-9]{8}/)

  // 上传窗口内新增的 console.error 必须恰好是这一条 400 原生网络日志
  const uploadConsoleErrors = issues
    .slice(uploadIssueStart)
    .filter((issue) => issue.kind === 'console')
    .map((issue) => issue.text)
  expect(uploadConsoleErrors).toEqual([
    'Failed to load resource: the server responded with a status of 400 (Bad Request)',
  ])

  // 只允许本场景「主动注入窗口」内产生的浏览器原生网络日志，且必须锚定到行首行尾。
  // MODEL_TIMEOUT 是页面业务错误码，绝不作为忽略 console.error 的依据。
  const injectedPatterns = [
    /^net::ERR_FAILED$/,
    /^Failed to load resource: net::ERR_FAILED$/,
    /^Failed to load resource: the server responded with a status of 503 \(Service Unavailable\)$/,
    /^Failed to load resource: the server responded with a status of 400 \(Bad Request\)$/,
  ]
  const consoleIssues = issues.filter((issue) => issue.kind === 'console')
  const allowed = consoleIssues.filter((issue) =>
    injectedPatterns.some((pattern) => pattern.test(issue.text)),
  )
  await test.info().attach('场景7 原始 console.error', {
    body: JSON.stringify(
      {
        total: consoleIssues.length,
        allowedCount: allowed.length,
        allowedTexts: allowed.map((issue) => issue.text),
        allTexts: consoleIssues.map((issue) => issue.text),
        pageErrors: issues.filter((issue) => issue.kind === 'pageerror').map((issue) => issue.text),
      },
      null,
      2,
    ),
    contentType: 'application/json',
  })
  expect(applicationIssues(issues, injectedPatterns)).toEqual([])
})

test('场景8 三档视口、真实 bounding box、键盘与 200% 缩放', async ({ page, issues }) => {
  const viewports = [
    { width: 390, height: 844, mobile: true },
    { width: 1024, height: 768, mobile: false },
    { width: 1440, height: 900, mobile: false },
  ]

  // 真实测量数据（不做任何编造），最终以 JSON 附件落盘到 test-results
  const layout: Record<string, unknown>[] = []

  for (const size of viewports) {
    await page.setViewportSize({ width: size.width, height: size.height })

    for (const path of ['/knowledge', '/chat', '/planning']) {
      await page.goto(path)
      await expect(page.locator('.ep-main')).toBeVisible()

      // 无整页横向溢出
      const overflow = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      }))
      expect(overflow.scrollWidth, `${path} @ ${size.width}`).toBeLessThanOrEqual(overflow.clientWidth)
      layout.push({ kind: 'viewport', path, width: size.width, height: size.height, ...overflow })

      // 第一个可聚焦元素是“跳到主要内容”，且当前导航有 aria-current=page
      await page.keyboard.press('Tab')
      expect(await page.evaluate(() => document.activeElement?.className ?? '')).toContain('ep-skip-link')
      const currentNav = page.locator('.ep-nav a[aria-current="page"]')
      await expect(currentNav).toHaveCount(1)
    }
  }

  // 桌面：引用操作真实 bounding box ≥ 40×40
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/chat')
  await page.getByLabel('输入问题').fill('课程代码 QM-CS201 的学分是多少？')
  await page.getByRole('button', { name: '发送' }).click()
  await expect(page.getByRole('button', { name: '复制回答' })).toBeVisible({ timeout: 60_000 })

  const selectBox = await page.locator('.ep-evidence-card__select').first().boundingBox()
  const openBox = await page.locator('.ep-evidence-card__open').first().boundingBox()
  expect(selectBox).not.toBeNull()
  expect(openBox).not.toBeNull()
  expect(selectBox!.width).toBeGreaterThanOrEqual(40)
  expect(selectBox!.height).toBeGreaterThanOrEqual(40)
  expect(openBox!.width).toBeGreaterThanOrEqual(40)
  expect(openBox!.height).toBeGreaterThanOrEqual(40)
  layout.push({ kind: 'desktop-target', widget: 'ep-evidence-card__select', box: selectBox })
  layout.push({ kind: 'desktop-target', widget: 'ep-evidence-card__open', box: openBox })

  // 键盘：聚焦引用并回车打开原文抽屉，关闭后焦点返回触发按钮
  await page.locator('.ep-evidence-card__select').first().click()
  const openButton = page.locator('.ep-evidence-card__open').first()
  await openButton.focus()
  await page.keyboard.press('Enter')
  await expect(page.locator('.ep-source__file')).toBeVisible({ timeout: 30_000 })
  await page.keyboard.press('Escape')
  await expect(page.locator('.ep-source__file')).toBeHidden()
  const focusReturned = await page.evaluate(() =>
    (document.activeElement?.className ?? '').includes('ep-evidence-card__open'),
  )
  expect(focusReturned).toBe(true)

  // 键盘：Space 同样可以激活“查看原文”，Escape 关闭后焦点再次返回触发按钮
  await openButton.focus()
  await page.keyboard.press('Space')
  await expect(page.locator('.ep-source__file')).toBeVisible({ timeout: 30_000 })
  await page.keyboard.press('Escape')
  await expect(page.locator('.ep-source__file')).toBeHidden()
  expect(
    await page.evaluate(() =>
      (document.activeElement?.className ?? '').includes('ep-evidence-card__open'),
    ),
  ).toBe(true)

  // 移动端：先真实交互（滚动到证据卡）再测量，不得对尚未进入布局的元素取值
  await page.setViewportSize({ width: 390, height: 844 })
  const mobileCard = page.locator('.ep-evidence-card').first()
  await mobileCard.scrollIntoViewIfNeeded()
  await expect(mobileCard).toBeVisible()
  const mobileSelect = await mobileCard.locator('.ep-evidence-card__select').boundingBox()
  const mobileOpen = await mobileCard.locator('.ep-evidence-card__open').boundingBox()
  expect(mobileSelect).not.toBeNull()
  expect(mobileOpen).not.toBeNull()
  expect(mobileSelect!.height).toBeGreaterThanOrEqual(44)
  expect(mobileOpen!.height).toBeGreaterThanOrEqual(44)
  layout.push({ kind: 'mobile-target', widget: 'ep-evidence-card__select', box: mobileSelect })
  layout.push({ kind: 'mobile-target', widget: 'ep-evidence-card__open', box: mobileOpen })

  // 200% 等价验证：把 1440×900 的有效 CSS viewport 缩小为 720×450
  // （等价布局验证；不是操作浏览器菜单缩放，也不是 CDP pageScaleFactor）
  await page.setViewportSize({ width: 720, height: 450 })
  await page.goto('/chat')
  const zoomedOverflow = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }))
  expect(zoomedOverflow.scrollWidth).toBeLessThanOrEqual(zoomedOverflow.clientWidth)
  await expect(page.locator('.ep-composer__input')).toBeVisible()
  layout.push({ kind: 'equivalent-200-percent', width: 720, height: 450, ...zoomedOverflow })

  await page.setViewportSize({ width: 1440, height: 900 })
  await test.info().attach('场景8 真实布局测量', {
    body: JSON.stringify(layout, null, 2),
    contentType: 'application/json',
  })

  expect(applicationIssues(issues)).toEqual([])
})
