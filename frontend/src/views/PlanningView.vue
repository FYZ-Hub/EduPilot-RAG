<script setup lang="ts">
import { DataAnalysis } from '@element-plus/icons-vue'

import PageHeader from '@/components/common/PageHeader.vue'
</script>

<template>
  <section class="ep-view">
    <PageHeader title="学业规划" description="由确定性规则计算学分缺口与缺失必修课" />

    <div class="ep-card">
      <ElAlert
        type="info"
        :closable="false"
        show-icon
        title="功能将在后续阶段实现"
        description="课程记录与培养规则导入、确定性学分计算将在阶段 7 与阶段 8 实现。本阶段不显示任何学分数字。"
      />

      <div class="ep-placeholder">
        <ElIcon :size="24"><DataAnalysis /></ElIcon>
        <p class="ep-placeholder__title">尚未接入学业规划能力</p>
        <p>当前健康接口如实返回 planning = unavailable，因此这里不展示任何学分、缺口或规划结果。</p>
      </div>
    </div>
  </section>
</template>
