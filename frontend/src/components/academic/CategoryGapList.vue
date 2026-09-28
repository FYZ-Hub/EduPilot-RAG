<script setup lang="ts">
/**
 * 类别学分缺口（UI_SPEC 7.4）：横向组合进度条 + 文本数值，不引入任何图表库。
 *
 * 五个数字全部来自服务端 ``CategoryGap``，**不重新计算** ``remaining_credits``；
 * 归一化比例只用于 CSS 宽度。
 */
import { computed } from 'vue'

import type { CategoryGap } from '@/api/academic'
import { buildCategoryGapBreakdown, formatCredit, type AcademicProgressSegmentKey } from '@/domain/academic'

const props = defineProps<{ gaps: CategoryGap[] }>()

interface GapItem {
  key: string
  category: string
  fields: Array<{ key: string; label: string; value: number }>
  valid: boolean
  message: string | null
  exceeded: boolean
  segments: Record<AcademicProgressSegmentKey, number>
}

const items = computed<GapItem[]>(() =>
  props.gaps.map((gap) => {
    const breakdown = buildCategoryGapBreakdown(gap)
    const percentOf = (key: AcademicProgressSegmentKey): number =>
      breakdown.segments.find((segment) => segment.key === key)?.percent ?? 0
    return {
      key: gap.category,
      category: gap.category,
      fields: [
        { key: 'required_credits', label: '要求', value: gap.required_credits },
        { key: 'completed_credits', label: '已修', value: gap.completed_credits },
        { key: 'in_progress_credits', label: '在修', value: gap.in_progress_credits },
        { key: 'remaining_credits', label: '剩余', value: gap.remaining_credits },
      ],
      valid: breakdown.valid,
      message: breakdown.message,
      exceeded: breakdown.exceeded,
      segments: {
        completed: percentOf('completed'),
        in_progress: percentOf('in_progress'),
        remaining: percentOf('remaining'),
      },
    }
  }),
)
</script>

<template>
  <section class="ep-gap-list" aria-label="类别学分缺口">
    <h2 class="ep-gap-list__title">类别学分缺口</h2>

    <p v-if="gaps.length === 0" class="ep-gap-list__empty">没有类别学分缺口</p>

    <ul v-else class="ep-gap-list__items">
      <li v-for="item in items" :key="item.key" class="ep-gap-item">
        <h3 class="ep-gap-item__category">{{ item.category }}</h3>

        <dl class="ep-gap-item__fields">
          <div
            v-for="field in item.fields"
            :key="field.key"
            class="ep-gap-item__field"
            :data-field="field.key"
          >
            <dt class="ep-gap-item__label">{{ field.label }}</dt>
            <dd class="ep-gap-item__value">{{ formatCredit(field.value) }}</dd>
          </div>
        </dl>

        <div
          class="ep-progress"
          role="progressbar"
          :aria-label="`${item.category} 完成情况`"
          :aria-valuemin="0"
          :aria-valuemax="100"
          :aria-valuenow="Math.round(item.segments.completed + item.segments.in_progress)"
        >
          <div
            class="ep-progress__seg ep-progress__seg--completed"
            :style="{ width: `${item.segments.completed}%` }"
          />
          <div
            class="ep-progress__seg ep-progress__seg--in_progress"
            :style="{ width: `${item.segments.in_progress}%` }"
          />
          <div
            class="ep-progress__seg ep-progress__seg--remaining"
            :style="{ width: `${item.segments.remaining}%` }"
          />
        </div>

        <p v-if="item.exceeded" class="ep-gap-item__exceeded">该类别的已修与在修已超过要求</p>
        <p v-if="!item.valid" class="ep-gap-item__error" role="alert">数据错误：{{ item.message }}</p>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.ep-gap-list {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-3);
  min-width: 0;
}

.ep-gap-list__title,
.ep-gap-item__category {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--ep-color-text-secondary);
}

.ep-gap-list__empty {
  margin: 0;
  padding: var(--ep-space-4);
  border: 1px dashed var(--ep-color-border);
  border-radius: var(--ep-radius-card);
  color: var(--ep-color-text-muted);
  font-size: 13px;
  text-align: center;
}

.ep-gap-list__items {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-4);
  margin: 0;
  padding: 0;
  list-style: none;
}

.ep-gap-item {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-2);
  min-width: 0;
}

.ep-gap-item__fields {
  display: flex;
  flex-wrap: wrap;
  gap: var(--ep-space-3);
  margin: 0;
}

.ep-gap-item__field {
  display: flex;
  gap: var(--ep-space-1);
  font-size: 12px;
}

.ep-gap-item__label {
  color: var(--ep-color-text-muted);
}

.ep-gap-item__value {
  margin: 0;
  font-variant-numeric: tabular-nums;
  color: var(--ep-color-text-secondary);
}

.ep-gap-item__exceeded,
.ep-gap-item__error {
  margin: 0;
  font-size: 12px;
}

.ep-gap-item__exceeded {
  color: var(--ep-color-warning-text, #b45309);
}

.ep-gap-item__error {
  color: var(--ep-color-danger, #dc2626);
}

.ep-progress {
  display: flex;
  width: 100%;
  height: 10px;
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
</style>
