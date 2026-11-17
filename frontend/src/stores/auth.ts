import { defineStore } from 'pinia'
import { api, type User } from '../api'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    user: null as User | null,
    /** 是否已向后端确认过登录状态；路由守卫据此只确认一次。 */
    restored: false,
    modelReady: true,
  }),
  getters: {
    isAdmin: state => state.user?.role === 'admin',
  },
  actions: {
    async restore() {
      try { this.user = await api<User>('/me') } catch { /* 未登录时进入登录页。 */ }
      finally { this.restored = true }
    },
    async login(username: string, password: string) {
      this.user = await api<User>('/login', { method: 'POST', body: JSON.stringify({ username, password }) })
    },
    async logout() {
      await api('/logout', { method: 'POST' })
    },
    async checkModel() {
      this.modelReady = (await api<{ model_configured: boolean }>('/health')).model_configured
    },
  },
})
