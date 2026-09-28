<script setup lang="ts">
/**
 * 学业规划选择区（UI_SPEC 7.2 / 7.3）。
 *
 * 只展示真实 options 与已选 ID，**不做**任何选择决策：唯一 ready 选项的预选、
 * 多选项时保持未选择、以及 URL query 恢复全部由 Academic Store 与纯函数决定。
 */
import { computed } from 'vue'

import type { AcademicOptions, RecordSetOption, RuleSetOption } from '@/api/academic'
import type { ApiError } from '@/api/client'
import { formatLocalDateTime } from '@/domain/documents'
import ErrorAlert from '@/components/common/ErrorAlert.vue'

const props = defineProps<{
  options: AcademicOptions | null
  loading: boolean
  error: ApiError | null
  selectedRecordSetId: string | null
  selectedRuleSetId: string | null
  disabled: boolean
}>()

const emit = defineEmits<{
  'select-record': [id: string | null]
  'select-rule': [id: string | null]
  retry: []
}>()

const recordSets = computed<RecordSetOption[]>(() => props.options?.record_sets ?? [])
const ruleSets = computed<RuleSetOption[]>(() => props.options?.rule_sets ?? [])

function join(parts: Array<string | number | null | undefined>): string {
  return parts.filter((part) => part !== null && part !== undefined && part !== '').join(' · ')
}

function recordLabel(option: RecordSetOption): string {
  return join([option.name, formatLocalDateTime(option.updated_at), option.status])
}

function ruleLabel(option: RuleSetOption): string {
  return join([
    option.name,
    option.major,
    option.admission_year,
    option.rule_version,
    option.effective_from,
    option.status,
  ])
}

function onRecordChange(event: Event): void {
  const value = (event.target as HTMLSelectElement).value
  emit('select-record', value === '' ? null : value)
}

function onRuleChange(event: Event): void {
  const value = (event.target as HTMLSelectElement).value
  emit('select-rule', value === '' ? null : value)
}
</script>

<template>
  <section class="ep-selection" aria-label="规划数据选择">
    <p v-if="loading" class="ep-selection__loading" role="status" aria-live="polite">
      正在读取课程记录与培养方案规则…
    </p>

    <ErrorAlert
      v-else-if="error"
      class="ep-selection__error"
      :error="error"
      title="无法读取可选数据"
    >
      <ElButton class="ep-selection__retry" size="small" @click="emit('retry')">重试</ElButton>
    </ErrorAlert>

    <div class="ep-selection__grid">
      <div class="ep-selection__field">
        <label class="ep-selection__label" for="ep-record-set">课程记录集</label>
        <p id="ep-record-set-help" class="ep-selection__help">
          来自已导入的课程记录文件；未导入时无法计算。
        </p>
        <select
          id="ep-record-set"
          class="ep-selection__record"
          :value="selectedRecordSetId ?? ''"
          :disabled="disabled"
          aria-describedby="ep-record-set-help"
          @change="onRecordChange"
        >
          <option value="">请选择课程记录集</option>
          <option v-for="option in recordSets" :key="option.id" :value="option.id">
            {{ recordLabel(option) }}
          </option>
        </select>
        <p v-if="recordSets.length === 0" class="ep-selection__empty">还没有可用的课程记录</p>
      </div>

      <div class="ep-selection__field">
        <label class="ep-selection__label" for="ep-rule-set">培养方案规则</label>
        <p id="ep-rule-set-help" class="ep-selection__help">
          来自已导入的培养方案；多个版本同时存在时必须由你明确选择。
        </p>
        <select
          id="ep-rule-set"
          class="ep-selection__rule"
          :value="selectedRuleSetId ?? ''"
          :disabled="disabled"
          aria-describedby="ep-rule-set-help"
          @change="onRuleChange"
        >
          <option value="">请选择培养方案规则</option>
          <option v-for="option in ruleSets" :key="option.id" :value="option.id">
            {{ ruleLabel(option) }}
          </option>
        </select>
        <p v-if="ruleSets.length === 0" class="ep-selection__empty">还没有可用的培养方案规则</p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.ep-selection {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-3);
}

.ep-selection__loading {
  margin: 0;
  color: var(--ep-color-text-muted);
  font-size: 13px;
}

.ep-selection__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--ep-space-4);
}

.ep-selection__field {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-1);
  min-width: 0;
}

.ep-selection__label {
  font-size: 13px;
  font-weight: 600;
}

.ep-selection__help,
.ep-selection__empty {
  margin: 0;
  font-size: 12px;
  line-height: 18px;
  color: var(--ep-color-text-muted);
}

.ep-selection__record,
.ep-selection__rule {
  width: 100%;
  min-height: 40px;
  padding: var(--ep-space-1) var(--ep-space-2);
  border: 1px solid var(--ep-color-border);
  border-radius: 8px;
  background: #fff;
  color: var(--ep-color-text);
  font: inherit;
  font-size: 13px;
}

.ep-selection__record:focus-visible,
.ep-selection__rule:focus-visible {
  outline: 2px solid var(--ep-color-primary);
  outline-offset: 1px;
}

.ep-selection__record:disabled,
.ep-selection__rule:disabled {
  background: var(--ep-color-surface-subtle);
  color: var(--ep-color-text-muted);
}

@media (max-width: 767px) {
  .ep-selection__grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
