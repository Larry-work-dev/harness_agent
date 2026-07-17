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

export function renderCitations(content, sources) {
  return esc(content).replace(/\[(\d+)\]/g, (_, n) => {
    const s = sources[n]; const t = s ? esc(s.name || '') : ''
    if (s && s.url) return `<a class="cite" href="${esc(s.url)}" target="_blank" rel="noopener" title="${t}">[${n}]</a>`
    return `<span class="cite" title="${t}">[${n}]</span>`
  })
}

export function sourceListHTML(sources) {
  const ns = Object.keys(sources).map(Number).sort((a, b) => a - b)
  if (!ns.length) return ''
  const items = ns.map(n => {
    const s = sources[n], name = esc(s.name || ('來源 ' + n))
    const inner = s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${name}</a>` : name
    return `<div class="src"><span class="src-n">[${n}]</span>${inner}</div>`
  }).join('')
  return `<div class="sources"><div class="sources-title">來源</div>${items}</div>`
}

// 把 AI 回覆的 markdown 渲染成安全 HTML，並保留 [n] 引用連結
export function renderMarkdown(content, sources = {}) {
  let html = marked.parse(content ?? '')
  html = html.replace(/\[(\d+)\]/g, (_, n) => {
    const s = sources[n]; const t = s ? esc(s.name || '') : ''
    if (s && s.url) return `<a class="cite" href="${esc(s.url)}" target="_blank" rel="noopener" title="${t}">[${n}]</a>`
    return `<span class="cite" title="${t}">[${n}]</span>`
  })
  html += sourceListHTML(sources)
  return DOMPurify.sanitize(html, { ADD_ATTR: ['target', 'rel', 'class', 'title'] })
}
