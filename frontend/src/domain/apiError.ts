/**
 * API 错误展示派生（UI_SPEC 4.3）。
 *
 * 机器错误码与请求编号的统一口径：``ErrorAlert`` 与上传队列项共用同一处实现，
 * 避免出现两套会各自漂移的错误展示逻辑。
 * 纯函数，不产生任何后端不存在的数据。
 */

import type { ApiError } from '@/api/client'

/**
 * 展示用的机器错误码：优先服务端 ``code``；网络 / 取消这类没有服务端响应的情况
 * 使用稳定的前端标识，绝不用 HTTP 状态码冒充服务端业务错误码。
 */
export function apiErrorCodeLabel(error: ApiError | null | undefined): string | null {
  if (!error) {
    return null
  }
  if (error.code) {
    return error.code
  }
  if (error.kind === 'network') {
    return 'NETWORK_ERROR'
  }
  if (error.kind === 'aborted') {
    return 'REQUEST_ABORTED'
  }
  return error.status ? `HTTP_${error.status}` : null
}

/** 服务端错误显示真实 request ID；纯网络失败、取消或 CORS 阻断明确显示未获得，前端不伪造。 */
export function apiErrorRequestIdLabel(error: ApiError | null | undefined): string {
  return error?.requestIdLabel ?? '未获得服务端请求编号'
}
