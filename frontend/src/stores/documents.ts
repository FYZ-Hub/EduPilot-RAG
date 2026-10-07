/**
 * 文档列表状态（UI_SPEC 11：跨组件状态放 Pinia）。
 *
 * - 统计卡直接使用 ``GET /api/documents`` 的 total / counts.*，不在前端重算权威数字；
 * - 列表存在非终态 upload 文档时每 2 秒查询对应 ``/documents/{id}/status``，
 *   全部终态或调用 :func:`stop` 后停止，且任何时刻只有一个 timer。
 */

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { ApiError, toApiError } from '@/api/client'
import {
  deleteDocument as deleteDocumentRequest,
  getDocumentStatus,
  listDocuments,
  type DocumentCounts,
  type DocumentListItem,
} from '@/api/documents'
import { isDocumentProcessing } from '@/domain/documents'

/** 非终态文档的状态轮询间隔（UI_SPEC 5.5）。 */
export const DOCUMENT_STATUS_POLL_INTERVAL_MS = 2000

const EMPTY_COUNTS: DocumentCounts = { ready: 0, retrievable: 0, processing: 0, failed: 0 }

export const useDocumentsStore = defineStore('documents', () => {
  const items = ref<DocumentListItem[]>([])
  const counts = ref<DocumentCounts>({ ...EMPTY_COUNTS })
  const total = ref(0)
  const loading = ref(false)
  const loaded = ref(false)
  const error = ref<ApiError | null>(null)

  let inflight: Promise<void> | null = null
  let statusTimer: ReturnType<typeof setTimeout> | null = null
  let statusAbort: AbortController | null = null

  /** 需要持续跟踪状态的 upload 文档。 */
  const processingUploadIds = computed(() =>
    items.value
      .filter((item) => item.source_type === 'upload' && isDocumentProcessing(item.status))
      .map((item) => item.id),
  )
  const hasProcessing = computed(() => processingUploadIds.value.length > 0)
  const retrievableCount = computed(() => counts.value.retrievable)

  async function fetchList(options: { silent?: boolean } = {}): Promise<void> {
    if (!options.silent) {
      loading.value = true
    }
    try {
      const payload = await listDocuments()
      items.value = payload.items
      counts.value = payload.counts
      total.value = payload.total
      loaded.value = true
      error.value = null
    } catch (caught) {
      const apiError = toApiError(caught)
      if (!apiError.isAborted) {
        error.value = apiError
      }
    } finally {
      if (!options.silent) {
        loading.value = false
      }
    }
  }

  /** 加载列表；同一时刻只允许一个请求，重复进入不会重复拉取。 */
  async function load(options: { force?: boolean; silent?: boolean } = {}): Promise<void> {
    if (inflight) {
      return inflight
    }
    if (loaded.value && !options.force) {
      return
    }
    inflight = fetchList(options).finally(() => {
      inflight = null
    })
    return inflight
  }

  function refresh(): Promise<void> {
    return load({ force: true, silent: true })
  }

  function stopStatusPolling(): void {
    if (statusTimer !== null) {
      clearTimeout(statusTimer)
      statusTimer = null
    }
    statusAbort?.abort()
    statusAbort = null
  }

  function scheduleStatusTick(): void {
    if (statusTimer !== null) {
      clearTimeout(statusTimer)
    }
    statusTimer = setTimeout(() => {
      statusTimer = null
      void pollStatuses()
    }, DOCUMENT_STATUS_POLL_INTERVAL_MS)
  }

  async function pollStatuses(): Promise<void> {
    const ids = processingUploadIds.value
    if (!ids.length) {
      stopStatusPolling()
      return
    }

    const controller = new AbortController()
    statusAbort = controller
    let settled = false
    try {
      const results = await Promise.all(
        ids.map((id) => getDocumentStatus(id, controller.signal).catch(() => null)),
      )
      for (const result of results) {
        if (!result) {
          continue
        }
        const index = items.value.findIndex((item) => item.id === result.id)
        if (index >= 0) {
          items.value[index] = result
        }
        if (!isDocumentProcessing(result.status)) {
          settled = true
        }
      }
    } finally {
      if (statusAbort === controller) {
        statusAbort = null
      }
    }

    if (settled) {
      // 出现终态后重新对齐服务端权威统计
      await refresh()
    }
    if (processingUploadIds.value.length) {
      scheduleStatusTick()
    } else {
      stopStatusPolling()
    }
  }

  /** 启动状态轮询；重复调用不会产生第二个 timer。 */
  function startStatusPolling(): void {
    stopStatusPolling()
    if (hasProcessing.value) {
      scheduleStatusTick()
    }
  }

  function stop(): void {
    stopStatusPolling()
  }

  async function remove(documentId: string): Promise<void> {
    await deleteDocumentRequest(documentId)
    await refresh()
  }

  return {
    items,
    counts,
    total,
    loading,
    loaded,
    error,
    processingUploadIds,
    hasProcessing,
    retrievableCount,
    load,
    refresh,
    startStatusPolling,
    pollStatuses,
    stopStatusPolling,
    stop,
    remove,
  }
})
