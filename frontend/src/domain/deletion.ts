/**
 * 删除确认文案（UI_SPEC 4.3）：纯函数，来源类型不同则文案不同。
 */

import type { SourceType } from '@/api/documents'

export const DELETE_CONFIRM_TITLE = '确认删除该文档？'
export const DELETE_CONFIRM_BUTTON = '删除文档'

const MESSAGES: Record<SourceType, string> = {
  demo: '仅移除运行时索引，可通过加载演示资料恢复',
  upload: '将删除上传文件及相关索引，此操作不可撤销',
}

export function deleteConfirmMessage(sourceType: SourceType): string {
  return MESSAGES[sourceType] ?? MESSAGES.upload
}
