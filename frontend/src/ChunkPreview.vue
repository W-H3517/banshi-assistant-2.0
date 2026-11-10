<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import { VuePDF } from '@tato30/vue-pdf/minimal'
import { getDocument, GlobalWorkerOptions, type PDFDocumentLoadingTask } from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { api, type Document } from './api'

interface Region { page: number; x: number; y: number; width: number; height: number }
interface Chunk {
  ordinal: number; title: string; chapter: string; pages: number[]; content: string;
  token_count: number; regions: Region[]; location_status: 'complete' | 'partial' | 'unlocated';
  needs_review?: boolean; over_budget?: boolean; row?: number; header_pages?: number[];
  warnings?: string[];
  source: { document_title: string; filename: string; chapter: string; article: string };
  retrieval_text: string;
  boundary_status: 'confirmed' | 'uncertain' | 'broken';
  boundary_reasons: string[];
}
interface Preview {
  pages: { number: number; width: number; height: number }[];
  chunks: Chunk[]; removed_line_count: number;
  ocr_pages?: number[]; warnings?: string[];
}
const props = defineProps<{ document: Document }>()
const emit = defineEmits<{ close: [] }>()
GlobalWorkerOptions.workerSrc = workerUrl
const visible = ref(true)
const loading = ref(true)
const error = ref('')
const result = shallowRef<Preview>()
const pdf = shallowRef<PDFDocumentLoadingTask>()
const pdfPages = ref<{ cancel: () => void }[]>([])
const scrollArea = ref<HTMLElement>()
const page = ref(1)
const pageWidth = ref(700)
const hovered = ref<number[]>([])
const pinned = ref<number | null>(null)
const pinnedGroup = ref<number[]>([])
const activeId = computed(() => hovered.value[0] ?? pinned.value)
const active = computed(() => result.value?.chunks.find(c => c.ordinal === activeId.value))
const choices = computed(() => hovered.value.length ? hovered.value : pinnedGroup.value)
const colors = ['#f5cc45', '#6ac8aa', '#7faaf0', '#d99bda']
const statusLabels = { complete: '已定位', partial: '部分定位，请核对', unlocated: '未定位，未猜测原文位置' }
const pageRegions = computed(() => {
  const grouped: Record<number, (Region & { ordinal: number })[]> = {}
  for (const chunk of result.value?.chunks || []) for (const rect of chunk.regions) {
    (grouped[rect.page] ??= []).push({ ...rect, ordinal: chunk.ordinal })
  }
  return grouped
})
let request: AbortController | undefined
let observer: ResizeObserver | undefined
let closed = false

function fitWidth() {
  if (scrollArea.value) pageWidth.value = Math.max(200, scrollArea.value.clientWidth - 40)
}
function jumpPage(value: number | undefined) {
  if (value) scrollArea.value?.querySelector(`[data-page="${value}"]`)?.scrollIntoView({ block: 'start' })
}
function selectChunk(id: number) {
  pinnedGroup.value = [id]
  pinned.value = id
  hovered.value = []
  const chunk = result.value?.chunks.find(c => c.ordinal === id)
  if (chunk) { page.value = chunk.pages[0]; jumpPage(page.value) }
}
function hits(event: MouseEvent, number: number) {
  const bounds = (event.currentTarget as HTMLElement).getBoundingClientRect()
  const x = (event.clientX - bounds.left) / bounds.width
  const y = (event.clientY - bounds.top) / bounds.height
  return [...new Set((pageRegions.value[number] || []).filter(r =>
    x >= r.x && x <= r.x + r.width && y >= r.y && y <= r.y + r.height).map(r => r.ordinal))]
}
function pinAt(event: MouseEvent, number: number) {
  pinnedGroup.value = hits(event, number)
  pinned.value = pinnedGroup.value[0] ?? null
  hovered.value = []
}
function switchOverlap(id: number) { pinned.value = id; hovered.value = [] }
function reportError(cause: unknown) {
  if (!closed) error.value = cause instanceof Error ? cause.message : 'PDF 渲染失败，请重试'
}
async function load() {
  loading.value = true; error.value = ''; result.value = undefined
  hovered.value = []; pinned.value = null; pinnedGroup.value = []
  request?.abort()
  request = new AbortController()
  for (const item of pdfPages.value) item.cancel()
  await pdf.value?.destroy()
  const task = getDocument({
    url: `/api/documents/${props.document.id}/file`,
    cMapUrl: '/pdfjs/cmaps/', cMapPacked: true,
    standardFontDataUrl: '/pdfjs/standard_fonts/', wasmUrl: '/pdfjs/wasm/',
  })
  pdf.value = task
  try {
    const [data] = await Promise.all([
      api<Preview>(`/documents/${props.document.id}/chunk-preview`, { signal: request.signal }),
      task.promise,
    ])
    if (closed) return
    result.value = data
    await nextTick()
    fitWidth()
    observer?.disconnect()
    observer = new ResizeObserver(fitWidth)
    if (scrollArea.value) observer.observe(scrollArea.value)
  } catch (cause) {
    reportError(cause)
  } finally { loading.value = false }
}
onMounted(load)
onBeforeUnmount(() => {
  closed = true; request?.abort(); observer?.disconnect()
  for (const item of pdfPages.value) item.cancel()
  void pdf.value?.destroy()
})
</script>

<template>
  <el-dialog v-model="visible" :title="`切分预览 · ${document.title}`" width="96vw" top="3vh" destroy-on-close @closed="emit('close')">
    <p class="preview-note">当前检索片段 · 已融合文件、章节和条目信息。悬停查看，点击固定；跨行、跨页同色。</p>
    <div v-if="error" class="preview-error"><el-alert :title="error" type="error" :closable="false" /><el-button :disabled="loading" @click="load">重试</el-button></div>
    <div v-loading="loading" element-loading-text="正在加载 PDF 和切分结果…" class="preview-body">
      <template v-if="result && pdf">
        <section class="pdf-column">
          <div class="preview-toolbar">
            <span>页码</span><el-input-number v-model="page" :min="1" :max="result.pages.length" :precision="0" size="small" aria-label="PDF 页码" @change="jumpPage" />
            <span>/ {{ result.pages.length }}</span><el-button size="small" @click="fitWidth">适应宽度</el-button>
            <span>{{ result.chunks.length }} 个检索片段</span>
          </div>
          <div ref="scrollArea" class="pdf-scroll">
            <div v-for="item in result.pages" :key="item.number" :data-page="item.number" class="pdf-sheet" :style="{ width: `${pageWidth}px` }">
              <div class="page-caption">第 {{ item.number }} 页</div>
              <VuePDF ref="pdfPages" :pdf="pdf" :page="item.number" :width="pageWidth" :auto-destroy="false" @error="reportError">
                <template #overlay="{ width, height }">
                  <div v-if="width && height" class="chunk-overlay" :style="{ width: `${width}px`, height: `${height}px` }"
                    @mousemove="hovered = hits($event, item.number)" @mouseleave="hovered = []" @click="pinAt($event, item.number)">
                    <span v-for="(rect, index) in pageRegions[item.number] || []" :key="index" class="chunk-highlight" :data-chunk="rect.ordinal"
                      :style="{ left: `${rect.x * 100}%`, top: `${rect.y * 100}%`, width: `${rect.width * 100}%`, height: `${rect.height * 100}%`, background: colors[rect.ordinal % colors.length], opacity: activeId === null ? .25 : activeId === rect.ordinal ? .5 : .1 }" />
                  </div>
                </template>
              </VuePDF>
            </div>
          </div>
        </section>
        <aside class="chunk-details">
          <el-select :model-value="pinned" placeholder="选择片段（含未定位片段）" aria-label="选择片段" @change="selectChunk">
            <el-option v-for="chunk in result.chunks" :key="chunk.ordinal" :value="chunk.ordinal" :label="`片段 ${chunk.ordinal + 1} · ${chunk.title}`" />
          </el-select>
          <div v-if="choices.length > 1" class="overlap-choices"><p>此处属于多个片段，点击固定后可切换：</p><el-button v-for="id in choices" :key="id" size="small" :type="activeId === id ? 'primary' : 'default'" @click="switchOverlap(id)">{{ id + 1 }}</el-button></div>
          <template v-if="active">
            <h3>片段 {{ active.ordinal + 1 }} · {{ active.title }}</h3>
            <p>文件：{{ active.source.filename }}</p>
            <p>章节：{{ active.source.chapter || '未明确识别' }}<span v-if="active.source.article"> · {{ active.source.article }}</span></p>
            <p>正文 {{ active.token_count }} tokens</p>
            <p>阅读边界：{{ active.boundary_status === 'confirmed' ? '已按结构确认' : '需核对原页' }}（不代表全文识别无误）</p>
            <p v-for="reason in active.boundary_reasons" :key="reason" class="location-warning">{{ reason }}</p>
            <p>PDF {{ active.pages.join('、') }} 页 <span v-if="active.pages.length > 1">· 跨页片段</span></p>
            <p :class="{ 'location-warning': active.location_status !== 'complete' }">{{ statusLabels[active.location_status] }}</p>
            <p v-if="active.needs_review && active.row !== undefined" class="location-warning">表格第 {{ active.row }} 行 · 字段关系待核对；表头来源第 {{ active.header_pages?.join('、') }} 页。高亮为本行实际来源单元格。</p>
            <p v-for="warning in active.warnings || []" :key="warning" class="location-warning">{{ warning }}</p>
            <el-tag v-if="active.over_budget" type="warning">超过模型输入上限</el-tag>
            <pre>{{ active.content }}</pre>
            <details><summary>查看用于检索的完整片段</summary><pre>{{ active.retrieval_text }}</pre></details>
            <p v-if="pinned === active.ordinal" class="preview-note">已固定，点击 PDF 空白处取消。</p>
          </template>
          <p v-else class="preview-note">将鼠标移到高亮区域查看片段，或从上方选择。</p>
          <p v-for="warning in result.warnings || []" :key="warning" class="location-warning">{{ warning }}</p>
          <p class="preview-note">纯标题作为所属信息保留，不单独建片段。已排除 {{ result.removed_line_count }} 个页边、脚注或目录块；原页核验仍可读取。</p>
        </aside>
      </template>
    </div>
  </el-dialog>
</template>

<style scoped>
.preview-note{font-size:13px;color:#758173;line-height:1.7;margin:0 0 12px}.preview-body{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:20px;height:73vh;min-height:300px}.pdf-column{display:flex;flex-direction:column;min-height:0;min-width:0}.preview-toolbar{display:flex;gap:10px;align-items:center;margin-bottom:12px;font-size:13px}.pdf-scroll{overflow:auto;background:#e9ece7;flex:1;padding:0 20px 20px;min-height:0}.pdf-sheet{margin:0 auto 18px}.page-caption{padding:10px 0;font-size:12px;color:#6e7869}.chunk-overlay{position:absolute;top:0;left:0;cursor:pointer}.chunk-highlight{position:absolute;pointer-events:none;mix-blend-mode:multiply}.chunk-details{overflow:auto;padding:0 10px 0 0;font-size:13px;line-height:1.7}.chunk-details h3{font-size:16px;margin:18px 0 10px}.chunk-details pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;border-top:1px solid #e3e7df;padding-top:14px}.location-warning{color:#9a641b}.preview-error{display:flex;gap:12px;margin-bottom:12px}.overlap-choices{padding:10px 0}.overlap-choices .el-button{margin:0 6px 6px 0}
</style>
