import { describe, expect, it } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import ElementPlus from 'element-plus'

import FilePickField from './FilePickField.vue'

function mountField(props: Record<string, unknown> = {}): VueWrapper {
  return mount(FilePickField, {
    props: {
      accept: ['pdf'],
      hint: '仅支持 PDF，单个文件不超过 50MB',
      hintId: 'ep-test-hint',
      ...props,
    },
    global: { plugins: [ElementPlus] },
  })
}

function dropZone(wrapper: VueWrapper) {
  return wrapper.find('.ep-file-field__drop')
}

async function dropFiles(wrapper: VueWrapper, files: File[]): Promise<void> {
  await dropZone(wrapper).trigger('drop', { dataTransfer: { files } })
}

async function selectFiles(wrapper: VueWrapper, files: File[]): Promise<void> {
  const input = wrapper.find('input[type="file"]')
  Object.defineProperty(input.element, 'files', { value: files, configurable: true })
  await input.trigger('change')
}

describe('FilePickField', () => {
  it('emits the dropped files when enabled', async () => {
    const wrapper = mountField()
    const file = new File(['a'], 'plan.pdf')

    await dropFiles(wrapper, [file])

    expect(wrapper.emitted('files')).toEqual([[[file]]])
  })

  it('emits the selected files when enabled', async () => {
    const wrapper = mountField()
    const file = new File(['a'], 'plan.pdf')

    await selectFiles(wrapper, [file])

    expect(wrapper.emitted('files')).toEqual([[[file]]])
  })

  it('ignores a drop while disabled', async () => {
    const wrapper = mountField({ disabled: true })

    await dropFiles(wrapper, [new File(['a'], 'plan.pdf')])

    expect(wrapper.emitted('files')).toBeUndefined()
  })

  it('ignores an input change while disabled', async () => {
    const wrapper = mountField({ disabled: true })

    await selectFiles(wrapper, [new File(['a'], 'plan.pdf')])

    expect(wrapper.emitted('files')).toBeUndefined()
  })

  it('disables the hidden input and the pick button while disabled', () => {
    const wrapper = mountField({ disabled: true })

    expect(wrapper.find('input[type="file"]').attributes('disabled')).toBeDefined()
    const pick = wrapper.findAll('button').find((node) => node.text().includes('选择文件'))
    expect(pick!.attributes('disabled')).toBeDefined()
  })

  it('keeps the hint association for assistive technology', () => {
    const wrapper = mountField()

    const input = wrapper.find('input[type="file"]')
    expect(input.attributes('aria-describedby')).toBe('ep-test-hint')
    expect(input.attributes('accept')).toBe('.pdf')
    expect(wrapper.find('#ep-test-hint').text()).toContain('仅支持 PDF')
  })
})
