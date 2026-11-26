import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Separate explicit entry: never proxies requests to the daily 8000 instance.
export default defineConfig({
  plugins: [vue()],
  server: { host: '127.0.0.1', port: 18889, strictPort: true,
    proxy: { '/api/voice-expression-lab': 'http://127.0.0.1:18888' } },
  build: { rollupOptions: { input: 'voice-expression-lab.html' } },
})
