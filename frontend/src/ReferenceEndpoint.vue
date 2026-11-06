<script setup lang="ts">
export interface Endpoint { document_id: string; document_title: string; page_count: number; section_id: number | null; section_title: string; pages: number[]; content: string }
defineProps<{ item: Endpoint }>()
defineEmits<{ follow: [item: Endpoint]; source: [item: Endpoint, page: number] }>()
</script>
<template>
  <div class="reference-endpoint">
    <strong>{{ item.document_title }} · {{ item.section_title }}</strong>
    <div class="reference-actions"><el-button v-for="p in item.pages" :key="p" size="small" @click="$emit('source', item, p)">PDF 第 {{ p }} 页</el-button><el-button v-if="item.section_id" size="small" type="primary" plain @click="$emit('follow', item)">以此条款继续追查</el-button></div>
    <details><summary>查看依据全文</summary><p>{{ item.content }}</p></details>
  </div>
</template>
<style scoped>
.reference-endpoint{padding:12px 0;font-size:13px}.reference-actions{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}.reference-actions .el-button{margin:0}summary{cursor:pointer;color:#557b62}p{white-space:pre-wrap;line-height:1.9}
</style>
