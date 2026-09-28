<script setup lang="ts">
/**
 * 计算证据面板（UI_SPEC 7.5）：与 RAG 引用面板共用 :class:`SourceEvidenceCard`。
 *
 * 只展示 ``PlanningResult.evidence`` 的真实字段；``focusedChunkId`` 由缺失必修课 /
 * 冲突提醒的「查看证据」按钮给出，用于高亮并滚动到对应卡片 —— **不**根据文本推断来源。
 */
import { nextTick, watch } from 'vue'

import type { PlanningEvidence } from '@/api/academic'
import SourceEvidenceCard from '@/components/common/SourceEvidenceCard.vue'

const props = withDefaults(
  defineProps<{
    evidence: PlanningEvidence[]
    focusedChunkId?: string | null
  }>(),
  { focusedChunkId: null },
)

const emit = defineEmits<{ 'open-source': [item: PlanningEvidence] }>()

function elementId(chunkId: string): string {
  return `ep-planning-evidence-${chunkId}`
}

watch(
  () => props.focusedChunkId,
  async (chunkId) => {
    if (!chunkId) {
      return
    }
    await nextTick()
    const element = document.getElementById(elementId(chunkId))
    if (element && typeof element.scrollIntoView === 'function') {
      element.scrollIntoView({ block: 'nearest' })
    }
  },
)
</script>

<template>
  <section class="ep-planning-evidence" aria-label="计算证据" aria-live="polite">
    <h2 class="ep-planning-evidence__title">计算证据</h2>

    <p v-if="evidence.length === 0" class="ep-planning-evidence__empty">
      本次计算没有可展示的证据
    </p>

    <div v-else class="ep-planning-evidence__list">
      <SourceEvidenceCard
        v-for="item in evidence"
        :id="elementId(item.chunk_id)"
        :key="item.chunk_id"
        :chunk-id="item.chunk_id"
        :focused="focusedChunkId === item.chunk_id"
        :file-name="item.file_name"
        :document-version="item.document_version"
        :effective-from="item.effective_from"
        :page-number="item.page_number"
        :sheet-name="item.sheet_name"
        :row-start="item.row_start"
        :row-end="item.row_end"
        :section-title="item.section_title"
        :quote="item.quote"
        @open="emit('open-source', item)"
      />
    </div>
  </section>
</template>

<style scoped>
.ep-planning-evidence {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-3);
  min-width: 0;
}

.ep-planning-evidence__title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--ep-color-text-secondary);
}

.ep-planning-evidence__empty {
  margin: 0;
  padding: var(--ep-space-4);
  border: 1px dashed var(--ep-color-border);
  border-radius: var(--ep-radius-card);
  color: var(--ep-color-text-muted);
  font-size: 13px;
  text-align: center;
}

.ep-planning-evidence__list {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-3);
}
</style>
