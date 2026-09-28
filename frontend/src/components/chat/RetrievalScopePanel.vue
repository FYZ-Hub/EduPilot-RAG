<script setup lang="ts">
/**
 * 检索范围筛选（UI_SPEC 6.3）：默认折叠，选项**全部**来自 ``GET /api/retrieval/options``。
 *
 * 空数组对应的选择器完全隐藏，不硬编码任何专业 / 年级 / 学期 / 文档分类；
 * 使用原生 ``select`` 以保证选项文本可见、键盘可操作且不引入额外依赖。
 */
import { ref } from 'vue'

import type { ChatFilters } from '@/api/chat'
import type { ApiError } from '@/api/client'
import type { RetrievalOptions } from '@/api/retrieval'
import type { ChatFilterKey } from '@/stores/chat'
import ErrorAlert from '@/components/common/ErrorAlert.vue'

const props = defineProps<{
  options: RetrievalOptions | null
  loading: boolean
  error: ApiError | null
  filters: ChatFilters
  activeFilters: Array<{ key: ChatFilterKey; label: string }>
}>()

const emit = defineEmits<{
  update: [payload: { key: ChatFilterKey; value: string | number | null }]
  remove: [key: ChatFilterKey]
  clear: []
  retry: []
}>()

const details = ref<HTMLDetailsElement | null>(null)

/** 供页面在「调整检索范围」时展开面板。 */
function open(): void {
  if (details.value) {
    details.value.open = true
  }
}

defineExpose({ open })

function onChange(key: ChatFilterKey, event: Event, numeric: boolean): void {
  const raw = (event.target as HTMLSelectElement).value
  emit('update', { key, value: raw === '' ? null : numeric ? Number(raw) : raw })
}

const majorCount = () => props.options?.majors.length ?? 0
const gradeYearCount = () => props.options?.grade_years.length ?? 0
const semesterCount = () => props.options?.semesters.length ?? 0
const categoryCount = () => props.options?.doc_categories.length ?? 0
</script>

<template>
  <details ref="details" class="ep-scope">
    <summary class="ep-scope__summary">检索范围</summary>

    <div class="ep-scope__body">
      <ErrorAlert v-if="error" :error="error" title="检索范围加载失败">
        <ElButton class="ep-scope__retry" size="small" @click="emit('retry')">重试</ElButton>
      </ErrorAlert>

      <p v-else-if="loading" class="ep-scope__loading">正在读取检索范围…</p>

      <template v-else>
        <div v-if="majorCount() > 0" class="ep-scope__field">
          <label class="ep-scope__label" for="ep-filter-major">专业</label>
          <select
            id="ep-filter-major"
            class="ep-scope__select"
            :value="filters.major ?? ''"
            @change="onChange('major', $event, false)"
          >
            <option value="">全部专业</option>
            <option v-for="major in options?.majors" :key="major" :value="major">{{ major }}</option>
          </select>
        </div>

        <div v-if="gradeYearCount() > 0" class="ep-scope__field">
          <label class="ep-scope__label" for="ep-filter-grade">年级</label>
          <select
            id="ep-filter-grade"
            class="ep-scope__select"
            :value="filters.grade_year === null ? '' : String(filters.grade_year)"
            @change="onChange('grade_year', $event, true)"
          >
            <option value="">全部年级</option>
            <option v-for="year in options?.grade_years" :key="year" :value="String(year)">
              {{ year }}
            </option>
          </select>
        </div>

        <div v-if="semesterCount() > 0" class="ep-scope__field">
          <label class="ep-scope__label" for="ep-filter-semester">学期</label>
          <select
            id="ep-filter-semester"
            class="ep-scope__select"
            :value="filters.semester ?? ''"
            @change="onChange('semester', $event, false)"
          >
            <option value="">全部学期</option>
            <option v-for="semester in options?.semesters" :key="semester" :value="semester">
              {{ semester }}
            </option>
          </select>
        </div>

        <div v-if="categoryCount() > 0" class="ep-scope__field">
          <label class="ep-scope__label" for="ep-filter-category">文档类型</label>
          <select
            id="ep-filter-category"
            class="ep-scope__select"
            :value="filters.doc_category ?? ''"
            @change="onChange('doc_category', $event, false)"
          >
            <option value="">全部文档类型</option>
            <option
              v-for="category in options?.doc_categories"
              :key="category.value"
              :value="category.value"
            >
              {{ category.label }}
            </option>
          </select>
        </div>
      </template>

      <div v-if="activeFilters.length > 0" class="ep-scope__tags">
        <ElTag
          v-for="filter in activeFilters"
          :key="filter.key"
          class="ep-scope__tag"
          size="small"
          closable
          disable-transitions
          @close="emit('remove', filter.key)"
        >
          {{ filter.label }}
        </ElTag>
        <ElButton class="ep-scope__clear" size="small" text type="primary" @click="emit('clear')">
          清除全部筛选
        </ElButton>
      </div>
    </div>
  </details>
</template>

<style scoped>
.ep-scope {
  border: 1px solid var(--ep-color-border);
  border-radius: var(--ep-radius-card);
  background: #fff;
}

.ep-scope__summary {
  padding: var(--ep-space-3) var(--ep-space-4);
  color: var(--ep-color-text-secondary);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}

.ep-scope__body {
  display: flex;
  flex-wrap: wrap;
  gap: var(--ep-space-3);
  align-items: flex-end;
  padding: 0 var(--ep-space-4) var(--ep-space-4);
}

.ep-scope__field {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-1);
  min-width: 160px;
  flex: 1 1 160px;
}

.ep-scope__label {
  font-size: 12px;
  color: var(--ep-color-text-muted);
}

.ep-scope__select {
  width: 100%;
  height: 34px;
  padding: 0 var(--ep-space-2);
  border: 1px solid var(--ep-color-border);
  border-radius: 8px;
  background: #fff;
  color: var(--ep-color-text);
  font-size: 13px;
}

.ep-scope__loading {
  margin: 0;
  color: var(--ep-color-text-muted);
  font-size: 13px;
}

.ep-scope__tags {
  display: flex;
  flex-wrap: wrap;
  gap: var(--ep-space-2);
  align-items: center;
  width: 100%;
}
</style>
