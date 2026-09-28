/**
 * 集中式 API 客户端（UI_SPEC 10）。
 *
 * - Base URL **只在此处定义**，其他模块不得自行拼接主机地址。
 * - 统一解析后端错误体 `{code, message, details, request_id}`（app/core/errors.py）。
 * - 网络失败、请求取消（AbortError）与 HTTP/服务端错误严格区分。
 * - 不在任何位置记录文件正文、API Key、路径或敏感响应；不打印请求体。
 */

const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL

export const API_BASE_URL: string = (configuredBaseUrl ?? 'http://localhost:8000/api').replace(
  /\/+$/,
  '',
)

/** 后端统一错误体的真实形状。 */
export interface ApiErrorBody {
  code: string
  message: string
  details: Record<string, unknown>
  request_id: string
}

export type ApiErrorKind = 'http' | 'network' | 'aborted'

function isApiErrorBody(value: unknown): value is ApiErrorBody {
  if (typeof value !== 'object' || value === null) {
    return false
  }
  const body = value as Record<string, unknown>
  return typeof body.code === 'string' && typeof body.message === 'string'
}

/**
 * 归一化的 API 错误。
 *
 * ``requestId`` 只来自服务端错误体；前端**绝不**生成或伪造请求编号。
 */
export class ApiError extends Error {
  readonly kind: ApiErrorKind
  readonly status?: number
  readonly code?: string
  readonly requestId?: string
  readonly details?: Record<string, unknown>

  constructor(
    message: string,
    options: {
      kind?: ApiErrorKind
      status?: number
      code?: string
      requestId?: string
      details?: Record<string, unknown>
    } = {},
  ) {
    super(message)
    this.name = 'ApiError'
    this.kind = options.kind ?? 'http'
    this.status = options.status
    this.code = options.code
    this.requestId = options.requestId
    this.details = options.details
  }

  /** 服务端错误展示真实 request_id；网络/取消错误明确说明未获得。 */
  get requestIdLabel(): string {
    return this.requestId ? `请求编号：${this.requestId}` : '未获得服务端请求编号'
  }

  get isAborted(): boolean {
    return this.kind === 'aborted'
  }
}

export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) {
    return error
  }
  return new ApiError(error instanceof Error ? error.message : '未知错误', { kind: 'network' })
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'DELETE'
  query?: Record<string, string | number | boolean | null | undefined>
  json?: unknown
  form?: FormData
  signal?: AbortSignal
}

function buildUrl(path: string, query?: RequestOptions['query']): string {
  const url = `${API_BASE_URL}${path}`
  if (!query) {
    return url
  }
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value === null || value === undefined) {
      continue
    }
    params.set(key, String(value))
  }
  const search = params.toString()
  return search ? `${url}?${search}` : url
}

/**
 * 执行一次请求并返回已解析的 JSON。
 *
 * 204 返回 ``undefined``；非 2xx 一律抛出带机器错误码的 :class:`ApiError`。
 */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  let body: BodyInit | undefined

  if (options.form) {
    // multipart：绝不能手动设置 Content-Type，浏览器需要自行补 boundary
    body = options.form
  } else if (options.json !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(options.json)
  }

  let response: Response
  try {
    response = await fetch(buildUrl(path, options.query), {
      method: options.method ?? 'GET',
      headers,
      body,
      signal: options.signal,
    })
  } catch (error) {
    if (options.signal?.aborted || (error instanceof Error && error.name === 'AbortError')) {
      throw new ApiError('请求已取消', { kind: 'aborted' })
    }
    // 连接失败、DNS、CORS 预检被阻断都会走到这里
    throw new ApiError('无法连接后端服务（网络错误或跨域被阻断）', { kind: 'network' })
  }

  if (response.status === 204) {
    return undefined as T
  }

  const text = await response.text()
  let parsed: unknown
  try {
    parsed = text ? JSON.parse(text) : null
  } catch {
    parsed = null
  }

  if (!response.ok) {
    if (isApiErrorBody(parsed)) {
      throw new ApiError(parsed.message, {
        kind: 'http',
        status: response.status,
        code: parsed.code,
        requestId: parsed.request_id,
        details: parsed.details,
      })
    }
    throw new ApiError(`请求失败（HTTP ${response.status}）`, {
      kind: 'http',
      status: response.status,
    })
  }

  return parsed as T
}

export function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  return request<T>(path, { signal })
}

export function postJson<T>(path: string, json?: unknown, signal?: AbortSignal): Promise<T> {
  return request<T>(path, { method: 'POST', json: json ?? {}, signal })
}

export function postForm<T>(path: string, form: FormData, signal?: AbortSignal): Promise<T> {
  return request<T>(path, { method: 'POST', form, signal })
}

export function deleteRequest<T = void>(path: string, signal?: AbortSignal): Promise<T> {
  return request<T>(path, { method: 'DELETE', signal })
}
