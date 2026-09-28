import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'

import type { ChatCitation } from '@/api/chat'

import EvidencePanel from './EvidencePanel.vue'

function citation(overrides: Partial<ChatCitation> = {}): ChatCitation {
  return {
    citation_index: 1,
    chunk_id: 'chunk-1',
    doc_id: 'doc-1',
    file_name: '01-培养方案.pdf',
    document_version: '2026.1',
    effective_from: '2026-09-01',
    dataset_version: '2026.1',
    page_number: 4,
    sheet_name: null,
    row_start: null,
    row_end: null,
    section_title: '培养方案/总则',
    quote: '毕业总学分为 155.0 学分。',
    ...overrides,
  }
}

function mountPanel(citations: ChatCitation[], selectedIndex: number | null = null) {
  return mount(EvidencePanel, {
    props: { citations, selectedIndex },
    global: { plugins: [ElementPlus] },
  })
}

describe('EvidencePanel', () => {
  it('asks the user to click a citation when nothing is selected', () => {
    const wrapper = mountPanel([])

    expect(wrapper.find('.ep-evidence__empty').text()).toContain('点击回答中的引用查看原文')
    expect(wrapper.findAll('.ep-evidence-card')).toHaveLength(0)
  })

  it('shows only the fields present on a real citation', () => {
    const wrapper = mountPanel([citation()])
    const card = wrapper.find('.ep-evidence-card')

    expect(card.find('.ep-evidence-card__index').text()).toBe('[1]')
    expect(card.text()).toContain('01-培养方案.pdf')
    expect(card.text()).toContain('2026.1')
    expect(card.text()).toContain('2026-09-01')
    expect(card.text()).toContain('第 4 页')
    expect(card.text()).toContain('培养方案/总则')
    expect(card.find('.ep-evidence-card__quote').text()).toBe('毕业总学分为 155.0 学分。')
    expect(card.text()).not.toContain('工作表')
    expect(card.text()).not.toContain('行')
  })

  it('renders the worksheet and row range for a spreadsheet citation', () => {
    const wrapper = mountPanel([
      citation({
        citation_index: 2,
        chunk_id: 'chunk-2',
        file_name: '13-课程记录-匿名学生A.xlsx',
        page_number: null,
        sheet_name: '课程记录',
        row_start: 2,
        row_end: 5,
        section_title: null,
      }),
    ])

    const card = wrapper.find('.ep-evidence-card')
    expect(card.text()).toContain('课程记录')
    expect(card.text()).toContain('2-5')
    expect(card.text()).not.toContain('页码')
  })

  it('keeps every conflicting version side by side', () => {
    const wrapper = mountPanel([
      citation({ citation_index: 1, document_version: '2025.1', effective_from: '2025-09-01' }),
      citation({
        citation_index: 2,
        chunk_id: 'chunk-2',
        doc_id: 'doc-2',
        file_name: '02-培养方案-2026修订版.pdf',
        document_version: '2026.1',
        effective_from: '2026-09-01',
      }),
    ])

    const cards = wrapper.findAll('.ep-evidence-card')
    expect(cards).toHaveLength(2)
    expect(cards[0].text()).toContain('2025.1')
    expect(cards[1].text()).toContain('2026.1')
  })

  it('marks the selected card and emits the index when another card is clicked', async () => {
    const wrapper = mountPanel(
      [citation({ citation_index: 1 }), citation({ citation_index: 2, chunk_id: 'chunk-2' })],
      1,
    )

    const cards = wrapper.findAll('.ep-evidence-card')
    expect(cards[0].classes()).toContain('is-selected')
    expect(cards[1].classes()).not.toContain('is-selected')

    await cards[1].trigger('click')
    expect(wrapper.emitted('select')).toEqual([[2]])
  })

  it('requests the original text only when the user asks for it', async () => {
    const wrapper = mountPanel([citation()])

    expect(wrapper.emitted('open-source')).toBeUndefined()

    await wrapper.find('.ep-evidence-card__open').trigger('click')

    expect(wrapper.emitted('open-source')).toHaveLength(1)
    expect((wrapper.emitted('open-source')?.[0]?.[0] as ChatCitation).chunk_id).toBe('chunk-1')
  })
})
