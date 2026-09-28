/**
 * 学业规划状态机（PRODUCT_SPEC 6.4、UI_SPEC 7）。
 *
 * 只负责「用户显式选择 → 请求规划 → 保存服务端结果」，绝不计算学分：
 * - options 只来自真实 ``GET /api/academic/options``；空数组是合法 empty，不是错误；
 * - **绝不**自行挑选「最新 / 第一个 / 最后一个」选项；多选项时保持未选择，等待用户操作；
 * - 刷新 options 后，已消失的选择必须清除并标记 stale；
 * - 同一时间只允许一个 options 请求与一个规划请求；新请求取代旧请求，旧响应不得覆盖新状态；
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
  resolveAcademicSelection,
  type AcademicSelectionQuery,
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

  /** 用最新 options 与当前选择恢复状态；选择有效性由纯函数判定。 */
  function applyOptions(next: AcademicOptions, query: AcademicSelectionQuery): void {
    const resolution = resolveAcademicSelection(next, query)
    options.value = next
    selectedRecordSetId.value = resolution.recordSetId
    selectedRuleSetId.value = resolution.ruleSetId
    staleRecordSelection.value = resolution.staleRecordSelection
    staleRuleSelection.value = resolution.staleRuleSelection
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

    const currentSelection = {
      record_set_id: selectedRecordSetId.value,
      rule_set_id: selectedRuleSetId.value,
    }
    optionsLoading.value = true

    try {
      const next = await fetchAcademicOptions(controller.signal)
      if (sequence !== optionsSequence) {
        return
      }
      applyOptions(next, currentSelection)
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
    selectedRecordSetId.value = id
    staleRecordSelection.value = false
  }

  function selectRuleSet(id: string | null): void {
    selectedRuleSetId.value = id
    staleRuleSelection.value = false
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
   * 成功时原样保存服务端结果，重算失败时保留上一份结果并标记「未更新」。
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

    try {
      const next = await calculateAcademicPlan(
        { record_set_id: recordSetId, rule_set_id: ruleSetId },
        controller.signal,
      )
      if (sequence !== planSequence) {
        return
      }
      result.value = next
      resultNotUpdated.value = false
      calculationError.value = null
    } catch (caught) {
      if (sequence !== planSequence) {
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
    calculating,
    calculationError,
    resultNotUpdated,
    importingRecords,
    importingRules,
    importRecordsError,
    importRulesError,
    loadOptions,
    selectRecordSet,
    selectRuleSet,
    cancel,
    importRecords,
    importRules,
    calculate,
  }
})
