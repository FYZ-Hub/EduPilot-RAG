import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { ApiError } from '@/api/client'
import type { ChatCitation } from '@/api/chat'
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
  return { ok: true, status: 200, body: stream } as unknown as Response
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
