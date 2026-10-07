<script setup lang="ts">
/**
 * 来源原文抽屉（UI_SPEC 6.6 / 11）：共享组件，按 ``chunk_id`` 调用
 * ``GET /api/sources/{chunk_id}``，只有用户点击「查看原文」时才请求。
 *
 * - 关闭或切换来源时中止上一个请求，旧响应绝不覆盖较新的来源；
 * - 只渲染后端返回的定位字段与原文，不展示 storage_path、source_key 或内部诊断；
 * - 原文用文本节点 + ``<pre>`` 输出，不解释 HTML、不自动生成链接。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { toApiError, type ApiError } from '@/api/client'
import { fetchSource, type SourceDetail } from '@/api/retrieval'
import { fileTypeLabel } from '@/domain/documents'
import ErrorAlert from '@/components/common/ErrorAlert.vue'

const props = defineProps<{
  modelValue: boolean
  chunkId: string | null
}>()

const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const source = ref<SourceDetail | null>(null)
const loading = ref(false)
const error = ref<ApiError | null>(null)

let controller: AbortController | null = null
let sequence = 0

const rowRange = computed(() => {
  const current = source.value
  if (!current || current.row_start === null) {
    return null
  }
  if (current.row_end === null || current.row_end === current.row_start) {
    return `第 ${current.row_start} 行`
  }
  return `${current.row_start}-${current.row_end}`
})

function abortActive(): void {
  controller?.abort()
  controller = null
}

async function load(): Promise<void> {
  const chunkId = props.chunkId
  if (!props.modelValue || !chunkId) {
    return
  }
  abortActive()
  sequence += 1
  const current = sequence
  const active = new AbortController()
  controller = active
  loading.value = true
  error.value = null
  source.value = null

  try {
    const detail = await fetchSource(chunkId, active.signal)
    if (current !== sequence) {
      return // 已经被更新的来源取代，旧响应直接丢弃
    }
    source.value = detail
  } catch (caught) {
    if (current !== sequence) {
      return
    }
    const apiError = toApiError(caught)
    if (!apiError.isAborted) {
      error.value = apiError
    }
  } finally {
    if (current === sequence) {
      loading.value = false
    }
    if (controller === active) {
      controller = null
    }
  }
}

/** 供父组件在卸载时中止未完成的请求。 */
function cancel(): void {
  sequence += 1
  abortActive()
  loading.value = false
}

defineExpose({ cancel })

watch(
  () => [props.modelValue, props.chunkId] as const,
  ([open]) => {
    if (open && props.chunkId) {
      void load()
      return
    }
    cancel()
    source.value = null
    error.value = null
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  cancel()
})
</script>

<template>
  <ElDrawer v-model="visible" title="来源原文" size="520px">
    <div class="ep-source">
      <p v-if="loading" class="ep-source__loading">正在读取来源原文…</p>

      <ErrorAlert v-else-if="error" :error="error" title="无法读取来源原文">
        <ElButton class="ep-source__retry" size="small" @click="load">重试</ElButton>
      </ErrorAlert>

      <template v-else-if="source">
        <header class="ep-source__head">
          <h3 class="ep-source__file">{{ source.file_name }}</h3>
          <p class="ep-source__type">{{ fileTypeLabel(source.file_type) }}</p>
        </header>

        <dl class="ep-source__meta">
          <div v-if="source.document_version" class="ep-source__meta-item">
            <dt>版本</dt>
            <dd>{{ source.document_version }}</dd>
          </div>
          <div v-if="source.effective_from" class="ep-source__meta-item">
            <dt>生效日期</dt>
            <dd>{{ source.effective_from }}</dd>
          </div>
          <div v-if="source.dataset_version" class="ep-source__meta-item">
            <dt>数据集版本</dt>
            <dd>{{ source.dataset_version }}</dd>
          </div>
        </dl>

        <div class="ep-source__locator">
          <ElTag
            v-if="source.page_number !== null"
            size="small"
            effect="plain"
            disable-transitions
          >
            第 {{ source.page_number }} 页
          </ElTag>
          <ElTag v-if="source.sheet_name" size="small" effect="plain" disable-transitions>
            {{ source.sheet_name }}
          </ElTag>
          <ElTag v-if="rowRange" size="small" effect="plain" disable-transitions>{{ rowRange }}</ElTag>
          <ElTag v-if="source.section_title" size="small" effect="plain" disable-transitions>
            {{ source.section_title }}
          </ElTag>
        </div>

        <pre class="ep-source__text">{{ source.text }}</pre>
      </template>

      <p v-else-if="!loading && !error" class="ep-source__empty">没有可展示的原文内容。</p>
    </div>
  </ElDrawer>
</template>

<style scoped>
.ep-source {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-4);
}

.ep-source__loading,
.ep-source__empty {
  margin: 0;
  color: var(--ep-color-text-muted);
  font-size: 13px;
}

.ep-source__file {
  margin: 0;
  font-size: 16px;
  overflow-wrap: anywhere;
}

.ep-source__type {
  margin: var(--ep-space-1) 0 0;
  color: var(--ep-color-text-muted);
  font-size: 12px;
}

.ep-source__meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--ep-space-4);
  margin: 0;
  font-size: 13px;
}

.ep-source__meta-item {
  display: flex;
  gap: var(--ep-space-1);
}

.ep-source__meta-item dt {
  margin: 0;
  color: var(--ep-color-text-muted);
}

.ep-source__meta-item dd {
  margin: 0;
}

.ep-source__locator {
  display: flex;
  flex-wrap: wrap;
  gap: var(--ep-space-1);
}

.ep-source__text {
  margin: 0;
  padding: var(--ep-space-4);
  border: 1px solid var(--ep-color-border);
  border-radius: 8px;
  background: var(--ep-color-surface-muted, #f8fafc);
  font-family: inherit;
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
