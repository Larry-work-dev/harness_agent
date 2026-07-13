<script setup>
import { ref } from 'vue'
import { api, setToken } from '../api.js'

const mode = ref('login')      // 'login' | 'register'
const username = ref('')
const password = ref('')
const err = ref('')

async function submit() {
  if (!username.value || !password.value) { err.value = '請輸入帳號與密碼'; return }
  try {
    const r = await api('/auth/' + mode.value, {
      method: 'POST',
      body: { username: username.value, password: password.value },
    })
    setToken(r.token)          // token 變動 → App 自動切換到 MainView
  } catch (e) {
    err.value = e.message
  }
}
</script>

<template>
  <div class="auth">
    <div class="card">
      <h1><span class="dot"></span>Agent Harness</h1>
      <p class="sub">登入以使用你的 workspace 與對話</p>

      <div class="tabs">
        <button :class="{ active: mode === 'login' }" @click="mode = 'login'; err = ''">登入</button>
        <button :class="{ active: mode === 'register' }" @click="mode = 'register'; err = ''">註冊</button>
      </div>

      <div class="field">
        <label>使用者名稱</label>
        <input v-model="username" autocomplete="username" @keydown.enter="submit" />
      </div>
      <div class="field">
        <label>密碼</label>
        <input v-model="password" type="password" autocomplete="current-password" @keydown.enter="submit" />
      </div>

      <button class="primary" @click="submit">{{ mode === 'login' ? '登入' : '註冊' }}</button>
      <div class="err">{{ err }}</div>
    </div>
  </div>
</template>

<style scoped>
.auth { height: 100vh; display: grid; place-items: center; padding: 20px; }
.card { width: 100%; max-width: 360px; background: var(--surface); border: 1px solid var(--border); border-radius: 16px; padding: 28px; }
h1 { font-family: var(--font-display); font-size: 20px; display: flex; align-items: center; gap: 9px; margin-bottom: 4px; }
.dot { width: 9px; height: 9px; border-radius: 50%; background: var(--agent); }
.sub { color: var(--muted); font-size: 13px; margin-bottom: 20px; }
.tabs { display: flex; gap: 6px; margin-bottom: 16px; }
.tabs button { flex: 1; padding: 8px; background: var(--surface-2); border: 1px solid var(--border); color: var(--muted); border-radius: 9px; font-weight: 500; }
.tabs button.active { color: var(--text); border-color: var(--agent); }
.field { margin-bottom: 12px; }
.field label { display: block; font-size: 12.5px; color: var(--muted); margin-bottom: 5px; }
.field input { width: 100%; background: var(--surface-2); border: 1px solid var(--border); color: var(--text); border-radius: 9px; padding: 10px 12px; font-family: inherit; font-size: 14px; }
.field input:focus { outline: none; border-color: var(--agent); }
.primary { width: 100%; background: var(--agent); color: #0A0E14; border: none; border-radius: 9px; padding: 11px; font-weight: 600; margin-top: 6px; }
.primary:hover { opacity: .9; }
.err { color: var(--danger); font-size: 12.5px; margin-top: 10px; min-height: 18px; }
</style>
