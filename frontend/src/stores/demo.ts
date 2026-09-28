/**
 * 演示数据集状态与轮询生命周期（UI_SPEC 5.3、DEMO_DATA_SPEC）。
 *
 * 关键不变量：
 * - Job 的 ``completed`` **绝不**直接推导 Dataset ``loaded``；终态后必须重新
 *   ``GET /api/demo/status``，只有 ``status.loaded === true`` 才表示已加载。
 * - 网络失败只是「正在重新连接」，**绝不**把 job 写成 failed。
 * - 任何时刻只有一个 timer；终态、任务切换或页面卸载都会停止。
 * - 连续三次网络失败后暂停自动轮询，提供手动「重新连接」。
 * - job 查询 404 时重新获取 demo status，再按 ``active_job_id`` / ``last_job_id`` 恢复。
 */

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { ApiError, toApiError } from '@/api/client'
import {
  fetchDemoJob,
  fetchDemoStatus,
  isJobTerminal,
  seedDemo,
  type DatasetState,
  type DemoJob,
  type DemoStatus,
} from '@/api/demo'

/** 连续网络失败达到该次数后暂停自动轮询。 */
export const MAX_POLL_FAILURES = 3
/** 有上限的退避重试间隔。 */
export const RETRY_BACKOFF_MS = [2000, 4000, 8000] as const

export const useDemoStore = defineStore('demo', () => {
  const status = ref<DemoStatus | null>(null)
  const statusLoading = ref(false)
  const statusError = ref<ApiError | null>(null)

  const job = ref<DemoJob | null>(null)
  const jobId = ref<string | null>(null)
  const seeding = ref(false)
  const seedError = ref<ApiError | null>(null)

  const polling = ref(false)
  const pollPaused = ref(false)
  const pollFailures = ref(0)
  const lastPollError = ref<ApiError | null>(null)

  let timer: ReturnType<typeof setTimeout> | null = null
  let statusInflight: Promise<void> | null = null

  const state = computed<DatasetState | null>(() => status.value?.state ?? null)
  const loaded = computed(() => status.value?.loaded === true)
  const servingPreviousVersion = computed(() => status.value?.serving_previous_version === true)
  const pollIntervalMs = computed(() => (status.value?.poll_after_seconds ?? 2) * 1000)
  const reconnecting = computed(() => pollFailures.value > 0 && !pollPaused.value)
  const reconnectLabel = computed(() =>
    pollPaused.value ? '自动轮询已暂停' : '正在重新连接',
  )

  async function loadStatus(): Promise<void> {
    if (statusInflight) {
      return statusInflight
    }
    statusLoading.value = true
    statusInflight = (async () => {
      try {
        status.value = await fetchDemoStatus()
        statusError.value = null
      } catch (caught) {
        const apiError = toApiError(caught)
        if (!apiError.isAborted) {
          statusError.value = apiError
        }
      } finally {
        statusLoading.value = false
      }
    })().finally(() => {
      statusInflight = null
    })
    return statusInflight
  }

  function stopPolling(): void {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
    polling.value = false
  }

  function schedule(delayMs?: number): void {
    if (!jobId.value) {
      return
    }
    if (timer !== null) {
      clearTimeout(timer)
    }
    timer = setTimeout(() => {
      timer = null
      void pollOnce()
    }, delayMs ?? pollIntervalMs.value)
    polling.value = true
  }

  async function pollOnce(): Promise<void> {
    const currentId = jobId.value
    if (!currentId || pollPaused.value) {
      return
    }
    try {
      const next = await fetchDemoJob(currentId)
      if (jobId.value !== currentId) {
        return
      }
      job.value = next
      pollFailures.value = 0
      lastPollError.value = null
      if (isJobTerminal(next.status)) {
        stopPolling()
        // job 达到目标状态不等于数据集已加载：必须重新读取 status
        await loadStatus()
        return
      }
      schedule()
    } catch (caught) {
      const apiError = toApiError(caught)
      if (jobId.value !== currentId) {
        return
      }
      if (apiError.status === 404) {
        stopPolling()
        await loadStatus()
        const resumed = status.value?.active_job_id ?? status.value?.last_job_id ?? null
        job.value = null
        jobId.value = resumed
        if (resumed) {
          schedule(0)
        }
        return
      }
      // 网络失败：保持 job 原状态，只标记重连
      pollFailures.value += 1
      lastPollError.value = apiError
      if (pollFailures.value >= MAX_POLL_FAILURES) {
        pollPaused.value = true
        stopPolling()
        return
      }
      const index = Math.min(pollFailures.value, RETRY_BACKOFF_MS.length) - 1
      schedule(RETRY_BACKOFF_MS[index])
    }
  }

  /** 手动重新连接（暂停后使用）。 */
  async function reconnect(): Promise<void> {
    pollPaused.value = false
    pollFailures.value = 0
    lastPollError.value = null
    if (jobId.value) {
      schedule(0)
      return
    }
    await restore()
  }

  /** 调用唯一的 seed 接口；不发明 resume / reset / cancel。 */
  async function seed(): Promise<boolean> {
    if (seeding.value) {
      return false
    }
    seeding.value = true
    seedError.value = null
    try {
      const accepted = await seedDemo()
      jobId.value = accepted.job_id
      // 不伪造进度：真实进度由第一次轮询填充
      job.value = null
      pollPaused.value = false
      pollFailures.value = 0
      lastPollError.value = null
      schedule(0)
      return true
    } catch (caught) {
      seedError.value = toApiError(caught)
      return false
    } finally {
      seeding.value = false
    }
  }

  /** 进入页面时恢复：先读 status，再按 active_job_id / last_job_id 决定是否继续轮询。 */
  async function restore(): Promise<void> {
    await loadStatus()
    const candidate = status.value?.active_job_id ?? status.value?.last_job_id ?? null
    if (!candidate) {
      jobId.value = null
      job.value = null
      stopPolling()
      return
    }
    jobId.value = candidate
    pollPaused.value = false
    pollFailures.value = 0
    lastPollError.value = null
    await pollOnce()
  }

  return {
    status,
    statusLoading,
    statusError,
    job,
    jobId,
    seeding,
    seedError,
    polling,
    pollPaused,
    pollFailures,
    lastPollError,
    state,
    loaded,
    servingPreviousVersion,
    pollIntervalMs,
    reconnecting,
    reconnectLabel,
    loadStatus,
    seed,
    restore,
    reconnect,
    pollOnce,
    stopPolling,
  }
})
