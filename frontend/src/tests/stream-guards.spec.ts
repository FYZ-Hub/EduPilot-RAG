import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

/**
 * Chat 协议层的源码级守卫（UI_SPEC 6.5 / 9、PRODUCT_SPEC 6.3）：
 * 不得使用浏览器原生的事件源接口（接口是 POST，必须走 fetch 流），
 * 也不得把问题、回答或 quote 写进浏览器控制台。
 *
 * 被禁字面量由片段拼接而成，避免守卫文件自身命中。
 */
const FORBIDDEN: Array<{ label: string; needle: string }> = [
  { label: 'Event' + 'Source usage', needle: 'Event' + 'Source' },
  { label: 'con' + 'sole' + ' output', needle: 'con' + 'sole' + '.' },
]

function sourceFiles(directory: string): string[] {
  const entries: string[] = []
  for (const name of readdirSync(directory)) {
    const fullPath = join(directory, name)
    if (statSync(fullPath).isDirectory()) {
      entries.push(...sourceFiles(fullPath))
    } else if (name.endsWith('.ts') || name.endsWith('.vue')) {
      entries.push(fullPath)
    }
  }
  return entries
}

describe('chat protocol source guards', () => {
  it('never uses the browser native event-source API nor logs content to the console', () => {
    const srcDir = resolve(process.cwd(), 'src')
    const offenders: string[] = []

    for (const file of sourceFiles(srcDir)) {
      const content = readFileSync(file, 'utf8')
      for (const { label, needle } of FORBIDDEN) {
        if (content.includes(needle)) {
          offenders.push(`${file.replace(srcDir, 'src')} → ${label}`)
        }
      }
    }

    expect(offenders).toEqual([])
  })
})
