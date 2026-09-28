import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createPinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'

import { ApiError } from '@/api/client'
import type { CapabilityState, HealthResponse } from '@/api/health'
import type { DocumentListPayload } from '@/api/documents'
import type { DemoStatus } from '@/api/demo'
import type { ChatCitation } from '@/api/chat'
import type { RetrievalOptions } from '@/api/retrieval'
import { createAppRouter } from '@/router'

const mocks = vi.hoisted(() => ({
  fetchHealth: vi.fn(),
  listDocuments: vi.fn(),
  getDocument: vi.fn(),
  getDocumentStatus: vi.fn(),
  getDocumentPreview: vi.fn(),
  deleteDocument: vi.fn(),
  fetchDemoStatus: vi.fn(),
  seedDemo: vi.fn(),
  fetchDemoJob: vi.fn(),
  streamChat: vi.fn(),
  fetchRetrievalOptions: vi.fn(),
  fetchSource: vi.fn(),
}))

vi.mock('@/api/health', async () => {
  const actual = await vi.importActual<typeof import('@/api/health')>('@/api/health')
  return { ...actual, fetchHealth: mocks.fetchHealth }
})

vi.mock('@/api/documents', async () => {
  const actual = await vi.importActual<typeof import('@/api/documents')>('@/api/documents')
  return {
    ...actual,
    listDocuments: mocks.listDocuments,
    getDocument: mocks.getDocument,
    getDocumentStatus: mocks.getDocumentStatus,
    getDocumentPreview: mocks.getDocumentPreview,
    deleteDocument: mocks.deleteDocument,
  }
})

vi.mock('@/api/demo', async () => {
  const actual = await vi.importActual<typeof import('@/api/demo')>('@/api/demo')
  return {
    ...actual,
    fetchDemoStatus: mocks.fetchDemoStatus,
    seedDemo: mocks.seedDemo,
    fetchDemoJob: mocks.fetchDemoJob,
  }
})

vi.mock('@/api/chat', async () => {
  const actual = await vi.importActual<typeof import('@/api/chat')>('@/api/chat')
  return { ...actual, streamChat: mocks.streamChat }
})

vi.mock('@/api/retrieval', async () => {
  const actual = await vi.importActual<typeof import('@/api/retrieval')>('@/api/retrieval')
  return {
    ...actual,
    fetchRetrievalOptions: mocks.fetchRetrievalOptions,
    fetchSource: mocks.fetchSource,
  }
})

import ChatView from './ChatView.vue'

const encoder = new TextEncoder()

function encode(text: string): Uint8Array {
  return encoder.encode(text)
}

function frame(name: string, payload: unknown): string {
  return `event: ${name}\ndata: ${JSON.stringify(payload)}\n\n`
}

function responseOf(chunks: string[], signal?: AbortSignal): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encode(chunk))
      }
      if (signal) {
        if (signal.aborted) {
          controller.close()
          return
        }
        signal.addEventListener('abort', () => {
          const error = new Error('aborted')
          error.name = 'AbortError'
          try {
            controller.error(error)
          } catch {
            /* 已经关闭 */
          }
        })
      } else {
        controller.close()
      }
    },
  })
  return { ok: true, status: 200, body: stream } as unknown as Response
}

function health(documents: CapabilityState, chat: CapabilityState): HealthResponse {
  return {
    status: 'degraded',
    version: '0.1.0',
    capabilities: { documents, chat, planning: 'ready' },
    providers: {
      embedding: { provider: 'local', device: 'cpu', ready: false },
      reranker: { provider: 'local', device: 'cpu', ready: false },
      llm: { provider: 'openai_compatible', device: null, ready: false },
    },
  }
}

function documentsPayload(retrievable: number): DocumentListPayload {
  return {
    items: [],
    total: retrievable,
    counts: { ready: retrievable, retrievable, processing: 0, failed: 0 },
  }
}

function demoStatus(): DemoStatus {
  return {
    enabled: true,
    state: 'empty',
    dataset_version: '2026.1',
    manifest_sha256: null,
    pipeline_fingerprint: 'fp',
    available_documents: 0,
    ready_documents: 0,
    failed_documents: 0,
    loaded: false,
    poll_after_seconds: 2,
    active_dataset_version: null,
    serving_previous_version: false,
    active_job_id: null,
    last_job_id: null,
    reason: null,
  }
}

function optionsPayload(overrides: Partial<RetrievalOptions> = {}): RetrievalOptions {
  return {
    majors: [],
    grade_years: [],
    semesters: [],
    doc_categories: [],
    active_dataset_version: null,
    demo_available: true,
    ...overrides,
  }
}

function citation(overrides: Partial<ChatCitation> = {}): ChatCitation {
  return {
    citation_index: 1,
    chunk_id: 'a'.repeat(64),
    doc_id: 'doc-1',
    file_name: '01-培养方案.pdf',
    document_version: '2026.1',
    effective_from: '2026-09-01',
    dataset_version: '2026.1',
    page_number: 4,
    sheet_name: null,
    row_start: null,
    row_end: null,
    section_title: '培养方案/总则',
    quote: '毕业总学分为 155.0 学分。',
    ...overrides,
  }
}

const donePayload = {
  request_id: 'req-ok',
  outcome: 'answered',
  reason_code: null,
  citation_count: 1,
}

interface MountOptions {
  chat?: CapabilityState
  documents?: CapabilityState
  retrievable?: number
  backendDown?: boolean
  options?: RetrievalOptions
  optionsFail?: boolean
}

async function mountChat(overrides: MountOptions = {}): Promise<VueWrapper> {
  if (overrides.backendDown) {
    mocks.fetchHealth.mockRejectedValue(new TypeError('Failed to fetch'))
  } else {
    mocks.fetchHealth.mockResolvedValue(
      health(overrides.documents ?? 'ready', overrides.chat ?? 'unconfigured'),
    )
  }
  mocks.listDocuments.mockResolvedValue(documentsPayload(overrides.retrievable ?? 0))
  mocks.fetchDemoStatus.mockResolvedValue(demoStatus())
  if (overrides.optionsFail) {
    mocks.fetchRetrievalOptions.mockRejectedValue(
      new ApiError('无法连接后端服务（网络错误或跨域被阻断）', { kind: 'network' }),
    )
  } else {
    mocks.fetchRetrievalOptions.mockResolvedValue(overrides.options ?? optionsPayload())
  }

  const router = createAppRouter(createMemoryHistory())
  await router.push('/chat')
  await router.isReady()

  const wrapper = mount(ChatView, {
    global: { plugins: [createPinia(), router, ElementPlus] },
  })
  await flushPromises()
  return wrapper
}

/** 能力就绪 + 有可检索文档时的标准环境。 */
function mountReady(options: MountOptions = {}): Promise<VueWrapper> {
  return mountChat({ chat: 'ready', retrievable: 12, ...options })
}

async function ask(wrapper: VueWrapper, text: string): Promise<void> {
  const input = wrapper.find('.ep-composer textarea')
  await input.setValue(text)
  await input.trigger('keydown', { key: 'Enter' })
  await flushPromises()
}

function composerInput(wrapper: VueWrapper) {
  return wrapper.find('.ep-composer textarea')
}

function composerValue(wrapper: VueWrapper): string {
  return (composerInput(wrapper).element as HTMLTextAreaElement).value
}

describe('ChatView capability gating', () => {
  beforeEach(() => {
    for (const mock of Object.values(mocks)) {
      mock.mockReset()
    }
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('never opens a stream while chat is unconfigured and explains how to configure it', async () => {
    const wrapper = await mountChat({ chat: 'unconfigured' })

    const notice = wrapper.find('.ep-chat__notice')
    expect(notice.text()).toContain('大模型尚未配置')
    expect(notice.text()).toContain('.env')
    expect(notice.text()).not.toContain('sk-')
    expect(composerInput(wrapper).attributes('disabled')).toBeDefined()

    await ask(wrapper, '问题')
    expect(mocks.streamChat).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('shows the reason and a re-check action when chat is unavailable', async () => {
    const wrapper = await mountChat({ chat: 'unavailable' })

    const notice = wrapper.find('.ep-chat__notice')
    expect(notice.text()).toContain('不可用')
    expect(notice.find('button').text()).toContain('重新检测')

    await ask(wrapper, '问题')
    expect(mocks.streamChat).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('disables the input and points to the knowledge base when nothing is retrievable', async () => {
    const wrapper = await mountReady({ retrievable: 0 })

    expect(wrapper.find('.ep-chat__notice').text()).toContain('前往知识库')
    expect(wrapper.find('.ep-chat__notice a').attributes('href')).toContain('knowledge')
    expect(composerInput(wrapper).attributes('disabled')).toBeDefined()

    await ask(wrapper, '问题')
    expect(mocks.streamChat).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('never opens a stream when the backend is unreachable', async () => {
    const wrapper = await mountChat({ backendDown: true })

    expect(wrapper.text()).toContain('后端服务不可连接')
    expect(composerInput(wrapper).attributes('disabled')).toBeDefined()

    await ask(wrapper, '问题')
    expect(mocks.streamChat).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('enables the composer only when chat is ready and retrievable documents exist', async () => {
    const wrapper = await mountReady()
    const input = composerInput(wrapper)

    expect(input.attributes('disabled')).toBeUndefined()
    expect(wrapper.find('.ep-chat__retrievable').text()).toContain('12')

    await ask(wrapper, '毕业学分是多少？')
    expect(mocks.streamChat).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('does not treat candidate or processing documents as retrievable', async () => {
    const wrapper = await mountReady({ retrievable: 0 })

    expect(wrapper.find('.ep-chat__retrievable').text()).toContain('0')
    expect(composerInput(wrapper).attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('shows a loading state while the health and document requests are in flight', async () => {
    let resolveHealth: ((value: HealthResponse) => void) | null = null
    mocks.fetchHealth.mockImplementation(
      () => new Promise<HealthResponse>((resolve) => (resolveHealth = resolve)),
    )
    mocks.listDocuments.mockResolvedValue(documentsPayload(0))
    mocks.fetchDemoStatus.mockResolvedValue(demoStatus())
    mocks.fetchRetrievalOptions.mockResolvedValue(optionsPayload())

    const router = createAppRouter(createMemoryHistory())
    await router.push('/chat')
    await router.isReady()
    const wrapper = mount(ChatView, {
      global: { plugins: [createPinia(), router, ElementPlus] },
    })
    await flushPromises()

    expect(wrapper.find('.ep-chat__loading').exists()).toBe(true)
    expect(composerInput(wrapper).attributes('disabled')).toBeDefined()

    resolveHealth!(health('ready', 'ready'))
    await flushPromises()
    wrapper.unmount()
  })
})

describe('ChatView suggestions and retrieval scope', () => {
  beforeEach(() => {
    for (const mock of Object.values(mocks)) {
      mock.mockReset()
    }
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('offers functional suggestions that only fill the input', async () => {
    const wrapper = await mountReady()

    const suggestions = wrapper.findAll('.ep-suggestion')
    expect(suggestions.length).toBe(3)

    await suggestions[0].trigger('click')

    expect(composerValue(wrapper)).toBe(suggestions[0].text())
    expect(mocks.streamChat).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('builds the retrieval selectors only from the API options', async () => {
    const wrapper = await mountReady({
      options: optionsPayload({
        majors: ['计算机科学与技术'],
        grade_years: [2026],
        semesters: ['2026-2027-1'],
      }),
    })

    const text = wrapper.find('.ep-scope').text()
    expect(text).toContain('计算机科学与技术')
    expect(text).toContain('2026-2027-1')
    wrapper.unmount()
  })

  it('hides a selector whose option list is empty', async () => {
    const wrapper = await mountReady({
      options: optionsPayload({ majors: ['计算机科学与技术'] }),
    })

    const selects = wrapper.findAll('.ep-scope__select')
    expect(selects).toHaveLength(1)
    expect(wrapper.find('.ep-scope').text()).toContain('计算机科学与技术')
    wrapper.unmount()
  })

  it('shows active filters as removable tags and clears them all', async () => {
    const wrapper = await mountReady({
      options: optionsPayload({ majors: ['计算机科学与技术'], doc_categories: [{ value: 'degree_plan', label: '培养方案' }] }),
    })

    const scope = wrapper.findComponent({ name: 'RetrievalScopePanel' })
    scope.vm.$emit('update', { key: 'major', value: '计算机科学与技术' })
    scope.vm.$emit('update', { key: 'doc_category', value: 'degree_plan' })
    await flushPromises()

    const tags = wrapper.findAll('.ep-scope__tag')
    expect(tags.length).toBe(2)
    expect(wrapper.find('.ep-scope').text()).toContain('培养方案')

    await tags[0].find('.el-tag__close').trigger('click')
    await flushPromises()
    expect(wrapper.findAll('.ep-scope__tag').length).toBe(1)

    await wrapper.find('.ep-scope__clear').trigger('click')
    await flushPromises()
    expect(wrapper.findAll('.ep-scope__tag').length).toBe(0)
    wrapper.unmount()
  })

  it('submits grade_year as a number', async () => {
    const wrapper = await mountReady({ options: optionsPayload({ grade_years: [2026] }) })

    const scope = wrapper.findComponent({ name: 'RetrievalScopePanel' })
    scope.vm.$emit('update', { key: 'grade_year', value: 2026 })
    await flushPromises()
    await ask(wrapper, '问题')

    const body = mocks.streamChat.mock.calls[0][0] as { filters: { grade_year: unknown } }
    expect(body.filters.grade_year).toBe(2026)
    expect(typeof body.filters.grade_year).toBe('number')
    wrapper.unmount()
  })

  it('surfaces an options loading failure with a retry action', async () => {
    const wrapper = await mountReady({ optionsFail: true })

    const alert = wrapper.find('.ep-scope .ep-error-alert')
    expect(alert.exists()).toBe(true)
    expect(alert.text()).toContain('未获得服务端请求编号')

    mocks.fetchRetrievalOptions.mockResolvedValue(optionsPayload({ majors: ['计算机科学与技术'] }))
    await alert.find('button').trigger('click')
    await flushPromises()

    expect(mocks.fetchRetrievalOptions).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })
})

describe('ChatView answering flow', () => {
  beforeEach(() => {
    for (const mock of Object.values(mocks)) {
      mock.mockReset()
    }
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders the answered conversation with citations and message semantics', async () => {
    mocks.streamChat.mockResolvedValue(
      responseOf([
        frame('citation', citation()),
        frame('token', { text: '毕业总学分' }),
        frame('token', { text: '为 155.0 [1]' }),
        frame('done', donePayload),
      ]),
    )
    const wrapper = await mountReady()

    await ask(wrapper, '毕业学分是多少？')

    const userMessage = wrapper.find('.ep-msg--user')
    const assistantMessage = wrapper.find('.ep-msg--assistant')
    expect(userMessage.text()).toContain('毕业学分是多少？')
    expect(assistantMessage.find('.ep-msg__badge').text()).toBe('基于知识库')
    expect(assistantMessage.text()).toContain('为 155.0')

    const marker = assistantMessage.find('button.ep-citation')
    expect(marker.exists()).toBe(true)
    expect(wrapper.findAll('.ep-evidence-card')).toHaveLength(1)

    await marker.trigger('click')
    expect(wrapper.find('.ep-evidence-card').classes()).toContain('is-selected')
    wrapper.unmount()
  })

  it('selects the right evidence card when citations arrive out of order', async () => {
    mocks.streamChat.mockResolvedValue(
      responseOf([
        frame('citation', citation({ citation_index: 3, chunk_id: 'c'.repeat(64), file_name: '第三份.pdf' })),
        frame('citation', citation({ citation_index: 1, chunk_id: 'a'.repeat(64), file_name: '第一份.pdf' })),
        frame('token', { text: '正文 [3]' }),
        frame('done', { ...donePayload, citation_count: 2 }),
      ]),
    )
    const wrapper = await mountReady()

    await ask(wrapper, '问题')

    const cards = wrapper.findAll('.ep-evidence-card')
    expect(cards.map((card) => card.find('.ep-evidence-card__index').text())).toEqual(['[1]', '[3]'])

    await wrapper.find('.ep-msg--assistant button.ep-citation').trigger('click')
    expect(cards[1].classes()).toContain('is-selected')
    wrapper.unmount()
  })

  it('keeps a citation placeholder until the citation event arrives', async () => {
    mocks.streamChat.mockResolvedValue(
      responseOf([frame('token', { text: '依据 [1]' }), frame('done', { ...donePayload, citation_count: 0 })]),
    )
    const wrapper = await mountReady()

    await ask(wrapper, '问题')

    expect(wrapper.findAll('button.ep-citation')).toHaveLength(0)
    expect(wrapper.find('span.ep-citation--pending').exists()).toBe(true)
    expect(wrapper.findAll('.ep-evidence-card')).toHaveLength(0)
    wrapper.unmount()
  })

  it('shows the streaming cursor and turns the action into stop', async () => {
    let release: (() => void) | null = null
    mocks.streamChat.mockImplementation(async (_body: unknown, signal?: AbortSignal) => {
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(encode(frame('token', { text: '流式中' })))
          signal?.addEventListener('abort', () => {
            try {
              controller.close()
            } catch {
              /* 已经关闭 */
            }
          })
          release = () => {
            controller.enqueue(encode(frame('done', donePayload)))
            controller.close()
          }
        },
      })
      return { ok: true, status: 200, body: stream } as unknown as Response
    })
    const wrapper = await mountReady()

    const pending = ask(wrapper, '问题')
    await flushPromises()

    expect(wrapper.find('.ep-composer__action').text()).toContain('停止')
    expect(wrapper.find('.ep-msg--assistant .ep-msg__cursor').exists()).toBe(true)

    release!()
    await pending
    expect(wrapper.find('.ep-composer__action').text()).toContain('发送')
    wrapper.unmount()
  })

  it('stops the stream without showing a red error', async () => {
    mocks.streamChat.mockImplementation(async (_body: unknown, signal?: AbortSignal) =>
      responseOf([frame('token', { text: '部分回答' })], signal),
    )
    const wrapper = await mountReady()

    const pending = ask(wrapper, '问题')
    await flushPromises()
    await wrapper.find('.ep-composer__action').trigger('click')
    await pending

    expect(wrapper.text()).toContain('已停止生成')
    expect(wrapper.find('.ep-msg--assistant .el-alert--error').exists()).toBe(false)
    expect(wrapper.find('.ep-msg--assistant').text()).toContain('部分回答')
    wrapper.unmount()
  })

  it('rejects an over-long question in place without sending it', async () => {
    const wrapper = await mountReady()
    const question = 'x'.repeat(4001)

    await composerInput(wrapper).setValue(question)
    await composerInput(wrapper).trigger('keydown', { key: 'Enter' })
    await flushPromises()

    expect(mocks.streamChat).not.toHaveBeenCalled()
    expect(wrapper.find('.ep-chat__notice').text()).toContain('问题过长')
    expect(composerValue(wrapper)).toBe(question)
    wrapper.unmount()
  })

  it('renders a neutral refusal card with its reason and no citations', async () => {
    mocks.streamChat.mockResolvedValue(
      responseOf([
        frame('token', { text: '当前知识库没有足够依据。' }),
        frame('done', {
          request_id: 'req-refused',
          outcome: 'refused',
          reason_code: 'no_evidence',
          citation_count: 0,
        }),
      ]),
    )
    const wrapper = await mountReady()

    await ask(wrapper, '无依据的问题')

    const card = wrapper.find('.ep-msg__card--refused')
    expect(card.exists()).toBe(true)
    expect(card.text()).toContain('当前知识库没有足够依据')
    expect(card.classes()).not.toContain('el-alert--error')
    expect(wrapper.findAll('.ep-evidence-card')).toHaveLength(0)

    await wrapper.find('.ep-msg__action--details').trigger('click')
    await flushPromises()
    expect(wrapper.find('.ep-details').text()).toContain('no_evidence')
    wrapper.unmount()
  })

  it('renders a conflict warning that keeps every version', async () => {
    mocks.streamChat.mockResolvedValue(
      responseOf([
        frame('citation', citation({ citation_index: 1, document_version: '2025.1' })),
        frame('citation', citation({ citation_index: 2, chunk_id: 'b'.repeat(64), document_version: '2026.1' })),
        frame('token', { text: '两个版本不一致 [1][2]' }),
        frame('done', {
          request_id: 'req-conflict',
          outcome: 'conflict',
          reason_code: 'version_conflict',
          citation_count: 2,
        }),
      ]),
    )
    const wrapper = await mountReady()

    await ask(wrapper, '毕业总学分')

    const card = wrapper.find('.ep-msg__card--conflict')
    expect(card.exists()).toBe(true)
    expect(card.text()).toContain('2025.1')
    expect(card.text()).toContain('2026.1')
    expect(wrapper.findAll('.ep-evidence-card')).toHaveLength(2)
    wrapper.unmount()
  })

  it('keeps the partial answer and the real request id after an in-stream error', async () => {
    mocks.streamChat.mockResolvedValue(
      responseOf([
        frame('token', { text: '部分回答' }),
        frame('error', {
          code: 'MODEL_TIMEOUT',
          message: '生成超时，请重试',
          retryable: true,
          request_id: 'req-err-1',
        }),
      ]),
    )
    const wrapper = await mountReady()

    await ask(wrapper, '问题')

    const message = wrapper.find('.ep-msg--assistant')
    expect(message.text()).toContain('部分回答')
    expect(message.find('.ep-msg__card--error').text()).toContain('回答中断')
    expect(message.text()).toContain('MODEL_TIMEOUT')
    expect(message.text()).toContain('req-err-1')
    expect(message.find('.ep-msg__action--retry').exists()).toBe(true)
    wrapper.unmount()
  })

  it('hides the retry action when the server marks the error as not retryable', async () => {
    mocks.streamChat.mockResolvedValue(
      responseOf([
        frame('error', {
          code: 'MODEL_RESPONSE_INVALID',
          message: '模型响应不符合引用协议',
          retryable: false,
          request_id: 'req-err-2',
        }),
      ]),
    )
    const wrapper = await mountReady()

    await ask(wrapper, '问题')

    expect(wrapper.find('.ep-msg__action--retry').exists()).toBe(false)
    wrapper.unmount()
  })

  it('marks a stream that ends without a terminal event as interrupted', async () => {
    mocks.streamChat.mockResolvedValue(responseOf([frame('token', { text: '半截回答' })]))
    const wrapper = await mountReady()

    await ask(wrapper, '问题')

    const card = wrapper.find('.ep-msg__card--interrupted')
    expect(card.exists()).toBe(true)
    expect(card.text()).toContain('连接中断')
    expect(wrapper.text()).toContain('半截回答')
    expect(wrapper.find('.ep-msg__action--retry').exists()).toBe(true)
    wrapper.unmount()
  })

  it('reports a network failure without inventing a request id', async () => {
    mocks.streamChat.mockRejectedValue(
      new ApiError('无法连接后端服务（网络错误或跨域被阻断）', { kind: 'network' }),
    )
    const wrapper = await mountReady()

    await ask(wrapper, '问题')

    const alert = wrapper.find('.ep-chat__error')
    expect(alert.text()).toContain('无法连接后端服务')
    expect(alert.text()).toContain('未获得服务端请求编号')
    wrapper.unmount()
  })

  it('retries the same question only after the user asks for it', async () => {
    mocks.streamChat.mockResolvedValueOnce(
      responseOf([
        frame('token', { text: '失败回答' }),
        frame('error', {
          code: 'MODEL_TIMEOUT',
          message: '生成超时，请重试',
          retryable: true,
          request_id: 'req-err-3',
        }),
      ]),
    )
    const wrapper = await mountReady()
    await ask(wrapper, '唯一的问题')
    expect(mocks.streamChat).toHaveBeenCalledTimes(1)

    mocks.streamChat.mockResolvedValueOnce(
      responseOf([frame('token', { text: '重试回答' }), frame('done', donePayload)]),
    )
    await wrapper.find('.ep-msg__action--retry').trigger('click')
    await flushPromises()

    expect(mocks.streamChat).toHaveBeenCalledTimes(2)
    const retryBody = mocks.streamChat.mock.calls[1][0] as { messages: Array<{ content: string }> }
    expect(retryBody.messages.filter((item) => item.content === '唯一的问题')).toHaveLength(1)
    expect(wrapper.find('.ep-msg--assistant').text()).toContain('重试回答')
    wrapper.unmount()
  })

  it('copies only the answer text', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    })
    mocks.streamChat.mockResolvedValue(
      responseOf([frame('token', { text: '毕业总学分为 155.0' }), frame('done', donePayload)]),
    )
    const wrapper = await mountReady()

    await ask(wrapper, '问题')
    await wrapper.find('.ep-msg__action--copy').trigger('click')
    await flushPromises()

    expect(writeText).toHaveBeenCalledWith('毕业总学分为 155.0')
    wrapper.unmount()
  })

  it('opens the source drawer only when the user asks for the original text', async () => {
    mocks.streamChat.mockResolvedValue(
      responseOf([frame('citation', citation()), frame('token', { text: '正文 [1]' }), frame('done', donePayload)]),
    )
    mocks.fetchSource.mockResolvedValue({
      chunk_id: 'a'.repeat(64),
      doc_id: 'doc-1',
      file_name: '01-培养方案.pdf',
      file_type: 'pdf',
      document_version: '2026.1',
      effective_from: '2026-09-01',
      dataset_version: '2026.1',
      text: '毕业总学分为 155.0 学分。',
      page_number: 4,
      sheet_name: null,
      row_start: null,
      row_end: null,
      section_title: '培养方案/总则',
    })
    const wrapper = await mountReady()

    await ask(wrapper, '问题')
    expect(mocks.fetchSource).not.toHaveBeenCalled()

    await wrapper.find('.ep-evidence-card__open').trigger('click')
    await flushPromises()

    expect(mocks.fetchSource).toHaveBeenCalledTimes(1)
    expect(wrapper.find('.ep-source__text').text()).toContain('毕业总学分为 155.0 学分。')
    wrapper.unmount()
  })
})

describe('ChatView scrolling and lifecycle', () => {
  beforeEach(() => {
    for (const mock of Object.values(mocks)) {
      mock.mockReset()
    }
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  function setScroll(wrapper: VueWrapper, metrics: { scrollTop: number; scrollHeight: number; clientHeight: number }) {
    const element = wrapper.find('.ep-chat__scroll').element as HTMLElement
    Object.defineProperty(element, 'scrollHeight', { value: metrics.scrollHeight, configurable: true })
    Object.defineProperty(element, 'clientHeight', { value: metrics.clientHeight, configurable: true })
    Object.defineProperty(element, 'scrollTop', {
      value: metrics.scrollTop,
      writable: true,
      configurable: true,
    })
    return element
  }

  it('pauses auto-follow after the user scrolls up and resumes at the bottom', async () => {
    const wrapper = await mountReady()
    const element = setScroll(wrapper, { scrollTop: 700, scrollHeight: 1000, clientHeight: 300 })

    expect(wrapper.find('.ep-chat__scroll').attributes('data-auto-follow')).toBe('on')

    element.dispatchEvent(new Event('scroll'))
    await flushPromises()
    expect(wrapper.find('.ep-chat__scroll').attributes('data-auto-follow')).toBe('on')

    setScroll(wrapper, { scrollTop: 100, scrollHeight: 1000, clientHeight: 300 })
    element.dispatchEvent(new Event('scroll'))
    await flushPromises()
    expect(wrapper.find('.ep-chat__scroll').attributes('data-auto-follow')).toBe('off')

    setScroll(wrapper, { scrollTop: 700, scrollHeight: 1000, clientHeight: 300 })
    element.dispatchEvent(new Event('scroll'))
    await flushPromises()
    expect(wrapper.find('.ep-chat__scroll').attributes('data-auto-follow')).toBe('on')
    wrapper.unmount()
  })

  it('aborts the active stream when the page unmounts', async () => {
    mocks.streamChat.mockImplementation(async (_body: unknown, signal?: AbortSignal) =>
      responseOf([frame('token', { text: '进行中' })], signal),
    )
    const wrapper = await mountReady()

    const pending = ask(wrapper, '问题')
    await flushPromises()
    const signal = mocks.streamChat.mock.calls[0][1] as AbortSignal
    expect(signal.aborted).toBe(false)

    wrapper.unmount()
    await pending

    expect(signal.aborted).toBe(true)
  })

  it('aborts an in-flight source request when the page unmounts', async () => {
    mocks.streamChat.mockResolvedValue(
      responseOf([frame('citation', citation()), frame('token', { text: '正文 [1]' }), frame('done', donePayload)]),
    )
    mocks.fetchSource.mockImplementation(() => new Promise(() => {}))
    const wrapper = await mountReady()

    await ask(wrapper, '问题')
    await wrapper.find('.ep-evidence-card__open').trigger('click')
    await flushPromises()
    const signal = mocks.fetchSource.mock.calls[0][1] as AbortSignal

    wrapper.unmount()

    expect(signal.aborted).toBe(true)
  })
})
