<script setup lang="ts">
/**
 * 学分概览（UI_SPEC 7.4）：四个数字卡严格绑定 ``PlanningResult``，
 * 组合进度条使用 :func:`summariseCredits` 与 :func:`buildProgressBreakdown` 的展示比例。
 *
 * 绝不在浏览器重新计算、覆盖或“修正”服务端数字；数据非法时如实显示数据错误。
 */
import { computed } from 'vue'

import type { PlanningResult } from '@/api/academic'
import {
  buildProgressBreakdown,
  formatCredit,
  summariseCredits,
  type AcademicProgressSegmentKey,
} from '@/domain/academic'

const props = defineProps<{ result: PlanningResult }>()

const summary = computed(() => summariseCredits(props.result))
const breakdown = computed(() => buildProgressBreakdown(props.result))
const progressPercent = computed(() => Math.round(breakdown.value.progressPercent))

const cards = computed(() => [
  { key: 'required', label: '要求学分', value: props.result.required_credits },
  { key: 'completed', label: '已修学分', value: props.result.completed_credits },
  { key: 'in_progress', label: '在修学分', value: props.result.in_progress_credits },
  { key: 'remaining', label: '剩余学分', value: props.result.remaining_credits },
])

function widthOf(key: AcademicProgressSegmentKey): string {
  const segment = breakdown.value.segments.find((item) => item.key === key)
  return `${segment ? segment.percent : 0}%`
}
</script>

<template>
  <section class="ep-credit-summary" aria-label="学分概览">
    <dl class="ep-credit-summary__cards">
      <div v-for="card in cards" :key="card.key" class="ep-credit-card" :data-credit="card.key">
        <dt class="ep-credit-card__label">{{ card.label }}</dt>
        <dd class="ep-credit-card__value">{{ formatCredit(card.value) }}</dd>
      </div>
    </dl>

    <div
      class="ep-progress"
      role="progressbar"
      aria-live="polite"
      aria-label="总学分完成情况"
      :aria-valuemin="0"
      :aria-valuemax="100"
      :aria-valuenow="progressPercent"
    >
      <div class="ep-progress__seg ep-progress__seg--completed" :style="{ width: widthOf('completed') }" />
      <div class="ep-progress__seg ep-progress__seg--in_progress" :style="{ width: widthOf('in_progress') }" />
      <div class="ep-progress__seg ep-progress__seg--remaining" :style="{ width: widthOf('remaining') }" />
    </div>

    <p class="ep-credit-summary__legend">
      <span class="ep-credit-summary__legend-item ep-credit-summary__legend-item--completed">已修</span>
      <span class="ep-credit-summary__legend-item ep-credit-summary__legend-item--in-progress">在修</span>
      <span class="ep-credit-summary__legend-item ep-credit-summary__legend-item--remaining">剩余</span>
    </p>

    <p v-if="breakdown.exceeded" class="ep-credit-summary__exceeded">
      已修与在修学分已超过要求，进度条按 100% 展示（数字仍为服务端返回值）
    </p>

    <p v-if="!summary.valid" class="ep-credit-summary__error" role="alert">
      数据错误：{{ summary.message }}
    </p>
  </section>
</template>

<style scoped>
.ep-credit-summary {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-3);
}

.ep-credit-summary__cards {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--ep-space-3);
  margin: 0;
}

.ep-credit-card {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-1);
  padding: var(--ep-space-4);
  border: 1px solid var(--ep-color-border);
  border-radius: var(--ep-radius-card);
  background: #fff;
}

.ep-credit-card__label {
  font-size: 13px;
  color: var(--ep-color-text-muted);
}

.ep-credit-card__value {
  margin: 0;
  font-size: 24px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--ep-color-text);
}

.ep-progress {
  display: flex;
  width: 100%;
  height: 12px;
  overflow: hidden;
  border-radius: 999px;
  background: var(--ep-color-surface-subtle);
}

.ep-progress__seg {
  height: 100%;
}

.ep-progress__seg--completed {
  background: var(--ep-color-primary);
}

.ep-progress__seg--in_progress {
  background: var(--ep-color-info-soft, #93c5fd);
}

.ep-progress__seg--remaining {
  background: var(--ep-color-border);
}

.ep-credit-summary__legend {
  display: flex;
  flex-wrap: wrap;
  gap: var(--ep-space-3);
  margin: 0;
  font-size: 12px;
  color: var(--ep-color-text-muted);
}

.ep-credit-summary__legend-item::before {
  display: inline-block;
  width: 8px;
  height: 8px;
  margin-right: var(--ep-space-1);
  border-radius: 2px;
  vertical-align: middle;
  content: '';
}

.ep-credit-summary__legend-item--completed::before {
  background: var(--ep-color-primary);
}

.ep-credit-summary__legend-item--in-progress::before {
  background: var(--ep-color-info-soft, #93c5fd);
}

.ep-credit-summary__legend-item--remaining::before {
  background: var(--ep-color-border);
}

.ep-credit-summary__exceeded {
  margin: 0;
  color: var(--ep-color-warning-text, #b45309);
  font-size: 13px;
}

.ep-credit-summary__error {
  margin: 0;
  color: var(--ep-color-danger, #dc2626);
  font-size: 13px;
}

@media (max-width: 767px) {
  .ep-credit-summary__cards {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
