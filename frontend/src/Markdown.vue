<script setup lang="ts">
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
const props = defineProps<{ text: string, availableDocumentIds: string[] }>()
const emit = defineEmits<{ document: [id: string, page: number] }>()
const md = new MarkdownIt({ html: false, linkify: false, breaks: true })
const html = computed(() => {
  const document = new DOMParser().parseFromString(md.render(props.text), 'text/html')
  for (const anchor of document.querySelectorAll('a')) {
    const source = anchor.getAttribute('href')?.match(/^(?:citation:)?(\/api\/documents\/([a-f0-9-]+)(?:#page=\d+)?)$/i)
    if (!source) continue
    // 兼容历史回答中模型添加的 citation: 前缀，统一使用站内原文链接。
    anchor.setAttribute('href', source[1]!)
    if (!props.availableDocumentIds.includes(source[2]!)) {
      const label = document.createElement('span')
      label.className = 'deleted-source'
      label.textContent = `${anchor.textContent}（资料已删除）`
      anchor.replaceWith(label)
    }
  }
  return document.body.innerHTML
})
function click(event: MouseEvent) {
  const anchor = (event.target as HTMLElement).closest('a')
  if (!anchor) return
  const href = anchor.getAttribute('href') || ''
  const source = href.match(/^\/api\/documents\/([a-f0-9-]+)(?:#page=(\d+))?$/i)
  if (source) {
    event.preventDefault()
    emit('document', source[1]!, Number(source[2] || 1))
  } else { anchor.target = '_blank'; anchor.rel = 'noopener noreferrer' }
}
</script>
<template><div class="markdown" @click="click" v-html="html"></div></template>
