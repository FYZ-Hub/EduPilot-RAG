import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createPinia } from 'pinia'

import { ApiError } from '@/api/client'
import type { DocumentListPayload } from '@/api/documents'
import type { DemoStatus } from '@/api/demo'
import type { HealthResponse } from '@/api/health'

const mocks = vi.hoisted(() => ({
  fetchHealth: vi.fn(),
  listDocuments: vi.fn(),
  getDocument: vi.fn(),
  getDocumentStatus: vi.fn(),
  getDocumentPreview: vi.fn(),
  deleteDocument: vi.fn(),
  fetchDemoStatus: vi.fn(),
  seedDemo: vi.fn(),
  fetchDemoJob: vi.fn(),
}))

vi.mock('@/api/health', async () => {
  const actual = await vi.importActual<typeof import('@/api/health')>('@/api/health')
  return { ...actual, fetchHealth: mocks.fetchHealth }
})

vi.mock('@/api/documents', async () => {
  const actual = await vi.importActual<typeof import('@/api/documents')>('@/api/documents')
  return {
    ...actual,
    listDocuments: mocks.listDocuments,
    getDocument: mocks.getDocument,
    getDocumentStatus: mocks.getDocumentStatus,
    getDocumentPreview: mocks.getDocumentPreview,
    deleteDocument: mocks.deleteDocument,
  }
})

vi.mock('@/api/demo', async () => {
  const actual = await vi.importActual<typeof import('@/api/demo')>('@/api/demo')
  return {
    ...actual,
    fetchDemoStatus: mocks.fetchDemoStatus,
    seedDemo: mocks.seedDemo,
    fetchDemoJob: mocks.fetchDemoJob,
  }
})

import KnowledgeView from './KnowledgeView.vue'

function health(documents: 'ready' | 'unconfigured' | 'unavailable'): HealthResponse {
  return {
    status: 'degraded',
    version: '0.1.0',
    capabilities: { documents, chat: 'unconfigured', planning: 'ready' },
    providers: {
      embedding: { provider: 'local', device: 'cpu', ready: false },
      reranker: { provider: 'local', device: 'cpu', ready: false },
      llm: { provider: 'openai_compatible', device: null, ready: false },
    },
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

function demoStatus(overrides: Partial<DemoStatus> = {}): DemoStatus {
  return {
    enabled: true,
    state: 'empty',
    dataset_version: '2026.1',
    manifest_sha256: null,
    pipeline_fingerprint: 'fp',
    available_documents: 0,
    ready_documents: 0,
    failed_documents: 0,
    loaded: false,
    poll_after_seconds: 2,
    active_dataset_version: null,
    serving_previous_version: false,
    active_job_id: null,
    last_job_id: null,
    reason: null,
    ...overrides,
  }
}

function mountView(): VueWrapper {
  return mount(KnowledgeView, {
    global: { plugins: [createPinia(), ElementPlus] },
  })
}

function buttonByText(wrapper: VueWrapper, label: string) {
  return wrapper.findAll('button').find((node) => node.text().includes(label))
}

function statValue(wrapper: VueWrapper, label: string): string {
  const card = wrapper.findAll('.ep-stat').find((node) => node.find('.ep-stat__label').text() === label)
  expect(card, `stat card ${label}`).toBeTruthy()
  return card!.find('.ep-stat__value').text()
}

describe('KnowledgeView', () => {
  beforeEach(() => {
    for (const mock of Object.values(mocks)) {
      mock.mockReset()
    }
    mocks.listDocuments.mockResolvedValue(payload())
    mocks.fetchDemoStatus.mockResolvedValue(demoStatus())
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('binds the statistics cards to counts.retrievable instead of counts.ready', async () => {
    mocks.fetchHealth.mockResolvedValue(health('ready'))
    mocks.listDocuments.mockResolvedValue(
      payload({ total: 9, counts: { ready: 5, retrievable: 2, processing: 1, failed: 1 } }),
    )
    const wrapper = mountView()
    await flushPromises()

    expect(statValue(wrapper, '全部文档')).toBe('9')
    expect(statValue(wrapper, '可检索')).toBe('2')
    expect(statValue(wrapper, '处理中')).toBe('1')
    expect(statValue(wrapper, '失败')).toBe('1')
    wrapper.unmount()
  })

  it('shows real zeros for an empty library and never auto-seeds', async () => {
    mocks.fetchHealth.mockResolvedValue(health('ready'))
    const wrapper = mountView()
    await flushPromises()

    expect(statValue(wrapper, '全部文档')).toBe('0')
    expect(statValue(wrapper, '可检索')).toBe('0')
    expect(mocks.seedDemo).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('enables write actions only when documents capability is ready', async () => {
    mocks.fetchHealth.mockResolvedValue(health('ready'))
    const wrapper = mountView()
    await flushPromises()

    const upload = buttonByText(wrapper, '上传文件')
    expect(upload!.attributes('disabled')).toBeUndefined()
    const seed = buttonByText(wrapper, '加载演示资料')
    expect(seed!.attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('disables upload, seed and delete when documents capability is unavailable', async () => {
    mocks.fetchHealth.mockResolvedValue(health('unavailable'))
    mocks.listDocuments.mockResolvedValue(
      payload({
        items: [
          {
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
            chunk_count: 1,
            block_count: 1,
            locator_types: ['page_number'],
            created_at: null,
            updated_at: '2026-03-02T02:00:00Z',
            error: null,
          },
        ],
        total: 1,
        counts: { ready: 1, retrievable: 1, processing: 0, failed: 0 },
      }),
    )
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('文档能力不可用')
    expect(buttonByText(wrapper, '上传文件')!.attributes('disabled')).toBeDefined()
    expect(buttonByText(wrapper, '加载演示资料')!.attributes('disabled')).toBeDefined()

    const rowButtons = wrapper.findAll('.ep-row-actions button')
    expect(rowButtons.length).toBeGreaterThan(0)
    for (const button of rowButtons) {
      expect(button.attributes('disabled')).toBeDefined()
    }
    wrapper.unmount()
  })

  it('disables write actions and explains why when the backend is unreachable', async () => {
    mocks.fetchHealth.mockRejectedValue(new TypeError('Failed to fetch'))
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('后端服务不可连接')
    expect(buttonByText(wrapper, '上传文件')!.attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('reports the upload success message through the uploader event', async () => {
    mocks.fetchHealth.mockResolvedValue(health('ready'))
    const wrapper = mountView()
    await flushPromises()

    const uploader = wrapper.findComponent({ name: 'FileUploader' })
    await uploader.vm.$emit('uploaded')
    await flushPromises()

    expect(mocks.listDocuments).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })

  it('shows the seed failure next to the demo section with its code and request id', async () => {
    mocks.fetchHealth.mockResolvedValue(health('ready'))
    mocks.seedDemo.mockRejectedValue(
      new ApiError('演示数据集清单不可用', {
        kind: 'http',
        status: 503,
        code: 'DEMO_PIPELINE_UNAVAILABLE',
        requestId: 'req-seed-1',
      }),
    )
    const wrapper = mountView()
    await flushPromises()

    await buttonByText(wrapper, '加载演示资料')!.trigger('click')
    await flushPromises()

    const demoCard = wrapper.find('.ep-demo')
    expect(demoCard.text()).toContain('演示数据集清单不可用')
    expect(demoCard.text()).toContain('DEMO_PIPELINE_UNAVAILABLE')
    expect(demoCard.text()).toContain('请求编号：req-seed-1')
    expect(buttonByText(wrapper, '重试加载')).toBeTruthy()
    expect(mocks.seedDemo).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('reports a network seed failure as unnumbered instead of fabricating a request id', async () => {
    mocks.fetchHealth.mockResolvedValue(health('ready'))
    mocks.seedDemo.mockRejectedValue(
      new ApiError('无法连接后端服务（网络错误或跨域被阻断）', { kind: 'network' }),
    )
    const wrapper = mountView()
    await flushPromises()

    await buttonByText(wrapper, '加载演示资料')!.trigger('click')
    await flushPromises()

    const text = wrapper.find('.ep-demo').text()
    expect(text).toContain('无法连接后端服务')
    expect(text).toContain('NETWORK_ERROR')
    expect(text).toContain('未获得服务端请求编号')
    expect(text).not.toContain('请求编号：')
    wrapper.unmount()
  })

  it('clears the previous seed error when the retry is accepted', async () => {
    mocks.fetchHealth.mockResolvedValue(health('ready'))
    mocks.seedDemo.mockRejectedValueOnce(
      new ApiError('演示数据集清单不可用', {
        kind: 'http',
        status: 503,
        code: 'DEMO_PIPELINE_UNAVAILABLE',
        requestId: 'req-seed-1',
      }),
    )
    const wrapper = mountView()
    await flushPromises()

    await buttonByText(wrapper, '加载演示资料')!.trigger('click')
    await flushPromises()
    expect(wrapper.find('.ep-demo').text()).toContain('DEMO_PIPELINE_UNAVAILABLE')

    mocks.seedDemo.mockResolvedValueOnce({
      job_id: 'job-2',
      dataset_version: '2026.1',
      target_stage: 'completed',
      status: 'queued',
      status_url: '/api/demo/jobs/job-2',
      poll_after_seconds: 2,
      reused_active_job: false,
    })
    mocks.fetchDemoJob.mockResolvedValue({
      job_id: 'job-2',
      dataset_version: '2026.1',
      target_stage: 'completed',
      status: 'running',
      current_stage: 'parsing',
      total: 1,
      imported: 0,
      resumed: 0,
      skipped: 0,
      failed: 0,
      processed: 0,
      progress_percent: 0,
      poll_after_seconds: 2,
      created_at: null,
      started_at: null,
      finished_at: null,
      documents: [],
      errors: [],
    })

    await buttonByText(wrapper, '重试加载')!.trigger('click')
    await flushPromises()

    expect(mocks.seedDemo).toHaveBeenCalledTimes(2)
    expect(wrapper.find('.ep-demo').text()).not.toContain('DEMO_PIPELINE_UNAVAILABLE')
    wrapper.unmount()
  })
})
