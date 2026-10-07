import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { useHealthStore } from './health'

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

function stubFetch(response: unknown, ok = true, status = 200): void {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok,
      status,
      text: async () => (typeof response === 'string' ? response : JSON.stringify(response)),
    }),
  )
}

describe('health store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('loads the real health payload from the backend', async () => {
    stubFetch(healthPayload)
    const store = useHealthStore()

    await store.load()

    expect(store.connection).toBe('connected')
    expect(store.data?.status).toBe('degraded')
    expect(store.capabilities?.documents).toBe('ready')
    expect(store.capabilities?.planning).toBe('ready')
    expect(store.capabilities?.chat).toBe('unconfigured')
    expect(store.runModeLabel).toBe('local · cpu')
  })

  it('marks the connection as failed when the backend is unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    const store = useHealthStore()

    await store.load()

    expect(store.connection).toBe('error')
    expect(store.data).toBeNull()
    expect(store.runModeLabel).toBeNull()
    expect(store.errorMessage).toContain('无法连接后端服务')
  })

  it('surfaces non-2xx responses as an error state', async () => {
    stubFetch({}, false, 503)
    const store = useHealthStore()

    await store.load()

    expect(store.connection).toBe('error')
    expect(store.errorMessage).toContain('503')
  })

  it('derives the connection layer text from the connection state', async () => {
    stubFetch(healthPayload)
    const store = useHealthStore()
    expect(store.connectionText).toBe('正在检测后端')
    expect(store.capabilitySummary).toBeNull()

    await store.load()

    expect(store.connectionText).toBe('后端已连接')
    expect(store.capabilitySummary).toBe('能力 2/3 就绪')
  })

  it('exposes the capability layer as three states', async () => {
    stubFetch(healthPayload)
    const store = useHealthStore()
    await store.load()

    expect(store.capabilityRows).toEqual([
      { key: 'documents', label: '文档功能', state: 'ready', text: '可用' },
      { key: 'chat', label: '问答', state: 'unconfigured', text: '未配置' },
      { key: 'planning', label: '规划', state: 'ready', text: '可用' },
    ])
    expect(store.readyCapabilityCount).toBe(2)
  })

  it('explains that chat needs retrievable documents while documents stay usable', async () => {
    stubFetch({
      ...healthPayload,
      capabilities: { documents: 'ready', chat: 'unavailable', planning: 'ready' },
    })
    const store = useHealthStore()
    await store.load()

    expect(store.chatHint).toContain('文档功能仍然可用')
    expect(store.capabilityRows[1]?.text).toBe('不可用')
    expect(store.capabilityRows[0]?.text).toBe('可用')
  })

  it('treats provider ready=false as "no successful call yet", not as a fault', async () => {
    stubFetch(healthPayload)
    const store = useHealthStore()
    await store.load()

    expect(store.providerRows.map((row) => row.text)).toEqual([
      '尚无成功调用证据',
      '尚无成功调用证据',
      '尚无成功调用证据',
    ])
    for (const row of store.providerRows) {
      expect(row.text).not.toContain('故障')
      expect(row.text).not.toContain('失败')
      expect(row.ready).toBe(false)
    }
    expect(store.providerRows[2]?.detail).toBe('openai_compatible')
  })

  it('reports successful calls once a provider has real evidence', async () => {
    stubFetch({
      ...healthPayload,
      status: 'healthy',
      capabilities: { documents: 'ready', chat: 'ready', planning: 'ready' },
      providers: {
        embedding: { provider: 'api', device: 'cpu', ready: true },
        reranker: { provider: 'api', device: 'cpu', ready: true },
        llm: { provider: 'openai_compatible', device: null, ready: true },
      },
    })
    const store = useHealthStore()
    await store.load()

    expect(store.capabilitySummary).toBe('能力 3/3 就绪')
    expect(store.serviceStatusText).toBe('能力前置条件全部满足')
    expect(store.providerRows.map((row) => row.text)).toEqual([
      '已有成功调用',
      '已有成功调用',
      '已有成功调用',
    ])
  })

  it('describes a healthy status as prerequisites met, not as end-to-end verification', async () => {
    stubFetch({ ...healthPayload, status: 'healthy' })
    const store = useHealthStore()
    await store.load()

    expect(store.serviceStatusText).toBe('能力前置条件全部满足')
    expect(store.serviceStatusText).not.toContain('通过')
    expect(store.serviceStatusText).not.toContain('验证')
  })

  it('clears every layer when the backend cannot be reached', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    const store = useHealthStore()
    await store.load()

    expect(store.connectionText).toBe('后端不可连接')
    expect(store.serviceStatusText).toBeNull()
    expect(store.capabilitySummary).toBeNull()
    expect(store.capabilityRows).toEqual([])
    expect(store.providerRows).toEqual([])
    expect(store.chatHint).toBeNull()
  })
})
