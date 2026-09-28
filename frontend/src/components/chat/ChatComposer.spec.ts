import { describe, expect, it } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import ElementPlus from 'element-plus'

import ChatComposer from './ChatComposer.vue'

function mountComposer(overrides: Record<string, unknown> = {}): VueWrapper {
  return mount(ChatComposer, {
    props: {
      modelValue: '',
      disabled: false,
      disabledReason: '',
      streaming: false,
      maxChars: 4000,
      ...overrides,
    },
    global: { plugins: [ElementPlus] },
  })
}

function textarea(wrapper: VueWrapper) {
  return wrapper.find('textarea')
}

describe('ChatComposer input behaviour', () => {
  it('sends on Enter', async () => {
    const wrapper = mountComposer({ modelValue: '问题' })

    await textarea(wrapper).trigger('keydown', { key: 'Enter' })

    expect(wrapper.emitted('submit')).toHaveLength(1)
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  it('does not send on Shift+Enter', async () => {
    const wrapper = mountComposer({ modelValue: '问题' })

    await textarea(wrapper).trigger('keydown', { key: 'Enter', shiftKey: true })

    expect(wrapper.emitted('submit')).toBeUndefined()
  })

  it('emits the typed value and shows a real character counter', async () => {
    const wrapper = mountComposer({ modelValue: '毕业学分' })

    expect(wrapper.find('.ep-composer__counter').text()).toContain('4')
    expect(wrapper.find('.ep-composer__counter').text()).toContain('4000')

    await textarea(wrapper).setValue('毕业学分是多少')
    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual(['毕业学分是多少'])
  })

  it('caps the input at the shared single-message limit', () => {
    const wrapper = mountComposer({ maxChars: 4000 })

    expect(textarea(wrapper).attributes('maxlength')).toBe('4000')
  })

  it('turns the action into stop while streaming and never submits', async () => {
    const wrapper = mountComposer({ modelValue: '问题', streaming: true })

    const action = wrapper.find('.ep-composer__action')
    expect(action.text()).toContain('停止')

    await action.trigger('click')
    expect(wrapper.emitted('stop')).toHaveLength(1)
    expect(wrapper.emitted('submit')).toBeUndefined()
  })

  it('blocks input and explains why while disabled', async () => {
    const wrapper = mountComposer({
      disabled: true,
      disabledReason: '大模型尚未配置',
    })

    expect(textarea(wrapper).attributes('disabled')).toBeDefined()
    expect(wrapper.find('.ep-composer__action').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('大模型尚未配置')

    await textarea(wrapper).trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('submit')).toBeUndefined()
  })

  it('does not submit an empty question', async () => {
    const wrapper = mountComposer({ modelValue: '   ' })

    await textarea(wrapper).trigger('keydown', { key: 'Enter' })

    expect(wrapper.emitted('submit')).toBeUndefined()
  })
})
