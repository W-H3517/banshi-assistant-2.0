<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { ChatDotRound, Collection, Document as DocumentIcon, Plus, Search, Upload, ArrowRight, SwitchButton, Close, Menu } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, streamTurn, type User, type Conversation, type Document, type Message } from './api'
import Markdown from './Markdown.vue'
const ChunkPreview = defineAsyncComponent(() => import('./ChunkPreview.vue'))

const user = ref<User | null>(null)
const booting = ref(true)
const username = ref('admin')
const password = ref('')
const signingIn = ref(false)
const loginError = ref('')
const page = ref<'chat' | 'knowledge'>('chat')
const conversations = ref<Conversation[]>([])
const current = ref<Conversation | null>(null)
const documents = ref<Document[]>([])
const availableDocumentIds = ref<string[]>([])
const search = ref('')
const selectedDocument = ref<Document | null>(null)
const documentOpen = ref(false)
const inspectionDocument = ref<Document | null>(null)
const documentPage = ref(1)
const pdfUrl = computed(() => selectedDocument.value ? `/api/documents/${selectedDocument.value.id}/file#page=${documentPage.value}` : '')
const draft = ref('')
const sending = ref(false)
const uploading = ref(false)
const deleting = ref<string | null>(null)
const resetting = ref(false)
const modelReady = ref(true)
const answerValues = ref<Record<string, string>>({})
const scrollArea = ref<HTMLElement>()
const mobileOpen = ref(false)
const fileInput = ref<HTMLInputElement>()
const messages = computed(() => current.value?.transcript || [])
const busy = computed(() => sending.value || current.value?.status === 'running')
const readyAnswers = computed(() => current.value?.pending.every(q => answerValues.value[q.id]?.trim()))
const statusLabel: Record<string, string> = { running: '生成中', waiting: '等待回答', failed: '生成失败', interrupted: '已中断' }
const examples = ['外地培训回来，报销需要准备什么材料？', '出差住宿的报销限额是多少？', '购买办公用品需要走哪些流程？']

function showError(error: unknown) { ElMessage.error(error instanceof Error ? error.message : '操作失败') }
async function guard(action: () => Promise<unknown>) { try { await action() } catch (error) { showError(error) } }
async function listConversations() { conversations.value = await api<Conversation[]>('/conversations') }
async function loadDocuments() {
  const all = await api<Document[]>('/documents')
  availableDocumentIds.value = all.map(doc => doc.id)
  documents.value = search.value.trim() ? await api<Document[]>('/documents?q=' + encodeURIComponent(search.value)) : all
}
async function initialize() {
  await Promise.all([listConversations(), loadDocuments()])
  const health = await api<{ model_configured: boolean }>('/health')
  modelReady.value = health.model_configured
}
async function login() {
  signingIn.value = true; loginError.value = ''
  try {
    user.value = await api<User>('/login', { method: 'POST', body: JSON.stringify({ username: username.value, password: password.value }) })
    password.value = ''; await initialize()
  } catch (error) { loginError.value = (error as Error).message }
  finally { signingIn.value = false }
}
function clearSession() {
  user.value = null; current.value = null; conversations.value = []; documents.value = []
  inspectionDocument.value = null
  availableDocumentIds.value = []
  selectedDocument.value = null; documentOpen.value = false; draft.value = ''; answerValues.value = {}
}
async function logout() {
  if (busy.value && current.value) await api(`/conversations/${current.value.id}/stop`, { method: 'POST' })
  await api('/logout', { method: 'POST' }); clearSession()
}
async function openConversation(id: string) {
  if (sending.value) return
  current.value = await api<Conversation>(`/conversations/${id}`)
  await loadDocuments()
  answerValues.value = {}; draft.value = ''; page.value = 'chat'; mobileOpen.value = false
  await scrollDown()
}
function newChat() {
  if (sending.value) return
  current.value = null; draft.value = ''; answerValues.value = {}; page.value = 'chat'; mobileOpen.value = false
}
async function scrollDown() { await nextTick(); scrollArea.value?.scrollTo({ top: scrollArea.value.scrollHeight }) }
async function openDocument(id: string, page = 1) {
  await guard(async () => {
    selectedDocument.value = await api<Document>(`/documents/${id}`)
    documentPage.value = Math.max(1, Math.min(page, selectedDocument.value.page_count))
    documentOpen.value = true
  })
}
async function resetDemo() {
  try {
    await ElMessageBox.confirm('将清空全部制度（包括真实制度）及所有用户的聊天记录，并恢复三份初始模拟制度。正在生成的回答会停止。此操作无法撤销，本地源文件和登录账号保留。', '重置测试数据', {
      confirmButtonText: '清空并重置', cancelButtonText: '取消', type: 'warning',
    })
  } catch { return }
  resetting.value = true
  try {
    await api('/admin/reset-demo', { method: 'POST' })
    current.value = null; conversations.value = []; draft.value = ''; answerValues.value = {}
    documentOpen.value = false; selectedDocument.value = null; search.value = ''
    await initialize()
    ElMessage.success('已清空制度和聊天记录，并恢复三份模拟制度')
  } catch (error) { showError(error) }
  finally { resetting.value = false }
}
async function deleteDocument(doc: Document) {
  try {
    await ElMessageBox.confirm(`确定删除“${doc.title}”吗？原文和检索索引将一并删除，历史对话保留，但其中的原文链接将失效。`, '删除制度', {
      confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning',
    })
  } catch { return }
  deleting.value = doc.id
  try {
    await api(`/documents/${doc.id}`, { method: 'DELETE' })
    if (selectedDocument.value?.id === doc.id) { documentOpen.value = false; selectedDocument.value = null }
    await loadDocuments()
    ElMessage.success('制度及检索索引已删除')
  } catch (error) { showError(error) }
  finally { deleting.value = null }
}
async function upload(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]; if (!file) return
  uploading.value = true
  try {
    const data = new FormData(); data.append('file', file)
    await api('/documents', { method: 'POST', body: data })
    search.value = ''; await loadDocuments(); ElMessage.success('PDF 已完成解析和索引，可以查询了')
  } catch (error) { showError(error) }
  finally { uploading.value = false; input.value = '' }
}
async function send(text?: string) {
  if (busy.value || !modelReady.value) return
  const prompt = text || draft.value.trim()
  const answering = Boolean(current.value?.pending.length)
  if (answering ? !readyAnswers.value : !prompt) return
  sending.value = true
  const previousDraft = draft.value
  let id = ''
  let failed = false
  try {
    if (!current.value) current.value = await api<Conversation>('/conversations', { method: 'POST' })
    const convo = current.value
    id = convo.id
    const answers = answering ? { ...answerValues.value } : {}
    const userText = answering ? convo.pending.map(q => `${q.question}：${answers[q.id]}`).join('\n') : prompt
    convo.transcript.push({ role: 'user', text: userText })
    convo.transcript.push({ role: 'assistant', text: '', tools: [], sources: [] })
    const message = convo.transcript[convo.transcript.length - 1] as Message
    convo.status = 'running'; convo.error = ''; draft.value = ''
    await scrollDown()
    await streamTurn(id, { prompt: answering ? '' : prompt, answers }, event => {
      if (event.type === 'delta') message.text += event.text
      if (event.type === 'tool') {
        const tool = message.tools!.find(t => t.id === event.id)
        if (tool) tool.status = event.status
        else message.tools!.push(event)
      }
      if (event.type === 'source') message.sources!.push(event)
      if (event.type === 'error') { failed = true; ElMessage.warning(event.message) }
      void scrollDown()
    })
  } catch (error) { failed = true; showError(error) }
  finally {
    sending.value = false
    if (user.value) {
      if (id) await guard(async () => { current.value = await api<Conversation>(`/conversations/${id}`) })
      await guard(listConversations)
    }
    if (failed && !answering) draft.value = previousDraft || prompt
    if (!failed) answerValues.value = {}
    await scrollDown()
  }
}
async function stop() {
  if (!current.value) return
  await api(`/conversations/${current.value.id}/stop`, { method: 'POST' })
  if (!sending.value) current.value = await api<Conversation>(`/conversations/${current.value.id}`)
}
function composerKey(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); void send() }
}
onMounted(async () => {
  window.addEventListener('session-expired', clearSession)
  try { user.value = await api<User>('/me'); await initialize() } catch { /* 未登录时显示登录页。 */ }
  finally { booting.value = false }
})
onUnmounted(() => window.removeEventListener('session-expired', clearSession))
</script>

<template>
  <div v-if="booting" class="loading-screen">正在打开办事助手…</div>
  <main v-else-if="!user" class="login-page">
    <section class="login-intro">
      <div class="brand"><div class="brand-mark">办</div><span>办事助手</span></div>
      <div class="login-copy"><p class="eyebrow">部门制度 · 一处查询</p><h1>把制度查清楚，<br>把事情办明白。</h1><p>查阅制度原文，厘清办理流程。<br>让每一个回答，都有据可循。</p><div class="intro-tags"><span>制度知识库</span><span>智能问答</span><span>个人会话</span></div></div>
      <small>内部试用版 · 请核对制度适用范围</small>
    </section>
    <section class="login-form-area"><form class="login-card" @submit.prevent="login">
      <p class="eyebrow">欢迎回来</p><h2>登录你的工作空间</h2><p class="muted">制度共享，聊天记录仅自己可见。</p>
      <label for="username">账号</label><el-input id="username" v-model="username" size="large" autocomplete="username" placeholder="输入账号" />
      <label for="password">密码</label><el-input id="password" v-model="password" size="large" type="password" show-password autocomplete="current-password" placeholder="输入密码" />
      <p v-if="loginError" class="error-text" role="alert">{{ loginError }}</p>
      <el-button class="login-submit" type="primary" size="large" native-type="submit" :loading="signingIn">进入工作空间 <el-icon><ArrowRight /></el-icon></el-button>
      <div class="demo-note">本地测试账号：admin / alice / bob<br>初始密码：demo12345</div>
    </form></section>
  </main>
  <div v-else class="workspace">
    <div v-if="mobileOpen" class="sidebar-shade" @click="mobileOpen = false"></div>
    <aside class="sidebar" :class="{ 'mobile-open': mobileOpen }">
      <div class="brand"><div class="brand-mark">办</div><div>办事助手<small>部门工作空间</small></div></div>
      <el-button class="new-chat" :icon="Plus" :disabled="sending" @click="newChat">开启新对话</el-button>
      <nav aria-label="主导航"><button :class="{ active: page === 'chat' }" @click="page = 'chat'; mobileOpen = false"><el-icon><ChatDotRound /></el-icon>办事助手</button><button :class="{ active: page === 'knowledge' }" @click="page = 'knowledge'; mobileOpen = false; guard(loadDocuments)"><el-icon><Collection /></el-icon>制度知识库<span>{{ documents.length }}</span></button></nav>
      <div class="history-label">我的对话 <span>仅自己可见</span></div>
      <div class="history-list"><p v-if="!conversations.length" class="empty-history">从第一个问题开始</p><button v-for="c in conversations" :key="c.id" :disabled="sending" :class="{ selected: current?.id === c.id }" @click="guard(() => openConversation(c.id))"><span>{{ c.title }}</span><i v-if="c.status === 'waiting'"></i></button></div>
      <div class="user-bar"><div class="avatar">{{ user.name.slice(-1) }}</div><div><strong>{{ user.name }}</strong><small>{{ user.role === 'admin' ? '管理员' : '普通员工' }}</small></div><el-button text :icon="SwitchButton" aria-label="退出登录" @click="guard(logout)" /></div>
    </aside>
    <main class="main-panel">
      <header class="topbar"><div><button class="mobile-menu" aria-label="打开菜单" @click="mobileOpen = true"><el-icon><Menu /></el-icon></button><span>{{ page === 'chat' ? '办事助手' : '制度知识库' }}</span><span class="header-divider">/</span><span class="muted">{{ page === 'chat' ? '让办理有据可循' : '部门共享资料' }}</span></div><span class="workspace-badge"><i></i>内部试用</span></header>
      <section v-if="page === 'chat'" class="chat-page">
        <div v-if="!modelReady" class="model-notice">模型密钥尚未配置，暂不能对话。制度知识库可正常使用。</div>
        <div ref="scrollArea" class="chat-scroll">
          <div v-if="!messages.length" class="welcome">
            <div class="welcome-icon"><el-icon><ChatDotRound /></el-icon></div><p class="eyebrow">你好，{{ user.name }}</p><h1>今天有什么需要帮忙？</h1><p class="welcome-sub">告诉我你要办什么事，我会查找制度依据，<br>在需要时向你确认细节。</p>
            <div class="suggestions"><button v-for="(example, index) in examples" :key="example" :disabled="busy || !modelReady" @click="send(example)"><span class="suggestion-index">0{{ index + 1 }}</span><span>{{ example }}</span><el-icon><ArrowRight /></el-icon></button></div>
            <span class="welcome-foot">可以问流程、所需材料，或具体的制度条款</span>
          </div>
          <div v-else class="message-list">
            <article v-for="(message, index) in messages" :key="index" class="message" :class="message.role">
              <div v-if="message.role === 'assistant'" class="assistant-avatar">办</div><div class="message-body"><span class="message-author">{{ message.role === 'user' ? '你' : '办事助手' }}</span>
                <div v-if="message.tools?.length" class="tool-row"><span v-for="tool in message.tools" :key="tool.id" class="tool-chip"><i :class="tool.status"></i>{{ tool.name === 'ask_question' ? '问问题' : '查资料' }} · {{ tool.status === 'running' ? '进行中' : tool.status === 'waiting' ? '待回答' : '已完成' }}</span></div>
                <Markdown v-if="message.role === 'assistant' && message.text" :text="message.text" :available-document-ids="availableDocumentIds" @document="openDocument" /><p v-else-if="message.role === 'user'" class="user-text">{{ message.text }}</p><div v-else-if="busy && index === messages.length - 1" class="thinking">正在思考<span>…</span></div>
                <div v-if="message.sources?.length" class="source-row"><span>查阅资料</span><button v-for="source in message.sources" :key="`${source.id}-${source.page}`" :disabled="!availableDocumentIds.includes(source.id)" @click="openDocument(source.id, source.page)"><el-icon><DocumentIcon /></el-icon>{{ source.title }} · 第 {{ source.page }} 页<span v-if="!availableDocumentIds.includes(source.id)">（资料已删除）</span></button></div>
              </div>
            </article>
            <div v-if="current?.pending.length && !sending" class="question-card"><div class="question-heading">补充一点信息，继续办理</div><div v-for="question in current.pending" :key="question.id" class="question-item"><label :for="question.id">{{ question.question }}</label><div class="question-options"><button v-for="option in question.options || []" :key="option" :class="{ chosen: answerValues[question.id] === option }" @click="answerValues[question.id] = option">{{ option }}</button></div><el-input :id="question.id" v-model="answerValues[question.id]" placeholder="也可以直接输入你的回答" :maxlength="12000" /></div><el-button type="primary" :disabled="!readyAnswers || !modelReady" @click="send()">提交回答并继续 <el-icon><ArrowRight /></el-icon></el-button></div>
            <el-alert v-if="current?.error" :title="current.error" type="warning" show-icon :closable="false" />
          </div>
        </div>
        <div class="composer-area"><div v-if="busy" class="generation-status"><span>正在处理你的问题…</span><el-button size="small" @click="guard(stop)">停止生成</el-button></div><div v-else-if="current && statusLabel[current.status]" class="generation-status"><span>{{ statusLabel[current.status] }}</span></div><div class="composer" :class="{ disabled: busy || Boolean(current?.pending.length) }"><textarea v-model="draft" aria-label="输入办事问题" :disabled="busy || Boolean(current?.pending.length) || !modelReady" :placeholder="current?.pending.length ? '请先回答上方问题' : '输入你想了解的事项…'" rows="2" maxlength="12000" @keydown="composerKey"></textarea><div class="composer-bottom"><span>基于制度回答 · 支持继续追问</span><el-button type="primary" :disabled="!draft.trim() || busy || Boolean(current?.pending.length) || !modelReady" :icon="ArrowRight" aria-label="发送消息" @click="send()" /></div></div><p class="composer-note">回答仅供参考，请以现行制度原文为准。</p></div>
      </section>
      <section v-else class="knowledge-page"><div class="page-heading"><div><p class="eyebrow">KNOWLEDGE BASE</p><h1>制度知识库</h1><p class="muted">查找制度、查看原文，让每一步都有依据。</p></div><div v-if="user.role === 'admin'" class="knowledge-admin-actions"><el-button type="danger" plain :loading="resetting" :disabled="uploading || deleting !== null" @click="resetDemo">重置测试数据</el-button><el-button :disabled="resetting" type="primary" :icon="Upload" :loading="uploading" @click="fileInput?.click()">{{ uploading ? '正在解析并建立索引…' : '上传 PDF' }}</el-button></div><input ref="fileInput" type="file" accept=".pdf,application/pdf" hidden @change="upload" /></div>
        <div class="knowledge-toolbar"><el-input v-model="search" placeholder="输入问题或关键词搜索制度" maxlength="300" :prefix-icon="Search" clearable @keyup.enter="guard(loadDocuments)" @clear="guard(loadDocuments)" /><el-button @click="guard(loadDocuments)">搜索</el-button><span>{{ documents.length }} 份制度</span></div>
        <div v-if="documents.length" class="document-grid"><article v-for="doc in documents" :key="doc.id" class="document-entry"><button class="document-card" @click="openDocument(doc.id, doc.match_page)"><div class="document-card-top"><span class="file-icon"><el-icon><DocumentIcon /></el-icon></span><span class="file-type">PDF · {{ doc.page_count }} 页</span></div><h3>{{ doc.title }}</h3><p>{{ doc.preview }}</p><div class="document-card-footer"><span>{{ doc.filename }}</span><span>查看原文 ↗</span></div></button><div v-if="user.role === 'admin'" class="document-actions"><el-button size="small" :disabled="resetting || deleting !== null" @click="inspectionDocument = doc">切分预览</el-button><el-button type="danger" plain size="small" :loading="deleting === doc.id" :disabled="deleting !== null || resetting" :aria-label="`删除${doc.title}`" @click="deleteDocument(doc)">删除制度</el-button></div></article></div><el-empty v-else description="没有找到匹配的制度，试试其他关键词" />
        <p class="knowledge-note">{{ user.role === 'admin' ? '支持文字及扫描 PDF，单份不超过 20 MB、200 页；扫描页会在本机识别，耗时较长，完成后即可查询。暂不支持加密文件。' : '制度由管理员维护，所有登录成员均可查询。' }}</p>
      </section>
    </main>
    <ChunkPreview v-if="inspectionDocument && user.role === 'admin'" :document="inspectionDocument" @close="inspectionDocument = null" />
    <el-drawer v-model="documentOpen" :title="selectedDocument?.title || '制度原文'" size="min(980px, 100vw)" destroy-on-close><div v-if="selectedDocument" class="document-reader"><div class="reader-meta"><span>{{ selectedDocument.filename }}</span><el-tag size="small" effect="plain">制度原文</el-tag></div><div class="pdf-controls"><span>定位到第</span><el-input-number v-model="documentPage" :min="1" :max="selectedDocument.page_count" :precision="0" size="small" aria-label="PDF 页码" /><span>页 / 共 {{ selectedDocument.page_count }} 页</span><a :href="pdfUrl" target="_blank" rel="noopener noreferrer">新窗口打开原文 ↗</a></div><iframe :key="pdfUrl" :src="pdfUrl" class="pdf-frame" title="制度 PDF 原文"></iframe><p class="knowledge-note">引用使用 PDF 物理页码。若内嵌预览不可用，请在新窗口打开原文。</p></div></el-drawer>
  </div>
</template>
