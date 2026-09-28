import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'

import type { PlanningEvidence } from '@/api/academic'
import PlanningEvidencePanel from './PlanningEvidencePanel.vue'

function evidence(overrides: Partial<PlanningEvidence> = {}): PlanningEvidence {
  return {
    chunk_id: 'c-1',
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
    ...overrides,
  }
}

function mountPanel(props: {
  evidence: PlanningEvidence[]
  focusedChunkId?: string | null
}) {
  return mount(PlanningEvidencePanel, {
    props,
    global: { plugins: [ElementPlus] },
  })
}

describe('PlanningEvidencePanel', () => {
  it('renders only the real fields returned by the server', () => {
    const wrapper = mountPanel({ evidence: [evidence()] })

    const card = wrapper.find('.ep-evidence-card')
    expect(card.exists()).toBe(true)
    expect(card.text()).toContain('培养方案.pdf')
    expect(card.text()).toContain('2026.1')
    expect(card.text()).toContain('2026-09-01')
    expect(card.text()).toContain('第 4 页')
    expect(card.text()).toContain('培养方案/总则')
    expect(card.text()).toContain('毕业总学分 155.0')
    expect(card.text()).not.toContain('doc-1')
    expect(card.text()).not.toContain('c-1')
  })

  it('does not render empty locator labels for missing fields', () => {
    const wrapper = mountPanel({
      evidence: [evidence({ page_number: null, section_title: null })],
    })

    const card = wrapper.find('.ep-evidence-card')
    expect(card.text()).not.toContain('页')
    expect(card.text()).not.toContain('工作表')
    expect(card.text()).not.toContain('行')
  })

  it('shows xlsx sheet and row locators when the server provides them', () => {
    const wrapper = mountPanel({
      evidence: [
        evidence({
          page_number: null,
          section_title: null,
          sheet_name: '成绩汇总',
          row_start: 3,
          row_end: 5,
        }),
      ],
    })

    const card = wrapper.find('.ep-evidence-card')
    expect(card.text()).toContain('成绩汇总')
    expect(card.text()).toContain('3-5')
  })

  it('emits open-source only when the user clicks 查看原文', async () => {
    const item = evidence()
    const wrapper = mountPanel({ evidence: [item] })

    expect(wrapper.emitted('open-source')).toBeUndefined()

    await wrapper.find('.ep-evidence-card__open').trigger('click')

    expect(wrapper.emitted('open-source')).toEqual([[item]])
  })

  it('marks the linked chunk id so a warning or missing course can locate it', () => {
    const wrapper = mountPanel({ evidence: [evidence()], focusedChunkId: 'c-1' })

    const card = wrapper.find('.ep-evidence-card')
    expect(card.attributes('data-chunk-id')).toBe('c-1')
    expect(card.classes()).toContain('is-focused')
  })

  it('renders html-looking quote as plain text', () => {
    const wrapper = mountPanel({
      evidence: [evidence({ quote: '<img src=x onerror="alert(1)">' })],
    })

    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.find('.ep-evidence-card__quote').text()).toBe('<img src=x onerror="alert(1)">')
  })

  it('shows an explicit empty state instead of inventing evidence', () => {
    const wrapper = mountPanel({ evidence: [] })

    expect(wrapper.text()).toContain('本次计算没有可展示的证据')
    expect(wrapper.find('.ep-evidence-card').exists()).toBe(false)
  })
})

describe('PlanningEvidencePanel focus scrolling', () => {
  let scrollIntoView: ReturnType<typeof vi.fn>

  beforeEach(() => {
    scrollIntoView = vi.fn()
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      value: scrollIntoView,
      configurable: true,
      writable: true,
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('scrolls when the focused id is already set before mount', async () => {
    mountPanel({ evidence: [evidence()], focusedChunkId: 'c-1' })
    await flushPromises()

    expect(scrollIntoView).toHaveBeenCalledTimes(1)
    expect(scrollIntoView).toHaveBeenCalledWith({ block: 'nearest' })
  })

  it('scrolls again when the focused chunk changes later', async () => {
    const wrapper = mountPanel({
      evidence: [evidence(), evidence({ chunk_id: 'c-2' })],
      focusedChunkId: null,
    })
    await flushPromises()
    expect(scrollIntoView).not.toHaveBeenCalled()

    await wrapper.setProps({ focusedChunkId: 'c-2' })
    await flushPromises()

    expect(scrollIntoView).toHaveBeenCalledTimes(1)
  })

  it('never scrolls without a focused chunk id', async () => {
    mountPanel({ evidence: [evidence()], focusedChunkId: null })
    await flushPromises()

    expect(scrollIntoView).not.toHaveBeenCalled()
  })
})
