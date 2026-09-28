import { describe, expect, it } from 'vitest'

import type { AcademicOptions, PlanningResult } from '@/api/academic'

import {
  buildProgressBreakdown,
  resolveAcademicSelection,
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
