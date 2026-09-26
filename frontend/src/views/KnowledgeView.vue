<script setup lang="ts">
import { FolderOpened } from '@element-plus/icons-vue'

import PageHeader from '@/components/common/PageHeader.vue'
</script>

<template>
  <section class="ep-view">
    <PageHeader title="知识库管理" description="管理用于检索与学业规划的可信资料" />

    <div class="ep-card">
      <ElAlert
        type="info"
        :closable="false"
        show-icon
        title="功能将在后续阶段实现"
        description="本阶段仅提供应用框架。演示资料加载、文档上传、状态跟踪、预览与删除将在阶段 2 与阶段 8 实现。"
      />

      <div class="ep-placeholder">
        <ElIcon :size="24"><FolderOpened /></ElIcon>
        <p class="ep-placeholder__title">暂无可展示的文档</p>
        <p>阶段 1 不生成模拟资料、也不接入文档接口，因此这里不显示任何文档数量或处理进度。</p>
      </div>
    </div>
  </section>
</template>
