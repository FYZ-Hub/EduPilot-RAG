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

function routedFetch(overrides: { documentsFails?: boolean; health?: unknown; down?: boolean } = {}) {
  return vi.fn(async (input: RequestInfo | URL) => {
    if (overrides.down) {
      throw new TypeError('Failed to fetch')
    }
    const url = String(input)
    if (url.includes('/health')) {
      return jsonResponse(overrides.health ?? healthPayload)
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

async function mountLayout(
  overrides: { documentsFails?: boolean; health?: unknown; down?: boolean } = {},
): Promise<{
  wrapper: VueWrapper
  router: Router
}> {
  vi.stubGlobal('fetch', routedFetch(overrides))

  const router = createAppRouter(createMemoryHistory())
  await router.push('/knowledge')
  await router.isReady()

  const wrapper = mount(AppLayout, {
    global: { plugins: [createPinia(), router, ElementPlus] },
  })
  await flushPromises()

  return { wrapper, router }
}

/** 展开顶部分层状态面板；面板关闭时不渲染，因此必须先点击再断言。 */
async function openHealthPanel(wrapper: VueWrapper) {
  await wrapper.find('.ep-status').trigger('click')
  await flushPromises()
  return wrapper.find('.ep-health')
}

function healthPayloadWith(
  capabilities: Record<string, string>,
  providers: Record<string, unknown>,
  status = 'degraded',
) {
  return {
    status,
    version: '0.1.0',
    capabilities,
    providers,
  }
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

  it('shows the connection state and capability summary instead of a vague degraded label', async () => {
    const { wrapper } = await mountLayout()

    expect(wrapper.find('.ep-status__text').text()).toBe('后端已连接')
    expect(wrapper.find('.ep-status__hint').text()).toBe('能力 2/3 就绪')
    expect(wrapper.text()).not.toContain('后端降级运行')
    expect(wrapper.text()).toContain('local · cpu')
    expect(wrapper.text()).toContain('演示数据均为虚构')
  })

  it('expands a four-layer status panel built from the real health payload', async () => {
    const { wrapper } = await mountLayout()

    // ElPopover 的展开动画在 jsdom 中不会跑完过渡，因此这里只断言
    // 「点击后内容可访问，且各层数据来自真实响应」，不依赖 CSS 可见性。
    const panel = await openHealthPanel(wrapper)
    expect(panel.exists()).toBe(true)
    expect(panel.attributes('aria-label')).toBe('后端状态分层详情')

    const layerTitles = panel.findAll('.ep-health__layer-title').map((node) => node.text())
    expect(layerTitles).toEqual(['1. 连接', '2. 能力', '3. Provider', '4. 资料'])

    const layerText = (index: number) => panel.findAll('.ep-health__layer')[index]?.text() ?? ''

    // 连接层
    expect(layerText(0)).toContain('后端已连接')
    expect(layerText(0)).toContain('0.1.0')
    expect(layerText(0)).toContain('部分能力前置条件未满足')

    // 能力层：文档可用、问答未配置、规划可用
    expect(layerText(1)).toContain('文档功能')
    expect(layerText(1)).toContain('可用')
    expect(layerText(1)).toContain('问答')
    expect(layerText(1)).toContain('未配置')
    expect(layerText(1)).toContain('规划')

    // Provider 层：只有「尚无成功调用证据」，不判故障
    expect(layerText(2)).toContain('Embedding')
    expect(layerText(2)).toContain('Reranker')
    expect(layerText(2)).toContain('LLM')
    expect(layerText(2)).toContain('尚无成功调用证据')
    expect(layerText(2)).toContain('不代表 Provider 故障')

    // 资料层：来自真实文档列表
    expect(layerText(3)).toContain('可检索 2 / 共 3')
  })

  it('keeps the empty-library semantics: documents usable while chat is unavailable', async () => {
    const { wrapper } = await mountLayout({
      health: healthPayloadWith(
        { documents: 'ready', chat: 'unavailable', planning: 'ready' },
        {
          embedding: { provider: 'api', device: 'cpu', ready: false },
          reranker: { provider: 'api', device: 'cpu', ready: false },
          llm: { provider: 'openai_compatible', device: null, ready: false },
        },
      ),
    })

    expect(wrapper.find('.ep-status__hint').text()).toBe('能力 2/3 就绪')

    const panel = await openHealthPanel(wrapper)
    const capabilityLayer = panel.findAll('.ep-health__layer')[1]
    expect(capabilityLayer?.text()).toContain('文档功能')
    expect(capabilityLayer?.text()).toContain('问答')
    expect(capabilityLayer?.text()).toContain('不可用')
    expect(capabilityLayer?.text()).toContain('文档功能仍然可用')
  })

  it('reports every layer ready when all capabilities are ready', async () => {
    const { wrapper } = await mountLayout({
      health: healthPayloadWith(
        { documents: 'ready', chat: 'ready', planning: 'ready' },
        {
          embedding: { provider: 'api', device: 'cpu', ready: true },
          reranker: { provider: 'api', device: 'cpu', ready: true },
          llm: { provider: 'openai_compatible', device: null, ready: true },
        },
        'healthy',
      ),
    })

    expect(wrapper.find('.ep-status__hint').text()).toBe('能力 3/3 就绪')

    const panel = await openHealthPanel(wrapper)
    expect(panel.findAll('.ep-health__layer')[0]?.text()).toContain('能力前置条件全部满足')

    const providerLayer = panel.findAll('.ep-health__layer')[2]
    expect(providerLayer?.text()).toContain('已有成功调用')

    // Provider 层的取值不得出现「故障」这类断言
    const providerValues = providerLayer?.findAll('.ep-health__value').map((node) => node.text())
    expect(providerValues).toEqual([
      '已有成功调用（api · cpu）',
      '已有成功调用（api · cpu）',
      '已有成功调用（openai_compatible）',
    ])
  })

  it('never labels a provider without a successful call as failed', async () => {
    const { wrapper } = await mountLayout()
    const panel = await openHealthPanel(wrapper)

    const providerLayer = panel.findAll('.ep-health__layer')[2]
    const providerValues = providerLayer?.findAll('.ep-health__value').map((node) => node.text())
    expect(providerValues).toEqual([
      '尚无成功调用证据（local · cpu）',
      '尚无成功调用证据（local · cpu）',
      '尚无成功调用证据（openai_compatible）',
    ])
    for (const value of providerValues ?? []) {
      expect(value).not.toContain('故障')
      expect(value).not.toContain('失败')
    }
    expect(wrapper.find('.ep-status__dot').classes()).toEqual(['ep-status__dot', 'ep-status__dot--degraded'])
  })

  it('shows a partial-outage capability as unavailable without hiding the rest', async () => {
    const { wrapper } = await mountLayout({
      health: healthPayloadWith(
        { documents: 'ready', chat: 'ready', planning: 'unavailable' },
        {
          embedding: { provider: 'api', device: 'cpu', ready: true },
          reranker: { provider: 'api', device: 'cpu', ready: false },
          llm: { provider: 'openai_compatible', device: null, ready: true },
        },
      ),
    })

    expect(wrapper.find('.ep-status__hint').text()).toBe('能力 2/3 就绪')

    const panel = await openHealthPanel(wrapper)
    const capabilityLayer = panel.findAll('.ep-health__layer')[1]
    const values = capabilityLayer?.findAll('.ep-health__value').map((node) => node.text())
    expect(values).toEqual(['可用', '可用', '不可用'])
    expect(capabilityLayer?.find('[data-state="unavailable"]').exists()).toBe(true)
  })

  it('shows the connection layer only — with no fabricated numbers — when the backend is down', async () => {
    const { wrapper } = await mountLayout({ down: true })

    expect(wrapper.find('.ep-status__text').text()).toBe('后端不可连接')

    const panel = await openHealthPanel(wrapper)
    // 断连时只剩连接层与资料层，能力层/Provider 层没有可展示的真实数据
    expect(panel.findAll('.ep-health__layer-title').map((node) => node.text())).toEqual([
      '1. 连接',
      '4. 资料',
    ])
    expect(panel.text()).toContain('后端不可连接')
    expect(panel.text()).toContain('知识库概况暂不可用')
    expect(panel.text()).not.toMatch(/\d+\s*\/\s*共\s*\d+/)
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
    const { wrapper } = await mountLayout({ documentsFails: true })

    const footer = wrapper.find('.ep-sidebar__footer')
    expect(footer.text()).toContain('知识库概况暂不可用')
    expect(footer.text()).not.toMatch(/\d+\s*\/\s*共\s*\d+/)
  })

  it('shows the fixed error bar and a re-check action when the backend is down', async () => {
    const { wrapper } = await mountLayout({ down: true })

    expect(wrapper.find('.ep-content__alert').exists()).toBe(true)
    expect(wrapper.text()).toContain('后端服务不可连接')
    expect(wrapper.text()).toContain('重新检测')
  })
})
