<script setup lang="ts">
/**
 * 页面标题区：标题、说明与可选右侧主要操作（UI_SPEC 4.1）。
 */
defineProps<{
  title: string
  description?: string
}>()
</script>

<template>
  <header class="ep-page-header">
    <div>
      <h1 class="ep-page-header__title">{{ title }}</h1>
      <p v-if="description" class="ep-page-header__description">{{ description }}</p>
    </div>
    <div class="ep-page-header__actions">
      <slot name="actions" />
    </div>
  </header>
</template>
