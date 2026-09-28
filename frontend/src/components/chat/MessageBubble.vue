<script setup lang="ts">
/**
 * 单条消息（UI_SPEC 6.5）：用户消息右对齐、助手消息左对齐并标注「基于知识库」，
 * 终止状态各自使用中性 / 警告 / 错误卡，不把拒答、停止或冲突渲染成红色错误。
 */
import { computed } from 'vue'

import { ApiError } from '@/api/client'
import type { ChatTurnMessage } from '@/stores/chat'
import ErrorAlert from '@/components/common/ErrorAlert.vue'

import MessageContent from './MessageContent.vue'

const props = withDefaults(
  defineProps<{
    message: ChatTurnMessage
    streaming?: boolean
    activeCitationIndex?: number | null
    canRetry?: boolean
  }>(),
  { streaming: false, activeCitationIndex: null, canRetry: false },
)

const emit = defineEmits<{
  'select-citation': [index: number]
  copy: []
  retry: []
  details: []
  'adjust-scope': []
}>()

const isUser = computed(() => props.message.role === 'user')
const hasContent = computed(() => props.message.content.trim().length > 0)
const isFailed = computed(
  () => props.message.errorCode !== null || props.message.interrupted || props.message.stopped,
)
const showRetry = computed(() => props.canRetry && isFailed.value)
const showActions = computed(() => !isUser.value && (hasContent.value || showRetry.value))

/** 用消息里保存的真实错误信息重建 ApiError，以便复用统一错误展示。 */
const messageError = computed(() =>
  props.message.errorCode === null
    ? null
    : new ApiError(props.message.errorMessage ?? props.message.errorCode, {
        kind: 'http',
        code: props.message.errorCode,
        requestId: props.message.requestId ?? undefined,
      }),
)
</script>

<template>
  <article class="ep-msg" :class="isUser ? 'ep-msg--user' : 'ep-msg--assistant'">
    <div class="ep-msg__bubble">
      <p v-if="!isUser" class="ep-msg__badge">基于知识库</p>

      <MessageContent
        :content="message.content"
        :citations="message.citations"
        :active-index="activeCitationIndex"
        @select="emit('select-citation', $event)"
      />

      <span v-if="streaming" class="ep-msg__cursor" aria-hidden="true"></span>
    </div>

    <ElAlert
      v-if="message.outcome === 'refused'"
      class="ep-msg__card ep-msg__card--refused"
      type="info"
      :closable="false"
      show-icon
      title="当前知识库没有足够依据"
    >
      <p class="ep-msg__card-text">资料不足时不会生成结论，也不会编造引用。</p>
      <div class="ep-msg__card-actions">
        <ElButton size="small" @click="emit('adjust-scope')">调整检索范围</ElButton>
        <RouterLink class="ep-msg__card-link" to="/knowledge">前往知识库</RouterLink>
      </div>
    </ElAlert>

    <ElAlert
      v-if="message.outcome === 'conflict'"
      class="ep-msg__card ep-msg__card--conflict"
      type="warning"
      :closable="false"
      show-icon
      title="资料之间存在版本冲突"
    >
      <p class="ep-msg__card-text">以下版本同时保留，需要你自行判断，系统不会替你选择结论。</p>
      <ul class="ep-msg__versions">
        <li v-for="citation in message.citations" :key="citation.citation_index">
          [{{ citation.citation_index }}] {{ citation.file_name }} ·
          版本 {{ citation.document_version ?? '未标注' }} · 生效
          {{ citation.effective_from ?? '未标注' }}
        </li>
      </ul>
    </ElAlert>

    <ErrorAlert
      v-if="message.errorCode !== null"
      class="ep-msg__card--error"
      :error="messageError"
      title="回答中断"
    />

    <ElAlert
      v-if="message.interrupted"
      class="ep-msg__card ep-msg__card--interrupted"
      type="warning"
      :closable="false"
      show-icon
      title="连接中断"
    >
      <p class="ep-msg__card-text">
        回答在完成前中断，内容可能不完整，不会被当作成功结果。
      </p>
    </ElAlert>

    <ElAlert
      v-if="message.stopped"
      class="ep-msg__card ep-msg__card--stopped"
      type="info"
      :closable="false"
      show-icon
      title="已停止生成"
    />

    <div v-if="showActions" class="ep-msg__actions">
      <ElButton
        v-if="hasContent"
        class="ep-msg__action--copy"
        size="small"
        text
        @click="emit('copy')"
      >
        复制回答
      </ElButton>
      <ElButton class="ep-msg__action--details" size="small" text @click="emit('details')">
        检索详情
      </ElButton>
      <ElButton
        v-if="showRetry"
        class="ep-msg__action--retry"
        size="small"
        text
        type="primary"
        @click="emit('retry')"
      >
        重试
      </ElButton>
    </div>
  </article>
</template>

<style scoped>
.ep-msg {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-2);
  min-width: 0;
}

.ep-msg--user {
  align-items: flex-end;
}

.ep-msg--assistant {
  align-items: flex-start;
}

.ep-msg__bubble {
  position: relative;
  max-width: 86%;
  padding: var(--ep-space-4);
  border: 1px solid var(--ep-color-border);
  border-radius: var(--ep-radius-bubble);
  background: #fff;
  min-width: 0;
}

.ep-msg--user .ep-msg__bubble {
  max-width: 72%;
  border-color: var(--ep-color-primary-soft);
  background: var(--ep-color-primary-soft);
}

.ep-msg__badge {
  margin: 0 0 var(--ep-space-2);
  color: var(--ep-color-text-muted);
  font-size: 12px;
}

.ep-msg__cursor {
  display: inline-block;
  width: 7px;
  height: 1em;
  margin-left: 2px;
  background: var(--ep-color-primary);
  vertical-align: text-bottom;
  animation: ep-cursor-blink 1s steps(2, start) infinite;
}

@keyframes ep-cursor-blink {
  to {
    visibility: hidden;
  }
}

.ep-msg__card,
.ep-msg__card--error {
  width: 100%;
  max-width: 86%;
}

.ep-msg__card-text {
  margin: 0 0 var(--ep-space-2);
  font-size: 13px;
}

.ep-msg__card-actions {
  display: flex;
  gap: var(--ep-space-3);
  align-items: center;
  flex-wrap: wrap;
}

.ep-msg__card-link {
  color: var(--ep-color-primary);
  font-size: 13px;
}

.ep-msg__versions {
  margin: 0;
  padding-left: var(--ep-space-5);
  font-size: 13px;
}

.ep-msg__actions {
  display: flex;
  gap: var(--ep-space-2);
  flex-wrap: wrap;
}
</style>
