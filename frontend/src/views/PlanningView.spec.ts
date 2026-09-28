import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import { ApiError } from '@/api/client'
import type { AcademicOptions, ImportResult, PlanningResult } from '@/api/academic'
import type { HealthResponse, CapabilityState } from '@/api/health'

const mocks = vi.hoisted(() => ({
  fetchHealth: vi.fn(),
  fetchAcademicOptions: vi.fn(),
  importAcademicRecords: vi.fn(),
  importAcademicRules: vi.fn(),
  calculateAcademicPlan: vi.fn(),
  fetchSource: vi.fn(),
}))

vi.mock('@/api/health', async () => {
  const actual = await vi.importActual<typeof import('@/api/health')>('@/api/health')
  return { ...actual, fetchHealth: mocks.fetchHealth }
})

vi.mock('@/api/academic', async () => {
  const actual = await vi.importActual<typeof import('@/api/academic')>('@/api/academic')
  return {
    ...actual,
    fetchAcademicOptions: mocks.fetchAcademicOptions,
    importAcademicRecords: mocks.importAcademicRecords,
    importAcademicRules: mocks.importAcademicRules,
    calculateAcademicPlan: mocks.calculateAcademicPlan,
  }
})

vi.mock('@/api/retrieval', async () => {
  const actual = await vi.importActual<typeof import('@/api/retrieval')>('@/api/retrieval')
  return { ...actual, fetchSource: mocks.fetchSource }
})

import PlanningView from './PlanningView.vue'
import { useAcademicStore } from '@/stores/academic'

function health(planning: CapabilityState = 'ready'): HealthResponse {
  return {
    status: 'degraded',
    version: '0.1.0',
    capabilities: { documents: 'ready', chat: 'unconfigured', planning },
    providers: {
      embedding: { provider: 'local', device: 'cpu', ready: false },
      reranker: { provider: 'local', device: 'cpu', ready: false },
      llm: { provider: 'unconfigured', device: null, ready: false },
    },
  }
}

function options(recordIds: string[], ruleIds: string[]): AcademicOptions {
  return {
    record_sets: recordIds.map((id) => ({
      id,
      name: `课程记录 ${id}`,
      source_doc_id: `doc-${id}`,
      status: 'ready',
      updated_at: '2026-09-01T00:00:00Z',
    })),
    rule_sets: ruleIds.map((id) => ({
      id,
      name: `培养方案 ${id}`,
      major: '计算机科学与技术',
      admission_year: 2026,
      rule_version: `v-${id}`,
      effective_from: '2026-09-01',
      source_doc_id: `doc-${id}`,
      status: 'ready',
    })),
  }
}

function plan(overrides: Partial<PlanningResult> = {}): PlanningResult {
  return {
    required_credits: 155.0,
    completed_credits: 100.0,
    in_progress_credits: 20.0,
    remaining_credits: 35.0,
    missing_required_courses: [],
    category_gaps: [],
    conflict_warnings: [],
    evidence: [],
    ...overrides,
  }
}

function importResult(id: string): ImportResult {
  return { id, status: 'ready', warnings: [] }
}

async function mountView(
  query: Record<string, string | string[]> = {},
): Promise<{ wrapper: VueWrapper; router: Router }> {
  setActivePinia(createPinia())
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/planning', name: 'planning', component: PlanningView }],
  })
  await router.push({ path: '/planning', query })
  await router.isReady()
  const wrapper = mount(PlanningView, { global: { plugins: [router, ElementPlus] } })
  await flushPromises()
  return { wrapper, router }
}

function buttonByText(wrapper: VueWrapper, label: string) {
  return wrapper.findAll('button').find((node) => node.text().trim() === label)
}

/** 覆盖全局 matchMedia 替身，模拟 1200px 断点两侧。 */
function stubMatchMedia(matches: boolean): void {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  })
}

async function selectBoth(wrapper: VueWrapper, record: string, rule: string): Promise<void> {
  await wrapper.find('.ep-selection__record').setValue(record)
  await wrapper.find('.ep-selection__rule').setValue(rule)
  await flushPromises()
}

beforeEach(() => {
  for (const mock of Object.values(mocks)) {
    mock.mockReset()
  }
  // 默认按桌面宽屏（证据面板常驻）渲染
  stubMatchMedia(true)
  mocks.fetchHealth.mockResolvedValue(health())
  mocks.fetchAcademicOptions.mockResolvedValue(options([], []))
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('PlanningView capability gating', () => {
  it('shows a loading state while the options request is in flight', async () => {
    let release: ((value: AcademicOptions) => void) | null = null
    mocks.fetchAcademicOptions.mockImplementation(
      () => new Promise<AcademicOptions>((resolve) => (release = resolve)),
    )

    const { wrapper } = await mountView()

    expect(wrapper.find('.ep-planning__loading').exists()).toBe(true)
    expect(buttonByText(wrapper, '开始计算')!.attributes('disabled')).toBeDefined()

    release!(options(['rs-1'], ['ru-1']))
    await flushPromises()
  })

  it('treats empty options as a valid empty state rather than an interface error', async () => {
    const { wrapper } = await mountView()

    expect(wrapper.text()).toContain('还没有可用的课程记录')
    expect(wrapper.find('.ep-error-alert').exists()).toBe(false)
    expect(buttonByText(wrapper, '开始计算')!.attributes('disabled')).toBeDefined()
  })

  it('shows the real options error next to the selectors and can retry', async () => {
    mocks.fetchAcademicOptions
      .mockRejectedValueOnce(new ApiError('无法连接后端服务（网络错误或跨域被阻断）', { kind: 'network' }))
      .mockResolvedValueOnce(options(['rs-1'], ['ru-1']))

    const { wrapper } = await mountView()

    const alert = wrapper.find('.ep-error-alert')
    expect(alert.text()).toContain('NETWORK_ERROR')
    expect(alert.text()).toContain('未获得服务端请求编号')

    await wrapper.find('.ep-selection__retry').trigger('click')
    await flushPromises()

    expect(mocks.fetchAcademicOptions).toHaveBeenCalledTimes(2)
    expect(wrapper.find('.ep-error-alert').exists()).toBe(false)
  })

  it('disables import and calculation when planning is unavailable', async () => {
    mocks.fetchHealth.mockResolvedValue(health('unavailable'))

    const { wrapper } = await mountView()

    expect(wrapper.text()).toContain('规划能力不可用')
    expect(wrapper.find('.ep-planning__recheck').exists()).toBe(true)
    expect(buttonByText(wrapper, '导入课程记录')!.attributes('disabled')).toBeDefined()
    expect(buttonByText(wrapper, '导入培养规则')!.attributes('disabled')).toBeDefined()
    expect(buttonByText(wrapper, '开始计算')!.attributes('disabled')).toBeDefined()
  })

  it('reports an unreachable backend with a manual recheck', async () => {
    mocks.fetchHealth.mockRejectedValue(new Error('offline'))

    const { wrapper } = await mountView()

    expect(wrapper.find('.ep-planning__backend-error').exists()).toBe(true)

    mocks.fetchHealth.mockResolvedValue(health())
    await wrapper.find('.ep-planning__recheck').trigger('click')
    await flushPromises()

    expect(wrapper.find('.ep-planning__backend-error').exists()).toBe(false)
  })

  it('never imports or calculates on its own', async () => {
    mocks.fetchAcademicOptions.mockResolvedValue(options(['rs-1'], ['ru-1']))

    await mountView()

    expect(mocks.importAcademicRecords).not.toHaveBeenCalled()
    expect(mocks.importAcademicRules).not.toHaveBeenCalled()
    expect(mocks.calculateAcademicPlan).not.toHaveBeenCalled()
  })
})

describe('PlanningView selection and url', () => {
  it('preselects a lone ready option', async () => {
    mocks.fetchAcademicOptions.mockResolvedValue(options(['rs-1'], ['ru-1']))

    const { wrapper } = await mountView()

    expect((wrapper.find('.ep-selection__record').element as HTMLSelectElement).value).toBe('rs-1')
    expect(buttonByText(wrapper, '开始计算')!.attributes('disabled')).toBeUndefined()
  })

  it('never selects anything when several options exist', async () => {
    mocks.fetchAcademicOptions.mockResolvedValue(options(['rs-1', 'rs-2'], ['ru-1', 'ru-2']))

    const { wrapper } = await mountView()

    expect((wrapper.find('.ep-selection__record').element as HTMLSelectElement).value).toBe('')
    expect(buttonByText(wrapper, '开始计算')!.attributes('disabled')).toBeDefined()
  })

  it('restores a valid selection from the url and keeps unrelated query fields', async () => {
    mocks.fetchAcademicOptions.mockResolvedValue(options(['rs-1', 'rs-2'], ['ru-1', 'ru-2']))

    const { wrapper, router } = await mountView({ record_set_id: 'rs-2', rule_set_id: 'ru-1', tab: 'x' })

    expect((wrapper.find('.ep-selection__record').element as HTMLSelectElement).value).toBe('rs-2')
    expect((wrapper.find('.ep-selection__rule').element as HTMLSelectElement).value).toBe('ru-1')
    expect(router.currentRoute.value.query.tab).toBe('x')
    expect(wrapper.find('.ep-planning__stale-notice').exists()).toBe(false)
  })

  it('clears an invalid url id, removes the field and explains it', async () => {
    mocks.fetchAcademicOptions.mockResolvedValue(options(['rs-1', 'rs-2'], ['ru-1', 'ru-2']))

    const { wrapper, router } = await mountView({ record_set_id: 'gone', rule_set_id: 'ru-1' })

    expect(wrapper.find('.ep-planning__stale-notice').text()).toContain('之前选择的数据已失效，请重新选择')
    expect(router.currentRoute.value.query.record_set_id).toBeUndefined()
    expect(router.currentRoute.value.query.rule_set_id).toBe('ru-1')
  })

  it('writes the selection to the url without looping', async () => {
    mocks.fetchAcademicOptions.mockResolvedValue(options(['rs-1', 'rs-2'], ['ru-1', 'ru-2']))

    const { wrapper, router } = await mountView({ tab: 'x' })
    const optionsCalls = mocks.fetchAcademicOptions.mock.calls.length

    await selectBoth(wrapper, 'rs-1', 'ru-1')

    expect(router.currentRoute.value.query.record_set_id).toBe('rs-1')
    expect(router.currentRoute.value.query.rule_set_id).toBe('ru-1')
    expect(router.currentRoute.value.query.tab).toBe('x')
    // URL 与 Store 的同步不得再次触发选项加载
    expect(mocks.fetchAcademicOptions.mock.calls.length).toBe(optionsCalls)
  })

  it('keeps the calculate button disabled until both ids are chosen', async () => {
    mocks.fetchAcademicOptions.mockResolvedValue(options(['rs-1', 'rs-2'], ['ru-1', 'ru-2']))

    const { wrapper } = await mountView()
    await wrapper.find('.ep-selection__record').setValue('rs-1')
    await flushPromises()

    expect(buttonByText(wrapper, '开始计算')!.attributes('disabled')).toBeDefined()

    await wrapper.find('.ep-selection__rule').setValue('ru-1')
    await flushPromises()

    expect(buttonByText(wrapper, '开始计算')!.attributes('disabled')).toBeUndefined()
  })
})

describe('PlanningView calculation', () => {
  it('calculates only after the user clicks and never repeats while in flight', async () => {
    mocks.fetchAcademicOptions.mockResolvedValue(options(['rs-1'], ['ru-1']))
    let release: ((value: PlanningResult) => void) | null = null
    mocks.calculateAcademicPlan.mockImplementation(
      () => new Promise<PlanningResult>((resolve) => (release = resolve)),
    )

    const { wrapper } = await mountView()
    expect(mocks.calculateAcademicPlan).not.toHaveBeenCalled()

    const button = buttonByText(wrapper, '开始计算')!
    await button.trigger('click')
    await flushPromises()

    expect(mocks.calculateAcademicPlan).toHaveBeenCalledTimes(1)
    expect(mocks.calculateAcademicPlan.mock.calls[0][0]).toEqual({
      record_set_id: 'rs-1',
      rule_set_id: 'ru-1',
    })

    await button.trigger('click')
    await flushPromises()
    expect(mocks.calculateAcademicPlan).toHaveBeenCalledTimes(1)

    release!(plan())
    await flushPromises()
  })

  it('shows the four credit cards, gaps, missing courses, warnings and evidence', async () => {
    mocks.fetchAcademicOptions.mockResolvedValue(options(['rs-1'], ['ru-1']))
    mocks.calculateAcademicPlan.mockResolvedValue(
      plan({
        missing_required_courses: [
          {
            course_code: 'QM-CS401',
            course_name: '毕业设计',
            credits: 8.0,
            category: '专业必修',
            evidence_chunk_ids: ['c'.repeat(64)],
          },
        ],
        category_gaps: [
          {
            category: '专业必修',
            required_credits: 60.0,
            completed_credits: 40.0,
            in_progress_credits: 12.0,
            remaining_credits: 8.0,
          },
        ],
        conflict_warnings: [
          {
            code: 'DEGREE_PLAN_VERSION_CONFLICT',
            message: '存在两个生效版本',
            severity: 'warning',
            evidence_chunk_ids: [],
          },
        ],
        evidence: [
          {
            chunk_id: 'c'.repeat(64),
            doc_id: 'doc-1',
            file_name: '培养方案.pdf',
            document_version: '2026.1',
            effective_from: '2026-09-01',
            page_number: 4,
            sheet_name: null,
            row_start: null,
            row_end: null,
            section_title: '培养方案/总则',
            quote: '毕业总学分 155.0',
          },
        ],
      }),
    )

    const { wrapper } = await mountView()
    await buttonByText(wrapper, '开始计算')!.trigger('click')
    await flushPromises()

    expect(wrapper.find('.ep-credit-summary').exists()).toBe(true)
    expect(wrapper.find('.ep-gap-item').text()).toContain('专业必修')
    expect(wrapper.find('.ep-missing__row').text()).toContain('QM-CS401')
    expect(wrapper.find('.ep-conflict-item').text()).toContain('DEGREE_PLAN_VERSION_CONFLICT')
    expect(wrapper.find('.ep-evidence-card').text()).toContain('培养方案.pdf')
    expect(wrapper.text()).toContain('结果由确定性规则引擎计算；AI 仅负责解释')
  })

  it('leaves no result behind when the first calculation fails', async () => {
    mocks.fetchAcademicOptions.mockResolvedValue(options(['rs-1'], ['ru-1']))
    mocks.calculateAcademicPlan.mockRejectedValue(
      new ApiError('规则集合不可用', {
        kind: 'http',
        status: 409,
        code: 'ACADEMIC_RULE_SET_UNAVAILABLE',
        requestId: 'req-plan-1',
      }),
    )

    const { wrapper } = await mountView()
    await buttonByText(wrapper, '开始计算')!.trigger('click')
    await flushPromises()

    expect(wrapper.find('.ep-credit-summary').exists()).toBe(false)
    const alert = wrapper.find('.ep-planning__calc-error')
    expect(alert.text()).toContain('ACADEMIC_RULE_SET_UNAVAILABLE')
    expect(alert.text()).toContain('请求编号：req-plan-1')
    expect(buttonByText(wrapper, '重新计算')).toBeTruthy()
  })

  it('keeps the previous result and marks it not updated when a recalculation fails', async () => {
    mocks.fetchAcademicOptions.mockResolvedValue(options(['rs-1'], ['ru-1']))
    mocks.calculateAcademicPlan.mockResolvedValueOnce(plan({ completed_credits: 100.0 }))
    mocks.calculateAcademicPlan.mockRejectedValueOnce(
      new ApiError('规则集合不可用', { kind: 'http', status: 409, code: 'ACADEMIC_RULE_SET_UNAVAILABLE' }),
    )

    const { wrapper } = await mountView()
    await buttonByText(wrapper, '开始计算')!.trigger('click')
    await flushPromises()
    await buttonByText(wrapper, '开始计算')!.trigger('click')
    await flushPromises()

    expect(wrapper.find('.ep-credit-summary').exists()).toBe(true)
    expect(wrapper.find('.ep-planning__stale-result').text()).toContain('本次计算失败，当前结果未更新')
  })
})

describe('PlanningView result context', () => {
  it('describes the result by the ids it actually belongs to, not the current selection', async () => {
    mocks.fetchAcademicOptions.mockResolvedValue(options(['rs-1', 'rs-2'], ['ru-1', 'ru-2']))
    mocks.calculateAcademicPlan.mockResolvedValue(plan())

    const { wrapper } = await mountView()
    await selectBoth(wrapper, 'rs-1', 'ru-1')
    await buttonByText(wrapper, '开始计算')!.trigger('click')
    await flushPromises()

    await wrapper.find('.ep-selection__record').setValue('rs-2')
    await flushPromises()

    const context = wrapper.find('.ep-planning__result-context')
    expect(context.text()).toContain('课程记录 rs-1')
    expect(context.text()).toContain('培养方案 ru-1')
    expect(wrapper.find('.ep-planning__stale-result').exists()).toBe(true)
  })

  it('flags a result whose source options disappeared instead of showing an internal id', async () => {
    mocks.fetchAcademicOptions.mockResolvedValueOnce(options(['rs-1'], ['ru-1']))
    mocks.calculateAcademicPlan.mockResolvedValue(plan())

    const { wrapper } = await mountView()
    await buttonByText(wrapper, '开始计算')!.trigger('click')
    await flushPromises()
    expect(wrapper.find('.ep-planning__result-context').text()).toContain('课程记录 rs-1')

    const store = useAcademicStore()
    mocks.fetchAcademicOptions.mockResolvedValue(options(['rs-9'], ['ru-9']))
    await store.loadOptions()
    await flushPromises()

    const context = wrapper.find('.ep-planning__result-context')
    expect(context.text()).not.toContain('rs-1')
    expect(wrapper.find('.ep-planning__result-invalid').text()).toContain('上一份结果的数据来源已失效')
    expect(wrapper.find('.ep-planning__stale-result').exists()).toBe(true)
  })
})

describe('PlanningView evidence and lifecycle', () => {
  it('loads the source only when the user opens 查看原文', async () => {
    const chunkId = 'c'.repeat(64)
    mocks.fetchAcademicOptions.mockResolvedValue(options(['rs-1'], ['ru-1']))
    mocks.calculateAcademicPlan.mockResolvedValue(
      plan({
        evidence: [
          {
            chunk_id: chunkId,
            doc_id: 'doc-1',
            file_name: '培养方案.pdf',
            document_version: null,
            effective_from: null,
            page_number: 4,
            sheet_name: null,
            row_start: null,
            row_end: null,
            section_title: null,
            quote: 'quote',
          },
        ],
      }),
    )
    mocks.fetchSource.mockResolvedValue({
      chunk_id: chunkId,
      doc_id: 'doc-1',
      file_name: '培养方案.pdf',
      file_type: 'pdf',
      document_version: '2026.1',
      effective_from: '2026-09-01',
      dataset_version: '2026.1',
      text: '原文内容',
      page_number: 4,
      sheet_name: null,
      row_start: null,
      row_end: null,
      section_title: null,
    })

    const { wrapper } = await mountView()
    await buttonByText(wrapper, '开始计算')!.trigger('click')
    await flushPromises()

    expect(mocks.fetchSource).not.toHaveBeenCalled()

    await wrapper.find('.ep-evidence-card__open').trigger('click')
    await flushPromises()

    expect(mocks.fetchSource).toHaveBeenCalledWith(chunkId, expect.anything())
  })

  it('moves the evidence into a drawer on narrower viewports', async () => {
    stubMatchMedia(false)
    mocks.fetchAcademicOptions.mockResolvedValue(options(['rs-1'], ['ru-1']))
    mocks.calculateAcademicPlan.mockResolvedValue(
      plan({
        evidence: [
          {
            chunk_id: 'c'.repeat(64),
            doc_id: 'doc-1',
            file_name: '培养方案.pdf',
            document_version: null,
            effective_from: null,
            page_number: 4,
            sheet_name: null,
            row_start: null,
            row_end: null,
            section_title: null,
            quote: 'quote',
          },
        ],
      }),
    )

    const { wrapper } = await mountView()
    await buttonByText(wrapper, '开始计算')!.trigger('click')
    await flushPromises()

    expect(wrapper.find('.ep-planning__aside').exists()).toBe(false)

    const openEvidence = buttonByText(wrapper, '查看计算证据')
    expect(openEvidence).toBeTruthy()

    await openEvidence!.trigger('click')
    await flushPromises()

    expect(wrapper.find('.ep-evidence-card').exists()).toBe(true)
  })

  it('cancels in-flight work when the page unmounts', async () => {
    mocks.fetchAcademicOptions.mockResolvedValue(options(['rs-1'], ['ru-1']))
    const { wrapper } = await mountView()
    const store = useAcademicStore()
    const cancelSpy = vi.spyOn(store, 'cancel')

    wrapper.unmount()

    expect(cancelSpy).toHaveBeenCalled()
  })
})
