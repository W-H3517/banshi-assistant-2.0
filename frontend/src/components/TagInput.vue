<script setup lang="ts">
import { computed } from 'vue'

// 自由填写的标签：回车、逗号、顿号、分号或移开焦点都会保存；下方列出已有标签便于沿用同一说法。
const props = defineProps<{ id?: string; options: string[] }>()
const tags = defineModel<string[]>({ required: true })
const suggestions = computed(() => props.options.filter(tag => !tags.value.includes(tag)).slice(0, 12))
</script>

<template>
  <div class="tag-input">
    <el-input-tag :id="id" v-model="tags" :max="8" :delimiter="/[,，、;；]/" save-on-blur placeholder="例如：采购要求（输入后按回车，可填多个）" />
    <div v-if="suggestions.length" class="tag-suggestions"><span>已有标签</span><button v-for="tag in suggestions" :key="tag" type="button" :disabled="tags.length >= 8" @click="tags = [...tags, tag]">{{ tag }}</button></div>
  </div>
</template>

<style scoped>
.tag-input { display: grid; gap: 8px; min-width: 0; }
.tag-suggestions { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; font-size: var(--fs-xs); color: var(--text-2); }
.tag-suggestions button { border: 1px solid var(--exp-line); background: var(--exp-soft); border-radius: var(--radius-s); padding: 3px 9px; font-size: var(--fs-xs); color: var(--exp); overflow-wrap: anywhere; }
.tag-suggestions button:hover:not(:disabled) { border-color: var(--exp); }
</style>
