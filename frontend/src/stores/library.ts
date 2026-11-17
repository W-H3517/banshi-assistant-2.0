import { defineStore } from 'pinia'
import { api, type Document } from '../api'

export const useLibraryStore = defineStore('library', {
  state: () => ({
    /** 当前列表：有搜索词时是搜索结果，否则是全部制度。 */
    documents: [] as Document[],
    /** 全部现存制度的 ID，用于判断历史引用是否已失效。 */
    availableIds: [] as string[],
    search: '',
  }),
  actions: {
    async load() {
      const all = await api<Document[]>('/documents')
      const documents = this.search.trim()
        ? await api<Document[]>('/documents?q=' + encodeURIComponent(this.search))
        : all
      this.availableIds = all.map(doc => doc.id)
      this.documents = documents
    },
  },
})
