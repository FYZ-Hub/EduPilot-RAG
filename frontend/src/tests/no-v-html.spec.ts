import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

/**
 * UI_SPEC 9：文档 quote、文件名与错误消息一律按纯文本渲染，
 * 禁止 ``v-html`` 直接渲染后端或模型内容。
 */
function vueFiles(directory: string): string[] {
  const entries: string[] = []
  for (const name of readdirSync(directory)) {
    const fullPath = join(directory, name)
    if (statSync(fullPath).isDirectory()) {
      entries.push(...vueFiles(fullPath))
    } else if (name.endsWith('.vue')) {
      entries.push(fullPath)
    }
  }
  return entries
}

describe('rendering safety', () => {
  it('never uses the v-html directive in any component template', () => {
    const srcDir = resolve(process.cwd(), 'src')
    const offenders = vueFiles(srcDir)
      .filter((file) => /\bv-html\s*=/.test(readFileSync(file, 'utf8')))
      .map((file) => file.replace(srcDir, 'src'))

    expect(offenders).toEqual([])
  })
})
