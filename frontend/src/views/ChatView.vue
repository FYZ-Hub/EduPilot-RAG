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
        title="问答能力尚未接入"
        description="流式回答、引用抽屉与拒答状态尚未实现；是否可用以健康接口返回的 chat 能力为准，本页不生成任何回答或来源。"
      />

      <div class="ep-placeholder">
        <ElIcon :size="24"><ChatDotRound /></ElIcon>
        <p class="ep-placeholder__title">暂未提供输入区</p>
        <p>在问答能力接入前，这里不展示任何回答、来源或检索范围选项。</p>
      </div>
    </div>
  </section>
</template>
