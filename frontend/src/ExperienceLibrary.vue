<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Search } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { api } from './api'
import { day, type PublishedExperience } from './experience'

const emit = defineEmits<{ tags: [tags: string[]] }>()
const items = ref<PublishedExperience[]>([])
const tags = ref<string[]>([])
const query = ref('')
const tag = ref('')
const loading = ref(true)
const opened = ref<PublishedExperience | null>(null)

async function load() {
  loading.value = true
  try {
    const params = new URLSearchParams({ q: query.value, tag: tag.value })
    const result = await api<{ items: PublishedExperience[]; tags: string[] }>('/experiences?' + params)
    items.value = result.items; tags.value = result.tags; emit('tags', result.tags)
  } catch (error) { ElMessage.error(error instanceof Error ? error.message : '加载失败') }
  finally { loading.value = false }
}
function pick(value: string) { tag.value = tag.value === value ? '' : value; void load() }
onMounted(load)
</script>

<template>
  <div>
    <div class="knowledge-toolbar"><el-input v-model="query" placeholder="输入关键词搜索经验" maxlength="100" :prefix-icon="Search" clearable @keyup.enter="load" @clear="load" /><el-button @click="load">搜索</el-button><span>{{ items.length }} 条经验</span></div>
    <div v-if="tags.length" class="tag-filter"><span>标签</span><button v-for="name in tags" :key="name" :class="{ chosen: tag === name }" @click="pick(name)">{{ name }}</button></div>
    <div v-loading="loading" class="experience-grid">
      <button v-for="item in items" :key="item.id" class="experience-card" @click="opened = item">
        <div class="experience-card-top"><span v-for="name in item.tags" :key="name">{{ name }}</span><em v-if="item.conflict">与制度不同</em></div>
        <p>{{ item.content }}</p>
        <div class="experience-card-foot"><span>{{ item.author }} 分享</span><span>{{ day(item.published_at) }}</span></div>
      </button>
    </div>
    <el-empty v-if="!loading && !items.length" :description="query || tag ? '没有找到匹配的经验，试试其他关键词或标签' : '还没有已发布的经验'" />
    <p class="knowledge-note">经验来自同事的实际办理经历，经管理员审核后发布，是实践参考；标有“与制度不同”的，请留意说明并自行核对。</p>
    <el-dialog :model-value="opened !== null" title="经验详情" width="min(720px, 94vw)" @close="opened = null">
      <template v-if="opened">
        <div class="experience-card-top"><span v-for="name in opened.tags" :key="name">{{ name }}</span></div>
        <el-alert v-if="opened.conflict" type="warning" :closable="false" show-icon title="这条经验与制度的规定不同" :description="opened.conflict_note || '管理员已标记，请对照制度原文。'" />
        <p class="experience-detail">{{ opened.content }}</p>
        <div class="experience-card-foot"><span>{{ opened.author }} 分享 · 经管理员审核</span><span>发布于 {{ day(opened.published_at) }}</span></div>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.tag-filter{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:0 0 22px;font-size:12px;color:#7c8983}.tag-filter button{border:1px solid #d4dfd6;background:white;border-radius:14px;padding:4px 12px;font-size:12px;color:#54705f}.tag-filter button.chosen{background:#286451;border-color:#286451;color:white}.experience-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px;min-height:60px}.experience-card{text-align:left;background:white;border:1px solid #e1e8e2;border-radius:10px;padding:18px 20px;display:flex;flex-direction:column;gap:12px;transition:border-color .15s,box-shadow .15s}.experience-card:hover{border-color:#b8d1c6;box-shadow:0 4px 14px #28645114}.experience-card-top{display:flex;gap:6px;flex-wrap:wrap;align-items:center}.experience-card-top span{font-size:12px;padding:2px 8px;background:#edf4f0;border-radius:4px;color:#3d6b57}.experience-card-top em{font-style:normal;font-size:12px;padding:2px 8px;background:#fbf1dc;border-radius:4px;color:#8a6417;margin-left:auto}.experience-card p{margin:0;font-size:14px;line-height:1.8;display:-webkit-box;-webkit-line-clamp:5;-webkit-box-orient:vertical;overflow:hidden;white-space:pre-wrap}.experience-card-foot{display:flex;justify-content:space-between;font-size:12px;color:#8c9a94;margin-top:auto}.experience-detail{white-space:pre-wrap;line-height:1.9;font-size:14px;margin:18px 0}.el-alert{margin-top:14px}
</style>
