/**
 * 演示数据集状态域逻辑（UI_SPEC 5.3、DEMO_DATA_SPEC）。
 *
 * Dataset state / Job status / Job document status 是**三套互不替代**的类型：
 * Job 的 ``completed`` 只代表达到该任务目标，绝不能据此推导 Dataset 已加载。
 */

import type { DatasetState, JobDocumentStatus, JobResult, JobStatus } from '@/api/demo'

import type { Tone } from './documents'

export interface DatasetStateDescriptor {
  /** 按钮文案；``null`` 表示不提供按钮。 */
  buttonLabel: string | null
  /** 该按钮是否为页面主操作（空库时“加载演示资料”是主要操作）。 */
  primary: boolean
  /** 点击按钮是否调用 ``POST /api/demo/seed``。 */
  seeds: boolean
  /** 按钮是否禁用。 */
  disabled: boolean
  /** 是否展示 ``reason``（安全错误摘要 / 配置提示）。 */
  showsReason: boolean
  /** 状态标签文案。 */
  label: string
  tone: Tone
  description: string
}

const DESCRIPTORS: Record<DatasetState, DatasetStateDescriptor> = {
  disabled: {
    buttonLabel: '演示资料不可用',
    primary: false,
    seeds: false,
    disabled: true,
    showsReason: true,
    label: '演示资料已禁用',
    tone: 'info',
    description: '当前部署未启用演示数据集，可上传自有资料。',
  },
  unavailable: {
    buttonLabel: '演示资料不可用',
    primary: false,
    seeds: false,
    disabled: true,
    showsReason: true,
    label: '演示资料不可用',
    tone: 'danger',
    description: '演示数据集配置或清单不可用，请检查部署配置后重新检测。',
  },
  empty: {
    buttonLabel: '加载演示资料',
    primary: true,
    seeds: true,
    disabled: false,
    showsReason: false,
    label: '空知识库',
    tone: 'info',
    description: '知识库为空，可加载演示资料或上传自有文档。',
  },
  queued: {
    buttonLabel: '正在加载',
    primary: false,
    seeds: false,
    disabled: true,
    showsReason: false,
    label: '等待加载',
    tone: 'primary',
    description: '演示任务已排队，正在等待处理。',
  },
  running: {
    buttonLabel: '正在加载',
    primary: false,
    seeds: false,
    disabled: true,
    showsReason: false,
    label: '正在加载',
    tone: 'primary',
    description: '正在导入演示资料，进度来自后端任务。',
  },
  partial: {
    buttonLabel: '继续加载',
    primary: false,
    seeds: true,
    disabled: false,
    showsReason: false,
    label: '部分就绪',
    tone: 'warning',
    description: '部分演示文档尚未就绪，可继续加载补齐。',
  },
  loaded: {
    buttonLabel: '重新校验',
    primary: false,
    seeds: true,
    disabled: false,
    showsReason: false,
    label: '演示资料已加载',
    tone: 'success',
    description: '演示资料已就绪，可重新校验以生成全 skipped 审计任务。',
  },
  failed: {
    buttonLabel: '重试加载',
    primary: false,
    seeds: true,
    disabled: false,
    showsReason: true,
    label: '加载失败',
    tone: 'danger',
    description: '上一次演示加载失败，可重试同一个 seed 接口。',
  },
}

export function describeDatasetState(state: DatasetState): DatasetStateDescriptor {
  return DESCRIPTORS[state]
}

const JOB_STATUS_LABELS: Record<JobStatus, string> = {
  queued: '排队中',
  running: '处理中',
  completed: '已完成',
  completed_with_errors: '完成但有失败项',
  failed: '失败',
}

export function jobStatusLabel(status: JobStatus): string {
  return JOB_STATUS_LABELS[status]
}

const JOB_RESULT_LABELS: Record<JobResult, string> = {
  imported: '已导入',
  resumed: '已续跑',
  skipped: '已跳过',
  failed: '失败',
}

export function jobResultLabel(result: JobResult | null): string {
  return result ? JOB_RESULT_LABELS[result] : '待处理'
}

const JOB_DOCUMENT_LABELS: Record<JobDocumentStatus, string> = {
  pending: '等待处理',
  running: '处理中',
  completed: '已完成',
  failed: '失败',
  skipped: '已跳过',
}

export function jobDocumentStatusLabel(status: JobDocumentStatus): string {
  return JOB_DOCUMENT_LABELS[status]
}

const STAGE_LABELS: Record<string, string> = {
  validating: '校验',
  storing: '入库',
  parsing: '解析',
  chunking: '切片',
  embedding: '向量化',
  vector_indexing: '向量索引',
  keyword_indexing: '关键词索引',
  activating: '激活',
  completed: '完成',
}

export function stageLabel(stage: string | null): string {
  if (!stage) {
    return '—'
  }
  return STAGE_LABELS[stage] ?? stage
}
