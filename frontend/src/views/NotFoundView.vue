<script setup lang="ts">
import { RouterLink } from 'vue-router'
</script>

<template>
  <div class="ep-notfound">
    <h1 class="ep-notfound__code">404</h1>
    <p class="ep-notfound__text">未找到请求的页面。</p>
    <RouterLink to="/knowledge">
      <ElButton type="primary">返回知识库</ElButton>
    </RouterLink>
  </div>
</template>

<style scoped>
.ep-notfound {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--ep-space-4);
  min-height: 100vh;
  padding: var(--ep-space-6);
  text-align: center;
}

.ep-notfound__code {
  font-size: 40px;
  line-height: 48px;
  font-weight: 700;
  color: var(--ep-color-text);
}

.ep-notfound__text {
  color: var(--ep-color-text-secondary);
}
</style>
