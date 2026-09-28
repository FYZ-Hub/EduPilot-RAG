/**
 * 演示数据集接口 DTO 与调用（PRODUCT_SPEC 6.3、DEMO_DATA_SPEC、UI_SPEC 5.3）。
 *
 * Dataset state、Job status、Job document status 是**三套互不替代**的类型，
 * 分别对应 app/constants.py 的 DEMO_STATE_* / JOB_* / JOB_DOC_* 与 RESULT_*。
 */

import { getJson, postJson } from './client'

/** GET /api/demo/status 的聚合状态（app/constants.py DEMO_STATE_*）。 */
export type DatasetState =
  | 'disabled'
  | 'unavailable'
  | 'empty'
  | 'queued'
  | 'running'
  | 'partial'
  | 'loaded'
  | 'failed'

/** 异步任务状态（app/constants.py JOB_*）。 */
export type JobStatus = 'queued' | 'running' | 'completed' | 'completed_with_errors' | 'failed'

/** 任务内单个文档状态（app/constants.py JOB_DOC_*）。 */
export type JobDocumentStatus = 'pending' | 'running' | 'completed' | 'failed' | 'skipped'

/** 任务内单个文档结果（app/constants.py RESULT_*）。 */
export type JobResult = 'imported' | 'resumed' | 'skipped' | 'failed'

export interface DatasetReason {
  code: string
  message: string
  retryable: boolean
}

export interface DemoStatus {
  enabled: boolean
  state: DatasetState
  dataset_version: string
  manifest_sha256: string | null
  pipeline_fingerprint: string
  available_documents: number
  ready_documents: number
  failed_documents: number
  loaded: boolean
  poll_after_seconds: number | null
  active_dataset_version: string | null
  serving_previous_version: boolean
  active_job_id: string | null
  last_job_id: string | null
  reason: DatasetReason | null
}

export interface DemoJobDocument {
  manifest_path: string
  file_name: string
  doc_id: string | null
  status: JobDocumentStatus
  last_completed_stage: string | null
  result: JobResult | null
  error_code: string | null
}

export interface DemoJobError {
  manifest_path: string
  file_name: string
  code: string
  message: string
  retryable: boolean
}

export interface DemoJob {
  job_id: string
  dataset_version: string
  target_stage: string
  status: JobStatus
  current_stage: string | null
  total: number
  imported: number
  resumed: number
  skipped: number
  failed: number
  processed: number
  progress_percent: number
  poll_after_seconds: number | null
  created_at: string | null
  started_at: string | null
  finished_at: string | null
  documents: DemoJobDocument[]
  errors: DemoJobError[]
}

export interface SeedAccepted {
  job_id: string
  dataset_version: string
  target_stage: string
  status: JobStatus
  status_url: string
  poll_after_seconds: number | null
  reused_active_job: boolean
}

export function fetchDemoStatus(signal?: AbortSignal): Promise<DemoStatus> {
  return getJson<DemoStatus>('/demo/status', signal)
}

export function seedDemo(signal?: AbortSignal): Promise<SeedAccepted> {
  return postJson<SeedAccepted>('/demo/seed', undefined, signal)
}

export function fetchDemoJob(jobId: string, signal?: AbortSignal): Promise<DemoJob> {
  return getJson<DemoJob>(`/demo/jobs/${jobId}`, signal)
}

export const JOB_TERMINAL_STATUSES: readonly JobStatus[] = [
  'completed',
  'completed_with_errors',
  'failed',
]

export function isJobTerminal(status: JobStatus): boolean {
  return JOB_TERMINAL_STATUSES.includes(status)
}
