import { cancelSessionRequests } from './api'
import { useAuthStore } from './stores/auth'
import { useChatStore } from './stores/chat'
import { useExperienceStore } from './stores/experiences'
import { useLibraryStore } from './stores/library'

/** 登录后加载工作区的公共数据：会话列表、制度、已发布经验和模型状态。 */
export async function loadWorkspace() {
  await Promise.all([useChatStore().list(), useLibraryStore().load(), useExperienceStore().refresh()])
  await useAuthStore().checkModel()
}

/** 退出登录或登录过期时清空本地的用户数据。 */
export function clearSession() {
  cancelSessionRequests()
  useAuthStore().user = null
  useChatStore().$reset()
  useLibraryStore().$reset()
  useExperienceStore().$reset()
}
