import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'

import { ApiError } from '@/api/client'
import { MAX_UPLOAD_BYTES } from '@/api/documents'
import type { AcademicOptions, ImportResult } from '@/api/academic'

const mocks = vi.hoisted(() => ({
  fetchAcademicOptions: vi.fn(),
  importAcademicRecords: vi.fn(),
  importAcademicRules: vi.fn(),
  calculateAcademicPlan: vi.fn(),
  uploadDocument: vi.fn(),
}))

vi.mock('@/api/academic', async () => {
  const actual = await vi.importActual<typeof import('@/api/academic')>('@/api/academic')
  return {
    ...actual,
    fetchAcademicOptions: mocks.fetchAcademicOptions,
    importAcademicRecords: mocks.importAcademicRecords,
    importAcademicRules: mocks.importAcademicRules,
    calculateAcademicPlan: mocks.calculateAcademicPlan,
  }
})

vi.mock('@/api/documents', async () => {
  const actual = await vi.importActual<typeof import('@/api/documents')>('@/api/documents')
  return { ...actual, uploadDocument: mocks.uploadDocument }
})

import AcademicImportDialog from './AcademicImportDialog.vue'

const EMPTY: AcademicOptions = { record_sets: [], rule_sets: [] }

function importResult(overrides: Partial<ImportResult> = {}): ImportResult {
  return { id: 'set-1', status: 'ready', warnings: [], ...overrides }
}

async function mountDialog(props: {
  modelValue?: boolean
  kind: 'records' | 'rules'
  disabled?: boolean
  disabledReason?: string
}): Promise<VueWrapper> {
  setActivePinia(createPinia())
  const wrapper = mount(AcademicImportDialog, {
    props: { modelValue: true, ...props },
    global: { plugins: [ElementPlus] },
  })
  await flushPromises()
  return wrapper
}

async function selectFile(wrapper: VueWrapper, file: File): Promise<void> {
  const input = wrapper.find('input[type="file"]')
  Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
  await input.trigger('change')
  await flushPromises()
}

function buttonByText(wrapper: VueWrapper, label: string) {
  return wrapper.findAll('button').find((node) => node.text().trim() === label)
}

beforeEach(() => {
  for (const mock of Object.values(mocks)) {
    mock.mockReset()
  }
  mocks.fetchAcademicOptions.mockResolvedValue(EMPTY)
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('AcademicImportDialog', () => {
  it('accepts only a single xlsx file for course records', async () => {
    const wrapper = await mountDialog({ kind: 'records' })

    expect(wrapper.find('input[type="file"]').attributes('accept')).toBe('.xlsx')
    expect(wrapper.text()).toContain('仅支持 XLSX')

    await selectFile(wrapper, new File(['a'], 'notes.pdf'))
    expect(wrapper.text()).toContain('不支持的文件类型')
    expect(mocks.importAcademicRecords).not.toHaveBeenCalled()
  })

  it('accepts pdf, docx and xlsx for degree rules', async () => {
    const wrapper = await mountDialog({ kind: 'rules' })

    expect(wrapper.find('input[type="file"]').attributes('accept')).toBe('.pdf,.docx,.xlsx')

    await selectFile(wrapper, new File(['a'], '培养方案.pdf'))
    expect(wrapper.text()).not.toContain('不支持的文件类型')
  })

  it('rejects an oversized file before calling the API', async () => {
    const wrapper = await mountDialog({ kind: 'rules' })
    const big = new File(['a'], 'big.pdf')
    Object.defineProperty(big, 'size', { value: MAX_UPLOAD_BYTES + 1 })

    await selectFile(wrapper, big)

    expect(wrapper.text()).toContain('文件超过单文件大小上限')
    expect(mocks.importAcademicRules).not.toHaveBeenCalled()
  })

  it('sends the optional display name and calls the academic endpoint', async () => {
    mocks.importAcademicRecords.mockResolvedValue(importResult())
    const wrapper = await mountDialog({ kind: 'records' })
    const file = new File(['a'], '记录.xlsx')

    await selectFile(wrapper, file)
    await wrapper.find('.ep-academic-import__name').setValue('2026 秋季记录')
    await buttonByText(wrapper, '开始导入')!.trigger('click')
    await flushPromises()

    expect(mocks.importAcademicRecords).toHaveBeenCalledTimes(1)
    expect(mocks.importAcademicRecords.mock.calls[0][0]).toBe(file)
    expect(mocks.importAcademicRecords.mock.calls[0][1]).toBe('2026 秋季记录')
    expect(mocks.uploadDocument).not.toHaveBeenCalled()
  })

  it('omits a blank display name', async () => {
    mocks.importAcademicRules.mockResolvedValue(importResult({ id: 'ru-1' }))
    const wrapper = await mountDialog({ kind: 'rules' })

    await selectFile(wrapper, new File(['a'], '培养方案.pdf'))
    await wrapper.find('.ep-academic-import__name').setValue('   ')
    await buttonByText(wrapper, '开始导入')!.trigger('click')
    await flushPromises()

    expect(mocks.importAcademicRules.mock.calls[0][1]).toBeUndefined()
    expect(mocks.uploadDocument).not.toHaveBeenCalled()
  })

  it('blocks a second submission while the import is in flight', async () => {
    let release: ((value: ImportResult) => void) | null = null
    mocks.importAcademicRecords.mockImplementation(
      () => new Promise<ImportResult>((resolve) => (release = resolve)),
    )
    const wrapper = await mountDialog({ kind: 'records' })

    await selectFile(wrapper, new File(['a'], '记录.xlsx'))
    const submit = buttonByText(wrapper, '开始导入')!
    await submit.trigger('click')
    await flushPromises()

    expect(submit.attributes('disabled')).toBeDefined()
    await submit.trigger('click')
    await flushPromises()

    expect(mocks.importAcademicRecords).toHaveBeenCalledTimes(1)

    release!(importResult())
    await flushPromises()
  })

  it('shows the real machine code and request id when the server rejects', async () => {
    mocks.importAcademicRecords.mockRejectedValue(
      new ApiError('仅支持 XLSX 课程记录', {
        kind: 'http',
        status: 400,
        code: 'ACADEMIC_FILE_TYPE_UNSUPPORTED',
        requestId: 'req-import-1',
      }),
    )
    const wrapper = await mountDialog({ kind: 'records' })

    await selectFile(wrapper, new File(['a'], '记录.xlsx'))
    await buttonByText(wrapper, '开始导入')!.trigger('click')
    await flushPromises()

    const alert = wrapper.find('.ep-error-alert')
    expect(alert.text()).toContain('ACADEMIC_FILE_TYPE_UNSUPPORTED')
    expect(alert.text()).toContain('请求编号：req-import-1')
    expect(wrapper.emitted('imported')).toBeUndefined()
  })

  it('surfaces the real warnings returned by the server', async () => {
    mocks.importAcademicRules.mockResolvedValue(
      importResult({ id: 'ru-1', warnings: ['DUPLICATE_CATALOG_ENTRY'] }),
    )
    const wrapper = await mountDialog({ kind: 'rules' })

    await selectFile(wrapper, new File(['a'], '培养方案.pdf'))
    await buttonByText(wrapper, '开始导入')!.trigger('click')
    await flushPromises()

    expect(wrapper.find('.ep-academic-import__warnings').text()).toContain('DUPLICATE_CATALOG_ENTRY')
    expect(wrapper.emitted('imported')?.[0]?.[0]).toMatchObject({ id: 'ru-1' })
  })

  it('closes only after a successful import', async () => {
    mocks.importAcademicRecords.mockResolvedValue(importResult())
    const wrapper = await mountDialog({ kind: 'records' })

    await selectFile(wrapper, new File(['a'], '记录.xlsx'))
    await buttonByText(wrapper, '开始导入')!.trigger('click')
    await flushPromises()

    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual([false])
    expect(mocks.fetchAcademicOptions).toHaveBeenCalledTimes(1)
  })

  it('blocks the import when planning is unavailable', async () => {
    const wrapper = await mountDialog({
      kind: 'rules',
      disabled: true,
      disabledReason: '规划能力不可用，已禁用导入',
    })

    expect(wrapper.text()).toContain('规划能力不可用')
    expect(buttonByText(wrapper, '开始导入')!.attributes('disabled')).toBeDefined()
    expect(mocks.importAcademicRules).not.toHaveBeenCalled()
  })
})
