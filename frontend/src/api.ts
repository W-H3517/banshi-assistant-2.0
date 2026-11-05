export interface User { id: string; username: string; name: string; role: string }
export interface Source { id: string; title: string }
export interface Tool { id: string; name: string; status: string }
export interface Message { role: string; text: string; tools?: Tool[]; sources?: Source[] }
export interface Question { id: string; question: string; options?: string[] | null }
export interface Conversation { id: string; title: string; status: string; updated_at: string; transcript: Message[]; pending: Question[]; error: string }
export interface Document extends Source { filename: string; preview?: string; content?: string; created_at: string }

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch('/api' + path, { ...init, headers: init.body instanceof FormData ? init.headers : { 'Content-Type': 'application/json', ...init.headers } })
  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    if (response.status === 401) window.dispatchEvent(new Event('session-expired'))
    throw new Error(typeof error.detail === 'string' ? error.detail : `请求失败（${response.status}）`)
  }
  return response.json()
}

export async function streamTurn(id: string, body: unknown, receive: (event: any) => void) {
  const response = await fetch(`/api/conversations/${id}/turn`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  if (!response.ok) {
    const data = await response.json()
    if (response.status === 401) window.dispatchEvent(new Event('session-expired'))
    throw new Error(typeof data.detail === 'string' ? data.detail : '发送失败')
  }
  const reader = response.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let terminal = false
  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let end: number
      while ((end = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, end)
        buffer = buffer.slice(end + 2)
        if (frame.startsWith('data: ')) {
          const event = JSON.parse(frame.slice(6))
          terminal ||= event.type === 'done' || event.type === 'error'
          receive(event)
        }
      }
    }
    if (!terminal) throw new Error('连接中断，请重新加载会话查看状态')
  } finally { reader.releaseLock() }
}
