<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from './api'
import ExperienceForm from './ExperienceForm.vue'
import { day, statusText, statusType, type OwnExperience } from './experience'

defineProps<{ tagOptions: string[] }>()
const emit = defineEmits<{ record: [] }>()
const items = ref<OwnExperience[]>([])
const loading = ref(true)
const editing = ref<OwnExperience | null>(null)

async function load() {
  loading.value = true
  try { items.value = await api<OwnExperience[]>('/experiences/mine') }
  catch (error) { ElMessage.error(error instanceof Error ? error.message : '加载失败') }
  finally { loading.value = false }
}
async function submit(item: OwnExperience) {
  try {
    await api(`/experiences/${item.id}`, { method: 'PUT', body: JSON.stringify({ text: item.text, tags: item.tags, submit: true }) })
    ElMessage.success('已提交，等待管理员审核'); await load()
  } catch (error) { ElMessage.error(error instanceof Error ? error.message : '提交失败') }
}
async function remove(item: OwnExperience) {
  try { await ElMessageBox.confirm('确定删除这条草稿吗？删除后无法恢复。', '删除草稿', { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' }) }
  catch { return }
  try { await api(`/experiences/${item.id}`, { method: 'DELETE' }); await load() }
  catch (error) { ElMessage.error(error instanceof Error ? error.message : '删除失败') }
}
async function saved() { editing.value = null; await load() }
onMounted(load)
</script>

<template>
  <section class="knowledge-page">
    <div class="page-heading"><div><p class="eyebrow">MY RECORDS</p><h1>我的记录</h1><p class="muted">草稿仅自己可见；提交后由管理员审核，发布后全部门可见。</p></div><el-button type="primary" @click="emit('record')">记录新经验</el-button></div>
    <div v-loading="loading" class="record-list">
      <article v-for="item in items" :key="item.id" class="record-item">
        <header><el-tag :type="statusType[item.status]" effect="light">{{ statusText[item.status] }}</el-tag><span v-for="tag in item.tags" :key="tag" class="record-tag">{{ tag }}</span><time>{{ item.status === 'draft' ? '保存于 ' + day(item.updated_at) : '提交于 ' + day(item.submitted_at) }}</time></header>
        <p class="record-text">{{ item.text }}</p>
        <p v-if="item.admin_note" class="record-note"><strong>管理员留言：</strong>{{ item.admin_note }}</p>
        <div v-if="item.status === 'published' && item.content !== item.text" class="record-published"><strong>发布内容（经管理员整理）</strong><p>{{ item.content }}</p></div>
        <footer v-if="item.status === 'draft'"><el-button size="small" @click="editing = item">编辑</el-button><el-button size="small" type="primary" plain @click="submit(item)">提交审核</el-button><el-button size="small" type="danger" plain @click="remove(item)">删除</el-button></footer>
      </article>
      <el-empty v-if="!loading && !items.length" description="还没有记录。办事时遇到的坑和窍门，都可以随手记一句。" />
    </div>
    <el-dialog :model-value="editing !== null" title="编辑草稿" width="min(820px, 94vw)" destroy-on-close @close="editing = null">
      <ExperienceForm :editing="editing" :tag-options="tagOptions" @saved="saved" />
    </el-dialog>
  </section>
</template>

<style scoped>
.record-list{display:grid;gap:14px;min-height:120px}.record-item{background:white;border:1px solid #e1e8e2;border-radius:10px;padding:18px 20px}.record-item header{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.record-item time{margin-left:auto;font-size:12px;color:#8c9a94}.record-tag{font-size:12px;padding:2px 8px;border:1px solid #d4dfd6;border-radius:4px;color:#54705f}.record-text{white-space:pre-wrap;line-height:1.8;margin:14px 0 0;font-size:14px}.record-note{margin:12px 0 0;padding:10px 12px;background:#f6f3ea;border-radius:6px;font-size:13px;color:#6f5b2c}.record-published{margin-top:12px;padding:12px 14px;background:#edf4f0;border-radius:6px;font-size:13px}.record-published p{white-space:pre-wrap;line-height:1.8;margin:8px 0 0}.record-item footer{margin-top:14px;display:flex;gap:8px}.record-item footer .el-button{margin-left:0}
</style>
