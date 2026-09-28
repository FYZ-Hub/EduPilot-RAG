<script setup lang="ts">
/**
 * 知识库管理页面（UI_SPEC 第 5 节）。
 *
 * 所有数量、状态与进度均来自真实接口；页面不自动 seed、不伪造任何数字。
 * 写操作（上传 / 加载演示资料 / 预览 / 删除）仅在 ``documents`` 能力为 ready
 * 且后端可达时可用，否则禁用并解释原因。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Delete, Document, Search, UploadFilled, View } from '@element-plus/icons-vue'

import { toApiError, type ApiError } from '@/api/client'
import {
  getDocument,
  getDocumentPreview,
  type DocumentDetail,
  type DocumentListItem,
  type DocumentPreview,
} from '@/api/documents'
import { isJobTerminal } from '@/api/demo'
import AsyncState from '@/components/common/AsyncState.vue'
import ConfirmDeleteDialog from '@/components/common/ConfirmDeleteDialog.vue'
import ErrorAlert from '@/components/common/ErrorAlert.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import StatusTag from '@/components/common/StatusTag.vue'
import DocumentPreviewDrawer from '@/components/documents/DocumentPreviewDrawer.vue'
import FileUploader from '@/components/documents/FileUploader.vue'
import JobProgressPanel from '@/components/documents/JobProgressPanel.vue'
import { describeDatasetState } from '@/domain/demoState'
import {
  documentSubtitle,
  fileTypeLabel,
  formatLocalDateTime,
  isDocumentProcessing,
  locatorLabel,
  sourceLabel,
} from '@/domain/documents'
import { useDemoStore } from '@/stores/demo'
import { useDocumentsStore } from '@/stores/documents'
import { useHealthStore } from '@/stores/health'

const health = useHealthStore()
const documents = useDocumentsStore()
const demo = useDemoStore()

/** 搜索防抖（UI_SPEC 5.5：300ms）。 */
const SEARCH_DEBOUNCE_MS = 300

type StatusFilter = 'all' | 'retrievable' | 'processing' | 'candidate' | 'inactive' | 'failed'

const searchInput = ref('')
const searchTerm = ref('')
const sourceFilter = ref<'all' | 'demo' | 'upload'>('all')
const typeFilter = ref<'all' | 'pdf' | 'docx' | 'xlsx'>('all')
const statusFilter = ref<StatusFilter>('all')

let searchTimer: ReturnType<typeof setTimeout> | null = null
watch(searchInput, (value) => {
  if (searchTimer !== null) {
    clearTimeout(searchTimer)
  }
  searchTimer = setTimeout(() => {
    searchTerm.value = value.trim().toLowerCase()
    searchTimer = null
  }, SEARCH_DEBOUNCE_MS)
})

/** ---- 能力门控 ---- */
const documentsCapability = computed(() => health.capabilities?.documents ?? null)
const backendDown = computed(() => health.connection === 'error')
const writeEnabled = computed(
  () => documentsCapability.value === 'ready' && !backendDown.value,
)

const writeBlockedNotice = computed(() => {
  if (backendDown.value) {
    return '后端服务不可连接，写操作已禁用，只读页面仍可使用。'
  }
  if (health.connection !== 'connected') {
    return null
  }
  if (documentsCapability.value === 'ready') {
    return null
  }
  if (documentsCapability.value === 'unconfigured') {
    return '文档能力未配置（documents = unconfigured）：上传、加载演示资料、预览与删除已禁用。'
  }
  return '文档能力不可用（documents = unavailable）：上传、加载演示资料、预览与删除已禁用。'
})

const writeDisabledReason = computed(
  () => writeBlockedNotice.value ?? '当前不可执行写操作。',
)

/** ---- 统计卡：直接绑定接口字段，空库显示真实 0 ---- */
const stats = computed(() => [
  { label: '全部文档', value: documents.total },
  { label: '可检索', value: documents.counts.retrievable },
  { label: '处理中', value: documents.counts.processing },
  { label: '失败', value: documents.counts.failed },
])

/** ---- 演示数据状态 ---- */
const datasetDescriptor = computed(() =>
  demo.state ? describeDatasetState(demo.state) : null,
)

const seedDisabled = computed(
  () => !writeEnabled.value || (datasetDescriptor.value?.disabled ?? true),
)

const seedReason = computed(() =>
  !writeEnabled.value ? writeDisabledReason.value : '',
)

const showJobPanel = computed(() => demo.jobId !== null)

const reasonText = computed(() => {
  const reason = demo.status?.reason
  return reason ? `${reason.code}：${reason.message}` : ''
})

async function onSeed(): Promise<void> {
  if (!datasetDescriptor.value?.seeds || seedDisabled.value) {
    return
  }
  const accepted = await demo.seed()
  if (accepted) {
    ElMessage.info('已提交演示资料加载任务')
  }
}

/** job 进入终态后刷新文档列表（Demo 状态由 store 自行重新读取）。 */
const refreshedJobId = ref<string | null>(null)
watch(
  () => demo.job,
  (job) => {
    if (job && isJobTerminal(job.status) && job.job_id !== refreshedJobId.value) {
      refreshedJobId.value = job.job_id
      void documents.refresh().then(() => documents.startStatusPolling())
    }
  },
)

/** ---- 本地筛选与排序（无服务端分页，不伪造分页） ---- */
function timestamp(value: string | null): number {
  if (!value) {
    return 0
  }
  const parsed = Date.parse(value)
  return Number.isNaN(parsed) ? 0 : parsed
}

function matchesStatus(item: DocumentListItem, filter: StatusFilter): boolean {
  switch (filter) {
    case 'retrievable':
      return item.retrievable
    case 'processing':
      return isDocumentProcessing(item.status)
    case 'candidate':
      return item.status === 'ready' && item.activation_state === 'candidate'
    case 'inactive':
      return item.status === 'ready' && item.activation_state === 'inactive'
    case 'failed':
      return item.status === 'failed'
    default:
      return true
  }
}

const visibleItems = computed(() => {
  const term = searchTerm.value
  return [...documents.items]
    .filter((item) => !term || item.file_name.toLowerCase().includes(term))
    .filter((item) => sourceFilter.value === 'all' || item.source_type === sourceFilter.value)
    .filter((item) => typeFilter.value === 'all' || item.file_type === typeFilter.value)
    .filter((item) => matchesStatus(item, statusFilter.value))
    .sort((a, b) => timestamp(b.updated_at) - timestamp(a.updated_at))
})

const filtersActive = computed(
  () =>
    searchInput.value.trim() !== '' ||
    sourceFilter.value !== 'all' ||
    typeFilter.value !== 'all' ||
    statusFilter.value !== 'all',
)

function clearFilters(): void {
  searchInput.value = ''
  searchTerm.value = ''
  sourceFilter.value = 'all'
  typeFilter.value = 'all'
  statusFilter.value = 'all'
}

/** ---- 上传 ---- */
const uploadOpen = ref(false)

function openUpload(): void {
  if (!writeEnabled.value) {
    return
  }
  uploadOpen.value = true
}

async function onUploaded(): Promise<void> {
  ElMessage.success('上传已受理')
  await documents.refresh()
  documents.startStatusPolling()
}

/** ---- 预览 / 详情 ---- */
const previewOpen = ref(false)
const previewLoading = ref(false)
const previewError = ref<ApiError | null>(null)
const previewDocument = ref<DocumentDetail | null>(null)
const previewData = ref<DocumentPreview | null>(null)

async function openPreview(item: DocumentListItem): Promise<void> {
  if (!writeEnabled.value) {
    return
  }
  previewOpen.value = true
  previewLoading.value = true
  previewError.value = null
  previewDocument.value = null
  previewData.value = null
  try {
    const [detail, preview] = await Promise.all([
      getDocument(item.id),
      getDocumentPreview(item.id),
    ])
    previewDocument.value = detail
    previewData.value = preview
  } catch (caught) {
    const apiError = toApiError(caught)
    if (!apiError.isAborted) {
      previewError.value = apiError
    }
  } finally {
    previewLoading.value = false
  }
}

const detailOpen = ref(false)
const detailLoading = ref(false)
const detailError = ref<ApiError | null>(null)
const detailDocument = ref<DocumentDetail | null>(null)

async function openDetail(item: DocumentListItem): Promise<void> {
  if (!writeEnabled.value) {
    return
  }
  detailOpen.value = true
  detailLoading.value = true
  detailError.value = null
  detailDocument.value = null
  try {
    detailDocument.value = await getDocument(item.id)
  } catch (caught) {
    const apiError = toApiError(caught)
    if (!apiError.isAborted) {
      detailError.value = apiError
    }
  } finally {
    detailLoading.value = false
  }
}

/** ---- 删除 ---- */
const confirmDialog = ref<InstanceType<typeof ConfirmDeleteDialog> | null>(null)
const deletingId = ref<string | null>(null)

async function onDelete(item: DocumentListItem): Promise<void> {
  if (!writeEnabled.value) {
    return
  }
  const confirmed = await confirmDialog.value?.confirm(item)
  if (!confirmed) {
    return
  }
  deletingId.value = item.id
  try {
    await documents.remove(item.id)
    ElMessage.success('文档已删除')
  } catch (caught) {
    const apiError = toApiError(caught)
    ElMessage.error(`删除失败：${apiError.message}（${apiError.requestIdLabel}）`)
  } finally {
    deletingId.value = null
  }
}

/** ---- 生命周期 ---- */
async function initialise(): Promise<void> {
  await Promise.all([documents.load({ force: true }), demo.restore()])
  documents.startStatusPolling()
}

onMounted(() => {
  if (health.connection === 'unknown') {
    void health.load()
  }
  void initialise()
})

onBeforeUnmount(() => {
  if (searchTimer !== null) {
    clearTimeout(searchTimer)
    searchTimer = null
  }
  documents.stop()
  demo.stopPolling()
})
</script>

<template>
  <section class="ep-view">
    <PageHeader title="知识库管理" description="管理用于检索与学业规划的可信资料">
      <template #actions>
        <ElButton
          :icon="UploadFilled"
          :disabled="!writeEnabled"
          :aria-label="writeEnabled ? '上传文件' : `上传文件已禁用：${writeDisabledReason}`"
          @click="openUpload"
        >
          上传文件
        </ElButton>
        <ElButton
          v-if="datasetDescriptor"
          :type="datasetDescriptor.primary ? 'primary' : 'default'"
          :loading="demo.seeding"
          :disabled="seedDisabled"
          :aria-label="
            seedDisabled ? `加载演示资料已禁用：${seedReason || datasetDescriptor.label}` : datasetDescriptor.buttonLabel
          "
          @click="onSeed"
        >
          {{ datasetDescriptor.buttonLabel }}
        </ElButton>
      </template>
    </PageHeader>

    <ElAlert
      v-if="writeBlockedNotice"
      type="error"
      :closable="false"
      show-icon
      title="写操作已禁用"
    >
      <p class="ep-notice__text">{{ writeBlockedNotice }}</p>
      <ElButton size="small" @click="health.load()">重新检测</ElButton>
    </ElAlert>

    <div class="ep-stats">
      <div v-for="stat in stats" :key="stat.label" class="ep-stat">
        <p class="ep-stat__label">{{ stat.label }}</p>
        <p class="ep-stat__value">{{ stat.value }}</p>
      </div>
    </div>

    <div class="ep-card ep-demo">
      <div class="ep-demo__header">
        <h2 class="ep-demo__title">演示资料</h2>
        <ElTag v-if="datasetDescriptor" size="small" effect="light" disable-transitions :type="datasetDescriptor.tone">
          {{ datasetDescriptor.label }}
        </ElTag>
      </div>

      <ErrorAlert
        v-if="demo.seedError"
        class="ep-demo__seed-error"
        :error="demo.seedError"
        title="演示资料加载失败"
      >
        <ElButton size="small" :loading="demo.seeding" :disabled="seedDisabled" @click="onSeed">
          重试加载
        </ElButton>
      </ErrorAlert>

      <AsyncState
        :error="demo.statusError"
        :empty="!demo.status"
        error-title="无法读取演示数据状态"
        empty-title="暂无演示数据状态"
        empty-description="尚未获得后端返回的演示数据集状态。"
        :skeleton-rows="1"
        loading-text="正在读取演示数据状态"
        :loading="demo.statusLoading && !demo.status"
      >
        <template #error-actions>
          <ElButton size="small" @click="demo.loadStatus()">重新检测</ElButton>
        </template>

        <template v-if="datasetDescriptor">
          <ElAlert
            v-if="datasetDescriptor.tone === 'danger'"
            class="ep-demo__danger"
            type="error"
            :closable="false"
            show-icon
            :title="datasetDescriptor.label"
          >
            <p class="ep-demo__description">{{ datasetDescriptor.description }}</p>
            <pre v-if="reasonText" class="ep-demo__reason">{{ reasonText }}</pre>
          </ElAlert>
          <template v-else>
            <p class="ep-demo__description">{{ datasetDescriptor.description }}</p>
            <pre
              v-if="datasetDescriptor.showsReason && reasonText"
              class="ep-demo__reason"
            >{{ reasonText }}</pre>
          </template>
        </template>

        <ElAlert
          v-if="demo.servingPreviousVersion"
          class="ep-demo__notice"
          type="warning"
          :closable="false"
          show-icon
          :title="`新版本未就绪，当前仍使用版本 ${demo.status?.active_dataset_version ?? '—'}`"
          description="演示数据集已更新但尚未完成加载，检索仍基于上一版本。"
        />

        <dl class="ep-demo__counts">
          <div><dt>可用文件</dt><dd>{{ demo.status?.available_documents ?? 0 }}</dd></div>
          <div><dt>就绪文件</dt><dd>{{ demo.status?.ready_documents ?? 0 }}</dd></div>
          <div><dt>失败文件</dt><dd>{{ demo.status?.failed_documents ?? 0 }}</dd></div>
          <div><dt>数据版本</dt><dd>{{ demo.status?.dataset_version ?? '—' }}</dd></div>
        </dl>
      </AsyncState>
    </div>

    <div v-if="showJobPanel" class="ep-card">
      <JobProgressPanel
        :job="demo.job"
        :polling="demo.polling"
        :paused="demo.pollPaused"
        :reconnecting="demo.reconnecting"
        :reconnect-label="demo.reconnectLabel"
        @reconnect="demo.reconnect()"
      />
    </div>

    <div class="ep-card">
      <div class="ep-toolbar">
        <ElInput
          v-model="searchInput"
          class="ep-toolbar__search"
          :prefix-icon="Search"
          clearable
          placeholder="搜索文件名"
          aria-label="搜索文件名"
        />
        <ElSelect v-model="sourceFilter" class="ep-toolbar__select" aria-label="按来源筛选">
          <ElOption label="全部来源" value="all" />
          <ElOption label="演示资料" value="demo" />
          <ElOption label="用户上传" value="upload" />
        </ElSelect>
        <ElSelect v-model="typeFilter" class="ep-toolbar__select" aria-label="按类型筛选">
          <ElOption label="全部类型" value="all" />
          <ElOption label="PDF" value="pdf" />
          <ElOption label="DOCX" value="docx" />
          <ElOption label="XLSX" value="xlsx" />
        </ElSelect>
        <ElSelect v-model="statusFilter" class="ep-toolbar__select" aria-label="按状态筛选">
          <ElOption label="全部状态" value="all" />
          <ElOption label="可检索" value="retrievable" />
          <ElOption label="处理中" value="processing" />
          <ElOption label="待整体激活" value="candidate" />
          <ElOption label="已退役" value="inactive" />
          <ElOption label="失败" value="failed" />
        </ElSelect>
        <ElButton :disabled="!filtersActive" @click="clearFilters">清除筛选</ElButton>
      </div>

      <AsyncState
        class="ep-list"
        :loading="documents.loading && !documents.loaded"
        :error="documents.error"
        :empty="documents.loaded && documents.items.length === 0"
        loading-text="正在读取文档列表"
        empty-title="知识库为空"
        empty-description="可加载演示资料，或上传 PDF / DOCX / XLSX 文档。"
        :skeleton-rows="5"
      >
        <template #error-actions>
          <ElButton size="small" @click="documents.refresh()">重试</ElButton>
        </template>

        <p v-if="visibleItems.length === 0" class="ep-list__filtered-empty">
          没有符合当前筛选条件的文档。
        </p>

        <div v-else class="ep-table-scroll">
          <ElTable :data="visibleItems" row-key="id" size="default">
            <ElTableColumn label="文件名" min-width="220">
              <template #default="{ row }">
                <div class="ep-doc-name">
                  <span class="ep-doc-name__primary">{{ row.file_name }}</span>
                  <span class="ep-doc-name__secondary">{{ documentSubtitle(row) }}</span>
                </div>
              </template>
            </ElTableColumn>
            <ElTableColumn label="来源" width="110">
              <template #default="{ row }">{{ sourceLabel(row.source_type) }}</template>
            </ElTableColumn>
            <ElTableColumn label="类型" width="90">
              <template #default="{ row }">{{ fileTypeLabel(row.file_type) }}</template>
            </ElTableColumn>
            <ElTableColumn label="状态" width="160">
              <template #default="{ row }"><StatusTag :document="row" /></template>
            </ElTableColumn>
            <ElTableColumn label="定位能力" width="150">
              <template #default="{ row }">{{ locatorLabel(row.locator_types) }}</template>
            </ElTableColumn>
            <ElTableColumn label="更新时间" width="180">
              <template #default="{ row }">{{ formatLocalDateTime(row.updated_at) }}</template>
            </ElTableColumn>
            <ElTableColumn label="操作" width="230">
              <template #default="{ row }">
                <div class="ep-row-actions">
                  <ElButton
                    size="small"
                    text
                    type="primary"
                    :icon="View"
                    :disabled="!writeEnabled"
                    :aria-label="`预览 ${row.file_name}`"
                    @click="openPreview(row)"
                  >
                    预览
                  </ElButton>
                  <ElButton
                    size="small"
                    text
                    :icon="Document"
                    :disabled="!writeEnabled"
                    :aria-label="`查看 ${row.file_name} 详情`"
                    @click="openDetail(row)"
                  >
                    详情
                  </ElButton>
                  <ElButton
                    size="small"
                    text
                    type="danger"
                    :icon="Delete"
                    :loading="deletingId === row.id"
                    :disabled="!writeEnabled || deletingId !== null"
                    :aria-label="`删除 ${row.file_name}`"
                    @click="onDelete(row)"
                  >
                    删除
                  </ElButton>
                </div>
              </template>
            </ElTableColumn>
          </ElTable>
        </div>
      </AsyncState>
    </div>

    <FileUploader
      v-model="uploadOpen"
      :disabled="!writeEnabled"
      :disabled-reason="writeDisabledReason"
      @uploaded="onUploaded"
    />

    <DocumentPreviewDrawer
      v-model="previewOpen"
      :document="previewDocument"
      :preview="previewData"
      :loading="previewLoading"
      :error="previewError"
      @recover="onSeed"
    />

    <ElDialog v-model="detailOpen" title="文档详情" width="560px">
      <AsyncState
        :loading="detailLoading"
        :error="detailError"
        loading-text="正在读取文档详情"
        :skeleton-rows="4"
      >
        <template #error-actions>
          <ElButton size="small" @click="detailOpen = false">关闭</ElButton>
        </template>
        <dl v-if="detailDocument" class="ep-detail">
          <div><dt>文件名</dt><dd>{{ detailDocument.file_name }}</dd></div>
          <div><dt>文档 ID</dt><dd>{{ detailDocument.id }}</dd></div>
          <div><dt>来源</dt><dd>{{ sourceLabel(detailDocument.source_type) }}</dd></div>
          <div><dt>类别</dt><dd>{{ documentSubtitle(detailDocument) }}</dd></div>
          <div><dt>类型</dt><dd>{{ fileTypeLabel(detailDocument.file_type) }}</dd></div>
          <div><dt>状态</dt><dd><StatusTag :document="detailDocument" /></dd></div>
          <div><dt>当前阶段</dt><dd>{{ detailDocument.current_stage ?? '—' }}</dd></div>
          <div><dt>切片 / 内容块</dt><dd>{{ detailDocument.chunk_count }} / {{ detailDocument.block_count }}</dd></div>
          <div><dt>定位能力</dt><dd>{{ locatorLabel(detailDocument.locator_types) }}</dd></div>
          <div><dt>生效日期</dt><dd>{{ detailDocument.effective_from ?? '—' }}</dd></div>
          <div><dt>校验值（SHA-256 前 12 位）</dt><dd>{{ detailDocument.checksum.slice(0, 12) }}</dd></div>
          <div><dt>创建时间</dt><dd>{{ formatLocalDateTime(detailDocument.created_at) }}</dd></div>
          <div><dt>更新时间</dt><dd>{{ formatLocalDateTime(detailDocument.updated_at) }}</dd></div>
        </dl>
        <p v-if="detailDocument?.error" class="ep-detail__error">
          错误码：{{ detailDocument.error.code }} · {{ detailDocument.error.message ?? '无详细信息' }}
        </p>
      </AsyncState>
    </ElDialog>

    <ConfirmDeleteDialog ref="confirmDialog" />
  </section>
</template>

<style scoped>
.ep-stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--ep-space-4);
}

.ep-stat {
  padding: var(--ep-space-5);
  background-color: var(--ep-color-surface);
  border: 1px solid var(--ep-color-border-light);
  border-radius: var(--ep-radius-card);
}

.ep-stat__label {
  font-size: 13px;
  line-height: 20px;
  color: var(--ep-color-text-secondary);
}

.ep-stat__value {
  margin-top: var(--ep-space-1);
  font-size: 28px;
  line-height: 36px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.ep-demo__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--ep-space-3);
  margin-bottom: var(--ep-space-3);
}

.ep-demo__title {
  font-size: 18px;
  line-height: 26px;
  font-weight: 600;
}

.ep-demo__description {
  font-size: 13px;
  line-height: 20px;
  color: var(--ep-color-text-secondary);
}

.ep-demo__reason {
  margin: var(--ep-space-2) 0 0;
  padding: var(--ep-space-3);
  background-color: var(--ep-color-surface-subtle);
  border: 1px solid var(--ep-color-border-light);
  border-radius: var(--ep-radius-card);
  font-family: var(--ep-font-family);
  font-size: 12px;
  line-height: 18px;
  color: var(--ep-color-text-secondary);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.ep-demo__notice {
  margin-top: var(--ep-space-3);
}

.ep-demo__seed-error {
  margin-bottom: var(--ep-space-4);
}

.ep-notice__text {
  margin-bottom: var(--ep-space-2);
  overflow-wrap: anywhere;
}

.ep-demo__counts {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--ep-space-3);
  margin: var(--ep-space-4) 0 0;
}

.ep-demo__counts dt {
  font-size: 12px;
  line-height: 18px;
  color: var(--ep-color-text-muted);
}

.ep-demo__counts dd {
  margin: 0;
  font-size: 16px;
  line-height: 24px;
  font-weight: 600;
  overflow-wrap: anywhere;
}

.ep-toolbar {
  display: flex;
  align-items: center;
  gap: var(--ep-space-2);
  flex-wrap: wrap;
  margin-bottom: var(--ep-space-4);
}

.ep-toolbar__search {
  width: 220px;
  min-width: 160px;
}

.ep-toolbar__select {
  width: 140px;
}

.ep-table-scroll {
  overflow-x: auto;
}

.ep-doc-name {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.ep-doc-name__primary {
  font-size: 14px;
  line-height: 22px;
  overflow-wrap: anywhere;
}

.ep-doc-name__secondary {
  font-size: 12px;
  line-height: 18px;
  color: var(--ep-color-text-muted);
  overflow-wrap: anywhere;
}

.ep-row-actions {
  display: flex;
  align-items: center;
  gap: var(--ep-space-1);
  flex-wrap: wrap;
}

.ep-list__filtered-empty {
  padding: var(--ep-space-6) 0;
  text-align: center;
  color: var(--ep-color-text-muted);
}

.ep-detail {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-2);
  margin: 0;
}

.ep-detail > div {
  display: grid;
  grid-template-columns: 160px 1fr;
  gap: var(--ep-space-2);
}

.ep-detail dt {
  font-size: 13px;
  line-height: 20px;
  color: var(--ep-color-text-muted);
}

.ep-detail dd {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  overflow-wrap: anywhere;
}

.ep-detail__error {
  margin-top: var(--ep-space-2);
  font-size: 12px;
  line-height: 18px;
  color: var(--ep-color-danger);
  overflow-wrap: anywhere;
}

@media (max-width: 1199px) {
  .ep-stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .ep-demo__counts {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 767px) {
  .ep-stats {
    grid-template-columns: 1fr;
  }

  .ep-toolbar__search,
  .ep-toolbar__select {
    width: 100%;
  }

  .ep-detail > div {
    grid-template-columns: 1fr;
  }
}
</style>
