<script setup lang="ts">
/**
 * 学业规划页面（UI_SPEC 7）。
 *
 * 能力门控：``capabilities.planning === 'ready'`` 且后端可连接时才允许导入与计算；
 * 选项为空是合法 empty。页面**不**自动导入、不自动计算、不自动加载演示资料。
 *
 * 全部学分数字、缺口、缺失课程、冲突与证据都直接来自服务端 ``PlanningResult``；
 * 结果的归属由 ``resultRecordSetId`` / ``resultRuleSetId`` 决定，绝不用当前选择冒充。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter, type LocationQuery } from 'vue-router'

import type { PlanningEvidence } from '@/api/academic'
import { resolvePlanResultContext } from '@/domain/academic'
import ErrorAlert from '@/components/common/ErrorAlert.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import SourceDrawer from '@/components/common/SourceDrawer.vue'
import AcademicImportDialog from '@/components/academic/AcademicImportDialog.vue'
import AcademicSelectionPanel from '@/components/academic/AcademicSelectionPanel.vue'
import CategoryGapList from '@/components/academic/CategoryGapList.vue'
import ConflictWarningPanel from '@/components/academic/ConflictWarningPanel.vue'
import CreditSummary from '@/components/academic/CreditSummary.vue'
import MissingCourseList from '@/components/academic/MissingCourseList.vue'
import PlanningEvidencePanel from '@/components/academic/PlanningEvidencePanel.vue'
import { useAcademicStore } from '@/stores/academic'
import { useHealthStore } from '@/stores/health'

const route = useRoute()
const router = useRouter()
const health = useHealthStore()
const academic = useAcademicStore()

const recordsOpen = ref(false)
const rulesOpen = ref(false)
const evidenceOpen = ref(false)
const focusedChunkId = ref<string | null>(null)
const sourceOpen = ref(false)
const sourceChunkId = ref<string | null>(null)

const mediaQuery =
  typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia('(min-width: 1200px)')
    : null
const isWide = ref(mediaQuery ? mediaQuery.matches : true)

const connected = computed(() => health.connection === 'connected')
const backendDown = computed(() => health.connection === 'error')
const planningCapability = computed(() => health.capabilities?.planning ?? null)
const bootstrapping = computed(
  () =>
    health.connection === 'unknown' ||
    health.connection === 'loading' ||
    academic.optionsLoading,
)

const canImport = computed(
  () => connected.value && planningCapability.value === 'ready' && !bootstrapping.value,
)

const canCalculate = computed(
  () =>
    connected.value &&
    planningCapability.value === 'ready' &&
    !bootstrapping.value &&
    !academic.calculating &&
    academic.selectedRecordSetId !== null &&
    academic.selectedRuleSetId !== null,
)

const staleSelection = computed(
  () => academic.staleRecordSelection || academic.staleRuleSelection,
)

const resultContext = computed(() =>
  resolvePlanResultContext(academic.options, academic.resultRecordSetId, academic.resultRuleSetId),
)

const missingCourses = computed(() => academic.result?.missing_required_courses ?? [])
const categoryGaps = computed(() => academic.result?.category_gaps ?? [])
const conflictWarnings = computed(() => academic.result?.conflict_warnings ?? [])
const evidence = computed(() => academic.result?.evidence ?? [])
/** 成功结果（可能为 null）；模板中只通过它读取 ``PlanningResult`` 字段。 */
const result = computed(() => academic.result)

const importDisabledReason = computed(() => {
  if (backendDown.value) {
    return '后端服务不可连接，导入已禁用。'
  }
  if (planningCapability.value !== 'ready') {
    return '规划能力不可用（capabilities.planning 不是 ready），导入已禁用。'
  }
  return '正在读取规划数据，稍后再试。'
})

function syncQuery(): void {
  const current = route.query
  const next: LocationQuery = { ...current }

  const record = academic.selectedRecordSetId
  const rule = academic.selectedRuleSetId

  if (record) {
    next.record_set_id = record
  } else {
    delete next.record_set_id
  }
  if (rule) {
    next.rule_set_id = rule
  } else {
    delete next.rule_set_id
  }

  if (sameSelectionQuery(current, next)) {
    return
  }
  void router.replace({ query: next })
}

function sameSelectionQuery(left: LocationQuery, right: LocationQuery): boolean {
  return (
    stringOf(left.record_set_id) === stringOf(right.record_set_id) &&
    stringOf(left.rule_set_id) === stringOf(right.rule_set_id)
  )
}

function stringOf(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined
}

watch(
  () => [academic.selectedRecordSetId, academic.selectedRuleSetId] as const,
  () => syncQuery(),
)

async function recheck(): Promise<void> {
  await health.load()
  await academic.loadOptions()
  syncQuery()
}

async function onCalculate(): Promise<void> {
  await academic.calculate()
}

function onFocusEvidence(chunkId: string): void {
  focusedChunkId.value = chunkId
  if (!isWide.value) {
    evidenceOpen.value = true
  }
}

function onOpenSource(item: PlanningEvidence): void {
  sourceChunkId.value = item.chunk_id
  sourceOpen.value = true
}

async function bootstrap(): Promise<void> {
  const healthTask = health.connection === 'unknown' ? health.load() : Promise.resolve()
  await Promise.all([healthTask, academic.restoreSelection(route.query)])
  // 失效的 URL 字段必须被移除，即使两个选择都保持未选中
  syncQuery()
}

onMounted(() => {
  void bootstrap()
  if (mediaQuery) {
    mediaQuery.addEventListener('change', onMediaChange)
  }
})

onBeforeUnmount(() => {
  academic.cancel()
  if (mediaQuery) {
    mediaQuery.removeEventListener('change', onMediaChange)
  }
})

function onMediaChange(event: MediaQueryListEvent): void {
  isWide.value = event.matches
}
</script>

<template>
  <section class="ep-view ep-planning">
    <PageHeader title="学业规划" description="由确定性规则计算学分缺口与缺失必修课">
      <template #actions>
        <ElButton
          class="ep-planning__import-records"
          :disabled="!canImport"
          @click="recordsOpen = true"
        >
          导入课程记录
        </ElButton>
        <ElButton
          class="ep-planning__import-rules"
          :disabled="!canImport"
          @click="rulesOpen = true"
        >
          导入培养规则
        </ElButton>
      </template>
    </PageHeader>

    <p class="ep-planning__badge">结果由确定性规则引擎计算；AI 仅负责解释</p>

    <p v-if="bootstrapping" class="ep-planning__loading" role="status" aria-live="polite">
      正在读取后端能力与规划选项…
    </p>

    <ElAlert
      v-if="backendDown"
      class="ep-planning__backend-error"
      type="error"
      :closable="false"
      show-icon
      title="后端服务不可连接"
    >
      <p class="ep-planning__notice-text">
        规划数据无法读取，导入与计算已禁用；请重新检测后端服务。
      </p>
      <ElButton class="ep-planning__recheck" size="small" @click="recheck">重新检测</ElButton>
    </ElAlert>

    <ElAlert
      v-else-if="!bootstrapping && planningCapability !== 'ready'"
      class="ep-planning__capability"
      type="error"
      :closable="false"
      show-icon
      title="规划能力不可用"
    >
      <p class="ep-planning__notice-text">
        后端返回 capabilities.planning =
        {{ planningCapability ?? '未知' }}，导入与计算已禁用。
      </p>
      <ElButton class="ep-planning__recheck" size="small" @click="recheck">重新检测</ElButton>
    </ElAlert>

    <p v-if="staleSelection" class="ep-planning__stale-notice" role="status" aria-live="polite">
      之前选择的数据已失效，请重新选择
    </p>

    <AcademicSelectionPanel
      :options="academic.options"
      :loading="academic.optionsLoading"
      :error="academic.optionsError"
      :selected-record-set-id="academic.selectedRecordSetId"
      :selected-rule-set-id="academic.selectedRuleSetId"
      :disabled="!canImport"
      @select-record="academic.selectRecordSet($event)"
      @select-rule="academic.selectRuleSet($event)"
      @retry="academic.loadOptions()"
    />

    <div class="ep-planning__actions">
      <ElButton
        class="ep-planning__calculate"
        type="primary"
        :loading="academic.calculating"
        :disabled="!canCalculate"
        @click="onCalculate"
      >
        开始计算
      </ElButton>
      <span v-if="bootstrapping" class="ep-planning__actions-hint">正在读取规划数据…</span>
      <span v-else-if="planningCapability !== 'ready'" class="ep-planning__actions-hint">
        规划能力不可用，暂时无法计算。
      </span>
      <span
        v-else-if="academic.selectedRecordSetId === null || academic.selectedRuleSetId === null"
        class="ep-planning__actions-hint"
      >
        请先选择课程记录集与培养方案规则。
      </span>
    </div>

    <ErrorAlert
      v-if="academic.calculationError"
      class="ep-planning__calc-error"
      :error="academic.calculationError"
      title="计算失败"
    >
      <ElButton
        v-if="canCalculate"
        class="ep-planning__recalculate"
        size="small"
        @click="onCalculate"
      >
        重新计算
      </ElButton>
    </ErrorAlert>

    <p
      v-if="result && academic.resultNotUpdated"
      class="ep-planning__stale-result"
      aria-live="polite"
    >
      {{
        academic.calculationError
          ? '本次计算失败，当前结果未更新'
          : '当前结果未更新：所选数据与展示结果不一致'
      }}
    </p>

    <template v-if="result">
      <header class="ep-planning__result-context" aria-label="结果来源">
        <p class="ep-planning__result-record">
          课程记录：{{ resultContext.recordSet ? resultContext.recordSet.name : '数据来源已失效' }}
        </p>
        <p class="ep-planning__result-rule">
          培养方案：{{
            resultContext.ruleSet
              ? `${resultContext.ruleSet.name}（${resultContext.ruleSet.rule_version}）`
              : '数据来源已失效'
          }}
        </p>
        <p v-if="resultContext.resolvable" class="ep-planning__result-meta">
          专业 {{ resultContext.ruleSet?.major }} · 入学年份
          {{ resultContext.ruleSet?.admission_year }} · 规则版本
          {{ resultContext.ruleSet?.rule_version }}
        </p>
        <p v-else class="ep-planning__result-invalid">上一份结果的数据来源已失效</p>
      </header>

      <CreditSummary :result="result" />

      <div class="ep-planning__layout" :class="{ 'is-wide': isWide }">
        <div class="ep-planning__main">
          <CategoryGapList :gaps="categoryGaps" />
          <MissingCourseList
            :courses="missingCourses"
            :evidence="evidence"
            @focus-evidence="onFocusEvidence"
          />
        </div>

        <aside v-if="isWide" class="ep-planning__aside">
          <ConflictWarningPanel
            :warnings="conflictWarnings"
            :evidence="evidence"
            @focus-evidence="onFocusEvidence"
          />
          <PlanningEvidencePanel
            :evidence="evidence"
            :focused-chunk-id="focusedChunkId"
            @open-source="onOpenSource"
          />
        </aside>
      </div>

      <ElButton
        v-if="!isWide"
        class="ep-planning__evidence-button"
        @click="evidenceOpen = true"
      >
        查看计算证据
      </ElButton>
    </template>

    <ElDrawer v-if="!isWide" v-model="evidenceOpen" title="计算证据" size="480px">
      <div class="ep-planning__drawer">
        <ConflictWarningPanel
          :warnings="conflictWarnings"
          :evidence="evidence"
          @focus-evidence="onFocusEvidence"
        />
        <PlanningEvidencePanel
          :evidence="evidence"
          :focused-chunk-id="focusedChunkId"
          @open-source="onOpenSource"
        />
      </div>
    </ElDrawer>

    <AcademicImportDialog
      v-model="recordsOpen"
      kind="records"
      :disabled="!canImport"
      :disabled-reason="importDisabledReason"
    />
    <AcademicImportDialog
      v-model="rulesOpen"
      kind="rules"
      :disabled="!canImport"
      :disabled-reason="importDisabledReason"
    />

    <SourceDrawer v-model="sourceOpen" :chunk-id="sourceChunkId" />
  </section>
</template>

<style scoped>
.ep-planning {
  gap: var(--ep-space-4);
}

/* 固定标签：强调结果来自确定性规则引擎（UI_SPEC 7.5） */
.ep-planning__badge {
  display: inline-block;
  align-self: flex-start;
  margin: 0;
  padding: var(--ep-space-1) var(--ep-space-3);
  border: 1px solid var(--ep-color-evidence);
  border-radius: 999px;
  background: var(--ep-color-evidence-soft);
  color: var(--ep-color-evidence);
  font-size: 12px;
  font-weight: 600;
}

.ep-planning__loading,
.ep-planning__actions-hint,
.ep-planning__notice-text {
  margin: 0;
  color: var(--ep-color-text-muted);
  font-size: 13px;
}

.ep-planning__stale-notice {
  margin: 0;
  padding: var(--ep-space-2) var(--ep-space-3);
  border: 1px solid var(--ep-color-warning, #f59e0b);
  border-radius: var(--ep-radius-card);
  background: var(--ep-color-warning-soft, #fffbeb);
  color: var(--ep-color-warning-text, #b45309);
  font-size: 13px;
}

.ep-planning__actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--ep-space-3);
}

.ep-planning__stale-result {
  margin: 0;
  padding: var(--ep-space-2) var(--ep-space-3);
  border: 1px solid var(--ep-color-warning, #f59e0b);
  border-radius: var(--ep-radius-card);
  background: var(--ep-color-warning-soft, #fffbeb);
  color: var(--ep-color-warning-text, #b45309);
  font-size: 13px;
}

.ep-planning__result-context {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-1);
  padding: var(--ep-space-3) var(--ep-space-4);
  border: 1px solid var(--ep-color-border);
  border-radius: var(--ep-radius-card);
  background: var(--ep-color-surface-subtle);
}

.ep-planning__result-context p {
  margin: 0;
  font-size: 13px;
  overflow-wrap: anywhere;
}

.ep-planning__result-meta {
  color: var(--ep-color-text-muted);
  font-size: 12px;
}

.ep-planning__result-invalid {
  color: var(--ep-color-warning-text, #b45309);
  font-weight: 600;
}

.ep-planning__layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: var(--ep-space-5);
  align-items: start;
}

.ep-planning__layout.is-wide {
  grid-template-columns: minmax(0, 1fr) 360px;
}

.ep-planning__main {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-5);
  min-width: 0;
}

.ep-planning__aside {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-4);
  min-width: 0;
}

.ep-planning__drawer {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-5);
}

@media (max-width: 767px) {
  .ep-planning__actions {
    align-items: stretch;
  }

  .ep-planning__actions .el-button {
    width: 100%;
  }
}
</style>
