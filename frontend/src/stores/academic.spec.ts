import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { ApiError } from '@/api/client'
import type { AcademicOptions, ImportResult, PlanningResult } from '@/api/academic'

const mocks = vi.hoisted(() => ({
  fetchAcademicOptions: vi.fn(),
  importAcademicRecords: vi.fn(),
  importAcademicRules: vi.fn(),
  calculateAcademicPlan: vi.fn(),
}))

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

import { useAcademicStore } from './academic'

function optionsPayload(recordIds: string[], ruleIds: string[]): AcademicOptions {
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

function planPayload(overrides: Partial<PlanningResult> = {}): PlanningResult {
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

function importPayload(id: string): ImportResult {
  return { id, status: 'ready', warnings: [] }
}

function mountStore() {
  setActivePinia(createPinia())
  return useAcademicStore()
}

async function settleUntil(predicate: () => boolean): Promise<void> {
  for (let attempt = 0; attempt < 25 && !predicate(); attempt += 1) {
    await Promise.resolve()
  }
}

beforeEach(() => {
  for (const mock of Object.values(mocks)) {
    mock.mockReset()
  }
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('academic store options', () => {
  it('loads options and preselects a lone ready set', async () => {
    mocks.fetchAcademicOptions.mockResolvedValue(optionsPayload(['rs-1'], ['ru-1']))
    const store = mountStore()

    const pending = store.loadOptions()
    expect(store.optionsLoading).toBe(true)
    await pending

    expect(store.optionsLoading).toBe(false)
    expect(store.optionsError).toBeNull()
    expect(store.options?.record_sets).toHaveLength(1)
    expect(store.selectedRecordSetId).toBe('rs-1')
    expect(store.selectedRuleSetId).toBe('ru-1')
  })

  it('treats empty options as a valid empty state rather than an error', async () => {
    mocks.fetchAcademicOptions.mockResolvedValue(optionsPayload([], []))
    const store = mountStore()

    await store.loadOptions()

    expect(store.optionsError).toBeNull()
    expect(store.options?.record_sets).toEqual([])
    expect(store.selectedRecordSetId).toBeNull()
    expect(store.selectedRuleSetId).toBeNull()
  })

  it('keeps the real server error when options fail to load', async () => {
    mocks.fetchAcademicOptions.mockRejectedValue(
      new ApiError('无法连接后端服务（网络错误或跨域被阻断）', { kind: 'network' }),
    )
    const store = mountStore()

    await store.loadOptions()

    expect(store.optionsError?.kind).toBe('network')
    expect(store.optionsLoading).toBe(false)
    expect(store.options).toBeNull()
  })

  it('never lets a stale options response overwrite a newer one', async () => {
    const resolvers: Array<(value: AcademicOptions) => void> = []
    mocks.fetchAcademicOptions.mockImplementation(
      (signal?: AbortSignal) =>
        new Promise<AcademicOptions>((resolve, reject) => {
          resolvers.push(resolve)
          signal?.addEventListener('abort', () => {
            const error = new Error('aborted')
            error.name = 'AbortError'
            reject(error)
          })
        }),
    )
    const store = mountStore()

    const first = store.loadOptions()
    const second = store.loadOptions()
    expect(mocks.fetchAcademicOptions).toHaveBeenCalledTimes(2)
    expect(mocks.fetchAcademicOptions.mock.calls[0][0]?.aborted).toBe(true)

    resolvers[1](optionsPayload(['rs-new'], ['ru-new']))
    await second
    expect(store.selectedRecordSetId).toBe('rs-new')

    resolvers[0](optionsPayload(['rs-old'], ['ru-old']))
    await first

    expect(store.options?.record_sets[0].id).toBe('rs-new')
    expect(store.selectedRecordSetId).toBe('rs-new')
  })

  it('clears a selection that disappeared and reports it as stale', async () => {
    mocks.fetchAcademicOptions.mockResolvedValueOnce(optionsPayload(['rs-1'], ['ru-1']))
    const store = mountStore()
    await store.loadOptions()
    expect(store.selectedRecordSetId).toBe('rs-1')

    mocks.fetchAcademicOptions.mockResolvedValueOnce(optionsPayload(['rs-2'], ['ru-1']))
    await store.loadOptions()

    expect(store.selectedRecordSetId).toBeNull()
    expect(store.staleRecordSelection).toBe(true)
    expect(store.selectedRuleSetId).toBe('ru-1')
    expect(store.staleRuleSelection).toBe(false)
  })

  it('never auto-selects among several options', async () => {
    mocks.fetchAcademicOptions.mockResolvedValue(optionsPayload(['rs-1', 'rs-2'], ['ru-1', 'ru-2']))
    const store = mountStore()

    await store.loadOptions()

    expect(store.selectedRecordSetId).toBeNull()
    expect(store.selectedRuleSetId).toBeNull()
  })

  it('lets the user select explicitly, then aborts on cancel without unhandled errors', async () => {
    mocks.fetchAcademicOptions.mockResolvedValue(optionsPayload(['rs-1', 'rs-2'], ['ru-1']))
    const store = mountStore()
    await store.loadOptions()

    store.selectRecordSet('rs-2')
    expect(store.selectedRecordSetId).toBe('rs-2')

    mocks.fetchAcademicOptions.mockImplementation(
      (signal?: AbortSignal) =>
        new Promise<AcademicOptions>((_resolve, reject) => {
          signal?.addEventListener('abort', () => {
            const error = new Error('aborted')
            error.name = 'AbortError'
            reject(error)
          })
        }),
    )
    const pending = store.loadOptions()
    await settleUntil(() => store.optionsLoading)

    expect(() => store.cancel()).not.toThrow()
    expect(store.optionsLoading).toBe(false)
    expect(store.selectedRecordSetId).toBe('rs-2')

    await pending
    expect(store.optionsError).toBeNull()
  })
})

describe('academic store imports', () => {
  it('imports course records, then refreshes options without auto-selecting', async () => {
    mocks.fetchAcademicOptions.mockResolvedValue(optionsPayload(['rs-1', 'rs-2'], ['ru-1']))
    const store = mountStore()
    await store.loadOptions()

    mocks.importAcademicRecords.mockResolvedValue(importPayload('rs-2'))
    const file = new File(['xlsx'], '记录.xlsx')

    const result = await store.importRecords(file, ' 2026 秋季 ')

    expect(result).toEqual(importPayload('rs-2'))
    expect(mocks.importAcademicRecords).toHaveBeenCalledWith(file, ' 2026 秋季 ')
    expect(mocks.fetchAcademicOptions).toHaveBeenCalledTimes(2)
    expect(store.importingRecords).toBe(false)
    expect(store.importRecordsError).toBeNull()
    expect(store.selectedRecordSetId).toBeNull()
  })

  it('keeps the real import error and never touches the records pipeline of rules', async () => {
    mocks.fetchAcademicOptions.mockResolvedValue(optionsPayload([], []))
    const store = mountStore()
    await store.loadOptions()

    mocks.importAcademicRecords.mockRejectedValue(
      new ApiError('仅支持 XLSX 课程记录', {
        kind: 'http',
        status: 400,
        code: 'ACADEMIC_FILE_TYPE_INVALID',
        requestId: 'req-import-1',
      }),
    )

    const result = await store.importRecords(new File(['x'], '记录.xlsx'))

    expect(result).toBeNull()
    expect(store.importRecordsError?.code).toBe('ACADEMIC_FILE_TYPE_INVALID')
    expect(store.importRecordsError?.requestId).toBe('req-import-1')
    expect(store.importRulesError).toBeNull()
  })

  it('blocks a second concurrent records import', async () => {
    mocks.fetchAcademicOptions.mockResolvedValue(optionsPayload([], []))
    const store = mountStore()
    await store.loadOptions()

    let release: ((value: ImportResult) => void) | null = null
    mocks.importAcademicRecords.mockImplementation(
      () => new Promise<ImportResult>((resolve) => (release = resolve)),
    )

    const first = store.importRecords(new File(['a'], 'a.xlsx'))
    await settleUntil(() => store.importingRecords)
    const second = await store.importRecords(new File(['b'], 'b.xlsx'))

    expect(second).toBeNull()
    expect(mocks.importAcademicRecords).toHaveBeenCalledTimes(1)

    release!(importPayload('rs-1'))
    await first
    expect(store.importingRecords).toBe(false)
  })

  it('imports degree rules through the dedicated endpoint', async () => {
    mocks.fetchAcademicOptions.mockResolvedValue(optionsPayload([], []))
    const store = mountStore()
    await store.loadOptions()

    mocks.importAcademicRules.mockResolvedValue(importPayload('ru-1'))
    const file = new File(['pdf'], '培养方案.pdf')

    const result = await store.importRules(file)

    expect(result).toEqual(importPayload('ru-1'))
    expect(mocks.importAcademicRules).toHaveBeenCalledWith(file, undefined)
    expect(mocks.importAcademicRecords).not.toHaveBeenCalled()
    expect(store.importingRules).toBe(false)
    expect(store.importRulesError).toBeNull()
  })

  it('blocks a second concurrent rules import', async () => {
    mocks.fetchAcademicOptions.mockResolvedValue(optionsPayload([], []))
    const store = mountStore()
    await store.loadOptions()

    let release: ((value: ImportResult) => void) | null = null
    mocks.importAcademicRules.mockImplementation(
      () => new Promise<ImportResult>((resolve) => (release = resolve)),
    )

    const first = store.importRules(new File(['a'], 'a.pdf'))
    await settleUntil(() => store.importingRules)
    const second = await store.importRules(new File(['b'], 'b.pdf'))

    expect(second).toBeNull()
    expect(mocks.importAcademicRules).toHaveBeenCalledTimes(1)

    release!(importPayload('ru-1'))
    await first
    expect(store.importingRules).toBe(false)
  })
})

describe('academic store calculation', () => {
  it('does not call the plan endpoint when a selection is missing', async () => {
    mocks.fetchAcademicOptions.mockResolvedValue(optionsPayload(['rs-1'], ['ru-1', 'ru-2']))
    const store = mountStore()
    await store.loadOptions()

    await store.calculate()

    expect(mocks.calculateAcademicPlan).not.toHaveBeenCalled()
    expect(store.calculating).toBe(false)
    expect(store.result).toBeNull()
  })

  it('sends exactly the two selected ids and stores the result untouched', async () => {
    mocks.fetchAcademicOptions.mockResolvedValue(optionsPayload(['rs-1'], ['ru-1']))
    const store = mountStore()
    await store.loadOptions()

    const payload = planPayload()
    mocks.calculateAcademicPlan.mockResolvedValue(payload)

    await store.calculate()

    expect(mocks.calculateAcademicPlan).toHaveBeenCalledTimes(1)
    const [request] = mocks.calculateAcademicPlan.mock.calls[0]
    expect(Object.keys(request as object).sort()).toEqual(['record_set_id', 'rule_set_id'])
    expect(request).toEqual({ record_set_id: 'rs-1', rule_set_id: 'ru-1' })
    expect(store.result).toEqual(payload)
    expect(store.resultNotUpdated).toBe(false)
    expect(store.calculationError).toBeNull()
  })

  it('leaves no result behind when the very first calculation fails', async () => {
    mocks.fetchAcademicOptions.mockResolvedValue(optionsPayload(['rs-1'], ['ru-1']))
    const store = mountStore()
    await store.loadOptions()

    mocks.calculateAcademicPlan.mockRejectedValue(
      new ApiError('规则集合不可用', {
        kind: 'http',
        status: 409,
        code: 'ACADEMIC_RULE_SET_UNAVAILABLE',
        requestId: 'req-plan-1',
      }),
    )

    await store.calculate()

    expect(store.result).toBeNull()
    expect(store.calculationError?.code).toBe('ACADEMIC_RULE_SET_UNAVAILABLE')
    expect(store.calculationError?.requestId).toBe('req-plan-1')
    expect(store.resultNotUpdated).toBe(false)
    expect(store.calculating).toBe(false)
  })

  it('keeps the previous result and marks it as not updated when recalculation fails', async () => {
    mocks.fetchAcademicOptions.mockResolvedValue(optionsPayload(['rs-1'], ['ru-1']))
    const store = mountStore()
    await store.loadOptions()

    const first = planPayload({ completed_credits: 100.0 })
    mocks.calculateAcademicPlan.mockResolvedValueOnce(first)
    await store.calculate()

    mocks.calculateAcademicPlan.mockRejectedValueOnce(
      new ApiError('规则集合不可用', {
        kind: 'http',
        status: 409,
        code: 'ACADEMIC_RULE_SET_UNAVAILABLE',
        requestId: 'req-plan-2',
      }),
    )
    await store.calculate()

    expect(store.result).toEqual(first)
    expect(store.resultNotUpdated).toBe(true)
    expect(store.calculationError?.requestId).toBe('req-plan-2')

    const second = planPayload({ completed_credits: 120.0 })
    mocks.calculateAcademicPlan.mockResolvedValueOnce(second)
    await store.calculate()

    expect(store.result).toEqual(second)
    expect(store.resultNotUpdated).toBe(false)
    expect(store.calculationError).toBeNull()
  })

  it('runs only one calculation at a time and never retries automatically', async () => {
    mocks.fetchAcademicOptions.mockResolvedValue(optionsPayload(['rs-1'], ['ru-1']))
    const store = mountStore()
    await store.loadOptions()

    let release: ((value: PlanningResult) => void) | null = null
    mocks.calculateAcademicPlan.mockImplementation(
      () => new Promise<PlanningResult>((resolve) => (release = resolve)),
    )

    const first = store.calculate()
    await settleUntil(() => store.calculating)
    await store.calculate()

    expect(mocks.calculateAcademicPlan).toHaveBeenCalledTimes(1)

    release!(planPayload())
    await first
    expect(mocks.calculateAcademicPlan).toHaveBeenCalledTimes(1)
  })

  it('cancels an in-flight calculation without reporting an error', async () => {
    mocks.fetchAcademicOptions.mockResolvedValue(optionsPayload(['rs-1'], ['ru-1']))
    const store = mountStore()
    await store.loadOptions()

    mocks.calculateAcademicPlan.mockImplementation(
      (_request: unknown, signal?: AbortSignal) =>
        new Promise<PlanningResult>((_resolve, reject) => {
          signal?.addEventListener('abort', () => {
            const error = new Error('aborted')
            error.name = 'AbortError'
            reject(error)
          })
        }),
    )

    const pending = store.calculate()
    await settleUntil(() => store.calculating)

    store.cancel()
    await pending

    expect(store.calculating).toBe(false)
    expect(store.calculationError).toBeNull()
    expect(store.resultNotUpdated).toBe(false)
    expect(store.result).toBeNull()
  })
})
