<script setup lang="ts">
/** 聊天页右侧的证据栏：制度用阅读器定位并高亮被引用片段，经验显示发布内容。 */
import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Close, TopRight } from '@element-plus/icons-vue'
import { api, isAbortError, type Document } from '../api'
import { day, type PublishedExperience } from '../experience'
import { showError } from '../feedback'
import DocumentReader from './DocumentReader.vue'

export type Evidence =
  | { kind: 'document'; id: string; page: number; nodes: string[] }
  | { kind: 'experience'; id: string }

const props = defineProps<{ evidence: Evidence }>()
const emit = defineEmits<{ close: [] }>()
const router = useRouter()
const info = ref<Document | null>(null)
const experience = ref<PublishedExperience | null>(null)

watch(() => props.evidence, async (evidence, _, onCleanup) => {
  if (evidence.kind === 'document') {
    if (info.value?.id !== evidence.id) info.value = null
    return
  }
  experience.value = null
  const request = new AbortController()
  onCleanup(() => request.abort())
  try {
    experience.value = await api<PublishedExperience>(`/experiences/${evidence.id}/published`, { signal: request.signal })
  } catch (error) {
    if (isAbortError(error)) return
    showError(error)
    emit('close')
  }
}, { immediate: true })
</script>

<template>
  <aside class="evidence">
    <header>
      <span class="kind-tag" :class="evidence.kind">{{ evidence.kind === 'document' ? '制度' : '经验' }}</span>
      <div class="title">
        <template v-if="evidence.kind === 'document'">
          <h2>{{ info?.title || '制度原文' }}</h2>
          <p v-if="info">{{ info.filename }} · 共 {{ info.page_count }} 页</p>
        </template>
        <template v-else>
          <h2>{{ experience?.tags.join(' · ') || '经验详情' }}</h2>
          <p v-if="experience">{{ experience.author }} 分享 · 经管理员审核 · {{ day(experience.published_at) }} 发布</p>
        </template>
      </div>
      <el-button
        v-if="evidence.kind === 'document'" text :icon="TopRight" aria-label="在知识库中打开" title="在知识库中打开"
        @click="router.push({ name: 'library', params: { tab: 'documents', id: evidence.id }, query: { page: evidence.page } })"
      />
      <el-button text :icon="Close" aria-label="关闭证据栏" title="关闭" @click="emit('close')" />
    </header>
    <DocumentReader
      v-if="evidence.kind === 'document'" stacked
      :document-id="evidence.id" :page="evidence.page" :focus-nodes="evidence.nodes" @loaded="info = $event"
    />
    <div v-else v-loading="!experience" class="experience">
      <template v-if="experience">
        <el-alert
          v-if="experience.conflict" type="warning" :closable="false" show-icon title="这条经验与制度的规定不同"
          :description="experience.conflict_note || '管理员已标记，请对照制度原文。'"
        />
        <p class="content">{{ experience.content }}</p>
      </template>
    </div>
  </aside>
</template>

<style scoped>
.evidence { width: 46%; min-width: 460px; max-width: 640px; flex-shrink: 0; display: flex; flex-direction: column; min-height: 0; border-left: 1px solid var(--border); background: var(--bg); }
header .el-button { margin: 0; padding: 0 8px; }
header { flex-shrink: 0; display: flex; align-items: center; gap: 10px; padding: 10px 8px 10px 16px; background: var(--surface); border-bottom: 1px solid var(--border); }
.title { flex: 1; min-width: 0; }
.title h2 { margin: 0; font-size: var(--fs-l); font-weight: 600; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.title p { margin: 2px 0 0; font-size: var(--fs-xs); color: var(--text-3); }
.experience { flex: 1; overflow: auto; padding: 20px 24px; background: var(--surface); }
.content { margin: 16px 0 0; font-size: 15px; line-height: 2; color: var(--text); white-space: pre-wrap; }
.el-alert + .content { margin-top: 16px; }

/* 窗口不够宽时证据栏盖在对话上方。 */
@media (max-width: 1100px) {
  .evidence { position: fixed; top: 0; right: 0; bottom: 0; z-index: 20; width: min(640px, 100vw); min-width: 0; box-shadow: var(--shadow); }
}
</style>
