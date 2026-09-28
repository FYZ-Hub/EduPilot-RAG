/**
 * Chat 状态机（UI_SPEC 6.5、PRODUCT_SPEC 6.3）。
 *
 * 只保存**当前标签页会话**：不建立服务端会话、不写数据库、不持久化到 URL 或本地存储。
 * 协议解析完全交给 :class:`ChatStreamParser`，本文件只负责状态迁移。
 *
 * 状态规则：
 * - 同一时间只允许一个活动请求；第二次 ``send`` 在流未结束时直接忽略；
 * - 请求历史遵循后端契约（1–10 条、单条 ≤ 4000 字符、总计 ≤ 12000 字符），
 *   超长问题**就地提示**并保留输入框，绝不静默截断；
 * - ``token`` 只追加文本，**绝不**根据正文里的 ``[1]`` 生成引用；
 * - ``citation`` 可乱序到达，按 ``citation_index`` 去重并升序保存；
 * - ``done.outcome`` 只允许 answered / refused / conflict，``done.reason_code`` 原样保存；
 * - 流内 ``error`` 保留已收到的部分回答与引用，并使用事件携带的 ``request_id`` 与 ``retryable``；
 * - 无终止事件的 EOF 记为「连接中断」，**不能**当成成功；
 * - 用户主动停止使用 ``AbortController``，状态为 ``stopped`` 而不是错误；
 * - 失败后不自动重复提交问题。
 */

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import {
  buildChatRequestMessages,
  CHAT_QUESTION_TOO_LONG,
  emptyChatFilters,
  MAX_CHAT_MESSAGE_CHARS,
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

/** 本地（非服务端）校验错误：没有 request_id 字段，因此不可能被伪造。 */
export interface ChatValidationError {
  code: string
  message: string
}

export interface ChatTurnMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  /** 仅助手消息：终态结论（answered / refused / conflict）。 */
  outcome: ChatOutcome | null
  citations: ChatCitation[]
  requestId: string | null
  /** 仅助手消息：服务端 ``done.reason_code`` 的真实值，前端绝不推断或伪造。 */
  reasonCode: string | null
  /** 仅助手消息：服务端流内 ``error.retryable`` 的真实值；其它路径保持 null。 */
  retryable: boolean | null
  /** 仅助手消息：流内错误码或前端协议错误码；用户停止与连接中断都不算错误。 */
  errorCode: string | null
  /** 仅助手消息：错误的人类可读说明（服务端 error.message 或前端协议说明）。 */
  errorMessage: string | null
  interrupted: boolean
  stopped: boolean
}

export type ChatFilterKey = 'major' | 'grade_year' | 'semester' | 'doc_category'

const FILTER_KEYS: readonly ChatFilterKey[] = ['major', 'grade_year', 'semester', 'doc_category']

function byCitationIndex(left: ChatCitation, right: ChatCitation): number {
  return left.citation_index - right.citation_index
}

/**
 * 只有**完整**的助手回答才能进入下一轮的问题改写上下文。
 * stopped / interrupted / 出错的半截回答一律不参与，避免污染后续提问。
 */
function isCompleteAnswer(message: ChatTurnMessage): boolean {
  return (
    message.role === 'assistant' &&
    !message.interrupted &&
    !message.stopped &&
    message.errorCode === null
  )
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
  /** 服务端 ``done.reason_code``；唯一来源是 done 事件。 */
  const reasonCode = ref<string | null>(null)
  /** 服务端流内 ``error.retryable``；唯一来源是 error 事件，其它路径保持 null。 */
  const retryable = ref<boolean | null>(null)
  /** 本地校验错误（超长问题）；不来自服务端。 */
  const validationError = ref<ChatValidationError | null>(null)

  let controller: AbortController | null = null
  let counter = 0

  const canSend = computed(() => !streaming.value && question.value.trim().length > 0)

  const activeFilters = computed(() =>
    FILTER_KEYS.flatMap((key) => {
      const value = filters.value[key]
      if (value === null || value === '') {
        return []
      }
      return [{ key, label: filterLabel(key, value) }]
    }),
  )

  /** 只有「最后一条是不完整的助手回答」且服务端没有禁止重试时才提供手动重试。 */
  const canRetry = computed(
    () => !streaming.value && retryable.value !== false && lastIncompleteAssistantId() !== null,
  )

  function filterLabel(key: ChatFilterKey, value: string | number): string {
    if (key === 'doc_category') {
      return (
        options.value?.doc_categories.find((item) => item.value === value)?.label ?? String(value)
      )
    }
    return String(value)
  }

  function updateFilter(key: ChatFilterKey, value: string | number | null): void {
    filters.value = { ...filters.value, [key]: value }
  }

  function clearFilters(): void {
    filters.value = emptyChatFilters()
  }

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
    reasonCode.value = null
    retryable.value = null
    validationError.value = null
    stopped.value = false
    interrupted.value = false
  }

  /** 最后一条消息若是不完整的助手回答，返回它的 id（用于重试替换）。 */
  function lastIncompleteAssistantId(): string | null {
    const last = messages.value.at(-1)
    if (last && last.role === 'assistant' && !isCompleteAnswer(last)) {
      return last.id
    }
    return null
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

    // 超长问题**就地提示**：不清空输入框、不追加消息、不进入 streaming、不发起请求
    if (text.length > MAX_CHAT_MESSAGE_CHARS) {
      validationError.value = {
        code: CHAT_QUESTION_TOO_LONG,
        message: `问题过长：最多 ${MAX_CHAT_MESSAGE_CHARS} 个字符（当前 ${text.length} 个）`,
      }
      return
    }

    question.value = ''
    await runTurn(text, true)
  }

  /**
   * 手动重试上一条问题：**只有用户点击才会调用**，绝不自动重试。
   *
   * 不追加新的 user 消息，因此请求历史里不会出现两次同样的问题；
   * 重试成功后新的回答会替代原来的不完整回答，失败时旧内容仍然保留。
   */
  async function retry(): Promise<void> {
    if (streaming.value) {
      return
    }
    const lastUser = [...messages.value].reverse().find((message) => message.role === 'user')
    if (!lastUser) {
      return
    }
    await runTurn(lastUser.content, false)
  }

  /** 本轮请求要发送的消息：剔除不完整的助手回答，只保留可用上下文与当前问题。 */
  function requestMessages(): ChatMessagePayload[] {
    return buildChatRequestMessages(
      messages.value
        .filter((message) => message.role === 'user' || isCompleteAnswer(message))
        .map<ChatMessagePayload>((message) => ({
          role: message.role,
          content: message.content,
        })),
    )
  }

  function createUserMessage(content: string): ChatTurnMessage {
    return {
      id: nextId('user'),
      role: 'user',
      content,
      outcome: null,
      citations: [],
      requestId: null,
      reasonCode: null,
      retryable: null,
      errorCode: null,
      errorMessage: null,
      interrupted: false,
      stopped: false,
    }
  }

  function createAssistantMessage(): ChatTurnMessage {
    return {
      id: nextId('assistant'),
      role: 'assistant',
      content: streamingContent.value,
      outcome: outcome.value,
      citations: [...citations.value],
      requestId: requestId.value,
      reasonCode: reasonCode.value,
      retryable: retryable.value,
      errorCode: streamError.value?.code ?? null,
      errorMessage: streamError.value?.message ?? null,
      interrupted: interrupted.value,
      stopped: stopped.value,
    }
  }

  /** 执行一轮问答；``appendUser`` 为 false 时视为对同一条问题的重试。 */
  async function runTurn(text: string, appendUser: boolean): Promise<void> {
    resetStreamState()
    const replaceId = appendUser ? null : lastIncompleteAssistantId()
    if (appendUser) {
      messages.value = [...messages.value, createUserMessage(text)]
    }
    streaming.value = true

    const current = new AbortController()
    controller = current
    const parser = new ChatStreamParser()
    let doneOutcome: ChatOutcome | null = null
    let doneRequestId: string | null = null
    let doneReasonCode: string | null = null

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
        doneReasonCode = event.done.reason_code
        return
      }
      // 流内 error：保留部分回答与引用，只记录错误本身
      streamError.value = new ApiError(event.error.message, {
        kind: 'http',
        code: event.error.code,
        requestId: event.error.request_id,
      })
      requestId.value = event.error.request_id
      retryable.value = event.error.retryable
    }

    let failure: ApiError | null = null
    try {
      const response = await streamChat(
        {
          messages: requestMessages(),
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
      reasonCode.value = doneReasonCode
    }

    if (streamingContent.value !== '' || parser.sawTerminal) {
      const next = [...messages.value]
      if (replaceId !== null) {
        const replaceIndex = next.findIndex((message) => message.id === replaceId)
        if (replaceIndex >= 0) {
          next.splice(replaceIndex, 1)
        }
      }
      next.push(createAssistantMessage())
      messages.value = next
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
    reasonCode,
    retryable,
    validationError,
    canSend,
    canRetry,
    activeFilters,
    loadOptions,
    send,
    retry,
    updateFilter,
    clearFilters,
    stop,
    selectCitation,
    clearConversation,
  }
})
