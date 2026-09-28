import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { ApiError } from '@/api/client'
import {
  CHAT_QUESTION_TOO_LONG,
  MAX_CHAT_MESSAGE_CHARS,
  MAX_CHAT_MESSAGES,
  MAX_CHAT_TOTAL_CHARS,
  type ChatCitation,
  type ChatMessagePayload,
} from '@/api/chat'
import type { RetrievalOptions } from '@/api/retrieval'

const mocks = vi.hoisted(() => ({ streamChat: vi.fn(), fetchRetrievalOptions: vi.fn() }))

vi.mock('@/api/chat', async () => {
  const actual = await vi.importActual<typeof import('@/api/chat')>('@/api/chat')
  return { ...actual, streamChat: mocks.streamChat }
})

vi.mock('@/api/retrieval', async () => {
  const actual = await vi.importActual<typeof import('@/api/retrieval')>('@/api/retrieval')
  return { ...actual, fetchRetrievalOptions: mocks.fetchRetrievalOptions }
})

import { useChatStore } from './chat'

const encoder = new TextEncoder()

function encode(text: string): Uint8Array {
  return encoder.encode(text)
}

function frame(name: string, payload: unknown): string {
  return `event: ${name}\ndata: ${JSON.stringify(payload)}\n\n`
}

function citation(index: number): ChatCitation {
  return {
    citation_index: index,
    chunk_id: `chunk-${index}`,
    doc_id: `doc-${index}`,
    file_name: `file-${index}.pdf`,
    document_version: '2026.1',
    effective_from: '2026-09-01',
    dataset_version: '2026.1',
    page_number: index,
    sheet_name: null,
    row_start: null,
    row_end: null,
    section_title: `章节 ${index}`,
    quote: `原文 ${index}`,
  }
}

const donePayload = {
  request_id: 'req-1',
  outcome: 'answered',
  reason_code: null,
  citation_count: 1,
}

function responseOf(stream: ReadableStream<Uint8Array>): Response {
  return { ok: true, status: 200, body: stream } as unknown as Response
}

/** 把若干文本片段切分为独立的网络 chunk，模拟真实分片到达。 */
function streamOf(chunks: string[], signal?: AbortSignal): Response {
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
  return responseOf(stream)
}

function mountStore() {
  setActivePinia(createPinia())
  return useChatStore()
}

/** 让已入队的微任务跑完，直到条件成立（或达到有界上限）。 */
async function settleUntil(predicate: () => boolean): Promise<void> {
  for (let attempt = 0; attempt < 25 && !predicate(); attempt += 1) {
    await Promise.resolve()
  }
}

describe('chat store streaming', () => {
  beforeEach(() => {
    mocks.streamChat.mockReset()
    mocks.fetchRetrievalOptions.mockReset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('streams a standard answer and finalizes the assistant message', async () => {
    mocks.streamChat.mockResolvedValue(
      streamOf([
        frame('token', { text: '毕业总学分' }),
        frame('citation', citation(1)),
        frame('token', { text: '为 155.0' }),
        frame('done', donePayload),
      ]),
    )
    const store = mountStore()
    store.question = '毕业学分是多少？'

    await store.send()

    expect(store.messages).toHaveLength(2)
    expect(store.messages[0]).toMatchObject({ role: 'user', content: '毕业学分是多少？' })
    expect(store.messages[1]).toMatchObject({
      role: 'assistant',
      content: '毕业总学分为 155.0',
      outcome: 'answered',
      requestId: 'req-1',
      interrupted: false,
      stopped: false,
    })
    expect(store.messages[1].citations).toHaveLength(1)
    expect(store.messages[1].citations[0].citation_index).toBe(1)
    expect(store.streaming).toBe(false)
    expect(store.question).toBe('')
    expect(store.streamError).toBeNull()
    expect(store.outcome).toBe('answered')
  })

  it('appends token text only and never derives citations from the answer body', async () => {
    mocks.streamChat.mockResolvedValue(
      streamOf([
        frame('token', { text: '依据 [1] 的规定' }),
        frame('done', { ...donePayload, citation_count: 0 }),
      ]),
    )
    const store = mountStore()
    store.question = '问题'

    await store.send()

    expect(store.messages[1].content).toBe('依据 [1] 的规定')
    expect(store.citations).toEqual([])
    expect(store.messages[1].citations).toEqual([])
  })

  it('keeps a refused terminal outcome without fabricating citations', async () => {
    mocks.streamChat.mockResolvedValue(
      streamOf([
        frame('token', { text: '当前知识库没有足够依据。' }),
        frame('done', {
          request_id: 'req-2',
          outcome: 'refused',
          reason_code: 'no_evidence',
          citation_count: 0,
        }),
      ]),
    )
    const store = mountStore()
    store.question = '无依据的问题'

    await store.send()

    expect(store.outcome).toBe('refused')
    expect(store.messages[1].outcome).toBe('refused')
    expect(store.citations).toEqual([])
    expect(store.streamError).toBeNull()
  })

  it('keeps every real citation when a conflict is reported', async () => {
    mocks.streamChat.mockResolvedValue(
      streamOf([
        frame('citation', citation(1)),
        frame('citation', citation(2)),
        frame('token', { text: '两个版本不一致' }),
        frame('done', {
          request_id: 'req-3',
          outcome: 'conflict',
          reason_code: 'version_conflict',
          citation_count: 2,
        }),
      ]),
    )
    const store = mountStore()
    store.question = '毕业总学分'

    await store.send()

    expect(store.outcome).toBe('conflict')
    expect(store.citations.map((item) => item.citation_index)).toEqual([1, 2])
    expect(store.messages[1].citations).toHaveLength(2)
  })

  it('keeps out-of-order citations and stores them sorted by citation_index', async () => {
    mocks.streamChat.mockResolvedValue(
      streamOf([
        frame('citation', citation(3)),
        frame('citation', citation(1)),
        frame('citation', citation(2)),
        frame('token', { text: '正文' }),
        frame('done', { ...donePayload, citation_count: 3 }),
      ]),
    )
    const store = mountStore()
    store.question = '问题'

    await store.send()

    expect(store.citations.map((item) => item.citation_index)).toEqual([1, 2, 3])
    expect(store.citations.map((item) => item.chunk_id)).toEqual([
      'chunk-1',
      'chunk-2',
      'chunk-3',
    ])
  })

  it('keeps the partial answer and the in-stream request id when the stream errors', async () => {
    mocks.streamChat.mockResolvedValue(
      streamOf([
        frame('token', { text: '部分回答' }),
        frame('citation', citation(1)),
        frame('error', {
          code: 'MODEL_TIMEOUT',
          message: '生成超时，请重试',
          retryable: true,
          request_id: 'req-err-1',
        }),
      ]),
    )
    const store = mountStore()
    store.question = '问题'

    await store.send()

    expect(store.streamError?.code).toBe('MODEL_TIMEOUT')
    expect(store.streamError?.requestId).toBe('req-err-1')
    expect(store.requestId).toBe('req-err-1')
    expect(store.messages[1].content).toBe('部分回答')
    expect(store.messages[1].citations).toHaveLength(1)
    expect(store.messages[1].errorCode).toBe('MODEL_TIMEOUT')
    expect(store.outcome).toBeNull()
  })

  it('marks an unterminated stream as interrupted instead of answered', async () => {
    mocks.streamChat.mockResolvedValue(streamOf([frame('token', { text: '半截回答' })]))
    const store = mountStore()
    store.question = '问题'

    await store.send()

    expect(store.interrupted).toBe(true)
    expect(store.outcome).toBeNull()
    expect(store.messages[1].interrupted).toBe(true)
    expect(store.messages[1].content).toBe('半截回答')
    expect(store.streamError).toBeNull()
  })

  it('treats a protocol violation as an explicit client-side error', async () => {
    mocks.streamChat.mockResolvedValue(streamOf([frame('delta', { text: 'x' })]))
    const store = mountStore()
    store.question = '问题'

    await store.send()

    expect(store.streamError?.code).toBe('CHAT_STREAM_PROTOCOL_ERROR')
    expect(store.streamError?.requestId).toBeUndefined()
    expect(store.interrupted).toBe(false)
    expect(store.streaming).toBe(false)
  })

  it('marks a user abort as stopped rather than an error', async () => {
    mocks.streamChat.mockImplementation(async (_body: unknown, signal?: AbortSignal) =>
      streamOf([frame('token', { text: '部分' })], signal),
    )
    const store = mountStore()
    store.question = '问题'

    const pending = store.send()
    await settleUntil(() => store.streamingContent !== '')
    expect(store.streamingContent).toBe('部分')
    store.stop()
    await pending

    expect(store.stopped).toBe(true)
    expect(store.streamError).toBeNull()
    expect(store.interrupted).toBe(false)
    expect(store.streaming).toBe(false)
    expect(store.messages[1].stopped).toBe(true)
    expect(store.messages[1].errorCode).toBeNull()
  })

  it('ignores a second send while a stream is active', async () => {
    mocks.streamChat.mockImplementation(async (_body: unknown, signal?: AbortSignal) =>
      streamOf([frame('token', { text: '进行中' })], signal),
    )
    const store = mountStore()
    store.question = '问题'

    const first = store.send()
    await settleUntil(() => store.streamingContent !== '')
    store.question = '第二个问题'
    await store.send()

    expect(mocks.streamChat).toHaveBeenCalledTimes(1)

    store.stop()
    await first
  })

  it('clears the previous stream state while keeping finished messages', async () => {
    mocks.streamChat.mockResolvedValue(
      streamOf([frame('token', { text: '第一轮' }), frame('done', donePayload)]),
    )
    const store = mountStore()
    store.question = '第一个问题'
    await store.send()

    expect(store.messages).toHaveLength(2)

    mocks.streamChat.mockResolvedValue(
      streamOf([frame('token', { text: '第二轮' }), frame('done', donePayload)]),
    )
    store.question = '第二个问题'
    await store.send()

    expect(store.messages).toHaveLength(4)
    expect(store.messages[1].content).toBe('第一轮')
    expect(store.messages[3].content).toBe('第二轮')
    expect(store.streamingContent).toBe('第二轮')
  })

  it('sends the last ten messages and the current filters verbatim', async () => {
    // 每次请求都必须拿到一条全新的流（ReadableStream 只能被消费一次）
    mocks.streamChat.mockImplementation(async () => streamOf([frame('done', donePayload)]))
    const store = mountStore()
    store.filters = {
      major: '计算机科学与技术',
      grade_year: 2026,
      semester: null,
      doc_category: 'degree_plan',
    }

    for (let index = 0; index < 7; index += 1) {
      store.question = `问题 ${index}`
      await store.send()
    }

    const lastBody = mocks.streamChat.mock.calls.at(-1)?.[0] as {
      messages: unknown[]
      filters: unknown
    }
    expect(lastBody.messages).toHaveLength(10)
    expect(lastBody.messages.at(-1)).toMatchObject({ role: 'user', content: '问题 6' })
    expect(lastBody.filters).toEqual({
      major: '计算机科学与技术',
      grade_year: 2026,
      semester: null,
      doc_category: 'degree_plan',
    })
  })

  it('surfaces a pre-stream HTTP error with the server request id and never retries', async () => {
    mocks.streamChat.mockRejectedValue(
      new ApiError('问答能力未配置', {
        kind: 'http',
        status: 503,
        code: 'LLM_PROVIDER_UNAVAILABLE',
        requestId: 'req-http-1',
      }),
    )
    const store = mountStore()
    store.question = '问题'

    await store.send()
    await Promise.resolve()

    expect(store.streamError?.code).toBe('LLM_PROVIDER_UNAVAILABLE')
    expect(store.streamError?.requestId).toBe('req-http-1')
    expect(store.streaming).toBe(false)
    expect(store.messages).toHaveLength(1)
    expect(mocks.streamChat).toHaveBeenCalledTimes(1)
  })

  it('reports a network failure without fabricating a request id', async () => {
    mocks.streamChat.mockRejectedValue(
      new ApiError('无法连接后端服务（网络错误或跨域被阻断）', { kind: 'network' }),
    )
    const store = mountStore()
    store.question = '问题'

    await store.send()

    expect(store.streamError?.requestId).toBeUndefined()
    expect(store.streamError?.requestIdLabel).toBe('未获得服务端请求编号')
    expect(store.requestId).toBeNull()
  })

  it('ignores an empty question and keeps stop idempotent when idle', async () => {
    const store = mountStore()

    expect(store.canSend).toBe(false)
    store.question = '   '
    await store.send()

    expect(mocks.streamChat).not.toHaveBeenCalled()
    expect(store.messages).toHaveLength(0)

    store.question = '问题'
    expect(store.canSend).toBe(true)

    expect(() => {
      store.stop()
      store.stop()
    }).not.toThrow()
    expect(store.streaming).toBe(false)
    expect(store.stopped).toBe(false)
  })
})

describe('chat store options', () => {
  beforeEach(() => {
    mocks.streamChat.mockReset()
    mocks.fetchRetrievalOptions.mockReset()
  })

  it('loads retrieval options with numeric grade years', async () => {
    const options: RetrievalOptions = {
      majors: ['计算机科学与技术'],
      grade_years: [2026],
      semesters: ['2026-2027-1'],
      doc_categories: [{ value: 'degree_plan', label: '培养方案' }],
      active_dataset_version: '2026.1',
      demo_available: true,
    }
    mocks.fetchRetrievalOptions.mockResolvedValue(options)
    const store = mountStore()

    await store.loadOptions()

    expect(store.options?.grade_years).toEqual([2026])
    expect(typeof store.options?.grade_years[0]).toBe('number')
    expect(store.optionsLoading).toBe(false)
    expect(store.optionsError).toBeNull()
  })

  it('surfaces option loading failures without clearing existing options', async () => {
    const store = mountStore()
    mocks.fetchRetrievalOptions.mockRejectedValueOnce(
      new ApiError('无法连接后端服务（网络错误或跨域被阻断）', { kind: 'network' }),
    )

    await store.loadOptions()

    expect(store.optionsError?.kind).toBe('network')
    expect(store.optionsLoading).toBe(false)
    expect(store.options).toBeNull()
  })
})

describe('chat store citation selection', () => {
  beforeEach(() => {
    mocks.streamChat.mockReset()
    mocks.fetchRetrievalOptions.mockReset()
  })

  it('selects and clears the active citation and resets it on a new turn', async () => {
    mocks.streamChat.mockResolvedValue(
      streamOf([frame('citation', citation(1)), frame('done', donePayload)]),
    )
    const store = mountStore()
    store.question = '问题'
    await store.send()

    store.selectCitation(1)
    expect(store.selectedCitationIndex).toBe(1)

    store.selectCitation(1)
    expect(store.selectedCitationIndex).toBeNull()

    mocks.streamChat.mockResolvedValue(
      streamOf([frame('citation', citation(2)), frame('done', donePayload)]),
    )
    store.question = '第二个问题'
    const pending = store.send()
    expect(store.selectedCitationIndex).toBeNull()
    await pending
  })

  it('clears the whole conversation on request', async () => {
    mocks.streamChat.mockResolvedValue(streamOf([frame('done', donePayload)]))
    const store = mountStore()
    store.question = '问题'
    await store.send()

    store.clearConversation()

    expect(store.messages).toEqual([])
    expect(store.citations).toEqual([])
    expect(store.streamingContent).toBe('')
    expect(store.outcome).toBeNull()
    expect(store.requestId).toBeNull()
  })
})

describe('chat store question validation', () => {
  beforeEach(() => {
    mocks.streamChat.mockReset()
    mocks.fetchRetrievalOptions.mockReset()
  })

  it('accepts a question of exactly the single-message limit', async () => {
    mocks.streamChat.mockResolvedValue(streamOf([frame('done', donePayload)]))
    const store = mountStore()
    store.question = 'x'.repeat(MAX_CHAT_MESSAGE_CHARS)

    await store.send()

    expect(mocks.streamChat).toHaveBeenCalledTimes(1)
    const sent = mocks.streamChat.mock.calls[0][0] as { messages: ChatMessagePayload[] }
    expect(sent.messages.at(-1)?.content).toHaveLength(MAX_CHAT_MESSAGE_CHARS)
    expect(store.validationError).toBeNull()
  })

  it('rejects an over-long question without clearing the input or sending it', async () => {
    const store = mountStore()
    const question = 'x'.repeat(MAX_CHAT_MESSAGE_CHARS + 1)
    store.question = question

    await store.send()

    expect(mocks.streamChat).not.toHaveBeenCalled()
    expect(store.question).toBe(question)
    expect(store.messages).toHaveLength(0)
    expect(store.streaming).toBe(false)
    expect(store.validationError?.code).toBe(CHAT_QUESTION_TOO_LONG)
    expect(store.validationError?.message).toContain(String(MAX_CHAT_MESSAGE_CHARS))
    expect(store.requestId).toBeNull()
    expect(store.streamError).toBeNull()
  })

  it('clears the validation error once a valid question is sent', async () => {
    const store = mountStore()
    store.question = 'x'.repeat(MAX_CHAT_MESSAGE_CHARS + 1)
    await store.send()
    expect(store.validationError).not.toBeNull()

    mocks.streamChat.mockResolvedValue(streamOf([frame('done', donePayload)]))
    store.question = '正常问题'
    await store.send()

    expect(store.validationError).toBeNull()
    expect(mocks.streamChat).toHaveBeenCalledTimes(1)
  })

  it('keeps every request inside the backend contract for a long conversation', async () => {
    mocks.streamChat.mockImplementation(async () => streamOf([frame('done', donePayload)]))
    const store = mountStore()

    for (let index = 0; index < 8; index += 1) {
      store.question = `${index}`.padEnd(3000, 'q')
      await store.send()
    }

    const sent = mocks.streamChat.mock.calls.at(-1)?.[0] as { messages: ChatMessagePayload[] }
    const total = sent.messages.reduce((sum, item) => sum + item.content.length, 0)

    expect(sent.messages.length).toBeGreaterThanOrEqual(1)
    expect(sent.messages.length).toBeLessThanOrEqual(MAX_CHAT_MESSAGES)
    expect(sent.messages.every((item) => item.content.length <= MAX_CHAT_MESSAGE_CHARS)).toBe(true)
    expect(total).toBeLessThanOrEqual(MAX_CHAT_TOTAL_CHARS)
    expect(sent.messages.at(-1)?.role).toBe('user')
    expect(sent.messages.at(-1)?.content.trim()).not.toBe('')
  })
})

describe('chat store terminal metadata', () => {
  beforeEach(() => {
    mocks.streamChat.mockReset()
    mocks.fetchRetrievalOptions.mockReset()
  })

  it('keeps the server reason_code for a refusal without evidence', async () => {
    mocks.streamChat.mockResolvedValue(
      streamOf([
        frame('token', { text: '当前知识库没有足够依据。' }),
        frame('done', {
          request_id: 'req-r1',
          outcome: 'refused',
          reason_code: 'no_evidence',
          citation_count: 0,
        }),
      ]),
    )
    const store = mountStore()
    store.question = '问题'

    await store.send()

    expect(store.reasonCode).toBe('no_evidence')
    expect(store.messages[1].reasonCode).toBe('no_evidence')
  })

  it('keeps the planning guard reason_code for a refusal', async () => {
    mocks.streamChat.mockResolvedValue(
      streamOf([
        frame('token', { text: '规划能力尚未接入。' }),
        frame('done', {
          request_id: 'req-r2',
          outcome: 'refused',
          reason_code: 'planning_unavailable',
          citation_count: 0,
        }),
      ]),
    )
    const store = mountStore()
    store.question = '我的学分够吗'

    await store.send()

    expect(store.reasonCode).toBe('planning_unavailable')
    expect(store.messages[1].reasonCode).toBe('planning_unavailable')
  })

  it('keeps the version conflict reason_code together with its citations', async () => {
    mocks.streamChat.mockResolvedValue(
      streamOf([
        frame('citation', citation(1)),
        frame('citation', citation(2)),
        frame('done', {
          request_id: 'req-r3',
          outcome: 'conflict',
          reason_code: 'version_conflict',
          citation_count: 2,
        }),
      ]),
    )
    const store = mountStore()
    store.question = '毕业总学分'

    await store.send()

    expect(store.reasonCode).toBe('version_conflict')
    expect(store.outcome).toBe('conflict')
    expect(store.messages[1].reasonCode).toBe('version_conflict')
    expect(store.messages[1].citations).toHaveLength(2)
  })

  it('keeps a null reason_code for a normal answer', async () => {
    mocks.streamChat.mockResolvedValue(
      streamOf([frame('token', { text: '答案' }), frame('done', donePayload)]),
    )
    const store = mountStore()
    store.question = '问题'

    await store.send()

    expect(store.reasonCode).toBeNull()
    expect(store.messages[1].reasonCode).toBeNull()
  })

  it('clears the previous reason_code when a new request starts', async () => {
    mocks.streamChat.mockResolvedValue(
      streamOf([
        frame('done', {
          request_id: 'req-r4',
          outcome: 'refused',
          reason_code: 'no_evidence',
          citation_count: 0,
        }),
      ]),
    )
    const store = mountStore()
    store.question = '问题'
    await store.send()
    expect(store.reasonCode).toBe('no_evidence')

    mocks.streamChat.mockResolvedValue(streamOf([frame('done', donePayload)]))
    store.question = '第二个问题'
    const pending = store.send()
    expect(store.reasonCode).toBeNull()
    await pending

    expect(store.messages.at(-1)?.reasonCode).toBeNull()
    expect(store.messages[1].reasonCode).toBe('no_evidence')
  })
})

describe('chat store stream retryable', () => {
  beforeEach(() => {
    mocks.streamChat.mockReset()
    mocks.fetchRetrievalOptions.mockReset()
  })

  it('keeps retryable=true from an in-stream error', async () => {
    mocks.streamChat.mockResolvedValue(
      streamOf([
        frame('token', { text: '部分回答' }),
        frame('error', {
          code: 'MODEL_TIMEOUT',
          message: '生成超时，请重试',
          retryable: true,
          request_id: 'req-e1',
        }),
      ]),
    )
    const store = mountStore()
    store.question = '问题'

    await store.send()

    expect(store.retryable).toBe(true)
    expect(store.streamError?.code).toBe('MODEL_TIMEOUT')
    expect(store.messages[1].retryable).toBe(true)
    expect(store.messages[1].content).toBe('部分回答')
  })

  it('keeps retryable=false from an in-stream error', async () => {
    mocks.streamChat.mockResolvedValue(
      streamOf([
        frame('error', {
          code: 'MODEL_RESPONSE_INVALID',
          message: '模型响应不符合引用协议',
          retryable: false,
          request_id: 'req-e2',
        }),
      ]),
    )
    const store = mountStore()
    store.question = '问题'

    await store.send()

    expect(store.retryable).toBe(false)
    expect(store.messages[1].retryable).toBe(false)
  })

  it('clears the previous retryable when a new request starts', async () => {
    mocks.streamChat.mockResolvedValue(
      streamOf([
        frame('error', {
          code: 'MODEL_TIMEOUT',
          message: '生成超时，请重试',
          retryable: true,
          request_id: 'req-e3',
        }),
      ]),
    )
    const store = mountStore()
    store.question = '问题'
    await store.send()
    expect(store.retryable).toBe(true)

    mocks.streamChat.mockResolvedValue(streamOf([frame('done', donePayload)]))
    store.question = '第二个问题'
    const pending = store.send()
    expect(store.retryable).toBeNull()
    await pending

    expect(store.messages[1].retryable).toBe(true)
    expect(store.messages.at(-1)?.retryable).toBeNull()
  })

  it('leaves retryable null for a pre-stream HTTP error', async () => {
    mocks.streamChat.mockRejectedValue(
      new ApiError('问答能力未配置', {
        kind: 'http',
        status: 503,
        code: 'LLM_PROVIDER_UNAVAILABLE',
        requestId: 'req-http-1',
      }),
    )
    const store = mountStore()
    store.question = '问题'

    await store.send()

    expect(store.streamError?.requestId).toBe('req-http-1')
    expect(store.retryable).toBeNull()
  })

  it('leaves retryable null for a network failure or a protocol violation', async () => {
    mocks.streamChat.mockRejectedValue(
      new ApiError('无法连接后端服务（网络错误或跨域被阻断）', { kind: 'network' }),
    )
    const networkStore = mountStore()
    networkStore.question = '问题'
    await networkStore.send()
    expect(networkStore.retryable).toBeNull()

    mocks.streamChat.mockResolvedValue(streamOf([frame('delta', { text: 'x' })]))
    const protocolStore = mountStore()
    protocolStore.question = '问题'
    await protocolStore.send()
    expect(protocolStore.streamError?.code).toBe('CHAT_STREAM_PROTOCOL_ERROR')
    expect(protocolStore.retryable).toBeNull()
  })

  it('leaves retryable null when the user stops or the stream is interrupted', async () => {
    mocks.streamChat.mockImplementation(async (_body: unknown, signal?: AbortSignal) =>
      streamOf([frame('token', { text: '部分' })], signal),
    )
    const stoppedStore = mountStore()
    stoppedStore.question = '问题'
    const pending = stoppedStore.send()
    await settleUntil(() => stoppedStore.streamingContent !== '')
    stoppedStore.stop()
    await pending
    expect(stoppedStore.stopped).toBe(true)
    expect(stoppedStore.retryable).toBeNull()

    mocks.streamChat.mockResolvedValue(streamOf([frame('token', { text: '半截' })]))
    const interruptedStore = mountStore()
    interruptedStore.question = '问题'
    await interruptedStore.send()
    expect(interruptedStore.interrupted).toBe(true)
    expect(interruptedStore.retryable).toBeNull()
    expect(interruptedStore.messages[1].retryable).toBeNull()
  })
})

describe('chat store manual retry', () => {
  beforeEach(() => {
    mocks.streamChat.mockReset()
    mocks.fetchRetrievalOptions.mockReset()
  })

  async function failFirstTurn(store: ReturnType<typeof mountStore>, question: string) {
    mocks.streamChat.mockResolvedValueOnce(
      streamOf([
        frame('token', { text: '不完整回答' }),
        frame('error', {
          code: 'MODEL_TIMEOUT',
          message: '生成超时，请重试',
          retryable: true,
          request_id: 'req-fail-1',
        }),
      ]),
    )
    store.question = question
    await store.send()
  }

  it('keeps incomplete assistant answers out of the next request history', async () => {
    const store = mountStore()
    await failFirstTurn(store, '第一个问题')
    expect(store.messages).toHaveLength(2)

    mocks.streamChat.mockResolvedValueOnce(streamOf([frame('done', donePayload)]))
    store.question = '第二个问题'
    await store.send()

    const second = mocks.streamChat.mock.calls[1][0] as { messages: ChatMessagePayload[] }
    expect(second.messages.map((item) => item.content)).toEqual(['第一个问题', '第二个问题'])
  })

  it('keeps a stopped or interrupted answer out of the next request history', async () => {
    mocks.streamChat.mockResolvedValue(streamOf([frame('token', { text: '半截回答' })]))
    const store = mountStore()
    store.question = '第一个问题'
    await store.send()
    expect(store.interrupted).toBe(true)

    mocks.streamChat.mockResolvedValueOnce(streamOf([frame('done', donePayload)]))
    store.question = '第二个问题'
    await store.send()

    const second = mocks.streamChat.mock.calls[1][0] as { messages: ChatMessagePayload[] }
    expect(second.messages.map((item) => item.content)).toEqual(['第一个问题', '第二个问题'])
  })

  it('retries the same question without duplicating it in the request history', async () => {
    const store = mountStore()
    await failFirstTurn(store, '唯一的问题')

    mocks.streamChat.mockResolvedValueOnce(
      streamOf([frame('token', { text: '完整回答' }), frame('done', donePayload)]),
    )
    await store.retry()

    expect(mocks.streamChat).toHaveBeenCalledTimes(2)
    const retryBody = mocks.streamChat.mock.calls[1][0] as { messages: ChatMessagePayload[] }
    expect(retryBody.messages.filter((item) => item.content === '唯一的问题')).toHaveLength(1)
    expect(retryBody.messages).toHaveLength(1)
    expect(retryBody.messages.at(-1)).toMatchObject({ role: 'user', content: '唯一的问题' })
  })

  it('keeps the failed answer visible until the retry finishes, then replaces it', async () => {
    const store = mountStore()
    await failFirstTurn(store, '问题')
    expect(store.messages[1].content).toBe('不完整回答')

    let release: (() => void) | null = null
    mocks.streamChat.mockImplementation(async () => {
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(encode(frame('token', { text: '重试成功' })))
          release = () => {
            controller.enqueue(encode(frame('done', donePayload)))
            controller.close()
          }
        },
      })
      return responseOf(stream)
    })

    const pending = store.retry()
    await settleUntil(() => store.streamingContent !== '')
    expect(store.messages.map((item) => item.content)).toEqual(['问题', '不完整回答'])

    release!()
    await pending

    expect(store.messages).toHaveLength(2)
    expect(store.messages[1].content).toBe('重试成功')
    expect(store.messages[1].errorCode).toBeNull()
  })

  it('never retries automatically after a failure', async () => {
    const store = mountStore()
    await failFirstTurn(store, '问题')
    await Promise.resolve()

    expect(mocks.streamChat).toHaveBeenCalledTimes(1)
    expect(store.canRetry).toBe(true)
  })

  it('exposes retry only for a failed turn the server did not forbid', async () => {
    const store = mountStore()
    await failFirstTurn(store, '问题')
    expect(store.canRetry).toBe(true)

    mocks.streamChat.mockResolvedValueOnce(
      streamOf([
        frame('error', {
          code: 'MODEL_RESPONSE_INVALID',
          message: '模型响应不符合引用协议',
          retryable: false,
          request_id: 'req-fail-2',
        }),
      ]),
    )
    store.question = '第二个问题'
    await store.send()
    expect(store.retryable).toBe(false)
    expect(store.canRetry).toBe(false)

    mocks.streamChat.mockResolvedValueOnce(
      streamOf([
        frame('error', {
          code: 'MODEL_TIMEOUT',
          message: '生成超时，请重试',
          retryable: true,
          request_id: 'req-fail-3',
        }),
      ]),
    )
    store.question = '第三个问题'
    const pending = store.send()
    expect(store.canRetry).toBe(false)
    await pending
    expect(store.canRetry).toBe(true)
  })

  it('ignores a retry with no user question to repeat', async () => {
    const store = mountStore()

    await store.retry()

    expect(mocks.streamChat).not.toHaveBeenCalled()
  })

  it('ignores a retry while a stream is active', async () => {
    const store = mountStore()
    await failFirstTurn(store, '问题')

    // 流保持打开，因此重试期间 streaming 一直为真
    mocks.streamChat.mockImplementation(async (_body: unknown, signal?: AbortSignal) =>
      streamOf([frame('token', { text: '进行中' })], signal),
    )
    const first = store.retry()
    await settleUntil(() => store.streamingContent !== '')
    await store.retry()

    expect(mocks.streamChat).toHaveBeenCalledTimes(2)
    store.stop()
    await first
  })
})

describe('chat store pre-stream recovery', () => {
  beforeEach(() => {
    mocks.streamChat.mockReset()
    mocks.fetchRetrievalOptions.mockReset()
  })

  it('offers a retry when the stream never opened', async () => {
    mocks.streamChat.mockRejectedValue(
      new ApiError('无法连接后端服务（网络错误或跨域被阻断）', { kind: 'network' }),
    )
    const store = mountStore()
    store.question = '唯一的问题'

    await store.send()

    expect(store.messages).toHaveLength(1)
    expect(store.streamError).not.toBeNull()
    expect(store.canRetry).toBe(true)
  })

  it('offers a retry for an HTTP failure and a protocol failure before the first token', async () => {
    mocks.streamChat.mockRejectedValueOnce(
      new ApiError('问答能力未配置', {
        kind: 'http',
        status: 503,
        code: 'LLM_PROVIDER_UNAVAILABLE',
        requestId: 'req-http-1',
      }),
    )
    const httpStore = mountStore()
    httpStore.question = '问题'
    await httpStore.send()
    expect(httpStore.canRetry).toBe(true)
    expect(httpStore.streamError?.requestId).toBe('req-http-1')

    mocks.streamChat.mockResolvedValueOnce(streamOf([frame('delta', { text: 'x' })]))
    const protocolStore = mountStore()
    protocolStore.question = '问题'
    await protocolStore.send()
    expect(protocolStore.streamError?.code).toBe('CHAT_STREAM_PROTOCOL_ERROR')
    expect(protocolStore.canRetry).toBe(true)
  })

  it('retries the failed question without duplicating it and clears the old error', async () => {
    mocks.streamChat.mockRejectedValueOnce(
      new ApiError('无法连接后端服务（网络错误或跨域被阻断）', { kind: 'network' }),
    )
    const store = mountStore()
    store.question = '唯一的问题'
    await store.send()
    expect(mocks.streamChat).toHaveBeenCalledTimes(1)

    mocks.streamChat.mockResolvedValueOnce(
      streamOf([frame('token', { text: '成功回答' }), frame('done', donePayload)]),
    )
    await store.retry()

    expect(mocks.streamChat).toHaveBeenCalledTimes(2)
    const retryBody = mocks.streamChat.mock.calls[1][0] as { messages: ChatMessagePayload[] }
    expect(retryBody.messages.filter((item) => item.content === '唯一的问题')).toHaveLength(1)
    expect(retryBody.messages).toHaveLength(1)

    expect(store.streamError).toBeNull()
    expect(store.messages).toHaveLength(2)
    expect(store.messages.filter((item) => item.role === 'user')).toHaveLength(1)
    expect(store.messages.filter((item) => item.role === 'assistant')).toHaveLength(1)
    expect(store.messages[1].content).toBe('成功回答')
    expect(store.canRetry).toBe(false)
  })

  it('treats retry as a safe no-op after a successful answer', async () => {
    mocks.streamChat.mockResolvedValue(
      streamOf([frame('token', { text: '回答' }), frame('done', donePayload)]),
    )
    const store = mountStore()
    store.question = '问题'
    await store.send()

    expect(store.canRetry).toBe(false)
    await store.retry()

    expect(mocks.streamChat).toHaveBeenCalledTimes(1)
    expect(store.messages).toHaveLength(2)
  })

  it('never offers a retry for an in-stream error the server marked as not retryable', async () => {
    mocks.streamChat.mockResolvedValueOnce(
      streamOf([
        frame('error', {
          code: 'MODEL_RESPONSE_INVALID',
          message: '模型响应不符合引用协议',
          retryable: false,
          request_id: 'req-nr-1',
        }),
      ]),
    )
    const store = mountStore()
    store.question = '问题'
    await store.send()

    expect(store.retryable).toBe(false)
    expect(store.canRetry).toBe(false)

    await store.retry()
    expect(mocks.streamChat).toHaveBeenCalledTimes(1)
  })
})

describe('chat store filters', () => {
  beforeEach(() => {
    mocks.streamChat.mockReset()
    mocks.fetchRetrievalOptions.mockReset()
  })

  it('updates, lists and clears the retrieval filters', () => {
    const store = mountStore()

    store.updateFilter('major', '计算机科学与技术')
    store.updateFilter('grade_year', 2026)

    expect(store.filters.major).toBe('计算机科学与技术')
    expect(store.filters.grade_year).toBe(2026)
    expect(store.activeFilters).toEqual([
      { key: 'major', label: '计算机科学与技术' },
      { key: 'grade_year', label: '2026' },
    ])

    store.clearFilters()

    expect(store.activeFilters).toEqual([])
    expect(store.filters).toEqual({
      major: null,
      grade_year: null,
      semester: null,
      doc_category: null,
    })
  })

  it('labels a document category filter with the real option label', async () => {
    mocks.fetchRetrievalOptions.mockResolvedValue({
      majors: [],
      grade_years: [],
      semesters: [],
      doc_categories: [{ value: 'degree_plan', label: '培养方案' }],
      active_dataset_version: null,
      demo_available: true,
    })
    const store = mountStore()
    await store.loadOptions()

    store.updateFilter('doc_category', 'degree_plan')

    expect(store.activeFilters).toEqual([{ key: 'doc_category', label: '培养方案' }])
  })

  it('sends the current filters with the request', async () => {
    mocks.streamChat.mockResolvedValue(streamOf([frame('done', donePayload)]))
    const store = mountStore()
    store.updateFilter('semester', '2026-2027-1')
    store.updateFilter('grade_year', 2026)
    store.question = '问题'

    await store.send()

    const body = mocks.streamChat.mock.calls[0][0] as { filters: unknown }
    expect(body.filters).toEqual({
      major: null,
      grade_year: 2026,
      semester: '2026-2027-1',
      doc_category: null,
    })
  })
})
