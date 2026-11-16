<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RealtimeOutputPlayer, type RealtimeMetric, type RealtimePlaybackState,
  type RelayObservation } from './realtimeOutput'

// This panel is compiled into the isolated product rehearsal only. It never
// accepts arbitrary text, voice IDs, provider URLs, or credentials from the UI.
const props = defineProps<{characterId:number; conversationId:number|null}>()
type Sample = 'normal'|'error'|'remote_close'|'disconnect'|'late'
const sample = ref<Sample>('normal')
const state = ref<RealtimePlaybackState>('idle')
const starting = ref(false)
const detail = ref('仅连接本机假供应商；不会调用 ElevenLabs。')
const receiveComplete = ref(false)
const browserFirstAudio = ref<number|null>(null)
const browserFirstPlayback = ref<number|null>(null)
const serverDispatchToAudio = ref<number|null>(null)
const playbackEnd = ref<number|null>(null)
const userStartToPlayback = ref<number|null>(null)
const lastClose = ref<{origin:string; code:number|null}|null>(null)
let scopeEpoch = 0
let closing: Promise<void> = Promise.resolve()
let userStartAt: number|null = null

const firstAudioLatency = computed(() => serverDispatchToAudio.value)
const firstPlaybackLatency = computed(() =>
  serverDispatchToAudio.value !== null && browserFirstAudio.value !== null &&
  browserFirstPlayback.value !== null
    ? serverDispatchToAudio.value + browserFirstPlayback.value - browserFirstAudio.value : null)
const busy = computed(() => starting.value || ['connecting','waiting','playing'].includes(state.value))

const player = new RealtimeOutputPlayer(
  value => {
    state.value = value
    if (value === 'unknown') detail.value = '传输断开，输出结果未知；旧音频已清空，不会自动重发。'
    if (value === 'completed') detail.value = '接收和播放均已结束。'
  }, undefined,
  (metric:RealtimeMetric) => {
    if (metric.name === 'first_audio_received') browserFirstAudio.value = metric.client_ms
    if (metric.name === 'first_playback_started') {
      browserFirstPlayback.value = metric.client_ms
      if (userStartAt !== null) userStartToPlayback.value = metric.client_ms - userStartAt
    }
    if (metric.name === 'playback_ended') playbackEnd.value = metric.client_ms
  },
  diagnostic => { detail.value = `播放器停止：${diagnostic.code}；待播 ${diagnostic.pending_audio_bytes} 字节。` },
  (event:RelayObservation) => {
    if (event.type === 'transport_closed' && event.close_origin)
      lastClose.value = {origin:event.close_origin, code:event.remote_close_code ?? null}
    if (event.type === 'audio' && event.text_dispatch_to_first_audio_ms !== undefined)
      serverDispatchToAudio.value = event.text_dispatch_to_first_audio_ms
    if (event.type === 'completed') {
      receiveComplete.value = true
      detail.value = '供应商接收已结束，正在播放剩余的本地队列。'
    }
    if (event.type === 'failed') {
      const origin = event.close_origin ?? lastClose.value?.origin
      const close = origin === 'remote_close_frame'
        ? `远端关闭帧 ${event.remote_close_code ?? lastClose.value?.code ?? '未知码'}`
        : origin === 'transport_lost' ? '无关闭帧传输中断' : '未确认关闭原因'
      detail.value = `输出失败：${event.error_category ?? 'unknown'}；${close}；派发结果 ${event.request_outcome ?? 'unknown'}。不会自动重试。`
    }
  },
)

function clearTimings() {
  receiveComplete.value = false
  browserFirstAudio.value = null
  browserFirstPlayback.value = null
  serverDispatchToAudio.value = null
  playbackEnd.value = null
  userStartToPlayback.value = null
  lastClose.value = null
}
async function start() {
  if (busy.value) return
  userStartAt = performance.now()
  const epoch = ++scopeEpoch
  starting.value = true
  clearTimings()
  detail.value = '正在连接本机隔离 relay…'
  try {
    await closing
    if (epoch !== scopeEpoch) return
    if (player.getState() === 'idle' || player.getState() === 'stopped' ||
        player.getState() === 'completed' || player.getState() === 'failed' ||
        player.getState() === 'unknown') {
      try { await player.connect('ws://127.0.0.1:18767/ws/output') }
      catch (error) {
        if (!(error instanceof Error) || error.message !== 'already_connected') throw error
      }
    }
    if (epoch !== scopeEpoch) { await player.close(); return }
    await player.start(sample.value)
    if (epoch !== scopeEpoch) { await player.close(); return }
    detail.value = '文本仅发送一次；等待本机假供应商音频。'
  } catch {
    if (epoch === scopeEpoch) {
      state.value = 'failed'
      detail.value = '本机输出入口不可用；没有自动补连或重发。'
    }
  } finally { if (epoch === scopeEpoch) starting.value = false }
}
function stop() {
  scopeEpoch++
  starting.value = false
  player.stop()
  detail.value = '用户已停止，待播队列已清空。'
}
function leave() {
  scopeEpoch++
  starting.value = false
  closing = player.close()
  detail.value = '角色、会话或页面已切换，旧输出已隔离。'
}
watch(() => [props.characterId, props.conversationId], leave)
onMounted(() => window.addEventListener('pagehide', leave))
onBeforeUnmount(() => { window.removeEventListener('pagehide', leave); leave() })
</script>

<template>
  <section class="voice-section" data-v4-product-output>
    <h3>实时输出 · 隔离验收</h3>
    <p>仅播放冻结本地样本；不接麦克风、识别、聊天模型或真实供应商。</p>
    <label>本地场景
      <select v-model="sample" :disabled="busy" aria-label="本地场景">
        <option value="normal">已有音频完整播放</option>
        <option value="error">模拟应用错误</option>
        <option value="remote_close">模拟远端关闭</option>
        <option value="disconnect">模拟无关闭帧断线</option>
        <option value="late">模拟停止后迟到音频</option>
      </select>
    </label>
    <div class="voice-actions">
      <button class="primary-btn" :disabled="busy" @click="start">启动本地输出</button>
      <button class="secondary-btn" :disabled="!busy" @click="stop">停止并清空队列</button>
    </div>
    <p role="status">状态：{{state}} · {{detail}}</p>
    <p v-if="receiveComplete && state==='playing'">音频接收已结束，正在播完缓存。</p>
    <p>服务端文本发送尝试 → 首音频接收（不含 relay→浏览器传输）：{{firstAudioLatency === null ? '未测' : firstAudioLatency.toFixed(1)+' ms'}}</p>
    <p>分段估算至首次进入播放链（不含 relay→浏览器传输）：{{firstPlaybackLatency === null ? '未测' : firstPlaybackLatency.toFixed(1)+' ms'}}</p>
    <p>用户启动 → 首个非零样本进入播放链（浏览器同一时钟，软件耗时）：{{userStartToPlayback === null ? '未测' : userStartToPlayback.toFixed(1)+' ms'}}</p>
    <small>跨进程时间戳不直接相减；软件播放记录不证明人耳听见。</small>
    <span hidden data-v4-playback-ended>{{playbackEnd}}</span>
  </section>
</template>
