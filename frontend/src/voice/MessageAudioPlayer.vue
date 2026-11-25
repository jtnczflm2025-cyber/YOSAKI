<script setup lang="ts">
import AIMessageAudio from './AIMessageAudio.vue'
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { voiceApi, type VoiceStatus } from './voiceApi'
import { voiceAudioPlayer, type PlaybackState } from './voiceAudioPlayer'

const props = defineProps<{messageId:number;followingMessageIds?:number[];autoStart?:boolean;
  voiceCallId?:string;voiceCallTurnId?:string}>()
const emit = defineEmits<{play:[];'budget-changed':[];'playback-state':[value:PlaybackState]}>()
type Mode = 'none'|'thoughtful'|'auto'|'ai_expression'
type Preview = {segments:string[];safe_segments:string[];mode:Mode;effective_mode?:Mode;expression_style?:string;expression_tag:string|null;expression_planner_version:string|null;skipped_reasons:string[];characters:number;non_whitespace_characters:number;requests:number;reference_language:string;language_code:string|null;language_reason:string;model_id?:string;output_format?:string;profile_revision?:number;voice_id_masked?:string;effective_parameters?:{settings:string};preview_sha256:string;billing:string}
type Job = {job_id:string;status:string;error_code:string|null;failure_phase?:string|null;failed_segment?:number|null;audio:{binding_id:number;url:string;sequence:number}[]}
const enabled = ref(false)
const expressionAvailable = ref(false)
const automaticAvailable = ref(false)
const aiAvailable = ref(false)
const manuallySelected = ref(false)
const mode = ref<Mode>('none')
const preview = ref<Preview|null>(null)
const job = ref<Job|null>(null)
const cached = ref<Job['audio']>([])
const state = ref<PlaybackState|'idle'|'generating'>('idle')
const error = ref('')
let disposed = false
let pollTimer: ReturnType<typeof setTimeout>|null = null
let runEpoch = 0
let statusPromise: Promise<void>|null = null
let autoStartAttempted = false
let automaticJob = false
function reportBudgetIfAutomatic() {
  if (automaticJob) { automaticJob = false; emit('budget-changed') }
}

async function refreshStatus() {
  try {
    const status = await voiceApi<VoiceStatus>('/status')
    enabled.value = status.enabled
    expressionAvailable.value = Boolean(status.expression_enabled)
    automaticAvailable.value = Boolean(status.automatic_expression_ready)
  } catch { enabled.value = false; expressionAvailable.value = false; automaticAvailable.value = false }
  try {
    const ai=await voiceApi<{available:boolean;enabled:boolean}>('/ai-http/settings')
    aiAvailable.value=ai.available && !props.voiceCallId
    if (aiAvailable.value && ai.enabled && !manuallySelected.value) mode.value='ai_expression'
    else if (mode.value==='ai_expression' && !ai.enabled) mode.value='none'
  } catch { aiAvailable.value=false }
  if (disposed) return
  if (!expressionAvailable.value && mode.value !== 'none' && mode.value !== 'ai_expression') mode.value = 'none'
  if (!enabled.value) voiceAudioPlayer.stop(props.messageId)
  await loadCached()
}
async function loadCached() {
  const selected = mode.value
  const epoch = runEpoch
  if (!enabled.value || selected==='ai_expression') { cached.value = []; return }
  try {
    const result = await voiceApi<{audio:Job['audio']}>(`/messages/${props.messageId}/audio?mode=${selected}`)
    if (!disposed && selected === mode.value && epoch === runEpoch) cached.value = result.audio
  } catch {
    if (!disposed && selected === mode.value && epoch === runEpoch) cached.value = []
  }
}
function clearPoll() { if (pollTimer) clearTimeout(pollTimer); pollTimer = null }
watch(mode, () => {
  runEpoch++; clearPoll(); voiceAudioPlayer.stop(props.messageId)
  if (job.value && ['queued','generating'].includes(job.value.status)) {
    void voiceApi(`/jobs/${job.value.job_id}`, 'DELETE').catch(() => {})
  }
  job.value = null; preview.value = null; cached.value = []; state.value = 'idle'; error.value = ''
  void loadCached()
})
onMounted(() => {
  statusPromise = refreshStatus()
  void statusPromise.then(() => { if (props.autoStart) void autoStartOnce() })
  window.addEventListener('yosaki-voice-config-changed', refreshStatus)
})
watch(() => props.autoStart, value => {
  if (value) void autoStartOnce()
  else if (autoStartAttempted) void stop()
})
onBeforeUnmount(() => {
  disposed = true; runEpoch++; clearPoll(); voiceAudioPlayer.stop(props.messageId)
  if (job.value && ['queued','generating'].includes(job.value.status)) {
    void voiceApi(`/jobs/${job.value.job_id}`, 'DELETE').catch(() => {})
  }
  window.removeEventListener('yosaki-voice-config-changed', refreshStatus)
})
async function loadPreview() {
  error.value = ''
  const selected = mode.value
  const epoch = runEpoch
  try {
    const result = await voiceApi<Partial<Preview>>(`/messages/${props.messageId}/speech-preview?mode=${selected}`)
    if (disposed || selected !== mode.value || epoch !== runEpoch) return
    if (!Object.prototype.hasOwnProperty.call(result, 'language_code') ||
        !result.reference_language || !result.language_reason ||
        !Number.isInteger(result.non_whitespace_characters) ||
        (selected === 'thoughtful' &&
          (result.mode !== selected || !result.expression_tag ||
           !result.model_id || !result.output_format))) {
      throw new Error('当前朗读后端尚未启用新版语言规划，无法确认实际发送语言；请等待受控切换。')
    }
    preview.value = { ...result, mode:selected,
      expression_tag:result.expression_tag ?? null,
      expression_planner_version:result.expression_planner_version ?? null,
      safe_segments:result.safe_segments ?? result.segments ?? [] } as Preview
  }
  catch (e) {
    if (!disposed && selected === mode.value && epoch === runEpoch)
      error.value = e instanceof Error ? e.message : '无法预览朗读文字'
  }
}
async function autoStartOnce() {
  if (autoStartAttempted || disposed) return
  if (statusPromise) await statusPromise
  if (autoStartAttempted || disposed || !props.autoStart) return
  if (!enabled.value) { emit('playback-state','failed'); return }
  autoStartAttempted = true
  if (mode.value==='ai_expression') return
  if (automaticAvailable.value && !manuallySelected.value) {
    mode.value = 'auto'
    await nextTick()
    await loadCached()
  }
  if (disposed || !props.autoStart) return
  if (cached.value.length && !props.voiceCallId) { playCached(); return }
  await loadPreview()
  if (disposed || !props.autoStart) return
  if (!preview.value) { emit('playback-state','failed'); return }
  // The server checks the selected owner/day and session limits atomically.
  await confirmGenerate(true)
}
async function confirmGenerate(automatic = false) {
  if (!preview.value) return
  if (preview.value.mode !== mode.value) { preview.value = null; error.value = '表达模式已变化，请重新预览'; return }
  const token = ++runEpoch
  automaticJob = automatic
  error.value = ''; state.value = 'generating'
  try {
    const payload = expressionAvailable.value
      ? {preview_sha256:preview.value.preview_sha256, mode:preview.value.mode}
      : {preview_sha256:preview.value.preview_sha256}
    const boundPayload = automatic && props.voiceCallId && props.voiceCallTurnId
      ? {...payload, voice_call_id:props.voiceCallId,
        voice_call_turn_id:props.voiceCallTurnId} : payload
    const route = automatic ? 'auto-speak' : 'speak'
    const created = await voiceApi<Job>(`/messages/${props.messageId}/${route}`, 'POST', boundPayload)
    if (disposed || token !== runEpoch) {
      void voiceApi(`/jobs/${created.job_id}`, 'DELETE').catch(() => {})
      return
    }
    job.value = created
    preview.value = null
    await poll(token)
  } catch (e) {
    if (token === runEpoch) {
      state.value = 'idle'; preview.value = null
      const detail = e instanceof Error ? e.message : ''
      error.value = detail.includes('voice_call_tts_text_too_long') || detail.includes('voice_call_tts_request_limit')
        ? '本轮完整回复超出通话朗读额度，文字已保留；通话将停止，不会截断文字或追加请求。'
        : detail || '朗读生成失败'
      emit('playback-state','failed')
      reportBudgetIfAutomatic()
    }
  }
}
async function poll(token: number) {
  if (!job.value || disposed || token !== runEpoch) return
  try {
    const current = await voiceApi<Job>(`/jobs/${job.value.job_id}`)
    if (disposed || token !== runEpoch) return
    job.value = current
    if (current.status === 'completed' && current.audio.length) {
      cached.value = current.audio
      reportBudgetIfAutomatic()
      playCached()
    } else if (current.status === 'queued' || current.status === 'generating') {
      pollTimer = setTimeout(() => void poll(token), 500)
    } else {
      state.value = 'idle'
      emit('playback-state','failed')
      reportBudgetIfAutomatic()
      error.value = current.error_code === 'auto_budget_exhausted'
        ? '自动朗读额度已用完；文字聊天仍可继续，已有缓存仍可播放。请查看语音设置中的每日额度及本次登录剩余额度。'
        : current.error_code === 'auto_budget_unavailable'
          ? '自动朗读额度暂不可用；文字聊天仍可继续。'
          : current.status === 'unknown'
        ? '本次请求或缓存结果未确认，已停止且不会自动重试。已派发请求的实际扣费须按供应商账户记录核对；若要重新尝试，请先在声音设置中手动清理缓存并重新预览。'
        : current.status === 'cancelled'
          ? '生成已取消；已派发请求的费用仍可能产生。'
          : current.status === 'failed'
            ? '供应商未返回可用音频，已停止后续片段；不会自动重试。'
            : '朗读缓存已失效，请重新预览。'
      const phase = ({connecting:'连接供应商', waiting_response:'等待供应商响应',
        receiving_audio:'接收音频'} as Record<string,string>)[current.failure_phase ?? '']
      if (phase) error.value += ` 第 ${current.failed_segment ?? 1} 段停在：${phase}。`
    }
  } catch (e) { state.value = 'idle'; error.value = e instanceof Error ? e.message : '任务状态读取失败'; emit('playback-state','failed'); reportBudgetIfAutomatic() }
}
function playCached() {
  if (!cached.value.length) return
  const selected = mode.value
  const epoch = runEpoch
  state.value = 'idle'; emit('play')
  void voiceAudioPlayer.play(props.messageId, cached.value.map(item => item.url), value => {
    if (!disposed && selected === mode.value && epoch === runEpoch) { state.value = value; emit('playback-state',value) }
  })
}
async function playFollowingCached() {
  if (!cached.value.length) return
  const urls = cached.value.map(item => item.url)
  for (const id of (props.followingMessageIds || []).slice(0, 20)) {
    if (disposed) return
    try {
      const result = await voiceApi<{audio:Job['audio']}>(`/messages/${id}/audio`)
      urls.push(...result.audio.map(item => item.url))
    } catch { /* Only already authorized, complete bindings are eligible. */ }
  }
  if (disposed) return
  const epoch = runEpoch
  state.value = 'idle'; emit('play')
  void voiceAudioPlayer.play(props.messageId, urls, value => {
    if (!disposed && mode.value === 'none' && epoch === runEpoch) state.value = value
  })
}
async function stop() {
  runEpoch++; clearPoll(); voiceAudioPlayer.stop(props.messageId); state.value = 'stopped'
  if (props.voiceCallId) emit('playback-state','stopped')
  if (job.value && ['queued','generating'].includes(job.value.status)) {
    try { await voiceApi(`/jobs/${job.value.job_id}`, 'DELETE') }
    catch { error.value = '已停止本地播放；远端生成或计费结果可能未知。' }
  }
  reportBudgetIfAutomatic()
}
</script>

<template>
  <div v-if="enabled" class="message-audio-player">
    <label v-if="expressionAvailable || aiAvailable">表达模式
      <select v-model="mode" @change="manuallySelected = true" :disabled="state==='generating'">
        <option v-if="automaticAvailable" value="auto">按声音档案选择语气</option>
        <option v-if="aiAvailable" value="ai_expression">AI 表达规划（短日语 HTTP）</option>
        <option value="none">无标签（默认）</option>
        <option v-if="expressionAvailable" value="thoughtful">[thoughtful]（实验候选，仅日语单片段）</option>
      </select>
    </label>
    <AIMessageAudio v-if="mode==='ai_expression' && !voiceCallId" :message-id="messageId" :auto-start="autoStart" @play="emit('play')" @playback-state="emit('playback-state',$event)" @budget-changed="emit('budget-changed')" />
    <template v-if="mode!=='ai_expression'">
    <button v-if="cached.length && state!=='playing' && state!=='paused' && state!=='generating'" type="button" @click="playCached">播放缓存</button>
    <button v-if="mode==='none' && cached.length && followingMessageIds?.length && state!=='playing' && state!=='paused' && state!=='generating'" type="button" @click="playFollowingCached">连续播放缓存</button>
    <button v-if="state==='idle'||state==='stopped'||state==='ended'||state==='failed'" type="button" @click="loadPreview">ElevenLabs 朗读</button>
    <span v-if="state==='generating'">正在生成音频…</span>
    <button v-if="state==='playing'" type="button" @click="voiceAudioPlayer.pause(messageId)">暂停</button>
    <button v-if="state==='paused'" type="button" @click="voiceAudioPlayer.resume(messageId)">继续</button>
    <button v-if="state==='generating'||state==='playing'||state==='paused'" type="button" @click="stop">停止</button>
    <div v-if="preview" class="message-audio-preview">
      <p>下列公开文字将发送给 ElevenLabs。{{preview.requests}} 次请求，共 {{preview.characters}} 个请求字符（{{preview.non_whitespace_characters}} 个非空白字符）；实际费用以账户为准。</p>
      <p>表达模式：{{preview.mode==='auto' ? (preview.expression_style ?? preview.effective_mode ?? '按声音档案') : preview.mode==='none'?'无标签':preview.mode+'（实验候选）'}}；添加标签：{{preview.expression_tag ?? '无'}}。正文中的标签不会直接发送。</p>
      <p>模型：{{preview.model_id ?? '当前服务未提供'}}；格式：{{preview.output_format ?? '当前服务未提供'}}；Voice ID：{{preview.voice_id_masked ?? '当前服务未提供'}}；Profile revision：{{preview.profile_revision ?? '当前服务未提供'}}；settings：{{preview.effective_parameters?.settings ?? '当前服务未提供'}}。</p>
      <p>克隆参考录音语言：{{preview.reference_language}}；本次发送的 language_code：{{preview.language_code ?? '不传（不强制语言）'}}。{{preview.language_reason==='mixed_or_ambiguous'?'正文混合或难以可靠判定。':preview.language_reason==='undetermined'?'正文语言无法可靠判定。':''}}</p>
      <p v-if="preview.skipped_reasons.length">以下内容已从朗读中跳过：{{preview.skipped_reasons.join('、')}}。聊天正文保持原样。</p>
      <p v-for="(segment,index) in preview.segments" :key="index" style="white-space: pre-wrap">{{segment}}</p>
      <button type="button" @click="confirmGenerate(false)">确认发送并生成</button>
      <button type="button" @click="preview=null">取消</button>
    </div>
    <span v-if="error" role="status">{{error}}</span>
    </template>
  </div>
</template>
