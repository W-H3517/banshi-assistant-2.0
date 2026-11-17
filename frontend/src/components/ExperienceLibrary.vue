<script setup lang="ts">
/** 知识库的经验页签：左侧已发布经验列表（关键词和标签筛选），右侧经验全文。 */
import { onMounted, ref, watch } from 'vue'
import { Search } from '@element-plus/icons-vue'
import { api } from '../api'
import { day, type PublishedExperience } from '../experience'
import { guard } from '../feedback'

const props = defineProps<{
  /** 当前打开的经验；为空时自动打开列表里的第一条。 */
  id?: string
}>()
const emit = defineEmits<{ select: [id?: string]; tags: [tags: string[]] }>()
const items = ref<PublishedExperience[]>([])
const tags = ref<string[]>([])
const query = ref('')
const tag = ref('')
const loading = ref(true)
const opened = ref<PublishedExperience | null>(null)

async function load() {
  loading.value = true
  await guard(async () => {
    const params = new URLSearchParams({ q: query.value, tag: tag.value })
    const result = await api<{ items: PublishedExperience[]; tags: string[] }>('/experiences?' + params)
    items.value = result.items; tags.value = result.tags; emit('tags', result.tags)
  })
  loading.value = false
}
function pick(value: string) { tag.value = tag.value === value ? '' : value; void load() }

// 打开的经验优先取自列表；从链接直接进入且不在当前筛选结果里时单独读取。
watch(() => [props.id, items.value, loading.value] as const, async ([id, list]) => {
  if (!id) {
    opened.value = null
    if (list.length) emit('select', list[0]!.id)
    return
  }
  const found = list.find(item => item.id === id)
  if (found) { opened.value = found; return }
  if (loading.value || opened.value?.id === id) return
  opened.value = null
  await guard(async () => { opened.value = await api<PublishedExperience>(`/experiences/${id}/published`) })
})
onMounted(load)
</script>

<template>
  <div class="split">
    <aside class="list-pane">
      <div class="list-head">
        <slot name="tabs" />
        <div class="row">
          <el-input v-model="query" placeholder="输入关键词搜索经验" maxlength="100" :prefix-icon="Search" clearable @keyup.enter="load" @clear="load" />
          <el-button @click="load">搜索</el-button>
        </div>
        <div v-if="tags.length" class="chips">
          <button v-for="name in tags" :key="name" :class="{ on: tag === name }" @click="pick(name)">{{ name }}</button>
        </div>
      </div>
      <div v-loading="loading" class="list">
        <button v-for="item in items" :key="item.id" class="list-item" :class="{ on: item.id === id }" @click="emit('select', item.id)">
          <div class="item-title">
            <span>{{ item.tags.join(' · ') }}</span>
            <i v-if="item.conflict" class="kind-tag warn">与制度不同</i>
          </div>
          <div class="item-meta">{{ item.author }} 分享 · {{ day(item.published_at) }} 发布</div>
          <div class="item-text">{{ item.content }}</div>
        </button>
        <el-empty v-if="!loading && !items.length" :image-size="80" :description="query || tag ? '没有找到匹配的经验，试试其他关键词或标签' : '还没有已发布的经验'" />
      </div>
      <div class="list-foot">经验来自同事的实际办理经历，经管理员审核后发布，是实践参考；标有“与制度不同”的，请留意说明并自行核对。</div>
    </aside>

    <section class="pane detail">
      <article v-if="opened">
        <span class="kind-tag experience">经验</span>
        <h2>{{ opened.tags.join(' · ') }}</h2>
        <p class="by">{{ opened.author }} 分享 · 经管理员审核 · {{ day(opened.published_at) }} 发布</p>
        <el-alert
          v-if="opened.conflict" type="warning" :closable="false" show-icon title="这条经验与制度的规定不同"
          :description="opened.conflict_note || '管理员已标记，请对照制度原文。'"
        />
        <p class="content">{{ opened.content }}</p>
      </article>
      <div v-else-if="!loading" class="pane-empty"><el-empty :description="id ? '这条经验不存在或已下架' : '从左侧选择一条经验'" /></div>
    </section>
  </div>
</template>

<style scoped>
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chips button { height: 26px; padding: 0 10px; border: 1px solid var(--border); border-radius: 13px; background: var(--surface); font-size: var(--fs-xs); color: var(--text-2); }
.chips button.on { background: var(--exp-soft); border-color: var(--exp-line); color: var(--exp); }
.item-title i { margin-left: auto; font-style: normal; }
.detail { overflow: auto; background: var(--surface); }
.detail article { max-width: 760px; padding: 28px 36px; }
.detail h2 { margin: 10px 0 6px; font-size: var(--fs-xl); font-weight: 600; }
.by { margin: 0 0 18px; font-size: var(--fs-s); color: var(--text-3); }
.content { margin: 18px 0 0; font-size: 15px; line-height: 2; white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
