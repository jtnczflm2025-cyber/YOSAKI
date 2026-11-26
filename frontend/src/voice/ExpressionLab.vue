<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import GlassSurface from '../ui/GlassSurface.vue'
import ThemeToggle from '../ui/ThemeToggle.vue'

type Annotation = { fragment_id: string; intent: string }
type Fragment = { fragment_id: string; kind: string; start: number; end: number }
type Source = { safe_text: string; fragments: Fragment[]; input_sha256: string;
  source: { body: string; profile_revision: number } }
type Plan = { id: string; revision: number; state: string; reason: string | null;
  versions: { revision: number; annotations: Annotation[] }[] }
type Preview = { request_text: string; request_characters: number; segment_count: number;
  http_requests: number; preview_sha256: string; plan_revision: number; audio_state: string;
  historical: boolean; parameters: Record<string, string>; insertions: { tag: string; offset: number }[] }
type Status = { enabled: boolean; fixtures: {id: number; label: string}[]; intents: string[];
  counters: {mock_model_attempts: number; mock_tts_attempts: number; real_provider_requests: number} }
const prefix = '/api/voice-expression-lab'
const status = ref<Status | null>(null), source = ref<Source | null>(null)
const selected = ref(102), mode = ref('none'), granularity = ref('auto')
const showBadges = ref(false), plan = ref<Plan | null>(null), preview = ref<Preview | null>(null)
const annotations = ref<Annotation[]>([]), revision = ref(0), dirty = ref(false)
const busy = ref(false), error = ref(''), audioState = ref('未播放')
const audio = ref<HTMLAudioElement | null>(null)
let epoch = 0
const labels: Record<string, string> = {inherit: '不加额外标注', thoughtful: '若有所思',
  restrained: '克制候选', lazy: '慵懒候选', dry_humor: '冷幽默候选', resigned: '无奈候选',
  whisper: '低语候选', chuckle: '轻笑候选'}
const stateLabels: Record<string, string> = {planning: '正在规划，可取消', ready: '方案已就绪',
  empty: '无需额外标注', fallback: '规划无效，已回退普通朗读', failed_unknown: '规划结果未知，不会重试',
  cancelled: '已取消，旧结果不会播放', stale: '方案已过期', restricted: '当前权限不允许使用'}
const stateText = computed(() => plan.value ? stateLabels[plan.value.state] || plan.value.state : '尚未规划')
const rows = computed(() => source.value?.fragments.filter(f => granularity.value === 'auto' || f.kind === granularity.value) || [])
const selectedIntent = (id: string) => annotations.value.find(a => a.fragment_id === id)?.intent || ''
const slice = (f: Fragment) => Array.from(source.value?.safe_text || '').slice(f.start, f.end).join('')

async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(prefix + path, { method, credentials: 'same-origin',
    headers: body === undefined ? {} : {'Content-Type': 'application/json'},
    body: body === undefined ? undefined : JSON.stringify(body) })
  const value = await response.json()
  if (!response.ok) throw new Error(value.error || `HTTP ${response.status}`)
  return value as T
}
async function refreshStatus() { status.value = await api<Status>('/status') }
function stopAudio() {
  if (audio.value) { audio.value.pause(); audio.value.removeAttribute('src'); audio.value.load() }
  audioState.value = '已停止'
}
async function loadSource() {
  const old = plan.value, token = ++epoch
  stopAudio(); busy.value = false; error.value = ''; plan.value = null; preview.value = null
  dirty.value = false; annotations.value = []; revision.value = 0; showBadges.value = false
  if (old?.state === 'planning') await api(`/expression-plans/${old.id}`, 'DELETE').catch(() => {})
  try {
    const next = await api<Source>(`/messages/${selected.value}/expression-source?granularity=${granularity.value}&mode=${mode.value}`)
    if (token === epoch) source.value = next
  } catch (e) { if (token === epoch) error.value = String(e) }
}
async function loadPreview() {
  if (!plan.value) return
  preview.value = await api<Preview>(`/expression-plans/${plan.value.id}/speech-preview?revision=${revision.value}`)
  annotations.value = plan.value.versions.find(v => v.revision === revision.value)?.annotations.map(a => ({...a})) || []
  dirty.value = false
}
async function createPlan() {
  if (!source.value || busy.value) return
  const token = ++epoch; busy.value = true; error.value = ''; stopAudio()
  try {
    let active: Plan = await api<Plan>(`/messages/${selected.value}/expression-plans`, 'POST', {
      operation_id: crypto.randomUUID(), input_sha256: source.value.input_sha256,
      profile_revision: source.value.source.profile_revision,
      planning_consent: mode.value === 'ai_expression', granularity: granularity.value, mode: mode.value })
    if (token !== epoch) {
      await api(`/expression-plans/${active.id}`, 'DELETE').catch(() => {})
      return
    }
    plan.value = active
    const deadline = performance.now() + 22000
    while (active.state === 'planning' && token === epoch) {
      if (performance.now() >= deadline) throw new Error('等待结束；不会重新规划')
      await new Promise(resolve => setTimeout(resolve, 100))
      const next: Plan = await api<Plan>(`/expression-plans/${active.id}`)
      if (token !== epoch) return
      active = next; plan.value = next
    }
    if (token === epoch && ['ready', 'empty', 'fallback'].includes(active.state)) {
      revision.value = active.revision; await loadPreview()
    }
    await refreshStatus()
  } catch (e) { if (token === epoch) error.value = String(e) }
  finally { if (token === epoch) busy.value = false }
}
function changeAnnotation(id: string, event: Event) {
  const intent = (event.target as HTMLSelectElement).value
  annotations.value = annotations.value.filter(a => a.fragment_id !== id)
  if (intent) annotations.value.push({fragment_id: id, intent})
  dirty.value = true; stopAudio()
}
async function saveAnnotations() {
  if (!plan.value || busy.value) return
  error.value = ''
  try {
    plan.value = await api<Plan>(`/expression-plans/${plan.value.id}`, 'PATCH', {
      expected_revision: revision.value, annotations: annotations.value })
    revision.value = plan.value.revision; await loadPreview(); await refreshStatus()
  } catch (e) { error.value = String(e) }
}
async function synthesize() {
  if (!plan.value || !preview.value || busy.value || dirty.value || preview.value.historical) return
  const token = epoch, id = plan.value.id, old = preview.value
  busy.value = true; error.value = ''
  try {
    await api(`/expression-plans/${id}/speak`, 'POST', {
      revision: old.plan_revision, preview_sha256: old.preview_sha256 })
    if (token === epoch) await loadPreview()
    await refreshStatus()
  } catch (e) { if (token === epoch) error.value = String(e) }
  finally { if (token === epoch) busy.value = false }
}
async function playCache() {
  if (!preview.value || !plan.value || dirty.value || preview.value.audio_state !== 'ready') return
  stopAudio()
  const target = audio.value
  if (!target) return
  target.src = `${prefix}/expression-plans/${plan.value.id}/audio/${preview.value.preview_sha256}`
  try { await target.play(); audioState.value = '播放本地测试音（不是阿夜音频）' }
  catch { error.value = '本地缓存播放未成功，请查看浏览器播放权限' }
}
async function cancel() {
  ++epoch; stopAudio(); busy.value = false
  if (!plan.value) return
  try {
    await api(`/expression-plans/${plan.value.id}`, 'DELETE')
    plan.value = {...plan.value, state: 'cancelled'}; preview.value = null
    await refreshStatus()
  } catch (e) { error.value = String(e) }
}
onMounted(async () => { try { await api('/session'); await refreshStatus(); await loadSource() } catch (e) { error.value = String(e) } })
onBeforeUnmount(() => { ++epoch; stopAudio(); if (plan.value?.state === 'planning') {
  void api(`/expression-plans/${plan.value.id}`, 'DELETE').catch(() => {}) } })
</script>

<template>
  <main class="expression-lab">
    <header><h1>表达规划 · 隔离验收</h1><ThemeToggle /></header>
    <p>仅使用合成正文、本地假模型与测试音。不连接供应商，不修改日常聊天和声音档案。</p>
    <p v-if="status && !status.enabled" role="alert">隔离功能默认关闭，当前后端未启用。</p>
    <GlassSurface level="reading" class="lab-section">
      <label>合成样本 <select v-model="selected" data-testid="sample" @change="loadSource"><option v-for="s in status?.fixtures" :key="s.id" :value="s.id">{{ s.label }}</option></select></label>
      <label>表达选择 <select v-model="mode" data-testid="mode" @change="loadSource"><option value="none">普通无标签（默认）</option><option value="thoughtful">手选 thoughtful</option><option value="ai_expression">AI 建议（仅本地模拟）</option></select></label>
      <label>标注粒度 <select v-model="granularity" data-testid="granularity" @change="loadSource"><option value="auto">自动（允许稀疏段句）</option><option value="paragraph">按段</option><option value="sentence">按句</option></select></label>
      <p>AI 建议会对完整回复分析一次；刷新和重复操作复用同一计划。多位置标签仅为待听评候选，不保证标签只作用于标注句，也不自动插入重置标签。</p>
      <button data-testid="plan" :disabled="busy || !source || !status?.enabled" @click="createPlan">{{ mode === 'ai_expression' ? '规划一次（假模型）' : '准备预览（不调模型）' }}</button>
      <button data-testid="cancel" :disabled="!plan" @click="cancel">取消本条</button>
      <p role="status" data-testid="state">{{ stateText }}</p>
      <p v-if="error" role="alert" data-testid="error">{{ error }}</p>
    </GlassSurface>
    <GlassSurface v-if="source" level="reading" class="lab-section">
      <h2>原聊天正文</h2><div class="reply" data-testid="body">{{ source.source.body }}</div>
      <label><input v-model="showBadges" type="checkbox" data-testid="show-badges">显示语气小标（默认隐藏，不触发规划或合成）</label>
      <div v-if="showBadges && plan && mode === 'ai_expression'" data-testid="annotations">
        <p v-if="!annotations.length">目前没有额外标注，允许留白。</p>
        <div v-for="f in rows" :key="f.fragment_id" class="fragment">
          <span class="reply">{{ slice(f) }}</span>
          <label>{{ f.kind === 'paragraph' ? '段' : '句' }}语气
            <select :value="selectedIntent(f.fragment_id)" :disabled="busy || revision !== plan.revision" :data-fragment="f.fragment_id" @change="changeAnnotation(f.fragment_id, $event)">
              <option value="">移除标注</option><option v-for="intent in status?.intents" :key="intent" :value="intent">{{ labels[intent] }}</option>
            </select>
          </label>
        </div>
        <p v-if="dirty" role="status">标注未保存；当前预览仍是旧版。</p>
        <button data-testid="save" :disabled="!dirty || busy" @click="saveAnnotations">保存标注并更新预览</button>
      </div>
    </GlassSurface>
    <GlassSurface v-if="preview && plan" level="reading" class="lab-section">
      <h2>完整送读预览</h2>
      <label>方案版本 <select v-model="revision" data-testid="revision" @change="stopAudio(); loadPreview()"><option v-for="v in plan.versions" :key="v.revision" :value="v.revision">版本 {{ v.revision }}</option></select></label>
      <p v-if="preview.historical">历史版本可查看和播放已有缓存；不使用旧预览重新生成。</p>
      <div class="reply preview" data-testid="preview">{{ preview.request_text }}</div>
      <p data-testid="counts">{{ preview.request_characters }} 个实际请求字符 · {{ preview.segment_count }} 段 · {{ preview.http_requests }} 次 HTTP</p>
      <p>添加标签：{{ preview.insertions.map(i => `${i.tag.trim()} @${i.offset}`).join('，') || '无' }}</p>
      <p>{{ preview.parameters.model_id }} · {{ preview.parameters.output_format }} · language_code={{ preview.parameters.language_code }} · settings={{ preview.parameters.settings }}</p>
      <details><summary>预览指纹</summary><code class="hash">{{ preview.preview_sha256 }}</code></details>
      <button data-testid="synthesize" :disabled="busy || dirty || preview.historical || preview.audio_state !== 'not_generated'" @click="synthesize">交给假 TTS（只生成本地测试音）</button>
      <button data-testid="play-cache" :disabled="dirty || preview.audio_state !== 'ready'" @click="playCache">播放已有测试音</button>
      <button @click="audio?.pause(); audioState = '已暂停'">暂停</button>
      <button @click="audio?.play().then(() => audioState = '继续播放已有测试音').catch(() => {})">继续</button>
      <button data-testid="stop" @click="stopAudio">停止播放</button>
      <audio ref="audio" @ended="audioState = '已播完'" @error="audioState = '播放错误'" />
      <p role="status">{{ audioState }}</p>
    </GlassSurface>
    <p v-if="status" data-testid="counters">本地模拟：模型 {{ status.counters.mock_model_attempts }} 次，假 TTS {{ status.counters.mock_tts_attempts }} 次。真实供应商 {{ status.counters.real_provider_requests }} 次。</p>
  </main>
</template>

<style scoped>
:global(body) { margin: 0; background: var(--ui-bg); color: var(--ui-ink); font-family: var(--ui-font); }
.expression-lab { max-width: 860px; margin: 0 auto; padding: 24px; color: var(--ui-ink); font-family: var(--ui-font); }
header { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
h1 { font-size: 26px; } h2 { font-size: 20px; }
.lab-section { padding: 20px; margin: 20px 0; }
.reply { font-size: 17px; line-height: 1.75; white-space: pre-wrap; overflow-wrap: anywhere; margin: 12px 0; }
label { display: inline-flex; align-items: center; gap: 8px; margin: 8px 16px 8px 0; flex-wrap: wrap; }
select, button { font: inherit; color: var(--ui-ink); background: var(--ui-raised); border: 1px solid var(--ui-line); border-radius: 10px; min-height: 44px; padding: 8px 12px; max-width: 100%; }
button { margin: 6px 8px 6px 0; cursor: pointer; } button:disabled { opacity: .55; cursor: default; }
select:focus-visible, button:focus-visible { outline: 2px solid var(--ui-accent); outline-offset: 2px; }
p { line-height: 1.65; overflow-wrap: anywhere; } [role=alert] { color: var(--ui-danger); }
.fragment { padding: 8px 0; border-bottom: 1px solid var(--ui-line); } .fragment span { display: block; }
.preview { padding: 12px; border: 1px solid var(--ui-line); border-radius: 12px; }
.hash { overflow-wrap: anywhere; } audio { display: none; }
@media (max-width: 600px) { .expression-lab { padding: 12px; } .lab-section { padding: 12px; } h1 { font-size: 22px; } }
</style>
