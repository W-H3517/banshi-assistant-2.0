import { ElMessage } from 'element-plus'
import { isAbortError } from './api'

export function showError(error: unknown, fallback = '操作失败') {
  if (!isAbortError(error)) ElMessage.error(error instanceof Error ? error.message : fallback)
}

/** 执行一个操作，失败时弹出错误提示而不向上抛出。 */
export async function guard(action: () => Promise<unknown>) {
  try { await action() } catch (error) { showError(error) }
}
