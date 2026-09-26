import { describe, expect, it } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import { createAppRouter } from './index'

describe('router', () => {
  it('redirects / to /knowledge', async () => {
    const router = createAppRouter(createMemoryHistory())

    await router.push('/')
    await router.isReady()

    expect(router.currentRoute.value.path).toBe('/knowledge')
    expect(router.currentRoute.value.name).toBe('knowledge')
  })

  it('resolves the three core routes', async () => {
    const router = createAppRouter(createMemoryHistory())

    await router.push('/knowledge')
    await router.isReady()
    expect(router.currentRoute.value.name).toBe('knowledge')

    await router.push('/chat')
    expect(router.currentRoute.value.name).toBe('chat')

    await router.push('/planning')
    expect(router.currentRoute.value.name).toBe('planning')
  })

  it('falls back to the not-found route for unknown paths', async () => {
    const router = createAppRouter(createMemoryHistory())

    await router.push('/definitely-not-a-page')
    await router.isReady()

    expect(router.currentRoute.value.name).toBe('not-found')
  })
})
