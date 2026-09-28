<script setup lang="ts">
/**
 * 学业资料导入对话框（UI_SPEC 7.3）：课程记录与培养方案规则**分别**调用
 * ``POST /api/academic/records/import`` 与 ``POST /api/academic/rules/import``。
 *
 * 绝不经过普通知识库上传（``POST /api/documents``）；客户端只做扩展名与大小预检，
 * 服务端仍执行最终校验；成功后由 Academic Store 刷新 options，且不自动选中新项目。
 */
import { computed, ref, watch } from 'vue'

import { toApiError, type ApiError } from '@/api/client'
import { MAX_UPLOAD_BYTES } from '@/api/documents'
import {
  RECORD_IMPORT_EXTENSIONS,
  RULE_IMPORT_EXTENSIONS,
  type ImportResult,
} from '@/api/academic'
import ErrorAlert from '@/components/common/ErrorAlert.vue'
import FilePickField from '@/components/common/FilePickField.vue'
import { useAcademicStore } from '@/stores/academic'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    kind: 'records' | 'rules'
    disabled?: boolean
    disabledReason?: string
  }>(),
  { disabled: false, disabledReason: '' },
)

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  imported: [result: ImportResult]
}>()

const academic = useAcademicStore()

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const extensions = computed<readonly string[]>(() =>
  props.kind === 'records' ? RECORD_IMPORT_EXTENSIONS : RULE_IMPORT_EXTENSIONS,
)
const kindLabel = computed(() => (props.kind === 'records' ? '课程记录' : '培养方案规则'))
const nameFieldId = computed(() => `ep-academic-import-name-${props.kind}`)
const hintId = computed(() => `ep-academic-import-hint-${props.kind}`)

const file = ref<File | null>(null)
const displayName = ref('')
const localError = ref<string | null>(null)
const error = ref<ApiError | null>(null)
const warnings = ref<string[]>([])

const busy = computed(() =>
  props.kind === 'records' ? academic.importingRecords : academic.importingRules,
)
const canSubmit = computed(() => !props.disabled && !busy.value && file.value !== null)

const hintText = computed(
  () =>
    `仅支持 ${extensions.value.map((item) => item.toUpperCase()).join(' / ')}，` +
    `单个文件不超过 ${Math.round(MAX_UPLOAD_BYTES / 1024 / 1024)}MB`,
)

watch(
  () => props.modelValue,
  (open) => {
    if (!open) {
      file.value = null
      displayName.value = ''
      localError.value = null
      error.value = null
      warnings.value = []
    }
  },
)

function extensionOf(name: string): string {
  const index = name.lastIndexOf('.')
  return index >= 0 ? name.slice(index + 1).toLowerCase() : ''
}

function onFiles(files: File[]): void {
  if (busy.value || props.disabled) {
    return
  }
  file.value = null
  localError.value = null
  error.value = null
  warnings.value = []

  const picked = files[0]
  if (!picked) {
    return
  }
  if (!extensions.value.includes(extensionOf(picked.name))) {
    localError.value = `不支持的文件类型：仅支持 ${extensions.value
      .map((item) => item.toUpperCase())
      .join(' / ')}`
    return
  }
  if (picked.size > MAX_UPLOAD_BYTES) {
    localError.value = '文件超过单文件大小上限'
    return
  }
  file.value = picked
}

async function submit(): Promise<void> {
  const picked = file.value
  if (props.disabled || busy.value || !picked) {
    return
  }
  error.value = null
  warnings.value = []

  const name = displayName.value.trim() || undefined
  const result =
    props.kind === 'records'
      ? await academic.importRecords(picked, name)
      : await academic.importRules(picked, name)

  if (!result) {
    const storeError =
      props.kind === 'records' ? academic.importRecordsError : academic.importRulesError
    error.value = storeError ?? toApiError(new Error('导入失败'))
    return
  }

  warnings.value = [...result.warnings]
  // 已提交的同一个文件不得再次提交，避免重复导入
  file.value = null
  emit('imported', result)
  if (warnings.value.length === 0) {
    visible.value = false
  }
}

function onBeforeClose(done: () => void): void {
  if (busy.value) {
    return
  }
  done()
}
</script>

<template>
  <ElDialog
    v-model="visible"
    :title="`导入${kindLabel}`"
    width="560px"
    :close-on-click-modal="false"
    :close-on-press-escape="!busy"
    :show-close="!busy"
    :before-close="onBeforeClose"
  >
    <div v-if="disabled" class="ep-academic-import__disabled">
      <p>{{ disabledReason }}</p>
    </div>

    <template v-else>
      <FilePickField
        :accept="extensions"
        :disabled="busy"
        :hint="hintText"
        :hint-id="hintId"
        :aria-label="`选择要导入的${kindLabel}文件`"
        @files="onFiles"
      />

      <p v-if="file" class="ep-academic-import__file" aria-live="polite">
        已选择：{{ file.name }}
      </p>
      <p v-else class="ep-academic-import__file">尚未选择文件</p>

      <p v-if="localError" class="ep-academic-import__local-error" role="alert">{{ localError }}</p>

      <div class="ep-academic-import__name-field">
        <label class="ep-academic-import__label" :for="nameFieldId">显示名称（可选）</label>
        <p :id="`${nameFieldId}-help`" class="ep-academic-import__help">
          留空时由系统按文件内容命名；请勿填写真实姓名或学号。
        </p>
        <input
          :id="nameFieldId"
          v-model="displayName"
          class="ep-academic-import__name"
          type="text"
          :disabled="busy"
          :aria-describedby="`${nameFieldId}-help`"
        />
      </div>

      <ErrorAlert
        v-if="error"
        class="ep-academic-import__error"
        :error="error"
        title="导入失败"
      />

      <div v-if="warnings.length" class="ep-academic-import__warnings">
        <p class="ep-academic-import__warnings-title">导入完成，服务端返回以下提醒：</p>
        <ul class="ep-academic-import__warnings-list">
          <li v-for="warning in warnings" :key="warning">{{ warning }}</li>
        </ul>
      </div>
    </template>

    <template #footer>
      <ElButton :disabled="busy" @click="visible = false">关闭</ElButton>
      <ElButton
        type="primary"
        :loading="busy"
        :disabled="!canSubmit"
        @click="submit"
      >
        开始导入
      </ElButton>
    </template>
  </ElDialog>
</template>

<style scoped>
.ep-academic-import__disabled {
  padding: var(--ep-space-4);
  color: var(--ep-color-text-muted);
}

.ep-academic-import__file {
  margin: var(--ep-space-3) 0 0;
  font-size: 13px;
  overflow-wrap: anywhere;
}

.ep-academic-import__local-error {
  margin: var(--ep-space-2) 0 0;
  color: var(--ep-color-danger, #dc2626);
  font-size: 13px;
}

.ep-academic-import__name-field {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-1);
  margin-top: var(--ep-space-4);
}

.ep-academic-import__label {
  font-size: 13px;
  font-weight: 600;
}

.ep-academic-import__help {
  margin: 0;
  font-size: 12px;
  color: var(--ep-color-text-muted);
}

.ep-academic-import__name {
  min-height: 40px;
  padding: var(--ep-space-1) var(--ep-space-2);
  border: 1px solid var(--ep-color-border);
  border-radius: 8px;
  font: inherit;
  font-size: 13px;
}

.ep-academic-import__name:focus-visible {
  outline: 2px solid var(--ep-color-primary);
  outline-offset: 1px;
}

.ep-academic-import__error {
  margin-top: var(--ep-space-3);
}

.ep-academic-import__warnings {
  margin-top: var(--ep-space-3);
  padding: var(--ep-space-3);
  border: 1px solid var(--ep-color-warning, #f59e0b);
  border-radius: var(--ep-radius-card);
  background: var(--ep-color-warning-soft, #fffbeb);
  font-size: 13px;
}

.ep-academic-import__warnings-title {
  margin: 0 0 var(--ep-space-1);
  font-weight: 600;
}

.ep-academic-import__warnings-list {
  margin: 0;
  padding-left: var(--ep-space-5);
  overflow-wrap: anywhere;
}
</style>
