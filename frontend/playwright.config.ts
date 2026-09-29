import { defineConfig, devices } from '@playwright/test'

/**
 * 阶段 8 端到端验收配置（只在 E2E 隔离镜像 frontend/Dockerfile.e2e 中运行）。
 *
 * - 只跑 Chromium；workers=1，涉及共享数据卷的场景严格串行；
 * - retries=0：任何 flaky 都会直接失败，不允许用重试掩盖问题；
 * - trace / screenshot / video 仅在失败时保留，且只写入容器内的 test-results；
 * - 不使用固定 sleep，等待全部交给 locator 与 expect.poll；
 * - 浏览器运行在 frontend 容器内，因此基址是可被容器自身访问的 Vite 服务。
 */
const BASE_URL = process.env.E2E_BASE_URL ?? 'http://localhost:5173'

/** 测试产物目录：Docker E2E 通过环境变量指向挂载出来的隔离目录。 */
const OUTPUT_DIR = process.env.PLAYWRIGHT_OUTPUT_DIR ?? 'test-results'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  workers: 1,
  timeout: 300_000,
  expect: { timeout: 30_000 },
  reporter: [['list'], ['json', { outputFile: `${OUTPUT_DIR}/playwright-report.json` }]],
  outputDir: OUTPUT_DIR,
  use: {
    baseURL: BASE_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    viewport: { width: 1440, height: 900 },
    locale: 'zh-CN',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
    },
  ],
})
