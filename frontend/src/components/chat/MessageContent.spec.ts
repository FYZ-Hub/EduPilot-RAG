import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'

import type { ChatCitation } from '@/api/chat'

import MessageContent from './MessageContent.vue'

function citation(index: number): ChatCitation {
  return {
    citation_index: index,
    chunk_id: `chunk-${index}`,
    doc_id: `doc-${index}`,
    file_name: `file-${index}.pdf`,
    document_version: '2026.1',
    effective_from: '2026-09-01',
    dataset_version: '2026.1',
    page_number: index,
    sheet_name: null,
    row_start: null,
    row_end: null,
    section_title: `章节 ${index}`,
    quote: `原文 ${index}`,
  }
}

function mountContent(props: {
  content: string
  citations?: ChatCitation[]
  activeIndex?: number | null
}) {
  return mount(MessageContent, {
    props,
    global: { plugins: [ElementPlus] },
  })
}

describe('MessageContent structured rendering', () => {
  it('renders paragraphs, lists, code blocks and emphasis as elements', () => {
    const wrapper = mountContent({
      content: '第一段\n\n- 甲\n- 乙\n\n```\nconst a = 1\n```\n\n**重点** 与 `代码`',
    })

    expect(wrapper.findAll('.ep-md__paragraph')).toHaveLength(2)
    expect(wrapper.findAll('.ep-md__list li')).toHaveLength(2)
    expect(wrapper.find('.ep-md__code').text()).toBe('const a = 1')
    expect(wrapper.find('strong').text()).toBe('重点')
    expect(wrapper.find('code.ep-md__inline-code').text()).toBe('代码')
  })

  it('turns a citation marker into a focusable button when the citation exists', () => {
    const wrapper = mountContent({ content: '依据 [1] 的规定', citations: [citation(1)] })

    const markers = wrapper.findAll('button.ep-citation')
    expect(markers).toHaveLength(1)
    expect(markers[0].text()).toBe('[1]')
    expect(markers[0].attributes('disabled')).toBeUndefined()
    expect(markers[0].attributes('aria-label')).toContain('1')
  })

  it('emits the citation index when the marker is activated', async () => {
    const wrapper = mountContent({ content: '依据 [2]', citations: [citation(2)] })

    await wrapper.find('button.ep-citation').trigger('click')

    expect(wrapper.emitted('select')).toEqual([[2]])
  })

  it('renders a disabled placeholder while the citation has not arrived', () => {
    const wrapper = mountContent({ content: '依据 [1]', citations: [] })

    expect(wrapper.findAll('button.ep-citation')).toHaveLength(0)
    const placeholder = wrapper.find('span.ep-citation--pending')
    expect(placeholder.exists()).toBe(true)
    expect(placeholder.attributes('aria-disabled')).toBe('true')
    expect(placeholder.text()).toBe('[1]')
  })

  it('never creates source cards or links from the answer text alone', () => {
    const wrapper = mountContent({ content: '见 [1] 与 [2]', citations: [] })

    expect(wrapper.find('a').exists()).toBe(false)
    expect(wrapper.findAll('button.ep-citation')).toHaveLength(0)
  })

  it('marks the active citation marker', () => {
    const wrapper = mountContent({
      content: '依据 [1]',
      citations: [citation(1)],
      activeIndex: 1,
    })

    expect(wrapper.find('button.ep-citation').classes()).toContain('is-active')
  })
})

describe('MessageContent safety', () => {
  it('renders model-provided HTML as text and never executes it', () => {
    const wrapper = mountContent({
      content:
        '<script>alert(1)</script>\n<img src=x onerror=alert(2)>\n<a href="https://evil.test">链接</a>',
    })

    expect(wrapper.find('script').exists()).toBe(false)
    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.find('a').exists()).toBe(false)
    expect(wrapper.text()).toContain('<script>alert(1)</script>')
    expect(wrapper.text()).toContain('onerror=alert(2)')
    expect(wrapper.text()).toContain('https://evil.test')
  })

  it('renders quote-like content without interpreting markup or event attributes', () => {
    const wrapper = mountContent({ content: '<iframe onload=alert(3)></iframe>' })

    expect(wrapper.find('iframe').exists()).toBe(false)
    expect(wrapper.text()).toBe('<iframe onload=alert(3)></iframe>')
  })
})
