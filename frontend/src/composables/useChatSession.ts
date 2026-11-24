import { nextTick, onBeforeUnmount, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, sessionSignal, streamTurn, type Conversation, type Message, type Source } from '../api'
import { showError } from '../feedback'
import { useAuthStore } from '../stores/auth'
import { useChatStore } from '../stores/chat'
import { useExperienceStore } from '../stores/experiences'

/** 来源的标识：制度按文件和页，经验按经验 ID。 */
export const sourceKey = (source: Source) =>
  source.kind === 'document' ? `document:${source.id}#${source.page}` : `experience:${source.id}`

/** 离底部多近算“在底部”，用户滚到这里恢复跟随。 */
const FOLLOW_NEAR = 48
/** 跟随时两次滚动的最短间隔（毫秒）。 */
const FOLLOW_INTERVAL = 400

// 滚动状态放在模块级：同一时间只挂载一个聊天页，生成中离开再返回时，
// 仍在接收的那一轮通过这里滚动新挂载的聊天页，而不是已卸载的旧页。
const scrollArea = ref<HTMLElement | null>(null)
/** 是否跟随最新内容：用户向上翻看时停止，回到底部附近或点“回到最新”时恢复。 */
const following = ref(true)
let lastTop = 0
let timer: ReturnType<typeof setTimeout> | undefined

const gap = (area: HTMLElement) => area.scrollHeight - area.scrollTop - area.clientHeight

/** 滚动区的 scroll 事件：按用户的滚动方向和位置决定是否继续跟随。 */
function onScroll() {
  const area = scrollArea.value!
  // 任何向上的移动都算上翻（含逐像素的细小滚动）；内容变短时浏览器把位置夹到底部，
  // 位置虽变小但仍贴着底部，不算上翻。
  if (area.scrollTop < lastTop && gap(area) > 4) following.value = false
  else if (gap(area) < FOLLOW_NEAR) following.value = true
  lastTop = area.scrollTop
}

function cancelFollow() {
  clearTimeout(timer); timer = undefined
}

/** 立即到底部并恢复跟随：发送消息、打开或切换会话时用。 */
async function scrollDown() {
  following.value = true
  cancelFollow()
  await nextTick()
  // 打开会话或接收回答期间可能已离开聊天页，此时没有滚动区。
  const area = scrollArea.value
  if (!area) return
  area.scrollTo({ top: area.scrollHeight })
  lastTop = area.scrollTop
}

/** “回到最新”：平滑滚到底部并恢复跟随。 */
function jumpToLatest() {
  following.value = true
  const area = scrollArea.value!
  area.scrollTo({ top: area.scrollHeight, behavior: 'smooth' })
}

/** 新内容到达：跟随时按固定间隔平滑滚一次，不随每个字滚动。 */
function follow() {
  // 离开聊天页后流仍会继续推送，此时不安排滚动；返回后由新页的 scrollDown 接上。
  if (!following.value || timer || !scrollArea.value) return
  timer = setTimeout(() => {
    timer = undefined
    const area = scrollArea.value!
    if (following.value) area.scrollTo({ top: area.scrollHeight, behavior: 'smooth' })
  }, FOLLOW_INTERVAL)
}

/** 聊天页的发送流程：新建会话、接收流式回答、回答追问，以及结束后与后端对齐状态。 */
export function useChatSession() {
  const router = useRouter()
  const auth = useAuthStore()
  const chat = useChatStore()
  const experiences = useExperienceStore()

  // 卸载时滚动区引用由 Vue 置空，这里取消已安排的滚动。
  onBeforeUnmount(cancelFollow)

  async function send(text?: string) {
    if (chat.busy || chat.signingOut || !auth.modelReady) return
    const prompt = text || chat.draft.trim()
    const answering = Boolean(chat.current?.pending.length)
    if (answering ? !chat.readyAnswers : !prompt) return
    const signal = sessionSignal()
    chat.sending = true
    const previousDraft = chat.draft
    let id = ''
    let failed = false
    try {
      if (!chat.current) {
        chat.current = await api<Conversation>('/conversations', { method: 'POST' })
        // 地址换成新会话；current 已是这个会话，聊天页不会重新加载它。
        await router.replace({ name: 'chat', params: { id: chat.current.id } })
        // 新建和跳转期间可能已退出（store 已清空）或开始退出，都不能再发起这一轮。
        signal.throwIfAborted()
        if (chat.signingOut) return
      }
      const convo = chat.current
      id = convo.id
      const answers = answering ? { ...chat.answers } : {}
      const userText = answering ? convo.pending.map(q => `${q.question}：${answers[q.id]}`).join('\n') : prompt
      convo.transcript.push({ role: 'user', text: userText })
      convo.transcript.push({ role: 'assistant', text: '', tools: [], sources: [] })
      const message = convo.transcript[convo.transcript.length - 1] as Required<Message>
      convo.status = 'running'; convo.error = ''; chat.draft = ''
      await scrollDown()
      await streamTurn(id, { prompt: answering ? '' : prompt, answers }, event => {
        if (event.type === 'delta') message.text += event.text
        // 同一工具、同一来源会多次推送（状态变化、段落累加），按标识替换。
        if (event.type === 'tool') {
          const { type: _, ...tool } = event
          const index = message.tools.findIndex(t => t.id === tool.id)
          if (index !== -1) message.tools[index] = tool
          else {
            // 带说明的新步骤：已流式显示的正文就是这段说明，后端已把它移进过程。
            if (tool.note) message.text = ''
            message.tools.push(tool)
          }
        }
        if (event.type === 'source') {
          const { type: _, ...source } = event
          const index = message.sources.findIndex(s => sourceKey(s) === sourceKey(source))
          if (index === -1) message.sources.push(source)
          else message.sources[index] = source
        }
        if (event.type === 'error') {
          failed = true
          if (!chat.signingOut) ElMessage.warning(event.message)
        }
        if (chat.current?.id === id) follow()
      })
    } catch (error) { failed = true; showError(error) }
    finally {
      // 会话已结束时 store 已清空，本轮的草稿和状态不能再写回，否则会进入下一个账号的界面。
      if (!signal.aborted) {
        chat.sending = false
        if (failed && !answering) chat.draft = previousDraft || prompt
        if (!failed) chat.answers = {}
        try {
          // 只在仍停留在本会话时用后端状态替换。
          if (id && chat.current?.id === id) {
            const current = await api<Conversation>(`/conversations/${id}`)
            if (chat.current?.id === id) chat.current = current
          }
          await Promise.all([chat.list(), experiences.refresh()])
          follow()
        } catch (error) { showError(error) }
      }
    }
  }

  return { scrollArea, following, onScroll, scrollDown, jumpToLatest, send }
}
