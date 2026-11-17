<script setup lang="ts">
import { useRouter } from 'vue-router'
import ExperienceForm from '../components/ExperienceForm.vue'
import { useExperienceStore } from '../stores/experiences'

const router = useRouter()
const experiences = useExperienceStore()
</script>

<template>
  <section class="record-page">
    <div class="record-card">
      <p class="intro">办事时踩过的坑、摸索出的做法，随手记下来；一句话也可以，之后还能在“我的记录”里补充草稿。</p>
      <ExperienceForm :tag-options="experiences.tags" @saved="router.push({ name: 'mine' })" />
    </div>
  </section>
</template>

<style scoped>
.record-page { flex: 1; min-height: 0; overflow: auto; padding: clamp(16px, 3vw, 36px); background: var(--bg); }
.record-card { max-width: 760px; margin: 0 auto; padding: clamp(16px, 3vw, 28px); background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-l); box-shadow: var(--shadow); }
.intro { margin: 0 0 18px; color: var(--text-2); font-size: var(--fs); line-height: 1.7; }
</style>
