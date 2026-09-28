import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'

import type { MissingRequiredCourse, PlanningEvidence } from '@/api/academic'
import MissingCourseList from './MissingCourseList.vue'

function evidence(chunkId: string): PlanningEvidence {
  return {
    chunk_id: chunkId,
    doc_id: `doc-${chunkId}`,
    file_name: `${chunkId}.pdf`,
    document_version: null,
    effective_from: null,
    page_number: 2,
    sheet_name: null,
    row_start: null,
    row_end: null,
    section_title: null,
    quote: 'quote',
  }
}

function course(overrides: Partial<MissingRequiredCourse> = {}): MissingRequiredCourse {
  return {
    course_code: 'QM-CS401',
    course_name: '毕业设计',
    credits: 8.0,
    category: '专业必修',
    evidence_chunk_ids: ['c-1'],
    ...overrides,
  }
}

function mountList(courses: MissingRequiredCourse[], evidenceList: PlanningEvidence[] = []) {
  return mount(MissingCourseList, {
    props: { courses, evidence: evidenceList },
    global: { plugins: [ElementPlus] },
  })
}

describe('MissingCourseList', () => {
  it('renders the five server fields without allowing edits', () => {
    const wrapper = mountList([course()], [evidence('c-1')])
    const row = wrapper.find('.ep-missing__row')

    expect(row.text()).toContain('QM-CS401')
    expect(row.text()).toContain('毕业设计')
    expect(row.text()).toContain('8.0')
    expect(row.text()).toContain('专业必修')
    expect(wrapper.find('input').exists()).toBe(false)
    expect(wrapper.find('textarea').exists()).toBe(false)
  })

  it('renders desktop table headers', () => {
    const wrapper = mountList([course()], [evidence('c-1')])

    const headers = wrapper.findAll('.ep-missing__table th').map((node) => node.text())
    expect(headers).toEqual(['课程代码', '课程名称', '学分', '类别', '证据'])
  })

  it('links evidence chips only to real evidence chunk ids', () => {
    const wrapper = mountList(
      [course({ evidence_chunk_ids: ['c-1', 'not-real'] })],
      [evidence('c-1')],
    )

    const chips = wrapper.findAll('.ep-evidence-chip')
    expect(chips).toHaveLength(1)
    expect(chips[0].attributes('data-chunk-id')).toBe('c-1')
  })

  it('emits the chunk id when an evidence chip is activated', async () => {
    const wrapper = mountList([course()], [evidence('c-1')])

    await wrapper.find('.ep-evidence-chip').trigger('click')

    expect(wrapper.emitted('focus-evidence')).toEqual([['c-1']])
  })

  it('creates no chip when no evidence id can be resolved', () => {
    const wrapper = mountList([course({ evidence_chunk_ids: ['missing'] })], [evidence('c-1')])

    expect(wrapper.find('.ep-evidence-chip').exists()).toBe(false)
  })

  it('shows the satisfied state when nothing is missing', () => {
    const wrapper = mountList([])

    expect(wrapper.text()).toContain('已满足当前规则中的必修课要求')
    expect(wrapper.find('.ep-missing__row').exists()).toBe(false)
  })
})
