<script setup lang="ts">
/**
 * 证据面板（UI_SPEC 6.2 / 6.6）：每张卡片只显示真实 citation 字段。
 *
 * 卡片实现与学业规划的「计算证据」共用同一个 :class:`SourceEvidenceCard`，
 * 不复制第二套来源展示逻辑；没有选中引用时提示用户点击回答中的引用，
 * **不构造任何占位来源**；冲突结果的全部版本并列保留。
 */
import type { ChatCitation } from '@/api/chat'
import SourceEvidenceCard from '@/components/common/SourceEvidenceCard.vue'

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
</script>

<template>
  <section class="ep-evidence" aria-label="来源证据">
    <h2 class="ep-evidence__title">来源证据</h2>

    <p v-if="citations.length === 0" class="ep-evidence__empty">
      点击回答中的引用查看原文
    </p>

    <div v-else class="ep-evidence__list">
      <SourceEvidenceCard
        v-for="citation in citations"
        :key="citation.citation_index"
        selectable
        :index="citation.citation_index"
        :chunk-id="citation.chunk_id"
        :selected="selectedIndex === citation.citation_index"
        :file-name="citation.file_name"
        :document-version="citation.document_version"
        :effective-from="citation.effective_from"
        :dataset-version="citation.dataset_version"
        :page-number="citation.page_number"
        :sheet-name="citation.sheet_name"
        :row-start="citation.row_start"
        :row-end="citation.row_end"
        :section-title="citation.section_title"
        :quote="citation.quote"
        @select="emit('select', citation.citation_index)"
        @open="emit('open-source', citation)"
      />
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
</style>
