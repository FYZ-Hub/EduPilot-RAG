<script setup lang="ts">
/**
 * 删除文档的二次确认（UI_SPEC 4.3）。
 *
 * 使用 ``ElMessageBox`` 二次确认，按钮文字明确为“删除文档”，
 * 并按来源类型给出不同文案：demo 仅移除运行时索引；upload 不可撤销。
 * 取消或关闭弹窗都返回 ``false``，绝不当成确认。
 */
import { ElMessageBox } from 'element-plus'

import type { DocumentListItem } from '@/api/documents'
import { DELETE_CONFIRM_BUTTON, DELETE_CONFIRM_TITLE, deleteConfirmMessage } from '@/domain/deletion'

async function confirm(document: DocumentListItem): Promise<boolean> {
  try {
    await ElMessageBox.confirm(deleteConfirmMessage(document.source_type), DELETE_CONFIRM_TITLE, {
      confirmButtonText: DELETE_CONFIRM_BUTTON,
      cancelButtonText: '取消',
      type: 'warning',
      // 文案与按钮文字均来自规格，禁止使用“确定”
      distinguishCancelAndClose: true,
    })
    return true
  } catch {
    return false
  }
}

defineExpose({ confirm })
</script>

<template>
  <span class="ep-confirm-delete" hidden aria-hidden="true" />
</template>
