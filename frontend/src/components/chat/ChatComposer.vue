<script setup lang="ts">
/**
 * 问答输入区（UI_SPEC 6.5）：多行输入、真实字符计数、Enter 发送 / Shift+Enter 换行、
 * 请求期间按钮变为「停止」。字符上限与 Chat Store 使用同一个常量来源。
 */
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    modelValue: string
    disabled?: boolean
    disabledReason?: string
    streaming?: boolean
    maxChars?: number
  }>(),
  { disabled: false, disabledReason: '', streaming: false, maxChars: 4000 },
)

const emit = defineEmits<{
  'update:modelValue': [value: string]
  submit: []
  stop: []
}>()

const canSubmit = computed(
  () => !props.disabled && !props.streaming && props.modelValue.trim().length > 0,
)

const hint = computed(() =>
  props.disabled && props.disabledReason
    ? props.disabledReason
    : 'Enter 发送，Shift+Enter 换行',
)

function onInput(event: Event): void {
  emit('update:modelValue', (event.target as HTMLTextAreaElement).value)
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Enter' || event.shiftKey) {
    return
  }
  event.preventDefault()
  if (canSubmit.value) {
    emit('submit')
  }
}

function onAction(): void {
  if (props.streaming) {
    emit('stop')
    return
  }
  if (canSubmit.value) {
    emit('submit')
  }
}
</script>

<template>
  <div class="ep-composer">
    <label class="ep-composer__label" for="ep-chat-input">输入问题</label>
    <textarea
      id="ep-chat-input"
      class="ep-composer__input"
      rows="3"
      :value="modelValue"
      :maxlength="maxChars"
      :disabled="disabled"
      placeholder="基于知识库资料提问，回答会附带原文引用"
      @input="onInput"
      @keydown="onKeydown"
    ></textarea>

    <div class="ep-composer__footer">
      <p class="ep-composer__hint">{{ hint }}</p>
      <span class="ep-composer__counter">{{ modelValue.length }} / {{ maxChars }}</span>
      <ElButton
        class="ep-composer__action"
        type="primary"
        :disabled="disabled || (!streaming && !canSubmit)"
        @click="onAction"
      >
        {{ streaming ? '停止' : '发送' }}
      </ElButton>
    </div>
  </div>
</template>

<style scoped>
.ep-composer {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-2);
  padding: var(--ep-space-4);
  border: 1px solid var(--ep-color-border);
  border-radius: var(--ep-radius-card);
  background: #fff;
}

.ep-composer__label {
  font-size: 13px;
  font-weight: 600;
  color: var(--ep-color-text-secondary);
}

.ep-composer__input {
  width: 100%;
  min-height: 72px;
  padding: var(--ep-space-3);
  border: 1px solid var(--ep-color-border);
  border-radius: 8px;
  color: var(--ep-color-text);
  font-family: inherit;
  font-size: 15px;
  line-height: 1.6;
  resize: vertical;
}

.ep-composer__input:disabled {
  background: var(--ep-color-surface-muted, #f8fafc);
  cursor: not-allowed;
}

.ep-composer__footer {
  display: flex;
  flex-wrap: wrap;
  gap: var(--ep-space-3);
  align-items: center;
  justify-content: flex-end;
}

.ep-composer__hint {
  margin: 0;
  margin-right: auto;
  color: var(--ep-color-text-muted);
  font-size: 12px;
  overflow-wrap: anywhere;
}

.ep-composer__counter {
  color: var(--ep-color-text-muted);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
</style>
