import { nextTick, ref } from 'vue'
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

/** 聊天页的发送流程：新建会话、接收流式回答、回答追问，以及结束后与后端对齐状态。 */
export function useChatSession() {
  const router = useRouter()
  const auth = useAuthStore()
  const chat = useChatStore()
  const experiences = useExperienceStore()
  const scrollArea = ref<HTMLElement>()

  async function scrollDown() {
    await nextTick()
    scrollArea.value?.scrollTo({ top: scrollArea.value.scrollHeight })
  }

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
          if (index === -1) message.tools.push(tool)
          else message.tools[index] = tool
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
        void scrollDown()
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
          await scrollDown()
        } catch (error) { showError(error) }
      }
    }
  }

  return { scrollArea, scrollDown, send }
}
