<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from './api'
import TagInput from './TagInput.vue'
import type { OwnExperience } from './experience'

// editing 为空时新建；否则修改本人的草稿。
const props = defineProps<{ editing?: OwnExperience | null; tagOptions: string[] }>()
const emit = defineEmits<{ saved: [experience: OwnExperience] }>()
const text = ref('')
const tags = ref<string[]>([])
const saving = ref<'draft' | 'submit' | null>(null)
const ready = computed(() => text.value.trim() !== '' && tags.value.some(tag => tag.trim()))

watch(() => props.editing, value => { text.value = value?.text || ''; tags.value = [...(value?.tags || [])] }, { immediate: true })

async function save(submit: boolean) {
  saving.value = submit ? 'submit' : 'draft'
  try {
    const body = JSON.stringify({ text: text.value, tags: tags.value, submit })
    const saved = props.editing
      ? await api<OwnExperience>(`/experiences/${props.editing.id}`, { method: 'PUT', body })
      : await api<OwnExperience>('/experiences', { method: 'POST', body })
    ElMessage.success(submit ? '已提交，等待管理员审核' : '草稿已保存，仅自己可见')
    if (!props.editing) { text.value = ''; tags.value = [] }
    emit('saved', saved)
  } catch (error) { ElMessage.error(error instanceof Error ? error.message : '保存失败') }
  finally { saving.value = null }
}
</script>

<template>
  <form class="experience-form" @submit.prevent>
    <label for="experience-text">经验内容</label>
    <el-input id="experience-text" v-model="text" type="textarea" :autosize="{ minRows: 6, maxRows: 16 }" maxlength="5000" show-word-limit
      placeholder="写下你办事时踩过的坑或摸索出的做法，一句话也可以。例如：采购打印纸要先找行政要比价单。" />
    <label for="experience-tags">标签 <small>必填，可多个；用你自己的话描述这条经验是关于什么的</small></label>
    <TagInput id="experience-tags" v-model="tags" :options="tagOptions" />
    <div class="experience-form-actions">
      <span>提交后由管理员审核，发布时署名显示为“某同事”。</span>
      <el-button :disabled="!ready || saving !== null" :loading="saving === 'draft'" @click="save(false)">存为草稿</el-button>
      <el-button type="primary" :disabled="!ready || saving !== null" :loading="saving === 'submit'" @click="save(true)">提交审核</el-button>
    </div>
  </form>
</template>

<style scoped>
.experience-form{display:grid;gap:10px;max-width:760px}.experience-form label{font-size:13px;font-weight:600;margin-top:10px}.experience-form label small{font-weight:400;color:#7c8983;margin-left:8px}.experience-form-actions{display:flex;align-items:center;gap:10px;margin-top:14px;flex-wrap:wrap}.experience-form-actions span{margin-right:auto;font-size:12px;color:#7c8983}.experience-form-actions .el-button{margin-left:0}
</style>
