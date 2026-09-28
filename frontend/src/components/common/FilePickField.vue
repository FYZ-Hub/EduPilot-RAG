<script setup lang="ts">
/**
 * 纯展示的文件选择区域（UI_SPEC 5.4）：
 *
 * - 拖拽或点击选择文件，只负责把原始 ``File`` 列表交回调用方；
 * - **不做**扩展名 / 大小 / MIME 判定，也不发起任何请求；
 * - 普通知识库上传与学业规划导入共用同一处实现，避免复制两套拖拽交互。
 */
import { computed, ref } from 'vue'
import { UploadFilled } from '@element-plus/icons-vue'

const props = withDefaults(
  defineProps<{
    accept: readonly string[]
    multiple?: boolean
    disabled?: boolean
    title?: string
    hint: string
    hintId: string
    ariaLabel?: string
    buttonLabel?: string
  }>(),
  {
    multiple: false,
    disabled: false,
    title: '拖拽文件到此处，或点击选择文件',
    ariaLabel: '选择文件',
    buttonLabel: '选择文件',
  },
)

const emit = defineEmits<{ files: [files: File[]] }>()

const inputRef = ref<HTMLInputElement | null>(null)

const acceptAttribute = computed(() => props.accept.map((item) => `.${item}`).join(','))

function pick(): void {
  inputRef.value?.click()
}

function onSelect(event: Event): void {
  const target = event.target as HTMLInputElement
  if (target.files && target.files.length > 0) {
    emit('files', Array.from(target.files))
  }
  // 允许重复选择同一个文件
  target.value = ''
}

function onDrop(event: DragEvent): void {
  const files = event.dataTransfer?.files
  if (files && files.length > 0) {
    emit('files', Array.from(files))
  }
}
</script>

<template>
  <div class="ep-file-field" :class="{ 'is-disabled': disabled }">
    <div class="ep-file-field__drop" @dragover.prevent @drop.prevent="onDrop">
      <ElIcon :size="24"><UploadFilled /></ElIcon>
      <p class="ep-file-field__title">{{ title }}</p>
      <p :id="hintId" class="ep-file-field__hint">{{ hint }}</p>
      <ElButton :disabled="disabled" @click="pick">{{ buttonLabel }}</ElButton>
      <input
        ref="inputRef"
        class="ep-file-field__input"
        type="file"
        :multiple="multiple"
        :disabled="disabled"
        :accept="acceptAttribute"
        :aria-label="ariaLabel"
        :aria-describedby="hintId"
        @change="onSelect"
      />
    </div>
  </div>
</template>

<style scoped>
.ep-file-field__drop {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--ep-space-2);
  padding: var(--ep-space-6) var(--ep-space-4);
  border: 1px dashed var(--ep-color-border);
  border-radius: var(--ep-radius-card);
  background-color: var(--ep-color-surface-subtle);
  text-align: center;
  color: var(--ep-color-text-secondary);
}

.ep-file-field.is-disabled .ep-file-field__drop {
  opacity: 0.6;
}

.ep-file-field__title {
  font-size: 14px;
  line-height: 22px;
  font-weight: 500;
  color: var(--ep-color-text);
}

.ep-file-field__hint {
  font-size: 12px;
  line-height: 18px;
  color: var(--ep-color-text-muted);
}

.ep-file-field__input {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
</style>
