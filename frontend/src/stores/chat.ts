import { defineStore } from 'pinia'
import { api, type Conversation, type Message } from '../api'

export const useChatStore = defineStore('chat', {
  state: () => ({
    conversations: [] as Conversation[],
    current: null as Conversation | null,
    /** 本页面正在接收一轮流式回答。 */
    sending: false,
    /** 正在退出登录：期间不发起新的一轮，也不提示由退出引起的停止。 */
    signingOut: false,
    draft: '',
    /** 对助手追问的回答，按问题 ID 存放。 */
    answers: {} as Record<string, string>,
  }),
  getters: {
    messages: (state): Message[] => state.current?.transcript || [],
    busy: state => state.sending || state.current?.status === 'running',
    readyAnswers: state => state.current?.pending.every(q => state.answers[q.id]?.trim()),
  },
  actions: {
    async list() {
      this.conversations = await api<Conversation[]>('/conversations')
    },
    async open(id: string) {
      this.current = await api<Conversation>(`/conversations/${id}`)
      this.draft = ''; this.answers = {}
    },
    startNew() {
      this.current = null; this.draft = ''; this.answers = {}
    },
    async stop() {
      if (!this.current) return
      const { id } = this.current
      await api(`/conversations/${id}/stop`, { method: 'POST' })
      // 本页正在接收的流结束后会自行刷新；其余情况（如刷新页面后停止）在这里取回最新状态。
      if (!this.sending && this.current?.id === id) {
        const current = await api<Conversation>(`/conversations/${id}`)
        if (this.current?.id === id) this.current = current
      }
    },
  },
})
