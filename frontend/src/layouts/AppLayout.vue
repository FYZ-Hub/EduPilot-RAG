<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterView, useRoute } from 'vue-router'
import { ArrowDown, Menu as MenuIcon, Reading } from '@element-plus/icons-vue'

import AppNav from '@/components/common/AppNav.vue'
import { describeDatasetState } from '@/domain/demoState'
import { useDemoStore } from '@/stores/demo'
import { useDocumentsStore } from '@/stores/documents'
import { useHealthStore } from '@/stores/health'

const route = useRoute()
const health = useHealthStore()
const documents = useDocumentsStore()
const demo = useDemoStore()

const drawerOpen = ref(false)

const pageTitle = computed(() => (route.meta.title as string | undefined) ?? 'EduPilot')
const pageDescription = computed(() => (route.meta.description as string | undefined) ?? '')

/** 状态圆点语义（不只用颜色表达，同时提供文字）。 */
const statusKind = computed(() => {
  if (health.connection === 'connected') {
    return health.data?.status === 'healthy' ? 'ok' : 'degraded'
  }
  return health.connection === 'error' ? 'down' : 'pending'
})

/** 资料层：只用真实文档列表，未就绪或查询失败时不展示任何数字。 */
const libraryText = computed(() => {
  if (documents.loaded) {
    return `可检索 ${documents.retrievableCount} / 共 ${documents.total}`
  }
  return documents.error ? '知识库概况暂不可用' : '正在读取知识库概况…'
})

const datasetText = computed(() => {
  const state = demo.state
  if (state) {
    return describeDatasetState(state).label
  }
  return demo.statusError ? '演示数据状态不可用' : '正在读取演示数据状态…'
})

onMounted(() => {
  void health.load()
  if (!documents.loaded) {
    void documents.load()
  }
  if (!demo.status) {
    void demo.loadStatus()
  }
})
</script>

<template>
  <div class="ep-shell">
    <a class="ep-skip-link" href="#main-content">跳到主要内容</a>

    <aside class="ep-sidebar">
      <div class="ep-brand">
        <ElIcon :size="24" class="ep-brand__icon"><Reading /></ElIcon>
        <div class="ep-brand__text">
          <span class="ep-brand__name">EduPilot</span>
          <span class="ep-brand__subtitle">学业导航</span>
        </div>
      </div>

      <AppNav />

      <div class="ep-sidebar__footer" aria-label="知识库概况">
        <p class="ep-sidebar__footer-title">知识库概况</p>
        <p class="ep-sidebar__footer-hint">{{ libraryText }}</p>
        <p class="ep-sidebar__footer-hint">演示数据：{{ datasetText }}</p>
      </div>
    </aside>

    <div class="ep-main">
      <header class="ep-topbar">
        <div class="ep-topbar__left">
          <ElButton
            class="ep-topbar__menu"
            text
            aria-label="打开导航"
            @click="drawerOpen = true"
          >
            <ElIcon :size="20"><MenuIcon /></ElIcon>
          </ElButton>
          <div>
            <p class="ep-topbar__title">{{ pageTitle }}</p>
            <p class="ep-topbar__description">{{ pageDescription }}</p>
          </div>
        </div>

        <div class="ep-topbar__right">
          <ElPopover
            trigger="click"
            placement="bottom-end"
            :width="340"
            :teleported="false"
            popper-class="ep-health-popover"
          >
            <template #reference>
              <button
                type="button"
                class="ep-status"
                aria-label="后端状态详情"
                aria-haspopup="dialog"
              >
                <span
                  class="ep-status__dot"
                  :class="`ep-status__dot--${statusKind}`"
                  aria-hidden="true"
                />
                <span class="ep-status__text">{{ health.connectionText }}</span>
                <span v-if="health.capabilitySummary" class="ep-status__hint">
                  {{ health.capabilitySummary }}
                </span>
                <ElIcon class="ep-status__caret" :size="12"><ArrowDown /></ElIcon>
              </button>
            </template>

            <div class="ep-health" role="group" aria-label="后端状态分层详情">
              <p class="ep-health__title">后端状态详情</p>

              <section class="ep-health__layer">
                <p class="ep-health__layer-title">1. 连接</p>
                <p class="ep-health__row">
                  <span class="ep-health__label">后端进程</span>
                  <span class="ep-health__value">{{ health.connectionText }}</span>
                </p>
                <p v-if="health.data" class="ep-health__row">
                  <span class="ep-health__label">版本</span>
                  <span class="ep-health__value">{{ health.data.version }}</span>
                </p>
                <p v-if="health.serviceStatusText" class="ep-health__row">
                  <span class="ep-health__label">服务状态</span>
                  <span class="ep-health__value">{{ health.serviceStatusText }}</span>
                </p>
                <p v-if="health.connection === 'error'" class="ep-health__note">
                  {{ health.errorMessage ?? '无法读取后端状态。' }}
                </p>
                <ElButton v-if="health.connection === 'error'" size="small" @click="health.load()">
                  重新检测
                </ElButton>
              </section>

              <section v-if="health.capabilityRows.length" class="ep-health__layer">
                <p class="ep-health__layer-title">2. 能力</p>
                <p v-for="row in health.capabilityRows" :key="row.key" class="ep-health__row">
                  <span class="ep-health__label">{{ row.label }}</span>
                  <span class="ep-health__value" :data-state="row.state">{{ row.text }}</span>
                </p>
                <p v-if="health.chatHint" class="ep-health__note">{{ health.chatHint }}</p>
              </section>

              <section v-if="health.providerRows.length" class="ep-health__layer">
                <p class="ep-health__layer-title">3. Provider</p>
                <p v-for="row in health.providerRows" :key="row.key" class="ep-health__row">
                  <span class="ep-health__label">{{ row.label }}</span>
                  <span class="ep-health__value">
                    {{ row.text }}<span class="ep-health__detail">（{{ row.detail }}）</span>
                  </span>
                </p>
                <p class="ep-health__note">
                  「尚无成功调用证据」只表示还没发生成功调用，不代表 Provider 故障。
                </p>
              </section>

              <section class="ep-health__layer">
                <p class="ep-health__layer-title">4. 资料</p>
                <p class="ep-health__row">
                  <span class="ep-health__label">可检索文档</span>
                  <span class="ep-health__value">{{ libraryText }}</span>
                </p>
              </section>
            </div>
          </ElPopover>
          <ElTag v-if="health.runModeLabel" size="small" type="info" effect="plain" disable-transitions>
            {{ health.runModeLabel }}
          </ElTag>
          <ElTag size="small" effect="plain" disable-transitions>演示数据均为虚构</ElTag>
        </div>
      </header>

      <main id="main-content" class="ep-content" tabindex="-1">
        <ElAlert
          v-if="health.connection === 'error'"
          class="ep-content__alert"
          type="error"
          :closable="false"
          show-icon
          title="后端服务不可连接"
        >
          <p class="ep-content__alert-text">写操作已禁用，只读页面仍可使用。</p>
          <ElButton size="small" @click="health.load()">重新检测</ElButton>
        </ElAlert>

        <RouterView />
      </main>
    </div>

    <ElDrawer v-model="drawerOpen" direction="ltr" size="240px" title="导航">
      <AppNav />
    </ElDrawer>
  </div>
</template>

<style scoped>
.ep-shell {
  display: grid;
  grid-template-columns: var(--ep-sidebar-width) 1fr;
  min-height: 100vh;
}

.ep-sidebar {
  position: sticky;
  top: 0;
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-6);
  height: 100vh;
  padding: var(--ep-space-5) 0;
  background-color: var(--ep-color-surface);
  border-right: 1px solid var(--ep-color-border-light);
}

.ep-brand {
  display: flex;
  align-items: center;
  gap: var(--ep-space-3);
  padding: 0 var(--ep-space-4);
}

.ep-brand__icon {
  color: var(--ep-color-primary);
}

.ep-brand__text {
  display: flex;
  flex-direction: column;
}

.ep-brand__name {
  font-size: 16px;
  line-height: 24px;
  font-weight: 600;
}

.ep-brand__subtitle {
  font-size: 12px;
  line-height: 18px;
  font-weight: 500;
  color: var(--ep-color-text-muted);
}

.ep-sidebar__footer {
  margin-top: auto;
  padding: var(--ep-space-3) var(--ep-space-4) 0;
  border-top: 1px solid var(--ep-color-border-light);
}

.ep-sidebar__footer-title {
  font-size: 12px;
  line-height: 18px;
  font-weight: 500;
  color: var(--ep-color-text-secondary);
}

.ep-sidebar__footer-hint {
  margin-top: var(--ep-space-1);
  font-size: 12px;
  line-height: 18px;
  color: var(--ep-color-text-muted);
}

.ep-main {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.ep-topbar {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--ep-space-4);
  height: var(--ep-topbar-height);
  padding: 0 var(--ep-content-padding);
  background-color: var(--ep-color-surface);
  border-bottom: 1px solid var(--ep-color-border-light);
}

.ep-topbar__left {
  display: flex;
  align-items: center;
  gap: var(--ep-space-3);
  min-width: 0;
}

.ep-topbar__left > div {
  min-width: 0;
}

.ep-topbar__title {
  font-size: 16px;
  line-height: 24px;
  font-weight: 600;
}

.ep-topbar__description {
  font-size: 13px;
  line-height: 20px;
  color: var(--ep-color-text-muted);
  overflow-wrap: anywhere;
}

.ep-topbar__right {
  display: flex;
  align-items: center;
  gap: var(--ep-space-3);
  flex-shrink: 0;
}

.ep-status {
  display: inline-flex;
  align-items: center;
  gap: var(--ep-space-2);
  padding: 2px var(--ep-space-2);
  font: inherit;
  font-size: 13px;
  line-height: 20px;
  color: var(--ep-color-text-secondary);
  background: none;
  border: none;
  cursor: pointer;
}

.ep-status:hover {
  background-color: var(--ep-color-surface-subtle);
}

.ep-status:focus-visible {
  outline: none;
  box-shadow: var(--ep-focus-ring);
}

.ep-status__hint {
  color: var(--ep-color-text-muted);
}

.ep-status__caret {
  color: var(--ep-color-text-muted);
}

.ep-health {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-3);
  font-size: 13px;
  line-height: 20px;
  color: var(--ep-color-text-secondary);
}

.ep-health__title {
  font-weight: 600;
  color: var(--ep-color-text);
}

.ep-health__layer {
  border-top: 1px solid var(--ep-color-border-light);
  padding-top: var(--ep-space-2);
}

.ep-health__layer-title {
  font-weight: 500;
  color: var(--ep-color-text-muted);
}

.ep-health__row {
  display: flex;
  justify-content: space-between;
  gap: var(--ep-space-3);
  margin-top: var(--ep-space-1);
}

.ep-health__label {
  flex-shrink: 0;
}

.ep-health__value {
  text-align: right;
  overflow-wrap: anywhere;
}

.ep-health__value[data-state='ready'] {
  color: var(--ep-color-success);
}

.ep-health__value[data-state='unconfigured'],
.ep-health__value[data-state='unavailable'] {
  color: var(--ep-color-warning);
}

.ep-health__detail {
  color: var(--ep-color-text-muted);
}

.ep-health__note {
  margin-top: var(--ep-space-1);
  color: var(--ep-color-text-muted);
}

.ep-status__dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background-color: var(--ep-color-text-muted);
}

.ep-status__dot--ok {
  background-color: var(--ep-color-success);
}

.ep-status__dot--degraded {
  background-color: var(--ep-color-warning);
}

.ep-status__dot--down {
  background-color: var(--ep-color-danger);
}

.ep-content {
  flex: 1;
  min-height: calc(100vh - var(--ep-topbar-height));
  padding: var(--ep-content-padding);
}

.ep-content__alert {
  margin-bottom: var(--ep-space-4);
}

.ep-content__alert-text {
  margin-bottom: var(--ep-space-2);
}

.ep-topbar__menu {
  display: none;
}

@media (min-width: 1440px) {
  .ep-content {
    padding: var(--ep-space-7);
  }
}

@media (max-width: 1199px) {
  .ep-shell {
    grid-template-columns: var(--ep-sidebar-width-compact) 1fr;
  }

  .ep-brand {
    justify-content: center;
    padding: 0;
  }

  .ep-brand__text,
  .ep-sidebar__footer,
  :deep(.ep-nav__label) {
    display: none;
  }

  :deep(.ep-nav__link) {
    justify-content: center;
    padding: var(--ep-space-2);
  }
}

@media (max-width: 767px) {
  .ep-shell {
    grid-template-columns: 1fr;
  }

  .ep-sidebar {
    display: none;
  }

  .ep-topbar {
    height: auto;
    flex-wrap: wrap;
    row-gap: var(--ep-space-2);
    padding-top: var(--ep-space-3);
    padding-bottom: var(--ep-space-3);
  }

  .ep-topbar__left {
    flex: 1 1 100%;
  }

  .ep-topbar__right {
    flex-shrink: 1;
    flex-wrap: wrap;
    width: 100%;
    min-width: 0;
  }

  .ep-topbar__menu {
    display: inline-flex;
  }

  .ep-content {
    padding: var(--ep-space-4);
  }
}
</style>
