<script setup lang="ts">
/**
 * 异步状态容器（UI_SPEC 4.2）。
 *
 * 显式区分 loading / error / disabled / empty / partial / success：
 * - loading 使用与真实布局同形的 skeleton，而不是全屏旋转图标；
 * - partial 使用橙色提示并保留失败数量与“继续处理”操作；
 * - disabled 降低对比度并说明原因。
 */
import { computed } from 'vue'

import ErrorAlert from './ErrorAlert.vue'
import type { ApiError } from '@/api/client'

const props = withDefaults(
  defineProps<{
    loading?: boolean
    error?: ApiError | null
    empty?: boolean
    disabled?: boolean
    partial?: boolean
    loadingText?: string
    errorTitle?: string
    emptyTitle?: string
    emptyDescription?: string
    disabledReason?: string
    skeletonRows?: number
  }>(),
  {
    loading: false,
    error: null,
    empty: false,
    disabled: false,
    partial: false,
    loadingText: '正在加载',
    errorTitle: '加载失败',
    emptyTitle: '暂无数据',
    emptyDescription: '',
    disabledReason: '',
    skeletonRows: 3,
  },
)

const rows = computed(() => Array.from({ length: Math.max(1, props.skeletonRows) }, (_, i) => i))
</script>

<template>
  <div class="ep-async">
    <div v-if="loading" class="ep-async__loading" role="status" aria-live="polite">
      <div v-for="row in rows" :key="row" class="ep-async__skeleton-row" aria-hidden="true" />
      <span class="ep-async__loading-text">{{ loadingText }}</span>
    </div>

    <div v-else-if="disabled" class="ep-async__disabled">
      <slot name="disabled">
        <p class="ep-async__title">当前不可用</p>
        <p class="ep-async__hint">{{ disabledReason }}</p>
      </slot>
    </div>

    <ErrorAlert v-else-if="error" :error="error" :title="errorTitle">
      <slot name="error-actions" />
    </ErrorAlert>

    <ElAlert
      v-else-if="partial"
      class="ep-async__partial"
      type="warning"
      :closable="false"
      show-icon
      :title="emptyTitle"
    >
      <p v-if="emptyDescription" class="ep-async__hint">{{ emptyDescription }}</p>
      <slot name="partial-actions" />
    </ElAlert>

    <div v-else-if="empty" class="ep-async__empty">
      <slot name="empty">
        <p class="ep-async__title">{{ emptyTitle }}</p>
        <p v-if="emptyDescription" class="ep-async__hint">{{ emptyDescription }}</p>
      </slot>
      <div class="ep-async__empty-actions">
        <slot name="empty-actions" />
      </div>
    </div>

    <slot v-else />
  </div>
</template>

<style scoped>
.ep-async__loading {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-2);
}

.ep-async__skeleton-row {
  height: 20px;
  border-radius: var(--ep-radius-card);
  background: linear-gradient(90deg, var(--ep-color-surface-subtle), var(--ep-color-border-light));
}

.ep-async__loading-text {
  font-size: 13px;
  line-height: 20px;
  color: var(--ep-color-text-muted);
}

.ep-async__disabled {
  padding: var(--ep-space-5);
  border: 1px dashed var(--ep-color-border);
  border-radius: var(--ep-radius-card);
  background-color: var(--ep-color-surface-subtle);
  color: var(--ep-color-text-muted);
}

.ep-async__empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--ep-space-2);
  padding: var(--ep-space-8) var(--ep-space-4);
  text-align: center;
  color: var(--ep-color-text-muted);
}

.ep-async__empty-actions:not(:empty) {
  margin-top: var(--ep-space-2);
}

.ep-async__title {
  font-size: 16px;
  line-height: 24px;
  font-weight: 600;
  color: var(--ep-color-text);
}

.ep-async__hint {
  font-size: 13px;
  line-height: 20px;
  color: var(--ep-color-text-muted);
}
</style>
