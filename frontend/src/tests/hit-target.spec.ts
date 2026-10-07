import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { afterEach, describe, expect, it, vi } from 'vitest'

/**
 * UI_SPEC 10：可点击目标桌面最小 40×40px、移动端最小 44×44px。
 *
 * 这里做**样式守卫**（必须显式声明 min-height，不能靠文字行高碰巧撑大）；
 * 真实 bounding box 由下一轮 Playwright 端到端验收负责。
 */
function styleBlockOf(relativePath: string): string {
  const source = readFileSync(resolve(process.cwd(), 'src', relativePath), 'utf8')
  const matched = /<style scoped>([\s\S]*?)<\/style>/.exec(source)
  return matched ? matched[1] : ''
}

/** 取出某个选择器所在规则的声明块（简单规则，无嵌套）。 */
function declarationsFor(css: string, selector: string): string {
  const start = css.indexOf(selector)
  if (start < 0) {
    return ''
  }
  const open = css.indexOf('{', start)
  if (open < 0) {
    return ''
  }
  const close = css.indexOf('}', open)
  return close < 0 ? '' : css.slice(open + 1, close)
}

/** 取出某个媒体查询块的全部内容。 */
function mediaBlock(css: string, query: string): string {
  const start = css.indexOf(query)
  if (start < 0) {
    return ''
  }
  const open = css.indexOf('{', start)
  return open < 0 ? '' : css.slice(open + 1)
}

const TARGETS: Array<{ file: string; selectors: string[] }> = [
  { file: 'components/academic/MissingCourseList.vue', selectors: ['.ep-evidence-chip'] },
  { file: 'components/academic/ConflictWarningPanel.vue', selectors: ['.ep-evidence-chip'] },
  {
    file: 'components/common/SourceEvidenceCard.vue',
    selectors: ['.ep-evidence-card__select', '.ep-evidence-card__open'],
  },
]

afterEach(() => {
  vi.restoreAllMocks()
})

describe('hit target sizing guards', () => {
  it.each(TARGETS)('$file declares 40px desktop targets', ({ file, selectors }) => {
    const css = styleBlockOf(file)

    for (const selector of selectors) {
      expect(declarationsFor(css, selector), `${file} → ${selector}`).toContain('min-height: 40px')
    }
  })

  it.each(TARGETS)('$file declares 44px mobile targets inside a 767px query', ({ file, selectors }) => {
    const css = styleBlockOf(file)
    const mobile = mediaBlock(css, '@media (max-width: 767px)')

    expect(mobile).not.toBe('')
    for (const selector of selectors) {
      expect(mobile, `${file} → ${selector}`).toContain(selector)
    }
    expect(mobile).toContain('min-height: 44px')

    // 44px 必须出现在移动端媒体查询里，而不是散落在别处
    expect(declarationsFor(css, selectors[0])).not.toContain('min-height: 44px')
  })

  it.each(TARGETS)('$file keeps a visible focus ring for keyboard users', ({ file, selectors }) => {
    const css = styleBlockOf(file)

    for (const selector of selectors) {
      const focusRule = `${selector}:focus-visible`
      expect(css, `${file} → ${focusRule}`).toContain(focusRule)
    }
  })
})
