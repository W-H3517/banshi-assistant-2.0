import { createRouter, createWebHashHistory } from 'vue-router'
import { useAuthStore } from './stores/auth'

declare module 'vue-router' {
  interface RouteMeta {
    /** 顶栏显示的页面标题和副标题。 */
    title?: [string, string]
    /** 仅管理员可进入。 */
    admin?: boolean
  }
}

// 使用 hash 地址（/#/chat/…）：后端只托管静态文件，刷新任意页面都不需要后端配合。
export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('./views/LoginView.vue') },
    {
      path: '/',
      component: () => import('./layouts/WorkspaceLayout.vue'),
      children: [
        { path: '', redirect: { name: 'chat' } },
        {
          path: 'chat/:id?', name: 'chat', component: () => import('./views/ChatView.vue'),
          meta: { title: ['办事助手', '让办理有据可循'] },
        },
        { path: 'library', redirect: { name: 'library', params: { tab: 'documents' } } },
        {
          path: 'library/:tab(documents|experiences)/:id?', name: 'library',
          component: () => import('./views/LibraryView.vue'),
          meta: { title: ['知识库', '部门共享资料'] },
        },
        {
          path: 'experiences/new', name: 'record', component: () => import('./views/RecordView.vue'),
          meta: { title: ['记录经验', '把办事的经验留给同事'] },
        },
        {
          path: 'experiences/mine', name: 'mine', component: () => import('./views/MineView.vue'),
          meta: { title: ['我的记录', '我提交的经验'] },
        },
        {
          path: 'admin/review/:id?', name: 'review', component: () => import('./views/ReviewView.vue'),
          meta: { title: ['管理工作台', '审核与发布经验'], admin: true },
        },
      ],
    },
    // 用路径而不是路由名重定向：按名字重定向会把 rest 参数带给聊天路由并触发警告。
    { path: '/:rest(.*)*', redirect: '/chat' },
  ],
})

router.beforeEach(async to => {
  const auth = useAuthStore()
  if (!auth.restored) await auth.restore()
  if (to.name === 'login') return auth.user ? { name: 'chat' } : true
  if (!auth.user) return { name: 'login' }
  if (to.meta.admin && !auth.isAdmin) return { name: 'chat' }
  return true
})
