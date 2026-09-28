/**
 * 学业规划的确定性展示辅助（纯函数，PRODUCT_SPEC 5.2 / UI_SPEC 7）。
 *
 * 职责边界：
 * - 从 ``options`` 与 URL query 恢复用户选择：**绝不**静默挑选「最新」或任意一项；
 * - 校验与归一化学分展示比例：**只**生成显示用比例，绝不写回或改写 ``PlanningResult``；
 * - 全部函数不读写浏览器 URL、不访问网络、不产生副作用，相同输入必然产生相同输出。
 */

import type { AcademicOptions, PlanningResult } from '@/api/academic'

/** 后端唯一可供选择的就绪状态（``app.constants.STATUS_READY``）。 */
export const ACADEMIC_READY_STATUS = 'ready'

/** URL query 中可恢复的两个选择键；缺失或为 null 都视为「未提供」。 */
export interface AcademicSelectionQuery {
  record_set_id?: string | null
  rule_set_id?: string | null
}

/** 恢复结果；``stale*`` 表示 query 里的选择已失效（可供页面提示「选择已失效」）。 */
export interface AcademicSelection {
  recordSetId: string | null
  ruleSetId: string | null
  staleRecordSelection: boolean
  staleRuleSelection: boolean
}

export type AcademicProgressSegmentKey = 'completed' | 'in_progress' | 'remaining'

export interface AcademicProgressSegment {
  key: AcademicProgressSegmentKey
  percent: number
}

export interface AcademicProgressBreakdown {
  exceeded: boolean
  segments: AcademicProgressSegment[]
  totalPercent: number
}

export interface AcademicCreditSummary {
  valid: boolean
  message: string | null
  exceeded: boolean
}

const PROGRESS_KEYS: readonly AcademicProgressSegmentKey[] = [
  'completed',
  'in_progress',
  'remaining',
]

interface OptionLike {
  id: string
  status: string
}

/**
 * 解析单个维度（record 或 rule）的选择：
 *
 * 1. query ID 存在于对应 options 中 → 保留；
 * 2. query ID 不存在 → 清除并标记 ``stale``（绝不自动改选其它项）；
 * 3. 未提供 query 且恰好只有一个 ready 选项 → 预选它；
 * 4. 其余情况（0 个或多于 1 个候选）→ 保持未选择。
 *
 * record 与 rule 独立判断，互不影响。
 */
function resolveOne(
  items: readonly OptionLike[],
  queryId: string | null | undefined,
): { id: string | null; stale: boolean } {
  if (typeof queryId === 'string' && queryId.length > 0) {
    if (items.some((item) => item.id === queryId)) {
      return { id: queryId, stale: false }
    }
    return { id: null, stale: true }
  }
  const readyIds = items
    .filter((item) => item.status === ACADEMIC_READY_STATUS)
    .map((item) => item.id)
  if (readyIds.length === 1) {
    return { id: readyIds[0], stale: false }
  }
  return { id: null, stale: false }
}

/** 由 options 与 URL query 确定性地恢复选择；不修改入参，输出可重复。 */
export function resolveAcademicSelection(
  options: AcademicOptions,
  query: AcademicSelectionQuery,
): AcademicSelection {
  const record = resolveOne(options.record_sets, query.record_set_id)
  const rule = resolveOne(options.rule_sets, query.rule_set_id)
  return {
    recordSetId: record.id,
    ruleSetId: rule.id,
    staleRecordSelection: record.stale,
    staleRuleSelection: rule.stale,
  }
}

/**
 * 校验四个学分字段：必须是有限非负数。
 *
 * - ``required=0`` 且存在非零进度（已修 / 在修）→ 数据错误；
 * - ``completed + in_progress`` 超过 ``required`` 不算错误，仅标记 ``exceeded=true``；
 * - 绝不修正或写回任何数字。
 */
export function summariseCredits(result: PlanningResult): AcademicCreditSummary {
  const fields = [
    ['required_credits', result.required_credits],
    ['completed_credits', result.completed_credits],
    ['in_progress_credits', result.in_progress_credits],
    ['remaining_credits', result.remaining_credits],
  ] as const

  for (const [name, value] of fields) {
    if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) {
      return { valid: false, message: `学分字段「${name}」不是有效的非负有限数`, exceeded: false }
    }
  }

  const progress = result.completed_credits + result.in_progress_credits
  if (result.required_credits === 0 && progress > 0) {
    return {
      valid: false,
      message: '毕业总学分为 0 时不应存在已修或在修学分',
      exceeded: false,
    }
  }

  return { valid: true, message: null, exceeded: progress > result.required_credits }
}

/** 只取有限正数用于展示；其余（0 / 负数 / NaN / Infinity）一律按 0 处理。 */
function displayCredit(value: number): number {
  return Number.isFinite(value) && value > 0 ? value : 0
}

/**
 * 生成三段组合进度条的展示比例（占总宽度的百分比）：
 *
 * - ``completed`` 段封顶 ``required``（最多 100%）；
 * - ``in_progress`` 段封顶剩余空间（不越过 100% 总宽）；
 * - ``remaining`` 段补足到 ``required``；
 * - 总宽恒不超过 100%，且不改写 ``PlanningResult``。
 */
export function buildProgressBreakdown(result: PlanningResult): AcademicProgressBreakdown {
  const required = displayCredit(result.required_credits)
  const completed = displayCredit(result.completed_credits)
  const inProgress = displayCredit(result.in_progress_credits)

  if (required === 0) {
    return {
      exceeded: false,
      totalPercent: 0,
      segments: PROGRESS_KEYS.map((key) => ({ key, percent: 0 })),
    }
  }

  const completedPercent = Math.min((completed / required) * 100, 100)
  const inProgressPercent = Math.min(
    (inProgress / required) * 100,
    Math.max(100 - completedPercent, 0),
  )
  const remainingPercent = Math.max(100 - completedPercent - inProgressPercent, 0)

  return {
    exceeded: completed + inProgress > required,
    totalPercent: completedPercent + inProgressPercent + remainingPercent,
    segments: [
      { key: 'completed', percent: completedPercent },
      { key: 'in_progress', percent: inProgressPercent },
      { key: 'remaining', percent: remainingPercent },
    ],
  }
}
