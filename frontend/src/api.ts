export interface User { id: string; username: string; name: string; role: string }
/** 制度来源：同一文件同一页一条；nodes 是本轮回答在该页检索或读取过的段落（阅读单元节点 ID）。 */
export interface DocumentSource { kind: 'document'; id: string; title: string; page: number; nodes: string[] }
export interface ExperienceSource { kind: 'experience'; id: string; title: string }
export type Source = DocumentSource | ExperienceSource
/** 一次工具调用；input 是查了什么，result 是结果摘要，两者只用于展示检索过程。 */
export interface Tool {
  id: string; name: string; status: string
  input: { queries?: string[]; query?: string; scope?: string; page?: number; question?: string }
  result?: {
    items?: number; documents?: number; experiences?: number; total?: number
    title?: string; path?: string[]; pages?: number[]; experience?: string; answer?: string
  }
}
export interface Message { role: string; text: string; tools?: Tool[]; sources?: Source[] }
export interface Question { id: string; question: string; options?: string[] | null }
export interface Conversation { id: string; title: string; status: string; updated_at: string; transcript: Message[]; pending: Question[]; error: string }
export interface Document { id: string; title: string; filename: string; preview: string; created_at: string; page_count: number; chunk_count: number; match_page?: number }

/** 后端的错误说明：普通错误是一句话，字段校验错误是列表，取第一条的提示。 */
function detailText(detail: unknown) {
  if (typeof detail === 'string') return detail
  const first = Array.isArray(detail) ? detail[0]?.msg : ''
  return typeof first === 'string' ? first.replace(/^Value error, /, '') : ''
}

let session = new AbortController()
export const sessionSignal = () => session.signal
export function cancelSessionRequests() {
  const previous = session
  session = new AbortController()
  previous.abort()
}

export function isAbortError(error: unknown) {
  return error instanceof Error && error.name === 'AbortError'
}

async function readResponse<T>(path: string, response: Response, signal: AbortSignal): Promise<T> {
  // 非 2xx 的响应体可能不是 JSON，此时按状态码报错。
  const data = await response.json().catch(error => {
    if (response.ok) throw error
    return null
  })
  // 已完整收到的响应体不受中止影响，所以读完后再核对一次：会话结束后的结果一律按中止处理。
  signal.throwIfAborted()
  // 只有需要有效登录的请求才会触发全局过期。登录与启动时的身份探测自行处理 401。
  if (response.status === 401 && path !== '/login' && path !== '/me') {
    window.dispatchEvent(new Event('session-expired'))
    // 过期处理已清空会话、中止本信号；按中止结束，调用方不再弹错误提示。
    signal.throwIfAborted()
  }
  if (!response.ok) throw new Error(detailText(data?.detail) || `请求失败（${response.status}）`)
  return data
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const signal = init.signal ? AbortSignal.any([session.signal, init.signal]) : session.signal
  const response = await fetch('/api' + path, {
    ...init, signal,
    headers: init.body instanceof FormData ? init.headers : { 'Content-Type': 'application/json', ...init.headers },
  })
  return readResponse<T>(path, response, signal)
}

/** 聊天 SSE 推送的事件；done 或 error 表示本轮结束。 */
export type StreamEvent =
  | { type: 'delta'; text: string }
  | ({ type: 'tool' } & Tool)
  | ({ type: 'source' } & Source)
  | { type: 'error'; message: string }
  | { type: 'done' }

export async function streamTurn(id: string, body: unknown, receive: (event: StreamEvent) => void) {
  const path = `/conversations/${id}/turn`
  const signal = session.signal
  const response = await fetch('/api' + path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal,
  })
  if (!response.ok) { await readResponse(path, response, signal); return }
  const reader = response.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let terminal = false
  try {
    while (true) {
      const { value, done } = await reader.read()
      // 流已全部收到时中止不会让读取失败，缓冲里的旧事件仍会读出，需在此丢弃。
      signal.throwIfAborted()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let end: number
      while ((end = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, end)
        buffer = buffer.slice(end + 2)
        if (frame.startsWith('data: ')) {
          const event = JSON.parse(frame.slice(6)) as StreamEvent
          terminal ||= event.type === 'done' || event.type === 'error'
          receive(event)
        }
      }
    }
    if (!terminal) throw new Error('连接中断，请重新加载会话查看状态')
  } finally { reader.releaseLock() }
}
