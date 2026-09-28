import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import type { DocumentListPayload, DocumentListItem } from '@/api/documents'

const mocks = vi.hoisted(() => ({
  listDocuments: vi.fn(),
  getDocumentStatus: vi.fn(),
  deleteDocument: vi.fn(),
}))

vi.mock('@/api/documents', async () => {
  const actual = await vi.importActual<typeof import('@/api/documents')>('@/api/documents')
  return {
    ...actual,
    listDocuments: mocks.listDocuments,
    getDocumentStatus: mocks.getDocumentStatus,
    deleteDocument: mocks.deleteDocument,
  }
})

import { useDocumentsStore } from './documents'

function item(overrides: Partial<DocumentListItem> = {}): DocumentListItem {
  return {
    id: 'doc-1',
    file_name: 'plan.pdf',
    file_type: 'pdf',
    doc_category: 'degree_plan',
    source_type: 'demo',
    dataset_version: '2026.1',
    status: 'ready',
    retrievable: true,
    activation_state: 'active',
    current_stage: 'completed',
    chunk_count: 4,
    block_count: 4,
    locator_types: ['page_number'],
    created_at: '2026-03-01T02:00:00Z',
    updated_at: '2026-03-02T02:00:00Z',
    error: null,
    ...overrides,
  }
}

function payload(overrides: Partial<DocumentListPayload> = {}): DocumentListPayload {
  return {
    items: [],
    total: 0,
    counts: { ready: 0, retrievable: 0, processing: 0, failed: 0 },
    ...overrides,
  }
}

describe('documents store', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    setActivePinia(createPinia())
    mocks.listDocuments.mockReset()
    mocks.getDocumentStatus.mockReset()
    mocks.deleteDocument.mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('exposes counts.retrievable directly and never substitutes counts.ready', async () => {
    mocks.listDocuments.mockResolvedValue(
      payload({
        items: [item({ id: 'a', retrievable: true }), item({ id: 'b', retrievable: false })],
        total: 2,
        counts: { ready: 7, retrievable: 1, processing: 3, failed: 2 },
      }),
    )

    const store = useDocumentsStore()
    await store.load({ force: true })

    expect(store.counts.retrievable).toBe(1)
    expect(store.counts.ready).toBe(7)
    expect(store.retrievableCount).toBe(1)
    expect(store.retrievableCount).not.toBe(store.counts.ready)
    expect(store.total).toBe(2)
    expect(store.counts.processing).toBe(3)
    expect(store.counts.failed).toBe(2)
  })

  it('keeps real zeros for an empty library', async () => {
    mocks.listDocuments.mockResolvedValue(payload())

    const store = useDocumentsStore()
    await store.load({ force: true })

    expect(store.total).toBe(0)
    expect(store.counts.retrievable).toBe(0)
    expect(store.counts.processing).toBe(0)
    expect(store.counts.failed).toBe(0)
    expect(store.loaded).toBe(true)
  })

  it('starts at most one status timer even when started repeatedly', async () => {
    mocks.listDocuments.mockResolvedValue(
      payload({
        items: [item({ id: 'upload-1', source_type: 'upload', status: 'embedding' })],
        total: 1,
        counts: { ready: 0, retrievable: 0, processing: 1, failed: 0 },
      }),
    )
    mocks.getDocumentStatus.mockResolvedValue(
      item({ id: 'upload-1', source_type: 'upload', status: 'embedding' }),
    )

    const store = useDocumentsStore()
    await store.load({ force: true })

    store.startStatusPolling()
    store.startStatusPolling()
    store.startStatusPolling()

    await vi.advanceTimersByTimeAsync(2000)
    expect(mocks.getDocumentStatus).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(2000)
    expect(mocks.getDocumentStatus).toHaveBeenCalledTimes(2)

    store.stop()
  })

  it('stops polling once every upload document reaches a terminal status', async () => {
    mocks.listDocuments.mockResolvedValue(
      payload({
        items: [item({ id: 'upload-1', source_type: 'upload', status: 'parsing' })],
        total: 1,
        counts: { ready: 0, retrievable: 0, processing: 1, failed: 0 },
      }),
    )
    mocks.getDocumentStatus.mockResolvedValue(
      item({ id: 'upload-1', source_type: 'upload', status: 'ready', retrievable: true }),
    )

    const store = useDocumentsStore()
    await store.load({ force: true })
    store.startStatusPolling()

    await vi.advanceTimersByTimeAsync(2000)
    expect(mocks.getDocumentStatus).toHaveBeenCalledTimes(1)
    expect(mocks.listDocuments).toHaveBeenCalledTimes(2) // 终态后重新对齐权威统计

    await vi.advanceTimersByTimeAsync(10000)
    expect(mocks.getDocumentStatus).toHaveBeenCalledTimes(1)

    store.stop()
  })

  it('deduplicates concurrent loads and skips reloading once loaded', async () => {
    mocks.listDocuments.mockResolvedValue(payload({ total: 1 }))

    const store = useDocumentsStore()
    await Promise.all([store.load(), store.load()])

    expect(mocks.listDocuments).toHaveBeenCalledTimes(1)

    await store.load()
    expect(mocks.listDocuments).toHaveBeenCalledTimes(1)

    await store.load({ force: true })
    expect(mocks.listDocuments).toHaveBeenCalledTimes(2)
  })

  it('deletes a document then refreshes the authoritative list', async () => {
    mocks.listDocuments.mockResolvedValue(payload({ total: 1 }))
    mocks.deleteDocument.mockResolvedValue(undefined)

    const store = useDocumentsStore()
    await store.load({ force: true })
    await store.remove('doc-1')

    expect(mocks.deleteDocument).toHaveBeenCalledWith('doc-1')
    expect(mocks.listDocuments).toHaveBeenCalledTimes(2)
  })
})
