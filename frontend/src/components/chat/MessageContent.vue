<script setup lang="ts">
/**
 * 回答正文渲染（UI_SPEC 6.5）：结构化但不解释 HTML。
 *
 * 解析结果全部由 Vue 文本节点/元素输出，**不使用原始 HTML 注入**，
 * 因此模型返回的 ``<script>``、``<a href>``、事件属性只会作为文字显示。
 */
import { computed } from 'vue'

import type { ChatCitation } from '@/api/chat'
import { parseMarkdown } from '@/domain/chatMarkdown'

import MessageInline from './MessageInline.vue'

const props = withDefaults(
  defineProps<{
    content: string
    citations?: ChatCitation[]
    activeIndex?: number | null
  }>(),
  { citations: () => [], activeIndex: null },
)

const emit = defineEmits<{ select: [index: number] }>()

const blocks = computed(() => parseMarkdown(props.content))
</script>

<template>
  <div class="ep-md">
    <template v-for="(block, blockIndex) in blocks" :key="blockIndex">
      <pre v-if="block.type === 'code'" class="ep-md__code">{{ block.value }}</pre>

      <ul v-else-if="block.type === 'list' && !block.ordered" class="ep-md__list">
        <li v-for="(item, itemIndex) in block.items" :key="itemIndex">
          <MessageInline
            :nodes="item"
            :citations="citations"
            :active-index="activeIndex"
            @select="emit('select', $event)"
          />
        </li>
      </ul>

      <ol v-else-if="block.type === 'list'" class="ep-md__list ep-md__list--ordered">
        <li v-for="(item, itemIndex) in block.items" :key="itemIndex">
          <MessageInline
            :nodes="item"
            :citations="citations"
            :active-index="activeIndex"
            @select="emit('select', $event)"
          />
        </li>
      </ol>

      <blockquote v-else-if="block.type === 'quote'" class="ep-md__quote">
        <p v-for="(line, lineIndex) in block.lines" :key="lineIndex">
          <MessageInline
            :nodes="line"
            :citations="citations"
            :active-index="activeIndex"
            @select="emit('select', $event)"
          />
        </p>
      </blockquote>

      <p v-else class="ep-md__paragraph">
        <template v-for="(line, lineIndex) in block.lines" :key="lineIndex">
          <br v-if="lineIndex > 0" />
          <MessageInline
            :nodes="line"
            :citations="citations"
            :active-index="activeIndex"
            @select="emit('select', $event)"
          />
        </template>
      </p>
    </template>
  </div>
</template>

<style scoped>
.ep-md {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-3);
  font-size: 15px;
  line-height: 1.7;
  overflow-wrap: anywhere;
}

.ep-md__paragraph {
  margin: 0;
}

.ep-md__list {
  margin: 0;
  padding-left: var(--ep-space-5);
}

.ep-md__list li {
  margin-bottom: var(--ep-space-1);
  overflow-wrap: anywhere;
}

.ep-md__quote {
  margin: 0;
  padding: var(--ep-space-2) var(--ep-space-4);
  border-left: 3px solid var(--ep-color-border);
  color: var(--ep-color-text-secondary);
}

.ep-md__quote p {
  margin: 0;
}

.ep-md__code {
  margin: 0;
  padding: var(--ep-space-3);
  border-radius: 8px;
  background: #0f172a;
  color: #e2e8f0;
  font-family: var(--ep-font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: 13px;
  line-height: 1.6;
  overflow-x: auto;
  white-space: pre;
}
</style>
