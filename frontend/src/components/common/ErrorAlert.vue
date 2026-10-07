<script setup lang="ts">
/**
 * 统一错误提示（UI_SPEC 4.2 / 4.3 / 10）。
 *
 * - 服务端错误展示真实 ``request_id``；
 * - 网络失败、请求取消或 CORS 阻断明确显示“未获得服务端请求编号”，前端绝不伪造；
 * - 所有文本按纯文本渲染，不使用 ``v-html``，也不展示堆栈。
 */
import { computed } from 'vue'

import { toApiError, type ApiError } from '@/api/client'
import { apiErrorCodeLabel } from '@/domain/apiError'

const props = withDefaults(
  defineProps<{
    error: ApiError | null
    title?: string
  }>(),
  { title: '操作失败' },
)

const apiError = computed(() => (props.error ? toApiError(props.error) : null))

const codeText = computed(() => apiErrorCodeLabel(apiError.value))
</script>

<template>
  <ElAlert
    v-if="apiError"
    class="ep-error-alert"
    type="error"
    :closable="false"
    show-icon
    :title="title"
  >
    <p class="ep-error-alert__message">{{ apiError.message }}</p>
    <p v-if="codeText" class="ep-error-alert__meta">错误码：{{ codeText }}</p>
    <p class="ep-error-alert__meta">{{ apiError.requestIdLabel }}</p>
    <div class="ep-error-alert__actions">
      <slot />
    </div>
  </ElAlert>
</template>

<style scoped>
.ep-error-alert__message {
  overflow-wrap: anywhere;
}

.ep-error-alert__meta {
  margin-top: var(--ep-space-1);
  font-size: 12px;
  line-height: 18px;
  color: var(--ep-color-text-secondary);
  overflow-wrap: anywhere;
}

.ep-error-alert__actions:not(:empty) {
  margin-top: var(--ep-space-2);
}
</style>
