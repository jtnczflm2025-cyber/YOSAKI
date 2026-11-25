/// <reference types="vite/client" />
import { createApp } from 'vue'
import './ui/theme'
import './style.css'
import './chronicle.css'
import './ui/tokens.css'
import './ui/silver.css'

if (window.location.pathname === '/chronicle' && import.meta.env.VITE_YOSAKI_CHRONICLE_A0 === '1') {
  import('./ChronicleApp.vue').then(({ default: ChronicleApp }) => createApp(ChronicleApp).mount('#app'))
} else {
  import('./App.vue').then(({ default: App }) => createApp(App).mount('#app'))
}
