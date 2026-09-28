/**
 * 学业规划的确定性展示辅助（纯函数，PRODUCT_SPEC 5.2 / UI_SPEC 7）。
 *
 * 职责边界：
 * - 从 ``options`` 与 URL query 恢复用户选择：**绝不**静默挑选「最新」或任意一项；
 * - 校验与归一化学分展示比例：**只**生成显示用比例，绝不写回或改写 ``PlanningResult``；
 * - 全部函数不读写浏览器 URL、不访问网络、不产生副作用，相同输入必然产生相同输出。
 */

import type {
  AcademicOptions,
  CategoryGap,
  PlanningEvidence,
  PlanningResult,
  RecordSetOption,
  RuleSetOption,
} from '@/api/academic'

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

export interface AcademicCreditSummary {
  valid: boolean
  message: string | null
  exceeded: boolean
}

/** 从 URL query 归一化出的单字段：``value`` 为可用于匹配的 ID，``malformed`` 表示该字段存在但非法。 */
export interface AcademicQueryField {
  value: string | null
  malformed: boolean
}

export interface AcademicSelectionInput {
  record: AcademicQueryField
  rule: AcademicQueryField
}

/** 结果实际所属的课程记录与培养规则；选项已消失时为 null，绝不回退到当前选择。 */
export interface PlanResultContext {
  recordSet: RecordSetOption | null
  ruleSet: RuleSetOption | null
  resolvable: boolean
}

export type ConflictGroup = 'version' | 'time' | 'record' | 'other'

export interface SeverityDescriptor {
  tone: 'danger' | 'warning' | 'info'
  label: string
  blocking: boolean
}

/** 各维度在组合进度条中的展示比例；``totalPercent`` 恒不超过 100。 */
export interface AcademicProgressBreakdown {
  exceeded: boolean
  segments: AcademicProgressSegment[]
  totalPercent: number
  /** 已修 + 在修占总宽度的百分比（封顶 100），供进度条 aria-valuenow 使用。 */
  progressPercent: number
}

export interface CategoryGapBreakdown extends AcademicProgressBreakdown {
  valid: boolean
  message: string | null
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
 * 归一化路由 query：缺失（``undefined`` / ``null``）表示未提供；
 * 空字符串、数组或非字符串一律**按无效处理**（``malformed``）。
 */
export function readSelectionQuery(raw: {
  record_set_id?: unknown
  rule_set_id?: unknown
}): AcademicSelectionInput {
  return { record: readField(raw.record_set_id), rule: readField(raw.rule_set_id) }
}

function readField(value: unknown): AcademicQueryField {
  if (value === undefined || value === null) {
    return { value: null, malformed: false }
  }
  if (typeof value !== 'string') {
    return { value: null, malformed: true }
  }
  const trimmed = value.trim()
  if (trimmed === '') {
    return { value: null, malformed: true }
  }
  return { value: trimmed, malformed: false }
}

/** 用归一化后的 query 解析选择；非法字段与失效 ID 一样计入 ``stale``。 */
export function resolveSelectionInput(
  options: AcademicOptions,
  input: AcademicSelectionInput,
): AcademicSelection {
  const resolution = resolveAcademicSelection(options, {
    record_set_id: input.record.value,
    rule_set_id: input.rule.value,
  })
  return {
    recordSetId: resolution.recordSetId,
    ruleSetId: resolution.ruleSetId,
    staleRecordSelection: resolution.staleRecordSelection || input.record.malformed,
    staleRuleSelection: resolution.staleRuleSelection || input.rule.malformed,
  }
}

const CONFLICT_GROUPS: Record<string, ConflictGroup> = {
  DEGREE_PLAN_VERSION_CONFLICT: 'version',
  COURSE_TIME_CONFLICT: 'time',
  COURSE_RECORD_CONTRADICTION: 'record',
}

const CONFLICT_GROUP_LABELS: Record<ConflictGroup, string> = {
  version: '规则版本冲突',
  time: '课表冲突',
  record: '课程记录冲突',
  other: '其他提醒',
}

/** 依据服务端真实 code 分类；未知 code 一律进入 ``other`` 且仍然展示。 */
export function conflictGroup(code: string): ConflictGroup {
  return CONFLICT_GROUPS[code] ?? 'other'
}

export function conflictGroupLabel(group: ConflictGroup): string {
  return CONFLICT_GROUP_LABELS[group]
}

/** severity 同时给出文字标签，避免只靠颜色表达状态。 */
export function describeSeverity(severity: string): SeverityDescriptor {
  if (severity === 'blocking') {
    return { tone: 'danger', label: '阻断', blocking: true }
  }
  if (severity === 'warning') {
    return { tone: 'warning', label: '提醒', blocking: false }
  }
  return { tone: 'info', label: severity, blocking: false }
}

/**
 * 解析结果实际所属的课程记录与培养规则。
 *
 * 只依据 ``resultRecordSetId`` / ``resultRuleSetId`` 与 ``options`` 匹配，
 * **绝不**回退到当前选择；选项已消失时对应字段为 null。
 */
export function resolvePlanResultContext(
  options: AcademicOptions | null,
  recordSetId: string | null,
  ruleSetId: string | null,
): PlanResultContext {
  const recordSet =
    recordSetId === null
      ? null
      : (options?.record_sets.find((item) => item.id === recordSetId) ?? null)
  const ruleSet =
    ruleSetId === null ? null : (options?.rule_sets.find((item) => item.id === ruleSetId) ?? null)
  return { recordSet, ruleSet, resolvable: recordSet !== null && ruleSet !== null }
}

/**
 * 把 ``evidence_chunk_ids`` 关联到真实证据卡：只返回确实存在于 ``evidence`` 中的项，
 * 按给定顺序去重；找不到的 ID 直接丢弃，**绝不**新建虚构证据。
 */
export function linkEvidence(
  evidence: readonly PlanningEvidence[],
  chunkIds: readonly string[],
): PlanningEvidence[] {
  const byId = new Map(evidence.map((item) => [item.chunk_id, item]))
  const linked: PlanningEvidence[] = []
  const seen = new Set<string>()
  for (const id of chunkIds) {
    if (seen.has(id)) {
      continue
    }
    seen.add(id)
    const item = byId.get(id)
    if (item) {
      linked.push(item)
    }
  }
  return linked
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

/** 三段比例（已修 / 在修 / 剩余）的唯一计算点：总学分与类别缺口共用。 */
export function computeBreakdown(
  requiredInput: number,
  completedInput: number,
  inProgressInput: number,
): AcademicProgressBreakdown {
  const required = displayCredit(requiredInput)
  const completed = displayCredit(completedInput)
  const inProgress = displayCredit(inProgressInput)

  if (required === 0) {
    return {
      exceeded: false,
      totalPercent: 0,
      progressPercent: 0,
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
    progressPercent: completedPercent + inProgressPercent,
    segments: [
      { key: 'completed', percent: completedPercent },
      { key: 'in_progress', percent: inProgressPercent },
      { key: 'remaining', percent: remainingPercent },
    ],
  }
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
  return computeBreakdown(
    result.required_credits,
    result.completed_credits,
    result.in_progress_credits,
  )
}

/** 学分展示统一保留一位小数；非法值原样展示，绝不擅自纠正。 */
export function formatCredit(value: number): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return String(value)
  }
  return value.toFixed(1)
}

/**
 * 类别缺口的展示比例：复用同一套三段算法，**不**重新计算服务端 ``remaining_credits``；
 * 任一字段非法时就地返回错误信息与全 0 的比例。
 */
export function buildCategoryGapBreakdown(gap: CategoryGap): CategoryGapBreakdown {
  const fields = [
    ['required_credits', gap.required_credits],
    ['completed_credits', gap.completed_credits],
    ['in_progress_credits', gap.in_progress_credits],
    ['remaining_credits', gap.remaining_credits],
  ] as const

  for (const [name, value] of fields) {
    if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) {
      return {
        valid: false,
        message: `类别「${gap.category}」的字段「${name}」不是有效的非负有限数`,
        ...computeBreakdown(0, 0, 0),
      }
    }
  }

  return {
    valid: true,
    message: null,
    ...computeBreakdown(gap.required_credits, gap.completed_credits, gap.in_progress_credits),
  }
}
