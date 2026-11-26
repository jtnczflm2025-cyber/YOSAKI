import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
export default defineConfig({ plugins:[vue()],
  server:{host:'127.0.0.1',port:18893,strictPort:true,
    proxy:{'/api/voice-segmented-lab':'http://127.0.0.1:18892'}},
  build:{outDir:'dist-segmented-lab',rollupOptions:{input:'voice-segmented-lab.html'}} })
