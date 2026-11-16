<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { voiceApi, type Privacy, type VoiceConfiguration, type VoiceProfile, type VoiceStatus } from './voiceApi'

const props = defineProps<{characterId:number}>()
const status = ref<VoiceStatus|null>(null)
const privacy = ref<Privacy|null>(null)
const profile = ref<VoiceProfile|null>(null)
const voiceId = ref('')
const active = ref(false)
const language = ref<VoiceConfiguration['language']>('auto')
const personality = ref('')
const noticeChecked = ref(false)
const busy = ref(false)
const message = ref('')
type BudgetCounts = {jobs:number;requests:number;characters:number}
type BudgetSettings = {limits:BudgetCounts;effective_limits:BudgetCounts;ceilings:BudgetCounts;revision:number;daily_enabled:boolean}
const budgetSettings = ref<BudgetSettings|null>(null)
const budgetDraft = ref<BudgetCounts>({jobs:2,requests:2,characters:160})
const confirmBudgetIncrease = ref(false)
const budgetMessage = ref('')
const budgetIncreases = computed(() => Boolean(budgetSettings.value &&
  (budgetDraft.value.jobs > budgetSettings.value.limits.jobs ||
   budgetDraft.value.requests > budgetSettings.value.limits.requests ||
   budgetDraft.value.characters > budgetSettings.value.limits.characters)))
function wholeWithin(value:number, maximum:number) { return Number.isInteger(value) && value >= 0 && value <= maximum }
const budgetInputError = computed(() => {
  const maximum = budgetSettings.value?.ceilings
  if (!maximum) return ''
  if (!wholeWithin(budgetDraft.value.jobs, maximum.jobs)) return `每天最多合成任务须为 0～${maximum.jobs} 的整数。`
  if (!wholeWithin(budgetDraft.value.requests, maximum.requests)) return `每天最多请求须为 0～${maximum.requests} 的整数。`
  if (!wholeWithin(budgetDraft.value.characters, maximum.characters)) return `每天最多送读字符须为 0～${maximum.characters} 的整数。`
  return ''
})
type ScribeCounts = {attempts:number;connections:number;audio_seconds:number}
type ScribeSettings = {enabled:boolean;service_enabled:boolean;limits:ScribeCounts;maximum:ScribeCounts;revision:number;per_attempt_audio_seconds:number}
type ScribeUsage = {remaining:ScribeCounts;available_attempts:number;per_attempt_audio_seconds:number;used:{attempts:number;connections:number;reserved_audio_seconds:number;actual_audio_seconds:number};reset_at:string}
const scribeSettings = ref<ScribeSettings|null>(null)
const scribeUsage = ref<ScribeUsage|null>(null)
const scribeDraft = ref<ScribeCounts>({attempts:1,connections:1,audio_seconds:15})
const scribeEnabled = ref(false)
const confirmScribeEnable = ref(false)
const confirmScribeIncrease = ref(false)
const scribeMessage = ref('')
const scribeIncreases = computed(() => Boolean(scribeSettings.value &&
  (scribeDraft.value.attempts > scribeSettings.value.limits.attempts ||
   scribeDraft.value.connections > scribeSettings.value.limits.connections ||
   scribeDraft.value.audio_seconds > scribeSettings.value.limits.audio_seconds)))
const scribeDraftAvailable = computed(() => {
  const perAttempt = scribeSettings.value?.per_attempt_audio_seconds
  if (!perAttempt || !Number.isInteger(scribeDraft.value.attempts) ||
      !Number.isInteger(scribeDraft.value.connections) ||
      !Number.isInteger(scribeDraft.value.audio_seconds) ||
      scribeDraft.value.attempts < 0 || scribeDraft.value.connections < 0 ||
      scribeDraft.value.audio_seconds < 0) return null
  return Math.min(scribeDraft.value.attempts, scribeDraft.value.connections,
    Math.floor(scribeDraft.value.audio_seconds / perAttempt))
})
const scribeInputError = computed(() => {
  const maximum = scribeSettings.value?.maximum
  if (!maximum) return ''
  if (!wholeWithin(scribeDraft.value.attempts, maximum.attempts)) return `每日最多录音次数须为 0～${maximum.attempts} 的整数。`
  if (!wholeWithin(scribeDraft.value.connections, maximum.connections)) return `每日最多供应商连接须为 0～${maximum.connections} 的整数。`
  if (!wholeWithin(scribeDraft.value.audio_seconds, maximum.audio_seconds)) return `每日预留音频秒数须为 0～${maximum.audio_seconds} 的整数。`
  if (scribeEnabled.value && (scribeDraft.value.attempts < 1 || scribeDraft.value.connections < 1 || scribeDraft.value.audio_seconds < 15))
    return '开启麦克风至少需要 1 次录音、1 条连接和 15 秒预留音频。'
  return ''
})
async function refreshScribeUsage() {
  if (!scribeSettings.value) return
  try { scribeUsage.value = await voiceApi<ScribeUsage>('/scribe-budget') }
  catch { scribeUsage.value = null }
}
onMounted(() => window.addEventListener('yosaki-scribe-usage-changed', refreshScribeUsage))
onBeforeUnmount(() => window.removeEventListener('yosaki-scribe-usage-changed', refreshScribeUsage))

async function load() {
  try {
    status.value = await voiceApi<VoiceStatus>('/status')
    privacy.value = await voiceApi<Privacy>('/privacy')
    profile.value = await voiceApi<VoiceProfile|null>(`/profiles?character_id=${props.characterId}`)
    voiceId.value = profile.value?.voice_id || (status.value.enabled ? status.value.models.voice_id : '')
    active.value = status.value.enabled && !!profile.value?.active
    language.value = profile.value?.configuration.language || 'auto'
    personality.value = profile.value?.configuration.personality || ''
    if (status.value.auto_daily_budget_enabled) {
      budgetSettings.value = await voiceApi<BudgetSettings>('/auto-budget/settings')
      budgetDraft.value = {...budgetSettings.value.limits}
      confirmBudgetIncrease.value = false
    } else budgetSettings.value = null
    if (status.value.scribe_daily_enabled) {
      scribeSettings.value = await voiceApi<ScribeSettings>('/scribe-budget/settings')
      scribeUsage.value = await voiceApi<ScribeUsage>('/scribe-budget')
      scribeDraft.value = {...scribeSettings.value.limits}
      scribeEnabled.value = scribeSettings.value.enabled
      confirmScribeEnable.value = false
      confirmScribeIncrease.value = false
    } else { scribeSettings.value = null; scribeUsage.value = null }
  } catch (error) { message.value = error instanceof Error ? error.message : '读取语音设置失败' }
}
watch(() => props.characterId, () => { void load() }, {immediate:true})

async function accept() {
  if (!privacy.value || !noticeChecked.value) return
  busy.value = true
  try { await voiceApi('/privacy/consent','PUT',{notice_version:privacy.value.notice_version,accepted:true}); await load(); window.dispatchEvent(new Event('yosaki-voice-config-changed')); message.value='第三方处理告知已确认。麦克风仍需单独开启。' }
  catch (error) { message.value = error instanceof Error ? error.message : '确认失败' }
  finally { busy.value = false }
}
async function revoke() {
  busy.value = true
  try { await voiceApi('/privacy/consent','DELETE'); noticeChecked.value=false; await load(); window.dispatchEvent(new Event('yosaki-voice-config-changed')); message.value='确认已撤销。' }
  catch (error) { message.value = error instanceof Error ? error.message : '撤销失败' }
  finally { busy.value = false }
}
async function save() {
  if (!status.value) return
  if (!status.value.enabled && profile.value?.active &&
      !window.confirm('此声音档案此前处于活动状态。语音服务当前关闭，保存将把档案设为非活动状态。继续保存吗？')) return
  busy.value = true
  try {
    const configuration:VoiceConfiguration = {schema_version:1,model_standard:status.value.models.model_standard,
      model_realtime:status.value.models.model_realtime,model_stt:status.value.models.model_stt,
      language:language.value,personality:personality.value,audio_format:'mp3_44100_128'}
    profile.value = await voiceApi<VoiceProfile>(`/profiles/${props.characterId}`,'PUT',
      {expected_revision:profile.value?.revision||0,voice_id:voiceId.value.trim(),active:status.value.enabled && active.value,configuration})
    active.value = profile.value.active
    window.dispatchEvent(new Event('yosaki-voice-config-changed'))
    message.value=status.value.enabled?'声音档案已保存。真实供应商能力尚未验证。':'声音档案已保存为非活动状态；语音服务仍未启用。'
  } catch (error) { message.value = error instanceof Error ? error.message : '保存失败' }
  finally { busy.value = false }
}
async function clearCache() {
  if (!window.confirm('清空此角色的 ElevenLabs 朗读缓存与消息绑定？')) return
  busy.value = true
  try {
    const result = await voiceApi<{removed:number}>(`/cache?character_id=${props.characterId}`, 'DELETE')
    message.value = `已清理 ${result.removed} 个缓存对象。`
  } catch (error) { message.value = error instanceof Error ? error.message : '清理失败' }
  finally { busy.value = false }
}
async function saveBudget() {
  if (!budgetSettings.value) return
  if (budgetInputError.value) { budgetMessage.value = budgetInputError.value; return }
  busy.value = true
  budgetMessage.value = ''
  try {
    const saved = await voiceApi<BudgetSettings>('/auto-budget/settings', 'PUT', {
      expected_revision: budgetSettings.value.revision,
      ...budgetDraft.value, confirm_increase: budgetIncreases.value && confirmBudgetIncrease.value})
    budgetSettings.value = saved
    budgetDraft.value = {...saved.limits}
    confirmBudgetIncrease.value = false
    budgetMessage.value = '每日自动朗读额度已保存；达到额度后文字聊天仍可继续。'
    window.dispatchEvent(new Event('yosaki-voice-auto-budget-changed'))
  } catch (error) { budgetMessage.value = error instanceof Error ? error.message : '额度保存失败' }
  finally { busy.value = false }
}
async function saveScribe() {
  if (!scribeSettings.value) return
  if (scribeInputError.value) { scribeMessage.value = scribeInputError.value; return }
  busy.value = true
  scribeMessage.value = ''
  try {
    const saved = await voiceApi<ScribeSettings>('/scribe-budget/settings', 'PUT', {
      expected_revision: scribeSettings.value.revision, enabled: scribeEnabled.value,
      ...scribeDraft.value,
      confirm_enable: scribeEnabled.value && !scribeSettings.value.enabled && confirmScribeEnable.value,
      confirm_increase: scribeIncreases.value && confirmScribeIncrease.value,
    })
    scribeSettings.value = saved
    scribeDraft.value = {...saved.limits}
    scribeUsage.value = await voiceApi<ScribeUsage>('/scribe-budget')
    confirmScribeEnable.value = false
    confirmScribeIncrease.value = false
    scribeMessage.value = saved.enabled ? '麦克风草稿功能已开启；只有你点击开始录音才会采集。' : '麦克风草稿功能已关闭。'
    window.dispatchEvent(new Event('yosaki-scribe-budget-changed'))
  } catch (error) { scribeMessage.value = error instanceof Error ? error.message : '麦克风设置保存失败' }
  finally { busy.value = false }
}
</script>

<template>
  <section class="voice-section">
    <h3>ElevenLabs 声音档案</h3>
    <p v-if="!status">正在读取语音服务状态…</p>
    <template v-else>
      <p v-if="!status.enabled">语音服务尚未启用，当前仅保存本地配置。下方设备语音仍可使用。</p>
      <p>密钥：{{status.api_key_configured?'已在后端配置':'未配置'}}。真实 API 验收：{{status.real_api_verified?'已完成':'V1-B 分项已验证，整体未完成'}}。</p>
      <div v-if="status.enabled && !privacy?.accepted" class="voice-privacy">
        <p>启用后，所选公开聊天文字会发送给 ElevenLabs 合成。未来开始通话时，麦克风音频会经 YOSAKI 后端发送给 ElevenLabs 识别；麦克风录音默认不落盘。用户请求的朗读音频将按 7 天、512 MiB 策略缓存，可清空。服务商保留方式取决于账户及实际配置，不承诺 Zero Retention。确认后也不会自动开启麦克风。</p>
        <label><input v-model="noticeChecked" type="checkbox"/>我了解第三方音频处理</label>
        <button class="secondary-btn" :disabled="!noticeChecked||busy" @click="accept">确认告知</button>
      </div>
      <button v-else-if="status.enabled" class="text-btn" :disabled="busy" @click="revoke">撤销第三方处理确认</button>
      <label>Voice ID<input v-model="voiceId" maxlength="128" placeholder="在 ElevenLabs 官方平台获取"/></label>
      <label>{{status.planner_version==='v2a-plain-2'?'克隆参考录音语言':'语言'}}<select v-model="language"><option value="auto">自动</option><option value="zh">中文</option><option value="ja">日文</option><option value="en">英文</option></select></label>
      <p v-if="status.planner_version==='v2a-plain-2'">此设置只记录音色来源语言；每次朗读的语言参数由公开正文规划，并在生成前预览。</p>
      <p v-else>当前运行中的朗读后端尚未启用新版语言规划；请勿据此设置推断实际请求语言。</p>
      <label>声音人格<textarea v-model="personality" maxlength="1000" rows="2"/></label>
      <p v-if="!status.enabled && profile?.active">此档案此前处于活动状态；现在保存会将其设为非活动状态。</p>
      <label><input v-model="active" type="checkbox" :disabled="!status.enabled"/>启用此角色声音档案</label>
      <button class="secondary-btn" :disabled="busy||!voiceId.trim()||(status.enabled&&!privacy?.accepted&&active)" @click="save">保存声音档案</button>
      <button class="text-btn" :disabled="busy" @click="clearCache">清空此角色朗读缓存</button>
      <p v-if="profile">角色 ID：{{profile.character_id}}；档案状态：{{profile.active?'活动':'非活动'}}；revision：{{profile.revision}}</p>
      <p>音色克隆与训练在 ElevenLabs 官方平台完成；当前仅绑定 Voice ID。V1-B 已分项验证，空闲 TTD 仍未完成。</p>
      <section v-if="budgetSettings" class="voice-section">
        <h3>每日自动朗读额度</h3>
        <p>默认验收配置为每天 2 次合成任务、2 个请求、160 个送读字符。超过额度只暂停自动朗读，文字聊天和已有缓存不受影响。额度是调用量限制，不是费用上限。</p>
        <label>每天最多合成任务（0～{{budgetSettings.ceilings.jobs}}）<input v-model.number="budgetDraft.jobs" type="number" min="0" :max="budgetSettings.ceilings.jobs" step="1"/></label>
        <label>每天最多请求（0～{{budgetSettings.ceilings.requests}}）<input v-model.number="budgetDraft.requests" type="number" min="0" :max="budgetSettings.ceilings.requests" step="1"/></label>
        <label>每天最多送读字符（0～{{budgetSettings.ceilings.characters}}）<input v-model.number="budgetDraft.characters" type="number" min="0" :max="budgetSettings.ceilings.characters" step="1"/></label>
        <p>当前有效额度：{{budgetSettings.effective_limits.jobs}} 次、{{budgetSettings.effective_limits.requests}} 个请求、{{budgetSettings.effective_limits.characters}} 字；设置版本 {{budgetSettings.revision}}。香港时间零点重置每日使用量，重新登录不会重置。</p>
        <label v-if="budgetIncreases"><input v-model="confirmBudgetIncrease" type="checkbox"/>我确认主动提高每日自动合成调用量，可能增加实际费用</label>
        <p v-if="budgetInputError" role="alert">{{budgetInputError}}</p>
        <button class="secondary-btn" :disabled="busy||!!budgetInputError||(budgetIncreases&&!confirmBudgetIncrease)" @click="saveBudget">保存每日额度</button>
        <p role="status">{{budgetMessage}}</p>
      </section>
      <section v-if="scribeSettings" class="voice-section">
        <h3>Scribe 麦克风草稿</h3>
        <p>默认关闭。只有点击“开始录音”才向 ElevenLabs 发送麦克风音频；转写只填入草稿，编辑并点击发送后才会聊天。取消会释放麦克风和连接，不自动重录或重连。</p>
        <label><input v-model="scribeEnabled" type="checkbox"/>允许我主动使用 Scribe 麦克风</label>
        <label>每日最多录音次数（0～{{scribeSettings.maximum.attempts}}）<input v-model.number="scribeDraft.attempts" type="number" min="0" :max="scribeSettings.maximum.attempts" step="1"/></label>
        <label>每日最多供应商连接（0～{{scribeSettings.maximum.connections}}）<input v-model.number="scribeDraft.connections" type="number" min="0" :max="scribeSettings.maximum.connections" step="1"/></label>
        <label>每日预留音频秒数（0～{{scribeSettings.maximum.audio_seconds}}）<input v-model.number="scribeDraft.audio_seconds" type="number" min="0" :max="scribeSettings.maximum.audio_seconds" step="1"/></label>
        <p>默认每天 1 次录音、1 条连接、{{scribeSettings.per_attempt_audio_seconds}} 秒预留音频。每次开始会预占完整 {{scribeSettings.per_attempt_audio_seconds}} 秒；可录音次数受次数、连接数和音频秒数中最先耗尽的一项限制，即使取消或结果未知也不返还。重新登录和刷新页面不重置；额度不是费用上限。</p>
        <p v-if="scribeDraftAvailable !== null">按当前填写的额度，每天最多可开始 {{scribeDraftAvailable}} 次录音；保存后才会生效。</p>
        <p v-if="scribeUsage">今日剩余额度：{{scribeUsage.remaining.attempts}} 次尝试、{{scribeUsage.remaining.connections}} 条连接、{{scribeUsage.remaining.audio_seconds}} 秒预留音频。<strong>按每次预留 {{scribeUsage.per_attempt_audio_seconds}} 秒，当前最多还能录音 {{scribeUsage.available_attempts}} 次。</strong> 已尝试发送约 {{scribeUsage.used.actual_audio_seconds.toFixed(2)}} 秒，不能证明供应商全部收到。香港时间零点重置。</p>
        <label v-if="scribeEnabled&&!scribeSettings.enabled"><input v-model="confirmScribeEnable" type="checkbox"/>我确认主动开启麦克风草稿功能；录音会发送给 ElevenLabs</label>
        <label v-if="scribeIncreases"><input v-model="confirmScribeIncrease" type="checkbox"/>我确认提高每日录音调用量，可能增加实际费用</label>
        <p v-if="scribeInputError" role="alert">{{scribeInputError}}</p>
        <button class="secondary-btn" :disabled="busy||!!scribeInputError||(scribeEnabled&&!scribeSettings.enabled&&!confirmScribeEnable)||(scribeIncreases&&!confirmScribeIncrease)" @click="saveScribe">保存麦克风设置</button>
        <p role="status">{{scribeMessage}}</p>
      </section>
    </template>
    <p role="status">{{message}}</p>
  </section>
</template>
