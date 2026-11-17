<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { clearSession } from './session'

const router = useRouter()
const ready = ref(false)

function expire() {
  clearSession()
  void router.replace({ name: 'login' })
}
onMounted(async () => {
  window.addEventListener('session-expired', expire)
  await router.isReady()
  ready.value = true
})
onUnmounted(() => window.removeEventListener('session-expired', expire))
</script>

<template>
  <div v-if="!ready" class="loading-screen">正在打开办事助手…</div>
  <RouterView v-else />
</template>
