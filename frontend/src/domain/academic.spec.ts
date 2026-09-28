import { describe, expect, it } from 'vitest'

import type { AcademicOptions, CategoryGap, PlanningEvidence, PlanningResult } from '@/api/academic'

import {
  buildCategoryGapBreakdown,
  buildProgressBreakdown,
  conflictGroup,
  conflictGroupLabel,
  describeSeverity,
  formatCredit,
  linkEvidence,
  readSelectionQuery,
  resolveAcademicSelection,
  resolvePlanResultContext,
  resolveSelectionInput,
  summariseCredits,
} from './academic'

function recordSet(id: string, status = 'ready') {
  return {
    id,
    name: `课程记录 ${id}`,
    source_doc_id: `doc-${id}`,
    status,
    updated_at: '2026-09-01T00:00:00Z',
  }
}

function ruleSet(id: string, status = 'ready') {
  return {
    id,
    name: `培养方案 ${id}`,
    major: '计算机科学与技术',
    admission_year: 2026,
    rule_version: `v-${id}`,
    effective_from: '2026-09-01',
    source_doc_id: `doc-${id}`,
    status,
  }
}

function options(recordSets: string[], ruleSets: string[]): AcademicOptions {
  return {
    record_sets: recordSets.map((id) => recordSet(id)),
    rule_sets: ruleSets.map((id) => ruleSet(id)),
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

describe('academic selection recovery', () => {
  it('keeps a query id that exists in the matching options', () => {
    const resolution = resolveAcademicSelection(options(['rs-1'], ['ru-1']), {
      record_set_id: 'rs-1',
      rule_set_id: 'ru-1',
    })

    expect(resolution.recordSetId).toBe('rs-1')
    expect(resolution.ruleSetId).toBe('ru-1')
    expect(resolution.staleRecordSelection).toBe(false)
    expect(resolution.staleRuleSelection).toBe(false)
  })

  it('clears an invalid query id and reports it instead of picking a replacement', () => {
    const resolution = resolveAcademicSelection(options(['rs-2'], ['ru-2']), {
      record_set_id: 'rs-1',
      rule_set_id: 'ru-1',
    })

    expect(resolution.recordSetId).toBeNull()
    expect(resolution.ruleSetId).toBeNull()
    expect(resolution.staleRecordSelection).toBe(true)
    expect(resolution.staleRuleSelection).toBe(true)
  })

  it('preselects a lone ready option when no query is present', () => {
    const resolution = resolveAcademicSelection(options(['rs-1'], ['ru-1']), {})

    expect(resolution.recordSetId).toBe('rs-1')
    expect(resolution.ruleSetId).toBe('ru-1')
    expect(resolution.staleRecordSelection).toBe(false)
  })

  it('never silently picks one of several options', () => {
    const resolution = resolveAcademicSelection(options(['rs-1', 'rs-2'], ['ru-1', 'ru-2']), {})

    expect(resolution.recordSetId).toBeNull()
    expect(resolution.ruleSetId).toBeNull()
  })

  it('does not preselect when the only option is not ready', () => {
    const payload: AcademicOptions = {
      record_sets: [recordSet('rs-1', 'processing')],
      rule_sets: [ruleSet('ru-1', 'processing')],
    }

    const resolution = resolveAcademicSelection(payload, {})

    expect(resolution.recordSetId).toBeNull()
    expect(resolution.ruleSetId).toBeNull()
  })

  it('resolves the record and rule selection independently', () => {
    const resolution = resolveAcademicSelection(options(['rs-1', 'rs-2'], ['ru-9']), {
      record_set_id: 'rs-2',
      rule_set_id: 'ru-1',
    })

    expect(resolution.recordSetId).toBe('rs-2')
    expect(resolution.ruleSetId).toBeNull()
    expect(resolution.staleRecordSelection).toBe(false)
    expect(resolution.staleRuleSelection).toBe(true)
  })

  it('treats a missing query value as absent', () => {
    const resolution = resolveAcademicSelection(options(['rs-1'], ['ru-1', 'ru-2']), {
      record_set_id: null,
      rule_set_id: undefined,
    })

    expect(resolution.recordSetId).toBe('rs-1')
    expect(resolution.ruleSetId).toBeNull()
  })

  it('does not mutate the options it receives and is repeatable', () => {
    const payload = options(['rs-1', 'rs-2'], ['ru-1'])
    const snapshot = JSON.parse(JSON.stringify(payload))

    const first = resolveAcademicSelection(payload, { record_set_id: 'missing' })
    const second = resolveAcademicSelection(payload, { record_set_id: 'missing' })

    expect(payload).toEqual(snapshot)
    expect(first).toEqual(second)
  })

  it('handles empty options without inventing a selection', () => {
    const resolution = resolveAcademicSelection({ record_sets: [], rule_sets: [] }, {
      record_set_id: 'rs-1',
    })

    expect(resolution.recordSetId).toBeNull()
    expect(resolution.ruleSetId).toBeNull()
    expect(resolution.staleRecordSelection).toBe(true)
  })
})

describe('academic credit summary', () => {
  it('accepts a consistent result and reports no overflow', () => {
    const summary = summariseCredits(plan())

    expect(summary.valid).toBe(true)
    expect(summary.message).toBeNull()
    expect(summary.exceeded).toBe(false)
  })

  it('flags completed plus in-progress exceeding the requirement without calling it invalid', () => {
    const summary = summariseCredits(
      plan({ required_credits: 100.0, completed_credits: 80.0, in_progress_credits: 40.0 }),
    )

    expect(summary.valid).toBe(true)
    expect(summary.exceeded).toBe(true)
  })

  it('accepts a zero requirement with no progress', () => {
    const summary = summariseCredits(
      plan({ required_credits: 0, completed_credits: 0, in_progress_credits: 0, remaining_credits: 0 }),
    )

    expect(summary.valid).toBe(true)
    expect(summary.exceeded).toBe(false)
  })

  it('rejects a zero requirement that still carries progress', () => {
    const summary = summariseCredits(
      plan({ required_credits: 0, completed_credits: 12.0, in_progress_credits: 0, remaining_credits: 0 }),
    )

    expect(summary.valid).toBe(false)
    expect(summary.message).not.toBeNull()
  })

  it('rejects negative, NaN and infinite credits without rewriting them', () => {
    expect(summariseCredits(plan({ completed_credits: -1 })).valid).toBe(false)
    expect(summariseCredits(plan({ in_progress_credits: Number.NaN })).valid).toBe(false)
    expect(summariseCredits(plan({ required_credits: Number.POSITIVE_INFINITY })).valid).toBe(false)
    expect(summariseCredits(plan({ remaining_credits: Number.NEGATIVE_INFINITY })).valid).toBe(false)
  })

  it('does not mutate the planning result it validates', () => {
    const result = plan({ required_credits: 100.0, completed_credits: 80.0, in_progress_credits: 40.0 })
    const snapshot = JSON.parse(JSON.stringify(result))

    summariseCredits(result)

    expect(result).toEqual(snapshot)
  })
})

describe('academic progress breakdown', () => {
  it('splits the bar into completed, in-progress and remaining parts', () => {
    const breakdown = buildProgressBreakdown(plan())

    expect(breakdown.exceeded).toBe(false)
    expect(breakdown.segments.map((segment) => segment.key)).toEqual([
      'completed',
      'in_progress',
      'remaining',
    ])
    expect(breakdown.segments[0].percent).toBeCloseTo((100 / 155) * 100, 5)
    expect(breakdown.segments[1].percent).toBeCloseTo((20 / 155) * 100, 5)
    expect(breakdown.segments[2].percent).toBeCloseTo((35 / 155) * 100, 5)
  })

  it('caps the completed and in-progress parts at 100% when credits overflow', () => {
    const breakdown = buildProgressBreakdown(
      plan({ required_credits: 100.0, completed_credits: 80.0, in_progress_credits: 40.0 }),
    )

    expect(breakdown.exceeded).toBe(true)
    expect(breakdown.segments[0].percent).toBeCloseTo(80, 5)
    expect(breakdown.segments[1].percent).toBeCloseTo(20, 5)
    expect(breakdown.segments[2].percent).toBe(0)
    expect(breakdown.totalPercent).toBeLessThanOrEqual(100)
    expect(breakdown.totalPercent).toBeCloseTo(100, 5)
  })

  it('caps the completed part alone when it already exceeds the requirement', () => {
    const breakdown = buildProgressBreakdown(
      plan({ required_credits: 100.0, completed_credits: 130.0, in_progress_credits: 10.0 }),
    )

    expect(breakdown.segments[0].percent).toBeCloseTo(100, 5)
    expect(breakdown.segments[1].percent).toBe(0)
    expect(breakdown.segments[2].percent).toBe(0)
    expect(breakdown.totalPercent).toBeLessThanOrEqual(100)
  })

  it('returns an all-zero bar when nothing is required', () => {
    const breakdown = buildProgressBreakdown(
      plan({ required_credits: 0, completed_credits: 0, in_progress_credits: 0, remaining_credits: 0 }),
    )

    expect(breakdown.totalPercent).toBe(0)
    expect(breakdown.segments.every((segment) => segment.percent === 0)).toBe(true)
  })

  it('fills the remaining part up to the requirement', () => {
    const breakdown = buildProgressBreakdown(
      plan({ required_credits: 120.0, completed_credits: 30.0, in_progress_credits: 30.0, remaining_credits: 60.0 }),
    )

    const total = breakdown.segments.reduce((sum, segment) => sum + segment.percent, 0)
    expect(total).toBeCloseTo(100, 5)
    expect(breakdown.segments[2].percent).toBeCloseTo(50, 5)
  })

  it('never rewrites the planning result while computing display ratios', () => {
    const result = plan()
    const snapshot = JSON.parse(JSON.stringify(result))

    buildProgressBreakdown(result)

    expect(result).toEqual(snapshot)
    expect(result.required_credits).toBe(155.0)
  })
})

describe('academic selection query normalisation', () => {
  it('reads present string ids as the query values', () => {
    const input = readSelectionQuery({ record_set_id: 'rs-1', rule_set_id: ' ru-1 ' })

    expect(input.record).toEqual({ value: 'rs-1', malformed: false })
    expect(input.rule).toEqual({ value: 'ru-1', malformed: false })
  })

  it('treats absent values as not provided rather than invalid', () => {
    const input = readSelectionQuery({})

    expect(input.record).toEqual({ value: null, malformed: false })
    expect(input.rule).toEqual({ value: null, malformed: false })
  })

  it('treats empty strings, arrays and non-strings as malformed', () => {
    expect(readSelectionQuery({ record_set_id: '' }).record.malformed).toBe(true)
    expect(readSelectionQuery({ record_set_id: ['rs-1'] }).record.malformed).toBe(true)
    expect(readSelectionQuery({ rule_set_id: ['ru-1', 'ru-2'] }).rule.malformed).toBe(true)
    expect(readSelectionQuery({ rule_set_id: 42 }).rule.malformed).toBe(true)
  })

  it('resolves a valid query against the options', () => {
    const resolution = resolveSelectionInput(options(['rs-1'], ['ru-1']), {
      record: { value: 'rs-1', malformed: false },
      rule: { value: 'ru-1', malformed: false },
    })

    expect(resolution.recordSetId).toBe('rs-1')
    expect(resolution.ruleSetId).toBe('ru-1')
    expect(resolution.staleRecordSelection).toBe(false)
    expect(resolution.staleRuleSelection).toBe(false)
  })

  it('reports a malformed field as stale even when a replacement option exists', () => {
    const resolution = resolveSelectionInput(options(['rs-9'], ['ru-9']), {
      record: { value: null, malformed: true },
      rule: { value: null, malformed: false },
    })

    expect(resolution.recordSetId).toBe('rs-9')
    expect(resolution.staleRecordSelection).toBe(true)
    expect(resolution.staleRuleSelection).toBe(false)
  })

  it('does not mutate the options it reads', () => {
    const payload = options(['rs-1'], ['ru-1'])
    const snapshot = JSON.parse(JSON.stringify(payload))

    resolveSelectionInput(payload, { record: { value: 'rs-9', malformed: false }, rule: { value: null, malformed: false } })

    expect(payload).toEqual(snapshot)
  })
})

describe('academic credit formatting', () => {
  it('always shows one decimal and never hides zero', () => {
    expect(formatCredit(0)).toBe('0.0')
    expect(formatCredit(155)).toBe('155.0')
    expect(formatCredit(12.34)).toBe('12.3')
    expect(formatCredit(100.5)).toBe('100.5')
  })

  it('keeps invalid numbers explicit instead of correcting them', () => {
    expect(formatCredit(Number.NaN)).toBe('NaN')
    expect(formatCredit(Number.POSITIVE_INFINITY)).toBe('Infinity')
    expect(formatCredit(-1)).toBe('-1.0')
  })
})

describe('category gap breakdown', () => {
  function gap(overrides: Partial<CategoryGap> = {}): CategoryGap {
    return {
      category: '专业必修',
      required_credits: 60.0,
      completed_credits: 40.0,
      in_progress_credits: 12.0,
      remaining_credits: 8.0,
      ...overrides,
    }
  }

  it('builds a three-part bar without recomputing the server remaining credits', () => {
    const breakdown = buildCategoryGapBreakdown(gap())

    expect(breakdown.valid).toBe(true)
    expect(breakdown.message).toBeNull()
    expect(breakdown.segments.map((segment) => segment.key)).toEqual([
      'completed',
      'in_progress',
      'remaining',
    ])
    expect(breakdown.segments[0].percent).toBeCloseTo((40 / 60) * 100, 5)
    expect(breakdown.segments[1].percent).toBeCloseTo((12 / 60) * 100, 5)
    expect(breakdown.segments[2].percent).toBeCloseTo(100 - (40 / 60) * 100 - (12 / 60) * 100, 5)
  })

  it('caps an over-achieved category at 100% without calling it invalid', () => {
    const breakdown = buildCategoryGapBreakdown(
      gap({ required_credits: 30.0, completed_credits: 40.0, in_progress_credits: 5.0 }),
    )

    expect(breakdown.valid).toBe(true)
    expect(breakdown.exceeded).toBe(true)
    expect(breakdown.totalPercent).toBeLessThanOrEqual(100)
    expect(breakdown.totalPercent).toBeCloseTo(100, 5)
  })

  it('reports invalid gap data in place instead of correcting it', () => {
    const breakdown = buildCategoryGapBreakdown(gap({ completed_credits: Number.NaN }))

    expect(breakdown.valid).toBe(false)
    expect(breakdown.message).not.toBeNull()
    expect(breakdown.totalPercent).toBe(0)
  })
})

describe('conflict classification', () => {
  it('maps the known server codes to their categories', () => {
    expect(conflictGroup('DEGREE_PLAN_VERSION_CONFLICT')).toBe('version')
    expect(conflictGroup('COURSE_TIME_CONFLICT')).toBe('time')
    expect(conflictGroup('COURSE_RECORD_CONTRADICTION')).toBe('record')
  })

  it('keeps other known and unknown codes in the generic group', () => {
    expect(conflictGroup('COURSE_CATEGORY_MISMATCH')).toBe('other')
    expect(conflictGroup('SOMETHING_NEW')).toBe('other')
    expect(conflictGroupLabel('version')).toBe('规则版本冲突')
    expect(conflictGroupLabel('other')).toBe('其他提醒')
  })

  it('labels severity with text instead of relying on colour alone', () => {
    expect(describeSeverity('blocking')).toMatchObject({ label: '阻断', blocking: true })
    expect(describeSeverity('warning')).toMatchObject({ label: '提醒', blocking: false })
    expect(describeSeverity('critical')).toMatchObject({ label: 'critical', blocking: false })
  })
})

describe('plan result context', () => {
  it('resolves the record and rule the result actually belongs to', () => {
    const context = resolvePlanResultContext(options(['rs-1', 'rs-2'], ['ru-1', 'ru-2']), 'rs-2', 'ru-1')

    expect(context.resolvable).toBe(true)
    expect(context.recordSet?.id).toBe('rs-2')
    expect(context.ruleSet?.id).toBe('ru-1')
  })

  it('reports missing options instead of falling back to another selection', () => {
    const context = resolvePlanResultContext(options(['rs-2'], ['ru-2']), 'rs-1', 'ru-2')

    expect(context.resolvable).toBe(false)
    expect(context.recordSet).toBeNull()
    expect(context.ruleSet?.id).toBe('ru-2')
  })

  it('is not resolvable without options or without ids', () => {
    expect(resolvePlanResultContext(null, 'rs-1', 'ru-1').resolvable).toBe(false)
    expect(resolvePlanResultContext(options(['rs-1'], ['ru-1']), null, 'ru-1').resolvable).toBe(false)
  })
})

describe('evidence linking', () => {
  function evidence(chunkId: string): PlanningEvidence {
    return {
      chunk_id: chunkId,
      doc_id: `doc-${chunkId}`,
      file_name: `${chunkId}.pdf`,
      document_version: null,
      effective_from: null,
      page_number: 1,
      sheet_name: null,
      row_start: null,
      row_end: null,
      section_title: null,
      quote: 'quote',
    }
  }

  it('links only ids that exist in the real evidence', () => {
    const linked = linkEvidence([evidence('c1'), evidence('c2')], ['c1', 'missing'])

    expect(linked.map((item) => item.chunk_id)).toEqual(['c1'])
  })

  it('keeps a stable order and drops duplicates', () => {
    const linked = linkEvidence([evidence('c1'), evidence('c2')], ['c2', 'c1', 'c2'])

    expect(linked.map((item) => item.chunk_id)).toEqual(['c2', 'c1'])
  })

  it('returns nothing when no evidence matches', () => {
    expect(linkEvidence([], ['c1'])).toEqual([])
  })
})
