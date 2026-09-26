/**
 * 集中式 API 客户端配置。
 * Base URL 只在此处定义，其他模块不得自行拼接主机地址。
 */
const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL

export const API_BASE_URL: string = (configuredBaseUrl ?? 'http://localhost:8000/api').replace(
  /\/+$/,
  '',
)

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  let response: Response

  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      headers: { Accept: 'application/json' },
      signal,
    })
  } catch {
    throw new ApiError('无法连接后端服务')
  }

  if (!response.ok) {
    throw new ApiError(`请求失败（HTTP ${response.status}）`, response.status)
  }

  return (await response.json()) as T
}
