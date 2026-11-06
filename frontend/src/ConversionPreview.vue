<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api, type Document } from './api'
import Markdown from './Markdown.vue'
import ReferencePreview from './ReferencePreview.vue'
import type { Endpoint } from './ReferenceEndpoint.vue'

interface Section { id: number; title: string; chapter: string; kind: string; pages: number[]; content: string }
interface Chunk { ordinal: number; pages: number[]; content: string; section_id?: number; title?: string; chapter?: string; token_count?: number; needs_review?: boolean; over_budget?: boolean; row?: number; header_pages?: number[] }
interface TablePreview { section_id: number; page: number; title: string; unit: string; header_pages: number[]; headers: string[]; status: string; rows: { row: number; content: string; fields: { name: string; value: string; source_row: number | null; source_column: number | null }[] }[] }
interface Preview {
  tables: TablePreview[];
  markdown: string; sections: Section[]; chunks: Chunk[]; current_chunks: Chunk[];
  removed_lines: { page: number; text: string; reason: string }[];
  warnings: { page: number | null; message: string }[];
  version: string; active_for_search: boolean;
}
const props = defineProps<{ document: Document }>()
const emit = defineEmits<{ close: [] }>()
const visible = ref(true)
const loading = ref(false)
const error = ref('')
const result = ref<Preview | null>(null)
const tab = ref('markdown')
const sourceMode = ref(false)
const page = ref(1)
const sourceDocument = ref({ id: props.document.id, title: props.document.title, page_count: props.document.page_count })
function showSource(item: Endpoint, sourcePage: number) { sourceDocument.value = { id: item.document_id, title: item.document_title, page_count: item.page_count }; page.value = sourcePage }
const query = ref('')
const crossPageOnly = ref(false)
const request = new AbortController()
const pdfUrl = computed(() => `/api/documents/${sourceDocument.value.id}/file#page=${page.value}`)
const kindLabels: Record<string, string> = { article: '条款', heading: '章节', table: '表格', text: '段落' }
const sections = computed(() => (result.value?.sections || []).filter(item =>
  (!crossPageOnly.value || item.pages.length > 1) && `${item.chapter}${item.content}`.includes(query.value)))
const chunks = computed(() => (tab.value === 'current' ? result.value?.current_chunks : result.value?.chunks || [])?.filter(item =>
  (tab.value === 'current' || !crossPageOnly.value || item.pages.length > 1) && item.content.includes(query.value)) || [])
async function load() {
  loading.value = true; error.value = ''
  try { result.value = await api<Preview>(`/documents/${props.document.id}/conversion-preview`, { signal: request.signal }) }
  catch (cause) { if (!request.signal.aborted) error.value = cause instanceof Error ? cause.message : '预览失败' }
  finally { loading.value = false }
}
function download() {
  if (!result.value) return
  const url = URL.createObjectURL(new Blob([result.value.markdown], { type: 'text/markdown;charset=utf-8' }))
  const anchor = window.document.createElement('a')
  anchor.href = url; anchor.download = props.document.filename.replace(/\.pdf$/i, '') + '.preview.md'
  anchor.click(); URL.revokeObjectURL(url)
}
onMounted(load)
onUnmounted(() => request.abort())
</script>

<template>
  <el-dialog v-model="visible" class="conversion-dialog" :title="`转换与切分 · ${document.title}`" width="96vw" top="3vh" destroy-on-close @closed="emit('close')">
    <el-alert title="质量预览 · 聊天仍使用现有索引" description="本页按需在本地转换，不覆盖已有资料。请先核对正文、跨页条款和表格，再决定是否切换检索。" type="info" :closable="false" />
    <div class="conversion-layout">
      <aside class="conversion-original">
        <div class="conversion-controls"><strong>PDF 原文</strong><el-input-number v-model="page" :min="1" :max="sourceDocument.page_count" :precision="0" size="small" aria-label="预览 PDF 页码" /><span>/ {{ sourceDocument.page_count }} 页</span><a :href="pdfUrl" target="_blank" rel="noopener noreferrer">新窗口查看</a></div>
        <p class="conversion-help">{{ sourceDocument.title }}</p><iframe :key="pdfUrl" :src="pdfUrl" title="转换对照 PDF 原文"></iframe>
      </aside>
      <section class="conversion-result" v-loading="loading" element-loading-text="正在本地转换并生成候选切分…">
        <div v-if="error" class="conversion-error"><el-alert :title="error" type="error" :closable="false" /><el-button @click="load">重试</el-button></div>
        <template v-if="result">
          <div class="conversion-summary"><span>{{ result.sections.length }} 个结构单元 · {{ result.chunks.length }} 个候选片段 · {{ result.current_chunks.length }} 个现行片段</span><el-button size="small" @click="download">下载 Markdown</el-button></div>
          <el-tabs v-model="tab">
            <el-tab-pane label="Markdown 全文" name="markdown" />
            <el-tab-pane label="条款与页码" name="sections" />
            <el-tab-pane label="候选切分" name="chunks" />
            <el-tab-pane label="表格行描述" name="tables" /><el-tab-pane label="引用追查" name="references" />
            <el-tab-pane label="现行切分" name="current" />
            <el-tab-pane label="核对事项" name="review" />
          </el-tabs>
          <div v-if="tab === 'markdown'" class="conversion-content">
            <el-switch v-model="sourceMode" active-text="Markdown 源码" inactive-text="阅读预览" />
            <pre v-if="sourceMode" class="conversion-text">{{ result.markdown }}</pre>
            <Markdown v-else :text="result.markdown" :available-document-ids="[]" />
          </div>
          <div v-else-if="tab === 'references'" class="conversion-content"><ReferencePreview :document-id="document.id" :sections="result.sections" @source="showSource" /></div>
          <div v-else-if="tab === 'tables'" class="conversion-content">
            <p class="conversion-help">每行带候选字段名、单位和来源。仅按实际网格展开同页合并单元格；空白不按上下行猜填。所有描述仍待核对。</p>
            <el-empty v-if="!result.tables.length" description="未检测到网格表格" />
            <section v-for="table in result.tables" :key="table.section_id" class="conversion-block">
              <h3>{{ table.title }} · 第 {{ table.page }} 页</h3><p>{{ table.status }}</p><p>单位：{{ table.unit }} · 表头来源：PDF {{ table.header_pages.join('、') }} 页</p>
              <el-button size="small" @click="sourceDocument = document; page = table.page">核对本页原表</el-button>
              <details v-for="row in table.rows" :key="row.row" class="table-row-preview"><summary>第 {{ row.row }} 行 · {{ row.fields.filter(f => f.value).slice(0, 3).map(f => f.value).join(' / ') }}</summary><pre class="conversion-text">{{ row.content }}</pre><p class="conversion-help">单元格来源：{{ row.fields.map(f => `${f.name} ← ${f.source_row ? '第' + f.source_row + '行第' + f.source_column + '列' : '无网格来源'}`).join('；') }}</p></details>
            </section>
          </div>
          <template v-else-if="['sections', 'chunks', 'current'].includes(tab)">
            <div class="conversion-controls"><el-input v-model="query" clearable placeholder="在结果中查找文字" /><el-checkbox v-if="tab !== 'current'" v-model="crossPageOnly">仅跨页</el-checkbox></div>
            <p class="conversion-help">点击页码定位左侧原文。候选片段继承完整条款；长条款按本地模型长度上限进一步切分。表格按带字段名的整行展示；超长行会明确标记，尚不入索引。</p>
            <div class="conversion-content">
              <template v-if="tab === 'sections'">
                <details v-for="item in sections" :key="item.id" class="conversion-block">
                  <summary>#{{ item.id }} · {{ item.title }} <span>{{ kindLabels[item.kind] }} · PDF {{ item.pages.join('、') }} 页</span></summary>
                  <div class="conversion-pages"><span>{{ item.chapter }}</span><el-button v-for="p in item.pages" :key="p" size="small" @click="sourceDocument = document; page = p">第 {{ p }} 页</el-button></div>
                  <pre class="conversion-text">{{ item.content }}</pre>
                </details>
                <el-empty v-if="!sections.length" description="没有匹配的条款" />
              </template>
              <template v-else>
                <article v-for="item in chunks" :key="item.ordinal" class="conversion-block">
                  <strong>片段 {{ item.ordinal + 1 }}<span v-if="item.section_id"> · {{ item.title }}（结构单元 #{{ item.section_id }}）</span></strong>
                  <div class="conversion-pages"><span v-if="item.token_count">含标题 {{ item.token_count }} tokens</span><el-button v-for="p in item.pages" :key="p" size="small" @click="sourceDocument = document; page = p">第 {{ p }} 页</el-button></div>
                  <p v-if="item.needs_review" class="conversion-help">表格第 {{ item.row }} 行 · 表头来源 {{ item.header_pages?.join('、') }} 页 · 字段关系待审核</p><el-tag v-if="item.over_budget" type="warning">超过模型输入上限，尚不可建立索引</el-tag><pre class="conversion-text">{{ item.content }}</pre>
                </article>
                <el-empty v-if="!chunks.length" description="没有匹配的片段" />
              </template>
            </div>
          </template>
          <div v-else class="conversion-content">
            <h3>待核对</h3>
            <div v-for="(item, index) in result.warnings" :key="index" class="conversion-block"><el-button v-if="item.page" size="small" @click="sourceDocument = document; page = item.page">第 {{ item.page }} 页</el-button> {{ item.message }}</div>
            <h3>已移除的页边文字（{{ result.removed_lines.length }}）</h3>
            <div v-for="(item, index) in result.removed_lines" :key="index" class="conversion-block"><el-button size="small" @click="sourceDocument = document; page = item.page">第 {{ item.page }} 页</el-button> {{ item.text }}<p>{{ item.reason }}</p></div>
            <p>转换规则：{{ result.version }}。预览不调用聊天模型，也不生成或替换向量索引。</p>
          </div>
        </template>
      </section>
    </div>
  </el-dialog>
</template>

<style scoped>
.table-row-preview{border-top:1px solid #e5e9e1;padding:10px 0}.table-row-preview summary{cursor:pointer;line-height:1.7}

.conversion-layout{display:grid;grid-template-columns:42% minmax(0,1fr);gap:22px;margin-top:18px;height:70vh}.conversion-original,.conversion-result{min-width:0;min-height:0;display:flex;flex-direction:column}.conversion-original iframe{width:100%;flex:1;border:1px solid #dfe6dc;border-radius:8px}.conversion-controls,.conversion-summary,.conversion-pages{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:12px}.conversion-controls .el-input{flex:1;min-width:180px}.conversion-summary{justify-content:space-between;font-size:12px}.conversion-content{overflow:auto;flex:1;min-height:0;padding:4px 12px 16px 0}.conversion-text{white-space:pre-wrap;overflow-wrap:anywhere;font-family:inherit;font-size:13px;line-height:1.9;margin:12px 0}.conversion-block{border:1px solid #dfe6dc;border-radius:8px;padding:14px;margin-bottom:12px;font-size:13px}.conversion-block summary{cursor:pointer;line-height:1.8}.conversion-block summary span{color:#7a8775;font-size:12px}.conversion-pages{margin-top:12px;color:#7a8775;font-size:12px}.conversion-help{font-size:12px;line-height:1.7;color:#7a8775;margin:0 0 12px}.conversion-error{padding:20px}.conversion-content :deep(table){font-size:11px}.conversion-content :deep(td){min-width:55px}.conversion-content :deep(.markdown){padding-top:16px}@media(max-width:900px){.conversion-layout{grid-template-columns:1fr;height:72vh}.conversion-original{display:none}}
</style>
