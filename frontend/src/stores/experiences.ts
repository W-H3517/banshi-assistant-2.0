import { defineStore } from 'pinia'
import { api } from '../api'
import type { PublishedExperience } from '../experience'

export const useExperienceStore = defineStore('experiences', {
  state: () => ({
    /** 已发布经验用到的全部标签，供填写标签时选择。 */
    tags: [] as string[],
    /** 全部已发布经验的 ID，用于判断历史引用是否已下架。 */
    availableIds: [] as string[],
  }),
  actions: {
    async refresh() {
      const result = await api<{ tags: string[]; items: PublishedExperience[] }>('/experiences')
      this.tags = result.tags
      this.availableIds = result.items.map(item => item.id)
    },
  },
})
