/**
 * 文档展示域逻辑（UI_SPEC 5.5、5.6）：纯函数，便于直接测试。
 *
 * 只做**展示映射**，不产生任何后端不存在的数据；未知取值一律原样展示。
 */

import type {
  ActivationState,
  DocumentListItem,
  DocumentStatus,
  FileType,
  SourceType,
} from '@/api/documents'

export type Tone = 'primary' | 'success' | 'warning' | 'danger' | 'info'

export interface DocumentStatusDescriptor {
  label: string
  tone: Tone
}

/**
 * 类别 → 中文标签：与后端 app/documents/categories.py 的展示映射保持一致，
 * 仅供界面显示；未知类别原样返回，避免新增类别时丢失信息。
 */
const DOC_CATEGORY_LABELS: Record<string, string> = {
  degree_plan: '培养方案',
  course_syllabus: '课程大纲',
  academic_policy: '学籍与教学管理规定',
  academic_calendar: '校历',
  exam_notice: '考试通知',
  course_schedule: '课表',
  course_records: '课程记录',
  security_test: '安全测试样本',
  unknown: '未分类',
}

/** 非终态（仍在处理）的文档状态。 */
const PROCESSING_LABELS: Partial<Record<DocumentStatus, string>> = {
  queued: '等待处理',
  validating: '解析中',
  storing: '解析中',
  parsing: '解析中',
  chunking: '索引中',
  embedding: '索引中',
  vector_indexing: '索引中',
  keyword_indexing: '索引中',
}

/** 仍在处理中的状态集合（用于决定是否需要轮询）。 */
export const ACTIVE_DOCUMENT_STATUSES: readonly DocumentStatus[] = [
  'queued',
  'validating',
  'storing',
  'parsing',
  'chunking',
  'embedding',
  'vector_indexing',
  'keyword_indexing',
]

export function isDocumentProcessing(status: DocumentStatus): boolean {
  return ACTIVE_DOCUMENT_STATUSES.includes(status)
}

export function categoryLabel(value: string): string {
  return DOC_CATEGORY_LABELS[value] ?? value
}

export function sourceLabel(sourceType: SourceType): string {
  return sourceType === 'demo' ? '演示资料' : '用户上传'
}

export function fileTypeLabel(fileType: FileType): string {
  return fileType.toUpperCase()
}

const LOCATOR_LABELS: Record<string, string> = {
  page_number: '页码',
  section_title: '章节',
  sheet_name: '工作表',
  row_start: '行号',
  row_end: '行号',
}

/** 定位能力：按类型展示页码 / 章节 / 工作表。 */
export function locatorLabel(locatorTypes: string[]): string {
  const labels: string[] = []
  for (const type of locatorTypes) {
    const label = LOCATOR_LABELS[type]
    if (label && !labels.includes(label)) {
      labels.push(label)
    }
  }
  return labels.length ? labels.join(' · ') : '—'
}

/**
 * 状态文案同时依赖处理状态与可检索字段（UI_SPEC 5.5）。
 * ``retrievable=true`` 才显示“可检索”；candidate / inactive 分别展示待激活与已退役。
 */
export function describeDocumentStatus(document: DocumentListItem): DocumentStatusDescriptor {
  if (document.retrievable) {
    return { label: '可检索', tone: 'success' }
  }
  if (document.status === 'failed') {
    return { label: '失败', tone: 'danger' }
  }
  if (document.status === 'ready') {
    if (document.activation_state === 'candidate') {
      return { label: '已处理，待整体激活', tone: 'warning' }
    }
    if (document.activation_state === 'inactive') {
      return { label: '已退役', tone: 'info' }
    }
    return { label: '已处理，未进入检索', tone: 'info' }
  }
  const label = PROCESSING_LABELS[document.status]
  return { label: label ?? '未知状态', tone: 'info' }
}

/** 列表第二行：文档类别 · 版本（详情才带 ``document_version``）。 */
export function documentSubtitle(
  document: Pick<DocumentListItem, 'doc_category' | 'dataset_version'> & {
    document_version?: string | null
  },
): string {
  const parts = [categoryLabel(document.doc_category)]
  const version = document.dataset_version ?? document.document_version ?? null
  if (version) {
    parts.push(version)
  }
  return parts.join(' · ')
}

export function activationLabel(state: ActivationState | null): string | null {
  if (state === 'candidate') {
    return '待整体激活'
  }
  if (state === 'inactive') {
    return '已退役'
  }
  return null
}

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

/** ISO-8601 UTC → 本地时区绝对时间；不使用“刚刚”等模糊占位。 */
export function formatLocalDateTime(value: string | null | undefined): string {
  if (!value) {
    return '—'
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return '—'
  }
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
  )
}

/** 抽屉中展示的校验值：只显示后端详情返回的 SHA-256 前 12 位。 */
export function shortChecksum(checksum: string | null | undefined): string {
  if (!checksum) {
    return '—'
  }
  return checksum.slice(0, 12)
}

export interface PreviewLocator {
  label: string
  value: string
}

/**
 * 预览块的定位信息（UI_SPEC 5.6）：
 * PDF 显示页码/章节，DOCX 显示标题路径，XLSX 显示工作表与行范围。
 */
export function previewLocators(
  fileType: FileType,
  block: {
    page_number: number | null
    section_title: string | null
    sheet_name: string | null
    row_start: number | null
    row_end: number | null
  },
): PreviewLocator[] {
  const locators: PreviewLocator[] = []
  if (fileType === 'xlsx') {
    if (block.sheet_name) {
      locators.push({ label: '工作表', value: block.sheet_name })
    }
    if (block.row_start !== null || block.row_end !== null) {
      const start = block.row_start ?? block.row_end
      const end = block.row_end ?? block.row_start
      locators.push({
        label: '行范围',
        value: start === end ? `${start}` : `${start}-${end}`,
      })
    }
    return locators
  }
  if (block.page_number !== null) {
    locators.push({ label: '页码', value: `第 ${block.page_number} 页` })
  }
  if (block.section_title) {
    locators.push({ label: fileType === 'docx' ? '标题路径' : '章节', value: block.section_title })
  }
  return locators
}
