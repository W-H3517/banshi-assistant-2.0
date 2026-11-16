<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from './api'
import TagInput from './TagInput.vue'
import { actionText, day, statusText, statusType, type ExperienceStatus, type ReviewExperience } from './experience'

type Filter = ExperienceStatus | 'conflict'
const props = defineProps<{ tagOptions: string[] }>()
const emit = defineEmits<{ changed: [] }>()
const items = ref<ReviewExperience[]>([])
const loading = ref(true)
const filter = ref<Filter>('pending')
const current = ref<ReviewExperience | null>(null)
const working = ref<string | null>(null)
const form = reactive({ content: '', tags: [] as string[], conflict: false, conflict_note: '', admin_note: '', note: '' })
const filters: { key: Filter; label: string }[] = [
  { key: 'pending', label: '待审核' }, { key: 'verifying', label: '待核实' }, { key: 'published', label: '已发布' },
  { key: 'conflict', label: '与制度冲突' }, { key: 'rejected', label: '不发布' }, { key: 'withdrawn', label: '已下架' },
]
// “与制度冲突”只列已发布的，供定期复核。
const inFilter = (item: ReviewExperience, key: Filter) => key === 'conflict' ? item.conflict && item.status === 'published' : item.status === key
const shown = computed(() => items.value.filter(item => inFilter(item, filter.value)))
const count = (key: Filter) => items.value.filter(item => inFilter(item, key)).length
const changed = computed(() => current.value !== null && form.content.trim() !== current.value.content)

async function load() {
  loading.value = true
  try { items.value = await api<ReviewExperience[]>('/admin/experiences') }
  catch (error) { ElMessage.error(error instanceof Error ? error.message : '加载失败') }
  finally { loading.value = false }
}
function open(item: ReviewExperience) {
  current.value = item
  Object.assign(form, { content: item.content, tags: [...item.tags], conflict: item.conflict, conflict_note: item.conflict_note, admin_note: item.admin_note, note: '' })
}
async function act(action: 'verify' | 'publish' | 'reject' | 'withdraw' | 'save') {
  if (!current.value) return
  if (action === 'verify' && !form.note.trim()) { ElMessage.warning('请先在“处理记录”里写下要核实的事项或核实结果'); return }
  if (action === 'reject' || action === 'withdraw') {
    try { await ElMessageBox.confirm(action === 'reject' ? '确定不发布这条经验吗？提交人会在“我的记录”里看到状态和你的留言。' : '下架后其他同事将看不到这条经验，可以之后重新发布。', actionText[action], { confirmButtonText: actionText[action], cancelButtonText: '取消', type: 'warning' }) }
    catch { return }
  }
  working.value = action
  try {
    await api(`/admin/experiences/${current.value.id}/review`, { method: 'POST', body: JSON.stringify({ action, ...form }) })
    ElMessage.success({ verify: '已记录，状态为待核实', publish: '已发布，全部门可见', reject: '已标记为不发布', withdraw: '已下架', save: '已保存调整' }[action])
    current.value = null; await load(); emit('changed')
  } catch (error) { ElMessage.error(error instanceof Error ? error.message : '操作失败') }
  finally { working.value = null }
}
onMounted(load)
</script>

<template>
  <section class="knowledge-page">
    <div class="page-heading"><div><p class="eyebrow">WORKBENCH</p><h1>管理工作台</h1><p class="muted">审核同事提交的经验：核对是否正确，可直接调整内容后发布。</p></div></div>
    <div class="filter-row"><button v-for="item in filters" :key="item.key" :class="{ active: filter === item.key }" @click="filter = item.key">{{ item.label }}<span>{{ count(item.key) }}</span></button></div>
    <div v-loading="loading" class="review-list">
      <button v-for="item in shown" :key="item.id" class="review-item" @click="open(item)">
        <header><el-tag :type="statusType[item.status]" effect="light">{{ statusText[item.status] }}</el-tag><span v-for="tag in item.tags" :key="tag" class="review-tag">{{ tag }}</span><em v-if="item.conflict">与制度冲突</em><time>{{ item.author }} · {{ day(item.submitted_at) }}</time></header>
        <p>{{ item.content }}</p>
      </button>
      <el-empty v-if="!loading && !shown.length" description="这里暂时没有内容" />
    </div>
    <el-dialog :model-value="current !== null" title="审核经验" width="min(900px, 96vw)" top="5vh" destroy-on-close @close="current = null">
      <div v-if="current" class="review-body">
        <div class="review-meta"><el-tag :type="statusType[current.status]" effect="light">{{ statusText[current.status] }}</el-tag><span>提交人：{{ current.author }}（发布后显示为“某同事”）</span><span>提交于 {{ day(current.submitted_at) }}</span></div>
        <label>提交人原文 <small>始终保留，不会被修改</small></label>
        <p class="review-original">{{ current.text }}</p>
        <label for="review-content">发布内容 <small>可直接调整；提交人不会收到改动通知</small><em v-if="changed">已调整</em></label>
        <el-input id="review-content" v-model="form.content" type="textarea" :autosize="{ minRows: 5, maxRows: 14 }" maxlength="5000" show-word-limit />
        <label for="review-tags">标签</label>
        <TagInput id="review-tags" v-model="form.tags" :options="props.tagOptions" />
        <el-checkbox v-model="form.conflict">这条经验与制度的规定不同（发布后会向所有人标明，并列入“与制度冲突”供复核）</el-checkbox>
        <el-input v-if="form.conflict" v-model="form.conflict_note" maxlength="1000" placeholder="说明与哪份制度、哪一条不同，例如：差旅费管理办法第十二条要求政府采购渠道" />
        <label for="review-admin-note">给提交人的留言 <small>提交人在“我的记录”里可见</small></label>
        <el-input id="review-admin-note" v-model="form.admin_note" maxlength="1000" placeholder="可留空" />
        <label for="review-note">处理记录 <small>仅管理员可见，例如线下核实的情况</small></label>
        <el-input id="review-note" v-model="form.note" maxlength="1000" placeholder="本次操作的说明；记为待核实时必填" />
        <div v-if="current.events.length" class="review-events"><strong>操作记录</strong><ol><li v-for="(event, index) in current.events" :key="index"><time>{{ day(event.created_at) }}</time><span>{{ event.actor }} · {{ actionText[event.action] || event.action }}</span><p v-if="event.note">{{ event.note }}</p></li></ol></div>
      </div>
      <template #footer>
        <div v-if="current" class="review-actions">
          <el-button :loading="working === 'save'" :disabled="working !== null" @click="act('save')">仅保存调整</el-button>
          <template v-if="current.status === 'pending' || current.status === 'verifying'">
            <el-button :loading="working === 'verify'" :disabled="working !== null" @click="act('verify')">{{ current.status === 'pending' ? '需要核实' : '追加核实记录' }}</el-button>
            <el-button type="danger" plain :loading="working === 'reject'" :disabled="working !== null" @click="act('reject')">不发布</el-button>
          </template>
          <el-button v-if="current.status === 'published'" type="danger" plain :loading="working === 'withdraw'" :disabled="working !== null" @click="act('withdraw')">下架</el-button>
          <el-button v-if="current.status !== 'published' && current.status !== 'rejected'" type="primary" :loading="working === 'publish'" :disabled="working !== null || !form.content.trim() || !form.tags.length" @click="act('publish')">{{ current.status === 'withdrawn' ? '重新发布' : '发布' }}</el-button>
        </div>
      </template>
    </el-dialog>
  </section>
</template>

<style scoped>
.filter-row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:22px}.filter-row button{border:1px solid #d7e2d9;background:white;border-radius:8px;padding:8px 14px;font-size:13px;color:#5a6f63;display:flex;gap:8px;align-items:center}.filter-row button.active{background:#dfe9e0;border-color:#b8d1c6;color:#27593e;font-weight:600}.filter-row button span{font-size:11px;background:#edf2ee;border-radius:8px;padding:1px 7px}.review-list{display:grid;gap:12px;min-height:120px}.review-item{text-align:left;background:white;border:1px solid #e1e8e2;border-radius:10px;padding:16px 20px}.review-item:hover{border-color:#b8d1c6}.review-item header{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.review-item time{margin-left:auto;font-size:12px;color:#8c9a94}.review-item p{margin:12px 0 0;font-size:14px;line-height:1.8;white-space:pre-wrap;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}.review-tag{font-size:12px;padding:2px 8px;border:1px solid #d4dfd6;border-radius:4px;color:#54705f}.review-item em,.review-body label em{font-style:normal;font-size:12px;padding:2px 8px;background:#fbf1dc;border-radius:4px;color:#8a6417}.review-body{display:grid;gap:10px}.review-body label{font-size:13px;font-weight:600;margin-top:8px;display:flex;gap:8px;align-items:center}.review-body label small{font-weight:400;color:#7c8983}.review-meta{display:flex;gap:14px;align-items:center;flex-wrap:wrap;font-size:12px;color:#7c8983}.review-original{margin:0;padding:12px 14px;background:#f4f6f4;border-radius:6px;white-space:pre-wrap;line-height:1.8;font-size:14px}.review-events{margin-top:10px;font-size:13px}.review-events ol{margin:8px 0 0;padding-left:18px;display:grid;gap:6px}.review-events time{color:#8c9a94;margin-right:10px;font-size:12px}.review-events p{margin:2px 0 0;color:#5a6f63}.review-actions{display:flex;gap:8px;justify-content:flex-end;flex-wrap:wrap}.review-actions .el-button{margin-left:0}
</style>
