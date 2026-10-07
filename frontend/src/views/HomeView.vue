<script setup lang="ts">
/**
 * 首页（UI_SPEC 第 8 节）。
 *
 * 克制的教育科技风格：轻量背景动效（渐变光斑 + SVG 连线）、内容入场动画、
 * 功能卡片悬停反馈。三项核心能力与进入按钮一目了然。
 *
 * 数据真实性：所有数字只读现有 Pinia store（``documents`` / ``health``）；
 * 加载中或读取失败时显示文字说明，绝不编造数量。本页不发起任何网络请求，
 * 加载由 AppLayout 统一负责，避免重复拉取。
 */
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { ChatDotRound, DataAnalysis, FolderOpened } from '@element-plus/icons-vue'

import { useDocumentsStore } from '@/stores/documents'
import { useHealthStore } from '@/stores/health'

type CapabilityKey = 'documents' | 'chat' | 'planning'

interface CapabilityCard {
  key: CapabilityKey
  route: 'knowledge' | 'chat' | 'planning'
  label: string
  description: string
  cta: string
  icon: typeof FolderOpened
}

const health = useHealthStore()
const documents = useDocumentsStore()

/** 三项核心能力，顺序与主导航一致（UI_SPEC 2.3）。 */
const cards: CapabilityCard[] = [
  {
    key: 'documents',
    route: 'knowledge',
    label: '知识库管理',
    description: '加载演示资料或上传文档，统一解析、切片与索引，来源随时可预览。',
    cta: '打开知识库',
    icon: FolderOpened,
  },
  {
    key: 'chat',
    route: 'chat',
    label: 'RAG 问答',
    description: '按检索到的原文证据回答，引用可定位到页码、章节或工作表行。',
    cta: '开始问答',
    icon: ChatDotRound,
  },
  {
    key: 'planning',
    route: 'planning',
    label: '学业规划',
    description: '由确定性规则引擎计算学分缺口与冲突，多源资料交叉核对。',
    cta: '查看规划',
    icon: DataAnalysis,
  },
]

/** 能力状态文案：三态，不使用「故障」这类断言。 */
const CAPABILITY_TEXT: Record<string, string> = {
  ready: '可用',
  unconfigured: '未配置',
  unavailable: '不可用',
}

function capabilityText(key: CapabilityKey): string {
  if (health.connection === 'error') {
    return '后端不可连接'
  }
  const state = health.capabilities?.[key]
  if (!state) {
    return '正在检测'
  }
  return CAPABILITY_TEXT[state] ?? '状态未知'
}

function capabilityReady(key: CapabilityKey): boolean {
  return health.capabilities?.[key] === 'ready'
}

/** 资料数量只取真实文档列表；未就绪或查询失败时不给任何数字。 */
const libraryValue = computed(() =>
  documents.loaded ? `${documents.retrievableCount} / ${documents.total}` : '—',
)

const libraryNote = computed(() => {
  if (documents.loaded) {
    return `共 ${documents.total} 份资料，其中 ${documents.retrievableCount} 份可检索`
  }
  return documents.error ? '知识库概况暂不可用' : '正在读取知识库概况…'
})

const connectionText = computed(() => health.connectionText)
</script>

<template>
  <div class="ep-home">
    <div class="ep-home__bg" aria-hidden="true">
      <span class="ep-home__glow ep-home__glow--one" />
      <span class="ep-home__glow ep-home__glow--two" />
      <svg class="ep-home__network" viewBox="0 0 1200 620" preserveAspectRatio="xMidYMid slice" focusable="false">
        <g class="ep-home__links">
          <line x1="150" y1="140" x2="420" y2="90" />
          <line x1="420" y1="90" x2="700" y2="160" />
          <line x1="700" y1="160" x2="980" y2="100" />
          <line x1="150" y1="140" x2="300" y2="360" />
          <line x1="300" y1="360" x2="700" y2="160" />
          <line x1="300" y1="360" x2="640" y2="470" />
          <line x1="640" y1="470" x2="980" y2="100" />
          <line x1="640" y1="470" x2="960" y2="520" />
        </g>
        <g class="ep-home__nodes">
          <circle cx="150" cy="140" r="4" />
          <circle cx="420" cy="90" r="3" />
          <circle cx="700" cy="160" r="5" />
          <circle cx="980" cy="100" r="3" />
          <circle cx="300" cy="360" r="4" />
          <circle cx="640" cy="470" r="3" />
          <circle cx="960" cy="520" r="4" />
        </g>
      </svg>
    </div>

    <section class="ep-home__hero ep-home__reveal">
      <p class="ep-home__eyebrow">校园多源文档 RAG 学业规划助手</p>
      <h1 class="ep-home__title">用可信资料，回答学业问题</h1>
      <p class="ep-home__lead">
        资料可管理、回答有依据、规划由规则计算。三项能力共享同一套解析、切片与索引管线。
      </p>
      <div class="ep-home__actions">
        <RouterLink class="ep-home__primary" :to="{ name: 'knowledge' }">进入知识库</RouterLink>
        <RouterLink class="ep-home__ghost" :to="{ name: 'planning' }">查看学业规划</RouterLink>
      </div>
    </section>

    <section class="ep-home__stats ep-home__reveal" aria-label="实时概况">
      <div class="ep-home__stat">
        <p class="ep-home__stat-label">可检索文档</p>
        <p class="ep-home__stat-value" :data-loaded="documents.loaded">{{ libraryValue }}</p>
        <p class="ep-home__stat-note">{{ libraryNote }}</p>
      </div>
      <div class="ep-home__stat">
        <p class="ep-home__stat-label">后端连接</p>
        <p class="ep-home__stat-value" :data-loaded="health.connection === 'connected'">
          {{ connectionText }}
        </p>
        <p class="ep-home__stat-note">
          {{ health.runModeLabel ? `运行模式 ${health.runModeLabel}` : '等待健康接口返回运行模式' }}
        </p>
      </div>
    </section>

    <section class="ep-home__cards" aria-label="核心能力">
      <article
        v-for="(card, index) in cards"
        :key="card.key"
        class="ep-home__card ep-home__reveal"
        :style="{ animationDelay: `${120 + index * 60}ms` }"
      >
        <div class="ep-home__card-head">
          <span class="ep-home__card-icon" aria-hidden="true">
            <ElIcon :size="20"><component :is="card.icon" /></ElIcon>
          </span>
          <span
            class="ep-home__pill"
            :data-state="health.capabilities?.[card.key] ?? 'unknown'"
          >
            {{ capabilityText(card.key) }}
          </span>
        </div>
        <h2 class="ep-home__card-title">{{ card.label }}</h2>
        <p class="ep-home__card-text">{{ card.description }}</p>
        <RouterLink
          class="ep-home__card-cta"
          :to="{ name: card.route }"
          :data-ready="capabilityReady(card.key)"
        >
          {{ card.cta }}
          <span aria-hidden="true">→</span>
        </RouterLink>
      </article>
    </section>
  </div>
</template>

<style scoped>
.ep-home {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-6);
  isolation: isolate;
}

/* ---- 轻量背景动效：渐变光斑 + SVG 连线，只动 transform / opacity ---- */
.ep-home__bg {
  position: absolute;
  inset: calc(-1 * var(--ep-content-padding));
  z-index: -1;
  overflow: hidden;
  border-radius: var(--ep-radius-card);
  pointer-events: none;
}

.ep-home__glow {
  position: absolute;
  display: block;
  width: 420px;
  height: 420px;
  border-radius: 50%;
  background-color: var(--ep-color-primary);
  opacity: 0.06;
  filter: blur(64px);
  animation: ep-home-glow 18s ease-in-out infinite;
}

.ep-home__glow--one {
  top: -140px;
  right: -60px;
}

.ep-home__glow--two {
  bottom: -180px;
  left: -80px;
  width: 360px;
  height: 360px;
  background-color: var(--ep-color-evidence);
  animation-delay: -6s;
}

.ep-home__network {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  color: var(--ep-color-primary);
  animation: ep-home-drift 24s ease-in-out infinite;
}

.ep-home__links line {
  stroke: currentColor;
  stroke-width: 1;
  opacity: 0.14;
}

.ep-home__nodes circle {
  fill: currentColor;
  opacity: 0.3;
}

@keyframes ep-home-drift {
  0%,
  100% {
    transform: translate3d(0, 0, 0);
  }
  50% {
    transform: translate3d(0, -16px, 0);
  }
}

@keyframes ep-home-glow {
  0%,
  100% {
    opacity: 0.05;
    transform: scale(1);
  }
  50% {
    opacity: 0.09;
    transform: scale(1.06);
  }
}

/* ---- 内容入场 ---- */
.ep-home__reveal {
  animation: ep-home-rise 220ms ease-out both;
}

@keyframes ep-home-rise {
  from {
    opacity: 0;
    transform: translate3d(0, 12px, 0);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

/* ---- 首屏 ---- */
.ep-home__hero {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-3);
  padding: var(--ep-space-7) var(--ep-space-6);
  background-color: var(--ep-color-surface);
  border: 1px solid var(--ep-color-border-light);
  border-radius: var(--ep-radius-card);
}

.ep-home__eyebrow {
  font-size: 12px;
  line-height: 18px;
  font-weight: 500;
  color: var(--ep-color-primary);
}

.ep-home__title {
  font-size: 24px;
  line-height: 32px;
  font-weight: 700;
}

.ep-home__lead {
  max-width: 640px;
  font-size: 14px;
  line-height: 22px;
  color: var(--ep-color-text-secondary);
}

.ep-home__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--ep-space-2);
  margin-top: var(--ep-space-2);
}

.ep-home__primary,
.ep-home__ghost,
.ep-home__card-cta {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--ep-space-1);
  min-height: 40px;
  padding: 0 var(--ep-space-4);
  border-radius: var(--ep-radius-card);
  font-size: 14px;
  text-decoration: none;
  transition:
    background-color 180ms ease,
    color 180ms ease,
    border-color 180ms ease,
    transform 180ms ease;
}

.ep-home__primary {
  background-color: var(--ep-color-primary);
  color: var(--ep-color-surface);
}

.ep-home__primary:hover {
  background-color: var(--ep-color-primary-hover);
}

.ep-home__ghost {
  background-color: var(--ep-color-surface);
  color: var(--ep-color-text-secondary);
  border: 1px solid var(--ep-color-border);
}

.ep-home__ghost:hover {
  color: var(--ep-color-primary);
  border-color: var(--ep-color-primary);
  background-color: var(--ep-color-primary-soft);
}

.ep-home__primary:focus-visible,
.ep-home__ghost:focus-visible,
.ep-home__card-cta:focus-visible {
  box-shadow: var(--ep-focus-ring);
}

/* ---- 实时概况 ---- */
.ep-home__stats {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--ep-space-4);
}

.ep-home__stat {
  padding: var(--ep-space-5);
  background-color: var(--ep-color-surface);
  border: 1px solid var(--ep-color-border-light);
  border-radius: var(--ep-radius-card);
}

.ep-home__stat-label {
  font-size: 12px;
  line-height: 18px;
  font-weight: 500;
  color: var(--ep-color-text-secondary);
}

.ep-home__stat-value {
  margin-top: var(--ep-space-1);
  font-size: 28px;
  line-height: 36px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--ep-color-text);
}

.ep-home__stat-value[data-loaded='false'] {
  color: var(--ep-color-text-muted);
  font-size: 18px;
  line-height: 26px;
  font-weight: 600;
}

.ep-home__stat-note {
  margin-top: var(--ep-space-1);
  font-size: 13px;
  line-height: 20px;
  color: var(--ep-color-text-muted);
}

/* ---- 核心能力卡片 ---- */
.ep-home__cards {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--ep-space-4);
}

.ep-home__card {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-2);
  padding: var(--ep-space-6);
  background-color: var(--ep-color-surface);
  border: 1px solid var(--ep-color-border-light);
  border-radius: var(--ep-radius-card);
  transition:
    transform 180ms ease,
    box-shadow 180ms ease,
    border-color 180ms ease;
}

.ep-home__card:hover,
.ep-home__card:focus-within {
  transform: translate3d(0, -2px, 0);
  border-color: var(--ep-color-border);
  box-shadow: var(--ep-shadow-overlay);
}

.ep-home__card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--ep-space-2);
}

.ep-home__card-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  color: var(--ep-color-primary);
  background-color: var(--ep-color-primary-soft);
  border-radius: var(--ep-radius-card);
}

.ep-home__pill {
  font-size: 12px;
  line-height: 18px;
  font-weight: 500;
  color: var(--ep-color-text-secondary);
  padding: 2px var(--ep-space-2);
  border-radius: var(--ep-radius-card);
  background-color: var(--ep-color-surface-subtle);
}

.ep-home__pill[data-state='ready'] {
  color: var(--ep-color-success);
  background-color: var(--ep-color-surface-subtle);
}

.ep-home__pill[data-state='unconfigured'],
.ep-home__pill[data-state='unavailable'] {
  color: var(--ep-color-warning);
}

.ep-home__card-title {
  font-size: 16px;
  line-height: 24px;
  font-weight: 600;
}

.ep-home__card-text {
  flex: 1;
  font-size: 14px;
  line-height: 22px;
  color: var(--ep-color-text-secondary);
}

.ep-home__card-cta {
  align-self: flex-start;
  padding: 0;
  min-height: 40px;
  color: var(--ep-color-primary);
  font-weight: 500;
}

.ep-home__card-cta:hover {
  gap: var(--ep-space-2);
  color: var(--ep-color-primary-hover);
}

@media (max-width: 1199px) {
  .ep-home__cards {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 767px) {
  .ep-home__bg {
    inset: calc(-1 * var(--ep-space-4));
  }

  .ep-home__hero {
    padding: var(--ep-space-6) var(--ep-space-4);
  }

  .ep-home__stats,
  .ep-home__cards {
    grid-template-columns: 1fr;
  }

  .ep-home__primary,
  .ep-home__ghost,
  .ep-home__card-cta {
    min-height: 44px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .ep-home__glow,
  .ep-home__network,
  .ep-home__reveal {
    animation: none;
  }

  .ep-home__card,
  .ep-home__primary,
  .ep-home__ghost,
  .ep-home__card-cta {
    transition: none;
  }
}
</style>
