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
        title="规划页面尚未接入"
        description="课程记录与培养规则导入、确定性学分计算界面尚未实现；本页不展示任何学分、缺口或规划结果。"
      />

      <div class="ep-placeholder">
        <ElIcon :size="24"><DataAnalysis /></ElIcon>
        <p class="ep-placeholder__title">暂未提供规划结果</p>
        <p>在规划界面接入前，这里不展示任何学分数字或证据来源。</p>
      </div>
    </div>
  </section>
</template>
