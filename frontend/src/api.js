import { ref } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

marked.setOptions({ gfm: true, breaks: true })

// 同源部署（走 nginx 反代）用 ''；dev 由 vite proxy 代理
export const API = ''
const TOKEN_KEY = 'harness_token'
export const token = ref(localStorage.getItem(TOKEN_KEY) || null)

export function setToken(t) {
  token.value = t
  if (t) localStorage.setItem(TOKEN_KEY, t)
  else localStorage.removeItem(TOKEN_KEY)
}

export async function api(path, { method = 'GET', body = null } = {}) {
  const headers = { 'Content-Type': 'application/json' }
  if (token.value) headers.Authorization = 'Bearer ' + token.value
  const res = await fetch(API + path, { method, headers, body: body ? JSON.stringify(body) : null })
  if (res.status === 401) { setToken(null); throw new Error('未授權') }
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText)
  return res.status === 204 ? null : res.json()
}

export async function uploadFiles(files, conversationId) {
  const fd = new FormData()
  fd.append('conversation_id', conversationId)
  for (const f of files) fd.append('files', f)
  const headers = {}
  if (token.value) headers.Authorization = 'Bearer ' + token.value
  const res = await fetch(API + '/uploads', { method: 'POST', headers, body: fd })
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText)
  return res.json()
}

// 下載對話裡先前上傳過的附件（a.path 形如 "{conversation_id}/{uuid}__{filename}"）
export async function downloadAttachment(a) {
  const headers = {}
  if (token.value) headers.Authorization = 'Bearer ' + token.value
  const res = await fetch(API + '/attachments/' + a.path, { headers })
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url; link.download = a.name
  document.body.appendChild(link); link.click(); link.remove()
  URL.revokeObjectURL(url)
}

const escMap = { '&': '&amp;', '<': '&lt;', '>': '&gt;' }
export const esc = s => (s ?? '').replace(/[&<>]/g, c => escMap[c])

// 引用標記是後端 RAG 服務的 FileID（32 碼 hex，或含 dash 的 UUID），不是位置序號——
// 這樣筆數一多（甚至上百筆）時，模型引用的來源還是能直接對應回正確的檔案。
// 畫面上仍然只顯示流水號（1、2、3...，依文字裡第一次出現的順序編），FileID 只在
// 內部拿來對應 sources，不曝露給使用者看。
// 方括號內外的 \s* 是容錯：模型偶爾會多打空白（例如 "[ b04d50a0...]"），跟後端
// cited_sources() 的規則一致，不然這裡對不到、來源清單就會是空的。
const CITATION_RE = /\[\s*([0-9a-fA-F-]{8,40})\s*\]/g

// 依 content 裡 [FileID] 第一次出現的順序，把每個 FileID 對應到一個流水號。
function displayNumbers(content, sources) {
  const numberOf = {}
  let next = 1
  for (const m of (content ?? '').matchAll(CITATION_RE)) {
    const id = m[1]
    if (sources[id] && !(id in numberOf)) numberOf[id] = next++
  }
  return numberOf
}

export function renderCitations(content, sources) {
  const numberOf = displayNumbers(content, sources)
  return esc(content).replace(CITATION_RE, (whole, n) => {
    const s = sources[n]
    if (!s) return whole  // 對不到已知來源就原樣保留，不要顯示裸 FileID
    const t = esc(s.name || ''), label = numberOf[n]
    if (s.url) return `<a class="cite" href="${esc(s.url)}" target="_blank" rel="noopener" title="${t}">[${label}]</a>`
    return `<span class="cite" title="${t}">[${label}]</span>`
  })
}

export function sourceListHTML(sources, numberOf) {
  // 只列「文字裡真的引用到」的來源（numberOf 有分配到編號的）——sources 這個物件
  // 常常是整個回合累積下來的（例如 send() 裡把這一輪所有 skill_result 的來源都收
  // 進同一個物件），不代表每一筆都真的被最終答案引用，這裡要主動篩掉沒用到的，
  // 不能只是給個 "?" 就照樣列出來。
  const ns = Object.keys(sources).filter(n => n in numberOf).sort((a, b) => numberOf[a] - numberOf[b])
  if (!ns.length) return ''
  const items = ns.map(n => {
    const s = sources[n], label = numberOf[n], name = esc(s.name || ('來源 ' + label))
    const inner = s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${name}</a>` : name
    return `<div class="src"><span class="src-n">[${label}]</span>${inner}</div>`
  }).join('')
  return `<div class="sources"><div class="sources-title">來源</div>${items}</div>`
}

// 把 AI 回覆的 markdown 渲染成安全 HTML，並把 [FileID] 引用換成給人看的流水號連結
export function renderMarkdown(content, sources = {}) {
  const numberOf = displayNumbers(content, sources)
  let html = marked.parse(content ?? '')
  html = html.replace(CITATION_RE, (whole, n) => {
    const s = sources[n]
    if (!s) return whole
    const t = esc(s.name || ''), label = numberOf[n]
    if (s.url) return `<a class="cite" href="${esc(s.url)}" target="_blank" rel="noopener" title="${t}">[${label}]</a>`
    return `<span class="cite" title="${t}">[${label}]</span>`
  })
  html += sourceListHTML(sources, numberOf)
  return DOMPurify.sanitize(html, { ADD_ATTR: ['target', 'rel', 'class', 'title'] })
}
