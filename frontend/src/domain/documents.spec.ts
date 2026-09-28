import { describe, expect, it } from 'vitest'

import type { DocumentListItem } from '@/api/documents'
import {
  categoryLabel,
  describeDocumentStatus,
  documentSubtitle,
  fileTypeLabel,
  formatLocalDateTime,
  isDocumentProcessing,
  locatorLabel,
  previewLocators,
  shortChecksum,
  sourceLabel,
} from './documents'

function doc(overrides: Partial<DocumentListItem> = {}): DocumentListItem {
  return {
    id: 'doc-1',
    file_name: 'plan.pdf',
    file_type: 'pdf',
    doc_category: 'degree_plan',
    source_type: 'demo',
    dataset_version: '2026.1',
    status: 'ready',
    retrievable: true,
    activation_state: 'active',
    current_stage: 'completed',
    chunk_count: 4,
    block_count: 4,
    locator_types: ['page_number', 'section_title'],
    created_at: '2026-03-01T02:00:00Z',
    updated_at: '2026-03-02T02:00:00Z',
    error: null,
    ...overrides,
  }
}

describe('document status mapping', () => {
  it('shows 可检索 only when retrievable is true', () => {
    expect(describeDocumentStatus(doc({ retrievable: true })).label).toBe('可检索')
  })

  it('does not claim retrievable when ready but not retrievable', () => {
    const descriptor = describeDocumentStatus(
      doc({ retrievable: false, status: 'ready', activation_state: 'active' }),
    )
    expect(descriptor.label).toBe('已处理，未进入检索')
    expect(descriptor.label).not.toBe('可检索')
  })

  it('maps candidate ready documents to 已处理，待整体激活', () => {
    const descriptor = describeDocumentStatus(
      doc({ retrievable: false, status: 'ready', activation_state: 'candidate' }),
    )
    expect(descriptor.label).toBe('已处理，待整体激活')
    expect(descriptor.tone).toBe('warning')
  })

  it('maps inactive ready documents to 已退役', () => {
    expect(
      describeDocumentStatus(doc({ retrievable: false, status: 'ready', activation_state: 'inactive' }))
        .label,
    ).toBe('已退役')
  })

  it('maps failed documents to 失败 regardless of retrievable', () => {
    expect(describeDocumentStatus(doc({ retrievable: false, status: 'failed' })).label).toBe('失败')
  })

  it('maps processing statuses to the specified Chinese labels', () => {
    expect(describeDocumentStatus(doc({ retrievable: false, status: 'queued' })).label).toBe('等待处理')
    expect(describeDocumentStatus(doc({ retrievable: false, status: 'parsing' })).label).toBe('解析中')
    expect(describeDocumentStatus(doc({ retrievable: false, status: 'storing' })).label).toBe('解析中')
    expect(describeDocumentStatus(doc({ retrievable: false, status: 'embedding' })).label).toBe('索引中')
    expect(describeDocumentStatus(doc({ retrievable: false, status: 'keyword_indexing' })).label).toBe(
      '索引中',
    )
  })

  it('treats only non-terminal statuses as processing', () => {
    expect(isDocumentProcessing('queued')).toBe(true)
    expect(isDocumentProcessing('embedding')).toBe(true)
    expect(isDocumentProcessing('ready')).toBe(false)
    expect(isDocumentProcessing('failed')).toBe(false)
  })
})

describe('document labels', () => {
  it('maps categories, sources, file types and locator capabilities', () => {
    expect(categoryLabel('degree_plan')).toBe('培养方案')
    expect(categoryLabel('unknown_custom')).toBe('unknown_custom')
    expect(sourceLabel('demo')).toBe('演示资料')
    expect(sourceLabel('upload')).toBe('用户上传')
    expect(fileTypeLabel('xlsx')).toBe('XLSX')
    expect(locatorLabel(['page_number', 'section_title'])).toBe('页码 · 章节')
    expect(locatorLabel([])).toBe('—')
  })

  it('builds a subtitle from category and version', () => {
    expect(documentSubtitle({ doc_category: 'course_syllabus', dataset_version: '2026.1' })).toBe(
      '课程大纲 · 2026.1',
    )
    expect(documentSubtitle({ doc_category: 'degree_plan', dataset_version: null })).toBe('培养方案')
  })
})

describe('local absolute time', () => {
  it('formats on the local timezone without fuzzy wording', () => {
    const formatted = formatLocalDateTime('2026-03-02T02:03:04Z')
    expect(formatted).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/)
    expect(formatted).not.toContain('刚刚')
  })

  it('returns an em dash for missing or invalid values', () => {
    expect(formatLocalDateTime(null)).toBe('—')
    expect(formatLocalDateTime('not-a-date')).toBe('—')
  })
})

describe('checksum display', () => {
  it('shows only the leading 12 characters of the backend SHA-256', () => {
    const checksum = 'abcdef0123456789abcdef0123456789'
    expect(shortChecksum(checksum)).toBe('abcdef012345')
    expect(shortChecksum(null)).toBe('—')
  })
})

describe('preview locators by file type', () => {
  const base = { page_number: null, section_title: null, sheet_name: null, row_start: null, row_end: null }

  it('shows page and section for PDF', () => {
    const locators = previewLocators('pdf', { ...base, page_number: 4, section_title: '培养方案/总则' })
    expect(locators.map((item) => item.label)).toEqual(['页码', '章节'])
    expect(locators[0].value).toBe('第 4 页')
  })

  it('labels the section as 标题路径 for DOCX', () => {
    const locators = previewLocators('docx', { ...base, section_title: '第 1 章/1.1 目标' })
    expect(locators).toEqual([{ label: '标题路径', value: '第 1 章/1.1 目标' }])
  })

  it('shows sheet name and row range for XLSX and omits page fields', () => {
    const locators = previewLocators('xlsx', {
      ...base,
      sheet_name: '课程记录',
      row_start: 2,
      row_end: 5,
      page_number: 1,
    })
    expect(locators).toEqual([
      { label: '工作表', value: '课程记录' },
      { label: '行范围', value: '2-5' },
    ])
  })

  it('omits empty locator fields instead of rendering blank tags', () => {
    expect(previewLocators('pdf', base)).toEqual([])
    expect(previewLocators('xlsx', base)).toEqual([])
  })
})
