import { describe, expect, it } from 'vitest'

import { DELETE_CONFIRM_BUTTON, DELETE_CONFIRM_TITLE, deleteConfirmMessage } from './deletion'

describe('delete confirmation copy', () => {
  it('uses a distinct, explicit confirm button label', () => {
    expect(DELETE_CONFIRM_TITLE).toBe('确认删除该文档？')
    expect(DELETE_CONFIRM_BUTTON).toBe('删除文档')
    expect(DELETE_CONFIRM_BUTTON).not.toBe('确定')
  })

  it('warns that demo deletion only removes runtime indexes', () => {
    expect(deleteConfirmMessage('demo')).toBe('仅移除运行时索引，可通过加载演示资料恢复')
  })

  it('warns that upload deletion is irreversible', () => {
    expect(deleteConfirmMessage('upload')).toBe('将删除上传文件及相关索引，此操作不可撤销')
  })

  it('never returns the same copy for demo and upload sources', () => {
    expect(deleteConfirmMessage('demo')).not.toBe(deleteConfirmMessage('upload'))
  })
})
