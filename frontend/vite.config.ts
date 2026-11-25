import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
export default defineConfig({ cacheDir:'.vite-cache', plugins:[vue()], server:{ port:5173, proxy:{'/api':{target:process.env.YOSAKI_API_PROXY || 'http://127.0.0.1:8000',ws:true}} } })
