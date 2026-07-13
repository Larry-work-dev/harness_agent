import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// dev 時把 API 代理到 backend（正式是走 nginx 同源）
const proxy = Object.fromEntries(
  ['/auth', '/workspaces', '/conversations', '/memories', '/skills', '/chat']
    .map(p => [p, { target: 'http://localhost:8000', changeOrigin: true }])
)

export default defineConfig({ plugins: [vue()], server: { proxy } })
