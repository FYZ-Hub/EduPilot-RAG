import { describe, expect, it } from 'vitest'

import type { ChatCitation } from '@/api/chat'

import { ChatStreamParser, ChatStreamProtocolError } from './chatStream'

const encoder = new TextEncoder()

function encode(text: string): Uint8Array {
  return encoder.encode(text)
}

function frame(name: string, payload: unknown, terminator = '\n\n'): string {
  return `event: ${name}\ndata: ${JSON.stringify(payload)}${terminator}`
}

function citation(index: number, chunkId = `chunk-${index}`): ChatCitation {
  return {
    citation_index: index,
    chunk_id: chunkId,
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

function drain(parser: ChatStreamParser, chunks: Uint8Array[]): unknown[] {
  const events: unknown[] = []
  for (const chunk of chunks) {
    events.push(...parser.push(chunk))
  }
  return events
}

describe('chat stream parser framing', () => {
  it('parses a standard token → citation → done stream', () => {
    const parser = new ChatStreamParser()
    const events = drain(parser, [
      encode(
        frame('token', { text: '毕业' }) +
          frame('citation', citation(1)) +
          frame('done', donePayload),
      ),
    ])
    parser.finish()

    expect(events.map((event) => (event as { name: string }).name)).toEqual([
      'token',
      'citation',
      'done',
    ])
    expect(events[0]).toEqual({ name: 'token', text: '毕业' })
    expect(events[1]).toEqual({ name: 'citation', citation: citation(1) })
    expect(events[2]).toEqual({ name: 'done', done: donePayload })
    expect(parser.sawTerminal).toBe(true)
  })

  it('parses one event split across several network chunks', () => {
    const parser = new ChatStreamParser()
    const text = frame('token', { text: '拆分' })

    expect(parser.push(encode(text.slice(0, 3)))).toEqual([])
    expect(parser.push(encode(text.slice(3, 12)))).toEqual([])
    expect(parser.push(encode(text.slice(12)))).toEqual([{ name: 'token', text: '拆分' }])
  })

  it('parses several events delivered in one chunk', () => {
    const parser = new ChatStreamParser()
    const events = parser.push(
      encode(frame('token', { text: 'A' }) + frame('token', { text: 'B' }) + frame('token', { text: 'C' })),
    )

    expect(events).toEqual([
      { name: 'token', text: 'A' },
      { name: 'token', text: 'B' },
      { name: 'token', text: 'C' },
    ])
  })

  it('keeps a multi-byte UTF-8 character split across two byte chunks', () => {
    const parser = new ChatStreamParser()
    const bytes = encode(frame('token', { text: '计算机' }))
    // '算' 的 UTF-8 编码为 3 字节，这里在该字符中间切分
    const cut = bytes.indexOf(0xe7)
    expect(cut).toBeGreaterThan(0)

    const first = parser.push(bytes.slice(0, cut + 1))
    const second = parser.push(bytes.slice(cut + 1))

    expect(first).toEqual([])
    expect(second).toEqual([{ name: 'token', text: '计算机' }])
  })

  it('accepts CRLF framing and ignores SSE comments', () => {
    const parser = new ChatStreamParser()
    const events = drain(parser, [
      encode(': keep-alive\r\n\r\n'),
      encode(frame('token', { text: 'CRLF' }, '\r\n\r\n')),
      encode(': keep-alive\r\n\r\n'),
      encode(frame('done', donePayload, '\r\n\r\n')),
    ])

    expect(events).toEqual([
      { name: 'token', text: 'CRLF' },
      { name: 'done', done: donePayload },
    ])
  })

  it('accepts bare CR line endings and comments between frames', () => {
    const parser = new ChatStreamParser()
    const events = drain(parser, [
      encode(': comment\r'),
      encode(frame('token', { text: 'CR' }, '\r\r')),
      encode(frame('done', donePayload, '\r\r')),
    ])
    // 最后一个孤立的 CR 只能在 EOF 处判定为行终止符
    events.push(...parser.finish())

    expect(events).toEqual([
      { name: 'token', text: 'CR' },
      { name: 'done', done: donePayload },
    ])
    expect(parser.sawTerminal).toBe(true)
  })

  it('keeps citation events in arrival order without re-indexing them', () => {
    const parser = new ChatStreamParser()
    const events = parser.push(
      encode(frame('citation', citation(2)) + frame('citation', citation(1))),
    )

    expect(events).toEqual([
      { name: 'citation', citation: citation(2) },
      { name: 'citation', citation: citation(1) },
    ])
  })

  it('reports an unterminated stream as having no terminal event', () => {
    const parser = new ChatStreamParser()
    const events = drain(parser, [encode(frame('token', { text: '半' }))])
    parser.finish()

    expect(events).toEqual([{ name: 'token', text: '半' }])
    expect(parser.sawTerminal).toBe(false)
  })

  it('discards a trailing truncated frame instead of guessing its content', () => {
    const parser = new ChatStreamParser()
    const events = drain(parser, [encode('event: token\ndata: {"text":"截断"')])
    parser.finish()

    expect(events).toEqual([])
    expect(parser.sawTerminal).toBe(false)
  })

  it('parses an error event and keeps its retryable flag', () => {
    const parser = new ChatStreamParser()
    const payload = {
      code: 'MODEL_TIMEOUT',
      message: '生成超时，请重试',
      retryable: true,
      request_id: 'req-err-1',
    }
    const events = parser.push(encode(frame('error', payload)))
    parser.finish()

    expect(events).toEqual([{ name: 'error', error: payload }])
    expect(parser.sawTerminal).toBe(true)
  })

  it('keeps a non-null done reason_code verbatim', () => {
    const parser = new ChatStreamParser()
    const payload = {
      request_id: 'req-refused',
      outcome: 'refused',
      reason_code: 'no_evidence',
      citation_count: 0,
    }
    const events = parser.push(encode(frame('done', payload)))

    expect(events).toEqual([{ name: 'done', done: payload }])
  })
})

describe('chat stream parser protocol violations', () => {
  function expectProtocolError(run: () => void, reason: string): void {
    let caught: unknown
    try {
      run()
    } catch (error) {
      caught = error
    }
    expect(caught).toBeInstanceOf(ChatStreamProtocolError)
    expect((caught as ChatStreamProtocolError).reason).toBe(reason)
    expect((caught as ChatStreamProtocolError).code).toBe('CHAT_STREAM_PROTOCOL_ERROR')
    expect((caught as ChatStreamProtocolError).message).not.toMatch(/\{/)
  }

  it('rejects invalid JSON without echoing the payload', () => {
    const parser = new ChatStreamParser()
    expectProtocolError(() => parser.push(encode('event: token\ndata: {"text":\n\n')), 'invalid_json')
  })

  it('rejects an empty data line', () => {
    const parser = new ChatStreamParser()
    expectProtocolError(() => parser.push(encode('event: token\ndata: \n\n')), 'empty_data')
  })

  it('rejects an unknown event name instead of silently ignoring it', () => {
    const parser = new ChatStreamParser()
    expectProtocolError(() => parser.push(encode(frame('delta', { text: 'x' }))), 'unknown_event')
  })

  it('rejects a payload whose fields do not match the contract', () => {
    const parser = new ChatStreamParser()
    expectProtocolError(
      () => parser.push(encode(frame('token', { text: 42 }))),
      'invalid_payload',
    )
  })

  it('rejects an invalid done outcome', () => {
    const parser = new ChatStreamParser()
    expectProtocolError(
      () => parser.push(encode(frame('done', { ...donePayload, outcome: 'ok' }))),
      'invalid_payload',
    )
  })

  it('rejects a second terminal event in the same stream', () => {
    const parser = new ChatStreamParser()
    expectProtocolError(
      () => parser.push(encode(frame('done', donePayload) + frame('done', donePayload))),
      'duplicate_terminal',
    )
  })

  it('rejects any business event after the terminal event', () => {
    const parser = new ChatStreamParser()
    expectProtocolError(
      () => parser.push(encode(frame('done', donePayload) + frame('token', { text: 'late' }))),
      'event_after_terminal',
    )
  })

  it('accepts comments after the terminal event but still no business events', () => {
    const parser = new ChatStreamParser()
    const events = drain(parser, [encode(frame('done', donePayload) + ': keep-alive\n\n')])

    expect(events).toEqual([{ name: 'done', done: donePayload }])
    expect(parser.sawTerminal).toBe(true)
  })
})
