import { describe, expect, it } from 'vitest'
import { mount, type DOMWrapper } from '@vue/test-utils'
import ElementPlus from 'element-plus'

import type { CategoryGap } from '@/api/academic'
import CategoryGapList from './CategoryGapList.vue'

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

function mountList(gaps: CategoryGap[]) {
  return mount(CategoryGapList, { props: { gaps }, global: { plugins: [ElementPlus] } })
}

function field(item: DOMWrapper<Element>, name: string): string {
  return item.find(`[data-field="${name}"] .ep-gap-item__value`).text()
}

describe('CategoryGapList', () => {
  it('renders every server field for each category', () => {
    const wrapper = mountList([gap()])
    const item = wrapper.find('.ep-gap-item')

    expect(wrapper.find('.ep-gap-item__category').text()).toBe('专业必修')
    expect(field(item, 'required_credits')).toBe('60.0')
    expect(field(item, 'completed_credits')).toBe('40.0')
    expect(field(item, 'in_progress_credits')).toBe('12.0')
    expect(field(item, 'remaining_credits')).toBe('8.0')
  })

  it('never recomputes the server remaining credits', () => {
    const wrapper = mountList([gap({ remaining_credits: 99.0 })])
    const item = wrapper.find('.ep-gap-item')

    expect(field(item, 'remaining_credits')).toBe('99.0')
  })

  it('draws a three-part bar capped at 100%', () => {
    const wrapper = mountList([
      gap({ required_credits: 30.0, completed_credits: 40.0, in_progress_credits: 5.0 }),
    ])

    const completed = wrapper.find('.ep-gap-item .ep-progress__seg--completed')
    const inProgress = wrapper.find('.ep-gap-item .ep-progress__seg--in_progress')
    const remaining = wrapper.find('.ep-gap-item .ep-progress__seg--remaining')

    expect(completed.attributes('style')).toContain('100%')
    expect(inProgress.attributes('style')).toContain('0%')
    expect(remaining.attributes('style')).toContain('0%')
    expect(wrapper.find('.ep-gap-item .ep-progress').attributes('aria-valuemax')).toBe('100')
  })

  it('reports invalid category data in place', () => {
    const wrapper = mountList([gap({ completed_credits: Number.NaN })])

    expect(wrapper.find('.ep-gap-item__error').exists()).toBe(true)
    expect(wrapper.find('.ep-gap-item__error').text()).toContain('数据错误')
  })

  it('shows an explicit empty state instead of inventing categories', () => {
    const wrapper = mountList([])

    expect(wrapper.text()).toContain('没有类别学分缺口')
    expect(wrapper.find('.ep-gap-item').exists()).toBe(false)
  })
})
