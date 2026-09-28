/**
 * ``POST /api/chat/stream`` 的请求契约与 SSE 载荷类型（PRODUCT_SPEC 6.3）。
 *
 * 请求体**严格**只有 ``messages`` 与 ``filters``：不存在 session_id / conversation_id，
 * 后端也没有服务端会话。``streamChat`` 只负责建立流，正文由 Chat Store 逐块解析。
 */

import { postStream } from './client'

/** ``done.outcome`` 只允许这三种取值。 */
export const CHAT_OUTCOMES = ['answered', 'refused', 'conflict'] as const
export type ChatOutcome = (typeof CHAT_OUTCOMES)[number]

/** 后端 messages 的数量上限（app/chat/schemas.py）。 */
export const MIN_CHAT_MESSAGES = 1
export const MAX_CHAT_MESSAGES = 10

/** 单条消息的字符上限（app/chat/schemas.py MAX_MESSAGE_CHARS）。 */
export const MAX_CHAT_MESSAGE_CHARS = 4000
/** 全部消息的字符总上限（app/chat/schemas.py MAX_TOTAL_CHARS）。 */
export const MAX_CHAT_TOTAL_CHARS = 12000

/** 前端本地校验错误码：问题超过单条上限时**就地提示**，不静默截断、不发起请求。 */
export const CHAT_QUESTION_TOO_LONG = 'CHAT_QUESTION_TOO_LONG'

export interface ChatMessagePayload {
  role: 'user' | 'assistant'
  content: string
}

/** 过滤字段固定四项；**不硬编码**任何专业、年级、学期或分类取值。 */
export interface ChatFilters {
  major: string | null
  grade_year: number | null
  semester: string | null
  doc_category: string | null
}

export interface ChatRequestBody {
  messages: ChatMessagePayload[]
  filters: ChatFilters
}

/** ``event: citation`` 的 13 个字段（不含分数、路径或内部诊断）。 */
export interface ChatCitation {
  citation_index: number
  chunk_id: string
  doc_id: string
  file_name: string
  document_version: string | null
  effective_from: string | null
  dataset_version: string | null
  page_number: number | null
  sheet_name: string | null
  row_start: number | null
  row_end: number | null
  section_title: string | null
  quote: string
}

export interface ChatDone {
  request_id: string
  outcome: ChatOutcome
  reason_code: string | null
  citation_count: number
}

export interface ChatStreamErrorPayload {
  code: string
  message: string
  retryable: boolean
  request_id: string
}

export function emptyChatFilters(): ChatFilters {
  return { major: null, grade_year: null, semester: null, doc_category: null }
}

/**
 * 从完整会话中挑选本次请求要发送的消息，保证满足后端契约：
 * ``1 <= 数量 <= 10``、``每条 <= 4000`` 字符、``总字符 <= 12000``、最后一条仍是当前问题。
 *
 * - 超过单条上限的消息**整条丢弃**，绝不截断或改写任何正文；
 * - 超过总上限时从**最旧**的历史开始删除，直到满足上限；
 * - 顺序保持不变，当前问题（最后一条）始终保留 —— 调用方必须在此之前校验它不超限。
 */
export function buildChatRequestMessages(
  messages: readonly ChatMessagePayload[],
): ChatMessagePayload[] {
  if (messages.length === 0) {
    return []
  }
  const withinSingleLimit = messages.filter(
    (message) => message.content.length <= MAX_CHAT_MESSAGE_CHARS,
  )
  const selected = withinSingleLimit.slice(-MAX_CHAT_MESSAGES)

  let start = 0
  let total = selected.reduce((sum, message) => sum + message.content.length, 0)
  while (total > MAX_CHAT_TOTAL_CHARS && start < selected.length - 1) {
    total -= selected[start].content.length
    start += 1
  }
  return selected.slice(start)
}

/**
 * 建立 SSE 问答流。成功时返回 ``Response``，调用方必须自行读取 ``response.body``；
 * 开流前的 4xx/5xx 抛出带 ``request_id`` 的 :class:`ApiError`。
 */
export function streamChat(body: ChatRequestBody, signal?: AbortSignal): Promise<Response> {
  return postStream('/chat/stream', body, signal)
}
