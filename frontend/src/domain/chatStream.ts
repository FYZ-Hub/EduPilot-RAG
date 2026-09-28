/**
 * ``POST /api/chat/stream`` 的 SSE 解析器（UI_SPEC 6.5、PRODUCT_SPEC 6.3）。
 *
 * 纯 TypeScript、不依赖 Vue，便于确定性测试。只接受四种事件：
 * ``token`` / ``citation`` / ``done`` / ``error``；其它一律视为协议损坏。
 *
 * 关键保证：
 * - 用流式 ``TextDecoder`` 解码，**UTF-8 字符跨字节边界不会被切坏**；
 * - 按行缓冲，网络 chunk 可以任意拆分事件、也可以在单个 chunk 内含多个事件；
 * - 同时支持 LF、CRLF 与孤立 CR；``:`` 开头的注释（保活）被忽略；
 * - ``done`` / ``error`` 之后不再接受任何业务事件；重复终止同样报错；
 * - 解析器**不会**替服务端补事件，也**不会**根据正文里的 ``[1]`` 生成引用。
 *
 * 无终止事件的 EOF 由调用方判定：:attr:`ChatStreamParser.sawTerminal` 为 ``false``。
 */

import type {
  ChatCitation,
  ChatDone,
  ChatOutcome,
  ChatStreamErrorPayload,
} from '@/api/chat'
import { CHAT_OUTCOMES } from '@/api/chat'

/** 前端协议错误码；它**不是**服务端业务错误码，也不携带 request_id。 */
export const CHAT_STREAM_PROTOCOL_ERROR = 'CHAT_STREAM_PROTOCOL_ERROR'

export type ChatStreamProtocolReason =
  | 'empty_data'
  | 'invalid_json'
  | 'unknown_event'
  | 'invalid_payload'
  | 'duplicate_terminal'
  | 'event_after_terminal'

const REASON_MESSAGES: Record<ChatStreamProtocolReason, string> = {
  empty_data: '问答流返回了空数据帧',
  invalid_json: '问答流返回了无法解析的数据帧',
  unknown_event: '问答流返回了未知事件类型',
  invalid_payload: '问答流返回的数据结构与协议不一致',
  duplicate_terminal: '问答流出现了重复的终止事件',
  event_after_terminal: '问答流在终止事件之后仍然发送了业务事件',
}

/** 协议损坏错误；只带稳定的前端错误码与原因，绝不回显帧内容或堆栈。 */
export class ChatStreamProtocolError extends Error {
  readonly code = CHAT_STREAM_PROTOCOL_ERROR
  readonly reason: ChatStreamProtocolReason

  constructor(reason: ChatStreamProtocolReason) {
    super(REASON_MESSAGES[reason])
    this.name = 'ChatStreamProtocolError'
    this.reason = reason
  }
}

export type ChatStreamEvent =
  | { name: 'token'; text: string }
  | { name: 'citation'; citation: ChatCitation }
  | { name: 'done'; done: ChatDone }
  | { name: 'error'; error: ChatStreamErrorPayload }

const KNOWN_EVENTS = ['token', 'citation', 'done', 'error'] as const

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function requireString(payload: Record<string, unknown>, key: string): string {
  const value = payload[key]
  if (typeof value !== 'string') {
    throw new ChatStreamProtocolError('invalid_payload')
  }
  return value
}

function optionalString(payload: Record<string, unknown>, key: string): string | null {
  const value = payload[key]
  if (value === null || value === undefined) {
    return null
  }
  if (typeof value !== 'string') {
    throw new ChatStreamProtocolError('invalid_payload')
  }
  return value
}

function optionalNumber(payload: Record<string, unknown>, key: string): number | null {
  const value = payload[key]
  if (value === null || value === undefined) {
    return null
  }
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new ChatStreamProtocolError('invalid_payload')
  }
  return value
}

function toCitation(payload: Record<string, unknown>): ChatCitation {
  const index = optionalNumber(payload, 'citation_index')
  if (index === null || !Number.isInteger(index) || index < 1) {
    throw new ChatStreamProtocolError('invalid_payload')
  }
  return {
    citation_index: index,
    chunk_id: requireString(payload, 'chunk_id'),
    doc_id: requireString(payload, 'doc_id'),
    file_name: requireString(payload, 'file_name'),
    document_version: optionalString(payload, 'document_version'),
    effective_from: optionalString(payload, 'effective_from'),
    dataset_version: optionalString(payload, 'dataset_version'),
    page_number: optionalNumber(payload, 'page_number'),
    sheet_name: optionalString(payload, 'sheet_name'),
    row_start: optionalNumber(payload, 'row_start'),
    row_end: optionalNumber(payload, 'row_end'),
    section_title: optionalString(payload, 'section_title'),
    quote: requireString(payload, 'quote'),
  }
}

function toDone(payload: Record<string, unknown>): ChatDone {
  const rawOutcome = payload.outcome
  if (typeof rawOutcome !== 'string' || !(CHAT_OUTCOMES as readonly string[]).includes(rawOutcome)) {
    throw new ChatStreamProtocolError('invalid_payload')
  }
  const count = optionalNumber(payload, 'citation_count')
  if (count === null || !Number.isInteger(count) || count < 0) {
    throw new ChatStreamProtocolError('invalid_payload')
  }
  return {
    request_id: requireString(payload, 'request_id'),
    outcome: rawOutcome as ChatOutcome,
    reason_code: optionalString(payload, 'reason_code'),
    citation_count: count,
  }
}

function toErrorPayload(payload: Record<string, unknown>): ChatStreamErrorPayload {
  const retryable = payload.retryable
  if (typeof retryable !== 'boolean') {
    throw new ChatStreamProtocolError('invalid_payload')
  }
  return {
    code: requireString(payload, 'code'),
    message: requireString(payload, 'message'),
    retryable,
    request_id: requireString(payload, 'request_id'),
  }
}

export class ChatStreamParser {
  private readonly decoder = new TextDecoder('utf-8')
  private buffer = ''
  private eventName = ''
  private dataLines: string[] = []
  private terminal = false

  /** 是否已经收到本流的终止事件（``done`` 或 ``error``）。 */
  get sawTerminal(): boolean {
    return this.terminal
  }

  /** 喂入一个网络 chunk，返回本次新完成的**全部**事件。 */
  push(chunk: Uint8Array | string): ChatStreamEvent[] {
    this.buffer += typeof chunk === 'string' ? chunk : this.decoder.decode(chunk, { stream: true })
    return this.drain()
  }

  /**
   * 流结束时调用：处理 EOF 处孤立的 CR 行终止符，返回由此产生的事件。
   * 未以空行结束的残缺帧一律丢弃，由调用方依据 :attr:`sawTerminal` 判定连接中断。
   */
  finish(): ChatStreamEvent[] {
    this.buffer += this.decoder.decode()
    if (this.buffer.endsWith('\r')) {
      // EOF 的 CR 不可能是 CRLF 的前半，必然是本行的终止符
      this.buffer = `${this.buffer.slice(0, -1)}\n`
    }
    const events = this.drain()
    this.buffer = ''
    return events
  }

  private drain(): ChatStreamEvent[] {
    const events: ChatStreamEvent[] = []
    let line = this.readLine()
    while (line !== null) {
      events.push(...this.handleLine(line))
      line = this.readLine()
    }
    return events
  }

  private readLine(): string | null {
    for (let index = 0; index < this.buffer.length; index += 1) {
      const char = this.buffer[index]
      if (char === '\n') {
        const line = this.buffer.slice(0, index)
        this.buffer = this.buffer.slice(index + 1)
        return line
      }
      if (char === '\r') {
        if (index === this.buffer.length - 1) {
          // 可能是被拆开的 CRLF，等待下一个 chunk 再决定
          return null
        }
        const line = this.buffer.slice(0, index)
        this.buffer =
          this.buffer[index + 1] === '\n' ? this.buffer.slice(index + 2) : this.buffer.slice(index + 1)
        return line
      }
    }
    return null
  }

  private handleLine(line: string): ChatStreamEvent[] {
    if (line === '') {
      return this.dispatch()
    }
    if (line.startsWith(':')) {
      return [] // SSE comment（保活）：忽略
    }
    const separator = line.indexOf(':')
    const field = separator === -1 ? line : line.slice(0, separator)
    let value = separator === -1 ? '' : line.slice(separator + 1)
    if (value.startsWith(' ')) {
      value = value.slice(1)
    }
    if (field === 'event') {
      this.eventName = value
    } else if (field === 'data') {
      this.dataLines.push(value)
    }
    return [] // id / retry 等字段忽略
  }

  private dispatch(): ChatStreamEvent[] {
    const name = this.eventName
    const raw = this.dataLines.join('\n')
    this.eventName = ''
    this.dataLines = []

    if (name === '' && raw === '') {
      return [] // 只有注释或空帧
    }
    if (!(KNOWN_EVENTS as readonly string[]).includes(name)) {
      throw new ChatStreamProtocolError('unknown_event')
    }
    if (this.terminal) {
      throw new ChatStreamProtocolError(
        name === 'done' || name === 'error' ? 'duplicate_terminal' : 'event_after_terminal',
      )
    }
    if (raw === '') {
      throw new ChatStreamProtocolError('empty_data')
    }

    let parsed: unknown
    try {
      parsed = JSON.parse(raw)
    } catch {
      throw new ChatStreamProtocolError('invalid_json')
    }
    if (!isRecord(parsed)) {
      throw new ChatStreamProtocolError('invalid_payload')
    }

    if (name === 'token') {
      const text = parsed.text
      if (typeof text !== 'string') {
        throw new ChatStreamProtocolError('invalid_payload')
      }
      return [{ name: 'token', text }]
    }
    if (name === 'citation') {
      return [{ name: 'citation', citation: toCitation(parsed) }]
    }
    if (name === 'done') {
      const done = toDone(parsed)
      this.terminal = true
      return [{ name: 'done', done }]
    }
    const error = toErrorPayload(parsed)
    this.terminal = true
    return [{ name: 'error', error }]
  }
}
