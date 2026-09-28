<script setup lang="ts">
/**
 * 共享来源证据卡（UI_SPEC 6.6 / 7.5）。
 *
 * RAG 引用面板与学业规划计算证据**共用同一处**展示实现，保证两处不会漂移。
 * 只渲染调用方传入的真实字段；缺失字段不显示空标签；原文始终以文本节点输出，
 * 不解释 HTML、不生成链接。
 */
withDefaults(
  defineProps<{
    index?: number | null
    selected?: boolean
    focused?: boolean
    selectable?: boolean
    selectLabel?: string
    chunkId?: string | null
    fileName: string
    documentVersion?: string | null
    effectiveFrom?: string | null
    datasetVersion?: string | null
    pageNumber?: number | null
    sheetName?: string | null
    rowStart?: number | null
    rowEnd?: number | null
    sectionTitle?: string | null
    quote: string
  }>(),
  {
    index: null,
    selected: false,
    focused: false,
    selectable: false,
    selectLabel: '选择引用',
    chunkId: null,
    documentVersion: null,
    effectiveFrom: null,
    datasetVersion: null,
    pageNumber: null,
    sheetName: null,
    rowStart: null,
    rowEnd: null,
    sectionTitle: null,
  },
)

const emit = defineEmits<{ select: []; open: [] }>()

function hasNumber(value: number | null): boolean {
  return value !== null && value !== undefined
}

function hasText(value: string | null): boolean {
  return value !== null && value !== undefined && value !== ''
}

function rowRange(
  start: number | null,
  end: number | null,
): string | null {
  if (!hasNumber(start)) {
    return null
  }
  if (end === null || end === start) {
    return `第 ${start} 行`
  }
  return `${start}-${end}`
}
</script>

<template>
  <article
    class="ep-evidence-card"
    :class="{ 'is-selected': selected, 'is-focused': focused }"
    :data-chunk-id="chunkId ?? undefined"
  >
    <button
      v-if="selectable"
      type="button"
      class="ep-evidence-card__select"
      :aria-pressed="selected"
      :aria-label="`${selectLabel} ${index}：${fileName}`"
      @click="emit('select')"
    >
      <span v-if="index !== null" class="ep-evidence-card__index">[{{ index }}]</span>
      <span class="ep-evidence-card__file">{{ fileName }}</span>
    </button>

    <p v-else class="ep-evidence-card__heading">
      <span v-if="index !== null" class="ep-evidence-card__index">[{{ index }}]</span>
      <span class="ep-evidence-card__file">{{ fileName }}</span>
    </p>

    <dl class="ep-evidence-card__meta">
      <div v-if="hasText(documentVersion)" class="ep-evidence-card__meta-item">
        <dt>版本</dt>
        <dd>{{ documentVersion }}</dd>
      </div>
      <div v-if="hasText(effectiveFrom)" class="ep-evidence-card__meta-item">
        <dt>生效</dt>
        <dd>{{ effectiveFrom }}</dd>
      </div>
      <div v-if="hasText(datasetVersion)" class="ep-evidence-card__meta-item">
        <dt>数据集</dt>
        <dd>{{ datasetVersion }}</dd>
      </div>
    </dl>

    <div class="ep-evidence-card__locator">
      <ElTag v-if="hasNumber(pageNumber)" size="small" effect="plain" disable-transitions>
        第 {{ pageNumber }} 页
      </ElTag>
      <ElTag v-if="hasText(sheetName)" size="small" effect="plain" disable-transitions>
        {{ sheetName }}
      </ElTag>
      <ElTag
        v-if="rowRange(rowStart, rowEnd)"
        size="small"
        effect="plain"
        disable-transitions
      >
        {{ rowRange(rowStart, rowEnd) }}
      </ElTag>
      <ElTag v-if="hasText(sectionTitle)" size="small" effect="plain" disable-transitions>
        {{ sectionTitle }}
      </ElTag>
    </div>

    <pre class="ep-evidence-card__quote">{{ quote }}</pre>

    <ElButton
      class="ep-evidence-card__open"
      size="small"
      text
      type="primary"
      @click="emit('open')"
    >
      查看原文
    </ElButton>
  </article>
</template>

<style scoped>
.ep-evidence-card {
  padding: var(--ep-space-4);
  border: 1px solid var(--ep-color-border);
  border-radius: var(--ep-radius-card);
  background: #fff;
}

/* 选择引用是真正的 button：Tab 可聚焦，Enter / Space 原生激活（UI_SPEC 9 / 11.3） */
.ep-evidence-card__select {
  display: flex;
  gap: var(--ep-space-2);
  align-items: center;
  width: 100%;
  min-width: 0;
  min-height: 40px;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.ep-evidence-card__heading {
  display: flex;
  gap: var(--ep-space-2);
  align-items: baseline;
  margin: 0;
}

.ep-evidence-card__select:hover .ep-evidence-card__file {
  color: var(--ep-color-primary);
}

.ep-evidence-card__select:focus-visible {
  outline: 2px solid var(--ep-color-primary);
  outline-offset: 2px;
  border-radius: 4px;
}

.ep-evidence-card.is-selected {
  border-color: var(--ep-color-evidence);
  box-shadow: inset 3px 0 0 var(--ep-color-evidence);
  background: var(--ep-color-evidence-soft);
}

/* 由缺失必修课 / 冲突提醒定位过来的证据卡：用描边而非颜色单独表达 */
.ep-evidence-card.is-focused {
  border-color: var(--ep-color-evidence);
  outline: 2px solid var(--ep-color-evidence);
  outline-offset: 1px;
}

.ep-evidence-card__index {
  color: var(--ep-color-evidence);
  font-weight: 700;
}

.ep-evidence-card__file {
  font-weight: 600;
  overflow-wrap: anywhere;
}

.ep-evidence-card__meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--ep-space-3);
  margin: var(--ep-space-2) 0 0;
  font-size: 12px;
  color: var(--ep-color-text-muted);
}

.ep-evidence-card__meta-item {
  display: flex;
  gap: var(--ep-space-1);
}

.ep-evidence-card__meta-item dt {
  margin: 0;
}

.ep-evidence-card__meta-item dd {
  margin: 0;
  color: var(--ep-color-text-secondary);
}

.ep-evidence-card__locator {
  display: flex;
  flex-wrap: wrap;
  gap: var(--ep-space-1);
  margin-top: var(--ep-space-2);
}

.ep-evidence-card__quote {
  margin: var(--ep-space-3) 0;
  padding: var(--ep-space-3);
  border-radius: 8px;
  background: var(--ep-color-surface-muted, #f8fafc);
  color: var(--ep-color-text-secondary);
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

/* UI_SPEC 9：可点击目标桌面最小 40×40，移动端最小 44×44（显式声明，不靠行高撑大） */
.ep-evidence-card__open {
  min-height: 40px;
  padding: 0 var(--ep-space-2);
}

.ep-evidence-card__open:focus-visible {
  outline: 2px solid var(--ep-color-primary);
  outline-offset: 2px;
}

@media (max-width: 767px) {
  .ep-evidence-card__select,
  .ep-evidence-card__open {
    min-height: 44px;
  }
}
</style>
