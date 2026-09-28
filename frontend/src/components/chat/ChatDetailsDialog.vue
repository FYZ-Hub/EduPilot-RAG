<script setup lang="ts">
/**
 * 检索详情（UI_SPEC 6.5）：展示本轮**真实**的结论、原因码、请求编号与引用清单。
 *
 * 只展示服务端返回或由协议事件携带的字段，不含 API Key、内部路径或调试对象。
 */
import { computed } from 'vue'

import type { ChatTurnMessage } from '@/stores/chat'

const props = defineProps<{
  modelValue: boolean
  message: ChatTurnMessage | null
}>()

const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})
</script>

<template>
  <ElDialog v-model="visible" title="检索详情" width="520px">
    <div v-if="message" class="ep-details">
      <dl class="ep-details__list">
        <div class="ep-details__row">
          <dt>结论</dt>
          <dd>{{ message.outcome ?? '未完成' }}</dd>
        </div>
        <div class="ep-details__row">
          <dt>原因码</dt>
          <dd class="ep-details__reason">{{ message.reasonCode ?? '无' }}</dd>
        </div>
        <div class="ep-details__row">
          <dt>请求编号</dt>
          <dd>{{ message.requestId ?? '未获得服务端请求编号' }}</dd>
        </div>
        <div class="ep-details__row">
          <dt>引用数量</dt>
          <dd>{{ message.citations.length }}</dd>
        </div>
        <div v-if="message.retryable !== null" class="ep-details__row">
          <dt>服务端可重试</dt>
          <dd>{{ message.retryable ? '是' : '否' }}</dd>
        </div>
        <div v-if="message.errorCode !== null" class="ep-details__row">
          <dt>错误码</dt>
          <dd>{{ message.errorCode }}</dd>
        </div>
      </dl>

      <ul v-if="message.citations.length > 0" class="ep-details__citations">
        <li v-for="citation in message.citations" :key="citation.citation_index">
          [{{ citation.citation_index }}] {{ citation.file_name }} ·
          {{ citation.document_version ?? '未标注版本' }}
        </li>
      </ul>
      <p v-else class="ep-details__empty">本轮没有引用来源。</p>
    </div>
  </ElDialog>
</template>

<style scoped>
.ep-details {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-4);
}

.ep-details__list {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-2);
  margin: 0;
}

.ep-details__row {
  display: flex;
  gap: var(--ep-space-3);
  font-size: 13px;
}

.ep-details__row dt {
  min-width: 84px;
  margin: 0;
  color: var(--ep-color-text-muted);
}

.ep-details__row dd {
  margin: 0;
  overflow-wrap: anywhere;
}

.ep-details__citations {
  margin: 0;
  padding-left: var(--ep-space-5);
  font-size: 13px;
  overflow-wrap: anywhere;
}

.ep-details__empty {
  margin: 0;
  color: var(--ep-color-text-muted);
  font-size: 13px;
}
</style>
