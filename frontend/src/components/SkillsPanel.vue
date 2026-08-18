<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { api } from '../api.js'

const emit = defineEmits(['close'])

const skills = ref([])
const showCreate = ref(false)
const testing = ref(null)   // 目前展開測試表單的技能 id
const testArgs = reactive({})   // { [skillId]: { paramName: value } }
const testMessage = reactive({})
const testResult = reactive({}) // { [skillId]: { text, error } }
const err = ref('')

const KIND_LABEL = { http: 'HTTP', prompt: 'Prompt', code: 'Code' }

onMounted(load)
async function load() {
  try { skills.value = await api('/skills') } catch (e) { err.value = e.message }
}

function blankForm() {
  return {
    kind: 'http', name: '', description: '',
    method: 'GET', url: '', headers: [], body_template: '', static_values: [],
    trigger_keywords: '', system_prompt_template: '',
    language: 'python', source: '',
    params: [],
  }
}
const form = reactive(blankForm())

function resetForm() { Object.assign(form, blankForm()) }
function addParam() { form.params.push({ name: '', type: 'str', required: true, description: '' }) }
function delParam(i) { form.params.splice(i, 1) }
function addKV(list) { list.push({ key: '', value: '' }) }
function delKV(list, i) { list.splice(i, 1) }

function kvToObject(list) {
  const o = {}
  for (const { key, value } of list) if (key) o[key] = value
  return o
}

async function submit() {
  err.value = ''
  const spec = {}
  if (form.kind === 'http') {
    spec.method = form.method
    spec.url = form.url
    spec.headers_template = kvToObject(form.headers)
    spec.body_template = form.body_template || ''
    spec.static_values = kvToObject(form.static_values)
    spec.params = form.params.filter(p => p.name)
  } else if (form.kind === 'prompt') {
    spec.trigger_keywords = form.trigger_keywords.split(',').map(s => s.trim()).filter(Boolean)
    spec.system_prompt_template = form.system_prompt_template
  } else {
    spec.language = form.language
    spec.source = form.source
    spec.params = form.params.filter(p => p.name)
  }
  try {
    await api('/skills', { method: 'POST', body: { kind: form.kind, name: form.name, description: form.description, spec } })
    resetForm()
    showCreate.value = false
    await load()
  } catch (e) { err.value = e.message }
}

async function del(id) {
  if (!confirm('刪除這個技能？')) return
  try {
    await api('/skills/' + id, { method: 'DELETE' })
    skills.value = skills.value.filter(s => s.id !== id)
  } catch (e) { err.value = e.message }
}

function openTest(skill) {
  testing.value = testing.value === skill.id ? null : skill.id
  if (!testArgs[skill.id]) testArgs[skill.id] = {}
  if (testMessage[skill.id] === undefined) testMessage[skill.id] = ''
}

async function runTest(skill) {
  testResult[skill.id] = null
  const body = skill.kind === 'prompt'
    ? { message: testMessage[skill.id] || '' }
    : { args: { ...testArgs[skill.id] } }
  try {
    const r = await api(`/skills/${skill.id}/test`, { method: 'POST', body })
    testResult[skill.id] = { text: r.result ?? JSON.stringify(r), error: null }
  } catch (e) {
    testResult[skill.id] = { text: '', error: e.message }
  }
}
</script>

<template>
  <div class="overlay" @click="emit('close')"></div>
  <div class="panel">
    <h3>你的技能 <button class="close" @click="emit('close')">×</button></h3>
    <div class="desc">
      自建的技能：http 呼叫外部 API、prompt 依關鍵字換一套系統提示、code 交給隔離的
      沙盒服務執行。http/code 建好後模型就能在對話中直接呼叫；prompt 命中關鍵字時不經模型，直接回答。
    </div>

    <div class="list">
      <div v-if="!skills.length && !showCreate" class="empty">還沒有任何技能，點下面「新增技能」建立第一個。</div>

      <div v-for="s in skills" :key="s.id" class="item">
        <div class="row-top">
          <span class="badge" :class="s.kind">{{ KIND_LABEL[s.kind] }}</span>
          <span class="name">{{ s.name }}</span>
          <span class="spacer"></span>
          <button class="link" @click="openTest(s)">測試</button>
          <button class="del" title="刪除" @click="del(s.id)">×</button>
        </div>
        <div class="sub">{{ s.description }}</div>

        <div v-if="testing === s.id" class="test-box">
          <template v-if="s.kind === 'prompt'">
            <textarea v-model="testMessage[s.id]" rows="2" placeholder="輸入一句測試訊息"></textarea>
          </template>
          <template v-else>
            <div v-if="!(s.spec.params || []).length" class="hint">這個技能沒有參數。</div>
            <div v-for="p in (s.spec.params || [])" :key="p.name" class="test-field">
              <label>{{ p.name }}<span v-if="p.required">*</span></label>
              <input v-model="testArgs[s.id][p.name]" :placeholder="p.description" />
            </div>
          </template>
          <button class="primary small" @click="runTest(s)">執行</button>
          <div v-if="testResult[s.id]" class="test-result">
            <div v-if="testResult[s.id].error" class="test-err">{{ testResult[s.id].error }}</div>
            <pre v-else>{{ testResult[s.id].text }}</pre>
          </div>
        </div>
      </div>

      <div v-if="showCreate" class="create-form">
        <div class="mfield"><label>名稱</label><input v-model="form.name" placeholder="例如 查匯率" /></div>
        <div class="mfield"><label>說明</label><input v-model="form.description" placeholder="給模型看的用途說明" /></div>
        <div class="mfield">
          <label>類型</label>
          <select v-model="form.kind">
            <option value="http">http（呼叫外部 API）</option>
            <option value="prompt">prompt（關鍵字觸發，換系統提示）</option>
            <option value="code">code（交給沙盒執行程式碼）</option>
          </select>
        </div>

        <template v-if="form.kind === 'http'">
          <div class="mfield inline">
            <select v-model="form.method" class="short">
              <option>GET</option><option>POST</option><option>PUT</option><option>DELETE</option>
            </select>
            <input v-model="form.url" placeholder="https://api.example.com/..." />
          </div>
          <div class="mfield"><label>Body 模板（選填，可用 {參數名} 帶入）</label>
            <textarea v-model="form.body_template" rows="2"></textarea></div>
          <div class="kv-editor">
            <label>Headers</label>
            <div v-for="(h, i) in form.headers" :key="i" class="kv-row">
              <input v-model="h.key" placeholder="header 名稱" />
              <input v-model="h.value" placeholder="值（可用 {參數名}）" />
              <button class="del" @click="delKV(form.headers, i)">×</button>
            </div>
            <button class="link" @click="addKV(form.headers)">+ 新增 header</button>
          </div>
          <div class="kv-editor">
            <label>固定帶入的值（不給模型填，每次呼叫都帶這些）</label>
            <div v-for="(h, i) in form.static_values" :key="i" class="kv-row">
              <input v-model="h.key" placeholder="欄位名稱" />
              <input v-model="h.value" type="password" placeholder="值（例如 API 金鑰）" />
              <button class="del" @click="delKV(form.static_values, i)">×</button>
            </div>
            <button class="link" @click="addKV(form.static_values)">+ 新增固定值</button>
          </div>
        </template>

        <template v-else-if="form.kind === 'prompt'">
          <div class="mfield"><label>觸發關鍵字（逗號分隔）</label>
            <input v-model="form.trigger_keywords" placeholder="例如 翻譯,英翻中" /></div>
          <div class="mfield"><label>系統提示模板</label>
            <textarea v-model="form.system_prompt_template" rows="5"
                      placeholder="你是一個...，請..."></textarea></div>
        </template>

        <template v-else>
          <div class="mfield">
            <label>語言</label>
            <select v-model="form.language">
              <option value="python">Python</option>
              <option value="javascript">JavaScript</option>
            </select>
          </div>
          <div class="mfield"><label>程式碼（需要定義 run(...) 函式）</label>
            <textarea v-model="form.source" rows="8" class="code"
                      :placeholder="form.language === 'python' ? 'def run(a, b):\n    return a + b' : 'function run(args) {\n  return args.a + args.b\n}'"></textarea></div>
        </template>

        <template v-if="form.kind !== 'prompt'">
          <div class="kv-editor">
            <label>參數（給模型/測試表單用）</label>
            <div v-for="(p, i) in form.params" :key="i" class="param-row">
              <input v-model="p.name" placeholder="參數名" class="short" />
              <select v-model="p.type" class="short">
                <option value="str">str</option><option value="int">int</option>
                <option value="float">float</option><option value="bool">bool</option>
              </select>
              <label class="req"><input type="checkbox" v-model="p.required" /> 必填</label>
              <input v-model="p.description" placeholder="說明" />
              <button class="del" @click="delParam(i)">×</button>
            </div>
            <button class="link" @click="addParam">+ 新增參數</button>
          </div>
        </template>

        <div v-if="err" class="err">{{ err }}</div>
        <div class="form-actions">
          <button class="primary" @click="submit">建立</button>
          <button @click="showCreate = false; resetForm()">取消</button>
        </div>
      </div>
    </div>

    <button v-if="!showCreate" class="add-btn" @click="showCreate = true">+ 新增技能</button>
  </div>
</template>

<style scoped>
.overlay { position: fixed; inset: 0; background: rgba(0,0,0,.5); z-index: 40; }
.panel { position: fixed; top: 0; right: 0; width: 420px; max-width: 92vw; height: 100vh; background: var(--surface); border-left: 1px solid var(--border); z-index: 50; display: flex; flex-direction: column; }
h3 { font-family: var(--font-display); font-size: 16px; padding: 18px 20px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; }
.close { background: none; border: none; color: var(--muted); font-size: 20px; }
.desc { color: var(--muted); font-size: 12px; padding: 12px 20px 4px; line-height: 1.5; }
.list { flex: 1; overflow-y: auto; padding: 8px 16px 16px; }
.empty { color: var(--muted); font-size: 13px; padding: 16px 4px; }
.item { background: var(--surface-2); border: 1px solid var(--border); border-radius: 10px; padding: 11px 13px; margin-bottom: 8px; }
.row-top { display: flex; align-items: center; gap: 8px; }
.badge { font-size: 10.5px; font-family: var(--font-mono); border-radius: 6px; padding: 1px 6px; color: #0A0E14; }
.badge.http { background: var(--skill); }
.badge.prompt { background: var(--agent); }
.badge.code { background: var(--danger); }
.name { font-weight: 600; font-size: 13.5px; }
.spacer { flex: 1; }
.sub { color: var(--muted); font-size: 12px; margin-top: 5px; }
.link { background: none; border: none; color: var(--agent); font-size: 12.5px; padding: 0 4px; }
.del { background: none; border: none; color: var(--muted); font-size: 15px; }
.del:hover { color: var(--danger); }
.test-box { margin-top: 9px; padding-top: 9px; border-top: 1px dashed var(--border); display: flex; flex-direction: column; gap: 6px; }
.test-field { display: flex; align-items: center; gap: 6px; font-size: 12.5px; }
.test-field label { width: 90px; color: var(--muted); flex-shrink: 0; overflow: hidden; text-overflow: ellipsis; }
.test-field input, .test-box textarea { flex: 1; background: var(--surface); border: 1px solid var(--border); color: var(--text); border-radius: 7px; padding: 6px 9px; font-size: 12.5px; font-family: inherit; }
.hint { color: var(--muted); font-size: 12px; }
.test-result { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; font-size: 12px; margin-top: 2px; }
.test-result pre { white-space: pre-wrap; word-break: break-word; margin: 0; font-family: var(--font-mono); }
.test-err { color: var(--danger); }
.add-btn { margin: 10px 16px 16px; padding: 10px; background: var(--surface-2); border: 1px dashed var(--border); color: var(--muted); border-radius: 10px; font-weight: 500; }
.create-form { background: var(--surface-2); border: 1px solid var(--border); border-radius: 10px; padding: 13px; margin-top: 4px; display: flex; flex-direction: column; gap: 10px; }
.mfield label { display: block; font-size: 12px; color: var(--muted); margin-bottom: 4px; }
.mfield input, .mfield select, .mfield textarea, .create-form input, .create-form select, .create-form textarea {
  width: 100%; background: var(--surface); border: 1px solid var(--border); color: var(--text);
  border-radius: 8px; padding: 8px 10px; font-family: inherit; font-size: 13px;
}
.mfield textarea.code { font-family: var(--font-mono); font-size: 12.5px; }
.mfield.inline { display: flex; gap: 6px; }
.short { width: 90px; flex-shrink: 0; }
.kv-editor label { display: block; font-size: 12px; color: var(--muted); margin-bottom: 4px; }
.kv-row, .param-row { display: flex; gap: 6px; align-items: center; margin-bottom: 6px; }
.kv-row input, .param-row input, .param-row select { background: var(--surface); border: 1px solid var(--border); color: var(--text); border-radius: 7px; padding: 6px 9px; font-size: 12.5px; }
.param-row .req { display: flex; align-items: center; gap: 4px; font-size: 11.5px; color: var(--muted); white-space: nowrap; }
.err { color: var(--danger); font-size: 12.5px; }
.form-actions { display: flex; gap: 8px; }
.primary { background: var(--agent); color: #0A0E14; border: none; border-radius: 9px; padding: 9px 14px; font-weight: 600; }
.primary.small { align-self: flex-start; padding: 6px 12px; font-size: 12.5px; }
</style>
