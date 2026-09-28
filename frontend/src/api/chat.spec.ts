import { afterEach, describe, expect, it, vi } from 'vitest'

import { API_BASE_URL, ApiError } from './client'
import {
  buildChatRequestMessages,
  emptyChatFilters,
  MAX_CHAT_MESSAGE_CHARS,
  MAX_CHAT_MESSAGES,
  MAX_CHAT_TOTAL_CHARS,
  streamChat,
  type ChatMessagePayload,
  type ChatRequestBody,
} from './chat'

function okStreamResponse(): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.close()
    },
  })
  return { ok: true, status: 200, body: stream, text: async () => '' } as unknown as Response
}

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    body: null,
    text: async () => (typeof body === 'string' ? body : JSON.stringify(body)),
  } as unknown as Response
}

function stubFetch(response: Response | Error): ReturnType<typeof vi.fn> {
  const mock = vi.fn()
  if (response instanceof Error) {
    mock.mockRejectedValue(response)
  } else {
    mock.mockResolvedValue(response)
  }
  vi.stubGlobal('fetch', mock)
  return mock
}

function body(): ChatRequestBody {
  return {
    messages: [{ role: 'user', content: '计算机科学专业需要多少毕业学分？' }],
    filters: emptyChatFilters(),
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('chat streaming API', () => {
  it('posts messages and filters to the centralized stream endpoint', async () => {
    const mock = stubFetch(okStreamResponse())
    const payload = body()

    await streamChat(payload)

    const [url, init] = mock.mock.calls[0]
    expect(url).toBe(`${API_BASE_URL}/chat/stream`)
    expect(init.method).toBe('POST')
    expect(init.headers.Accept).toBe('text/event-stream')
    expect(init.headers['Content-Type']).toBe('application/json')
    expect(init.body).toBe(JSON.stringify(payload))
  })

  it('sends exactly messages and filters, never inventing session fields', async () => {
    const mock = stubFetch(okStreamResponse())

    await streamChat(body())

    const parsed = JSON.parse(mock.mock.calls[0][1].body as string)
    expect(Object.keys(parsed).sort()).toEqual(['filters', 'messages'])
    expect(parsed.messages).toEqual([
      { role: 'user', content: '计算机科学专业需要多少毕业学分？' },
    ])
  })

  it('submits null, integer and string filters verbatim', async () => {
    const mock = stubFetch(okStreamResponse())

    await streamChat({
      messages: [{ role: 'user', content: '问题' }],
      filters: {
        major: '计算机科学与技术',
        grade_year: 2026,
        semester: '2026-2027-1',
        doc_category: null,
      },
    })

    const parsed = JSON.parse(mock.mock.calls[0][1].body as string)
    expect(parsed.filters).toEqual({
      major: '计算机科学与技术',
      grade_year: 2026,
      semester: '2026-2027-1',
      doc_category: null,
    })
    expect(typeof parsed.filters.grade_year).toBe('number')
  })

  it('defaults every filter to null', () => {
    expect(emptyChatFilters()).toEqual({
      major: null,
      grade_year: null,
      semester: null,
      doc_category: null,
    })
  })

  it('keeps the real server request id for a pre-stream JSON error', async () => {
    stubFetch(
      jsonResponse(503, {
        code: 'LLM_PROVIDER_UNAVAILABLE',
        message: '问答能力未配置',
        details: { reason: 'llm_not_configured' },
        request_id: 'req-chat-1',
      }),
    )

    const error = await streamChat(body()).catch((caught) => caught)

    expect(error).toBeInstanceOf(ApiError)
    expect(error.kind).toBe('http')
    expect(error.status).toBe(503)
    expect(error.code).toBe('LLM_PROVIDER_UNAVAILABLE')
    expect(error.requestId).toBe('req-chat-1')
    expect(error.requestIdLabel).toBe('请求编号：req-chat-1')
  })

  it('falls back to the HTTP status when the pre-stream body is not the unified shape', async () => {
    stubFetch(jsonResponse(500, '<html>boom</html>'))

    const error = await streamChat(body()).catch((caught) => caught)

    expect(error.code).toBeUndefined()
    expect(error.status).toBe(500)
    expect(error.requestId).toBeUndefined()
  })

  it('reports network failures without fabricating a request id', async () => {
    stubFetch(new TypeError('Failed to fetch'))

    const error = await streamChat(body()).catch((caught) => caught)

    expect(error.kind).toBe('network')
    expect(error.requestId).toBeUndefined()
    expect(error.requestIdLabel).toBe('未获得服务端请求编号')
  })

  it('reports an aborted stream request as aborted', async () => {
    const abortError = new Error('aborted')
    abortError.name = 'AbortError'
    stubFetch(abortError)

    const controller = new AbortController()
    const error = await streamChat(body(), controller.signal).catch((caught) => caught)

    expect(error.isAborted).toBe(true)
  })
})

describe('chat request history selection', () => {
  function message(role: 'user' | 'assistant', length: number): ChatMessagePayload {
    return { role, content: 'x'.repeat(length) }
  }

  function totalChars(messages: readonly ChatMessagePayload[]): number {
    return messages.reduce((sum, item) => sum + item.content.length, 0)
  }

  it('keeps the whole conversation when it already fits', () => {
    const history = [
      message('assistant', 2000),
      message('user', 4000),
      message('assistant', 4000),
      message('user', 2000),
    ]

    const selected = buildChatRequestMessages(history)

    expect(totalChars(history)).toBe(MAX_CHAT_TOTAL_CHARS)
    expect(selected).toEqual(history)
    expect(selected.at(-1)?.role).toBe('user')
  })

  it('drops the oldest history first once the total limit is exceeded', () => {
    const history = [
      message('assistant', 2001),
      message('user', 4000),
      message('assistant', 4000),
      message('user', 2000),
    ]

    const selected = buildChatRequestMessages(history)

    expect(totalChars(history)).toBe(MAX_CHAT_TOTAL_CHARS + 1)
    expect(selected).toEqual(history.slice(1))
    expect(totalChars(selected)).toBeLessThanOrEqual(MAX_CHAT_TOTAL_CHARS)
  })

  it('caps the request at the message-count limit while preserving order', () => {
    const history = Array.from({ length: 14 }, (_, index) =>
      message(index % 2 === 0 ? 'assistant' : 'user', 100),
    )

    const selected = buildChatRequestMessages(history)

    expect(selected).toHaveLength(MAX_CHAT_MESSAGES)
    expect(selected).toEqual(history.slice(-MAX_CHAT_MESSAGES))
  })

  it('drops an over-long historical message instead of truncating it', () => {
    const history = [
      message('assistant', MAX_CHAT_MESSAGE_CHARS + 1),
      message('user', 10),
    ]

    const selected = buildChatRequestMessages(history)

    expect(selected).toEqual([history[1]])
    expect(selected.some((item) => item.content.length > MAX_CHAT_MESSAGE_CHARS)).toBe(false)
  })

  it('keeps the current question last and never rewrites any content', () => {
    const history = [
      message('user', 5000),
      message('assistant', 150),
      message('user', 60),
    ]

    const selected = buildChatRequestMessages(history)

    expect(selected.at(-1)).toEqual(history[2])
    expect(selected.every((item) => history.includes(item))).toBe(true)
  })

  it('returns an empty request for an empty conversation', () => {
    expect(buildChatRequestMessages([])).toEqual([])
  })

  it('always satisfies the backend request contract', () => {
    const history = Array.from({ length: 14 }, (_, index) =>
      message(index % 2 === 0 ? 'assistant' : 'user', 1500 + index),
    )

    const selected = buildChatRequestMessages(history)

    expect(selected.length).toBeGreaterThanOrEqual(1)
    expect(selected.length).toBeLessThanOrEqual(MAX_CHAT_MESSAGES)
    expect(selected.every((item) => item.content.length <= MAX_CHAT_MESSAGE_CHARS)).toBe(true)
    expect(totalChars(selected)).toBeLessThanOrEqual(MAX_CHAT_TOTAL_CHARS)
    expect(selected.at(-1)?.role).toBe('user')
    expect((selected.at(-1)?.content ?? '').trim().length).toBeGreaterThan(0)
  })
})
