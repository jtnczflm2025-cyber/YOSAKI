<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RealtimeOutputPlayer, type RealtimeMetric, type RealtimePlaybackState,
  type RelayObservation } from './realtimeOutput'

// The server resolves the saved message, voice, model, and provider address.
const props = defineProps<{messageId:number; conversationId:number; characterId:number}>()
const emit = defineEmits<{play:[]}>()
const state = ref<RealtimePlaybackState>('idle')
const detail = ref('仅在隔离验收环境开放。')
const startToPlaybackMs = ref<number|null>(null)
const serverDispatchToAudioMs = ref<number|null>(null)
const firstAudioMs = ref<number|null>(null)
const receiveEndMs = ref<number|null>(null)
const playbackEndMs = ref<number|null>(null)
const starting = ref(false)
let clickAt: number|null = null
let scope = 0
let closing: Promise<void> = Promise.resolve()

const busy = computed(() => starting.value || ['connecting','waiting','playing'].includes(state.value))
const player = new RealtimeOutputPlayer(
  value => {
    state.value = value
    if (value === 'completed') detail.value = '音频接收和本地播放均已结束。'
    if (value === 'unknown') detail.value = '连接中断，文本结果未知；不会自动重发。'
  }, undefined,
  (metric:RealtimeMetric) => {
    if (metric.name === 'first_audio_received') firstAudioMs.value = metric.client_ms
    if (metric.name === 'first_playback_started' && clickAt !== null)
      startToPlaybackMs.value = metric.client_ms - clickAt
    if (metric.name === 'receive_completed') receiveEndMs.value = metric.client_ms
    if (metric.name === 'playback_ended') playbackEndMs.value = metric.client_ms
  },
  diagnostic => { detail.value = `播放器停止：${diagnostic.code}；待播 ${diagnostic.pending_audio_bytes} 字节。` },
  (event:RelayObservation) => {
    if (event.type === 'audio' && event.text_dispatch_to_first_audio_ms !== undefined)
      serverDispatchToAudioMs.value = event.text_dispatch_to_first_audio_ms
    if (event.type === 'completed') detail.value = '接收完成，正在播完已收到的音频。'
    if (event.type === 'input_rejected') detail.value = `启动被拒绝：${event.reason ?? 'unknown'}；未派发供应商。`
    if (event.type === 'failed') detail.value =
      `输出失败：${event.error_category ?? 'unknown'}；文本结果 ${event.request_outcome ?? 'unknown'}，不会重发。`
  },
)

function resetMetrics() {
  startToPlaybackMs.value = null
  serverDispatchToAudioMs.value = null
  firstAudioMs.value = null
  receiveEndMs.value = null
  playbackEndMs.value = null
}

async function start() {
  if (busy.value) return
  const epoch = ++scope
  clickAt = performance.now()
  resetMetrics()
  starting.value = true
  detail.value = '正在建立受控连接…'
  emit('play')
  try {
    await closing
    if (epoch !== scope) return
    await player.close()
    if (epoch !== scope) return
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    await player.connect(`${protocol}//${location.host}/api/voice/realtime/output`)
    if (epoch !== scope) { await player.close(); return }
    await player.startSavedMessage(props.messageId, props.conversationId, props.characterId)
    if (epoch !== scope) { await player.close(); return }
    detail.value = '已提交保存消息标识；等待服务端校验和输出。'
  } catch {
    if (epoch === scope) { state.value = 'failed'; detail.value = '连接或启动失败；不会自动补连。' }
  } finally { if (epoch === scope) starting.value = false }
}

function stop() {
  scope++
  starting.value = false
  player.stop()
  detail.value = '已停止并清空本地播放队列。'
  closing = player.close()
}

function leave() { stop() }
watch(() => [props.messageId, props.conversationId, props.characterId], leave)
onMounted(() => window.addEventListener('pagehide', leave))
onBeforeUnmount(() => { window.removeEventListener('pagehide', leave); leave() })
</script>

<template>
  <div class="voice-v4-bound-output" data-v4-bound-output>
    <button class="message-speak" :disabled="busy" @click="start">{{busy ? '输出中…' : '实时输出（隔离）'}}</button>
    <button v-if="busy" class="message-speak" @click="stop">停止</button>
    <span role="status">{{detail}}</span>
    <small>服务端文本发送尝试 → 首音频接收（不含 relay→浏览器传输）：{{serverDispatchToAudioMs === null ? '未测' : serverDispatchToAudioMs.toFixed(1)+' ms'}}</small>
    <small>用户启动 → 首个非零样本进入播放链（浏览器同一时钟，软件耗时）：{{startToPlaybackMs === null ? '未测' : startToPlaybackMs.toFixed(1)+' ms'}}</small>
    <span hidden data-v4-first-audio>{{firstAudioMs}}</span>
    <span hidden data-v4-receive-end>{{receiveEndMs}}</span>
    <span hidden data-v4-playback-end>{{playbackEndMs}}</span>
  </div>
</template>
