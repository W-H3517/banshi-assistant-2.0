<script setup lang="ts">
import { ref, onUnmounted } from 'vue'
import { api } from './api'
import ReferenceEndpoint, { type Endpoint } from './ReferenceEndpoint.vue'
interface Edge { source: Endpoint; targets: Endpoint[]; reference: string; status: string; scope: string; reason: string }
interface Trace { main: Endpoint; outgoing: Edge[]; incoming: Edge[]; exception_candidates: { target: Endpoint; shared_terms: string[]; cues: string[]; reason: string }[]; candidate_count: number; document_count: number; notice: string }
const props = defineProps<{ documentId: string; sections: { id: number; title: string; kind: string; pages: number[] }[] }>()
const emit = defineEmits<{ source: [item: Endpoint, page: number] }>()
const selected = ref(props.sections.find(s => s.kind === 'article')?.id || props.sections[0]?.id)
const result = ref<Trace | null>(null)
const loading = ref(false)
const error = ref('')
let request: AbortController | null = null
const labels: Record<string, string> = { resolved: '已定位（适用性待核对）', missing: '未定位', ambiguous: '有歧义，未选择版本' }
async function trace(documentId = props.documentId, sectionId = selected.value) {
  request?.abort(); request = new AbortController()
  const active = request
  loading.value = true; error.value = ''; result.value = null
  try { result.value = await api<Trace>(`/documents/${documentId}/reference-preview?section_id=${sectionId}`, { signal: active.signal }) }
  catch (cause) { if (!active.signal.aborted) error.value = cause instanceof Error ? cause.message : '追查失败' }
  finally { if (request === active) loading.value = false }
}
function follow(item: Endpoint) { if (item.section_id) trace(item.document_id, item.section_id) }
function source(item: Endpoint, page: number) { emit('source', item, page) }
onUnmounted(() => request?.abort())
</script>
<template>
  <div class="reference-preview" v-loading="loading" element-loading-text="正在本地扫描已入库制度的引用…">
    <div class="reference-toolbar"><el-select v-model="selected" aria-label="主条款" filterable><el-option v-for="s in sections.filter(s => s.kind !== 'table')" :key="s.id" :value="s.id" :label="`${s.title} · PDF ${s.pages.join('、')} 页`" /></el-select><el-button type="primary" :disabled="!selected || loading" @click="trace()">追查所选条款</el-button></div>
    <p class="reference-note">先选主要条款，再查它引用谁、谁引用它。限制与例外候选另列，不自动作为适用依据。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <template v-if="result">
      <p class="reference-note">已扫描 {{ result.document_count }} 份制度。{{ result.notice }}</p>
      <h3>当前主条款</h3><ReferenceEndpoint :item="result.main" @follow="follow" @source="source" />
      <template v-for="group in [{ title: '向外引用', items: result.outgoing }, { title: '反向引用', items: result.incoming }]" :key="group.title">
        <h3>{{ group.title }}（{{ group.items.length }}）</h3>
        <p v-if="!group.items.length" class="reference-note">未发现明确引用，不代表不存在关联。</p>
        <article v-for="(edge, index) in group.items" :key="index" class="reference-card">
          <el-tag :type="edge.status === 'resolved' ? 'info' : 'warning'">{{ labels[edge.status] }}</el-tag>
          <p>引用文字：{{ edge.reference }} · {{ edge.scope === 'document' ? '仅制度级' : '条款 / 附件级' }}</p>
          <p class="reference-note">{{ edge.reason }}</p>
          <span>引用方</span><ReferenceEndpoint :item="edge.source" @follow="follow" @source="source" />
          <span v-if="edge.targets.length">被引用方{{ edge.status === 'ambiguous' ? '（候选）' : '' }}</span><ReferenceEndpoint v-for="item in edge.targets" :key="`${item.document_id}-${item.section_id}`" :item="item" @follow="follow" @source="source" />
        </article>
      </template>
      <h3>限制与例外候选（展示 {{ result.exception_candidates.length }} / {{ result.candidate_count }}）</h3>
      <p v-if="!result.candidate_count" class="reference-note">本次规则未找到候选，不代表没有例外。</p>
      <article v-for="item in result.exception_candidates" :key="`${item.target.document_id}-${item.target.section_id}`" class="reference-card"><p>{{ item.reason }}</p><p class="reference-note">共同主题：{{ item.shared_terms.join('、') }}；提示词：{{ item.cues.join('、') }}</p><ReferenceEndpoint :item="item.target" @follow="follow" @source="source" /></article>
    </template>
  </div>
</template>
<style scoped>
.reference-preview{min-height:180px}.reference-toolbar{display:flex;gap:10px}.reference-toolbar .el-select{flex:1}.reference-note{font-size:12px;color:#7a8775;line-height:1.8}.reference-card{border:1px solid #dfe6dc;border-radius:8px;padding:14px;margin-bottom:12px;font-size:13px}h3{margin:20px 0 10px}
</style>
