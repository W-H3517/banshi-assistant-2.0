<script setup lang="ts">
/**
 * 制度阅读器，知识库和聊天证据栏共用。
 * 两种模式：切分预览（默认，叠加检索片段的高亮）和原文（只显示 PDF）。
 * 传入 focusNodes 时，属于这些段落的片段作为“被引用片段”突出显示并滚动到位。
 */
import { computed, nextTick, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { VuePDF } from '@tato30/vue-pdf/minimal'
import { getDocument, GlobalWorkerOptions, type PDFDocumentLoadingTask } from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { api, isAbortError, sessionSignal, type Document } from '../api'

interface Region { page: number; x: number; y: number; width: number; height: number }
interface Chunk {
  ordinal: number; node_id: string; parent_id: string | null; title: string; pages: number[]; content: string
  regions: Region[]; row?: number; header_pages?: number[]
  source: { document_title: string; filename: string; chapter: string; article: string }
  // 以下为诊断信息，后端只返回给管理员。
  token_count?: number; location_status?: 'complete' | 'partial' | 'unlocated'
  boundary_status?: 'confirmed' | 'uncertain' | 'broken'; boundary_reasons?: string[]; warnings?: string[]
}
interface Preview {
  pages: { number: number; width: number; height: number }[]
  chunks: Chunk[]; ocr_pages: number[]; warnings: string[]; document: Document
  removed_line_count?: number
}

const props = defineProps<{
  documentId: string
  /** 要定位到的页码。 */
  page?: number
  /** 被引用的段落（阅读单元节点 ID）；整表的节点对应它的各行片段。 */
  focusNodes?: string[]
  /** 窄栏布局：片段详情放在页面下方而不是右侧。 */
  stacked?: boolean
}>()
const emit = defineEmits<{ loaded: [document: Document] }>()
GlobalWorkerOptions.workerSrc = workerUrl

const mode = ref<'chunks' | 'pdf'>('chunks')
const loading = ref(true)
const error = ref('')
const result = shallowRef<Preview>()
const pdf = shallowRef<PDFDocumentLoadingTask>()
const pdfPages = ref<{ cancel: () => void }[]>([])
const scrollArea = ref<HTMLElement>()
const currentPage = ref(1)
const pageWidth = ref(700)
/** 已进入可视范围、需要渲染的页；长文件只渲染看到的页。 */
const rendered = ref(new Set<number>())
const hovered = ref<number[]>([])
const pinned = ref<number | null>(null)
const pinnedGroup = ref<number[]>([])
const colors = ['var(--chunk-yellow)', 'var(--chunk-green)', 'var(--chunk-violet)', 'var(--chunk-orange)']
const statusLabels = { complete: '已定位', partial: '部分定位，请核对', unlocated: '未定位，未猜测原文位置' }

const cited = computed(() => {
  const focus = new Set(props.focusNodes || [])
  return new Set((result.value?.chunks || [])
    .filter(c => focus.has(c.node_id) || (c.parent_id !== null && focus.has(c.parent_id)))
    .map(c => c.ordinal))
})
const activeId = computed(() => hovered.value[0] ?? pinned.value)
const active = computed(() => result.value?.chunks.find(c => c.ordinal === activeId.value))
const choices = computed(() => hovered.value.length ? hovered.value : pinnedGroup.value)
const pageRegions = computed(() => {
  const grouped: Record<number, (Region & { ordinal: number })[]> = {}
  for (const chunk of result.value?.chunks || []) for (const rect of chunk.regions) {
    (grouped[rect.page] ??= []).push({ ...rect, ordinal: chunk.ordinal })
  }
  return grouped
})
let request: AbortController | undefined
let destroying: Promise<void> | undefined
let resizeObserver: ResizeObserver | undefined
let pageObserver: IntersectionObserver | undefined
let closed = false

function highlight(ordinal: number) {
  const isCited = cited.value.has(ordinal)
  const background = isCited ? 'var(--primary)' : colors[ordinal % colors.length]
  let opacity: number
  if (activeId.value === ordinal) opacity = .45
  else if (cited.value.size) opacity = isCited ? .28 : .07
  else opacity = activeId.value === null ? .25 : .1
  return { background, opacity }
}
function sheetHeight(item: { width: number; height: number }) {
  return pageWidth.value * item.height / item.width
}
function fitWidth() {
  const area = scrollArea.value
  if (!area) return
  const style = getComputedStyle(area)
  const width = Math.min(900, Math.max(1, Math.floor(area.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight))))
  if (width === pageWidth.value) return
  const anchor = sheet(currentPage.value)
  const offset = anchor ? (area.scrollTop - anchor.offsetTop) / anchor.offsetHeight : 0
  pageWidth.value = width
  if (anchor && area.scrollTop > 0) void nextTick(() => {
    if (scrollArea.value === area) area.scrollTop = anchor.offsetTop + offset * anchor.offsetHeight
  })
}
function sheet(number: number) {
  return scrollArea.value?.querySelector<HTMLElement>(`[data-page="${number}"]`)
}
function jumpPage(value: number | undefined) {
  if (value) sheet(value)?.scrollIntoView({ block: 'start' })
}
function trackPage() {
  const area = scrollArea.value
  if (!area) return
  const line = area.scrollTop + area.clientHeight / 3
  for (const item of area.querySelectorAll<HTMLElement>('[data-page]')) {
    if (item.offsetTop + item.offsetHeight > line) { currentPage.value = Number(item.dataset.page); return }
  }
}
function selectChunk(id: number) {
  const chunk = result.value?.chunks.find(c => c.ordinal === id)
  if (!chunk) return
  pinnedGroup.value = [id]; pinned.value = id; hovered.value = []
  scrollToRegion(chunk.regions[0], chunk.pages[0]!)
}
function scrollToRegion(region: Region | undefined, fallbackPage: number) {
  const target = sheet(region?.page ?? fallbackPage)
  if (!target || !scrollArea.value) return
  const offset = region ? region.y * target.offsetHeight - 80 : 0
  scrollArea.value.scrollTo({ top: target.offsetTop + Math.max(0, offset) })
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
  if (!closed && !isAbortError(cause)) error.value = cause instanceof Error ? cause.message : 'PDF 渲染失败，请重试'
}

/** 定位到要看的位置：有被引用片段时固定第一个并滚到它，否则滚到指定页。 */
function reveal() {
  if (!result.value) return
  const page = props.page ?? 1
  const chunks = result.value.chunks.filter(c => cited.value.has(c.ordinal))
  const chunk = chunks.find(c => c.pages.includes(page)) ?? chunks[0]
  hovered.value = []
  if (!chunk) {
    pinned.value = null; pinnedGroup.value = []
    scrollToRegion(undefined, page)
    return
  }
  mode.value = 'chunks'
  pinned.value = chunk.ordinal; pinnedGroup.value = [chunk.ordinal]
  scrollToRegion(chunk.regions.find(r => r.page === page) ?? chunk.regions[0], page)
}

async function load() {
  loading.value = true; error.value = ''; result.value = undefined
  hovered.value = []; pinned.value = null; pinnedGroup.value = []; rendered.value = new Set()
  request?.abort()
  request = new AbortController()
  const signal = AbortSignal.any([request.signal, sessionSignal()])
  for (const item of pdfPages.value) item.cancel()
  try {
    await destroying
    // 等待旧任务销毁期间又切换或退出时不再创建：已中止的信号不会再触发下面的销毁监听。
    signal.throwIfAborted()
    const task = getDocument({
      url: `/api/documents/${props.documentId}/file`,
      cMapUrl: '/pdfjs/cmaps/', cMapPacked: true,
      standardFontDataUrl: '/pdfjs/standard_fonts/', wasmUrl: '/pdfjs/wasm/',
    })
    pdf.value = task
    // PDF.js 自行发起分段请求；销毁加载任务会取消其网络请求及渲染。
    signal.addEventListener('abort', () => {
      destroying = task.destroy().catch(reportError)
    }, { once: true })
    const [data] = await Promise.all([
      api<Preview>(`/documents/${props.documentId}/chunk-preview`, { signal }),
      task.promise,
    ])
    // task.promise 不受信号约束，中止后仍可能完成。
    signal.throwIfAborted()
    result.value = data
    emit('loaded', data.document)
    await nextTick()
    fitWidth()
    resizeObserver?.disconnect()
    resizeObserver = new ResizeObserver(fitWidth)
    pageObserver?.disconnect()
    pageObserver = new IntersectionObserver(entries => {
      const visible = entries.filter(e => e.isIntersecting).map(e => Number((e.target as HTMLElement).dataset.page))
      if (visible.some(n => !rendered.value.has(n))) rendered.value = new Set([...rendered.value, ...visible])
    }, { root: scrollArea.value, rootMargin: '800px 0px' })
    if (scrollArea.value) {
      resizeObserver.observe(scrollArea.value)
      for (const item of scrollArea.value.querySelectorAll('[data-page]')) pageObserver.observe(item)
    }
    await nextTick()
    reveal()
    trackPage()
  } catch (cause) {
    if (!signal.aborted) reportError(cause)
  } finally { if (!signal.aborted) loading.value = false }
}

watch(() => props.documentId, load, { immediate: true })
watch(() => [props.page, (props.focusNodes || []).join()], reveal)
onBeforeUnmount(() => {
  closed = true; request?.abort(); resizeObserver?.disconnect(); pageObserver?.disconnect()
  for (const item of pdfPages.value) item.cancel()
})
</script>

<template>
  <div class="reader" :class="{ stacked }">
    <div class="tools">
      <div class="segmented" role="tablist" aria-label="显示模式">
        <button role="tab" :aria-selected="mode === 'chunks'" :class="{ on: mode === 'chunks' }" @click="mode = 'chunks'">切分预览</button>
        <button role="tab" :aria-selected="mode === 'pdf'" :class="{ on: mode === 'pdf' }" @click="mode = 'pdf'">原文</button>
      </div>
      <span v-if="mode === 'chunks' && !stacked" class="hint">悬停查看片段，点击固定</span>
      <span class="grow"></span>
      <template v-if="result">
        <span>第</span>
        <el-input-number v-model="currentPage" :min="1" :max="result.pages.length" :precision="0" :controls="false" size="small" aria-label="PDF 页码" @change="jumpPage" />
        <span>/ {{ result.pages.length }} 页</span>
        <el-button v-if="!stacked" size="small" @click="fitWidth">适应宽度</el-button>
      </template>
    </div>
    <div v-if="error" class="failure">
      <el-alert :title="error" type="error" :closable="false" /><el-button :disabled="loading" @click="load">重试</el-button>
    </div>
    <div v-loading="loading" element-loading-text="正在加载原文…" class="body">
      <template v-if="result && pdf">
        <div ref="scrollArea" class="pages" @scroll.passive="trackPage">
          <div
            v-for="item in result.pages" :key="item.number" :data-page="item.number" class="sheet"
            :style="{ width: `${pageWidth}px`, height: `${sheetHeight(item)}px` }"
          >
            <VuePDF v-if="rendered.has(item.number)" ref="pdfPages" :pdf="pdf" :page="item.number" :width="pageWidth" :auto-destroy="false" @error="reportError">
              <template #overlay="{ width, height }">
                <div
                  v-if="mode === 'chunks' && width && height" class="overlay" :style="{ width: `${width}px`, height: `${height}px` }"
                  @mousemove="hovered = hits($event, item.number)" @mouseleave="hovered = []" @click="pinAt($event, item.number)"
                >
                  <span
                    v-for="(rect, index) in pageRegions[item.number] || []" :key="index" class="mark" :data-chunk="rect.ordinal"
                    :style="{ left: `${rect.x * 100}%`, top: `${rect.y * 100}%`, width: `${rect.width * 100}%`, height: `${rect.height * 100}%`, ...highlight(rect.ordinal) }"
                  />
                </div>
              </template>
            </VuePDF>
          </div>
        </div>
        <aside v-if="mode === 'chunks'" class="detail">
          <div v-if="choices.length > 1" class="overlap">
            <span>此处属于多个片段：</span>
            <el-button v-for="id in choices" :key="id" size="small" :type="activeId === id ? 'primary' : 'default'" @click="switchOverlap(id)">{{ id + 1 }}</el-button>
          </div>
          <template v-if="active">
            <div class="detail-head">
              <strong>{{ cited.has(active.ordinal) ? '被引用片段' : `片段 ${active.ordinal + 1}` }}</strong>
              <span class="pill">{{ [active.source.chapter, active.source.article].filter(Boolean).join(' · ') || active.title }}</span>
              <span class="pill">第 {{ active.pages.join('、') }} 页</span>
              <span class="grow"></span>
              <el-button size="small" text :disabled="active.ordinal === 0" aria-label="上一片段" @click="selectChunk(active.ordinal - 1)">‹</el-button>
              <el-button size="small" text :disabled="active.ordinal === result.chunks.length - 1" aria-label="下一片段" @click="selectChunk(active.ordinal + 1)">›</el-button>
            </div>
            <p v-if="active.row !== undefined" class="note">表格第 {{ active.row }} 行，表头在第 {{ active.header_pages?.join('、') }} 页；高亮为本行实际所在的单元格。</p>
            <pre class="content">{{ active.content }}</pre>
            <div v-if="active.token_count !== undefined" class="diagnostics">
              <div class="diagnostics-title"><span>诊断信息</span><span class="pill">仅管理员可见</span></div>
              <dl>
                <dt>片段</dt><dd>#{{ active.ordinal + 1 }} · {{ active.token_count }} tokens</dd>
                <dt>阅读边界</dt><dd>{{ active.boundary_status === 'confirmed' ? '已按结构确认' : '需核对原页' }}</dd>
                <dt>高亮定位</dt><dd :class="{ warn: active.location_status !== 'complete' }">{{ statusLabels[active.location_status!] }}</dd>
                <template v-if="result.removed_line_count !== undefined"><dt>排除块</dt><dd>本文件 {{ result.removed_line_count }} 个页边、脚注、目录或落款块</dd></template>
              </dl>
              <p v-for="reason in active.boundary_reasons" :key="reason" class="warn">{{ reason }}</p>
              <p v-for="warning in active.warnings" :key="warning" class="warn">{{ warning }}</p>
            </div>
          </template>
          <p v-else class="note">
            {{ stacked ? '点击页面上的高亮区域查看片段。' : '将鼠标移到高亮区域查看片段，点击可固定。' }}
            <el-button v-if="result.chunks.length" size="small" text type="primary" @click="selectChunk(0)">从第一个片段开始</el-button>
          </p>
          <p v-for="warning in result.warnings" :key="warning" class="warn">{{ warning }}</p>
        </aside>
      </template>
    </div>
  </div>
</template>

<style scoped>
.reader { flex: 1; min-width: 0; min-height: 0; display: flex; flex-direction: column; background: var(--bg); color: var(--text); font-size: var(--fs-s); }
.tools { flex-shrink: 0; display: flex; align-items: center; flex-wrap: wrap; gap: 8px; padding: 8px 16px; background: var(--surface); border-bottom: 1px solid var(--border); color: var(--text-2); }
.tools .el-input-number { width: 52px; }
.grow { flex: 1; }
.hint { color: var(--text-3); }
.failure { display: flex; gap: 12px; padding: 12px 16px; }
.body { flex: 1; min-height: 0; display: flex; }
.stacked .body { flex-direction: column; }
.pages { position: relative; flex: 1; min-width: 0; min-height: 0; overflow: auto; padding: 20px; overflow-anchor: none; }
.sheet { position: relative; margin: 0 auto 16px; background: var(--paper); box-shadow: 0 1px 3px rgba(0, 0, 0, .12); }
.overlay { position: absolute; top: 0; left: 0; cursor: pointer; }
.mark { position: absolute; pointer-events: none; mix-blend-mode: multiply; }
.detail { width: 320px; flex-shrink: 0; overflow: auto; padding: 14px 16px; background: var(--surface); border-left: 1px solid var(--border); line-height: 1.7; }
.stacked .detail { width: auto; max-height: 38%; border-left: 0; border-top: 1px solid var(--border); }
.detail-head { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
.detail-head strong { font-size: var(--fs); font-weight: 600; }
.detail-head .el-button { margin: 0; padding: 0 8px; font-size: var(--fs-l); }
.pill { display: inline-flex; align-items: center; min-width: 0; min-height: 22px; padding: 2px 8px; border-radius: var(--radius-s); background: var(--sidebar); color: var(--text-2); font-size: var(--fs-xs); line-height: 1.5; overflow-wrap: anywhere; }
.content { margin: 0 0 12px; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface-2); font: inherit; line-height: 1.8; white-space: pre-wrap; overflow-wrap: anywhere; }
.stacked .content { max-height: 9em; overflow: auto; }
.reader { container: document-reader / inline-size; }
@container document-reader (max-width: 720px) {
  .body { flex-direction: column; }
  .detail { width: auto; max-height: 38%; border-left: 0; border-top: 1px solid var(--border); }
  .hint { display: none; }
}
.note { margin: 0 0 10px; color: var(--text-3); }
.warn { margin: 6px 0 0; color: var(--warn); }
.overlap { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; color: var(--text-2); }
.overlap .el-button { margin: 0; }
.diagnostics { padding: 12px; border: 1px dashed var(--border-strong); border-radius: var(--radius); font-size: var(--fs-xs); }
.diagnostics-title { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; color: var(--text-3); }
.diagnostics dl { display: grid; grid-template-columns: 64px 1fr; gap: 6px 10px; margin: 0; color: var(--text-2); }
.diagnostics dt { color: var(--text-3); }
.diagnostics dd { margin: 0; }
</style>
