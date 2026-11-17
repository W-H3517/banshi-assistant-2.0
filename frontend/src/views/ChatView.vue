<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowDown, ArrowRight, ChatDotRound, CopyDocument, List, WarningFilled } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { isAbortError, type Message, type Source, type Tool } from '../api'
import EvidencePanel, { type Evidence } from '../components/EvidencePanel.vue'
import Markdown from '../components/Markdown.vue'
import { sourceKey, useChatSession } from '../composables/useChatSession'
import { guard, showError } from '../feedback'
import { useAuthStore } from '../stores/auth'
import { useChatStore } from '../stores/chat'
import { useExperienceStore } from '../stores/experiences'
import { useLibraryStore } from '../stores/library'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const chat = useChatStore()
const library = useLibraryStore()
const experiences = useExperienceStore()
const { scrollArea, scrollDown, send } = useChatSession()
/** 右侧证据栏当前显示的制度片段或经验。 */
const evidence = ref<Evidence | null>(null)

const statusLabel: Record<string, string> = { waiting: '等待你的回答', failed: '生成失败', interrupted: '已中断' }
const toolLabel: Record<string, string> = {
  search_documents: '搜索正文', locate_document_structure: '定位制度', read_document_content: '读取内容', ask_question: '询问用户',
}
const scopeLabel: Record<string, string> = { node: '所选段落', article: '整条条款', neighbors: '相邻段落', page: '原页核对' }
const examples = ['外地培训回来，报销需要准备什么材料？', '出差住宿的报销限额是多少？', '购买办公用品需要走哪些流程？']

const available = (source: Source) =>
  (source.kind === 'experience' ? experiences.availableIds : library.availableIds).includes(source.id)
const shown = (source: Source) => evidence.value?.kind === source.kind && evidence.value.id === source.id
  && (source.kind === 'experience' || (evidence.value as { page: number }).page === source.page)

/** 检索过程的标题：进行中显示当前步骤，结束后汇总查阅了多少资料。 */
function processTitle(message: Message) {
  const tools = message.tools || []
  const running = tools.find(tool => tool.status === 'running')
  if (running) return `正在${toolLabel[running.name] || '处理'}…`
  const sources = message.sources || []
  const documents = new Set(sources.filter(s => s.kind === 'document').map(s => s.id)).size
  const count = sources.filter(s => s.kind === 'experience').length
  const parts = [documents && `${documents} 份制度`, count && `${count} 条经验`].filter(Boolean)
  return `${parts.length ? `查阅了 ${parts.join('、')}` : '检索过程'} · ${tools.length} 步`
}
/** 一步工具调用的结果说明。 */
function toolResult(tool: Tool) {
  const result = tool.result
  if (tool.name === 'ask_question') return result ? `回答：${result.answer}` : tool.status === 'waiting' ? '等待回答' : ''
  if (!result) return tool.status === 'done' ? '未取得结果' : ''
  if (tool.name === 'search_documents') {
    return `命中制度 ${result.items} 条（来自 ${result.documents} 份文件）· 经验 ${result.experiences} 条`
  }
  if (tool.name === 'locate_document_structure') return `找到 ${result.total} 处`
  if (result.experience !== undefined) return `经验全文 · ${result.experience}`
  const path = (result.path || []).filter(label => label !== result.title).join(' / ')
  const pages = result.pages?.length ? `第 ${result.pages.join('、')} 页` : ''
  return [`《${result.title}》${path}`, scopeLabel[tool.input.scope || 'node'], pages].filter(Boolean).join(' · ')
}

/** 打开制度引用：高亮本条回答在该页检索或读取过的段落。 */
function openDocument(message: Message, id: string, page: number) {
  const source = message.sources?.find(s => s.kind === 'document' && s.id === id && s.page === page)
  evidence.value = { kind: 'document', id, page, nodes: source?.kind === 'document' ? source.nodes : [] }
}
function openExperience(id: string) {
  void guard(async () => {
    await experiences.refresh()
    if (!experiences.availableIds.includes(id)) { ElMessage.warning('经验已下架'); return }
    evidence.value = { kind: 'experience', id }
  })
}
function openSource(message: Message, source: Source) {
  if (source.kind === 'experience') openExperience(source.id)
  else openDocument(message, source.id, source.page)
}
async function copyAnswer(text: string) {
  await guard(async () => {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制回答')
  })
}
function composerKey(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); void send() }
}

// 地址里的会话 ID 决定显示哪个会话；没有 ID 时是新对话。
watch(() => route.params.id as string | undefined, async id => {
  if (!id) { evidence.value = null; if (chat.current) chat.startNew(); return }
  if (id === chat.current?.id) { await scrollDown(); return }
  evidence.value = null
  try {
    await chat.open(id)
    await Promise.all([library.load(), experiences.refresh()])
    await scrollDown()
  } catch (error) {
    if (isAbortError(error)) return
    showError(error)
    await router.replace({ name: 'chat' })
  }
}, { immediate: true })

onMounted(() => guard(() => experiences.refresh()))
</script>

<template>
  <section class="chat">
    <div class="column">
      <div v-if="!auth.modelReady" class="notice">
        <el-icon><WarningFilled /></el-icon>模型尚未配置，暂不能对话。知识库可正常使用。
      </div>
      <div ref="scrollArea" class="scroll">
        <div v-if="!chat.messages.length" class="welcome">
          <div class="welcome-icon"><el-icon><ChatDotRound /></el-icon></div>
          <h1>你好，{{ auth.user?.name }}。今天要办什么事？</h1>
          <p>说清要办的事，我会查找制度和同事经验，需要时向你确认细节。</p>
          <div class="examples">
            <button v-for="example in examples" :key="example" :disabled="chat.busy || !auth.modelReady" @click="send(example)">
              <span>{{ example }}</span><el-icon><ArrowRight /></el-icon>
            </button>
          </div>
        </div>
        <div v-else class="thread">
          <template v-for="(message, index) in chat.messages" :key="index">
            <div v-if="message.role === 'user'" class="user"><div>{{ message.text }}</div></div>
            <article v-else class="assistant">
              <div class="mark">办</div>
              <div class="body">
                <details v-if="message.tools?.length" class="process">
                  <summary>
                    <el-icon><List /></el-icon>{{ processTitle(message) }}<el-icon class="chevron"><ArrowDown /></el-icon>
                  </summary>
                  <ol>
                    <li v-for="(tool, step) in message.tools" :key="tool.id">
                      <span class="step">{{ step + 1 }}</span>
                      <div>
                        <strong>{{ toolLabel[tool.name] || tool.name }}</strong>
                        <p v-if="tool.input.queries || tool.input.query || tool.input.question">
                          <span v-for="query in tool.input.queries || []" :key="query" class="query">{{ query }}</span>
                          <span v-if="tool.input.query" class="query">{{ tool.input.query }}</span>
                          <template v-if="tool.input.question">{{ tool.input.question }}</template>
                        </p>
                        <p v-if="toolResult(tool)">{{ toolResult(tool) }}</p>
                        <p v-else-if="tool.status === 'running'" class="running">进行中…</p>
                      </div>
                    </li>
                  </ol>
                </details>
                <Markdown
                  v-if="message.text" :text="message.text"
                  :available-document-ids="library.availableIds" :available-experience-ids="experiences.availableIds"
                  @document="(id, page) => openDocument(message, id, page)" @experience="openExperience"
                />
                <p v-else-if="chat.busy && index === chat.messages.length - 1" class="thinking">正在思考…</p>
                <div v-if="message.sources?.length" class="sources">
                  <span>查阅资料</span>
                  <button
                    v-for="source in message.sources" :key="sourceKey(source)" :disabled="!available(source)"
                    :class="{ on: shown(source), gone: !available(source) }" @click="openSource(message, source)"
                  >
                    <i :class="source.kind">{{ source.kind === 'experience' ? '经验' : '制度' }}</i>
                    {{ source.title }}<template v-if="source.kind === 'document'"> · 第 {{ source.page }} 页</template>
                    <template v-if="!available(source)">{{ source.kind === 'experience' ? '（已下架）' : '（资料已删除）' }}</template>
                  </button>
                </div>
                <button
                  v-if="message.text && !(chat.busy && index === chat.messages.length - 1)"
                  class="copy-answer" type="button" @click="copyAnswer(message.text)"
                ><el-icon><CopyDocument /></el-icon>复制</button>
              </div>
            </article>
          </template>
          <div v-if="chat.current?.pending.length && !chat.sending" class="questions">
            <h3>补充一点信息，继续办理</h3>
            <div v-for="question in chat.current.pending" :key="question.id" class="question">
              <label :for="question.id">{{ question.question }}</label>
              <div v-if="question.options?.length" class="options">
                <button v-for="option in question.options" :key="option" :class="{ on: chat.answers[question.id] === option }" @click="chat.answers[question.id] = option">{{ option }}</button>
              </div>
              <el-input :id="question.id" v-model="chat.answers[question.id]" placeholder="也可以直接输入你的回答" :maxlength="12000" />
            </div>
            <el-button type="primary" :disabled="!chat.readyAnswers || !auth.modelReady" @click="send()">提交回答并继续</el-button>
          </div>
          <el-alert v-if="chat.current?.error" :title="chat.current.error" type="warning" show-icon :closable="false" />
        </div>
      </div>
      <div class="composer-area">
        <div v-if="chat.busy" class="status">
          <span>正在处理你的问题…</span><el-button size="small" @click="guard(() => chat.stop())">停止生成</el-button>
        </div>
        <div v-else-if="chat.current && statusLabel[chat.current.status]" class="status">
          <span>{{ statusLabel[chat.current.status] }}</span>
        </div>
        <div class="composer" :class="{ disabled: chat.busy || Boolean(chat.current?.pending.length) }">
          <textarea
            v-model="chat.draft" aria-label="输入办事问题" rows="2" maxlength="12000"
            :disabled="chat.busy || Boolean(chat.current?.pending.length) || !auth.modelReady"
            :placeholder="chat.current?.pending.length ? '请先回答上方问题' : '描述你要办的事，或继续追问…'"
            @keydown="composerKey"
          ></textarea>
          <div class="composer-foot">
            <span>Enter 发送 · Shift + Enter 换行</span>
            <el-button type="primary" :disabled="!chat.draft.trim() || chat.busy || Boolean(chat.current?.pending.length) || !auth.modelReady" :icon="ArrowRight" aria-label="发送消息" @click="send()" />
          </div>
        </div>
        <p class="disclaimer">回答仅供参考，请以现行制度原文为准。</p>
      </div>
    </div>
    <EvidencePanel v-if="evidence" :evidence="evidence" @close="evidence = null" />
  </section>
</template>

<style scoped>
.chat { flex: 1; min-height: 0; display: flex; color: var(--text); font-size: var(--fs); }
.column { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.copy-answer { display: inline-flex; align-items: center; gap: 4px; height: 28px; margin-top: 10px; padding: 0 10px; border: 1px solid transparent; border-radius: var(--radius-s); background: none; color: var(--text-2); font-size: var(--fs-xs); }
.copy-answer:hover { background: var(--surface-2); color: var(--primary); }
.notice { display: flex; align-items: center; gap: 8px; padding: 10px 24px; background: var(--warn-soft); color: var(--warn-text); font-size: var(--fs-s); }
.scroll { flex: 1; overflow-y: auto; }

.welcome { max-width: 640px; margin: 0 auto; padding: clamp(32px, 12vh, 120px) 28px 28px; text-align: center; }
.welcome-icon { width: 52px; height: 52px; margin: 0 auto 20px; display: grid; place-items: center; border-radius: var(--radius-l); background: var(--primary-soft); color: var(--primary); font-size: 26px; }
.welcome h1 { margin: 0 0 10px; font-size: 24px; font-weight: 600; }
.welcome p { margin: 0; color: var(--text-3); line-height: 1.8; }
.examples { display: grid; gap: 10px; margin-top: 28px; }
.examples button { display: flex; align-items: center; gap: 12px; padding: 14px 16px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); text-align: left; font-size: var(--fs); color: var(--text); transition: border-color .15s, box-shadow .15s; }
.examples button:hover:not(:disabled) { border-color: var(--primary); box-shadow: var(--shadow); }
.examples button:disabled { opacity: .55; cursor: not-allowed; }
.examples .el-icon { margin-left: auto; color: var(--text-3); }

.thread { max-width: 780px; margin: 0 auto; padding: 28px 28px 8px; }
.user { display: flex; justify-content: flex-end; margin-bottom: 24px; }
.user div { max-width: 80%; padding: 11px 16px; border-radius: 12px 12px 4px 12px; background: var(--primary-soft); line-height: 1.75; white-space: pre-wrap; overflow-wrap: anywhere; }
.assistant { display: flex; gap: 12px; margin-bottom: 28px; }
.mark { flex-shrink: 0; width: 30px; height: 30px; display: grid; place-items: center; border-radius: var(--radius); background: var(--primary); color: var(--on-primary); font-size: 15px; font-weight: 700; }
.body { flex: 1; min-width: 0; }
.thinking { margin: 4px 0 0; color: var(--text-3); }

.process { margin-bottom: 14px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface-2); font-size: var(--fs-s); }
.process summary { display: flex; align-items: center; gap: 8px; padding: 9px 12px; list-style: none; cursor: pointer; color: var(--text-2); }
.process summary::-webkit-details-marker { display: none; }
.chevron { margin-left: auto; transition: transform .15s; }
.process[open] .chevron { transform: rotate(180deg); }
.process ol { margin: 0; padding: 2px 12px 12px; list-style: none; border-top: 1px solid var(--border); }
.process li { display: grid; grid-template-columns: 22px 1fr; gap: 8px; padding-top: 10px; }
.step { width: 20px; height: 20px; display: grid; place-items: center; border-radius: 50%; border: 1px solid var(--border-strong); background: var(--surface); font-size: var(--fs-xs); color: var(--text-3); }
.process strong { font-weight: 600; }
.process p { margin: 3px 0 0; line-height: 1.7; color: var(--text-2); overflow-wrap: anywhere; }
.query { display: inline-block; padding: 1px 8px; margin: 2px 4px 2px 0; border: 1px solid var(--border); border-radius: 4px; background: var(--surface); color: var(--text); }
.running { color: var(--text-3); }

.sources { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 6px; padding-top: 12px; border-top: 1px solid var(--border); }
.sources > span { font-size: var(--fs-xs); color: var(--text-3); }
.sources button { display: inline-flex; align-items: center; gap: 6px; min-height: 28px; padding: 3px 10px 3px 4px; border: 1px solid var(--border); border-radius: var(--radius-s); background: var(--surface); font-size: var(--fs-xs); color: var(--text-2); text-align: left; }
.sources button:hover:not(:disabled) { border-color: var(--primary); }
.sources button.on { border-color: var(--primary); box-shadow: 0 0 0 2px var(--primary-line); }
.sources button.gone { color: var(--text-3); text-decoration: line-through; cursor: not-allowed; }
.sources i { flex-shrink: 0; padding: 0 6px; border-radius: 4px; font-style: normal; line-height: 20px; }
.sources i.document { background: var(--doc-soft); color: var(--doc); }
.sources i.experience { background: var(--exp-soft); color: var(--exp); }
.sources .gone i { background: var(--neutral-tag); color: var(--text-3); }

.questions { margin: 0 0 24px 42px; padding: 18px 20px; border: 1px solid var(--primary-line); border-radius: var(--radius-l); background: var(--primary-soft); }
.questions h3 { margin: 0 0 14px; font-size: var(--fs); font-weight: 600; }
.question { margin-bottom: 16px; }
.question label { display: block; margin-bottom: 8px; font-size: var(--fs); }
.options { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }
.options button { padding: 6px 14px; border: 1px solid var(--border-strong); border-radius: var(--radius-s); background: var(--surface); font-size: var(--fs-s); color: var(--text); }
.options button.on { border-color: var(--primary); background: var(--primary); color: var(--on-primary); }

.composer-area { padding: 8px 28px 16px; }
.status { max-width: 780px; margin: 0 auto 8px; display: flex; align-items: center; justify-content: space-between; font-size: var(--fs-s); color: var(--text-3); }
.composer { max-width: 780px; margin: 0 auto; padding: 12px 14px 10px; border: 1px solid var(--border-strong); border-radius: var(--radius-l); background: var(--surface); box-shadow: var(--shadow); }
.composer:focus-within { border-color: var(--primary); }
.composer.disabled { background: var(--surface-2); }
.composer textarea { width: 100%; min-height: 46px; border: 0; outline: 0; resize: none; background: none; font: inherit; line-height: 1.7; color: var(--text); }
.composer textarea::placeholder { color: var(--text-3); }
.composer-foot { display: flex; align-items: center; justify-content: space-between; font-size: var(--fs-xs); color: var(--text-3); }
.composer-foot .el-button { width: 36px; height: 32px; padding: 0; }
.disclaimer { margin: 8px 0 0; text-align: center; font-size: var(--fs-xs); color: var(--text-3); }

@media (max-width: 700px) {
  .thread { padding: 20px 16px 8px; }
  .composer-area { padding: 8px 12px 12px; }
  .questions { margin-left: 0; }
  .composer-foot > span { display: none; }
  .composer-foot { justify-content: flex-end; }
}
</style>
