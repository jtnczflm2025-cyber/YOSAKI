<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { pcm16At16k, pcmBase64 } from './scribePcm'
import { acquireScribeMicrophone, mayStartScribe } from './scribeCapture'
import { ScribeTurnDetector } from './scribeTurnDetector'
import type { BargeCapturePort } from './localBargeIn'

const props = defineProps<{characterId:number;conversationId:number|null;cancelEpoch:number;
  disabled?:boolean;continuousMode?:boolean;callId?:string;startSignal?:number;
  bargeCapture?:BargeCapturePort|null;inputDeviceId?:string}>()
const emit = defineEmits<{committed:[text:string,turnId?:string];'running-change':[value:boolean];'status-change':[value:string];'capture-ready':[];'audio-commit':[browserMs:number, lastAudibleMs:number|null];failed:[code:string]}>()
const syntheticTestButton = import.meta.env.VITE_YOSAKI_SCRIBE_FAKE_TEST === '1'
const continuousFakeCapture = syntheticTestButton &&
  import.meta.env.VITE_YOSAKI_CONTINUOUS_FAKE_CAPTURE === '1'
const continuousFakeCaptureOnce = continuousFakeCapture &&
  import.meta.env.VITE_YOSAKI_CONTINUOUS_FAKE_CAPTURE_ONCE === '1'
function startContinuousCapture() {
  if (continuousFakeCaptureOnce) {
    const testWindow = window as Window & {__yosakiContinuousFakeStarts?:number}
    if ((testWindow.__yosakiContinuousFakeStarts ?? 0) > 0) return
    testWindow.__yosakiContinuousFakeStarts = 1
  }
  void start(continuousFakeCapture)
}
const dailyMode = import.meta.env.VITE_YOSAKI_SCRIBE_DAILY === '1'
type ScribeUsage = {enabled:boolean;service_enabled:boolean;limits:{attempts:number;connections:number;audio_seconds:number};
  remaining:{attempts:number;connections:number;audio_seconds:number};available_attempts:number;
  per_attempt_audio_seconds:number;reset_at:string}
const dailyUsage = ref<ScribeUsage|null>(null)
const dailyReady = computed(() => !dailyMode || Boolean(dailyUsage.value?.enabled &&
  dailyUsage.value.service_enabled && dailyUsage.value.available_attempts > 0))
const dailyHint = computed(() => {
  if (!dailyMode) return ''
  if (!dailyUsage.value) return '录音额度暂不可读取；麦克风保持关闭。'
  if (!dailyUsage.value.enabled) return 'Scribe 麦克风未开启；可在语音设置中主动开启。'
  const r = dailyUsage.value.remaining
  return `今日剩余 ${r.attempts} 次尝试、${r.connections} 条连接、${r.audio_seconds} 秒预留音频；按每次预留 ${dailyUsage.value.per_attempt_audio_seconds} 秒，当前最多还能录音 ${dailyUsage.value.available_attempts} 次。香港时间零点重置。`
})
async function refreshDailyUsage() {
  if (!dailyMode) return
  try {
    const response = await fetch('/api/voice/scribe-budget', {credentials:'same-origin'})
    dailyUsage.value = response.ok ? await response.json() as ScribeUsage : null
  } catch { dailyUsage.value = null }
  window.dispatchEvent(new Event('yosaki-scribe-usage-changed'))
}
onMounted(() => {
  void refreshDailyUsage().then(() => {
    if (props.continuousMode && props.startSignal) startContinuousCapture()
  })
  window.addEventListener('yosaki-scribe-budget-changed', refreshDailyUsage)
})
const status = ref('')
const partial = ref('')
watch([status,partial], () => emit('status-change', [status.value,partial.value].filter(Boolean).join(' ')))
const running = ref(false)
const microphoneAttempted = ref(false)
let socket: WebSocket|null = null
let stream: MediaStream|null = null
let audioContext: AudioContext|null = null
let source: MediaStreamAudioSourceNode|null = null
let fixtureSource: AudioBufferSourceNode|null = null
let processor: ScriptProcessorNode|null = null
let epoch = 0
let bytesSent = 0
let synthetic = false
let finishing = false
let attemptId = ''
let starting = false
let turnDetector: ScribeTurnDetector|null = null
let activeBargeCapture:BargeCapturePort|null = null
const captureReady = ref(false)

function releaseAudio() {
  activeBargeCapture?.close(); activeBargeCapture=null
  if (fixtureSource) { try { fixtureSource.stop() } catch { /* already ended */ }
    fixtureSource.disconnect(); fixtureSource = null }
  if (processor) { processor.onaudioprocess = null; processor.disconnect(); processor = null }
  source?.disconnect(); source = null
  stream?.getTracks().forEach(track => track.stop()); stream = null
  if (audioContext) void audioContext.close().catch(() => {}); audioContext = null
}
function cancel() {
  epoch++; turnDetector=null; releaseAudio()
  if (socket) { socket.close(); socket = null }
  finishing = false
  captureReady.value = false
  running.value = false; partial.value = ''
}
function stop() {
  if (socket?.readyState === WebSocket.OPEN)
    try { socket.send(JSON.stringify({type:'stop'})) } catch { /* closing */ }
  status.value = '识别已取消。'
  cancel()
  void refreshDailyUsage()
}
watch(running, value => emit('running-change', value))
watch(() => [props.characterId, props.conversationId, props.disabled], () => cancel())
watch(() => props.cancelEpoch, () => cancel(), {flush:'sync'})
watch(() => props.startSignal, value => {
  if (props.continuousMode && value) startContinuousCapture()
})
onBeforeUnmount(() => {
  window.removeEventListener('yosaki-scribe-budget-changed', refreshDailyUsage)
  cancel()
})

function forwardFrame(frame:Float32Array, sampleRate:number, token:number) {
  if(token!==epoch||socket?.readyState!==WebSocket.OPEN)return
  try {
    const chunk=pcm16At16k(frame,sampleRate)
    if(bytesSent+chunk.length>480000){status.value='识别已达到 15 秒上限，请结束识别。';finish();return}
    if(!chunk.length)throw new Error('empty_pcm')
    bytesSent+=chunk.length
    socket.send(JSON.stringify({type:'audio',pcm_base64:pcmBase64(chunk)}))
    if(props.continuousMode&&turnDetector){
      const decision=turnDetector.observe(frame,sampleRate)
      if(decision==='commit')finish()
      else if(decision==='no_speech'){
        status.value='没有检测到清晰语音；本轮已停止。'
        emit('failed','no_speech');cancel()
      }
    }
  }catch{status.value='麦克风音频格式无效。';if(props.continuousMode)emit('failed','audio_invalid');cancel()}
}
async function startMicrophone(token:number) {
  try {
    if (props.bargeCapture) {
      activeBargeCapture=props.bargeCapture
      if (!activeBargeCapture.active) throw new Error('barge_capture_expired')
      turnDetector=new ScribeTurnDetector()
      captureReady.value=true
      socket?.send(JSON.stringify({type:'capture',sample_rate:activeBargeCapture.sampleRate,channels:1}))
      if (!activeBargeCapture.subscribe((frame,rate)=>forwardFrame(frame,rate,token)))
        throw new Error('barge_capture_expired')
      status.value='插话已接续识别；说完后自动转写并发送。'
      emit('capture-ready')
      return
    }
    let acquired: MediaStream
    if (synthetic && syntheticTestButton) {
      const response = await fetch('/api/voice/scribe-draft/fixture.wav')
      if (!response.ok) throw new Error('fixture_unavailable')
      audioContext = new AudioContext()
      const buffer = await audioContext.decodeAudioData(await response.arrayBuffer())
      const destination = audioContext.createMediaStreamDestination()
      fixtureSource = audioContext.createBufferSource()
      fixtureSource.buffer = buffer
      fixtureSource.connect(destination)
      acquired = destination.stream
    } else if (stream) {
      acquired = stream
    } else {
      const requested = await acquireScribeMicrophone(
        constraints => navigator.mediaDevices.getUserMedia(constraints), () => token===epoch,
        props.inputDeviceId)
      if (!requested) return
      acquired = requested
    }
    if (token !== epoch) { acquired.getTracks().forEach(track => track.stop()); releaseAudio(); return }
    stream = acquired
    audioContext ||= new AudioContext()
    const resumed = await Promise.race([audioContext.resume().then(() => true),
      new Promise<boolean>(resolve => setTimeout(() => resolve(false), 2500))])
    if (token !== epoch) { releaseAudio(); return }
    if (!resumed || audioContext.state !== 'running') {
      status.value = '麦克风音频处理未运行。'; cancel(); return
    }
    source = audioContext.createMediaStreamSource(stream)
    processor = audioContext.createScriptProcessor(8192, 1, 1)
    processor.onaudioprocess = event => {
      event.outputBuffer.getChannelData(0).fill(0)
      forwardFrame(event.inputBuffer.getChannelData(0),audioContext!.sampleRate,token)
    }
    source.connect(processor)
    processor.connect(audioContext.destination)
    if (fixtureSource) {
      fixtureSource.onended = () => { if (token===epoch) setTimeout(() => {
        if (token===epoch) finish()
      }, 250) }
      fixtureSource.start()
    }
    captureReady.value = true
    turnDetector = props.continuousMode ? new ScribeTurnDetector() : null
    socket?.send(JSON.stringify({type:'capture', sample_rate:audioContext.sampleRate,
      channels:1}))
    status.value = props.continuousMode
      ? '正在听取；说完后会自动转写并发送，可点“说完了”提前结束。'
      : '正在录音；点击“结束并转写”后，文字只会进入输入框。'
    if (props.continuousMode) emit('capture-ready')
  } catch (error) {
    status.value = (error as Error)?.name === 'NotAllowedError'
      ? '麦克风权限未允许。' : '麦克风不可用或音频处理失败。'
    if (props.continuousMode) emit('failed','microphone_unavailable')
    cancel()
  }
}
async function start(useSynthetic=false) {
  if (starting) return
  if (!mayStartScribe(running.value, Boolean(props.disabled),
                      dailyMode ? false : microphoneAttempted.value,
                      useSynthetic)) return
  starting = true
  const startEpoch = epoch
  try {
  if (dailyMode) {
    await refreshDailyUsage()
    if (startEpoch !== epoch) return
    if (!dailyReady.value) { status.value='录音开关未开启或今日额度已用完；文字聊天仍可使用。'; if(props.continuousMode)emit('failed','scribe_daily_exhausted'); return }
  }
  if (!useSynthetic) microphoneAttempted.value = true
  cancel(); const token=epoch; synthetic=useSynthetic; bytesSent=0; finishing=false
  attemptId=crypto.randomUUID().replace(/-/g, '')
  running.value=true
  if (!useSynthetic && !props.bargeCapture) {
    status.value='正在申请麦克风；此时尚未连接识别服务。'
    try {
      const acquired = await acquireScribeMicrophone(
        constraints => navigator.mediaDevices.getUserMedia(constraints), () => token===epoch,
        props.inputDeviceId)
      if (!acquired) return
      stream=acquired
    } catch (error) {
      status.value=(error as Error)?.name==='NotAllowedError'
        ? '麦克风权限未允许；没有连接识别服务。'
        : '麦克风不可用；没有连接识别服务。'
      if (props.continuousMode) emit('failed','microphone_unavailable')
      cancel(); return
    }
  }
  if (token!==epoch) return
  const scheme=location.protocol==='https:'?'wss:':'ws:'
  try { socket = new WebSocket(`${scheme}//${location.host}/api/voice/scribe-draft/ws`) }
  catch { status.value='识别连接无法建立。'; if(props.continuousMode)emit('failed','connection_failed'); cancel(); return }
  status.value=dailyMode?'正在连接识别服务…':'正在连接隔离识别服务…'
  socket.onopen=() => {
    if(token!==epoch) return
    socket?.send(JSON.stringify({type:'start',character_id:props.characterId,
      language_code:'auto',attempt_id:attemptId,
      ...(props.continuousMode ? {call_id:props.callId} : {})}))
  }
  socket.onmessage=event => {
    if (token!==epoch) return
    let item: {type?:string;text?:string;code?:string;attempt_id?:string;call_turn_id?:string}
    try { item=JSON.parse(event.data) } catch { status.value='识别事件格式错误。'; cancel(); return }
    if (item.type==='ready') {
      if (item.attempt_id !== attemptId) { status.value='识别轮次不匹配。'; cancel(); return }
      void startMicrophone(token)
    } else if (item.type==='partial' && typeof item.text==='string') partial.value=item.text
    else if (item.type==='committed' && typeof item.text==='string') {
      const text=item.text.trim()
      if (props.continuousMode) {
        if (text && item.call_turn_id) emit('committed',text,item.call_turn_id)
        else emit('failed', text ? 'call_turn_missing' : 'provider_empty_transcript')
      } else if (text) emit('committed',text)
      status.value=text?(props.continuousMode?'最终转写已交给聊天。':'转写已放入输入框；请核对后发送。'):'供应商返回空转写。'
      cancel()
      void refreshDailyUsage()
    } else if (item.type==='error') {
      const reasons: Record<string,string> = {
        empty_audio:'本地没有采集到音频。', audio_silent:'本地音频为静音。',
        commit_timeout:'供应商未在期限内返回最终转写。',
        scribe_timeout:'识别连接已超时。', provider_disconnected:'供应商连接中断。',
        scribe_user_disabled:'麦克风开关已关闭。',
        scribe_daily_budget_exhausted:'今日录音额度已用完。',
        scribe_attempt_reused:'本次录音已使用，请重新点击开始录音。',
        scribe_daily_schema_missing:'日常录音功能尚未完成启用。',
        scribe_attempt_budget_exceeded:'本次实验录音额度已用完。',
        provider_empty_transcript:'供应商没有识别出文字，请检查录音环境。',
        session_start_invalid:'识别服务没有正常开始。',
        auth_error:'识别服务鉴权失败。',
        quota_exceeded:'供应商识别额度不足。',
        rate_limited:'识别服务暂时限流。',
        insufficient_audio_activity:'录音中的有效语音不足。',
      }
      status.value=reasons[item.code||''] || `识别已停止：${item.code||'未知错误'}`
      if (props.continuousMode) emit('failed',item.code||'unknown')
      cancel()
      void refreshDailyUsage()
    }
  }
  socket.onerror=() => { if(token===epoch){status.value='识别连接出错，没有自动重试。';if(props.continuousMode)emit('failed','connection_error');cancel();void refreshDailyUsage()} }
  socket.onclose=() => { if(token===epoch){status.value='识别连接已结束，未提交文字。';if(props.continuousMode)emit('failed','connection_closed');cancel();void refreshDailyUsage()} }
  } finally { starting = false }
}
function finish() {
  if (!running.value || finishing || socket?.readyState!==WebSocket.OPEN) return
  if (!captureReady.value) { status.value='麦克风尚未开始采音。'; cancel(); return }
  if (bytesSent===0) { status.value='本地没有采集到音频，请检查麦克风。'; cancel(); return }
  if (!synthetic && bytesSent < 64000) {
    status.value='录音不足约两秒，请稍后再结束并转写。'; return
  }
  finishing=true
  releaseAudio()
  try { socket.send(JSON.stringify({type:'commit'})); emit('audio-commit',performance.now(),turnDetector?.lastAudibleMs ?? null) }
  catch { status.value='识别连接已结束，无法提交转写。'; cancel(); return }
  status.value='正在确认最终转写…'
}
defineExpose({ finish, stop })
</script>

<template>
  <div class="scribe-draft-isolated">
    <button v-if="!running && !continuousMode" type="button" :disabled="disabled || (dailyMode ? !dailyReady : microphoneAttempted)" @click="start(false)">{{dailyMode?'开始录音':'Scribe 麦克风（实验）'}}</button>
    <button v-if="!running && syntheticTestButton" type="button" :disabled="disabled || (dailyMode && !dailyReady)" @click="start(true)">本地音频夹具模拟</button>
    <button v-if="running" type="button" :disabled="!captureReady" @click="finish">{{continuousMode?'说完了':'结束并转写'}}</button>
    <button v-if="running" type="button" @click="stop">取消识别</button>
    <span role="status">{{status}} {{partial}}</span>
    <span v-if="dailyMode">{{dailyHint}} {{continuousMode?'连续通话已由你开启，最终转写会自动发送；临时转写不会发送。':'录音只会填入草稿；请编辑后自行发送，不会自动重录或重试。'}}</span>
    <span v-else-if="microphoneAttempted && !running">本次麦克风尝试已结束；不会自动重试。</span>
  </div>
</template>
