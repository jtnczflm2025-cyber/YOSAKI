import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: { host:'127.0.0.1', port:18897, strictPort:true,
    proxy:{ '/api/voice-ai-http-lab':'http://127.0.0.1:18896' } },
  build:{ rollupOptions:{ input:'voice-ai-http-lab.html' } },
})
