<script setup lang="ts">
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
const props = defineProps<{ text: string }>()
const emit = defineEmits<{ document: [id: string] }>()
const md = new MarkdownIt({ html: false, linkify: false, breaks: true })
const html = computed(() => md.render(props.text))
function click(event: MouseEvent) {
  const anchor = (event.target as HTMLElement).closest('a')
  if (!anchor) return
  const href = anchor.getAttribute('href') || ''
  if (href.startsWith('/api/documents/')) {
    event.preventDefault()
    emit('document', href.slice('/api/documents/'.length).split('#')[0]!)
  } else { anchor.target = '_blank'; anchor.rel = 'noopener noreferrer' }
}
</script>
<template><div class="markdown" @click="click" v-html="html"></div></template>
