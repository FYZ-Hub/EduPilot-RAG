<script setup lang="ts">
/**
 * 文档预览抽屉（UI_SPEC 5.6）。
 *
 * - 520px 右侧抽屉，展示文件名、来源、状态、SHA-256 前 12 位与版本；
 * - PDF 显示页码与章节，DOCX 显示标题路径，XLSX 显示工作表与行范围；
 * - 所有文本按纯文本渲染，``v-html`` 一律禁止，浏览器不会执行文档宏、脚本或链接；
 * - 解析失败时给出恢复指引：demo 走 seed 续跑，upload 重新选择文件后再次调用原上传接口。
 */
import { computed } from 'vue'

import type { ApiError } from '@/api/client'
import type { DocumentDetail, DocumentPreview } from '@/api/documents'
import {
  documentSubtitle,
  formatLocalDateTime,
  previewLocators,
  shortChecksum,
  sourceLabel,
} from '@/domain/documents'

import AsyncState from '../common/AsyncState.vue'
import ErrorAlert from '../common/ErrorAlert.vue'
import StatusTag from '../common/StatusTag.vue'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    document: DocumentDetail | null
    preview: DocumentPreview | null
    loading?: boolean
    error?: ApiError | null
  }>(),
  { loading: false, error: null },
)

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  recover: []
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const fileType = computed(() => props.document?.file_type ?? props.preview?.file_type ?? 'pdf')

const recoveryHint = computed(() => {
  if (props.document?.source_type === 'demo') {
    return '演示文档解析失败时，可再次调用“加载演示资料”续跑。'
  }
  return '上传文档解析失败时，请修正或更换文件后重新上传；服务端会按 disposition 决定续跑或拒绝。'
})

const failed = computed(() => props.document?.status === 'failed')
</script>

<template>
  <ElDrawer v-model="visible" title="文档预览" size="520px" direction="rtl">
    <AsyncState
      :loading="loading"
      :error="error"
      loading-text="正在读取预览"
      empty-title="无法读取预览"
      :skeleton-rows="5"
    >
      <ElAlert
        v-if="failed"
        class="ep-preview__notice"
        type="error"
        :closable="false"
        show-icon
        title="该文档解析失败"
      >
        <p>{{ recoveryHint }}</p>
        <p v-if="document?.error" class="ep-preview__error-code">
          错误码：{{ document.error.code }} · {{ document.error.message }}
          <span v-if="document.error.retryable">（可重试）</span>
        </p>
        <ElButton size="small" @click="emit('recover')">按来源恢复</ElButton>
      </ElAlert>

      <div v-if="document" class="ep-preview__meta">
        <p class="ep-preview__name">{{ document.file_name }}</p>
        <p class="ep-preview__subtitles">
          {{ sourceLabel(document.source_type) }} · {{ documentSubtitle(document) }}
        </p>
        <div class="ep-preview__tags">
          <StatusTag :document="document" />
          <ElTag size="small" effect="plain" disable-transitions>
            SHA-256 {{ shortChecksum(document.checksum) }}
          </ElTag>
        </div>
        <dl class="ep-preview__fields">
          <div><dt>生效日期</dt><dd>{{ document.effective_from ?? '—' }}</dd></div>
          <div><dt>更新时间</dt><dd>{{ formatLocalDateTime(document.updated_at) }}</dd></div>
          <div><dt>定位能力</dt><dd>{{ document.locator_types?.length ? document.locator_types.join(' / ') : '—' }}</dd></div>
        </dl>
      </div>

      <ElAlert
        v-if="preview?.truncated"
        class="ep-preview__notice"
        type="info"
        :closable="false"
        show-icon
        :title="`仅返回前 ${preview.returned_blocks} / ${preview.total_blocks} 个内容块`"
      />

      <ul v-if="preview?.blocks.length" class="ep-preview__blocks">
        <li v-for="block in preview.blocks" :key="block.block_index" class="ep-preview__block">
          <div class="ep-preview__locators">
            <ElTag
              v-for="locator in previewLocators(fileType, block)"
              :key="`${locator.label}-${locator.value}`"
              size="small"
              effect="plain"
              disable-transitions
            >
              {{ locator.label }}：{{ locator.value }}
            </ElTag>
            <ElTag v-if="!previewLocators(fileType, block).length" size="small" effect="plain" disable-transitions>
              无定位字段
            </ElTag>
          </div>
          <pre class="ep-preview__text">{{ block.text }}</pre>
        </li>
      </ul>

      <p v-else-if="!loading && !error && !failed" class="ep-preview__empty">该文档暂无可预览的解析内容。</p>
    </AsyncState>
  </ElDrawer>
</template>

<style scoped>
.ep-preview__meta {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-2);
  padding-bottom: var(--ep-space-4);
  border-bottom: 1px solid var(--ep-color-border-light);
}

.ep-preview__name {
  font-size: 15px;
  line-height: 22px;
  font-weight: 600;
  overflow-wrap: anywhere;
}

.ep-preview__subtitles {
  font-size: 12px;
  line-height: 18px;
  color: var(--ep-color-text-muted);
  overflow-wrap: anywhere;
}

.ep-preview__tags {
  display: flex;
  align-items: center;
  gap: var(--ep-space-2);
  flex-wrap: wrap;
}

.ep-preview__fields {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: var(--ep-space-2);
  margin: 0;
}

.ep-preview__fields dt {
  font-size: 12px;
  line-height: 18px;
  color: var(--ep-color-text-muted);
}

.ep-preview__fields dd {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  overflow-wrap: anywhere;
}

.ep-preview__notice,
.ep-preview__error-code {
  margin-top: var(--ep-space-2);
}

.ep-preview__blocks {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-4);
  margin: var(--ep-space-4) 0 0;
  padding: 0;
  list-style: none;
}

.ep-preview__block {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-2);
}

.ep-preview__locators {
  display: flex;
  gap: var(--ep-space-1);
  flex-wrap: wrap;
}

.ep-preview__text {
  margin: 0;
  padding: var(--ep-space-3);
  background-color: var(--ep-color-surface-subtle);
  border: 1px solid var(--ep-color-border-light);
  border-radius: var(--ep-radius-card);
  font-family: var(--ep-font-family);
  font-size: 13px;
  line-height: 20px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.ep-preview__empty {
  margin-top: var(--ep-space-4);
  font-size: 13px;
  line-height: 20px;
  color: var(--ep-color-text-muted);
}
</style>
