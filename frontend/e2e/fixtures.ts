import { expect, test as base, type Page } from '@playwright/test'

/**
 * 阶段 8 端到端共享 fixture。
 *
 * - 每个页面都收集 ``pageerror`` 与 ``console`` error；
 * - ``pageerror``（未处理异常 / 未处理 Promise）必须始终为零，测试结束即断言；
 * - console error 由各场景显式断言（故障注入场景允许浏览器原生的网络失败日志）。
 */
export interface BrowserIssue {
  kind: 'pageerror' | 'console'
  text: string
}

export const test = base.extend<{ issues: BrowserIssue[] }>({
  issues: async ({ page }, use) => {
    const issues: BrowserIssue[] = []
    page.on('pageerror', (error) => {
      issues.push({ kind: 'pageerror', text: String(error.message) })
    })
    page.on('console', (message) => {
      if (message.type() === 'error') {
        issues.push({ kind: 'console', text: message.text() })
      }
    })

    await use(issues)

    const pageErrors = issues.filter((issue) => issue.kind === 'pageerror')
    expect(pageErrors, `未处理的页面错误：${JSON.stringify(pageErrors)}`).toEqual([])
  },
})

/**
 * 过滤掉「本场景主动注入的故障」产生的浏览器原生日志。
 *
 * ``allowedConsolePatterns`` 默认为空，即：任何 console.error 都算问题。
 * 只有场景 7 会显式传入它注入的精确错误模式；禁止在共享函数里全局忽略网络错误。
 */
export function applicationIssues(
  issues: BrowserIssue[],
  allowedConsolePatterns: RegExp[] = [],
): BrowserIssue[] {
  return issues.filter(
    (issue) =>
      issue.kind !== 'console' ||
      !allowedConsolePatterns.some((pattern) => pattern.test(issue.text)),
  )
}

/** 真实运行数据：上传与学业导入都使用仓库内固定的虚构语料（只读挂载）。 */
export const CORPUS_DIR = process.env.E2E_CORPUS_DIR ?? '/app/e2e-fixtures/corpus'
export const BROKEN_FIXTURE =
  process.env.E2E_BROKEN_FIXTURE ?? '/app/e2e/assets/broken-upload.pdf'

export const API_BASE = process.env.E2E_API_BASE ?? 'http://backend:8000/api'

export function corpusFile(name: string): string {
  return `${CORPUS_DIR}/${name}`
}

/** 等待演示资料任务进入终态（由页面自身的轮询驱动）。 */
export async function waitForJobTerminal(page: Page): Promise<void> {
  await expect(page.locator('.ep-job__header .el-tag')).toHaveText(
    /已完成|完成但有失败项/,
    { timeout: 280_000 },
  )
}

/** 等宽表格行：按文件名 + 来源定位（同名 demo 与 upload 文档必须区分开）。 */
export function documentRow(page: Page, fileName: string, sourceLabel?: string) {
  let row = page.locator('.ep-table-scroll tbody tr').filter({ hasText: fileName })
  if (sourceLabel) {
    row = row.filter({ hasText: sourceLabel })
  }
  return row
}

export { expect }
