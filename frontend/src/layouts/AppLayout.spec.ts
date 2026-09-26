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
  capabilities: { documents: 'unavailable', chat: 'unconfigured', planning: 'unavailable' },
  providers: {
    embedding: { provider: 'local', device: 'cpu', ready: false },
    reranker: { provider: 'local', device: 'cpu', ready: false },
    llm: { provider: 'openai_compatible', device: null, ready: false },
  },
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
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => healthPayload }),
    )
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

  it('shows backend status and CPU run mode from the real health API', async () => {
    const { wrapper } = await mountLayout()

    expect(wrapper.find('.ep-status__text').text()).toBe('后端降级运行')
    expect(wrapper.text()).toContain('local · cpu')
    expect(wrapper.text()).toContain('演示数据均为虚构')
  })

  it('does not fabricate document counts in the sidebar summary', async () => {
    const { wrapper } = await mountLayout()

    const footer = wrapper.find('.ep-sidebar__footer')
    expect(footer.text()).toContain('暂无可用文档数据')
    expect(footer.text()).not.toMatch(/\d+\s*个文档/)
  })
})
