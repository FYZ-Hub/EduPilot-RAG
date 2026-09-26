<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterView, useRoute } from 'vue-router'
import { Menu as MenuIcon, Reading } from '@element-plus/icons-vue'

import AppNav from '@/components/common/AppNav.vue'
import { useHealthStore } from '@/stores/health'

const route = useRoute()
const health = useHealthStore()

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

const statusText = computed(() => {
  switch (statusKind.value) {
    case 'ok':
      return '后端正常'
    case 'degraded':
      return '后端降级运行'
    case 'down':
      return '后端不可连接'
    default:
      return '正在检测后端'
  }
})

onMounted(() => {
  void health.load()
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

      <div class="ep-sidebar__footer">
        <p class="ep-sidebar__footer-title">知识库概况</p>
        <p class="ep-sidebar__footer-hint">阶段 1 尚未接入文档接口，暂无可用文档数据。</p>
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
          <span class="ep-status">
            <span class="ep-status__dot" :class="`ep-status__dot--${statusKind}`" aria-hidden="true" />
            <span class="ep-status__text">{{ statusText }}</span>
          </span>
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
          description="写操作已禁用，只读页面仍可使用。"
        >
          <template #default>
            <ElButton size="small" @click="health.load()">重新检测</ElButton>
          </template>
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
  font-size: 13px;
  line-height: 20px;
  color: var(--ep-color-text-secondary);
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
