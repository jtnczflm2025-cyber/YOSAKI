<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RealtimeOutputPlayer, type RealtimeMetric, type RealtimePlaybackState,
  type RelayObservation } from './realtimeOutput'
import { voiceApi, wsUnavailableText } from './voiceApi'

// The server resolves the saved message, voice, model, and provider address.
const props = defineProps<{messageId:number; conversationId:number; characterId:number;
  dailyCallId?:string;dailyTurnId?:string;autoStart?:boolean}>()
const emit = defineEmits<{play:[];'playback-state':[state:'playing'|'ended'|'failed'|'stopped', reason?:string]}>()
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
let failureReported = false

function reportFailure(reason:string) {
  if (failureReported) return
  failureReported = true
  detail.value = reason
  if (props.dailyCallId) emit('playback-state', 'failed', reason)
}

const busy = computed(() => starting.value || ['connecting','waiting','playing'].includes(state.value))
const player = new RealtimeOutputPlayer(
  value => {
    state.value = value
    if (value === 'completed') detail.value = '音频接收和本地播放均已结束。'
    if (value === 'unknown') detail.value = 'WS 连接中断，合成结果未知。'
    if (props.dailyCallId && value === 'completed') emit('playback-state','ended')
    if (props.dailyCallId && (value === 'failed' || value === 'unknown'))
      reportFailure(detail.value)
  }, undefined,
  (metric:RealtimeMetric) => {
    if (metric.name === 'first_audio_received') firstAudioMs.value = metric.client_ms
    if (metric.name === 'first_playback_started') {
      if (clickAt !== null) startToPlaybackMs.value = metric.client_ms - clickAt
      if (props.dailyCallId) emit('playback-state','playing')
    }
    if (metric.name === 'receive_completed') receiveEndMs.value = metric.client_ms
    if (metric.name === 'playback_ended') playbackEndMs.value = metric.client_ms
  },
  diagnostic => { detail.value = `播放器停止：${diagnostic.code}；待播 ${diagnostic.pending_audio_bytes} 字节。` },
  (event:RelayObservation) => {
    if (event.type === 'audio' && event.text_dispatch_to_first_audio_ms !== undefined)
      serverDispatchToAudioMs.value = event.text_dispatch_to_first_audio_ms
    if (event.type === 'completed') detail.value = '接收完成，正在播完已收到的音频。'
    if (event.type === 'input_rejected') detail.value = wsUnavailableText(event.reason)
    if (event.type === 'failed') detail.value =
      event.request_outcome === 'unknown' ? 'WS 合成结果未知。' : 'WS 播放失败。'
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
  failureReported = false
  clickAt = performance.now()
  resetMetrics()
  starting.value = true
  detail.value = '正在检查 WS 播放条件…'
  emit('play')
  try {
    await closing
    if (epoch !== scope) return
    await player.close()
    if (epoch !== scope) return
    if (props.dailyCallId && props.dailyTurnId) {
      const query = new URLSearchParams({message_id:String(props.messageId),
        conversation_id:String(props.conversationId), character_id:String(props.characterId),
        call_id:props.dailyCallId, turn_id:props.dailyTurnId})
      try { await voiceApi('/realtime/daily-preview?' + query) }
      catch (error) {
        if (epoch === scope) reportFailure(wsUnavailableText(error instanceof Error ? error.message : null))
        return
      }
      if (epoch !== scope) return
    }
    detail.value = '正在连接 WS 播放服务…'
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    await player.connect(`${protocol}//${location.host}/api/voice/realtime/output`)
    if (epoch !== scope) { await player.close(); return }
    await player.startSavedMessage(props.messageId, props.conversationId,
      props.characterId, props.dailyCallId, props.dailyTurnId)
    if (epoch !== scope) { await player.close(); return }
    detail.value = '已提交保存消息标识；等待服务端校验和输出。'
  } catch {
    if (epoch === scope) {
      state.value = 'failed'
      reportFailure('WS 连接或播放启动失败。')
    }
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
function stopFromButton() {
  if (props.dailyCallId) emit('playback-state','stopped')
  else stop()
}
watch(() => [props.messageId, props.conversationId, props.characterId], leave)
watch(() => props.autoStart, value => { if(value) void start() }, {immediate:true})
onMounted(() => window.addEventListener('pagehide', leave))
onBeforeUnmount(() => { window.removeEventListener('pagehide', leave); leave() })
</script>

<template>
  <div class="voice-v4-bound-output" data-v4-bound-output>
    <button v-if="!dailyCallId" class="message-speak" :disabled="busy" @click="start">{{busy ? '输出中…' : '实时输出（隔离）'}}</button>
    <button v-if="busy" class="message-speak" @click="stopFromButton">停止</button>
    <span role="status">{{detail}}</span>
    <small>服务端文本发送尝试 → 首音频接收（不含 relay→浏览器传输）：{{serverDispatchToAudioMs === null ? '未测' : serverDispatchToAudioMs.toFixed(1)+' ms'}}</small>
    <small>用户启动 → 首个非零样本进入播放链（浏览器同一时钟，软件耗时）：{{startToPlaybackMs === null ? '未测' : startToPlaybackMs.toFixed(1)+' ms'}}</small>
    <span hidden data-v4-first-audio>{{firstAudioMs}}</span>
    <span hidden data-v4-receive-end>{{receiveEndMs}}</span>
    <span hidden data-v4-playback-end>{{playbackEndMs}}</span>
  </div>
</template>
