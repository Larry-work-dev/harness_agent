<script setup>
import { ref, nextTick, onMounted } from 'vue'
import { API, token, setToken, api, esc, renderCitations, sourceListHTML } from '../api.js'
import MemoryPanel from './MemoryPanel.vue'

const workspaces = ref([])
const workspaceId = ref(null)
const conversations = ref([])
const conversationId = ref(null)
const items = ref([])          // 對話串流：user / trace / agent / routing
const input = ref('')
const sending = ref(false)
const showMemory = ref(false)
const toastMsg = ref('')
const streamEl = ref(null)
let toastTimer

// 模型選擇
const models = ref({ profiles: [], gateway: [], custom: [] })
const selectedModel = ref('auto')
const showAddModel = ref(false)
const cm = ref({ name: '', base_url: '', model: '', api_key: '' })

onMounted(async () => { await loadWorkspaces(); await loadModels() })

async function loadModels() {
  try { models.value = await api('/models') } catch { /* gateway 不可用時忽略 */ }
}
async function addCustomModel() {
  if (!cm.value.name || !cm.value.base_url || !cm.value.model) { toast('請填名稱 / base_url / 模型'); return }
  await api('/models/custom', { method: 'POST', body: cm.value })
  cm.value = { name: '', base_url: '', model: '', api_key: '' }
  showAddModel.value = false
  await loadModels()
  toast('已新增自訂模型')
}

function toast(msg) {
  toastMsg.value = msg
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => (toastMsg.value = ''), 3500)
}
function scrollDown() { nextTick(() => { if (streamEl.value) streamEl.value.scrollTop = streamEl.value.scrollHeight }) }

async function loadWorkspaces() {
  workspaces.value = await api('/workspaces')
  workspaceId.value = workspaces.value.length ? workspaces.value[0].id : null
  await loadConversations()
}
async function onWorkspaceChange() {
  conversationId.value = null; items.value = []
  await loadConversations()
}
async function newWorkspace() {
  const name = prompt('新 workspace 名稱：'); if (!name) return
  await api('/workspaces', { method: 'POST', body: { name } })
  await loadWorkspaces()
}
async function invite() {
  const username = prompt('邀請哪位使用者（輸入其帳號）：'); if (!username) return
  try { await api(`/workspaces/${workspaceId.value}/members`, { method: 'POST', body: { username } }); toast('已加入成員') }
  catch (e) { toast('邀請失敗：' + e.message) }
}

async function loadConversations() {
  if (!workspaceId.value) { conversations.value = []; return }
  conversations.value = await api(`/workspaces/${workspaceId.value}/conversations`)
}
async function newConversation() {
  const cv = await api(`/workspaces/${workspaceId.value}/conversations`, { method: 'POST', body: {} })
  conversationId.value = cv.id; items.value = []; await loadConversations()
}
async function selectConversation(cid) {
  conversationId.value = cid; items.value = []
  const msgs = await api(`/conversations/${cid}/messages`)
  for (const m of msgs) {
    if (m.role === 'user') items.value.push({ kind: 'user', text: m.content, time: m.created_at })
    else {
      const sources = {}; (m.sources || []).forEach(s => (sources[s.n] = s))
      items.value.push({ kind: 'agent', html: renderCitations(m.content, sources) + sourceListHTML(sources), text: m.content, time: m.created_at })
    }
  }
  scrollDown()
}
async function delConversation(cid) {
  if (!confirm('刪除這個對話？')) return
  await api(`/conversations/${cid}`, { method: 'DELETE' })
  if (cid === conversationId.value) { conversationId.value = null; items.value = [] }
  await loadConversations()
}

async function send() {
  const text = input.value.trim(); if (!text || sending.value) return
  if (!conversationId.value) {
    const cv = await api(`/workspaces/${workspaceId.value}/conversations`, { method: 'POST', body: {} })
    conversationId.value = cv.id
  }
  input.value = ''; sending.value = true
  items.value.push({ kind: 'user', text, time: new Date().toISOString() })
  scrollDown()

  const pending = {}, sources = {}
  try {
    const res = await fetch(API + '/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token.value },
      body: JSON.stringify({ conversation_id: conversationId.value, message: text, model: selectedModel.value }),
    })
    const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = ''
    while (true) {
      const { done, value } = await reader.read(); if (done) break
      buf += dec.decode(value, { stream: true })
      const parts = buf.split('\n\n'); buf = parts.pop()
      for (const p of parts) {
        const line = p.replace(/^data: /, '').trim(); if (!line) continue
        const ev = JSON.parse(line)
        if (ev.type === 'routing') {
          items.value.push({ kind: 'routing', text: `${ev.reason}（${ev.mode === 'workflow' ? '流程' : '模型'}：${ev.model}）` })
        } else if (ev.type === 'skill_call') {
          const t = { kind: 'trace', skill: ev.skill, args: JSON.stringify(ev.args), result: '…' }
          items.value.push(t); (pending[ev.skill] = pending[ev.skill] || []).push(t)
        } else if (ev.type === 'skill_result') {
          const t = (pending[ev.skill] || []).shift(); if (t) t.result = ev.result
          if (ev.sources) ev.sources.forEach(s => (sources[s.n] = s))
        } else if (ev.type === 'final') {
          items.value.push({ kind: 'agent', html: renderCitations(ev.content, sources) + sourceListHTML(sources), text: ev.content, time: new Date().toISOString() })
        } else if (ev.type === 'memory_saved') {
          toast('🧠 已記住：' + ev.items.join('、'))
        } else if (ev.type === 'error') {
          items.value.push({ kind: 'agent', html: `<span style="color:var(--danger)">錯誤：${esc(ev.message)}</span>` })
        }
        scrollDown()
      }
    }
    await loadConversations()   // 更新標題/排序
  } catch (e) {
    items.value.push({ kind: 'agent', html: `<span style="color:var(--danger)">連線失敗：${esc(e.message)}</span>` })
  } finally {
    sending.value = false
  }
}

function onEnter(e) { if (!e.shiftKey) { e.preventDefault(); send() } }
function logout() { setToken(null) }

function formatTime(t) {
  if (!t) return ''
  const d = new Date(t), now = new Date()
  const hm = d.toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit' })
  if (d.toDateString() === now.toDateString()) return hm
  return d.toLocaleDateString('zh-TW', { month: 'numeric', day: 'numeric' }) + ' ' + hm
}

const copied = ref(null)          // 剛複製的 item index，用來短暫顯示「已複製」
async function copy(text, i) {
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    // clipboard API 不可用（如非 https）時的後備做法
    const ta = document.createElement('textarea')
    ta.value = text; document.body.appendChild(ta); ta.select()
    document.execCommand('copy'); ta.remove()
  }
  copied.value = i
  setTimeout(() => { if (copied.value === i) copied.value = null }, 1500)
}
</script>

<template>
  <div class="shell">
    <div class="topbar">
      <div class="brand"><span class="dot"></span>Agent Harness</div>
      <div class="ws">
        <select v-model="workspaceId" @change="onWorkspaceChange">
          <option v-for="w in workspaces" :key="w.id" :value="w.id">{{ w.name }}（{{ w.role }}）</option>
        </select>
        <button class="icon" title="新增 workspace" @click="newWorkspace">＋</button>
        <button class="icon" title="邀請成員" @click="invite">邀請</button>
      </div>
      <div class="spacer"></div>
      <div class="model-pick">
        <select v-model="selectedModel" class="model-sel" title="生成模型（自動路由或手動指定）">
          <option value="auto">🧭 自動路由</option>
          <optgroup label="分級 profile">
            <option v-for="p in models.profiles" :key="p" :value="'profile:' + p">{{ p }}</option>
          </optgroup>
          <optgroup label="Gateway 模型" v-if="models.gateway.length">
            <option v-for="g in models.gateway" :key="g" :value="'gateway:' + g">{{ g }}</option>
          </optgroup>
          <optgroup label="自訂" v-if="models.custom.length">
            <option v-for="c in models.custom" :key="c.id" :value="'custom:' + c.id">{{ c.name }}</option>
          </optgroup>
        </select>
        <button class="icon" title="新增自訂模型" @click="showAddModel = true">＋模型</button>
      </div>
      <button class="icon" @click="showMemory = true">🧠 記憶</button>
      <button class="icon" @click="logout">登出</button>
    </div>

    <div class="body">
      <aside>
        <button class="new-conv" @click="newConversation">＋ 新對話</button>
        <div class="conv-list">
          <div v-for="cv in conversations" :key="cv.id"
               class="conv-item" :class="{ active: cv.id === conversationId }"
               @click="selectConversation(cv.id)">
            <div class="conv-main">
              <span class="title">{{ cv.title }}</span>
              <span class="conv-time">{{ formatTime(cv.updated_at) }}</span>
            </div>
            <button class="del" title="刪除" @click.stop="delConversation(cv.id)">×</button>
          </div>
        </div>
      </aside>

      <main>
        <div class="stream" ref="streamEl">
          <div v-if="!items.length" class="empty">
            <h2>開始對話</h2>
            <p>從左側新建對話，或直接在下方輸入。skill 呼叫與來源會即時顯示在對話中。</p>
          </div>

          <template v-for="(it, i) in items" :key="i">
            <div v-if="it.kind === 'user'" class="row user">
              <div class="avatar user">你</div>
              <div class="col">
                <div class="bubble">{{ it.text }}</div>
                <div class="time">{{ formatTime(it.time) }}</div>
              </div>
            </div>
            <div v-else-if="it.kind === 'agent'" class="row agent">
              <div class="avatar agent">AI</div>
              <div class="col">
                <div class="bubble" v-html="it.html"></div>
                <div class="meta">
                  <span class="time" v-if="it.time">{{ formatTime(it.time) }}</span>
                  <button class="copy" @click="copy(it.text, i)">
                    {{ copied === i ? '已複製' : '複製' }}
                  </button>
                </div>
              </div>
            </div>
            <div v-else-if="it.kind === 'routing'" class="routing">🧭 {{ it.text }}</div>
            <div v-else class="trace">
              <div><span class="call">{{ it.skill }}</span><span class="args">({{ it.args }})</span></div>
              <div class="result">{{ it.result }}</div>
            </div>
          </template>
        </div>

        <div class="composer">
          <div class="box">
            <textarea v-model="input" rows="1" placeholder="輸入訊息，Enter 送出、Shift+Enter 換行"
                      @keydown.enter="onEnter"></textarea>
            <button class="send" :disabled="sending" @click="send">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>
            </button>
          </div>
        </div>
      </main>
    </div>

    <MemoryPanel v-if="showMemory" @close="showMemory = false" />

    <template v-if="showAddModel">
      <div class="overlay" @click="showAddModel = false"></div>
      <div class="modal">
        <h3>新增自訂模型 <button class="close" @click="showAddModel = false">×</button></h3>
        <div class="mfield"><label>顯示名稱</label><input v-model="cm.name" placeholder="例如 我的 Ollama" /></div>
        <div class="mfield"><label>base_url</label><input v-model="cm.base_url" placeholder="http://localhost:11434/v1" /></div>
        <div class="mfield"><label>模型名稱</label><input v-model="cm.model" placeholder="qwen2.5" /></div>
        <div class="mfield"><label>API 金鑰（選填）</label><input v-model="cm.api_key" type="password" /></div>
        <button class="primary" @click="addCustomModel">新增</button>
      </div>
    </template>

    <div v-if="toastMsg" class="toast">{{ toastMsg }}</div>
  </div>
</template>

<style scoped>
.shell { height: 100vh; display: grid; grid-template-rows: auto 1fr; }
.topbar { display: flex; align-items: center; gap: 14px; padding: 11px 18px; border-bottom: 1px solid var(--border); background: var(--surface); }
.brand { font-family: var(--font-display); font-weight: 700; font-size: 16px; display: flex; align-items: center; gap: 8px; }
.brand .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--success); }
.ws { display: flex; align-items: center; gap: 6px; margin-left: 8px; }
.ws select { background: var(--surface-2); color: var(--text); border: 1px solid var(--border); border-radius: 8px; padding: 6px 10px; font-family: inherit; font-size: 13.5px; }
.icon { background: var(--surface-2); border: 1px solid var(--border); color: var(--muted); border-radius: 8px; padding: 6px 11px; font-size: 13px; }
.icon:hover { color: var(--text); border-color: var(--agent); }
.spacer { flex: 1; }
.model-pick { display: flex; align-items: center; gap: 6px; }
.model-sel { background: var(--surface-2); color: var(--text); border: 1px solid var(--border); border-radius: 8px; padding: 6px 10px; font-family: inherit; font-size: 13px; max-width: 180px; }
.routing { align-self: center; font-size: 11.5px; color: var(--muted); font-family: var(--font-mono); background: var(--surface-2); border: 1px solid var(--border); border-radius: 20px; padding: 3px 12px; }
.modal { position: fixed; top: 50%; left: 50%; transform: translate(-50%,-50%); width: 380px; max-width: 92vw; background: var(--surface); border: 1px solid var(--border); border-radius: 14px; z-index: 50; padding: 22px; }
.modal h3 { font-family: var(--font-display); font-size: 16px; display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }
.modal .close { background: none; border: none; color: var(--muted); font-size: 20px; }
.mfield { margin-bottom: 11px; }
.mfield label { display: block; font-size: 12px; color: var(--muted); margin-bottom: 5px; }
.mfield input { width: 100%; background: var(--surface-2); border: 1px solid var(--border); color: var(--text); border-radius: 9px; padding: 9px 11px; font-family: inherit; font-size: 13.5px; }
.mfield input:focus { outline: none; border-color: var(--agent); }
.modal .primary { width: 100%; background: var(--agent); color: #0A0E14; border: none; border-radius: 9px; padding: 10px; font-weight: 600; margin-top: 6px; }

.body { display: grid; grid-template-columns: 260px 1fr; overflow: hidden; }
aside { background: var(--surface); border-right: 1px solid var(--border); display: flex; flex-direction: column; overflow: hidden; }
.new-conv { margin: 14px; padding: 10px; background: var(--surface-2); border: 1px dashed var(--border); border-radius: 10px; color: var(--text); font-weight: 500; font-size: 14px; }
.new-conv:hover { border-color: var(--agent); }
.conv-list { flex: 1; overflow-y: auto; padding: 0 10px 14px; }
.conv-item { display: flex; align-items: center; gap: 8px; padding: 9px 11px; border-radius: 9px; color: var(--muted); font-size: 13.5px; margin-bottom: 3px; cursor: pointer; }
.conv-item:hover, .conv-item.active { background: var(--surface-2); color: var(--text); }
.conv-main { flex: 1; min-width: 0; }
.conv-item .title { display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.conv-time { font-size: 11px; color: var(--muted); font-family: var(--font-mono); }
.conv-item .del { opacity: 0; color: var(--muted); font-size: 15px; padding: 0 4px; background: none; border: none; }
.conv-item:hover .del { opacity: .7; }
.conv-item .del:hover { color: var(--danger); opacity: 1; }

main { display: flex; flex-direction: column; overflow: hidden; }
.stream { flex: 1; overflow-y: auto; padding: 26px; display: flex; flex-direction: column; gap: 18px; scroll-behavior: smooth; }
.empty { margin: auto; text-align: center; color: var(--muted); max-width: 400px; }
.empty h2 { font-family: var(--font-display); color: var(--text); font-size: 20px; margin-bottom: 8px; }

.row { display: flex; gap: 13px; max-width: 780px; }
.row.user { align-self: flex-end; flex-direction: row-reverse; }
.col { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.row.user .col { align-items: flex-end; }
.time { font-size: 11px; color: var(--muted); font-family: var(--font-mono); padding: 0 2px; }
.meta { display: flex; align-items: center; gap: 10px; }
.copy { background: none; border: none; color: var(--muted); font-size: 11.5px; font-family: var(--font-mono); padding: 0; opacity: 0; transition: opacity .15s, color .15s; }
.row.agent:hover .copy { opacity: 1; }
.copy:hover { color: var(--agent); }
.avatar { flex-shrink: 0; width: 30px; height: 30px; border-radius: 8px; display: grid; place-items: center; font-family: var(--font-mono); font-size: 12px; font-weight: 600; }
.avatar.agent { background: color-mix(in srgb, var(--agent) 18%, var(--surface)); color: var(--agent); }
.avatar.user { background: var(--surface-2); color: var(--muted); }
.bubble { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 12px 15px; white-space: pre-wrap; word-break: break-word; }
.row.user .bubble { background: var(--surface-2); border-color: transparent; }

.trace { font-family: var(--font-mono); font-size: 12.5px; background: color-mix(in srgb, var(--skill) 7%, var(--surface)); border: 1px solid color-mix(in srgb, var(--skill) 28%, var(--border)); border-left: 2.5px solid var(--skill); border-radius: 8px; padding: 9px 13px; max-width: 780px; align-self: flex-start; margin-left: 43px; }
.trace .call { color: var(--skill); }
.trace .args { color: var(--muted); }
.trace .result { color: var(--text); margin-top: 5px; padding-top: 5px; border-top: 1px dashed var(--border); }
.trace .result::before { content: "→ "; color: var(--success); }

.composer { border-top: 1px solid var(--border); padding: 16px 26px 20px; }
.box { max-width: 806px; display: flex; gap: 10px; align-items: flex-end; background: var(--surface); border: 1px solid var(--border); border-radius: 14px; padding: 8px 8px 8px 16px; margin: 0 auto; }
.box:focus-within { border-color: var(--agent); }
textarea { flex: 1; background: none; border: none; color: var(--text); font-family: inherit; font-size: 15px; resize: none; max-height: 160px; line-height: 1.5; padding: 6px 0; }
textarea:focus { outline: none; }
textarea::placeholder { color: var(--muted); }
.send { flex-shrink: 0; background: var(--agent); color: #0A0E14; border: none; border-radius: 9px; width: 38px; height: 38px; display: grid; place-items: center; }
.send:hover { opacity: .88; }
.send:disabled { opacity: .4; }

.toast { position: fixed; bottom: 22px; left: 50%; transform: translateX(-50%); background: var(--surface-2); border: 1px solid color-mix(in srgb, var(--success) 40%, var(--border)); color: var(--text); font-size: 13px; padding: 10px 16px; border-radius: 10px; z-index: 60; }

@media (max-width: 720px) { .body { grid-template-columns: 1fr; } aside { display: none; } .trace { margin-left: 0; } }
</style>
