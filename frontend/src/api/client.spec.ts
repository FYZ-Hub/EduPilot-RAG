import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  API_BASE_URL,
  ApiError,
  deleteRequest,
  getJson,
  postForm,
  postJson,
  toApiError,
} from './client'

interface FakeResponseInit {
  status?: number
  body?: unknown
  text?: string
}

function fakeResponse({ status = 200, body = null, text }: FakeResponseInit) {
  const payload = text !== undefined ? text : body === null ? '' : JSON.stringify(body)
  return {
    status,
    ok: status >= 200 && status < 300,
    text: async () => payload,
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

/** 断言请求失败，并把抛出的错误作为 :class:`ApiError` 返回。 */
function fatal(promise: Promise<unknown>): Promise<ApiError> {
  return promise.then(
    () => {
      throw new Error('expected the request to fail')
    },
    (caught) => caught as ApiError,
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('api client', () => {
  it('exposes a single centralized base URL', () => {
    expect(API_BASE_URL).toBe('http://localhost:8000/api')
    expect(API_BASE_URL.endsWith('/')).toBe(false)
  })

  it('parses the unified error body into a machine-readable ApiError', async () => {
    stubFetch(
      fakeResponse({
        status: 422,
        body: {
          code: 'DOCUMENT_INVALID_TYPE',
          message: '不支持的文件类型',
          details: { field: 'file' },
          request_id: 'req-abc-123',
        },
      }),
    )

    const error = await fatal(getJson('/documents'))

    expect(error).toBeInstanceOf(ApiError)
    expect(error.kind).toBe('http')
    expect(error.status).toBe(422)
    expect(error.code).toBe('DOCUMENT_INVALID_TYPE')
    expect(error.message).toBe('不支持的文件类型')
    expect(error.requestId).toBe('req-abc-123')
    expect(error.details).toEqual({ field: 'file' })
    expect(error.requestIdLabel).toBe('请求编号：req-abc-123')
  })

  it('falls back to an HTTP status code when the body is not the unified shape', async () => {
    stubFetch(fakeResponse({ status: 503, text: '<html>gateway</html>' }))

    const error = await fatal(getJson('/documents'))

    expect(error).toBeInstanceOf(ApiError)
    expect(error.code).toBeUndefined()
    expect(error.status).toBe(503)
    expect(error.message).toContain('503')
  })

  it('distinguishes network failures and never fabricates a request id', async () => {
    stubFetch(new TypeError('Failed to fetch'))

    const error = await fatal(getJson('/documents'))

    expect(error).toBeInstanceOf(ApiError)
    expect(error.kind).toBe('network')
    expect(error.requestId).toBeUndefined()
    expect(error.requestIdLabel).toBe('未获得服务端请求编号')
  })

  it('reports aborted requests as a distinct kind', async () => {
    const abortError = new Error('aborted')
    abortError.name = 'AbortError'
    stubFetch(abortError)

    const controller = new AbortController()
    const error = await fatal(getJson('/documents', controller.signal))

    expect(error.kind).toBe('aborted')
    expect(error.isAborted).toBe(true)
    expect(error.requestIdLabel).toBe('未获得服务端请求编号')
  })

  it('returns undefined for 204 responses', async () => {
    stubFetch(fakeResponse({ status: 204 }))

    await expect(deleteRequest('/documents/doc-1')).resolves.toBeUndefined()
  })

  it('sends GET with only the Accept header and the centralized base URL', async () => {
    const mock = stubFetch(fakeResponse({ body: { items: [] } }))

    await getJson('/documents')

    const [url, init] = mock.mock.calls[0]
    expect(url).toBe(`${API_BASE_URL}/documents`)
    expect(init.method).toBe('GET')
    expect(init.headers.Accept).toBe('application/json')
    expect(init.headers['Content-Type']).toBeUndefined()
  })

  it('sends JSON POST with an explicit content type', async () => {
    const mock = stubFetch(fakeResponse({ body: { ok: true } }))

    await postJson('/demo/seed')

    const [, init] = mock.mock.calls[0]
    expect(init.method).toBe('POST')
    expect(init.headers['Content-Type']).toBe('application/json')
    expect(init.body).toBe('{}')
  })

  it('sends multipart POST without setting Content-Type so the browser adds the boundary', async () => {
    const mock = stubFetch(fakeResponse({ status: 202, body: { document_id: 'doc-1' } }))
    const form = new FormData()
    form.append('file', new File(['data'], 'plan.pdf'), 'plan.pdf')

    await postForm('/documents', form)

    const [, init] = mock.mock.calls[0]
    expect(init.method).toBe('POST')
    expect(init.body).toBe(form)
    expect(init.headers['Content-Type']).toBeUndefined()
  })

  it('normalizes unknown thrown values through toApiError', () => {
    const error = toApiError('boom')

    expect(error).toBeInstanceOf(ApiError)
    expect(error.kind).toBe('network')
    expect(error.requestId).toBeUndefined()
  })
})
