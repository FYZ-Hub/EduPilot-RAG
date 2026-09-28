<script setup lang="ts">
/**
 * RAG 问答页面（UI_SPEC 6）。
 *
 * 能力门控：只有「后端连接成功 + ``capabilities.chat === 'ready'`` +
 * ``documents.counts.retrievable > 0`` + 问题合法 + 没有进行中的请求」才允许发送；
 * 任何情况下都不会为了演示伪造 ready、自动加载演示资料或调用真实模型。
 *
 * 回答正文、引用与原文全部通过 Vue 文本节点渲染，不做任何原始 HTML 注入。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import { MAX_CHAT_MESSAGE_CHARS, type ChatCitation } from '@/api/chat'
import { isNearBottom } from '@/domain/scroll'
import ErrorAlert from '@/components/common/ErrorAlert.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import SourceDrawer from '@/components/common/SourceDrawer.vue'
import ChatComposer from '@/components/chat/ChatComposer.vue'
import ChatDetailsDialog from '@/components/chat/ChatDetailsDialog.vue'
import EvidencePanel from '@/components/chat/EvidencePanel.vue'
import MessageBubble from '@/components/chat/MessageBubble.vue'
import RetrievalScopePanel from '@/components/chat/RetrievalScopePanel.vue'
import { useChatStore, type ChatFilterKey, type ChatTurnMessage } from '@/stores/chat'
import { useDocumentsStore } from '@/stores/documents'
import { useHealthStore } from '@/stores/health'

/** 只描述检索动作，不写死任何课程、日期或学分等事实。 */
const SUGGESTIONS = ['查询某门课程的学分要求', '查询某学期的考试安排', '查询某项教学管理规定']

interface Notice {
  tone: 'info' | 'warning' | 'error'
  title: string
  description: string
  action?: 'recheck' | 'knowledge'
}

const health = useHealthStore()
const documents = useDocumentsStore()
const chat = useChatStore()

const scopePanel = ref<InstanceType<typeof RetrievalScopePanel> | null>(null)
const scrollElement = ref<HTMLElement | null>(null)
const autoFollow = ref(true)
const detailsOpen = ref(false)
const detailsMessage = ref<ChatTurnMessage | null>(null)
const sourceOpen = ref(false)
const sourceChunkId = ref<string | null>(null)

const connected = computed(() => health.connection === 'connected')
const backendDown = computed(() => health.connection === 'error')
const chatCapability = computed(() => health.capabilities?.chat ?? null)
const retrievable = computed(() => documents.counts.retrievable)
const bootstrapping = computed(
  () => health.connection === 'unknown' || health.connection === 'loading' || !documents.loaded,
)

const canAsk = computed(
  () => connected.value && chatCapability.value === 'ready' && retrievable.value > 0 && !bootstrapping.value,
)

const disabledReason = computed(() => {
  if (backendDown.value) {
    return '后端服务不可连接，问答已禁用'
  }
  if (chatCapability.value === 'unconfigured') {
    return '大模型尚未配置：请在后端 .env 中配置后重启服务'
  }
  if (chatCapability.value === 'unavailable') {
    return '问答能力不可用，请重新检测后端状态'
  }
  if (retrievable.value === 0) {
    return '暂无可检索资料：请先前往知识库加载演示资料或上传文档'
  }
  return ''
})

const notice = computed<Notice | null>(() => {
  if (chat.validationError) {
    return { tone: 'warning', title: '无法提交问题', description: chat.validationError.message }
  }
  if (backendDown.value) {
    return {
      tone: 'error',
      title: '后端服务不可连接',
      description: '问答已禁用；请先在上方错误条中重新检测后端服务，本页不会尝试建立流式连接。',
      action: 'recheck',
    }
  }
  if (bootstrapping.value) {
    return null
  }
  if (chatCapability.value === 'unconfigured') {
    return {
      tone: 'info',
      title: '大模型尚未配置',
      description:
        '后端未配置大模型（capabilities.chat = unconfigured）。请在后端 .env 中配置 LLM_BASE_URL、LLM_API_KEY 与 LLM_MODEL 并重启服务；本页不会收集、保存或展示任何 API Key。',
    }
  }
  if (chatCapability.value === 'unavailable') {
    return {
      tone: 'error',
      title: '问答能力不可用',
      description: '后端返回 chat = unavailable，暂时无法发起问答。',
      action: 'recheck',
    }
  }
  if (retrievable.value === 0) {
    return {
      tone: 'warning',
      title: '暂无可检索资料',
      description: '知识库中还没有可检索（retrievable）的文档，问答已禁用；处理中或候选文档不计入。',
      action: 'knowledge',
    }
  }
  return null
})

/** 与消息气泡无关的失败（例如开流前的网络错误）在页面级展示。 */
const pageError = computed(() => {
  const last = chat.messages.at(-1)
  if (!chat.streamError) {
    return null
  }
  if (last && last.role === 'assistant' && last.errorCode !== null) {
    return null
  }
  return chat.streamError
})

/** 正在流式输出的助手消息；结束后由 messages 中的正式消息接管。 */
const liveMessage = computed<ChatTurnMessage | null>(() =>
  chat.streaming
    ? {
        id: 'live-answer',
        role: 'assistant',
        content: chat.streamingContent,
        outcome: null,
        citations: chat.citations,
        requestId: chat.requestId,
        reasonCode: chat.reasonCode,
        retryable: chat.retryable,
        errorCode: null,
        errorMessage: null,
        interrupted: false,
        stopped: false,
      }
    : null,
)

function onScroll(): void {
  const element = scrollElement.value
  if (!element) {
    return
  }
  autoFollow.value = isNearBottom({
    scrollTop: element.scrollTop,
    scrollHeight: element.scrollHeight,
    clientHeight: element.clientHeight,
  })
}

async function scrollToBottom(): Promise<void> {
  await nextTick()
  const element = scrollElement.value
  if (element) {
    element.scrollTop = element.scrollHeight
  }
}

watch(
  () => chat.streamingContent,
  () => {
    if (autoFollow.value) {
      void scrollToBottom()
    }
  },
)

async function recheck(): Promise<void> {
  await Promise.all([health.load(), documents.load(), chat.loadOptions()])
}

async function onSubmit(): Promise<void> {
  autoFollow.value = true
  await chat.send()
}

async function onRetry(): Promise<void> {
  autoFollow.value = true
  await chat.retry()
}

function useSuggestion(text: string): void {
  chat.question = text
}

function onFilterUpdate(payload: { key: ChatFilterKey; value: string | number | null }): void {
  chat.updateFilter(payload.key, payload.value)
}

function openDetails(message: ChatTurnMessage): void {
  detailsMessage.value = message
  detailsOpen.value = true
}

function openSource(citation: ChatCitation): void {
  sourceChunkId.value = citation.chunk_id
  sourceOpen.value = true
}

async function copyAnswer(message: ChatTurnMessage): Promise<void> {
  try {
    await navigator.clipboard?.writeText(message.content)
    ElMessage.success('已复制回答')
  } catch {
    ElMessage.warning('浏览器未允许写入剪贴板')
  }
}

onMounted(() => {
  if (health.connection === 'unknown') {
    void health.load()
  }
  void documents.load()
  void chat.loadOptions()
})

onBeforeUnmount(() => {
  chat.stop()
  documents.stop()
})
</script>

<template>
  <section class="ep-view ep-chat">
    <PageHeader title="RAG 问答" description="按检索证据回答，并展示原文引用与定位">
      <template #actions>
        <span class="ep-chat__retrievable">当前可检索文档：{{ retrievable }}</span>
      </template>
    </PageHeader>

    <p v-if="bootstrapping" class="ep-chat__loading">正在读取后端能力与知识库状态…</p>

    <ElAlert
      v-if="notice"
      class="ep-chat__notice"
      :type="notice.tone"
      :closable="false"
      show-icon
      :title="notice.title"
    >
      <p class="ep-chat__notice-text">{{ notice.description }}</p>
      <div v-if="notice.action" class="ep-chat__notice-actions">
        <ElButton
          v-if="notice.action === 'recheck'"
          class="ep-chat__recheck"
          size="small"
          @click="recheck"
        >
          重新检测
        </ElButton>
        <RouterLink
          v-if="notice.action === 'knowledge'"
          class="ep-chat__notice-link"
          to="/knowledge"
        >
          前往知识库
        </RouterLink>
      </div>
    </ElAlert>

    <ErrorAlert
      v-if="pageError"
      class="ep-chat__error"
      :error="pageError"
      title="问答请求失败"
    />

    <div class="ep-chat__layout">
      <div class="ep-chat__main">
        <RetrievalScopePanel
          ref="scopePanel"
          :options="chat.options"
          :loading="chat.optionsLoading"
          :error="chat.optionsError"
          :filters="chat.filters"
          :active-filters="chat.activeFilters"
          @update="onFilterUpdate"
          @remove="chat.updateFilter($event, null)"
          @clear="chat.clearFilters()"
          @retry="chat.loadOptions()"
        />

        <div
          ref="scrollElement"
          class="ep-chat__scroll"
          :data-auto-follow="autoFollow ? 'on' : 'off'"
          @scroll="onScroll"
        >
          <div v-if="chat.messages.length === 0 && !chat.streaming" class="ep-chat__empty">
            <p class="ep-chat__empty-title">还没有问答记录</p>
            <div v-if="canAsk" class="ep-chat__suggestions">
              <button
                v-for="suggestion in SUGGESTIONS"
                :key="suggestion"
                type="button"
                class="ep-suggestion"
                @click="useSuggestion(suggestion)"
              >
                {{ suggestion }}
              </button>
            </div>
            <p v-else class="ep-chat__empty-hint">
              问答能力可用后，可以从建议问题开始；点击建议只会填入输入框。
            </p>
          </div>

          <MessageBubble
            v-for="message in chat.messages"
            :key="message.id"
            :message="message"
            :active-citation-index="chat.selectedCitationIndex"
            :can-retry="chat.canRetry && message.id === chat.messages.at(-1)?.id"
            @select-citation="chat.selectCitation($event)"
            @copy="copyAnswer(message)"
            @details="openDetails(message)"
            @retry="onRetry"
            @adjust-scope="scopePanel?.open()"
          />

          <MessageBubble
            v-if="liveMessage"
            :message="liveMessage"
            streaming
            :active-citation-index="chat.selectedCitationIndex"
            @select-citation="chat.selectCitation($event)"
            @copy="copyAnswer(liveMessage)"
            @details="openDetails(liveMessage)"
          />
        </div>

        <div class="ep-chat__composer">
          <ChatComposer
            v-model="chat.question"
            :disabled="!canAsk"
            :disabled-reason="disabledReason"
            :streaming="chat.streaming"
            :max-chars="MAX_CHAT_MESSAGE_CHARS"
            @submit="onSubmit"
            @stop="chat.stop()"
          />
        </div>
      </div>

      <aside class="ep-chat__aside">
        <EvidencePanel
          :citations="chat.citations"
          :selected-index="chat.selectedCitationIndex"
          @select="chat.selectCitation($event)"
          @open-source="openSource"
        />
      </aside>
    </div>

    <ChatDetailsDialog v-model="detailsOpen" :message="detailsMessage" />
    <SourceDrawer v-model="sourceOpen" :chunk-id="sourceChunkId" />
  </section>
</template>

<style scoped>
.ep-chat {
  gap: var(--ep-space-4);
}

.ep-chat__retrievable {
  color: var(--ep-color-text-secondary);
  font-size: 13px;
}

.ep-chat__loading {
  margin: 0;
  color: var(--ep-color-text-muted);
  font-size: 13px;
}

.ep-chat__notice-text {
  margin: 0 0 var(--ep-space-2);
  font-size: 13px;
  overflow-wrap: anywhere;
}

.ep-chat__notice-actions {
  display: flex;
  gap: var(--ep-space-3);
  align-items: center;
}

.ep-chat__layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  gap: var(--ep-space-5);
  align-items: start;
}

.ep-chat__main {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-4);
  width: 100%;
  max-width: 820px;
  min-width: 0;
}

.ep-chat__scroll {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-4);
  min-height: 240px;
  max-height: 58vh;
  padding-right: var(--ep-space-1);
  overflow-y: auto;
  overflow-wrap: anywhere;
}

.ep-chat__empty {
  display: flex;
  flex-direction: column;
  gap: var(--ep-space-3);
  padding: var(--ep-space-6);
  border: 1px dashed var(--ep-color-border);
  border-radius: var(--ep-radius-card);
  text-align: center;
}

.ep-chat__empty-title {
  margin: 0;
  font-weight: 600;
}

.ep-chat__empty-hint {
  margin: 0;
  color: var(--ep-color-text-muted);
  font-size: 13px;
}

.ep-chat__suggestions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--ep-space-2);
  justify-content: center;
}

.ep-suggestion {
  padding: var(--ep-space-2) var(--ep-space-4);
  border: 1px solid var(--ep-color-border);
  border-radius: 999px;
  background: #fff;
  color: var(--ep-color-text);
  font-family: inherit;
  font-size: 13px;
  cursor: pointer;
}

.ep-suggestion:hover {
  border-color: var(--ep-color-primary);
  color: var(--ep-color-primary);
}

.ep-chat__composer {
  display: flex;
  flex-direction: column;
}

.ep-chat__aside {
  min-width: 0;
}

@media (max-width: 1199px) {
  .ep-chat__layout {
    grid-template-columns: minmax(0, 1fr);
  }

  .ep-chat__main {
    max-width: none;
  }
}
</style>
