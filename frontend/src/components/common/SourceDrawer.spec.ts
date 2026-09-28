import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ElementPlus from 'element-plus'

import { ApiError } from '@/api/client'
import type { SourceDetail } from '@/api/retrieval'

const mocks = vi.hoisted(() => ({ fetchSource: vi.fn() }))

vi.mock('@/api/retrieval', async () => {
  const actual = await vi.importActual<typeof import('@/api/retrieval')>('@/api/retrieval')
  return { ...actual, fetchSource: mocks.fetchSource }
})

import SourceDrawer from './SourceDrawer.vue'

function source(overrides: Partial<SourceDetail> = {}): SourceDetail {
  return {
    chunk_id: 'a'.repeat(64),
    doc_id: 'doc-1',
    file_name: '01-培养方案.pdf',
    file_type: 'pdf',
    document_version: '2026.1',
    effective_from: '2026-09-01',
    dataset_version: '2026.1',
    text: '毕业总学分为 155.0 学分。',
    page_number: 4,
    sheet_name: null,
    row_start: null,
    row_end: null,
    section_title: '培养方案/总则',
    ...overrides,
  }
}

async function mountDrawer(chunkId: string | null): Promise<VueWrapper> {
  const wrapper = mount(SourceDrawer, {
    props: { modelValue: true, chunkId },
    global: { plugins: [ElementPlus] },
  })
  await flushPromises()
  return wrapper
}

describe('SourceDrawer locators', () => {
  beforeEach(() => {
    mocks.fetchSource.mockReset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('shows page and section for a PDF source', async () => {
    mocks.fetchSource.mockResolvedValue(source())
    const wrapper = await mountDrawer('a'.repeat(64))

    const text = wrapper.text()
    expect(text).toContain('01-培养方案.pdf')
    expect(text).toContain('2026.1')
    expect(text).toContain('2026-09-01')
    expect(text).toContain('第 4 页')
    expect(text).toContain('培养方案/总则')
    expect(wrapper.find('.ep-source__text').text()).toBe('毕业总学分为 155.0 学分。')
  })

  it('shows worksheet and row range for a spreadsheet source', async () => {
    mocks.fetchSource.mockResolvedValue(
      source({
        file_name: '13-课程记录-匿名学生A.xlsx',
        file_type: 'xlsx',
        page_number: null,
        sheet_name: '课程记录',
        row_start: 2,
        row_end: 5,
        section_title: null,
      }),
    )
    const wrapper = await mountDrawer('b'.repeat(64))

    const text = wrapper.text()
    expect(text).toContain('课程记录')
    expect(text).toContain('2-5')
    expect(text).not.toContain('第 4 页')
  })

  it('shows the section for a DOCX source', async () => {
    mocks.fetchSource.mockResolvedValue(
      source({
        file_name: '06-制度-选课管理办法.docx',
        file_type: 'docx',
        page_number: null,
        section_title: '第一章/选课范围',
      }),
    )
    const wrapper = await mountDrawer('c'.repeat(64))

    expect(wrapper.text()).toContain('第一章/选课范围')
  })

  it('renders the original text as plain text without executing markup', async () => {
    mocks.fetchSource.mockResolvedValue(source({ text: '<script>alert(1)</script>' }))
    const wrapper = await mountDrawer('a'.repeat(64))

    expect(wrapper.find('script').exists()).toBe(false)
    expect(wrapper.find('.ep-source__text').text()).toBe('<script>alert(1)</script>')
  })
})

describe('SourceDrawer request lifecycle', () => {
  beforeEach(() => {
    mocks.fetchSource.mockReset()
  })

  it('does not request anything while closed', async () => {
    const wrapper = mount(SourceDrawer, {
      props: { modelValue: false, chunkId: 'a'.repeat(64) },
      global: { plugins: [ElementPlus] },
    })
    await flushPromises()

    expect(mocks.fetchSource).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('surfaces SOURCE_NOT_FOUND with its code and request id and can retry', async () => {
    mocks.fetchSource.mockRejectedValueOnce(
      new ApiError('来源不存在或不可检索', {
        kind: 'http',
        status: 404,
        code: 'SOURCE_NOT_FOUND',
        requestId: 'req-source-1',
      }),
    )
    const wrapper = await mountDrawer('a'.repeat(64))

    const alert = wrapper.find('.ep-error-alert')
    expect(alert.exists()).toBe(true)
    expect(alert.text()).toContain('SOURCE_NOT_FOUND')
    expect(alert.text()).toContain('req-source-1')

    mocks.fetchSource.mockResolvedValue(source())
    await wrapper.find('.ep-source__retry').trigger('click')
    await flushPromises()

    expect(mocks.fetchSource).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('毕业总学分为 155.0 学分。')
  })

  it('reports a network failure without a request id', async () => {
    mocks.fetchSource.mockRejectedValue(
      new ApiError('无法连接后端服务（网络错误或跨域被阻断）', { kind: 'network' }),
    )
    const wrapper = await mountDrawer('a'.repeat(64))

    const alert = wrapper.find('.ep-error-alert')
    expect(alert.text()).toContain('无法连接后端服务')
    expect(alert.text()).toContain('未获得服务端请求编号')
  })

  it('aborts the previous request and ignores its stale response', async () => {
    const resolvers: Array<(value: SourceDetail) => void> = []
    mocks.fetchSource.mockImplementation(
      () => new Promise<SourceDetail>((resolve) => resolvers.push(resolve)),
    )

    const wrapper = await mountDrawer('a'.repeat(64))
    await wrapper.setProps({ chunkId: 'b'.repeat(64) })
    await flushPromises()

    expect(mocks.fetchSource).toHaveBeenCalledTimes(2)
    expect(mocks.fetchSource.mock.calls[0][1]?.aborted).toBe(true)

    resolvers[1](source({ chunk_id: 'b'.repeat(64), file_name: '较新的来源.pdf' }))
    await flushPromises()
    expect(wrapper.text()).toContain('较新的来源.pdf')

    resolvers[0](source({ chunk_id: 'a'.repeat(64), file_name: '过期的来源.pdf' }))
    await flushPromises()

    expect(wrapper.text()).toContain('较新的来源.pdf')
    expect(wrapper.text()).not.toContain('过期的来源.pdf')
  })

  it('aborts the active request when the drawer closes', async () => {
    mocks.fetchSource.mockImplementation(() => new Promise<SourceDetail>(() => {}))
    const wrapper = await mountDrawer('a'.repeat(64))

    await wrapper.setProps({ modelValue: false })
    await flushPromises()

    expect(mocks.fetchSource.mock.calls[0][1]?.aborted).toBe(true)
  })
})
