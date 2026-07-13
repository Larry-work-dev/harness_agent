<script setup>
import { ref, onMounted } from 'vue'
import { api, esc } from '../api.js'

const emit = defineEmits(['close'])
const memories = ref([])

onMounted(load)
async function load() { memories.value = await api('/memories') }

async function del(id) {
  await api('/memories/' + id, { method: 'DELETE' })
  memories.value = memories.value.filter(m => m.id !== id)
}
</script>

<template>
  <div class="overlay" @click="emit('close')"></div>
  <div class="panel">
    <h3>你的長期記憶 <button class="close" @click="emit('close')">×</button></h3>
    <div class="desc">跨對話記住的偏好與事實（綁你的帳號）。可刪除不想保留的。</div>
    <div class="list">
      <div v-if="!memories.length" class="empty">
        目前還沒有長期記憶。多聊幾句，系統會自動記下你的偏好與穩定事實。
      </div>
      <div v-for="m in memories" :key="m.id" class="item">
        <span class="txt" v-html="esc(m.content)"></span>
        <button class="del" title="刪除" @click="del(m.id)">×</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.overlay { position: fixed; inset: 0; background: rgba(0,0,0,.5); z-index: 40; }
.panel { position: fixed; top: 0; right: 0; width: 360px; max-width: 90vw; height: 100vh; background: var(--surface); border-left: 1px solid var(--border); z-index: 50; display: flex; flex-direction: column; }
h3 { font-family: var(--font-display); font-size: 16px; padding: 18px 20px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; }
.close { background: none; border: none; color: var(--muted); font-size: 20px; }
.desc { color: var(--muted); font-size: 12.5px; padding: 12px 20px 4px; }
.list { flex: 1; overflow-y: auto; padding: 8px 16px 16px; }
.item { display: flex; gap: 10px; align-items: flex-start; background: var(--surface-2); border: 1px solid var(--border); border-radius: 10px; padding: 11px 13px; margin-bottom: 8px; font-size: 13.5px; }
.txt { flex: 1; }
.del { background: none; border: none; color: var(--muted); font-size: 15px; }
.del:hover { color: var(--danger); }
.empty { color: var(--muted); font-size: 13px; padding: 16px 20px; }
</style>
