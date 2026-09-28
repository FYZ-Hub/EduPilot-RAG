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
})
