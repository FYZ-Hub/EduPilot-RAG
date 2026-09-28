import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'

import type { PlanningResult } from '@/api/academic'
import CreditSummary from './CreditSummary.vue'

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

function mountSummary(result: PlanningResult) {
  return mount(CreditSummary, { props: { result }, global: { plugins: [ElementPlus] } })
}

function valueOf(wrapper: ReturnType<typeof mountSummary>, key: string): string {
  return wrapper.find(`[data-credit="${key}"] .ep-credit-card__value`).text()
}

/** 读取某个进度段在 style 中声明的宽度百分比。 */
function widthOf(wrapper: ReturnType<typeof mountSummary>, modifier: string): number {
  const style = wrapper.find(`.ep-progress__seg--${modifier}`).attributes('style') ?? ''
  const matched = /width:\s*([\d.]+)%/.exec(style)
  return matched ? Number.parseFloat(matched[1]) : Number.NaN
}

function progressAria(wrapper: ReturnType<typeof mountSummary>, name: string): number {
  const raw = wrapper.find('[role="progressbar"]').attributes(name) ?? ''
  return Number.parseFloat(raw)
}

describe('CreditSummary', () => {
  it('binds the four cards strictly to the server result', () => {
    const wrapper = mountSummary(plan())

    expect(valueOf(wrapper, 'required')).toBe('155.0')
    expect(valueOf(wrapper, 'completed')).toBe('100.0')
    expect(valueOf(wrapper, 'in_progress')).toBe('20.0')
    expect(valueOf(wrapper, 'remaining')).toBe('35.0')
  })

  it('shows zero as 0.0 instead of hiding it', () => {
    const wrapper = mountSummary(
      plan({ completed_credits: 0, in_progress_credits: 0, remaining_credits: 155 }),
    )

    expect(valueOf(wrapper, 'completed')).toBe('0.0')
    expect(valueOf(wrapper, 'in_progress')).toBe('0.0')
  })

  it('splits a normal bar into completed, in-progress and remaining parts', () => {
    const wrapper = mountSummary(plan())

    expect(widthOf(wrapper, 'completed')).toBeCloseTo((100 / 155) * 100, 3)
    expect(wrapper.find('.ep-progress__seg--in_progress').exists()).toBe(true)
    expect(wrapper.find('.ep-progress__seg--remaining').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('已超过要求')
    expect(wrapper.text()).not.toContain('数据错误')
  })

  it('caps an over-achieved bar at 100% and says so without calling it a data error', () => {
    const wrapper = mountSummary(
      plan({ required_credits: 100.0, completed_credits: 80.0, in_progress_credits: 40.0 }),
    )

    expect(wrapper.text()).toContain('已超过要求')
    expect(wrapper.text()).not.toContain('数据错误')

    const total =
      widthOf(wrapper, 'completed') +
      widthOf(wrapper, 'in_progress') +
      widthOf(wrapper, 'remaining')
    expect(total).toBeLessThanOrEqual(100.001)
  })

  it('reports invalid credits as a data error without correcting the numbers', () => {
    const wrapper = mountSummary(plan({ required_credits: 0, completed_credits: 12 }))

    expect(wrapper.text()).toContain('数据错误')
    expect(valueOf(wrapper, 'required')).toBe('0.0')
    expect(valueOf(wrapper, 'completed')).toBe('12.0')
  })

  it('exposes the bar to assistive technology with aria values and a live region', () => {
    const wrapper = mountSummary(plan())

    const bar = wrapper.find('[role="progressbar"]')
    expect(bar.exists()).toBe(true)
    expect(bar.attributes('aria-valuemin')).toBe('0')
    expect(bar.attributes('aria-valuemax')).toBe('100')
    expect(progressAria(wrapper, 'aria-valuenow')).toBeGreaterThan(0)
    expect(bar.attributes('aria-live')).toBe('polite')
    expect(bar.attributes('aria-label')).toBeTruthy()
  })

  it('keeps a zero requirement bar empty instead of dividing by zero', () => {
    const wrapper = mountSummary(
      plan({ required_credits: 0, completed_credits: 0, in_progress_credits: 0, remaining_credits: 0 }),
    )

    const bar = wrapper.find('[role="progressbar"]')
    expect(bar.attributes('aria-valuenow')).toBe('0')
    expect(wrapper.text()).not.toContain('已超过要求')
  })
})
