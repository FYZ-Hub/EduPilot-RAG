import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'

import type { ConflictWarning, PlanningEvidence } from '@/api/academic'
import ConflictWarningPanel from './ConflictWarningPanel.vue'

function evidence(chunkId: string): PlanningEvidence {
  return {
    chunk_id: chunkId,
    doc_id: `doc-${chunkId}`,
    file_name: `${chunkId}.pdf`,
    document_version: '2026.1',
    effective_from: null,
    page_number: 1,
    sheet_name: null,
    row_start: null,
    row_end: null,
    section_title: null,
    quote: 'quote',
  }
}

function warning(overrides: Partial<ConflictWarning> = {}): ConflictWarning {
  return {
    code: 'DEGREE_PLAN_VERSION_CONFLICT',
    message: '同时存在两个生效的培养方案版本',
    severity: 'warning',
    evidence_chunk_ids: ['c-1'],
    ...overrides,
  }
}

function mountPanel(warnings: ConflictWarning[], evidenceList: PlanningEvidence[] = []) {
  return mount(ConflictWarningPanel, {
    props: { warnings, evidence: evidenceList },
    global: { plugins: [ElementPlus] },
  })
}

describe('ConflictWarningPanel', () => {
  it('shows a neutral green state when there is no conflict', () => {
    const wrapper = mountPanel([])

    expect(wrapper.text()).toContain('未发现规则冲突')
    expect(wrapper.find('.ep-conflict-item').exists()).toBe(false)
  })

  it('groups the known server codes by their category', () => {
    const wrapper = mountPanel([
      warning(),
      warning({ code: 'COURSE_TIME_CONFLICT', message: '同一时间段有两门在修课程' }),
      warning({ code: 'COURSE_RECORD_CONTRADICTION', message: '同一课程既通过又有不及格记录' }),
    ])

    const groups = wrapper.findAll('.ep-conflict-group').map((node) => node.attributes('data-group'))
    expect(groups).toEqual(['version', 'time', 'record'])
    expect(wrapper.text()).toContain('规则版本冲突')
    expect(wrapper.text()).toContain('课表冲突')
    expect(wrapper.text()).toContain('课程记录冲突')
  })

  it('keeps unknown codes visible in the generic group', () => {
    const wrapper = mountPanel([warning({ code: 'SOMETHING_NEW', message: '未知提醒' })])

    expect(wrapper.text()).toContain('SOMETHING_NEW')
    expect(wrapper.text()).toContain('未知提醒')
    expect(wrapper.find('.ep-conflict-group').attributes('data-group')).toBe('other')
  })

  it('states severity in text as well as colour', () => {
    const wrapper = mountPanel([
      warning({ code: 'COURSE_RECORD_CONTRADICTION', severity: 'blocking' }),
      warning(),
    ])

    const blocked = wrapper.find('[data-severity="blocking"]')
    const soft = wrapper.find('[data-severity="warning"]')
    expect(blocked.find('.ep-conflict-item__severity').text()).toBe('阻断')
    expect(soft.find('.ep-conflict-item__severity').text()).toBe('提醒')
  })

  it('links evidence chips only to real evidence chunk ids', async () => {
    const wrapper = mountPanel(
      [warning({ evidence_chunk_ids: ['c-1', 'not-real'] })],
      [evidence('c-1')],
    )

    const chips = wrapper.findAll('.ep-evidence-chip')
    expect(chips).toHaveLength(1)

    await chips[0].trigger('click')
    expect(wrapper.emitted('focus-evidence')).toEqual([['c-1']])
  })

  it('renders the server message as plain text', () => {
    const wrapper = mountPanel([warning({ message: '<b>粗体</b>' })])

    expect(wrapper.find('.ep-conflict-item__message').text()).toBe('<b>粗体</b>')
    expect(wrapper.find('b').exists()).toBe(false)
  })
})
