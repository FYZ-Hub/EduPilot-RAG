import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ElementPlus from 'element-plus'

import { ApiError } from '@/api/client'
import { MAX_UPLOAD_BYTES, type DocumentUploadResult, type UploadDisposition } from '@/api/documents'

const mocks = vi.hoisted(() => ({ uploadDocument: vi.fn() }))

vi.mock('@/api/documents', async () => {
  const actual = await vi.importActual<typeof import('@/api/documents')>('@/api/documents')
  return { ...actual, uploadDocument: mocks.uploadDocument }
})

import FileUploader from './FileUploader.vue'

function uploadResult(disposition: UploadDisposition): DocumentUploadResult {
  return {
    document_id: 'doc-1',
    status: 'queued',
    status_url: '/api/documents/doc-1/status',
    deduplicated: disposition === 'existing_ready',
    disposition,
  }
}

async function mountUploader(
  overrides: { disabled?: boolean; disabledReason?: string } = {},
): Promise<VueWrapper> {
  const wrapper = mount(FileUploader, {
    props: { modelValue: true, ...overrides },
    global: { plugins: [ElementPlus] },
  })
  // ElDialog 在 onMounted 中才把 rendered 置为 true，需要等待一次刷新
  await flushPromises()
  return wrapper
}

async function addFiles(wrapper: VueWrapper, files: File[]): Promise<void> {
  const input = wrapper.find('input[type="file"]')
  Object.defineProperty(input.element, 'files', { value: files, configurable: true })
  await input.trigger('change')
  await flushPromises()
}

async function clickStart(wrapper: VueWrapper): Promise<void> {
  const button = wrapper.findAll('button').find((node) => node.text().includes('开始上传'))
  expect(button).toBeTruthy()
  await button!.trigger('click')
  await flushPromises()
}

function queueText(wrapper: VueWrapper): string {
  const queue = wrapper.find('.ep-uploader__queue')
  return queue.exists() ? queue.text() : ''
}

function buttonByText(wrapper: VueWrapper, label: string) {
  return wrapper.findAll('button').find((node) => node.text().trim() === label)
}

describe('FileUploader', () => {
  beforeEach(() => {
    mocks.uploadDocument.mockReset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('maps all four upload dispositions to the specified messages', async () => {
    const cases: Array<[UploadDisposition, string]> = [
      ['created', '已受理'],
      ['existing_ready', '文档已存在'],
      ['attached', '已连接现有任务'],
      ['retry_started', '已开始恢复'],
    ]

    for (const [disposition, message] of cases) {
      mocks.uploadDocument.mockResolvedValue(uploadResult(disposition))
      const wrapper = await mountUploader()

      await addFiles(wrapper, [new File(['a'], 'plan.pdf', { type: 'application/pdf' })])
      await clickStart(wrapper)

      expect(queueText(wrapper)).toContain(message)
      wrapper.unmount()
    }
  })

  it('closes the dialog and emits uploaded only when every file succeeds', async () => {
    mocks.uploadDocument.mockResolvedValue(uploadResult('created'))
    const wrapper = await mountUploader()

    await addFiles(wrapper, [new File(['a'], 'plan.pdf')])
    await clickStart(wrapper)

    expect(wrapper.emitted('uploaded')).toHaveLength(1)
    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual([false])
  })

  it('keeps the dialog open on partial failure and allows per-item retry', async () => {
    mocks.uploadDocument.mockImplementation(async (file: File) => {
      if (file.name === 'bad.pdf') {
        throw new ApiError('解析失败', { kind: 'http', status: 500, code: 'DOCUMENT_PARSE_FAILED' })
      }
      return uploadResult('created')
    })
    const wrapper = await mountUploader()

    await addFiles(wrapper, [new File(['a'], 'ok.pdf'), new File(['b'], 'bad.pdf')])
    await clickStart(wrapper)

    expect(wrapper.emitted('uploaded')).toBeUndefined()
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    expect(wrapper.text()).toContain('1 个文件未成功')

    const retry = buttonByText(wrapper, '重试')
    expect(retry).toBeTruthy()
    expect(retry!.attributes('disabled')).toBeUndefined()

    mocks.uploadDocument.mockResolvedValue(uploadResult('created'))
    await retry!.trigger('click')
    await flushPromises()

    expect(wrapper.emitted('uploaded')).toHaveLength(1)
  })

  it('does not auto-retry DOCUMENT_RETRY_NOT_ALLOWED and disables the retry button', async () => {
    mocks.uploadDocument.mockRejectedValue(
      new ApiError('不允许重试', {
        kind: 'http',
        status: 409,
        code: 'DOCUMENT_RETRY_NOT_ALLOWED',
        requestId: 'req-1',
      }),
    )
    const wrapper = await mountUploader()

    await addFiles(wrapper, [new File(['a'], 'plan.pdf')])
    await clickStart(wrapper)

    expect(mocks.uploadDocument).toHaveBeenCalledTimes(1)
    expect(queueText(wrapper)).toContain('不允许重试')

    const retry = buttonByText(wrapper, '重试')
    expect(retry!.attributes('disabled')).toBeDefined()

    await retry!.trigger('click')
    await flushPromises()
    expect(mocks.uploadDocument).toHaveBeenCalledTimes(1)
  })

  it('rejects unsupported extensions client-side without calling the API', async () => {
    const wrapper = await mountUploader()

    await addFiles(wrapper, [new File(['a'], 'notes.txt')])

    expect(mocks.uploadDocument).not.toHaveBeenCalled()
    expect(queueText(wrapper)).toContain('不支持的文件类型')

    const start = buttonByText(wrapper, '开始上传')
    expect(start!.attributes('disabled')).toBeDefined()
  })

  it('rejects files above the 50MB single-file limit client-side', async () => {
    const wrapper = await mountUploader()
    const big = new File(['a'], 'big.pdf')
    Object.defineProperty(big, 'size', { value: MAX_UPLOAD_BYTES + 1 })

    await addFiles(wrapper, [big])

    expect(mocks.uploadDocument).not.toHaveBeenCalled()
    expect(queueText(wrapper)).toContain('文件超过单文件大小上限')
  })

  it('blocks upload submission while disabled and shows the reason', async () => {
    const wrapper = await mountUploader({
      disabled: true,
      disabledReason: '文档能力不可用（documents = unavailable）：上传已禁用。',
    })

    expect(wrapper.text()).toContain('文档能力不可用')
    const start = buttonByText(wrapper, '开始上传')
    expect(start!.attributes('disabled')).toBeDefined()
    expect(mocks.uploadDocument).not.toHaveBeenCalled()
  })

  it('shows the server message, machine code and real request id for a failed item', async () => {
    mocks.uploadDocument.mockRejectedValue(
      new ApiError('解析失败', {
        kind: 'http',
        status: 500,
        code: 'DOCUMENT_PARSE_FAILED',
        requestId: 'req-upload-1',
      }),
    )
    const wrapper = await mountUploader()

    await addFiles(wrapper, [new File(['a'], 'plan.pdf')])
    await clickStart(wrapper)

    const text = queueText(wrapper)
    expect(text).toContain('解析失败')
    expect(text).toContain('DOCUMENT_PARSE_FAILED')
    expect(text).toContain('请求编号：req-upload-1')
  })

  it('keeps the retry guidance while showing the code and request id for DOCUMENT_RETRY_NOT_ALLOWED', async () => {
    mocks.uploadDocument.mockRejectedValue(
      new ApiError('不允许重试', {
        kind: 'http',
        status: 409,
        code: 'DOCUMENT_RETRY_NOT_ALLOWED',
        requestId: 'req-1',
      }),
    )
    const wrapper = await mountUploader()

    await addFiles(wrapper, [new File(['a'], 'plan.pdf')])
    await clickStart(wrapper)

    const text = queueText(wrapper)
    expect(text).toContain('请修正或更换文件')
    expect(text).toContain('DOCUMENT_RETRY_NOT_ALLOWED')
    expect(text).toContain('请求编号：req-1')
    expect(buttonByText(wrapper, '重试')!.attributes('disabled')).toBeDefined()
  })

  it('reports a network failure without fabricating a request id', async () => {
    mocks.uploadDocument.mockRejectedValue(
      new ApiError('无法连接后端服务（网络错误或跨域被阻断）', { kind: 'network' }),
    )
    const wrapper = await mountUploader()

    await addFiles(wrapper, [new File(['a'], 'plan.pdf')])
    await clickStart(wrapper)

    const text = queueText(wrapper)
    expect(text).toContain('无法连接后端服务')
    expect(text).toContain('NETWORK_ERROR')
    expect(text).toContain('未获得服务端请求编号')
    expect(text).not.toContain('请求编号：')
  })

  it('keeps client-side precheck failures local instead of faking a server error', async () => {
    const wrapper = await mountUploader()

    await addFiles(wrapper, [new File(['a'], 'notes.txt')])

    const text = queueText(wrapper)
    expect(text).toContain('不支持的文件类型')
    expect(text).not.toContain('错误码：')
    expect(text).not.toContain('请求编号')
    expect(mocks.uploadDocument).not.toHaveBeenCalled()
  })
})
