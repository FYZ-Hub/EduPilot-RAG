import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { fetchHealth, type CapabilityState, type HealthResponse } from '@/api/health'

export type ConnectionState = 'unknown' | 'loading' | 'connected' | 'error'

export interface CapabilityRow {
  key: 'documents' | 'chat' | 'planning'
  label: string
  state: CapabilityState
  text: string
}

export interface ProviderRow {
  key: 'embedding' | 'reranker' | 'llm'
  label: string
  detail: string
  ready: boolean
  text: string
}

/** 能力状态文案：三态，不使用「故障」这类断言。 */
const CAPABILITY_TEXT: Record<CapabilityState, string> = {
  ready: '可用',
  unconfigured: '未配置',
  unavailable: '不可用',
}

const CAPABILITY_LABELS: Record<CapabilityRow['key'], string> = {
  documents: '文档功能',
  chat: '问答',
  planning: '规划',
}

const PROVIDER_LABELS: Record<ProviderRow['key'], string> = {
  embedding: 'Embedding',
  reranker: 'Reranker',
  llm: 'LLM',
}

/** Provider 是否「已有成功调用证据」——false 只表示尚无证据，绝不等于故障。 */
const PROVIDER_READY_TEXT = '已有成功调用'
const PROVIDER_PENDING_TEXT = '尚无成功调用证据'

/**
 * 读取真实 ``/api/health``，供顶部栏展示**分层**状态：连接层 / 能力层 /
 * Provider 层（资料层由知识库列表提供，见 AppLayout）。
 *
 * 不缓存假数据：请求失败时清空并暴露错误。
 */
export const useHealthStore = defineStore('health', () => {
  const connection = ref<ConnectionState>('unknown')
  const data = ref<HealthResponse | null>(null)
  const errorMessage = ref<string | null>(null)

  const capabilities = computed(() => data.value?.capabilities ?? null)
  const providers = computed(() => data.value?.providers ?? null)

  /** 运行模式标签，只在健康接口真实返回后展示。 */
  const runModeLabel = computed(() => {
    const embedding = data.value?.providers.embedding
    if (!embedding) {
      return null
    }
    return `${embedding.provider} · ${embedding.device ?? 'n/a'}`
  })

  /** 连接层：只描述「能不能连上」，不代表任何能力或模型状态。 */
  const connectionText = computed(() => {
    switch (connection.value) {
      case 'connected':
        return '后端已连接'
      case 'error':
        return '后端不可连接'
      default:
        return '正在检测后端'
    }
  })

  /** 服务状态文案：healthy/degraded 由后端按能力前置条件计算。 */
  const serviceStatusText = computed(() => {
    if (connection.value !== 'connected' || !data.value) {
      return null
    }
    return data.value.status === 'healthy' ? '能力前置条件全部满足' : '部分能力前置条件未满足'
  })

  /** 能力层：文档 / 问答 / 规划三态。 */
  const capabilityRows = computed<CapabilityRow[]>(() => {
    const current = capabilities.value
    if (!current) {
      return []
    }
    return (['documents', 'chat', 'planning'] as const).map((key) => ({
      key,
      label: CAPABILITY_LABELS[key],
      state: current[key],
      text: CAPABILITY_TEXT[current[key]],
    }))
  })

  const readyCapabilityCount = computed(
    () => capabilityRows.value.filter((row) => row.state === 'ready').length,
  )

  /** 顶部摘要用：连接成功后才给出「能力 x/y 就绪」。 */
  const capabilitySummary = computed(() => {
    if (connection.value !== 'connected') {
      return null
    }
    return `能力 ${readyCapabilityCount.value}/${capabilityRows.value.length} 就绪`
  })

  /** 空知识库（或检索查询失败）时，问答不可用而文档功能仍可用——给出解释而非故障暗示。 */
  const chatHint = computed(() => {
    const current = capabilities.value
    if (!current) {
      return null
    }
    if (current.documents === 'ready' && current.chat !== 'ready') {
      return '知识库暂无可用语料时，文档功能仍然可用，但问答不可用。'
    }
    return null
  })

  /** Provider 层：只报告「是否有成功调用证据」，不判定故障。 */
  const providerRows = computed<ProviderRow[]>(() => {
    const current = providers.value
    if (!current) {
      return []
    }
    return (['embedding', 'reranker', 'llm'] as const).map((key) => {
      const item = current[key]
      const detail = item.device ? `${item.provider} · ${item.device}` : item.provider
      return {
        key,
        label: PROVIDER_LABELS[key],
        detail,
        ready: item.ready,
        text: item.ready ? PROVIDER_READY_TEXT : PROVIDER_PENDING_TEXT,
      }
    })
  })

  async function load(signal?: AbortSignal): Promise<void> {
    connection.value = 'loading'
    errorMessage.value = null

    try {
      data.value = await fetchHealth(signal)
      connection.value = 'connected'
    } catch (error) {
      data.value = null
      connection.value = 'error'
      errorMessage.value = error instanceof Error ? error.message : '未知错误'
    }
  }

  return {
    connection,
    data,
    errorMessage,
    capabilities,
    providers,
    runModeLabel,
    connectionText,
    serviceStatusText,
    capabilityRows,
    readyCapabilityCount,
    capabilitySummary,
    chatHint,
    providerRows,
    load,
  }
})
