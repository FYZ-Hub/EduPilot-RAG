<script setup lang="ts">
/**
 * 证据面板（UI_SPEC 6.2 / 6.6）：每张卡片只显示真实 citation 字段。
 *
 * 没有选中引用时提示用户点击回答中的引用，**不构造任何占位来源**；
 * 冲突结果的全部版本并列保留。
 */
import type { ChatCitation } from '@/api/chat'

withDefaults(
  defineProps<{
    citations: ChatCitation[]
    selectedIndex?: number | null
  }>(),
  { selectedIndex: null },
)

const emit = defineEmits<{
  select: [index: number]
  'open-source': [citation: ChatCitation]
}>()

function hasNumber(value: number | null): boolean {
  return value !== null && value !== undefined
}

function hasText(value: string | null): boolean {
  return value !== null && value !== undefined && value !== ''
}

function rowRange(citation: ChatCitation): string | null {
  if (!hasNumber(citation.row_start)) {
    return null
  }
  if (citation.row_end === null || citation.row_end === citation.row_start) {
    return `第 ${citation.row_start} 行`
  }
  return `${citation.row_start}-${citation.row_end}`
}
</script>

<template>
  <section class="ep-evidence" aria-label="来源证据">
    <h2 class="ep-evidence__title">来源证据</h2>

    <p v-if="citations.length === 0" class="ep-evidence__empty">
      点击回答中的引用查看原文
    </p>

    <div v-else class="ep-evidence__list">
      <article
        v-for="citation in citations"
        :key="citation.citation_index"
        class="ep-evidence-card"
        :class="{ 'is-selected': selectedIndex === citation.citation_index }"
        @click="emit('select', citation.citation_index)"
      >
        <header class="ep-evidence-card__head">
          <span class="ep-evidence-card__index">[{{ citation.citation_index }}]</span>
          <span class="ep-evidence-card__file">{{ citation.file_name }}</span>
        </header>

        <dl class="ep-evidence-card__meta">
          <div v-if="hasText(citation.document_version)" class="ep-evidence-card__meta-item">
            <dt>版本</dt>
            <dd>{{ citation.document_version }}</dd>
          </div>
          <div v-if="hasText(citation.effective_from)" class="ep-evidence-card__meta-item">
            <dt>生效</dt>
            <dd>{{ citation.effective_from }}</dd>
          </div>
          <div v-if="hasText(citation.dataset_version)" class="ep-evidence-card__meta-item">
            <dt>数据集</dt>
            <dd>{{ citation.dataset_version }}</dd>
          </div>
        </dl>

        <div class="ep-evidence-card__locator">
          <ElTag
            v-if="hasNumber(citation.page_number)"
            size="small"
            effect="plain"
            disable-transitions
          >
            第 {{ citation.page_number }} 页
          </ElTag>
          <ElTag v-if="hasText(citation.sheet_name)" size="small" effect="plain" disable-transitions>
            {{ citation.sheet_name }}
          </ElTag>
          <ElTag v-if="rowRange(citation)" size="small" effect="plain" disable-transitions>
            {{ rowRange(citation) }}
          </ElTag>
          <ElTag
            v-if="hasText(citation.section_title)"
            size="small"
            effect="plain"
            disable-transitions
          >
            {{ citation.section_title }}
          </ElTag>
        </div>

        <pre class="ep-evidence-card__quote">{{ citation.quote }}</pre>

        <ElButton
          class="ep-evidence-card__open"
          size="small"
          text
          type="primary"
          @click.stop="emit('open-source', citation)"
        >
          查看原文
        </ElButton>
      </article>
    </div>
  </section>
</template>

<style scoped>
.ep-evidence {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-3);
  min-width: 0;
}

.ep-evidence__title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--ep-color-text-secondary);
}

.ep-evidence__empty {
  margin: 0;
  padding: var(--ep-space-4);
  border: 1px dashed var(--ep-color-border);
  border-radius: var(--ep-radius-card);
  color: var(--ep-color-text-muted);
  font-size: 13px;
  text-align: center;
}

.ep-evidence__list {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-3);
}

.ep-evidence-card {
  padding: var(--ep-space-4);
  border: 1px solid var(--ep-color-border);
  border-radius: var(--ep-radius-card);
  background: #fff;
  cursor: pointer;
}

.ep-evidence-card.is-selected {
  border-color: var(--ep-color-evidence);
  box-shadow: inset 3px 0 0 var(--ep-color-evidence);
  background: var(--ep-color-evidence-soft);
}

.ep-evidence-card__head {
  display: flex;
  gap: var(--ep-space-2);
  align-items: baseline;
  min-width: 0;
}

.ep-evidence-card__index {
  color: var(--ep-color-evidence);
  font-weight: 700;
}

.ep-evidence-card__file {
  font-weight: 600;
  overflow-wrap: anywhere;
}

.ep-evidence-card__meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--ep-space-3);
  margin: var(--ep-space-2) 0 0;
  font-size: 12px;
  color: var(--ep-color-text-muted);
}

.ep-evidence-card__meta-item {
  display: flex;
  gap: var(--ep-space-1);
}

.ep-evidence-card__meta-item dt {
  margin: 0;
}

.ep-evidence-card__meta-item dd {
  margin: 0;
  color: var(--ep-color-text-secondary);
}

.ep-evidence-card__locator {
  display: flex;
  flex-wrap: wrap;
  gap: var(--ep-space-1);
  margin-top: var(--ep-space-2);
}

.ep-evidence-card__quote {
  margin: var(--ep-space-3) 0;
  padding: var(--ep-space-3);
  border-radius: 8px;
  background: var(--ep-color-surface-muted, #f8fafc);
  color: var(--ep-color-text-secondary);
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
