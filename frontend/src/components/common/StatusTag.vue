<script setup lang="ts">
/**
 * 文档状态标签（UI_SPEC 5.5）。
 *
 * 文案与色调完全来自 :func:`describeDocumentStatus`，
 * 同时依赖 processing status 与 ``retrievable`` / ``activation_state``。
 */
import { computed } from 'vue'

import type { DocumentListItem } from '@/api/documents'
import { describeDocumentStatus } from '@/domain/documents'

const props = defineProps<{ document: DocumentListItem }>()

const descriptor = computed(() => describeDocumentStatus(props.document))
</script>

<template>
  <ElTag
    class="ep-status-tag"
    size="small"
    effect="light"
    disable-transitions
    :type="descriptor.tone"
  >
    {{ descriptor.label }}
  </ElTag>
</template>
