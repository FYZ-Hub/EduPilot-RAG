<script setup lang="ts">
/**
 * 缺失必修课（UI_SPEC 7.4）：桌面为表格，窄屏由 CSS 转为卡片；内容只读，不可编辑。
 *
 * ``evidence_chunk_ids`` 只关联 ``result.evidence`` 中真实存在的 chunk：
 * 找不到的 ID 不会创建任何虚构证据。
 */
import type { MissingRequiredCourse, PlanningEvidence } from '@/api/academic'
import { formatCredit, linkEvidence } from '@/domain/academic'

const props = defineProps<{
  courses: MissingRequiredCourse[]
  evidence: PlanningEvidence[]
}>()

const emit = defineEmits<{ 'focus-evidence': [chunkId: string] }>()

function linked(course: MissingRequiredCourse): PlanningEvidence[] {
  return linkEvidence(props.evidence, course.evidence_chunk_ids)
}
</script>

<template>
  <section class="ep-missing" aria-label="缺失必修课">
    <h2 class="ep-missing__title">缺失必修课</h2>

    <p v-if="courses.length === 0" class="ep-missing__empty">已满足当前规则中的必修课要求</p>

    <table v-else class="ep-missing__table">
      <caption class="ep-missing__caption">按当前培养规则仍缺失的必修课</caption>
      <thead>
        <tr>
          <th scope="col">课程代码</th>
          <th scope="col">课程名称</th>
          <th scope="col">学分</th>
          <th scope="col">类别</th>
          <th scope="col">证据</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="course in courses" :key="course.course_code" class="ep-missing__row">
          <td data-label="课程代码">{{ course.course_code }}</td>
          <td data-label="课程名称">{{ course.course_name }}</td>
          <td data-label="学分" class="ep-missing__credits">{{ formatCredit(course.credits) }}</td>
          <td data-label="类别">{{ course.category }}</td>
          <td data-label="证据">
            <span v-if="linked(course).length === 0" class="ep-missing__no-evidence">暂无关联证据</span>
            <button
              v-for="item in linked(course)"
              :key="item.chunk_id"
              type="button"
              class="ep-evidence-chip"
              :data-chunk-id="item.chunk_id"
              @click="emit('focus-evidence', item.chunk_id)"
            >
              查看证据
            </button>
          </td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<style scoped>
.ep-missing {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-3);
  min-width: 0;
}

.ep-missing__title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--ep-color-text-secondary);
}

.ep-missing__empty {
  margin: 0;
  padding: var(--ep-space-4);
  border: 1px dashed var(--ep-color-border);
  border-radius: var(--ep-radius-card);
  color: var(--ep-color-text-muted);
  font-size: 13px;
  text-align: center;
}

.ep-missing__table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.ep-missing__caption {
  padding-bottom: var(--ep-space-2);
  color: var(--ep-color-text-muted);
  font-size: 12px;
  text-align: left;
}

.ep-missing__table th,
.ep-missing__table td {
  padding: var(--ep-space-2);
  border-bottom: 1px solid var(--ep-color-border-light);
  text-align: left;
  vertical-align: top;
  overflow-wrap: anywhere;
}

.ep-missing__credits {
  font-variant-numeric: tabular-nums;
}

.ep-evidence-chip {
  min-height: 28px;
  padding: 0 var(--ep-space-2);
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

.ep-missing__no-evidence {
  color: var(--ep-color-text-muted);
  font-size: 12px;
}

/* 窄屏：表格转为卡片（保留语义化 table 标记，不复制第二套内容） */
@media (max-width: 767px) {
  .ep-missing__table thead {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
  }

  .ep-missing__table,
  .ep-missing__table tbody,
  .ep-missing__row,
  .ep-missing__table td {
    display: block;
    width: 100%;
  }

  .ep-missing__row {
    margin-bottom: var(--ep-space-3);
    padding: var(--ep-space-3);
    border: 1px solid var(--ep-color-border);
    border-radius: var(--ep-radius-card);
  }

  .ep-missing__table td {
    display: flex;
    gap: var(--ep-space-2);
    border-bottom: 0;
    padding: var(--ep-space-1) 0;
  }

  .ep-missing__table td::before {
    min-width: 72px;
    color: var(--ep-color-text-muted);
    content: attr(data-label);
  }
}
</style>
