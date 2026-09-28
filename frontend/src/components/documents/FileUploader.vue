<script setup lang="ts">
/**
 * 文件上传对话框（UI_SPEC 5.4）。
 *
 * - 560px 对话框，支持拖拽或选择 PDF / DOCX / XLSX；
 * - 客户端先检查扩展名与单文件大小，服务端仍执行最终校验；
 * - 每个文件**独立**调用 ``POST /api/documents``，并依据 ``disposition`` 反馈；
 * - 收到 ``DOCUMENT_RETRY_NOT_ALLOWED`` 时要求用户更换文件，不再自动重复提交；
 * - 全部成功才关闭；部分失败保留对话框并允许逐项重试；
 * - 请求期间按钮 loading 并阻止重复提交；不在浏览器日志中输出文件内容。
 */
import { computed, ref, watch } from 'vue'
import { UploadFilled } from '@element-plus/icons-vue'

import { toApiError, type ApiError } from '@/api/client'
import {
  ALLOWED_UPLOAD_EXTENSIONS,
  MAX_UPLOAD_BYTES,
  uploadDocument,
  type DocumentUploadResult,
  type UploadDisposition,
} from '@/api/documents'
import { fileTypeLabel } from '@/domain/documents'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    disabled?: boolean
    disabledReason?: string
  }>(),
  { disabled: false, disabledReason: '' },
)

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  uploaded: []
}>()

type ItemStatus = 'pending' | 'uploading' | 'success' | 'error'

interface QueueItem {
  id: string
  file: File
  status: ItemStatus
  message: string | null
  disposition: UploadDisposition | null
  error: ApiError | null
  retryable: boolean
}

const DISPOSITION_MESSAGES: Record<UploadDisposition, string> = {
  created: '已受理',
  existing_ready: '文档已存在',
  attached: '已连接现有任务',
  retry_started: '已开始恢复',
}

const queue = ref<QueueItem[]>([])
const uploading = ref(false)
const inputRef = ref<HTMLInputElement | null>(null)
let counter = 0

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const hintText = computed(
  () => `支持 PDF / DOCX / XLSX，单个文件不超过 ${Math.round(MAX_UPLOAD_BYTES / 1024 / 1024)}MB`,
)

const failedItems = computed(() => queue.value.filter((item) => item.status === 'error'))
const pendingRetry = computed(() =>
  queue.value.filter((item) => item.status === 'pending' || (item.status === 'error' && item.retryable)),
)
const allSucceeded = computed(
  () => queue.value.length > 0 && queue.value.every((item) => item.status === 'success'),
)

watch(
  () => props.modelValue,
  (open) => {
    if (!open) {
      uploading.value = false
      queue.value = []
    }
  },
)

function extensionOf(name: string): string {
  const index = name.lastIndexOf('.')
  return index >= 0 ? name.slice(index + 1).toLowerCase() : ''
}

function typeLabelOf(name: string): string {
  const extension = extensionOf(name)
  return (ALLOWED_UPLOAD_EXTENSIONS as readonly string[]).includes(extension)
    ? fileTypeLabel(extension as 'pdf' | 'docx' | 'xlsx')
    : '未知类型'
}

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) {
    return `${(bytes / 1024 / 1024).toFixed(1)}MB`
  }
  return `${Math.max(1, Math.round(bytes / 1024))}KB`
}

function addFiles(files: FileList | File[]): void {
  for (const file of Array.from(files)) {
    const extension = extensionOf(file.name)
    let status: ItemStatus = 'pending'
    let message: string | null = null
    let retryable = true

    if (!(ALLOWED_UPLOAD_EXTENSIONS as readonly string[]).includes(extension)) {
      status = 'error'
      message = `不支持的文件类型：仅允许 ${ALLOWED_UPLOAD_EXTENSIONS.map((item) =>
        item.toUpperCase(),
      ).join(' / ')}`
      retryable = false
    } else if (file.size > MAX_UPLOAD_BYTES) {
      status = 'error'
      message = '文件超过单文件大小上限'
      retryable = false
    }

    counter += 1
    queue.value.push({
      id: `upload-${counter}`,
      file,
      status,
      message,
      disposition: null,
      error: null,
      retryable,
    })
  }
}

function onSelect(event: Event): void {
  const target = event.target as HTMLInputElement
  if (target.files) {
    addFiles(target.files)
  }
  target.value = ''
}

function onDrop(event: DragEvent): void {
  const files = event.dataTransfer?.files
  if (files) {
    addFiles(files)
  }
}

function pickFiles(): void {
  inputRef.value?.click()
}

function removeItem(id: string): void {
  queue.value = queue.value.filter((item) => item.id !== id)
}

async function uploadItem(item: QueueItem): Promise<void> {
  item.status = 'uploading'
  item.message = null
  item.error = null
  try {
    const result: DocumentUploadResult = await uploadDocument(item.file)
    item.status = 'success'
    item.disposition = result.disposition
    item.message = DISPOSITION_MESSAGES[result.disposition]
  } catch (caught) {
    const apiError = toApiError(caught)
    item.status = 'error'
    item.error = apiError
    item.message = apiError.message
    // 不可重试错误要求用户更换文件，绝不自动重复提交
    item.retryable = !(apiError.code === 'DOCUMENT_RETRY_NOT_ALLOWED')
    if (apiError.code === 'DOCUMENT_RETRY_NOT_ALLOWED') {
      item.message = '该文件当前不允许重试，请修正或更换文件后重新提交'
    }
  }
}

async function startUpload(): Promise<void> {
  if (uploading.value || props.disabled) {
    return
  }
  uploading.value = true
  try {
    for (const item of [...pendingRetry.value]) {
      await uploadItem(item)
    }
  } finally {
    uploading.value = false
  }
  if (allSucceeded.value) {
    emit('uploaded')
    visible.value = false
  }
}

async function retryItem(item: QueueItem): Promise<void> {
  if (uploading.value || !item.retryable) {
    return
  }
  uploading.value = true
  try {
    await uploadItem(item)
  } finally {
    uploading.value = false
  }
  if (allSucceeded.value) {
    emit('uploaded')
    visible.value = false
  }
}
</script>

<template>
  <ElDialog v-model="visible" title="上传文档" width="560px" :close-on-click-modal="false">
    <div v-if="disabled" class="ep-uploader__disabled">
      <p>{{ disabledReason }}</p>
    </div>

    <template v-else>
      <div
        class="ep-uploader__drop"
        @dragover.prevent
        @drop.prevent="onDrop"
      >
        <ElIcon :size="24"><UploadFilled /></ElIcon>
        <p class="ep-uploader__drop-title">拖拽文件到此处，或点击选择文件</p>
        <p id="ep-uploader-hint" class="ep-uploader__hint">{{ hintText }}</p>
        <ElButton :disabled="uploading" @click="pickFiles">选择文件</ElButton>
        <input
          ref="inputRef"
          class="ep-uploader__input"
          type="file"
          multiple
          aria-label="选择要上传的文件"
          aria-describedby="ep-uploader-hint"
          :accept="ALLOWED_UPLOAD_EXTENSIONS.map((item) => `.${item}`).join(',')"
          @change="onSelect"
        />
      </div>

      <ul v-if="queue.length" class="ep-uploader__queue">
        <li v-for="item in queue" :key="item.id" class="ep-uploader__item">
          <div class="ep-uploader__item-main">
            <span class="ep-uploader__item-name">{{ item.file.name }}</span>
            <span class="ep-uploader__item-meta">
              {{ typeLabelOf(item.file.name) }} · {{ formatSize(item.file.size) }}
            </span>
          </div>
          <div class="ep-uploader__item-state">
            <ElTag
              size="small"
              effect="light"
              disable-transitions
              :type="
                item.status === 'success'
                  ? 'success'
                  : item.status === 'error'
                    ? 'danger'
                    : item.status === 'uploading'
                      ? 'primary'
                      : 'info'
              "
            >
              {{
                item.status === 'success'
                  ? '成功'
                  : item.status === 'error'
                    ? '失败'
                    : item.status === 'uploading'
                      ? '上传中'
                      : '等待'
              }}
            </ElTag>
            <span v-if="item.message" class="ep-uploader__item-message">{{ item.message }}</span>
            <ElButton
              v-if="item.status === 'error'"
              size="small"
              text
              type="primary"
              :loading="uploading"
              :disabled="!item.retryable || uploading"
              @click="retryItem(item)"
            >
              重试
            </ElButton>
            <ElButton
              size="small"
              text
              aria-label="移除该文件"
              :disabled="uploading"
              @click="removeItem(item.id)"
            >
              移除
            </ElButton>
          </div>
        </li>
      </ul>

      <ElAlert
        v-if="failedItems.length && !uploading"
        class="ep-uploader__partial"
        type="warning"
        :closable="false"
        show-icon
        :title="`${failedItems.length} 个文件未成功`"
        :description="'可逐项重试，或修正后重新提交；成功项已受理，不会重复创建。'"
      />
    </template>

    <template #footer>
      <ElButton :disabled="uploading" @click="visible = false">关闭</ElButton>
      <ElButton
        type="primary"
        :loading="uploading"
        :disabled="disabled || uploading || !pendingRetry.length"
        @click="startUpload"
      >
        {{ uploading ? '正在上传' : '开始上传' }}
      </ElButton>
    </template>
  </ElDialog>
</template>

<style scoped>
.ep-uploader__drop {
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

.ep-uploader__drop-title {
  font-size: 14px;
  line-height: 22px;
  font-weight: 500;
  color: var(--ep-color-text);
}

.ep-uploader__hint {
  font-size: 12px;
  line-height: 18px;
  color: var(--ep-color-text-muted);
}

.ep-uploader__input {
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

.ep-uploader__queue {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-2);
  margin: var(--ep-space-4) 0 0;
  padding: 0;
  list-style: none;
  max-height: 260px;
  overflow-y: auto;
}

.ep-uploader__item {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--ep-space-3);
  padding: var(--ep-space-2) 0;
  border-bottom: 1px solid var(--ep-color-border-light);
}

.ep-uploader__item-main {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.ep-uploader__item-name {
  font-size: 13px;
  line-height: 20px;
  overflow-wrap: anywhere;
}

.ep-uploader__item-meta,
.ep-uploader__item-message {
  font-size: 12px;
  line-height: 18px;
  color: var(--ep-color-text-muted);
  overflow-wrap: anywhere;
}

.ep-uploader__item-state {
  display: flex;
  align-items: center;
  gap: var(--ep-space-2);
  flex-shrink: 0;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.ep-uploader__partial {
  margin-top: var(--ep-space-3);
}

.ep-uploader__disabled {
  padding: var(--ep-space-4);
  color: var(--ep-color-text-muted);
}

@media (max-width: 767px) {
  .ep-uploader__item {
    flex-direction: column;
  }
}
</style>
