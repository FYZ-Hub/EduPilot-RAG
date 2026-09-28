import { describe, expect, it } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ElementPlus from 'element-plus'

import type { DocumentDetail, DocumentPreview, PreviewBlock } from '@/api/documents'

import DocumentPreviewDrawer from './DocumentPreviewDrawer.vue'

function detail(overrides: Partial<DocumentDetail> = {}): DocumentDetail {
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
    chunk_count: 2,
    block_count: 2,
    locator_types: ['page_number', 'section_title'],
    created_at: '2026-03-01T02:00:00Z',
    updated_at: '2026-03-02T02:00:00Z',
    error: null,
    document_version: '2026.1',
    effective_from: '2026-01-01',
    checksum: 'abcdef0123456789abcdef0123456789',
    ...overrides,
  }
}

function block(overrides: Partial<PreviewBlock> = {}): PreviewBlock {
  return {
    block_index: 0,
    block_type: 'text',
    text: '示例文本',
    page_number: null,
    sheet_name: null,
    row_start: null,
    row_end: null,
    section_title: null,
    metadata: null,
    ...overrides,
  }
}

function preview(fileType: 'pdf' | 'docx' | 'xlsx', blocks: PreviewBlock[]): DocumentPreview {
  return {
    document_id: 'doc-1',
    file_name: 'plan.' + fileType,
    file_type: fileType,
    status: 'ready',
    total_blocks: blocks.length,
    returned_blocks: blocks.length,
    truncated: false,
    blocks,
  }
}

async function mountDrawer(
  document: DocumentDetail | null,
  data: DocumentPreview | null,
): Promise<VueWrapper> {
  const wrapper = mount(DocumentPreviewDrawer, {
    props: { modelValue: true, document, preview: data, loading: false, error: null },
    global: { plugins: [ElementPlus] },
  })
  // ElDrawer 在 onMounted 后才渲染内容，需要等待一次刷新
  await flushPromises()
  return wrapper
}

describe('DocumentPreviewDrawer locators', () => {
  it('shows page and section locators for PDF', async () => {
    const wrapper = await mountDrawer(
      detail(),
      preview('pdf', [block({ page_number: 4, section_title: '培养方案/总则' })]),
    )
    const text = wrapper.text()
    expect(text).toContain('页码：第 4 页')
    expect(text).toContain('章节：培养方案/总则')
    expect(wrapper.findAll('.ep-preview__block')).toHaveLength(1)
  })

  it('shows the heading path for DOCX', async () => {
    const wrapper = await mountDrawer(
      detail({ file_type: 'docx' }),
      preview('docx', [block({ section_title: '第 1 章/1.1 目标' })]),
    )
    expect(wrapper.text()).toContain('标题路径：第 1 章/1.1 目标')
  })

  it('shows sheet name and row range for XLSX without page fields', async () => {
    const wrapper = await mountDrawer(
      detail({ file_type: 'xlsx' }),
      preview('xlsx', [block({ sheet_name: '课程记录', row_start: 2, row_end: 5, page_number: 1 })]),
    )
    const text = wrapper.text()
    expect(text).toContain('工作表：课程记录')
    expect(text).toContain('行范围：2-5')
    expect(text).not.toContain('页码：')
  })

  it('renders an explicit marker when a block has no locator fields', async () => {
    const wrapper = await mountDrawer(detail(), preview('pdf', [block()]))
    expect(wrapper.text()).toContain('无定位字段')
  })

  it('renders the backend SHA-256 truncated to 12 characters and no other checksum', async () => {
    const wrapper = await mountDrawer(detail(), preview('pdf', [block({ page_number: 1 })]))
    const text = wrapper.text()
    expect(text).toContain('SHA-256 abcdef012345')
    expect(text).not.toContain('abcdef0123456789')
  })

  it('renders document text as plain text without executing HTML', async () => {
    const wrapper = await mountDrawer(
      detail(),
      preview('pdf', [block({ page_number: 1, text: '<img src=x onerror=alert(1)>' })]),
    )
    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.find('.ep-preview__text').text()).toContain('<img src=x onerror=alert(1)>')
  })
})
