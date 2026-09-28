<script setup lang="ts">
/**
 * 行内节点的安全渲染（UI_SPEC 6.5）：全部通过 Vue 文本节点输出。
 *
 * 引用编号只有在该 ``citation_index`` 真的有 citation 事件时才渲染为可聚焦按钮，
 * 尚未到达时渲染为禁用占位；任何情况下都不会从正文推断文件名或位置。
 */
import type { ChatCitation } from '@/api/chat'
import type { InlineNode } from '@/domain/chatMarkdown'

const props = withDefaults(
  defineProps<{
    nodes: InlineNode[]
    citations?: ChatCitation[]
    activeIndex?: number | null
  }>(),
  { citations: () => [], activeIndex: null },
)

const emit = defineEmits<{ select: [index: number] }>()

function isAvailable(index: number): boolean {
  return props.citations.some((item) => item.citation_index === index)
}
</script>

<template>
  <template v-for="(node, index) in nodes" :key="index">
    <code v-if="node.type === 'code'" class="ep-md__inline-code">{{ node.value }}</code>
    <strong v-else-if="node.type === 'strong'">{{ node.value }}</strong>
    <em v-else-if="node.type === 'em'">{{ node.value }}</em>
    <button
      v-else-if="node.type === 'citation' && isAvailable(node.index)"
      type="button"
      class="ep-citation"
      :class="{ 'is-active': activeIndex === node.index }"
      :aria-label="`查看引用 ${node.index}`"
      @click="emit('select', node.index)"
    >[{{ node.index }}]</button>
    <span
      v-else-if="node.type === 'citation'"
      class="ep-citation ep-citation--pending"
      aria-disabled="true"
    >[{{ node.index }}]</span>
    <span v-else class="ep-md__text">{{ node.value }}</span>
  </template>
</template>

<style scoped>
.ep-md__text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.ep-md__inline-code {
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--ep-color-surface-muted, #f1f5f9);
  font-family: var(--ep-font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: 0.92em;
  overflow-wrap: anywhere;
}

/* 引用标记始终使用证据色，与普通按钮区分（UI_SPEC 11.3） */
.ep-citation {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  margin: 0 2px;
  padding: 0 6px;
  border: 1px solid var(--ep-color-evidence);
  border-radius: 999px;
  background: var(--ep-color-evidence-soft);
  color: var(--ep-color-evidence);
  font-size: 12px;
  font-weight: 600;
  line-height: 20px;
  cursor: pointer;
}

.ep-citation:hover {
  background: var(--ep-color-evidence);
  color: #fff;
}

.ep-citation.is-active {
  background: var(--ep-color-evidence);
  color: #fff;
}

.ep-citation--pending {
  border-style: dashed;
  background: transparent;
  cursor: not-allowed;
  opacity: 0.75;
}
</style>
