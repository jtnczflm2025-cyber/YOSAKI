<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import GlassSurface from '../ui/GlassSurface.vue'
import ThemeToggle from '../ui/ThemeToggle.vue'
import { voiceAudioPlayer } from './voiceAudioPlayer'

const base = '/api/voice-ai-http-lab'
type Preview = { request_text:string; request_characters:number; http_requests:number;
  parameters:Record<string,string>; effective_mode:string; fallback_reason:string|null; preview_sha256:string }
type Result = { id:string; bridge_state:string; error:string|null; reason:string|null;
  preview:Preview|null; audio:{url:string}[] }
const mode = ref('none'), consent = ref(false), enabled = ref(false), busy = ref(false)
const body = ref(''), result = ref<Result|null>(null), error = ref(''), playback = ref('未播放')
const history = ref<{url:string;label:string;sha:string}[]>([])
let profile:number|null = null, message = 501, epoch = 0, disposed = false
const labels:Record<string,string> = {none:'普通无标签',thoughtful:'手选 thoughtful',ai_expression:'AI 表达建议'}
const states:Record<string,string> = {planning:'正在规划完整回复',synthesizing:'正在合成',completed:'音频已保存',
  empty:'无需额外标注',fallback:'已回退普通朗读',failed_unknown:'规划结果未知，已停止',unknown:'合成结果未知，已停止',
  cancelled:'本轮已取消',restricted:'当前展示或权限不允许',stale:'正文或绑定已变化',failed:'本轮未派发或生成失败，已停止'}
async function api<T>(path:string, method='GET', data?:unknown):Promise<T> {
  const response = await fetch(base + path, {method,credentials:'same-origin',
    headers:data === undefined ? {} : {'Content-Type':'application/json'},
    body:data === undefined ? undefined : JSON.stringify(data)})
  const value = await response.json()
  if (!response.ok) throw new Error(value.error || `HTTP ${response.status}`)
  return value as T
}
async function source() {
  return api<{safe_text:string;input_sha256:string;source:{profile_revision:number}}>(`/messages/${message}/source?mode=${mode.value}`)
}
onMounted(async () => {
  try {
    await api('/session')
    const status = await api<{enabled:boolean;messages:number[];model_profile_id:number}>('/status')
    enabled.value = status.enabled; message = status.messages[0]; profile = status.model_profile_id
    if (enabled.value) body.value = (await source()).safe_text
  } catch (e) { error.value = e instanceof Error ? e.message : '隔离入口不可用' }
})
async function cancel() {
  epoch++; busy.value = false; voiceAudioPlayer.stop(message)
  const active = result.value
  if (active && ['planning','synthesizing','ready','empty','fallback'].includes(active.bridge_state)) {
    try { await api(`/plans/${active.id}`, 'DELETE') } catch { /* no restart */ }
  }
}
watch(mode, () => { void cancel(); result.value = null; consent.value = false; error.value = '' })
onBeforeUnmount(() => { disposed = true; void cancel() })
function play(url:string) {
  void voiceAudioPlayer.play(message,[url], state => { playback.value = state })
}
async function run() {
  if (busy.value || !enabled.value || (mode.value === 'ai_expression' && !consent.value)) return
  const token = ++epoch
  busy.value = true; error.value = ''
  try {
    const prepared = await source()
    if (token !== epoch || disposed) return
    let value = await api<Result>(`/messages/${message}/read`, 'POST', {
      operation_id:crypto.randomUUID(), input_sha256:prepared.input_sha256,
      profile_revision:prepared.source.profile_revision, planning_consent:consent.value,
      mode:mode.value, model_profile_id:mode.value === 'ai_expression' ? profile : null})
    if (token !== epoch || disposed) {
      await api(`/plans/${value.id}`, 'DELETE').catch(() => {})
      return
    }
    while (!disposed && token === epoch) {
      result.value = value
      if (value.bridge_state === 'completed' && value.audio.length && value.preview) {
        const url = value.audio[0].url, sha = value.preview.preview_sha256
        if (!history.value.some(h => h.sha === sha)) history.value.push({url,sha,label:labels[mode.value]})
        play(url); break
      }
      if (!['planning','synthesizing'].includes(value.bridge_state)) break
      await new Promise(resolve => setTimeout(resolve,100))
      if (token !== epoch || disposed) return
      value = await api<Result>(`/plans/${value.id}`)
    }
  } catch (e) { if (token === epoch) error.value = e instanceof Error ? e.message : '本轮未完成，不会重试' }
  finally { if (token === epoch) busy.value = false }
}
</script>

<template>
  <main class="bridge-lab">
    <header><h1>完整回复 → AI 表达 → HTTP 朗读</h1><ThemeToggle /></header>
    <p>仅隔离模拟：已保存并通过展示核验的短日语回复。假模型、假 TTS；不连接真实服务。</p>
    <GlassSurface level="reading" class="panel">
      <p v-if="!enabled">此隔离功能默认关闭，未启用。</p>
      <p class="reply">{{ body }}</p>
      <label>表达选择
        <select v-model="mode" :disabled="busy"><option value="none">普通无标签</option><option value="thoughtful">手选 thoughtful</option><option value="ai_expression">AI 表达建议（隔离）</option></select>
      </label>
      <label v-if="mode === 'ai_expression'"><input v-model="consent" type="checkbox" :disabled="busy">允许本轮模拟规划；真实接入可能新增一次模型分析</label>
      <div class="controls">
        <button :disabled="!enabled || busy || (mode === 'ai_expression' && !consent)" @click="run">模拟规划并朗读</button>
        <button :disabled="!busy" @click="cancel">取消本轮</button>
        <button @click="voiceAudioPlayer.pause(message)">暂停</button><button @click="voiceAudioPlayer.resume(message)">继续</button>
        <button @click="voiceAudioPlayer.stop(message)">停止播放</button>
      </div>
      <p role="status">{{ result ? states[result.bridge_state] || result.bridge_state : '尚未开始' }} · {{ playback }}</p>
      <p v-if="error || result?.error || result?.reason" role="alert">{{ error || result?.error || result?.reason }}</p>
      <template v-if="result?.preview">
        <h2>实际送读预览</h2><pre>{{ result.preview.request_text }}</pre>
        <p>{{ result.preview.request_characters }} 字符 · {{ result.preview.http_requests }} 次 HTTP · 有效模式 {{ result.preview.effective_mode }}</p>
        <p v-if="result.preview.fallback_reason">回退原因：{{ result.preview.fallback_reason }}</p>
        <details><summary>有效参数</summary><pre>{{ JSON.stringify(result.preview.parameters, null, 2) }}</pre></details>
      </template>
      <h2>已保存版本</h2><p>重播只读取已有音频，不重新规划或合成。合成 MP3 仅用于工程播放验证，不代表阿夜听感。</p>
      <button v-for="item in history" :key="item.sha" @click="play(item.url)">重播：{{ item.label }}</button>
    </GlassSurface>
  </main>
</template>

<style scoped>
.bridge-lab { max-width:860px; margin:24px auto; padding:16px; color:var(--ui-ink) }
header,.controls { display:flex; flex-wrap:wrap; gap:12px; align-items:center }
header { justify-content:space-between }
.panel { padding:24px }
.reply,pre { white-space:pre-wrap; overflow-wrap:anywhere; font:17px/1.75 var(--ui-font-body, sans-serif) }
label { display:block; margin:16px 0 }
button,select { min-height:44px; margin:4px; padding:8px 12px; color:var(--ui-ink); background:var(--ui-raised); border:1px solid var(--ui-line); border-radius:12px }
button:disabled { opacity:.55 }
[role=alert] { color:var(--ui-danger) }
</style>
