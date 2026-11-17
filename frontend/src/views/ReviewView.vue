<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, sessionSignal } from '../api'
import TagInput from '../components/TagInput.vue'
import { guard, showError } from '../feedback'
import { useExperienceStore } from '../stores/experiences'
import { actionText, day, statusText, statusType, type ExperienceStatus, type ReviewExperience } from '../experience'

type Filter = ExperienceStatus | 'conflict'
const route = useRoute()
const router = useRouter()
const experiences = useExperienceStore()
const items = ref<ReviewExperience[]>([])
const loading = ref(true)
const working = ref<string | null>(null)
const form = reactive({ content: '', tags: [] as string[], conflict: false, conflict_note: '', admin_note: '', note: '' })
const filters: { key: Filter; label: string }[] = [
  { key: 'pending', label: '待审核' }, { key: 'verifying', label: '待核实' }, { key: 'published', label: '已发布' },
  { key: 'conflict', label: '与制度冲突' }, { key: 'rejected', label: '不发布' }, { key: 'withdrawn', label: '已下架' },
]
// “与制度冲突”只列已发布的，供定期复核。
const inFilter = (item: ReviewExperience, key: Filter) => key === 'conflict' ? item.conflict && item.status === 'published' : item.status === key
const requested = computed(() => items.value.find(item => item.id === route.params.id))
const filter = computed<Filter>(() => {
  const key = filters.find(option => option.key === route.query.filter)?.key
  if (key && (!requested.value || inFilter(requested.value, key))) return key
  return requested.value?.status ?? key ?? 'pending'
})
const current = computed(() => requested.value ?? null)
const shown = computed(() => items.value.filter(item => inFilter(item, filter.value)))
const count = (key: Filter) => items.value.filter(item => inFilter(item, key)).length
const changed = computed(() => current.value !== null && form.content.trim() !== current.value.content)

const snapshot = () => JSON.stringify([form.content, form.tags, form.conflict, form.conflict_note, form.admin_note, form.note])
let saved = ''
watch([current, filter], ([item]) => {
  if (!item) return
  Object.assign(form, { content: item.content, tags: [...item.tags], conflict: item.conflict, conflict_note: item.conflict_note, admin_note: item.admin_note, note: '' })
  saved = snapshot()
}, { immediate: true })

function location(key: Filter, id?: string) {
  return { name: 'review', params: { id: id ?? '' }, query: { filter: key } }
}
function selectFilter(key: Filter) {
  if (key !== filter.value) {
    const id = current.value && inFilter(current.value, key) ? current.value.id : items.value.find(item => inFilter(item, key))?.id
    void router.push(location(key, id))
  }
}
function syncLocation() {
  const target = location(filter.value, current.value?.id ?? shown.value[0]?.id)
  if (router.resolve(target).fullPath !== route.fullPath) return router.replace(target)
}
onBeforeRouteUpdate(async (to, from) => {
  if (to.params.id === from.params.id && to.query.filter === from.query.filter) return true
  if (working.value) return false
  if (!current.value || saved === snapshot()) return true
  try {
    await ElMessageBox.confirm('有未保存的修改，确定放弃并切换吗？', '未保存的修改', {
      confirmButtonText: '放弃修改', cancelButtonText: '取消', type: 'warning',
    })
    return true
  } catch { return false }
})

async function load() {
  loading.value = true
  try { items.value = await api<ReviewExperience[]>('/admin/experiences') }
  catch (error) { showError(error, '加载失败') }
  finally { loading.value = false }
}
async function act(action: 'verify' | 'publish' | 'reject' | 'withdraw' | 'save') {
  if (!current.value) return
  if (action === 'verify' && !form.note.trim()) { ElMessage.warning('请先在“处理记录”里写下要核实的事项或核实结果'); return }
  if (action === 'reject' || action === 'withdraw') {
    const signal = sessionSignal()
    try { await ElMessageBox.confirm(action === 'reject' ? '确定不发布这条经验吗？提交人会在“我的记录”里看到状态和你的留言。' : '下架后其他同事将看不到这条经验，可以之后重新发布。', actionText[action], { confirmButtonText: actionText[action], cancelButtonText: '取消', type: 'warning' }) }
    catch { return }
    // 确认框不随登录过期关闭；会话已结束时，浏览器里的登录可能已换成别的账号，不能再发审核操作。
    if (signal.aborted) return
  }
  const id = current.value.id
  const key = filter.value
  const index = shown.value.findIndex(item => item.id === id)
  working.value = action
  try {
    await api(`/admin/experiences/${id}/review`, { method: 'POST', body: JSON.stringify({ action, ...form }) })
    ElMessage.success({ verify: '已记录，状态为待核实', publish: '已发布，全部门可见', reject: '已标记为不发布', withdraw: '已下架', save: '已保存调整' }[action])
    saved = snapshot()
    void guard(() => experiences.refresh())
    await load()
    const remaining = items.value.filter(item => inFilter(item, key))
    const next = remaining.find(item => item.id === id) ?? remaining[Math.min(index, remaining.length - 1)]
    working.value = null
    await router.replace(location(key, next?.id))
  } catch (error) { showError(error, '操作失败') }
  finally { working.value = null }
}
onMounted(() => guard(async () => {
  await load()
  await syncLocation()
}))
watch(() => route.fullPath, () => { if (route.name === 'review' && !loading.value) void syncLocation() })
</script>

<template>
  <section class="review-page">
    <p class="intro">审核同事提交的经验：核对是否正确，可直接调整内容后发布。</p>
    <div class="split" v-loading="loading">
      <aside class="list-pane">
        <div class="list-head filters" role="group" aria-label="按状态筛选">
          <button v-for="option in filters" :key="option.key" :disabled="working !== null" :aria-pressed="filter === option.key" @click="selectFilter(option.key)">
            {{ option.label }} <span>{{ count(option.key) }}</span>
          </button>
        </div>
        <div class="list">
          <button v-for="item in shown" :key="item.id" class="list-item review-item" :class="{ on: item.id === current?.id }"
            :aria-pressed="item.id === current?.id" :disabled="working !== null" @click="router.push(location(filter, item.id))">
            <span class="item-labels">
              <el-tag :type="statusType[item.status]" effect="light">{{ statusText[item.status] }}</el-tag>
              <span v-for="tag in item.tags" :key="tag" class="kind-tag experience">{{ tag }}</span>
              <span v-if="item.conflict" class="kind-tag warn">与制度冲突</span>
            </span>
            <span class="item-meta">{{ item.author }} · {{ day(item.submitted_at) }}</span>
            <span class="item-text">{{ item.content }}</span>
          </button>
          <el-empty v-if="!loading && !shown.length" :image-size="72" description="这里暂时没有内容" />
        </div>
      </aside>

      <section class="pane">
        <template v-if="current">
          <header class="pane-head review-meta">
            <el-tag :type="statusType[current.status]" effect="light">{{ statusText[current.status] }}</el-tag>
            <span>提交人：{{ current.author }}（发布后显示为“某同事”）</span>
            <time>提交于 {{ day(current.submitted_at) }}</time>
          </header>
          <div class="review-body" :key="current.id">
            <label>提交人原文 <small>始终保留，不会被修改</small></label>
            <p class="review-original">{{ current.text }}</p>
            <label for="review-content">发布内容 <small>可直接调整；提交人不会收到改动通知</small><em v-if="changed">已调整</em></label>
            <el-input id="review-content" v-model="form.content" type="textarea" :autosize="{ minRows: 5, maxRows: 14 }" maxlength="5000" show-word-limit />
            <label for="review-tags">标签</label>
            <TagInput id="review-tags" v-model="form.tags" :options="experiences.tags" />
            <el-checkbox v-model="form.conflict" class="conflict-check">这条经验与制度的规定不同（发布后会向所有人标明，并列入“与制度冲突”供复核）</el-checkbox>
            <el-input v-if="form.conflict" v-model="form.conflict_note" maxlength="1000" placeholder="说明与哪份制度、哪一条不同，例如：差旅费管理办法第十二条要求政府采购渠道" />
            <label for="review-admin-note">给提交人的留言 <small>提交人在“我的记录”里可见</small></label>
            <el-input id="review-admin-note" v-model="form.admin_note" maxlength="1000" placeholder="可留空" />
            <label for="review-note">处理记录 <small>仅管理员可见，例如线下核实的情况</small></label>
            <el-input id="review-note" v-model="form.note" maxlength="1000" placeholder="本次操作的说明；记为待核实时必填" />
            <div v-if="current.events.length" class="review-events"><strong>操作记录</strong><ol><li v-for="(event, index) in current.events" :key="index"><time>{{ day(event.created_at) }}</time><span>{{ event.actor }} · {{ actionText[event.action] || event.action }}</span><p v-if="event.note">{{ event.note }}</p></li></ol></div>
          </div>
          <footer class="review-actions">
            <el-button :loading="working === 'save'" :disabled="working !== null" @click="act('save')">仅保存调整</el-button>
            <template v-if="current.status === 'pending' || current.status === 'verifying'">
              <el-button :loading="working === 'verify'" :disabled="working !== null" @click="act('verify')">{{ current.status === 'pending' ? '需要核实' : '追加核实记录' }}</el-button>
              <el-button type="danger" plain :loading="working === 'reject'" :disabled="working !== null" @click="act('reject')">不发布</el-button>
            </template>
            <el-button v-if="current.status === 'published'" type="danger" plain :loading="working === 'withdraw'" :disabled="working !== null" @click="act('withdraw')">下架</el-button>
            <el-button v-if="current.status !== 'published' && current.status !== 'rejected'" type="primary" :loading="working === 'publish'" :disabled="working !== null || !form.content.trim() || !form.tags.length" @click="act('publish')">{{ current.status === 'withdrawn' ? '重新发布' : '发布' }}</el-button>
          </footer>
        </template>
        <div v-else-if="!loading" class="pane-empty"><el-empty :image-size="88" description="这里暂时没有内容" /></div>
      </section>
    </div>
  </section>
</template>

<style scoped>
.review-page { flex: 1; min-height: 0; display: flex; flex-direction: column; background: var(--bg); }
.intro { margin: 0; padding: 12px 16px; background: var(--surface); border-bottom: 1px solid var(--border); color: var(--text-2); font-size: var(--fs); line-height: 1.6; }
.filters { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; }
.filters button { display: flex; align-items: center; justify-content: space-between; gap: 6px; padding: 6px 8px; border: 1px solid var(--border); border-radius: var(--radius-s); background: var(--surface); color: var(--text-2); font-size: var(--fs-s); }
.filters button[aria-pressed="true"] { color: var(--primary); border-color: var(--primary-line); background: var(--primary-soft); }
.filters span { font-size: var(--fs-xs); }
.item-labels, .review-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; overflow-wrap: anywhere; }
.item-labels .kind-tag { max-width: 100%; }
.review-item .item-meta { display: block; margin: 6px 0 4px; }
.review-item .item-text { overflow-wrap: anywhere; }
.review-meta { font-size: var(--fs-s); color: var(--text-2); gap: 10px; }
.review-meta time { color: var(--text-2); font-size: var(--fs-xs); }
.review-body { flex: 1; min-height: 0; overflow: auto; display: grid; align-content: start; gap: 9px; padding: 16px clamp(16px, 3vw, 36px) 24px; font-size: var(--fs); }
.review-body label { margin-top: 6px; font-weight: 600; }
.review-body small, .review-body em { margin-inline-start: 6px; font-size: var(--fs-xs); color: var(--text-2); font-weight: 400; }
.review-body em { color: var(--exp); font-style: normal; }
.review-original { margin: 0; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.7; }
.conflict-check { white-space: normal; height: auto; margin: 6px 0; }
.conflict-check :deep(.el-checkbox__label) { line-height: 1.5; }
.review-events { margin-top: 8px; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); overflow-wrap: anywhere; }
.review-events ol { margin: 8px 0 0; padding-inline-start: 22px; }
.review-events li + li { margin-top: 8px; }
.review-events time { margin-inline-end: 8px; font-size: var(--fs-xs); color: var(--text-2); }
.review-events p { margin: 4px 0 0; white-space: pre-wrap; }
.review-actions { flex-shrink: 0; display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; padding: 10px 16px; border-top: 1px solid var(--border); background: var(--surface); }
.review-actions .el-button { margin: 0; }
@media (max-width: 900px) {
  .filters { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .review-page .list-pane { height: clamp(180px, 30%, 260px); }
}
</style>
