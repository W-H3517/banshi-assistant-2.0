<script setup lang="ts">
import { isAbortError } from '../api'
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ChatDotRound, Opportunity, Reading } from '@element-plus/icons-vue'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()
const username = ref('admin')
const password = ref('')
const signingIn = ref(false)
const loginError = ref('')
const features = [
  { icon: ChatDotRound, title: '办事问答', text: '说清要办什么，助手查制度、问细节，给出步骤与材料' },
  { icon: Reading, title: '回答可核对', text: '每条依据都能点开原文，定位并高亮到具体段落' },
  { icon: Opportunity, title: '同事经验', text: '办事中摸索出的做法，审核后全部门共享' },
]

async function login() {
  if (signingIn.value) return
  signingIn.value = true; loginError.value = ''
  try {
    await auth.login(username.value, password.value)
    await router.replace({ name: 'chat' })
  } catch (error) { if (!isAbortError(error)) loginError.value = (error as Error).message }
  finally { signingIn.value = false }
}
</script>

<template>
  <main class="login">
    <aside class="side">
      <div class="logo"><div class="logo-mark">办</div>办事助手</div>
      <div class="hero">
        <h1>制度有出处，经验能共享，<br>问题有人答。</h1>
        <p>部门制度、同事经验与智能问答，集中在一个工作台。</p>
        <ul class="features">
          <li v-for="feature in features" :key="feature.title">
            <span class="feature-icon"><el-icon><component :is="feature.icon" /></el-icon></span>
            <div><strong>{{ feature.title }}</strong><span>{{ feature.text }}</span></div>
          </li>
        </ul>
      </div>
      <small>内部试用版 · 回答仅供参考，以现行制度原文为准</small>
    </aside>
    <section class="form-area">
      <form class="card" @submit.prevent="login">
        <h2>登录</h2>
        <p class="lead">使用部门分配的账号进入工作台</p>
        <label for="username">账号</label>
        <el-input id="username" v-model="username" size="large" autocomplete="username" placeholder="输入账号" />
        <label for="password">密码</label>
        <el-input id="password" v-model="password" size="large" type="password" show-password autocomplete="current-password" placeholder="输入密码" />
        <p v-if="loginError" class="error" role="alert">{{ loginError }}</p>
        <el-button class="submit" type="primary" size="large" native-type="submit" :loading="signingIn">登录</el-button>
        <div class="tip">本地测试账号：admin / alice / bob<br>初始密码见项目 README</div>
      </form>
    </section>
  </main>
</template>

<style scoped>
.login {
  min-height: 100dvh;
  display: grid;
  grid-template-columns: minmax(420px, 46%) 1fr;
  color: var(--text);
  font-size: var(--fs);
}
.side {
  position: relative;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  padding: 48px 56px;
  color: var(--brand-text);
  background: linear-gradient(160deg, var(--brand-dark) 0%, var(--brand-mid) 55%, var(--primary) 100%);
}
.side::after {
  content: "";
  position: absolute;
  right: -120px;
  bottom: -160px;
  width: 460px;
  height: 460px;
  border-radius: 50%;
  border: 1px solid rgba(255, 255, 255, .12);
  box-shadow: 0 0 0 70px rgba(255, 255, 255, .03), 0 0 0 140px rgba(255, 255, 255, .025);
}
.logo {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: var(--fs-l);
  font-weight: 600;
}
.logo-mark {
  width: 34px;
  height: 34px;
  display: grid;
  place-items: center;
  border-radius: 9px;
  background: rgba(255, 255, 255, .16);
  color: var(--on-primary);
  font-size: 18px;
  font-weight: 700;
}
.hero {
  position: relative;
  z-index: 1;
  margin: auto 0;
  padding: 40px 0;
}
.hero h1 {
  margin: 0;
  font-size: clamp(26px, 2.6vw, 34px);
  font-weight: 600;
  line-height: 1.45;
  letter-spacing: 1px;
  color: var(--on-primary);
}
.hero > p {
  margin: 14px 0 0;
  font-size: var(--fs-l);
  line-height: 1.8;
  color: var(--brand-muted);
}
.features {
  display: grid;
  gap: 14px;
  max-width: 440px;
  margin: 40px 0 0;
  padding: 0;
  list-style: none;
}
.features li {
  display: flex;
  gap: 14px;
  padding: 14px 16px;
  border-radius: var(--radius);
  border: 1px solid rgba(255, 255, 255, .1);
  background: rgba(255, 255, 255, .07);
}
.feature-icon {
  flex-shrink: 0;
  width: 36px;
  height: 36px;
  display: grid;
  place-items: center;
  border-radius: var(--radius);
  background: rgba(255, 255, 255, .12);
  color: var(--on-primary);
  font-size: 18px;
}
.features strong {
  display: block;
  margin-bottom: 3px;
  font-weight: 600;
  color: var(--on-primary);
}
.features li span:not(.feature-icon) {
  font-size: var(--fs-s);
  line-height: 1.6;
  color: var(--brand-muted);
}
.side > small {
  position: relative;
  z-index: 1;
  font-size: var(--fs-xs);
  color: var(--brand-caption);
}
.form-area {
  display: grid;
  place-items: center;
  padding: 40px;
  background: var(--surface);
}
.card {
  width: min(360px, 100%);
}
.card h2 {
  margin: 0;
  font-size: var(--fs-2xl);
  font-weight: 600;
}
.lead {
  margin: 8px 0 32px;
  color: var(--text-3);
}
.card label {
  display: block;
  margin: 20px 0 8px;
  font-size: var(--fs-s);
  color: var(--text-2);
}
.error {
  margin: 12px 0 0;
  font-size: var(--fs-s);
  color: var(--danger);
}
.submit {
  width: 100%;
  margin-top: 28px;
}
.tip {
  margin-top: 24px;
  padding: 12px 14px;
  border-radius: var(--radius);
  border: 1px dashed var(--border-strong);
  background: var(--surface-2);
  font-size: var(--fs-xs);
  line-height: 1.8;
  color: var(--text-3);
}

/* 窄屏只保证能登录：收起功能介绍，品牌区缩成顶部一条。 */
@media (max-width: 800px) {
  .login { display: block; }
  .side { padding: 24px; }
  .hero { padding: 20px 0 0; }
  .hero > p, .features, .side > small { display: none; }
  .form-area { padding: 32px 24px 48px; }
}
</style>
