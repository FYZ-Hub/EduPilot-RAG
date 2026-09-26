<script setup lang="ts">
import { ChatDotRound } from '@element-plus/icons-vue'

import PageHeader from '@/components/common/PageHeader.vue'
</script>

<template>
  <section class="ep-view">
    <PageHeader title="RAG 问答" description="按检索证据回答，并展示原文引用与定位" />

    <div class="ep-card">
      <ElAlert
        type="info"
        :closable="false"
        show-icon
        title="功能将在后续阶段实现"
        description="流式回答、引用抽屉与拒答状态将在阶段 6 与阶段 8 实现。本阶段不显示任何回答或来源。"
      />

      <div class="ep-placeholder">
        <ElIcon :size="24"><ChatDotRound /></ElIcon>
        <p class="ep-placeholder__title">问答能力尚未接入</p>
        <p>当前健康接口如实返回 chat = unconfigured，因此此处不提供输入区，也不生成任何回答。</p>
      </div>
    </div>
  </section>
</template>
