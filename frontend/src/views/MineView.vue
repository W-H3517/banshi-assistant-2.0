<script setup lang="ts">
import { showError } from '../feedback'
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, sessionSignal } from '../api'
import { useRouter } from 'vue-router'
import ExperienceForm from '../components/ExperienceForm.vue'
import { useExperienceStore } from '../stores/experiences'
import { day, statusText, statusType, type ExperienceStatus, type OwnExperience } from '../experience'

const router = useRouter()
const experiences = useExperienceStore()
const items = ref<OwnExperience[]>([])
const loading = ref(true)
const editing = ref<OwnExperience | null>(null)

const filter = ref<ExperienceStatus | 'all'>('all')
const filters = computed(() => [
  { key: 'all' as const, label: '全部', count: items.value.length },
  ...(Object.keys(statusText) as ExperienceStatus[]).map(key => ({
    key, label: statusText[key], count: items.value.filter(item => item.status === key).length,
  })).filter(item => item.count > 0),
])
const shown = computed(() => filter.value === 'all' ? items.value : items.value.filter(item => item.status === filter.value))
watch(filters, options => { if (!options.some(option => option.key === filter.value)) filter.value = 'all' })

async function load() {
  loading.value = true
  try { items.value = await api<OwnExperience[]>('/experiences/mine') }
  catch (error) { showError(error, '加载失败') }
  finally { loading.value = false }
}
async function submit(item: OwnExperience) {
  try {
    await api(`/experiences/${item.id}`, { method: 'PUT', body: JSON.stringify({ text: item.text, tags: item.tags, submit: true }) })
    ElMessage.success('已提交，等待管理员审核'); await load()
  } catch (error) { showError(error, '提交失败') }
}
async function remove(item: OwnExperience) {
  const signal = sessionSignal()
  try { await ElMessageBox.confirm('确定删除这条草稿吗？删除后无法恢复。', '删除草稿', { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' }) }
  catch { return }
  // 确认框不随登录过期关闭；会话已结束时，浏览器里的登录可能已换成别的账号，不能再发删除。
  if (signal.aborted) return
  try { await api(`/experiences/${item.id}`, { method: 'DELETE' }); await load() }
  catch (error) { showError(error, '删除失败') }
}
async function saved() { editing.value = null; await load() }
onMounted(load)
</script>

<template>
  <section class="mine-page">
    <div class="mine-content">
      <div class="mine-intro">
        <p>草稿仅自己可见；提交后由管理员审核，发布后全部门可见。</p>
        <el-button type="primary" @click="router.push({ name: 'record' })">记录新经验</el-button>
      </div>
      <div class="status-filters" role="group" aria-label="按状态筛选">
        <button v-for="option in filters" :key="option.key" :aria-pressed="filter === option.key" @click="filter = option.key">
          {{ option.label }} <span>{{ option.count }}</span>
        </button>
      </div>
      <div v-loading="loading" class="record-list">
        <article v-for="item in shown" :key="item.id" class="record-item">
          <header><el-tag :type="statusType[item.status]" effect="light">{{ statusText[item.status] }}</el-tag><span v-for="tag in item.tags" :key="tag" class="record-tag">{{ tag }}</span><time>{{ item.status === 'draft' ? '保存于 ' + day(item.updated_at) : '提交于 ' + day(item.submitted_at) }}</time></header>
          <p class="record-text">{{ item.text }}</p>
          <p v-if="item.admin_note" class="record-note"><strong>管理员留言：</strong>{{ item.admin_note }}</p>
          <div v-if="item.status === 'published' && item.content !== item.text" class="record-published"><strong>发布内容（经管理员整理）</strong><p>{{ item.content }}</p></div>
          <footer v-if="item.status === 'draft'"><el-button size="small" @click="editing = item">编辑</el-button><el-button size="small" type="primary" plain @click="submit(item)">提交审核</el-button><el-button size="small" type="danger" plain @click="remove(item)">删除</el-button></footer>
        </article>
        <el-empty v-if="!loading && !items.length" description="还没有记录。办事时遇到的坑和窍门，都可以随手记一句。" />
      </div>
      <el-dialog :model-value="editing !== null" title="编辑草稿" width="min(820px, 94vw)" destroy-on-close @close="editing = null">
        <ExperienceForm :editing="editing" :tag-options="experiences.tags" @saved="saved" />
      </el-dialog>
    </div>
  </section>
</template>

<style scoped>
.mine-page { flex: 1; min-height: 0; overflow: auto; padding: clamp(16px, 3vw, 36px); background: var(--bg); }
.mine-content { max-width: 960px; margin: 0 auto; }
.mine-intro { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; color: var(--text-2); font-size: var(--fs); }
.mine-intro p { margin: 0; line-height: 1.7; }
.status-filters { display: flex; flex-wrap: wrap; gap: 6px; margin: 16px 0; }
.status-filters button { padding: 6px 10px; border: 1px solid var(--border); border-radius: var(--radius-s); background: var(--surface); color: var(--text-2); font-size: var(--fs-s); }
.status-filters button[aria-pressed="true"] { color: var(--primary); border-color: var(--primary-line); background: var(--primary-soft); }
.status-filters span { font-size: var(--fs-xs); margin-inline-start: 4px; }
.record-list { display: grid; gap: 12px; min-height: 120px; }
.record-item { padding: 16px 18px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); overflow-wrap: anywhere; }
.record-item header, .record-item footer { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.record-item time { margin-inline-start: auto; color: var(--text-2); font-size: var(--fs-xs); }
.record-tag { padding: 2px 8px; border-radius: var(--radius-s); background: var(--exp-soft); color: var(--exp); font-size: var(--fs-xs); }
.record-text, .record-note, .record-published { margin: 12px 0 0; white-space: pre-wrap; font-size: var(--fs); line-height: 1.7; }
.record-note, .record-published { padding: 10px 12px; border-radius: var(--radius-s); }
.record-note { background: var(--warn-soft); color: var(--text-2); }
.record-published { background: var(--surface-2); border: 1px solid var(--border); }
.record-published p { margin: 6px 0 0; }
.record-item footer { margin-top: 12px; }
.record-item footer .el-button { margin: 0; }
</style>
