<script setup lang="ts">
/**
 * 演示任务进度卡（UI_SPEC 5.3）。
 *
 * 进度、阶段与逐文件状态全部来自 ``GET /api/demo/jobs/{job_id}``；
 * 前端不根据文件序号模拟进度，也不把轮询网络失败写成 job failed。
 */
import { computed } from 'vue'

import type { DemoJob } from '@/api/demo'
import { jobDocumentStatusLabel, jobResultLabel, jobStatusLabel, stageLabel } from '@/domain/demoState'

const props = withDefaults(
  defineProps<{
    job: DemoJob | null
    polling?: boolean
    paused?: boolean
    reconnecting?: boolean
    reconnectLabel?: string
  }>(),
  { polling: false, paused: false, reconnecting: false, reconnectLabel: '正在重新连接' },
)

defineEmits<{ reconnect: [] }>()

const percent = computed(() => Math.min(100, Math.max(0, props.job?.progress_percent ?? 0)))
const documents = computed(() => props.job?.documents ?? [])
const errors = computed(() => props.job?.errors ?? [])
</script>

<template>
  <section class="ep-job" aria-labelledby="ep-job-title">
    <header class="ep-job__header">
      <h3 id="ep-job-title" class="ep-job__title">演示资料加载进度</h3>
      <ElTag v-if="job" size="small" effect="light" disable-transitions type="info">
        {{ jobStatusLabel(job.status) }}
      </ElTag>
    </header>

    <p v-if="!job" class="ep-job__waiting" aria-live="polite">
      {{ polling ? '正在获取任务进度' : '暂无任务进度' }}
    </p>

    <template v-else>
      <div
        class="ep-job__progress"
        role="progressbar"
        aria-valuemin="0"
        aria-valuemax="100"
        :aria-valuenow="percent"
        :aria-valuetext="`${percent}%`"
      >
        <ElProgress :percentage="percent" :stroke-width="10" :show-text="false" />
      </div>
      <p class="ep-job__progress-text" aria-live="polite">
        {{ percent }}% · 已处理 {{ job.processed }} / {{ job.total }} · 当前阶段：{{
          stageLabel(job.current_stage)
        }}
      </p>

      <dl class="ep-job__counts">
        <div><dt>已导入</dt><dd>{{ job.imported }}</dd></div>
        <div><dt>已续跑</dt><dd>{{ job.resumed }}</dd></div>
        <div><dt>已跳过</dt><dd>{{ job.skipped }}</dd></div>
        <div><dt>失败</dt><dd>{{ job.failed }}</dd></div>
      </dl>

      <ElAlert
        v-if="paused"
        class="ep-job__notice"
        type="warning"
        :closable="false"
        show-icon
        title="自动轮询已暂停"
      >
        <p class="ep-job__notice-text">
          连续多次无法获取任务状态。任务本身状态未改变，可手动重新连接。
        </p>
        <ElButton size="small" @click="$emit('reconnect')">重新连接</ElButton>
      </ElAlert>
      <ElAlert
        v-else-if="reconnecting"
        class="ep-job__notice"
        type="info"
        :closable="false"
        show-icon
        :title="reconnectLabel"
      >
        <p class="ep-job__notice-text">暂时无法获取最新进度，正在按退避间隔重试。</p>
      </ElAlert>

      <ElAlert
        v-if="errors.length"
        class="ep-job__notice"
        type="error"
        :closable="false"
        show-icon
        :title="`${errors.length} 个文件处理失败`"
      >
        <ul class="ep-job__errors">
          <li v-for="item in errors" :key="item.manifest_path">
            {{ item.file_name }}：{{ item.code }} · {{ item.message }}
            <span v-if="item.retryable">（可重试）</span>
          </li>
        </ul>
      </ElAlert>

      <ElCollapse v-if="documents.length" class="ep-job__documents">
        <ElCollapseItem name="documents" :title="`逐文件状态（${documents.length}）`">
          <ul class="ep-job__document-list">
            <li v-for="item in documents" :key="item.manifest_path" class="ep-job__document">
              <span class="ep-job__document-name">{{ item.file_name }}</span>
              <span class="ep-job__document-meta">
                {{ jobDocumentStatusLabel(item.status) }} · {{ jobResultLabel(item.result) }} ·
                {{ stageLabel(item.last_completed_stage) }}
              </span>
            </li>
          </ul>
        </ElCollapseItem>
      </ElCollapse>
    </template>
  </section>
</template>

<style scoped>
.ep-job {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-3);
}

.ep-job__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--ep-space-3);
}

.ep-job__title {
  font-size: 15px;
  line-height: 22px;
  font-weight: 600;
}

.ep-job__waiting,
.ep-job__progress-text {
  font-size: 13px;
  line-height: 20px;
  color: var(--ep-color-text-secondary);
}

.ep-job__notice-text {
  margin-bottom: var(--ep-space-2);
  overflow-wrap: anywhere;
}

.ep-job__counts {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--ep-space-2);
  margin: 0;
}

.ep-job__counts dt {
  font-size: 12px;
  line-height: 18px;
  color: var(--ep-color-text-muted);
}

.ep-job__counts dd {
  margin: 0;
  font-size: 16px;
  line-height: 24px;
  font-weight: 600;
}

.ep-job__errors {
  margin: 0;
  padding-left: var(--ep-space-4);
  overflow-wrap: anywhere;
}

.ep-job__document-list {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.ep-job__document {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.ep-job__document-name {
  font-size: 13px;
  line-height: 20px;
  overflow-wrap: anywhere;
}

.ep-job__document-meta {
  font-size: 12px;
  line-height: 18px;
  color: var(--ep-color-text-muted);
}

@media (max-width: 767px) {
  .ep-job__counts {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
