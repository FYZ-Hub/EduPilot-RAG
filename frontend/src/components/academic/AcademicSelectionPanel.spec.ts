import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'

import { ApiError } from '@/api/client'
import type { AcademicOptions } from '@/api/academic'
import AcademicSelectionPanel from './AcademicSelectionPanel.vue'

function options(): AcademicOptions {
  return {
    record_sets: [
      {
        id: 'rs-1',
        name: '匿名学生A · 课程记录',
        source_doc_id: 'doc-1',
        status: 'ready',
        updated_at: '2026-09-01T02:00:00Z',
      },
    ],
    rule_sets: [
      {
        id: 'ru-1',
        name: '计算机科学与技术 2026.1',
        major: '计算机科学与技术',
        admission_year: 2026,
        rule_version: '2026.1',
        effective_from: '2026-09-01',
        source_doc_id: 'doc-2',
        status: 'ready',
      },
    ],
  }
}

function mountPanel(props: Record<string, unknown> = {}) {
  return mount(AcademicSelectionPanel, {
    props: {
      options: options(),
      loading: false,
      error: null,
      selectedRecordSetId: null,
      selectedRuleSetId: null,
      disabled: false,
      ...props,
    },
    global: { plugins: [ElementPlus] },
  })
}

describe('AcademicSelectionPanel', () => {
  it('labels both selectors visibly', () => {
    const wrapper = mountPanel()

    expect(wrapper.find('label[for="ep-record-set"]').text()).toContain('课程记录集')
    expect(wrapper.find('label[for="ep-rule-set"]').text()).toContain('培养方案规则')
  })

  it('renders every real field of each option', () => {
    const wrapper = mountPanel()

    const record = wrapper.find('.ep-selection__record option[value="rs-1"]').text()
    expect(record).toContain('匿名学生A · 课程记录')
    expect(record).toContain('ready')

    const rule = wrapper.find('.ep-selection__rule option[value="ru-1"]').text()
    expect(rule).toContain('计算机科学与技术')
    expect(rule).toContain('2026')
    expect(rule).toContain('2026.1')
    expect(rule).toContain('2026-09-01')
  })

  it('emits the chosen ids instead of selecting anything itself', async () => {
    const wrapper = mountPanel()

    await wrapper.find('.ep-selection__record').setValue('rs-1')
    await wrapper.find('.ep-selection__rule').setValue('ru-1')

    expect(wrapper.emitted('select-record')).toEqual([['rs-1']])
    expect(wrapper.emitted('select-rule')).toEqual([['ru-1']])
  })

  it('shows an explicit empty state when nothing was imported yet', () => {
    const wrapper = mountPanel({ options: { record_sets: [], rule_sets: [] } })

    expect(wrapper.text()).toContain('还没有可用的课程记录')
    expect(wrapper.text()).toContain('还没有可用的培养方案规则')
    expect(wrapper.find('.ep-selection__record option[value="rs-1"]').exists()).toBe(false)
  })

  it('shows the real error with a manual retry', async () => {
    const wrapper = mountPanel({
      options: null,
      error: new ApiError('无法连接后端服务', { kind: 'network' }),
    })

    expect(wrapper.find('.ep-error-alert').text()).toContain('无法连接后端服务')

    await wrapper.find('.ep-selection__retry').trigger('click')
    expect(wrapper.emitted('retry')).toHaveLength(1)
  })

  it('announces loading and disables the selectors', () => {
    const wrapper = mountPanel({ loading: true, disabled: true })

    expect(wrapper.find('.ep-selection__loading').attributes('aria-live')).toBe('polite')
    expect(wrapper.find('.ep-selection__record').attributes('disabled')).toBeDefined()
    expect(wrapper.find('.ep-selection__rule').attributes('disabled')).toBeDefined()
  })
})
