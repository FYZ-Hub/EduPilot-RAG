<script setup lang="ts">
import { ChatDotRound, DataAnalysis, FolderOpened, HomeFilled } from '@element-plus/icons-vue'
import { RouterLink } from 'vue-router'

/** 主导航顺序固定（UI_SPEC 2.3）。 */
const items = [
  { name: 'home', label: '首页', icon: HomeFilled },
  { name: 'knowledge', label: '知识库管理', icon: FolderOpened },
  { name: 'chat', label: 'RAG 问答', icon: ChatDotRound },
  { name: 'planning', label: '学业规划', icon: DataAnalysis },
]
</script>

<template>
  <nav class="ep-nav" aria-label="主导航">
    <RouterLink
      v-for="item in items"
      :key="item.name"
      class="ep-nav__link"
      :to="{ name: item.name }"
      :aria-label="item.label"
    >
      <ElIcon :size="18"><component :is="item.icon" /></ElIcon>
      <span class="ep-nav__label">{{ item.label }}</span>
    </RouterLink>
  </nav>
</template>

<style scoped>
.ep-nav {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-1);
  padding: 0 var(--ep-space-2);
}

.ep-nav__link {
  position: relative;
  display: flex;
  align-items: center;
  gap: var(--ep-space-3);
  min-height: 40px;
  padding: var(--ep-space-2) var(--ep-space-3);
  border-radius: var(--ep-radius-card);
  color: var(--ep-color-text-secondary);
  font-size: 14px;
  text-decoration: none;
}

.ep-nav__link:hover {
  background-color: var(--ep-color-surface-subtle);
  color: var(--ep-color-text);
}

.ep-nav__link.router-link-active {
  background-color: var(--ep-color-primary-soft);
  color: var(--ep-color-primary);
  font-weight: 500;
}

.ep-nav__link.router-link-active::before {
  content: '';
  position: absolute;
  top: 8px;
  bottom: 8px;
  left: 0;
  width: 3px;
  border-radius: 0 2px 2px 0;
  background-color: var(--ep-color-primary);
}
</style>
