import { describe, expect, it } from 'vitest'

import { AUTO_SCROLL_THRESHOLD, isNearBottom } from './scroll'

describe('chat auto-scroll decision', () => {
  it('follows while the user is at the bottom', () => {
    expect(isNearBottom({ scrollTop: 700, scrollHeight: 1000, clientHeight: 300 })).toBe(true)
  })

  it('stops following after the user scrolls up beyond the threshold', () => {
    expect(isNearBottom({ scrollTop: 500, scrollHeight: 1000, clientHeight: 300 })).toBe(false)
  })

  it('still follows within the threshold band', () => {
    expect(
      isNearBottom({
        scrollTop: 1000 - 300 - AUTO_SCROLL_THRESHOLD,
        scrollHeight: 1000,
        clientHeight: 300,
      }),
    ).toBe(true)
    expect(
      isNearBottom({
        scrollTop: 1000 - 300 - AUTO_SCROLL_THRESHOLD - 1,
        scrollHeight: 1000,
        clientHeight: 300,
      }),
    ).toBe(false)
  })

  it('follows when the content is shorter than the viewport', () => {
    expect(isNearBottom({ scrollTop: 0, scrollHeight: 120, clientHeight: 300 })).toBe(true)
  })
})
