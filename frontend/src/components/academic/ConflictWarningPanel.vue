<script setup lang="ts">
/**
 * 冲突与提醒（UI_SPEC 7.5）：按服务端真实 code 分类，warning / blocking 同时用文字表达。
 *
 * 未知 code 不会被隐藏；不替用户选择结论，也不生成任何虚构建议。
 */
import { computed } from 'vue'

import type { ConflictWarning, PlanningEvidence } from '@/api/academic'
import {
  conflictGroup,
  conflictGroupLabel,
  describeSeverity,
  linkEvidence,
  type ConflictGroup,
} from '@/domain/academic'

const props = defineProps<{
  warnings: ConflictWarning[]
  evidence: PlanningEvidence[]
}>()

const emit = defineEmits<{ 'focus-evidence': [chunkId: string] }>()

const GROUP_ORDER: ConflictGroup[] = ['version', 'time', 'record', 'other']

const groups = computed(() =>
  GROUP_ORDER.map((group) => ({
    group,
    label: conflictGroupLabel(group),
    items: props.warnings.filter((warning) => conflictGroup(warning.code) === group),
  })).filter((group) => group.items.length > 0),
)

function linked(warning: ConflictWarning): PlanningEvidence[] {
  return linkEvidence(props.evidence, warning.evidence_chunk_ids)
}
</script>

<template>
  <section class="ep-conflicts" aria-label="冲突与提醒">
    <h2 class="ep-conflicts__title">冲突与提醒</h2>

    <p v-if="warnings.length === 0" class="ep-conflicts__ok">
      未发现规则冲突
    </p>

    <div v-else class="ep-conflicts__groups">
      <section
        v-for="group in groups"
        :key="group.group"
        class="ep-conflict-group"
        :data-group="group.group"
      >
        <h3 class="ep-conflict-group__title">{{ group.label }}</h3>

        <ul class="ep-conflict-group__items">
          <li
            v-for="warning in group.items"
            :key="`${warning.code}-${warning.message}`"
            class="ep-conflict-item"
            :data-code="warning.code"
            :data-severity="warning.severity"
          >
            <p class="ep-conflict-item__head">
              <span class="ep-conflict-item__code">{{ warning.code }}</span>
              <span class="ep-conflict-item__severity">{{ describeSeverity(warning.severity).label }}</span>
            </p>
            <p class="ep-conflict-item__message">{{ warning.message }}</p>
            <p v-if="linked(warning).length > 0" class="ep-conflict-item__evidence">
              <button
                v-for="item in linked(warning)"
                :key="item.chunk_id"
                type="button"
                class="ep-evidence-chip"
                :data-chunk-id="item.chunk_id"
                @click="emit('focus-evidence', item.chunk_id)"
              >
                查看证据
              </button>
            </p>
          </li>
        </ul>
      </section>
    </div>
  </section>
</template>

<style scoped>
.ep-conflicts {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-3);
  min-width: 0;
}

.ep-conflicts__title,
.ep-conflict-group__title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--ep-color-text-secondary);
}

.ep-conflicts__ok {
  margin: 0;
  padding: var(--ep-space-3);
  border: 1px solid var(--ep-color-success-border, #86efac);
  border-radius: var(--ep-radius-card);
  background: var(--ep-color-success-soft, #f0fdf4);
  color: var(--ep-color-success-text, #15803d);
  font-size: 13px;
}

.ep-conflicts__groups {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-3);
}

.ep-conflict-group__items {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-2);
  margin: var(--ep-space-2) 0 0;
  padding: 0;
  list-style: none;
}

.ep-conflict-item {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-1);
  padding: var(--ep-space-3);
  border: 1px solid var(--ep-color-border);
  border-left: 3px solid var(--ep-color-warning, #f59e0b);
  border-radius: var(--ep-radius-card);
  background: #fff;
}

.ep-conflict-item[data-severity='blocking'] {
  border-left-color: var(--ep-color-danger, #dc2626);
}

.ep-conflict-item__head {
  display: flex;
  flex-wrap: wrap;
  gap: var(--ep-space-2);
  align-items: baseline;
  margin: 0;
}

.ep-conflict-item__code {
  font-size: 12px;
  font-weight: 600;
  overflow-wrap: anywhere;
}

.ep-conflict-item__severity {
  font-size: 12px;
  color: var(--ep-color-text-muted);
}

.ep-conflict-item__message {
  margin: 0;
  font-size: 13px;
  overflow-wrap: anywhere;
}

.ep-conflict-item__evidence {
  display: flex;
  flex-wrap: wrap;
  gap: var(--ep-space-2);
  margin: 0;
}

/* UI_SPEC 9：可点击目标桌面最小 40×40，移动端最小 44×44（显式声明，不靠行高撑大） */
.ep-evidence-chip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 40px;
  padding: 0 var(--ep-space-3);
  border: 1px solid var(--ep-color-evidence);
  border-radius: 999px;
  background: var(--ep-color-evidence-soft);
  color: var(--ep-color-evidence);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}

.ep-evidence-chip:focus-visible {
  outline: 2px solid var(--ep-color-evidence);
  outline-offset: 2px;
}

@media (max-width: 767px) {
  .ep-evidence-chip {
    min-height: 44px;
  }
}
</style>
