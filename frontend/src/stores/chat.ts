/**
 * Chat 状态机（UI_SPEC 6.5、PRODUCT_SPEC 6.3）。
 *
 * 只保存**当前标签页会话**：不建立服务端会话、不写数据库、不持久化到 URL 或本地存储。
 * 协议解析完全交给 :class:`ChatStreamParser`，本文件只负责状态迁移。
 *
 * 状态规则：
 * - 同一时间只允许一个活动请求；第二次 ``send`` 在流未结束时直接忽略；
 * - ``token`` 只追加文本，**绝不**根据正文里的 ``[1]`` 生成引用；
 * - ``citation`` 可乱序到达，按 ``citation_index`` 去重并升序保存；
 * - ``done.outcome`` 只允许 answered / refused / conflict；
 * - 流内 ``error`` 保留已收到的部分回答与引用，并使用事件携带的 request_id；
 * - 无终止事件的 EOF 记为「连接中断」，**不能**当成成功；
 * - 用户主动停止使用 ``AbortController``，状态为 ``stopped`` 而不是错误；
 * - 失败后不自动重复提交问题。
 */

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import {
  emptyChatFilters,
  MAX_CHAT_MESSAGES,
  streamChat,
  type ChatCitation,
  type ChatFilters,
  type ChatMessagePayload,
  type ChatOutcome,
} from '@/api/chat'
import { ApiError, toApiError } from '@/api/client'
import { fetchRetrievalOptions, type RetrievalOptions } from '@/api/retrieval'
import {
  ChatStreamParser,
  ChatStreamProtocolError,
  type ChatStreamEvent,
} from '@/domain/chatStream'

export interface ChatTurnMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  /** 仅助手消息：终态结论（answered / refused / conflict）。 */
  outcome: ChatOutcome | null
  citations: ChatCitation[]
  requestId: string | null
  /** 仅助手消息：流内错误码或前端协议错误码；用户停止与连接中断都不算错误。 */
  errorCode: string | null
  interrupted: boolean
  stopped: boolean
}

function byCitationIndex(left: ChatCitation, right: ChatCitation): number {
  return left.citation_index - right.citation_index
}

export const useChatStore = defineStore('chat', () => {
  const messages = ref<ChatTurnMessage[]>([])
  const question = ref('')
  const streamingContent = ref('')
  /** 当前轮次的引用，始终按 citation_index 升序保存。 */
  const citations = ref<ChatCitation[]>([])
  const selectedCitationIndex = ref<number | null>(null)
  const filters = ref<ChatFilters>(emptyChatFilters())
  const options = ref<RetrievalOptions | null>(null)
  const optionsLoading = ref(false)
  const optionsError = ref<ApiError | null>(null)

  const streaming = ref(false)
  const stopped = ref(false)
  const interrupted = ref(false)
  const outcome = ref<ChatOutcome | null>(null)
  const streamError = ref<ApiError | null>(null)
  const requestId = ref<string | null>(null)

  let controller: AbortController | null = null
  let counter = 0

  const canSend = computed(() => !streaming.value && question.value.trim().length > 0)

  function nextId(role: ChatTurnMessage['role']): string {
    counter += 1
    return `${role}-${counter}`
  }

  /** 清理上一轮的临时流状态；**不动**已完成的 messages。 */
  function resetStreamState(): void {
    streamingContent.value = ''
    citations.value = []
    selectedCitationIndex.value = null
    outcome.value = null
    streamError.value = null
    requestId.value = null
    stopped.value = false
    interrupted.value = false
  }

  function upsertCitation(citation: ChatCitation): void {
    const next = citations.value.filter(
      (item) => item.citation_index !== citation.citation_index,
    )
    next.push(citation)
    next.sort(byCitationIndex)
    citations.value = next
  }

  function selectCitation(index: number): void {
    selectedCitationIndex.value = selectedCitationIndex.value === index ? null : index
  }

  async function loadOptions(): Promise<void> {
    optionsLoading.value = true
    try {
      options.value = await fetchRetrievalOptions()
      optionsError.value = null
    } catch (caught) {
      const error = toApiError(caught)
      if (!error.isAborted) {
        optionsError.value = error
      }
    } finally {
      optionsLoading.value = false
    }
  }

  /** 发送当前问题；流未结束时直接返回，保证同时只有一个活动请求。 */
  async function send(): Promise<void> {
    const text = question.value.trim()
    if (!text || streaming.value) {
      return
    }

    resetStreamState()
    messages.value = [
      ...messages.value,
      {
        id: nextId('user'),
        role: 'user',
        content: text,
        outcome: null,
        citations: [],
        requestId: null,
        errorCode: null,
        interrupted: false,
        stopped: false,
      },
    ]
    question.value = ''
    streaming.value = true

    const current = new AbortController()
    controller = current
    const parser = new ChatStreamParser()
    let doneOutcome: ChatOutcome | null = null
    let doneRequestId: string | null = null

    const apply = (event: ChatStreamEvent): void => {
      if (event.name === 'token') {
        streamingContent.value += event.text
        return
      }
      if (event.name === 'citation') {
        upsertCitation(event.citation)
        return
      }
      if (event.name === 'done') {
        doneOutcome = event.done.outcome
        doneRequestId = event.done.request_id
        return
      }
      // 流内 error：保留部分回答与引用，只记录错误本身
      streamError.value = new ApiError(event.error.message, {
        kind: 'http',
        code: event.error.code,
        requestId: event.error.request_id,
      })
      requestId.value = event.error.request_id
    }

    let failure: ApiError | null = null
    try {
      const response = await streamChat(
        {
          messages: messages.value
            .slice(-MAX_CHAT_MESSAGES)
            .map<ChatMessagePayload>((message) => ({
              role: message.role,
              content: message.content,
            })),
          filters: { ...filters.value },
        },
        current.signal,
      )
      const body = response.body
      if (!body) {
        throw new ChatStreamProtocolError('invalid_payload')
      }
      const reader = body.getReader()
      try {
        for (;;) {
          const { done, value } = await reader.read()
          if (done) {
            break
          }
          if (!value || value.length === 0) {
            continue
          }
          for (const event of parser.push(value)) {
            apply(event)
          }
        }
      } finally {
        reader.releaseLock()
      }
      for (const event of parser.finish()) {
        apply(event)
      }
    } catch (caught) {
      if (current.signal.aborted) {
        // 用户停止：不是错误
      } else if (caught instanceof ChatStreamProtocolError) {
        failure = new ApiError(caught.message, { kind: 'protocol', code: caught.code })
      } else {
        failure = toApiError(caught)
      }
    } finally {
      if (controller === current) {
        controller = null
      }
      streaming.value = false
    }

    if (current.signal.aborted) {
      stopped.value = true
    } else if (failure) {
      streamError.value = failure
    } else if (!parser.sawTerminal) {
      // 无终止事件的 EOF：连接中断，绝不当作成功
      interrupted.value = true
    } else if (doneOutcome !== null) {
      outcome.value = doneOutcome
      requestId.value = doneRequestId
    }

    if (streamingContent.value !== '' || parser.sawTerminal) {
      messages.value = [
        ...messages.value,
        {
          id: nextId('assistant'),
          role: 'assistant',
          content: streamingContent.value,
          outcome: outcome.value,
          citations: [...citations.value],
          requestId: requestId.value,
          errorCode: streamError.value?.code ?? null,
          interrupted: interrupted.value,
          stopped: stopped.value,
        },
      ]
    }
  }

  /** 中止当前流；空闲或重复调用都是安全的空操作。 */
  function stop(): void {
    const current = controller
    if (current === null) {
      return
    }
    controller = null
    current.abort()
  }

  function clearConversation(): void {
    stop()
    messages.value = []
    question.value = ''
    resetStreamState()
  }

  return {
    messages,
    question,
    streamingContent,
    citations,
    selectedCitationIndex,
    filters,
    options,
    optionsLoading,
    optionsError,
    streaming,
    stopped,
    interrupted,
    outcome,
    streamError,
    requestId,
    canSend,
    loadOptions,
    send,
    stop,
    selectCitation,
    clearConversation,
  }
})
