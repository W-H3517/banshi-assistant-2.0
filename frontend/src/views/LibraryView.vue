<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import DocumentLibrary from '../components/DocumentLibrary.vue'
import ExperienceLibrary from '../components/ExperienceLibrary.vue'
import { useExperienceStore } from '../stores/experiences'
import { useLibraryStore } from '../stores/library'

type Tab = 'documents' | 'experiences'
const route = useRoute()
const router = useRouter()
const library = useLibraryStore()
const experiences = useExperienceStore()
const tab = computed(() => route.params.tab as Tab)
const id = computed(() => (route.params.id as string) || undefined)
const page = computed(() => Number(route.query.page) || undefined)

/** 打开某份制度或某条经验；地址随之变化，可刷新和分享。 */
function open(name: Tab, target?: string, targetPage?: number) {
  void router.replace({
    name: 'library', params: { tab: name, id: target },
    query: targetPage && targetPage > 1 ? { page: targetPage } : {},
  })
}
</script>

<template>
  <component
    :is="tab === 'documents' ? DocumentLibrary : ExperienceLibrary" :id="id" :page="tab === 'documents' ? page : undefined"
    @select="(target?: string, targetPage?: number) => open(tab, target, targetPage)" @tags="experiences.tags = $event"
  >
    <template #tabs>
      <div class="segmented" role="tablist" aria-label="资料类型">
        <button role="tab" :aria-selected="tab === 'documents'" :class="{ on: tab === 'documents' }" @click="open('documents')">
          制度 {{ library.availableIds.length }}
        </button>
        <button role="tab" :aria-selected="tab === 'experiences'" :class="{ on: tab === 'experiences' }" @click="open('experiences')">
          经验 {{ experiences.availableIds.length }}
        </button>
      </div>
    </template>
  </component>
</template>
