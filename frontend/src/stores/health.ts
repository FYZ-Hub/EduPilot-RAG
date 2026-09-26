import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { fetchHealth, type HealthResponse } from '@/api/health'

export type ConnectionState = 'unknown' | 'loading' | 'connected' | 'error'

/**
 * 读取真实 ``/api/health``，供顶部栏展示后端状态与运行模式。
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

  return { connection, data, errorMessage, capabilities, providers, runModeLabel, load }
})
