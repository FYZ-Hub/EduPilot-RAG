import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'

import HomeView from './HomeView.vue'
import { ApiError } from '@/api/client'
import type { HealthResponse } from '@/api/health'
import { createAppRouter } from '@/router'
import { useDocumentsStore } from '@/stores/documents'
import { useHealthStore } from '@/stores/health'

const healthReady: HealthResponse = {
  status: 'healthy',
  version: '0.1.0',
  capabilities: { documents: 'ready', chat: 'ready', planning: 'ready' },
  providers: {
    embedding: { provider: 'api', device: 'cpu', ready: true },
    reranker: { provider: 'api', device: 'cpu', ready: true },
    llm: { provider: 'openai_compatible', device: null, ready: true },
  },
}

function healthWith(capabilities: HealthResponse['capabilities']): HealthResponse {
  return { ...healthReady, capabilities }
}

interface Stores {
  documents: ReturnType<typeof useDocumentsStore>
  health: ReturnType<typeof useHealthStore>
}

/** 直接构造 store 状态，验证首页只读现有 store、不自行发起请求。 */
async function mountHome(
  configure?: (stores: Stores) => void,
): Promise<{ wrapper: VueWrapper; stores: Stores }> {
  const pinia = createPinia()
  setActivePinia(pinia)
  const stores: Stores = { documents: useDocumentsStore(), health: useHealthStore() }
  configure?.(stores)

  const router = createAppRouter(createMemoryHistory())
  await router.push('/')
  await router.isReady()

  const wrapper = mount(HomeView, {
    global: { plugins: [pinia, router, ElementPlus] },
  })
  await flushPromises()
  return { wrapper, stores }
}

function loadedStores(stores: Stores, capabilities?: HealthResponse['capabilities']) {
  stores.documents.$patch({
    loaded: true,
    total: 15,
    counts: { ready: 15, retrievable: 15, processing: 0, failed: 0 },
  })
  stores.health.$patch({
    connection: 'connected',
    data: capabilities ? healthWith(capabilities) : healthReady,
  })
}

function styleBlock(): string {
  const source = readFileSync(resolve(process.cwd(), 'src/views/HomeView.vue'), 'utf8')
  return /<style scoped>([\s\S]*?)<\/style>/.exec(source)?.[1] ?? ''
}

describe('HomeView', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('introduces the product and links the three core capabilities', async () => {
    const { wrapper } = await mountHome((stores) => loadedStores(stores))

    expect(wrapper.find('.ep-home__title').text()).toBe('用可信资料，回答学业问题')

    const cards = wrapper.findAll('.ep-home__card')
    expect(cards).toHaveLength(3)
    expect(cards.map((card) => card.find('.ep-home__card-title').text())).toEqual([
      '知识库管理',
      'RAG 问答',
      '学业规划',
    ])
    expect(cards.map((card) => card.find('.ep-home__card-cta').attributes('href'))).toEqual([
      '/knowledge',
      '/chat',
      '/planning',
    ])
  })

  it('shows the real library numbers taken from the documents store', async () => {
    const { wrapper } = await mountHome((stores) => loadedStores(stores))

    const statValues = wrapper.findAll('.ep-home__stat-value')
    expect(statValues[0]?.text()).toBe('15 / 15')
    expect(wrapper.findAll('.ep-home__stat')[0]?.text()).toContain('共 15 份资料，其中 15 份可检索')
    expect(statValues[1]?.text()).toBe('后端已连接')
  })

  it('never fabricates a count when the document list is unreachable', async () => {
    const { wrapper } = await mountHome((stores) => {
      stores.documents.$patch({ loaded: false })
      stores.documents.error = new ApiError('无法连接后端服务（网络错误或跨域被阻断）', {
        kind: 'network',
      })
      stores.health.$patch({ connection: 'error' })
    })

    const libraryStat = wrapper.findAll('.ep-home__stat')[0]
    expect(libraryStat?.text()).toContain('可检索文档')
    expect(libraryStat?.text()).toContain('知识库概况暂不可用')
    expect(libraryStat?.text()).not.toMatch(/\d+\s*\/\s*\d+/)
    expect(wrapper.find('.ep-home__stat-value').text()).toBe('—')
  })

  it('reports each capability status without inventing a failure', async () => {
    const { wrapper } = await mountHome((stores) =>
      loadedStores(stores, { documents: 'ready', chat: 'unavailable', planning: 'ready' }),
    )

    const pills = wrapper.findAll('.ep-home__pill')
    expect(pills.map((pill) => pill.text())).toEqual(['可用', '不可用', '可用'])
    expect(wrapper.text()).not.toContain('故障')
  })

  it('issues no network request of its own while rendering', async () => {
    const fetchSpy = vi.fn(() => {
      throw new Error('HomeView must not fetch')
    })
    vi.stubGlobal('fetch', fetchSpy)

    const { wrapper } = await mountHome((stores) => loadedStores(stores))

    expect(fetchSpy).not.toHaveBeenCalled()
    expect(wrapper.findAll('.ep-home__card')).toHaveLength(3)
  })

  it('keeps the ambient motion cheap and disables it for reduced-motion users', async () => {
    const css = styleBlock()

    // 只动 transform / opacity，避免持续高负载动画
    expect(css).toContain('@keyframes ep-home-drift')
    expect(css).toContain('@keyframes ep-home-glow')
    expect(css).toMatch(/transform:\s*translate3d/)

    const motionBlock = /@media \(prefers-reduced-motion: reduce\) \{([\s\S]*?)\n\}/.exec(css)?.[1] ?? ''
    expect(motionBlock).not.toBe('')
    expect(motionBlock).toContain('.ep-home__network')
    expect(motionBlock).toContain('.ep-home__glow')
    expect(motionBlock).toContain('.ep-home__reveal')
    expect(motionBlock).toContain('animation: none')

    // 组件内不散落十六进制颜色（UI_SPEC 3.1）
    expect(css).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
  })

  it('declares 40px tap targets on desktop and 44px on mobile', async () => {
    const css = styleBlock()
    const mobile = /@media \(max-width: 767px\) \{([\s\S]*?)\n\}/.exec(css)?.[1] ?? ''

    expect(css).toContain('min-height: 40px')
    expect(mobile).toContain('min-height: 44px')
  })
})
