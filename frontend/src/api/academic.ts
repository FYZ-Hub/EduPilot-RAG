/**
 * 学业规划接口 DTO 与调用（PRODUCT_SPEC 5.2 / 6.4；backend/app/api/academic.py）。
 *
 * 字段严格对应后端真实返回，不增删、不补默认值：
 * - ``GET /api/academic/options`` 顶层只有 ``record_sets`` 与 ``rule_sets``，不返回默认选中项；
 * - ``POST /api/academic/records/import`` 只接受 XLSX，``POST /api/academic/rules/import``
 *   接受 PDF / DOCX / XLSX；两者都是固定 ``multipart/form-data``（``file`` 必填、``name`` 可选），
 *   导入结果严格为 ``{id, status, warnings}``；
 * - ``POST /api/academic/plan`` 请求体**只允许**两个显式 ID，响应直接是 ``PlanningResult``，
 *   数字只能来自后端确定性引擎 —— 客户端绝不重算、修正或覆盖任何数字。
 */

import { getJson, postForm, postJson } from './client'

/** ``GET /api/academic/options`` 的课程记录集合（backend RECORD_SET_FIELDS）。 */
export interface RecordSetOption {
  id: string
  name: string
  source_doc_id: string
  status: string
  updated_at: string | null
}

/** ``GET /api/academic/options`` 的培养方案规则集合（backend RULE_SET_FIELDS）。 */
export interface RuleSetOption {
  id: string
  name: string
  major: string
  admission_year: number
  rule_version: string
  effective_from: string | null
  source_doc_id: string
  status: string
}

export interface AcademicOptions {
  record_sets: RecordSetOption[]
  rule_sets: RuleSetOption[]
}

/** 导入成功响应；字段严格为 ``{id, status, warnings}``。 */
export interface ImportResult {
  id: string
  status: string
  warnings: string[]
}

/** ``POST /api/academic/plan`` 的请求体；后端 ``extra="forbid"``，因此**只有**这两个字段。 */
export interface PlanRequest {
  record_set_id: string
  rule_set_id: string
}

/** PRODUCT_SPEC 5.2 ``MissingRequiredCourse``。 */
export interface MissingRequiredCourse {
  course_code: string
  course_name: string
  credits: number
  category: string
  evidence_chunk_ids: string[]
}

/** PRODUCT_SPEC 5.2 ``CategoryGap``。 */
export interface CategoryGap {
  category: string
  required_credits: number
  completed_credits: number
  in_progress_credits: number
  remaining_credits: number
}

/** PRODUCT_SPEC 5.2 ``ConflictWarning``。 */
export interface ConflictWarning {
  code: string
  message: string
  severity: string
  evidence_chunk_ids: string[]
}

/** PRODUCT_SPEC 5.2 ``PlanningEvidence``（只含白名单元数据与真实 chunk 文本）。 */
export interface PlanningEvidence {
  chunk_id: string
  doc_id: string
  file_name: string
  document_version: string | null
  effective_from: string | null
  page_number: number | null
  sheet_name: string | null
  row_start: number | null
  row_end: number | null
  section_title: string | null
  quote: string
}

/** PRODUCT_SPEC 5.2 ``PlanningResult``；顶层字段严格且仅为这 8 项。 */
export interface PlanningResult {
  required_credits: number
  completed_credits: number
  in_progress_credits: number
  remaining_credits: number
  missing_required_courses: MissingRequiredCourse[]
  category_gaps: CategoryGap[]
  conflict_warnings: ConflictWarning[]
  evidence: PlanningEvidence[]
}

/** 课程记录客户端允许扩展名（服务端仍执行最终校验）。 */
export const RECORD_IMPORT_EXTENSIONS = ['xlsx'] as const
/** 培养规则客户端允许扩展名。 */
export const RULE_IMPORT_EXTENSIONS = ['pdf', 'docx', 'xlsx'] as const

export function fetchAcademicOptions(signal?: AbortSignal): Promise<AcademicOptions> {
  return getJson<AcademicOptions>('/academic/options', signal)
}

/**
 * 构造 multipart 表单：``file`` 必填；``name`` 只在其去空白后非空时才发送，
 * 绝不发送空白 name。不手动设置 ``Content-Type``（由浏览器补 boundary）。
 */
function buildImportForm(file: File, name?: string): FormData {
  const form = new FormData()
  form.append('file', file, file.name)
  const trimmed = name?.trim()
  if (trimmed) {
    form.append('name', trimmed)
  }
  return form
}

/** 导入课程记录集合（仅 XLSX）；必须走 academic 端点，不能进入普通 RAG 上传流水线。 */
export function importAcademicRecords(file: File, name?: string): Promise<ImportResult> {
  return postForm<ImportResult>('/academic/records/import', buildImportForm(file, name))
}

/** 导入培养方案规则集合（PDF / DOCX / XLSX）。 */
export function importAcademicRules(file: File, name?: string): Promise<ImportResult> {
  return postForm<ImportResult>('/academic/rules/import', buildImportForm(file, name))
}

/** 按用户显式选择的两个 ID 计算规划；请求体**严格**只有两个字段。 */
export function calculateAcademicPlan(
  request: PlanRequest,
  signal?: AbortSignal,
): Promise<PlanningResult> {
  return postJson<PlanningResult>(
    '/academic/plan',
    { record_set_id: request.record_set_id, rule_set_id: request.rule_set_id },
    signal,
  )
}
