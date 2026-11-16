<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
// @ts-expect-error TS5097: Vite must load this source, not the stale emitted .js sibling.
import { RealtimeOutputPlayer, type DynamicChatEvent, type RealtimeMetric, type RealtimePlaybackState } from './realtimeOutput.ts'

const props = withDefaults(defineProps<{characterId:number; conversationId:number; candidate?:boolean}>(),
  {candidate:false})
const state = ref<RealtimePlaybackState>('idle')
const detail = ref('隔离假聊天与假语音；不会连接真实模型或供应商。')
const publicText = ref('')
const runId = ref('')
const candidateKind = ref<'loopback_fake'|'real_approved'|''>('')
const modelPreview = ref<Record<string,unknown>|null>(null)
const messageId = ref<number|null>(null)
const generationStatus = ref('')
const modelStatus = ref('')
const displayStatus = ref('')
const segments = ref<number|null>(null)
const connections = ref<number|null>(null)
const firstPlaybackMs = ref<number|null>(null)
const receiveDoneMs = ref<number|null>(null)
const playbackDoneMs = ref<number|null>(null)
const startedAt = ref<number|null>(null)
const starting = ref(false)
const chatRunning = ref(false)
let scope = 0
let previewScope = 0
let closing:Promise<void> = Promise.resolve()

const busy = computed(() => starting.value ||
  ['connecting','waiting','playing'].includes(state.value))

const player = new RealtimeOutputPlayer(
  value => {
    state.value = value
    if (value === 'completed') detail.value = '整轮音频已接收并播完。'
    if (value === 'unknown') detail.value = '连接中断；已尝试派发的文字不会重发。'
  }, undefined,
  (metric:RealtimeMetric) => {
    if (metric.name === 'first_playback_started' && startedAt.value !== null)
      firstPlaybackMs.value = metric.client_ms - startedAt.value
    if (metric.name === 'receive_completed') receiveDoneMs.value = metric.client_ms
    if (metric.name === 'playback_ended') playbackDoneMs.value = metric.client_ms
  },
  diagnostic => { detail.value = `播放器停止：${diagnostic.code}` },
  event => {
    if (event.type === 'failed') detail.value = `语音输出失败：${event.error_category ?? 'unknown'}`
    if (event.type === 'input_rejected') {
      chatRunning.value = false
      runId.value = ''
      candidateKind.value = ''
      detail.value = `启动拒绝：${event.reason ?? 'unknown'}`
    }
  },
  (event:DynamicChatEvent) => {
    if (event.type === 'chat_text_delta') publicText.value += event.text ?? ''
    if (event.type === 'chat_message_complete') {
      chatRunning.value = false
      messageId.value = event.message_id ?? null
      generationStatus.value = event.generation_status ?? ''
      modelStatus.value = event.model_status ?? ''
      displayStatus.value = event.display_status ?? ''
      if (displayStatus.value === 'restrict') {
        publicText.value = ''
        player.stopVoiceOnly()
        detail.value = '此回答未获可展示确认；文字与语音均未发布。'
      }
    }
    if (event.type === 'chat_failed') {
      chatRunning.value = false
      detail.value = '聊天保存失败；语音已停止。'
    }
    if (event.type === 'turn_summary') {
      segments.value = event.segments ?? event.tts_segments ?? null
      connections.value = event.connections ?? event.tts_connections ?? null
    }
  },
)

async function loadStatus(expectedRunId?:string):Promise<boolean> {
  const requestScope = ++previewScope
  runId.value = ''
  candidateKind.value = ''
  modelPreview.value = null
  try {
    const path = props.candidate
      ? `/api/voice/realtime/chat-candidate/preview?character_id=${props.characterId}&conversation_id=${props.conversationId}`
      : '/api/voice/realtime/chat-isolated/status'
    const response = await fetch(path,
      {credentials:'same-origin'})
    if (!response.ok) {
      const error = await response.json().catch(() => ({}))
      if (requestScope === previewScope)
        detail.value = `候选预览未就绪：HTTP ${response.status} · ${error.detail ?? '未知原因'}`
      return false
    }
    const value = await response.json()
    if (requestScope !== previewScope) return false
    if (props.candidate) {
      const kind = value.receipt_kind
      const validKind = kind === 'loopback_fake' || kind === 'real_approved'
      const validRun = typeof value.run_id === 'string' &&
        /^[a-z0-9][a-z0-9-]{7,79}$/.test(value.run_id)
      const validApproval = (kind === 'real_approved') ===
        value.approved_for_real_execution
      const validPreview = value.model_request !== null &&
        typeof value.model_request === 'object' && !Array.isArray(value.model_request)
      if (!validKind || !validRun || !validApproval || !validPreview ||
          (expectedRunId && value.run_id !== expectedRunId)) {
        detail.value = '候选预览授权不完整或已变化；未启动。'
        return false
      }
      runId.value = value.run_id
      candidateKind.value = kind
      modelPreview.value = value.model_request
      detail.value = kind === 'real_approved'
        ? '一次性真实候选已通过后端预览；启动前仍将重新核验，派发由后端收据控制。'
        : '隔离假模型与假语音已就绪。'
      return true
    }
    if (value.mock_only === true && value.enabled === true && typeof value.run_id === 'string') {
      runId.value = value.run_id
      return true
    }
    detail.value = '隔离入口状态不符合预期；未启动。'
  } catch { detail.value = '隔离入口不可用；未启动。' }
  return false
}

async function start() {
  if (busy.value || chatRunning.value || !runId.value) return
  const selectedRunId = runId.value
  const epoch = ++scope
  starting.value = true
  chatRunning.value = true
  publicText.value = ''
  messageId.value = null
  generationStatus.value = ''
  modelStatus.value = ''
  displayStatus.value = ''
  segments.value = null
  connections.value = null
  firstPlaybackMs.value = null
  receiveDoneMs.value = null
  playbackDoneMs.value = null
  detail.value = '连接隔离产品路由…'
  try {
    if (props.candidate && !await loadStatus(selectedRunId)) {
      chatRunning.value = false
      state.value = 'failed'
      return
    }
    await closing
    if (epoch !== scope) return
    await player.close()
    if (epoch !== scope) return
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const endpoint = props.candidate ? 'chat-candidate' : 'chat-isolated'
    await player.connect(`${protocol}//${location.host}/api/voice/realtime/${endpoint}`)
    if (epoch !== scope) { await player.close(); return }
    startedAt.value = performance.now()
    await player.startChatTurn(selectedRunId, props.conversationId, props.characterId)
    if (candidateKind.value === 'real_approved') runId.value = ''
    detail.value = props.candidate && candidateKind.value === 'real_approved'
      ? '一次性真实候选已提交；由后端核验并占用收据。'
      : '隔离候选入口正在模拟输出；语音旁路消费公开正文。'
  } catch {
    if (epoch === scope) { chatRunning.value = false; state.value = 'failed'; detail.value = '隔离启动失败；不会自动重试。' }
  } finally { if (epoch === scope) starting.value = false }
}

function stopVoice() {
  scope++
  player.stopVoiceOnly()
  detail.value = '已停止语音；聊天文字继续。'
}

function stopGeneration() {
  scope++
  player.stopChatGeneration()
  detail.value = '已请求停止生成，并同步停止语音。'
}

function leave() {
  scope++
  chatRunning.value = false
  player.stop()
  closing = player.close()
}

watch(() => [props.characterId, props.conversationId], leave)
onMounted(() => { window.addEventListener('pagehide', leave); loadStatus() })
watch(() => [props.characterId, props.conversationId], () => { void loadStatus() })
onBeforeUnmount(() => { window.removeEventListener('pagehide', leave); leave() })
</script>

<template>
  <section class="voice-section" data-v4-dynamic-chat>
    <h3>{{candidate ? '动态聊天语音 · 隔离候选' : '动态聊天语音 · 隔离模拟'}}</h3>
    <p v-if="candidate">{{candidateKind === 'real_approved' ? '本次为一次性真实候选；后端将再次核验并原子占用收据。' : candidateKind === 'loopback_fake' ? '当前为隔离假模型与假语音；真实供应商仍关闭。' : '等待候选预览授权；未派发任何请求。'}}</p>
    <p v-else>固定假聊天流与本地假语音；日常功能及真实供应商仍关闭。</p>
    <details v-if="candidate && modelPreview"><summary>查看冻结的完整模型请求</summary><pre>{{JSON.stringify(modelPreview,null,2)}}</pre></details>
    <div class="voice-actions">
      <button :disabled="busy || chatRunning || !runId" @click="start">{{candidateKind === 'real_approved' ? '启动一次性真实候选' : '启动隔离模拟'}}</button>
      <button :disabled="!busy" @click="stopVoice">停止语音</button>
      <button :disabled="!chatRunning" @click="stopGeneration">停止生成</button>
    </div>
    <p role="status">{{detail}}</p>
    <p data-dynamic-public-text>{{publicText}}</p>
    <small>保存消息：{{messageId ?? '待定'}}；模型：{{modelStatus || '进行中'}}；展示：{{displayStatus || '待确认'}}；生成状态：{{generationStatus || '进行中'}}；片段／连接：{{segments ?? '—'}}／{{connections ?? '—'}}</small>
    <small v-if="candidate && displayStatus === 'allow'">生成完成且确认可展示后，才开始语音合成播放；这不是边生成边播。</small>
    <small>启动至首个非零样本进入播放链：{{firstPlaybackMs === null ? '未测' : firstPlaybackMs.toFixed(1)+' ms'}}</small>
    <span hidden data-dynamic-first-playback>{{firstPlaybackMs}}</span>
    <span hidden data-dynamic-receive-end>{{receiveDoneMs}}</span>
    <span hidden data-dynamic-playback-end>{{playbackDoneMs}}</span>
  </section>
</template>
