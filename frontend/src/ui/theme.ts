import { ref, watch } from 'vue'

export type Theme = 'light' | 'dark'
const key = 'YOSAKI.ui.theme.v1'
function readTheme(): Theme {
  try { return localStorage.getItem(key) === 'dark' ? 'dark' : 'light' } catch { return 'light' }
}
export const theme = ref<Theme>(readTheme())
function applyTheme(value: Theme) { document.documentElement.dataset.theme = value }
applyTheme(theme.value)
watch(theme, value => {
  applyTheme(value)
  try { localStorage.setItem(key, value) } catch { /* UI preference only. */ }
})
window.addEventListener('storage', event => {
  if (event.key === key) theme.value = event.newValue === 'dark' ? 'dark' : 'light'
})
export function toggleTheme() { theme.value = theme.value === 'dark' ? 'light' : 'dark' }
