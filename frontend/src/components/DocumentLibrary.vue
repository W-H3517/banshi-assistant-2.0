<script setup lang="ts">
/** 知识库的制度页签：左侧制度列表，右侧阅读器（切分预览 / 原文）。 */
import { computed, onMounted, ref, watch } from 'vue'
import { Document as DocumentIcon, Search, TopRight, Upload } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, sessionSignal, type Document } from '../api'
import { day } from '../experience'
import { guard, showError } from '../feedback'
import { loadWorkspace } from '../session'
import { useAuthStore } from '../stores/auth'
import { useChatStore } from '../stores/chat'
import { useLibraryStore } from '../stores/library'
import DocumentReader from './DocumentReader.vue'

const props = defineProps<{
  /** 当前打开的制度；为空时自动打开列表里的第一份。 */
  id?: string
  page?: number
}>()
const emit = defineEmits<{ select: [id?: string, page?: number] }>()
const auth = useAuthStore()
const chat = useChatStore()
const library = useLibraryStore()
/** 阅读器加载完成后给出的制度信息，用于右侧标题。 */
const opened = ref<Document | null>(null)
const searched = ref(false)
const uploading = ref(false)
const deleting = ref(false)
const resetting = ref(false)
const fileInput = ref<HTMLInputElement>()
const fileUrl = computed(() => `/api/documents/${props.id}/file#page=${props.page || 1}`)

async function search() {
  await guard(async () => { await library.load(); searched.value = library.search.trim() !== '' })
}
watch(() => [props.id, library.documents] as const, ([id, documents]) => {
  if (!id && documents.length) emit('select', documents[0]!.id, documents[0]!.match_page)
}, { immediate: true })
watch(() => props.id, () => { opened.value = null })

async function upload(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]; if (!file) return
  uploading.value = true
  try {
    const data = new FormData(); data.append('file', file)
    const created = await api<Document>('/documents', { method: 'POST', body: data })
    library.search = ''; searched.value = false
    await library.load()
    emit('select', created.id)
    ElMessage.success('PDF 已完成解析和索引，可以查询了')
  } catch (error) { showError(error) }
  finally { uploading.value = false; input.value = '' }
}
async function remove() {
  const doc = opened.value
  if (!doc) return
  const signal = sessionSignal()
  try {
    await ElMessageBox.confirm(`确定删除“${doc.title}”吗？原文和检索索引将一并删除，历史对话保留，但其中的原文链接将失效。`, '删除制度', {
      confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning',
    })
  } catch { return }
  // 确认框不随登录过期关闭；会话已结束时，浏览器里的登录可能已换成别的账号，不能再发删除。
  if (signal.aborted) return
  deleting.value = true
  try {
    await api(`/documents/${doc.id}`, { method: 'DELETE' })
    await library.load()
    emit('select')
    ElMessage.success('制度及检索索引已删除')
  } catch (error) { showError(error) }
  finally { deleting.value = false }
}
async function resetDemo() {
  const signal = sessionSignal()
  try {
    await ElMessageBox.confirm('将清空全部制度（包括真实制度）及所有用户的聊天记录，并恢复三份初始模拟制度。正在生成的回答会停止。此操作无法撤销，本地源文件和登录账号保留。', '重置测试数据', {
      confirmButtonText: '清空并重置', cancelButtonText: '取消', type: 'warning',
    })
  } catch { return }
  // 同上：会话已结束时不能再以别的账号发起重置。
  if (signal.aborted) return
  resetting.value = true
  try {
    await api('/admin/reset-demo', { method: 'POST' })
    chat.current = null; chat.conversations = []; chat.draft = ''; chat.answers = {}
    library.search = ''; searched.value = false
    await loadWorkspace()
    emit('select')
    ElMessage.success('已清空制度和聊天记录，并恢复三份模拟制度')
  } catch (error) { showError(error) }
  finally { resetting.value = false }
}
onMounted(() => guard(() => library.load()))
</script>

<template>
  <div class="split">
    <aside class="list-pane">
      <div class="list-head">
        <slot name="tabs" />
        <div class="row">
          <el-input v-model="library.search" placeholder="输入问题或关键词搜索制度" maxlength="300" :prefix-icon="Search" clearable @keyup.enter="search" @clear="search" />
          <el-button @click="search">搜索</el-button>
        </div>
        <div v-if="auth.isAdmin" class="row">
          <el-button type="primary" :icon="Upload" :loading="uploading" :disabled="resetting" @click="fileInput?.click()">
            {{ uploading ? '正在解析并建立索引…' : '上传 PDF' }}
          </el-button>
          <el-button text type="danger" :loading="resetting" :disabled="uploading || deleting" @click="resetDemo">重置测试数据</el-button>
          <input ref="fileInput" type="file" accept=".pdf,application/pdf" hidden @change="upload" />
        </div>
      </div>
      <div class="list">
        <button
          v-for="doc in library.documents" :key="doc.id" class="list-item" :class="{ on: doc.id === id }"
          @click="emit('select', doc.id, doc.match_page)"
        >
          <div class="item-title"><el-icon class="file"><DocumentIcon /></el-icon><span>{{ doc.title }}</span></div>
          <div class="item-meta">
            PDF · {{ doc.page_count }} 页<template v-if="searched && doc.match_page"> · 第 {{ doc.match_page }} 页命中</template> · {{ day(doc.created_at) }}
          </div>
          <div class="item-text">{{ doc.preview }}</div>
        </button>
        <el-empty v-if="!library.documents.length" :image-size="80" :description="searched ? '没有找到匹配的制度，试试其他关键词' : '还没有制度'" />
      </div>
      <div class="list-foot">
        {{ auth.isAdmin ? '支持文字及扫描 PDF，单份不超过 20 MB、200 页；扫描页在本机识别，耗时较长。暂不支持加密文件。' : '制度由管理员维护，所有登录成员均可查询。' }}
      </div>
    </aside>

    <section class="pane">
      <template v-if="id">
        <header class="pane-head">
          <span class="kind-tag document">制度</span>
          <div class="pane-title">
            <h2>{{ opened?.title || '制度原文' }}</h2>
            <p v-if="opened">{{ opened.filename }} · {{ opened.page_count }} 页 · {{ opened.chunk_count }} 个检索片段 · {{ day(opened.created_at) }} 上传</p>
          </div>
          <a class="el-button el-button--small" :href="fileUrl" target="_blank" rel="noopener noreferrer"><el-icon><TopRight /></el-icon><span>新窗口打开</span></a>
          <el-button v-if="auth.isAdmin" size="small" type="danger" plain :loading="deleting" :disabled="!opened || resetting" @click="remove">删除制度</el-button>
        </header>
        <DocumentReader :key="id" :document-id="id" :page="page" @loaded="opened = $event" />
      </template>
      <div v-else class="pane-empty"><el-empty description="从左侧选择一份制度" /></div>
    </section>
  </div>
</template>

<style scoped>
.file { color: var(--doc); font-size: 18px; }
</style>
