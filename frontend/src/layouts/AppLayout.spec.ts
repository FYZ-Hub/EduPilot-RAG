import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createPinia } from 'pinia'
import { createMemoryHistory, type Router } from 'vue-router'

import AppLayout from './AppLayout.vue'
import { createAppRouter } from '@/router'

const healthPayload = {
  status: 'degraded',
  version: '0.1.0',
  capabilities: { documents: 'ready', chat: 'unconfigured', planning: 'ready' },
  providers: {
    embedding: { provider: 'local', device: 'cpu', ready: false },
    reranker: { provider: 'local', device: 'cpu', ready: false },
    llm: { provider: 'openai_compatible', device: null, ready: false },
  },
}

const documentsPayload = {
  items: [],
  total: 3,
  counts: { ready: 2, retrievable: 2, processing: 1, failed: 0 },
}

const demoStatusPayload = {
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
}

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
  } as unknown as Response
}

function routedFetch(overrides: { documentsFails?: boolean } = {}) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.includes('/health')) {
      return jsonResponse(healthPayload)
    }
    if (url.includes('/demo/status')) {
      return jsonResponse(demoStatusPayload)
    }
    if (url.includes('/documents')) {
      if (overrides.documentsFails) {
        throw new TypeError('Failed to fetch')
      }
      return jsonResponse(documentsPayload)
    }
    throw new Error(`unexpected request: ${url}`)
  })
}

async function mountLayout(): Promise<{ wrapper: VueWrapper; router: Router }> {
  const router = createAppRouter(createMemoryHistory())
  await router.push('/knowledge')
  await router.isReady()

  const wrapper = mount(AppLayout, {
    global: { plugins: [createPinia(), router, ElementPlus] },
  })
  await flushPromises()

  return { wrapper, router }
}

function sidebarNavLinks(wrapper: VueWrapper) {
  return wrapper.find('.ep-sidebar').findAll('.ep-nav__link')
}

describe('AppLayout', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', routedFetch())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('exposes "跳到主要内容" as the first focusable element', async () => {
    const { wrapper } = await mountLayout()

    const skipLink = wrapper.find('.ep-skip-link')
    expect(skipLink.exists()).toBe(true)
    expect(skipLink.text()).toBe('跳到主要内容')
    expect(skipLink.attributes('href')).toBe('#main-content')
    expect(wrapper.find('#main-content').exists()).toBe(true)
  })

  it('renders the three fixed navigation entries in order', async () => {
    const { wrapper } = await mountLayout()

    expect(sidebarNavLinks(wrapper).map((link) => link.text())).toEqual([
      '知识库管理',
      'RAG 问答',
      '学业规划',
    ])
  })

  it('marks exactly the active entry with aria-current="page"', async () => {
    const { wrapper, router } = await mountLayout()

    const active = sidebarNavLinks(wrapper).filter(
      (link) => link.attributes('aria-current') === 'page',
    )
    expect(active).toHaveLength(1)
    expect(active[0]?.text()).toBe('知识库管理')

    await router.push('/planning')
    await flushPromises()

    const activeAfterNavigation = sidebarNavLinks(wrapper).filter(
      (link) => link.attributes('aria-current') === 'page',
    )
    expect(activeAfterNavigation).toHaveLength(1)
    expect(activeAfterNavigation[0]?.text()).toBe('学业规划')
  })

  it('gives every navigation link an accessible name independent of the responsive CSS', async () => {
    const { wrapper } = await mountLayout()

    // 768–1199px 会通过 CSS 隐藏 .ep-nav__label（图标侧栏），
    // 因此链接必须自带 aria-label，否则图标模式下没有任何可访问名称。
    expect(sidebarNavLinks(wrapper).map((link) => link.attributes('aria-label'))).toEqual([
      '知识库管理',
      'RAG 问答',
      '学业规划',
    ])
  })

  it('shows backend status and CPU run mode from the real health API', async () => {
    const { wrapper } = await mountLayout()

    expect(wrapper.find('.ep-status__text').text()).toBe('后端降级运行')
    expect(wrapper.text()).toContain('local · cpu')
    expect(wrapper.text()).toContain('演示数据均为虚构')
  })

  it('shows the knowledge summary from the real document list and demo status', async () => {
    const { wrapper } = await mountLayout()

    const footer = wrapper.find('.ep-sidebar__footer')
    expect(footer.text()).toContain('知识库概况')
    expect(footer.text()).toContain('可检索 2 / 共 3')
    expect(footer.text()).toContain('演示数据：空知识库')
    expect(footer.text()).not.toContain('阶段 1')
  })

  it('never fabricates numbers when the document list is unreachable', async () => {
    vi.stubGlobal('fetch', routedFetch({ documentsFails: true }))
    const { wrapper } = await mountLayout()

    const footer = wrapper.find('.ep-sidebar__footer')
    expect(footer.text()).toContain('知识库概况暂不可用')
    expect(footer.text()).not.toMatch(/\d+\s*\/\s*共\s*\d+/)
  })

  it('shows the fixed error bar and a re-check action when the backend is down', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    const { wrapper } = await mountLayout()

    expect(wrapper.find('.ep-content__alert').exists()).toBe(true)
    expect(wrapper.text()).toContain('后端服务不可连接')
    expect(wrapper.text()).toContain('重新检测')
  })
})
