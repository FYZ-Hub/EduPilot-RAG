/**
 * 检索选项与来源 API（PRODUCT_SPEC 6.3、UI_SPEC 6.3 / 6.6）。
 *
 * 选项全部来自 ``GET /api/retrieval/options`` 的真实聚合结果，**不硬编码**任何取值；
 * ``grade_year`` 在前后端都保持整数（``number``），提交时原样回传。
 */

import { getJson } from './client'
import type { FileType } from './documents'

export interface DocCategoryOption {
  value: string
  label: string
}

export interface RetrievalOptions {
  majors: string[]
  grade_years: number[]
  semesters: string[]
  doc_categories: DocCategoryOption[]
  active_dataset_version: string | null
  demo_available: boolean
}

/** ``GET /api/sources/{chunk_id}`` 的完整字段与定位信息。 */
export interface SourceDetail {
  chunk_id: string
  doc_id: string
  file_name: string
  file_type: FileType
  document_version: string | null
  effective_from: string | null
  dataset_version: string | null
  text: string
  page_number: number | null
  sheet_name: string | null
  row_start: number | null
  row_end: number | null
  section_title: string | null
}

export function fetchRetrievalOptions(signal?: AbortSignal): Promise<RetrievalOptions> {
  return getJson<RetrievalOptions>('/retrieval/options', signal)
}

export function fetchSource(chunkId: string, signal?: AbortSignal): Promise<SourceDetail> {
  return getJson<SourceDetail>(`/sources/${encodeURIComponent(chunkId)}`, signal)
}
