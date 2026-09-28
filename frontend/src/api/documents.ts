/**
 * 文档接口 DTO 与调用（PRODUCT_SPEC 6.2、UI_SPEC 5.4–5.6）。
 *
 * 字段严格对应 backend/app/documents/service.py 的真实返回，不做任何猜测或补默认值。
 */

import { deleteRequest, getJson, postForm } from './client'

export type FileType = 'pdf' | 'docx' | 'xlsx'
export type SourceType = 'demo' | 'upload'
export type ActivationState = 'active' | 'candidate' | 'inactive'

/** 后端文档处理状态（app/constants.py STATUS_*）。 */
export type DocumentStatus =
  | 'queued'
  | 'validating'
  | 'storing'
  | 'parsing'
  | 'chunking'
  | 'embedding'
  | 'vector_indexing'
  | 'keyword_indexing'
  | 'ready'
  | 'failed'

/** 上传结果分类（app/constants.py DISPOSITION_*）。 */
export type UploadDisposition = 'created' | 'existing_ready' | 'attached' | 'retry_started'

export interface DocumentErrorInfo {
  code: string
  message: string | null
  retryable: boolean
}

export interface DocumentListItem {
  id: string
  file_name: string
  file_type: FileType
  doc_category: string
  source_type: SourceType
  dataset_version: string | null
  status: DocumentStatus
  retrievable: boolean
  activation_state: ActivationState | null
  current_stage: string | null
  chunk_count: number
  block_count: number
  locator_types: string[]
  created_at: string | null
  updated_at: string | null
  error: DocumentErrorInfo | null
}

export interface DocumentDetail extends DocumentListItem {
  document_version: string | null
  effective_from: string | null
  checksum: string
}

export interface DocumentCounts {
  ready: number
  retrievable: number
  processing: number
  failed: number
}

export interface DocumentListPayload {
  items: DocumentListItem[]
  total: number
  counts: DocumentCounts
}

export interface DocumentUploadResult {
  document_id: string
  status: DocumentStatus
  status_url: string
  deduplicated: boolean
  disposition: UploadDisposition
}

export interface PreviewBlock {
  block_index: number
  block_type: string
  text: string
  page_number: number | null
  sheet_name: string | null
  row_start: number | null
  row_end: number | null
  section_title: string | null
  metadata: Record<string, unknown> | null
}

export interface DocumentPreview {
  document_id: string
  file_name: string
  file_type: FileType
  status: DocumentStatus
  total_blocks: number
  returned_blocks: number
  truncated: boolean
  blocks: PreviewBlock[]
}

/** 客户端预检使用的允许扩展名（服务端仍执行最终校验）。 */
export const ALLOWED_UPLOAD_EXTENSIONS = ['pdf', 'docx', 'xlsx'] as const
/** 单文件上限，与后端 max_upload_mb 默认值一致。 */
export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024

export function listDocuments(signal?: AbortSignal): Promise<DocumentListPayload> {
  return getJson<DocumentListPayload>('/documents', signal)
}

export function getDocument(documentId: string, signal?: AbortSignal): Promise<DocumentDetail> {
  return getJson<DocumentDetail>(`/documents/${documentId}`, signal)
}

export function getDocumentStatus(
  documentId: string,
  signal?: AbortSignal,
): Promise<DocumentListItem> {
  return getJson<DocumentListItem>(`/documents/${documentId}/status`, signal)
}

export function getDocumentPreview(
  documentId: string,
  signal?: AbortSignal,
): Promise<DocumentPreview> {
  return getJson<DocumentPreview>(`/documents/${documentId}/preview`, signal)
}

export function deleteDocument(documentId: string, signal?: AbortSignal): Promise<void> {
  return deleteRequest<void>(`/documents/${documentId}`, signal)
}

export function uploadDocument(file: File, signal?: AbortSignal): Promise<DocumentUploadResult> {
  const form = new FormData()
  form.append('file', file, file.name)
  return postForm<DocumentUploadResult>('/documents', form, signal)
}
