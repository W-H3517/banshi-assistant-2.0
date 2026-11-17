<script setup lang="ts">
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'

const props = defineProps<{ text: string, availableDocumentIds: string[], availableExperienceIds: string[] }>()
const emit = defineEmits<{ document: [id: string, page: number], experience: [id: string] }>()
const md = new MarkdownIt({ html: false, linkify: false, breaks: true })
const experienceLink = /^\/api\/experiences\/([a-f0-9-]+)\/published$/i
// 模型有时会给引用加上 citation: 前缀，统一成站内原文链接。
const documentLink = /^(?:citation:)?(\/api\/documents\/([a-f0-9-]+)(?:#page=(\d+))?)$/i

/** 把引用换成带样式的标签；资料已删除或下架时换成不可点击的说明。 */
const html = computed(() => {
  const document = new DOMParser().parseFromString(md.render(props.text), 'text/html')
  for (const anchor of document.querySelectorAll('a')) {
    const href = anchor.getAttribute('href') || ''
    const experience = href.match(experienceLink)
    const source = href.match(documentLink)
    if (!experience && !source) continue
    const gone = experience
      ? !props.availableExperienceIds.includes(experience[1]!)
      : !props.availableDocumentIds.includes(source![2]!)
    if (gone) {
      const label = document.createElement('span')
      label.className = 'cite gone'
      label.textContent = `${anchor.textContent}${experience ? '（已下架）' : '（资料已删除）'}`
      anchor.replaceWith(label)
      continue
    }
    anchor.className = experience ? 'cite exp' : 'cite doc'
    if (source) anchor.setAttribute('href', source[1]!)
  }
  return document.body.innerHTML
})
function click(event: MouseEvent) {
  const anchor = (event.target as HTMLElement).closest('a')
  if (!anchor) return
  const href = anchor.getAttribute('href') || ''
  const experience = href.match(experienceLink)
  if (experience) { event.preventDefault(); emit('experience', experience[1]!); return }
  const source = href.match(documentLink)
  if (source) {
    event.preventDefault()
    emit('document', source[2]!, Number(source[3] || 1))
  } else { anchor.target = '_blank'; anchor.rel = 'noopener noreferrer' }
}
</script>

<template><div class="markdown" @click="click" v-html="html"></div></template>

<style scoped>
.markdown { font-size: var(--fs); line-height: 1.9; color: var(--text); overflow-wrap: anywhere; }
.markdown :deep(p) { margin: 0 0 10px; }
.markdown :deep(h1) { font-size: var(--fs-xl); margin: 18px 0 10px; }
.markdown :deep(h2) { font-size: 18px; margin: 18px 0 10px; }
.markdown :deep(h3) { font-size: var(--fs-l); margin: 14px 0 8px; }
.markdown :deep(ul), .markdown :deep(ol) { margin: 0 0 10px; padding-left: 22px; }
.markdown :deep(a) { color: var(--primary); text-decoration: underline; text-underline-offset: 3px; }
.markdown :deep(pre) { overflow: auto; padding: 14px; border-radius: var(--radius); background: var(--surface-2); border: 1px solid var(--border); }
.markdown :deep(table) { display: block; max-width: 100%; overflow: auto; border-collapse: collapse; margin: 6px 0 12px; font-size: var(--fs-s); }
.markdown :deep(th), .markdown :deep(td) { padding: 7px 14px; border: 1px solid var(--border); text-align: left; }
.markdown :deep(th) { background: var(--surface-2); font-weight: 600; }
.markdown :deep(blockquote) { margin: 10px 0; padding: 2px 14px; border-left: 3px solid var(--primary-line); color: var(--text-2); }
/* 引用标签：制度蓝色，经验琥珀色。 */
.markdown :deep(.cite) { display: inline; padding: 2px 7px; margin: 0 2px; border-radius: 4px; font-size: var(--fs-xs); text-decoration: none; -webkit-box-decoration-break: clone; box-decoration-break: clone; }
.markdown :deep(a.cite.doc) { background: var(--doc-soft); color: var(--doc); }
.markdown :deep(a.cite.exp) { background: var(--exp-soft); color: var(--exp); }
.markdown :deep(a.cite:hover) { box-shadow: 0 0 0 1px currentColor; }
.markdown :deep(.cite.gone) { background: var(--neutral-tag); color: var(--text-3); text-decoration: line-through; }
</style>
