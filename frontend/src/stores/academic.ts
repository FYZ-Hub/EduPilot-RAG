/**
 * 学业规划状态机（PRODUCT_SPEC 6.4、UI_SPEC 7）。
 *
 * 只负责「用户显式选择 → 请求规划 → 保存服务端结果」，绝不计算学分：
 * - options 只来自真实 ``GET /api/academic/options``；空数组是合法 empty，不是错误；
 * - **绝不**自行挑选「最新 / 第一个 / 最后一个」选项；多选项时保持未选择，等待用户操作；
 * - 刷新 options 后，已消失的选择必须清除并标记 stale；
 * - 同一时间只允许一个 options 请求与一个规划请求；新请求取代旧请求，旧响应不得覆盖新状态；
 * - **结果上下文绑定**：成功结果同时保存它所属的 record / rule ID，只有当前选择与结果上下文
 *   完全一致时 ``resultNotUpdated`` 才为 false；选择一旦改变，在途请求立即作废（即使对方
 *   忽略 AbortSignal），旧结果保留但标记为「未更新」，切回原组合时恢复为「已更新」；
 * - 重新计算期间保留上一份成功结果；重算失败时结果保留并设置 ``resultNotUpdated=true``；
 * - 失败不自动重试；不调用 LLM / Chat / Embedding / Reranker。
 */

import { ref } from 'vue'
import { defineStore } from 'pinia'

import {
  calculateAcademicPlan,
  fetchAcademicOptions,
  importAcademicRecords,
  importAcademicRules,
  type AcademicOptions,
  type ImportResult,
  type PlanningResult,
} from '@/api/academic'
import { ApiError, toApiError } from '@/api/client'
import {
  readSelectionQuery,
  resolveSelectionInput,
  type AcademicSelection,
  type AcademicSelectionInput,
} from '@/domain/academic'

export const useAcademicStore = defineStore('academic', () => {
  const options = ref<AcademicOptions | null>(null)
  const optionsLoading = ref(false)
  const optionsError = ref<ApiError | null>(null)

  const selectedRecordSetId = ref<string | null>(null)
  const selectedRuleSetId = ref<string | null>(null)
  const staleRecordSelection = ref(false)
  const staleRuleSelection = ref(false)

  const result = ref<PlanningResult | null>(null)
  /** 当前 ``result`` 实际属于哪一组选择；没有成功结果时两者均为 null。 */
  const resultRecordSetId = ref<string | null>(null)
  const resultRuleSetId = ref<string | null>(null)
  const calculating = ref(false)
  const calculationError = ref<ApiError | null>(null)
  const resultNotUpdated = ref(false)

  const importingRecords = ref(false)
  const importingRules = ref(false)
  const importRecordsError = ref<ApiError | null>(null)
  const importRulesError = ref<ApiError | null>(null)

  let optionsController: AbortController | null = null
  let optionsSequence = 0
  let planController: AbortController | null = null
  let planSequence = 0
  /** 由 ``restoreSelection`` 注入的一次性 URL query，只被最新一次 ``loadOptions`` 消费。 */
  let pendingInput: AcademicSelectionInput | null = null
  /** 最近一次解析结果，供 ``restoreSelection`` 直接返回给页面。 */
  let lastResolution: AcademicSelection = {
    recordSetId: null,
    ruleSetId: null,
    staleRecordSelection: false,
    staleRuleSelection: false,
  }

  /** 是否已经有一份可归属的成功结果。 */
  function hasResultContext(): boolean {
    return result.value !== null && resultRecordSetId.value !== null && resultRuleSetId.value !== null
  }

  /** 当前选择是否与已知结果的上下文完全一致。 */
  function resultMatchesSelection(): boolean {
    return (
      hasResultContext() &&
      resultRecordSetId.value === selectedRecordSetId.value &&
      resultRuleSetId.value === selectedRuleSetId.value
    )
  }

  /** 没有成功结果时「未更新」无意义，必须保持 false；否则由上下文是否一致决定。 */
  function refreshResultNotUpdated(): void {
    resultNotUpdated.value = hasResultContext() && !resultMatchesSelection()
  }

  /** 选择或结果上下文变化后同步「未更新」标记。 */
  function syncResultForSelection(): void {
    calculationError.value = null
    refreshResultNotUpdated()
  }

  /** 作废在途规划请求：即使对方忽略 AbortSignal 并最终 resolve/reject 也不得写入状态。 */
  function invalidateInFlightPlan(): void {
    planSequence += 1
    planController?.abort()
    planController = null
    calculating.value = false
  }

  /** 用最新 options 与一份已解析的选择写回状态；选择有效性由纯函数判定。 */
  function applyResolution(next: AcademicOptions, resolution: AcademicSelection): void {
    const changed =
      resolution.recordSetId !== selectedRecordSetId.value ||
      resolution.ruleSetId !== selectedRuleSetId.value

    options.value = next
    selectedRecordSetId.value = resolution.recordSetId
    selectedRuleSetId.value = resolution.ruleSetId
    staleRecordSelection.value = resolution.staleRecordSelection
    staleRuleSelection.value = resolution.staleRuleSelection
    lastResolution = resolution

    // 选择完全没变时不得中止在途计算、也不得把结果标记为未更新
    if (changed) {
      invalidateInFlightPlan()
      syncResultForSelection()
    }
  }

  /** 当前选择作为一次「无 URL query」的解析输入。 */
  function currentSelectionInput(): AcademicSelectionInput {
    return {
      record: { value: selectedRecordSetId.value, malformed: false },
      rule: { value: selectedRuleSetId.value, malformed: false },
    }
  }

  /**
   * 载入可选上下文。新请求会中止旧请求，且旧响应一律不得覆盖新状态；
   * 取消（abort）不是错误，空 options 也不是错误。
   */
  async function loadOptions(): Promise<void> {
    optionsSequence += 1
    const sequence = optionsSequence
    optionsController?.abort()
    const controller = new AbortController()
    optionsController = controller

    optionsLoading.value = true

    try {
      const next = await fetchAcademicOptions(controller.signal)
      if (sequence !== optionsSequence) {
        return
      }
      const input = pendingInput ?? currentSelectionInput()
      pendingInput = null
      applyResolution(next, resolveSelectionInput(next, input))
      optionsError.value = null
    } catch (caught) {
      if (sequence !== optionsSequence) {
        return
      }
      const error = toApiError(caught)
      if (!error.isAborted) {
        optionsError.value = error
      }
    } finally {
      if (sequence === optionsSequence) {
        optionsLoading.value = false
      }
      if (optionsController === controller) {
        optionsController = null
      }
    }
  }

  function selectRecordSet(id: string | null): void {
    if (selectedRecordSetId.value === id) {
      return
    }
    selectedRecordSetId.value = id
    staleRecordSelection.value = false
    invalidateInFlightPlan()
    syncResultForSelection()
  }

  function selectRuleSet(id: string | null): void {
    if (selectedRuleSetId.value === id) {
      return
    }
    selectedRuleSetId.value = id
    staleRuleSelection.value = false
    invalidateInFlightPlan()
    syncResultForSelection()
  }

  /**
   * URL 恢复入口：用路由 query 重新解析选择。
   *
   * - 复用 ``resolveAcademicSelection``（经 ``resolveSelectionInput``），不在页面复制选择算法；
   * - 选项尚未加载时按该 query 先载入 options；
   * - 返回本次解析结果，供页面移除失效的 URL 字段并提示用户。
   */
  async function restoreSelection(raw: {
    record_set_id?: unknown
    rule_set_id?: unknown
  }): Promise<AcademicSelection> {
    const input = readSelectionQuery(raw)
    if (options.value === null) {
      pendingInput = input
      await loadOptions()
      return lastResolution
    }
    const resolution = resolveSelectionInput(options.value, input)
    applyResolution(options.value, resolution)
    return resolution
  }

  /** 中止在途的 options / 规划请求；空闲或重复调用都是安全的空操作，不算错误。 */
  function cancel(): void {
    optionsSequence += 1
    optionsController?.abort()
    optionsController = null
    optionsLoading.value = false

    planSequence += 1
    planController?.abort()
    planController = null
    calculating.value = false
  }

  /** 导入课程记录；成功后刷新 options，但**不**自动选择新项目。 */
  async function importRecords(file: File, name?: string): Promise<ImportResult | null> {
    if (importingRecords.value) {
      return null
    }
    importingRecords.value = true
    importRecordsError.value = null
    try {
      const imported = await importAcademicRecords(file, name)
      await loadOptions()
      return imported
    } catch (caught) {
      const error = toApiError(caught)
      if (!error.isAborted) {
        importRecordsError.value = error
      }
      return null
    } finally {
      importingRecords.value = false
    }
  }

  /** 导入培养方案规则；与课程记录完全分离，成功后同样只刷新而不自动选择。 */
  async function importRules(file: File, name?: string): Promise<ImportResult | null> {
    if (importingRules.value) {
      return null
    }
    importingRules.value = true
    importRulesError.value = null
    try {
      const imported = await importAcademicRules(file, name)
      await loadOptions()
      return imported
    } catch (caught) {
      const error = toApiError(caught)
      if (!error.isAborted) {
        importRulesError.value = error
      }
      return null
    } finally {
      importingRules.value = false
    }
  }

  /**
   * 计算规划。两个 ID 不齐全时安全返回、不发请求；同一时间只允许一个请求；
   * 成功时同时记录结果与它所属的 record / rule 上下文，且**只有**请求序号仍是最新
   * 且当前选择仍等于本次请求的两个 ID 时才允许写入。
   */
  async function calculate(): Promise<void> {
    if (calculating.value) {
      return
    }
    const recordSetId = selectedRecordSetId.value
    const ruleSetId = selectedRuleSetId.value
    if (!recordSetId || !ruleSetId) {
      return
    }

    planSequence += 1
    const sequence = planSequence
    const controller = new AbortController()
    planController = controller
    calculating.value = true

    /** 旧响应（序号过期）或选择已改变时，一律不得覆盖当前状态。 */
    const responseStillApplies = (): boolean =>
      sequence === planSequence &&
      selectedRecordSetId.value === recordSetId &&
      selectedRuleSetId.value === ruleSetId

    try {
      const next = await calculateAcademicPlan(
        { record_set_id: recordSetId, rule_set_id: ruleSetId },
        controller.signal,
      )
      if (!responseStillApplies()) {
        return
      }
      result.value = next
      resultRecordSetId.value = recordSetId
      resultRuleSetId.value = ruleSetId
      resultNotUpdated.value = false
      calculationError.value = null
    } catch (caught) {
      if (!responseStillApplies()) {
        return
      }
      const error = toApiError(caught)
      if (!error.isAborted) {
        calculationError.value = error
        if (result.value !== null) {
          resultNotUpdated.value = true
        }
      }
    } finally {
      if (sequence === planSequence) {
        calculating.value = false
      }
      if (planController === controller) {
        planController = null
      }
    }
  }

  return {
    options,
    optionsLoading,
    optionsError,
    selectedRecordSetId,
    selectedRuleSetId,
    staleRecordSelection,
    staleRuleSelection,
    result,
    resultRecordSetId,
    resultRuleSetId,
    calculating,
    calculationError,
    resultNotUpdated,
    importingRecords,
    importingRules,
    importRecordsError,
    importRulesError,
    loadOptions,
    restoreSelection,
    selectRecordSet,
    selectRuleSet,
    cancel,
    importRecords,
    importRules,
    calculate,
  }
})
