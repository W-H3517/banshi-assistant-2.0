export type ExperienceStatus = 'draft' | 'pending' | 'verifying' | 'published' | 'rejected' | 'withdrawn'

/** 全员可见的已发布经验：不含提交人、原文和审核记录。 */
export interface PublishedExperience {
  id: string; tags: string[]; content: string; author: string; published_at: string
  conflict: boolean; conflict_note: string
}
/** 提交人看到的自己的记录。 */
export interface OwnExperience {
  id: string; status: ExperienceStatus; tags: string[]; text: string; admin_note: string
  created_at: string; updated_at: string; submitted_at: string | null; published_at: string | null
  content: string | null
}
export interface ExperienceEvent { action: string; note: string; content: string; actor: string; created_at: string }
/** 管理员看到的完整记录。 */
export interface ReviewExperience extends Omit<OwnExperience, 'content'> {
  content: string; author: string; conflict: boolean; conflict_note: string; events: ExperienceEvent[]
}

export const statusText: Record<ExperienceStatus, string> = {
  draft: '草稿', pending: '待审核', verifying: '待核实', published: '已发布', rejected: '不发布', withdrawn: '已下架',
}
export const statusType: Record<ExperienceStatus, 'info' | 'warning' | 'success' | 'danger' | 'primary'> = {
  draft: 'info', pending: 'warning', verifying: 'primary', published: 'success', rejected: 'danger', withdrawn: 'info',
}
export const actionText: Record<string, string> = {
  submit: '提交', verify: '核实记录', save: '调整内容', publish: '发布', reject: '不发布', withdraw: '下架',
}
export function day(value: string | null) {
  return value ? new Date(value).toLocaleString('zh-CN', { dateStyle: 'short', timeStyle: 'short' }) : ''
}
