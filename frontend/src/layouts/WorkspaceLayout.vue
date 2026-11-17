<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter, type RouteLocationRaw } from 'vue-router'
import { ChatDotRound, Collection, Plus, SwitchButton, Menu, EditPen, Tickets, Finished } from '@element-plus/icons-vue'
import type { Conversation } from '../api'
import { guard } from '../feedback'
import { clearSession, loadWorkspace } from '../session'
import { useAuthStore } from '../stores/auth'
import { useChatStore } from '../stores/chat'
import { useLibraryStore } from '../stores/library'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const chat = useChatStore()
const library = useLibraryStore()
const mobileOpen = ref(false)
/** 顶栏标题：聊天页显示当前会话标题，其余显示页面名称。 */
const title = computed(() => route.name === 'chat' && chat.current ? chat.current.title : route.meta.title?.[0] || '办事助手')
const subtitle = computed(() => route.name === 'chat' && chat.current ? '' : route.meta.title?.[1] || '')
/** “办事助手”回到当前会话；没有打开的会话时是新对话。 */
const chatLink = computed<RouteLocationRaw>(() => ({ name: 'chat', params: chat.current ? { id: chat.current.id } : {} }))
/** 会话按最后更新时间分成今天和更早两组。 */
const groups = computed(() => {
  const today = new Date().toDateString()
  const recent: Conversation[] = [], earlier: Conversation[] = []
  for (const item of chat.conversations) {
    (new Date(item.updated_at).toDateString() === today ? recent : earlier).push(item)
  }
  return [{ label: '今天', items: recent }, { label: '更早', items: earlier }].filter(group => group.items.length)
})

watch(() => route.fullPath, () => { mobileOpen.value = false })

function newChat() {
  if (chat.sending) return
  chat.startNew()
  void router.push({ name: 'chat' })
}
async function logout() {
  if (chat.signingOut) return
  chat.signingOut = true
  try {
    // 生成中先停止后端生成再退出。本页的流随后照常收尾，退出失败时界面即可继续使用。
    if (chat.busy && chat.current) await chat.stop()
    await auth.logout()
  } finally { chat.signingOut = false }
  clearSession()
  await router.replace({ name: 'login' })
}
onMounted(() => guard(loadWorkspace))
</script>

<template>
  <div v-if="auth.user" class="workspace">
    <div v-if="mobileOpen" class="shade" @click="mobileOpen = false"></div>
    <aside class="sidebar" :class="{ open: mobileOpen }">
      <div class="logo"><div class="logo-mark">办</div><div>办事助手<small>部门工作台</small></div></div>
      <el-button class="new-chat" type="primary" :icon="Plus" :disabled="chat.sending" @click="newChat">新对话</el-button>
      <nav aria-label="主导航">
        <button :class="{ on: route.name === 'chat' }" @click="router.push(chatLink)">
          <el-icon><ChatDotRound /></el-icon>办事助手
        </button>
        <button :class="{ on: route.name === 'library' }" @click="router.push({ name: 'library', params: { tab: 'documents' } }); guard(() => library.load())">
          <el-icon><Collection /></el-icon>知识库<span class="count">{{ library.availableIds.length }}</span>
        </button>
        <button :class="{ on: route.name === 'record' }" @click="router.push({ name: 'record' })">
          <el-icon><EditPen /></el-icon>记录经验
        </button>
        <button :class="{ on: route.name === 'mine' }" @click="router.push({ name: 'mine' })">
          <el-icon><Tickets /></el-icon>我的记录
        </button>
        <button v-if="auth.isAdmin" :class="{ on: route.name === 'review' }" @click="router.push({ name: 'review' })">
          <el-icon><Finished /></el-icon>管理工作台
        </button>
      </nav>
      <div class="history">
        <p v-if="!chat.conversations.length" class="empty">还没有对话，从第一个问题开始</p>
        <template v-for="group in groups" :key="group.label">
          <div class="group-label">{{ group.label }}</div>
          <button
            v-for="c in group.items" :key="c.id" :disabled="chat.sending" :title="c.title"
            :class="{ on: chat.current?.id === c.id }" @click="router.push({ name: 'chat', params: { id: c.id } })"
          >
            <span>{{ c.title }}</span><i v-if="c.status === 'waiting'" title="等待你的回答"></i>
          </button>
        </template>
      </div>
      <div class="user-bar">
        <div class="avatar">{{ auth.user.name.slice(-1) }}</div>
        <div><strong>{{ auth.user.name }}</strong><small>{{ auth.isAdmin ? '管理员' : '普通员工' }}</small></div>
        <el-button text :icon="SwitchButton" aria-label="退出登录" title="退出登录" @click="guard(logout)" />
      </div>
    </aside>

    <main class="main">
      <header class="topbar">
        <button class="menu" aria-label="打开菜单" @click="mobileOpen = true"><el-icon><Menu /></el-icon></button>
        <h1>{{ title }}</h1>
        <span v-if="subtitle" class="subtitle">{{ subtitle }}</span>
        <span class="badge">内部试用</span>
      </header>
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.workspace { display: flex; height: 100dvh; overflow: hidden; color: var(--text); font-size: var(--fs); }
.sidebar { width: 236px; flex-shrink: 0; display: flex; flex-direction: column; padding: 18px 12px 0; background: var(--sidebar); border-right: 1px solid var(--border); }
.logo { display: flex; align-items: center; gap: 10px; padding: 0 8px 18px; font-size: 15px; font-weight: 600; }
.logo small { display: block; font-size: var(--fs-xs); font-weight: 400; color: var(--text-3); }
.logo-mark { width: 34px; height: 34px; display: grid; place-items: center; border-radius: 9px; background: var(--primary); color: var(--on-primary); font-size: 18px; font-weight: 700; }
.new-chat { width: 100%; height: 36px; }

nav { display: grid; gap: 2px; margin: 16px 0 14px; }
nav button { display: flex; align-items: center; gap: 10px; height: 36px; padding: 0 10px; border: 0; border-radius: var(--radius-s); background: none; font-size: var(--fs); color: var(--text-2); text-align: left; }
nav button:hover { background: var(--hover-fill); }
nav button.on { background: var(--surface); color: var(--primary); font-weight: 600; box-shadow: 0 1px 2px rgba(31, 42, 55, .06); }
nav .el-icon { font-size: 18px; }
.count { margin-left: auto; font-size: var(--fs-xs); font-weight: 400; color: var(--text-3); }

.history { flex: 1; min-height: 0; overflow: auto; }
.group-label { padding: 8px 10px 6px; font-size: var(--fs-xs); color: var(--text-3); }
.empty { margin: 0; padding: 12px 10px; font-size: var(--fs-s); color: var(--text-3); }
.history button { width: 100%; display: flex; align-items: center; gap: 8px; height: 34px; padding: 0 10px; border: 0; border-radius: var(--radius-s); background: none; font-size: var(--fs-s); color: var(--text-2); text-align: left; }
.history button span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.history button:hover:not(:disabled) { background: var(--hover-fill); }
.history button.on { background: var(--selected-fill); color: var(--text); }
.history i { flex-shrink: 0; width: 7px; height: 7px; margin-left: auto; border-radius: 50%; background: var(--warn); }

.user-bar { display: flex; align-items: center; gap: 10px; padding: 14px 8px; border-top: 1px solid var(--border); }
.avatar { flex-shrink: 0; width: 32px; height: 32px; display: grid; place-items: center; border-radius: 50%; background: var(--primary-soft); color: var(--primary); font-size: var(--fs-s); font-weight: 600; }
.user-bar strong { display: block; font-size: var(--fs-s); font-weight: 600; }
.user-bar small { font-size: var(--fs-xs); color: var(--text-3); }
.user-bar .el-button { margin-left: auto; padding: 0 8px; font-size: 16px; }

.main { flex: 1; min-width: 0; display: flex; flex-direction: column; background: var(--surface); }
.topbar { flex-shrink: 0; height: 56px; display: flex; align-items: center; gap: 12px; padding: 0 24px; border-bottom: 1px solid var(--border); }
.topbar h1 { margin: 0; font-size: var(--fs-l); font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.subtitle { flex-shrink: 0; font-size: var(--fs-s); color: var(--text-3); }
.badge { flex-shrink: 0; margin-left: auto; height: 22px; padding: 0 8px; border-radius: 4px; background: var(--neutral-tag); font-size: var(--fs-xs); line-height: 22px; color: var(--text-2); }
.menu { display: none; padding: 0; border: 0; background: none; font-size: 20px; color: var(--text-2); }
.shade { display: none; }

@media (max-width: 700px) {
  .sidebar { position: fixed; top: 0; bottom: 0; left: -250px; z-index: 30; transition: left .2s; }
  .sidebar.open { left: 0; }
  .shade { display: block; position: fixed; inset: 0; z-index: 25; background: rgba(17, 24, 39, .35); }
  .menu { display: flex; }
  .topbar { padding: 0 16px; }
  .subtitle { display: none; }
}
</style>
