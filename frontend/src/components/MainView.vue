<script setup>
import { ref, nextTick, onMounted } from 'vue'
import { API, token, setToken, api, esc, renderCitations, renderMarkdown, sourceListHTML, uploadFiles } from '../api.js'
import MemoryPanel from './MemoryPanel.vue'

const workspaces = ref([])
const workspaceId = ref(null)
const conversations = ref([])
const conversationId = ref(null)
const items = ref([])          // 對話串流：user / trace / agent / routing
const input = ref('')
const sending = ref(false)
const attachments = ref([])   // 已上傳待送出的附件
const uploading = ref(false)
const fileInput = ref(null)
const showMemory = ref(false)
const toastMsg = ref('')
const streamEl = ref(null)
let toastTimer

// 模型與對話模式
const models = ref({ profiles: [], gateway: [], gateway_error: null, custom: [] })
const convMode = ref('auto')          // 目前對話的模式 auto | manual
const convModelSel = ref('auto')      // manual 時選的模型字串
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
// 把模式/模型存回該對話（單一入口，避免讀到過時的 ref）
async function persistMode(mode, model) {
  convMode.value = mode
  convModelSel.value = model
  await api(`/conversations/${conversationId.value}/mode`, { method: 'POST', body: { mode, model } })
  const cv = conversations.value.find(c => c.id === conversationId.value)
  if (cv) { cv.mode = mode; cv.model = model }
}
function firstModelOption() {
  if (models.value.profiles.length) return 'profile:' + models.value.profiles[0]
  if (models.value.gateway.length) return 'gateway:' + models.value.gateway[0]
  if (models.value.custom.length) return 'custom:' + models.value.custom[0].id
  return 'profile:cloud'
}
// 切 auto / manual（toggle 按鈕用）
async function applyMode(mode) {
  if (!conversationId.value) return
  if (mode === 'auto') return persistMode('auto', 'auto')
  let model = convModelSel.value
  if (!model || model === 'auto') model = firstModelOption()   // 進手動時若還沒選模型就挑一個
  return persistMode('manual', model)
}
// 下拉選模型（值直接來自事件，不靠 v-model 時序）
async function applyModel(val) {
  if (!conversationId.value || !val) return
  return persistMode('manual', val)
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
async function delWorkspace() {
  if (!workspaceId.value) return
  if (!confirm('確定要刪除目前的 Workspace 嗎？這將會刪除裡面所有的對話且無法復原！')) return
  
  try {
    await api(`/workspaces/${workspaceId.value}`, { method: 'DELETE' })
    toast('Workspace 已刪除')
    // 刪除成功後重新載入列表，這會自動選取下一個可用的 Workspace 或清空畫面
    await loadWorkspaces() 
  } catch (e) {
    toast('刪除失敗：' + e.message)
  }
}
async function loadConversations() {
  if (!workspaceId.value) { conversations.value = []; return }
  conversations.value = await api(`/workspaces/${workspaceId.value}/conversations`)
}
async function newConversation() {
  const cv = await api(`/workspaces/${workspaceId.value}/conversations`, { method: 'POST', body: {} })
  conversationId.value = cv.id; items.value = []
  convMode.value = 'auto'; convModelSel.value = 'auto'
  await loadConversations()
}
async function selectConversation(cid) {
  conversationId.value = cid; items.value = []
  const cv = conversations.value.find(c => c.id === cid)
  convMode.value = cv?.mode || 'auto'
  convModelSel.value = cv?.model || 'auto'
  const msgs = await api(`/conversations/${cid}/messages`)
  for (const m of msgs) {
    if (m.role === 'user') items.value.push({ kind: 'user', text: m.content, time: m.created_at })
    else {
      const sources = {}; (m.sources || []).forEach(s => (sources[s.n] = s))
      items.value.push({ kind: 'agent', html: renderMarkdown(m.content, sources), text: m.content, time: m.created_at })
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

async function onFiles(e) {
  const files = Array.from(e.target.files || []); e.target.value = ''
  if (!files.length) return
  if (!conversationId.value) {
    const cv = await api(`/workspaces/${workspaceId.value}/conversations`, { method: 'POST', body: {} })
    conversationId.value = cv.id; await loadConversations()
  }
  uploading.value = true
  try {
    const r = await uploadFiles(files, conversationId.value)
    for (const a of r.attachments) attachments.value.push(a)
  } catch (err) { toast('上傳失敗：' + err.message) }
  finally { uploading.value = false }
}

async function send() {
  const text = input.value.trim(); if ((!text && !attachments.value.length) || sending.value) return
  if (!conversationId.value) {
    const cv = await api(`/workspaces/${workspaceId.value}/conversations`, { method: 'POST', body: {} })
    conversationId.value = cv.id
  }
  const atts = attachments.value.slice()
  input.value = ''; attachments.value = []; sending.value = true
  items.value.push({ kind: 'user', text, attachments: atts, time: new Date().toISOString() })
  scrollDown()

  const pending = {}, sources = {}
  try {
    const res = await fetch(API + '/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token.value },
      body: JSON.stringify({ conversation_id: conversationId.value, message: text, attachments: atts }),
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
          const actual = ev.actual_model ? ` → ${ev.actual_model}` : ''
          items.value.push({ kind: 'routing', text: `${ev.reason}（${ev.mode === 'workflow' ? '流程' : '模型'}：${ev.model}${actual}）` })
        } else if (ev.type === 'skill_call') {
          const t = { kind: 'trace', skill: ev.skill, args: JSON.stringify(ev.args), result: '…' }
          items.value.push(t); (pending[ev.skill] = pending[ev.skill] || []).push(t)
        } else if (ev.type === 'skill_result') {
          const t = (pending[ev.skill] || []).shift(); if (t) t.result = ev.result
          if (ev.sources) ev.sources.forEach(s => (sources[s.n] = s))
        } else if (ev.type === 'final') {
          items.value.push({ kind: 'agent', html: renderMarkdown(ev.content, sources), text: ev.content, time: new Date().toISOString() })
        } else if (ev.type === 'critic') {
          items.value.push({ kind: 'trace', skill: `critic:${ev.task_type}`, args: '',
            result: (ev.verdict === 'pass' ? '✅ 通過' : '🔁 重試') + '：' + ev.reason })
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

function shortModel(m) {
  if (!m || m === 'auto') return '自動'
  const [k, v] = m.split(':')
  if (k === 'custom') { const c = models.value.custom.find(x => String(x.id) === v); return c ? c.name : '自訂' }
  return v || m
}

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
        <button class="icon" title="刪除 workspace" @click="delWorkspace" v-if="workspaceId">刪除</button>
      </div>
      <div class="spacer"></div>
      <div class="mode-pick" v-if="conversationId">
        <div class="seg">
          <button :class="{ active: convMode === 'auto' }" @click="applyMode('auto')">🧭 自動</button>
          <button :class="{ active: convMode === 'manual' }" @click="applyMode('manual')">🔧 手動</button>
        </div>
        <select v-if="convMode === 'manual'" :value="convModelSel" class="model-sel"
                @change="applyModel($event.target.value)" title="這個對話使用的模型">
          <optgroup label="分級 profile">
            <option v-for="p in models.profiles" :key="p" :value="'profile:' + p">{{ p }}</option>
          </optgroup>
          <optgroup label="Gateway 模型" v-if="models.gateway.length || models.gateway_error">
            <option v-if="models.gateway_error" disabled>⚠ 連不到 gateway，請檢查憑證/連線</option>
            <option v-for="g in models.gateway" :key="g" :value="'gateway:' + g">{{ g }}</option>
          </optgroup>
          <optgroup label="自訂" v-if="models.custom.length">
            <option v-for="c in models.custom" :key="c.id" :value="'custom:' + c.id">{{ c.name }}</option>
          </optgroup>
        </select>
        <button v-if="convMode === 'manual'" class="icon" title="新增自訂模型" @click="showAddModel = true">＋模型</button>
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
              <span class="conv-meta">
                <span class="badge">{{ cv.mode === 'manual' ? '🔧 ' + shortModel(cv.model) : '🧭 自動' }}</span>
                <span class="conv-time">{{ formatTime(cv.updated_at) }}</span>
              </span>
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
                <div class="bubble" v-if="it.text">{{ it.text }}</div>
                <div v-if="it.attachments && it.attachments.length" class="msg-atts">
                  <span v-for="(a, ai) in it.attachments" :key="ai" class="msg-att">{{ a.kind === 'image' ? '🖼' : '📄' }} {{ a.name }}</span>
                </div>
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
          <div v-if="attachments.length || uploading" class="atts">
            <div v-for="(a, i) in attachments" :key="i" class="att">
              <span class="att-ic">{{ a.kind === 'image' ? '🖼' : '📄' }}</span>
              <span class="att-nm">{{ a.name }}</span>
              <button class="att-x" @click="attachments.splice(i, 1)">×</button>
            </div>
            <span v-if="uploading" class="att uploading">上傳中…</span>
          </div>
          <div class="box">
            <button class="attach" title="附加圖片或文件" @click="fileInput.click()">📎</button>
            <input ref="fileInput" type="file" multiple accept="image/*,.pdf,.docx,.txt,.md" style="display:none" @change="onFiles" />
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
.mode-pick { display: flex; align-items: center; gap: 6px; }
.seg { display: flex; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
.seg button { background: var(--surface-2); border: none; color: var(--muted); padding: 6px 11px; font-size: 12.5px; }
.seg button.active { background: color-mix(in srgb, var(--agent) 20%, var(--surface-2)); color: var(--text); }
.model-sel { background: var(--surface-2); color: var(--text); border: 1px solid var(--border); border-radius: 8px; padding: 6px 10px; font-family: inherit; font-size: 13px; max-width: 170px; }
.conv-meta { display: flex; align-items: center; justify-content: space-between; gap: 6px; margin-top: 2px; }
.badge { font-size: 10.5px; color: var(--muted); font-family: var(--font-mono); background: var(--bg); border: 1px solid var(--border); border-radius: 6px; padding: 1px 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 130px; }
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

/* AI 泡泡內的 markdown 排版 */
.bubble :first-child { margin-top: 0; }
.bubble :last-child { margin-bottom: 0; }
.bubble h1, .bubble h2, .bubble h3 { font-family: var(--font-display); line-height: 1.3; margin: 14px 0 8px; }
.bubble h1 { font-size: 1.3em; } .bubble h2 { font-size: 1.18em; } .bubble h3 { font-size: 1.06em; }
.bubble p { margin: 8px 0; }
.bubble ul, .bubble ol { margin: 8px 0; padding-left: 1.4em; }
.bubble li { margin: 3px 0; }
.bubble code { background: var(--surface-2); border: 1px solid var(--border); border-radius: 5px; padding: 1px 5px; font-family: var(--font-mono); font-size: .88em; }
.bubble pre { background: #0B0E14; border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; overflow-x: auto; margin: 10px 0; }
.bubble pre code { background: none; border: none; padding: 0; font-size: .86em; line-height: 1.5; }
.bubble blockquote { border-left: 3px solid var(--border); margin: 10px 0; padding: 2px 0 2px 12px; color: var(--muted); }
.bubble a { color: var(--agent); text-decoration: none; }
.bubble a:hover { text-decoration: underline; }
.bubble table { border-collapse: collapse; margin: 10px 0; font-size: .92em; }
.bubble th, .bubble td { border: 1px solid var(--border); padding: 5px 10px; }
.bubble hr { border: none; border-top: 1px solid var(--border); margin: 14px 0; }

.trace { font-family: var(--font-mono); font-size: 12.5px; background: color-mix(in srgb, var(--skill) 7%, var(--surface)); border: 1px solid color-mix(in srgb, var(--skill) 28%, var(--border)); border-left: 2.5px solid var(--skill); border-radius: 8px; padding: 9px 13px; max-width: 780px; align-self: flex-start; margin-left: 43px; }
.trace .call { color: var(--skill); }
.trace .args { color: var(--muted); }
.trace .result { color: var(--text); margin-top: 5px; padding-top: 5px; border-top: 1px dashed var(--border); }
.trace .result::before { content: "→ "; color: var(--success); }

.composer { border-top: 1px solid var(--border); padding: 16px 26px 20px; }
.atts { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }
.att { display: flex; align-items: center; gap: 6px; background: var(--surface-2); border: 1px solid var(--border); border-radius: 8px; padding: 5px 9px; font-size: 12.5px; }
.att-ic { font-size: 13px; }
.att-nm { max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.att-x { background: none; border: none; color: var(--muted); font-size: 15px; line-height: 1; }
.att-x:hover { color: var(--danger); }
.att.uploading { color: var(--muted); }
.attach { background: none; border: none; color: var(--muted); font-size: 18px; padding: 0 4px; align-self: flex-end; }
.attach:hover { color: var(--agent); }
.msg-atts { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.msg-att { background: var(--surface-2); border: 1px solid var(--border); border-radius: 7px; padding: 3px 8px; font-size: 12px; color: var(--muted); }
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
