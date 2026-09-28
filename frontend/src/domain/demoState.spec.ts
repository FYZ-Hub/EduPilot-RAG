import { describe, expect, it } from 'vitest'

import type { DatasetState } from '@/api/demo'
import {
  describeDatasetState,
  jobDocumentStatusLabel,
  jobResultLabel,
  jobStatusLabel,
  stageLabel,
} from './demoState'

const ALL_STATES: DatasetState[] = [
  'disabled',
  'unavailable',
  'empty',
  'queued',
  'running',
  'partial',
  'loaded',
  'failed',
]

describe('dataset state descriptors', () => {
  it('covers all eight dataset states exactly once', () => {
    expect(ALL_STATES).toHaveLength(8)
    for (const state of ALL_STATES) {
      expect(describeDatasetState(state)).toBeTruthy()
    }
  })

  it('only lets the empty state offer the primary load action', () => {
    const primaries = ALL_STATES.filter((state) => describeDatasetState(state).primary)
    expect(primaries).toEqual(['empty'])
  })

  it('maps every state to the exact button label and disabled flag from UI_SPEC 5.3', () => {
    const mapping: Record<DatasetState, { button: string | null; disabled: boolean }> = {
      disabled: { button: '演示资料不可用', disabled: true },
      unavailable: { button: '演示资料不可用', disabled: true },
      empty: { button: '加载演示资料', disabled: false },
      queued: { button: '正在加载', disabled: true },
      running: { button: '正在加载', disabled: true },
      partial: { button: '继续加载', disabled: false },
      loaded: { button: '重新校验', disabled: false },
      failed: { button: '重试加载', disabled: false },
    }
    for (const state of ALL_STATES) {
      const descriptor = describeDatasetState(state)
      expect(descriptor.buttonLabel).toBe(mapping[state].button)
      expect(descriptor.disabled).toBe(mapping[state].disabled)
    }
  })

  it('only seeds through the single POST /demo/seed path for actionable states', () => {
    const seeding = ALL_STATES.filter((state) => describeDatasetState(state).seeds)
    // loaded 的“重新校验”仍调用 seed，disabled/unavailable/queued/running 不调用
    expect(seeding.sort()).toEqual(['empty', 'failed', 'loaded', 'partial'].sort())
  })

  it('shows the reason only where a configuration message is meaningful', () => {
    expect(describeDatasetState('disabled').showsReason).toBe(true)
    expect(describeDatasetState('unavailable').showsReason).toBe(true)
    expect(describeDatasetState('failed').showsReason).toBe(true)
    expect(describeDatasetState('empty').showsReason).toBe(false)
  })

  it('uses the danger tone only for unavailable and failed', () => {
    const dangers = ALL_STATES.filter((state) => describeDatasetState(state).tone === 'danger')
    expect(dangers.sort()).toEqual(['failed', 'unavailable'].sort())
  })
})

describe('job and job document labels', () => {
  it('labels job statuses independently of dataset states', () => {
    expect(jobStatusLabel('running')).toBe('处理中')
    expect(jobStatusLabel('completed')).toBe('已完成')
    expect(jobStatusLabel('completed_with_errors')).toBe('完成但有失败项')
    expect(jobStatusLabel('failed')).toBe('失败')
    // Dataset 没有“已完成”这一状态名，两套枚举不互相替代
    expect(ALL_STATES).not.toContain('completed')
  })

  it('labels job document status and result', () => {
    expect(jobDocumentStatusLabel('pending')).toBe('等待处理')
    expect(jobDocumentStatusLabel('skipped')).toBe('已跳过')
    expect(jobResultLabel('imported')).toBe('已导入')
    expect(jobResultLabel('resumed')).toBe('已续跑')
    expect(jobResultLabel(null)).toBe('待处理')
  })

  it('maps pipeline stages to readable labels', () => {
    expect(stageLabel('parsing')).toBe('解析')
    expect(stageLabel('keyword_indexing')).toBe('关键词索引')
    expect(stageLabel(null)).toBe('—')
    expect(stageLabel('custom_stage')).toBe('custom_stage')
  })
})
