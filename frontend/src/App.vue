<script setup lang="ts">
import ContextMemoryPreview from './ContextMemoryPreview.vue'
const chronicleAvailable = import.meta.env.VITE_YOSAKI_CHRONICLE_A0 === '1'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import GlassSurface from './ui/GlassSurface.vue'
import YosakiGlobe from './ui/YosakiGlobe.vue'
import ThemeToggle from './ui/ThemeToggle.vue'
import ChatDrawer from './ui/ChatDrawer.vue'
import ChatEnhancementPanel from './chat/ChatEnhancementPanel.vue'
import {pendingSettings,modelRevision,sameScope,type PendingSettings,type Overrides} from './chat/nextRequestSettings'
import AttachmentInput from './chat/AttachmentInput.vue'
import './chat/chatEnhancement.css'
import ArchivePanel from './ArchivePanel.vue'
import BackupPlanPanel from './BackupPlanPanel.vue'
import ChatGPTPlanPanel from './ChatGPTPlanPanel.vue'
import DesirePanel from './DesirePanel.vue'
import LearningPanel from './LearningPanel.vue'
import PsychologyPanel from './PsychologyPanel.vue'
import VoiceSettings from './voice/VoiceSettings.vue'
import VoiceV4OutputPanel from './voice/VoiceV4OutputPanel.vue'
import MessageAudioPlayer from './voice/MessageAudioPlayer.vue'
import MessageRealtimeOutput from './voice/MessageRealtimeOutput.vue'
import DynamicChatOutput from './voice/DynamicChatOutput.vue'
import ScribeDraftInput from './voice/ScribeDraftInput.vue'
import ContinuousCallSettings from './voice/ContinuousCallSettings.vue'
import './voice/voiceDraft.css'
import { voiceAudioPlayer } from './voice/voiceAudioPlayer'
import { LocalBargeMonitor, type BargeCapture } from './voice/localBargeIn'
import type { PlaybackState } from './voice/voiceAudioPlayer'
import { MessageCircle, Settings2, Plus, Send, Paperclip, Mic, Sparkles, PanelLeft, ChevronDown, ShieldCheck, Bot, Save, Zap, ArrowRight, X, Minus, Maximize2, Minimize2, LayoutGrid, UserRound, Cpu, BrainCircuit, Wrench, Puzzle, Volume2, VolumeX, Home, Grip, RefreshCw, LogOut, LockKeyhole, ArrowUpRight } from 'lucide-vue-next'

const v4OutputIsolated = import.meta.env.VITE_YOSAKI_V4_OUTPUT_ISOLATED === '1'
const v4ProductOutputIsolated = import.meta.env.VITE_YOSAKI_V4_PRODUCT_OUTPUT_ISOLATED === '1'
const v4DynamicChatIsolated = import.meta.env.VITE_YOSAKI_V4_DYNAMIC_CHAT_ISOLATED === '1'
const v4DynamicCandidateIsolated = import.meta.env.VITE_YOSAKI_V4_DYNAMIC_CANDIDATE_ISOLATED === '1'

type ToolRunUi = { run_id:number; tool_name:string; server_name:string; plugin_name?:string|null; description:string; arguments:Record<string,unknown>; status:'pending_confirmation'|'success'|'rejected'|'error'|'invalid'|'running'|'revoked'|'archived'; result?:unknown; error?:string }
type Conversation = { id:number; character_id:number; title:string; model_profile_id:number|null; created_at:string; updated_at:string }
type Message = { id?: number; role: 'user' | 'assistant'; content: string; time: string; model?: string; toolRuns?: ToolRunUi[]; toolBatchRunIds?:number[]; toolBatchStatus?:string; reasoningContent?:string; monologueContent?:string; generationStatus?:string; usage?:any; attachments?:any[]; quotes?:any[]; reasoningSource?:string }
type Character = { id: number; name: string; subtitle: string; mood: string; color: string; online: boolean; last: string; system_prompt: string; greeting: string; enabled: boolean; default_model_id: number|null }
type FeatureKind = 'chat' | 'characters' | 'models' | 'memory' | 'desires' | 'learning' | 'psychology' | 'mcp' | 'plugins' | 'voice' | 'settings'
type WindowInstance = { id: string; kind: FeatureKind; title: string; x: number; y: number; width: number; height: number; zIndex: number; minimized: boolean; maximized: boolean }
type Feature = { kind: FeatureKind; title: string; description: string; icon: any; accent: string; status: string }
type Memory = { id:number; character_id:number; conversation_id:number|null; scope:'long'|'temporary'; content:string; enabled:boolean; managed_by_learning?:boolean; created_at:string; updated_at:string }

type PromptBlock = { id: number; character_id: number; name: string; content: string; injection_position: string; order_index: number; enabled: boolean }

type McpServer = { id:number; name:string; transport:'streamable_http'|'sse'; endpoint:string; auth_type:'none'|'bearer'|'api_key'; enabled:boolean; status:string; auth_configured:boolean }

type McpTool = { id:number; name:string; description:string; input_schema:any; requires_confirmation:boolean; enabled:boolean }
type PluginTool = {id:number;server_id:number;server_name:string;tool_name:string;alias:string;description:string;enabled:boolean;base_tool_enabled:boolean;requires_confirmation:boolean}
type Plugin = {id:number;manifest_id:string;name:string;version:string;description:string;author:string;permissions:string[];checksum:string;enabled:boolean;tools:PluginTool[]}

type ModelProfile = { id: number; name: string; provider_name: string; base_url: string; model_name: string; protocol: string; temperature: number; top_p: number; max_tokens: number; context_limit: number; supports_streaming: boolean; enabled: boolean; api_key_configured: boolean; reasoning_effort:string|null;updated_at:string }

const palette = ['#5f7185', '#617b78', '#7b6c78', '#847360', '#6d7180']
const features: Feature[] = [
  { kind: 'chat', title: '与阿夜聊天', description: '从一句话开始，进入你们的私人空间。', icon: MessageCircle, accent: '#9c8cff', status: '准备开始' },
  { kind: 'characters', title: '角色卡', description: '编辑人设、场景、开场白和提示词块。', icon: UserRound, accent: '#d894b1', status: '1 个角色' },
  { kind: 'models', title: '模型配置', description: '管理 OpenAI 兼容接口与对话模型。', icon: Cpu, accent: '#69c5bb', status: '演示模式' },
  { kind: 'memory', title: '提示词与记忆', description: '整理稳定前缀、关系记忆和上下文。', icon: BrainCircuit, accent: '#d2a264', status: '即将接入' },
  { kind: 'desires', title: '欲望与意愿', description: '查看驱力、意愿、自主活动与体验反馈。', icon: Sparkles, accent: '#ca91bb', status: 'Phase 4 · 默认关闭' },
  { kind: 'learning', title: '长期学习', description: '审阅经历记忆、偏好变化、策略与目标进度。', icon: Sparkles, accent: '#8eb8e8', status: 'Phase 5 · 审阅后生效' },
  { kind: 'psychology', title: '心理状态', description: '查看模拟情绪、虚拟身体、二阶欲望与关联。', icon: BrainCircuit, accent: '#a8a2dc', status: 'Phase 6 · 模拟参考' },
  { kind: 'mcp', title: 'MCP 与工具', description: '连接工具服务，执行前由你逐次确认。', icon: Wrench, accent: '#a69be9', status: '逐次确认' },
  { kind: 'plugins', title: '插件', description: '安装受控 JSON 工具包并按项授权。', icon: Puzzle, accent: '#7fb6da', status: '安全工具包' },
  { kind: 'voice', title: '语音', description: '使用设备语音朗读；语音转文字仅在本机可用或经你确认时启动。', icon: Volume2, accent: '#c98ca7', status: '浏览器语音' },
  { kind: 'settings', title: '系统设置', description: '调整工作台、数据和访问方式。', icon: Settings2, accent: '#92909f', status: '本地开发' },
]
const navGroups: { title: string; kinds: FeatureKind[] }[] = [
  { title: '日常', kinds: ['chat'] },
  { title: '角色与内在', kinds: ['characters', 'desires', 'learning', 'psychology'] },
  { title: '能力与连接', kinds: ['models', 'mcp', 'plugins', 'voice'] },
  { title: '管理', kinds: ['memory', 'settings'] },
]
const desktopVisible = ref(false)
function navTitle(kind: FeatureKind) { return kind === 'memory' ? '提示词与上下文' : feature(kind).title }
function visitFeature(kind: FeatureKind) {
  if (kind !== 'characters' && !confirmLeaveCharacterDraft()) return
  if (window.innerWidth <= 900) showNav.value = false
  openFeaturePage(kind)
}
async function visitChronicle() {
  if (!chronicleAvailable || !confirmLeaveCharacterDraft()) return
  if ((isStreaming.value || draft.value.trim()) && !window.confirm('离开当前页面将结束生成，并放弃尚未发送的草稿。前往记忆年历？')) return
  if (callId.value) await endContinuousCall('已前往记忆年历，通话结束。')
  window.location.assign('/chronicle')
}
function onComposerKeydown(event: KeyboardEvent) {
  if (event.key !== 'Enter' || event.shiftKey || event.ctrlKey || event.altKey || event.metaKey || event.isComposing || event.keyCode === 229) return
  event.preventDefault(); void send()
}
const characters = ref<Character[]>([{ id: 1, name: '阿夜', subtitle: '夜色里的共振者', mood: '今天想和你慢慢说话', color: '#5f7185', online: true, last: '我在。你今天过得怎么样？', system_prompt: '温柔但不软弱。拥有自己的判断、好奇心与边界。', greeting: '我在。今天想和我聊点什么？', enabled: true, default_model_id: null }])
const activeCharacter = ref(1)
const activeConversation = ref<number | null>(null)
const draft = ref('')
const isStreaming = ref(false)
const abortController = ref<AbortController|null>(null)
const streamStopped = ref(false)
const toolDecisionBusy = ref<number|null>(null)
const saving = ref(false)
const apiOnline = ref(false)
const errorMessage = ref('')
function routeWindowKind(pathname: string): FeatureKind | null {
  const routes: Partial<Record<string, FeatureKind>> = { '/chat': 'chat', '/characters': 'characters', '/models': 'models', '/memory': 'memory', '/desires': 'desires', '/learning': 'learning', '/psychology': 'psychology', '/mcp': 'mcp', '/plugins': 'plugins', '/voice': 'voice', '/settings': 'settings' }
  return routes[pathname] ?? null
}
const initialWindowQuery = new URLSearchParams(window.location.search).get('window')
const initialWindowKind = routeWindowKind(window.location.pathname) ?? (features.some(item => item.kind === initialWindowQuery) ? initialWindowQuery as FeatureKind : null)
const workspaceView = ref<'welcome' | 'hub'>(window.location.pathname === '/hub' || !!routeWindowKind(window.location.pathname) ? 'hub' : 'welcome')
const authReady=ref(false)
const authenticated=ref(false)
const authUser=ref('')
const setupRequired=ref(false)
const authBusy=ref(false)
const authError=ref('')
const authDraft=ref({username:'',password:'',confirmPassword:''})
const showNav = ref(true)
const model = ref('YOSAKI · demo')
const modelProfileId = ref<number | null>(null)
const modelProfiles = ref<ModelProfile[]>([])
const chronicleChatCap = ref(0)
const chronicleChatConsent = ref(false)
const chronicleChatWaitSeconds = ref<12 | 30>(12)
function clearChronicleChatConsent() {
  chronicleChatConsent.value = false
  chronicleChatWaitSeconds.value = 12
}
watch(chronicleChatConsent, allowed => { if (!allowed) chronicleChatWaitSeconds.value = 12 }, { flush: 'sync' })
const chronicleChatInstalled = ref(false)
const chronicleChatStatusText = ref('正在读取记忆补查状态。')
const chronicleChatUnavailableText = computed(() => chronicleChatCap.value > 0
  ? '请选择已配置密钥的官方 OpenAI Responses gpt-6.1-sol 档案；普通聊天与本地召回仍可使用。'
  : chronicleChatStatusText.value)
const chronicleChatAvailable = computed(() => {
  const profile = modelProfiles.value.find(item => item.id === modelProfileId.value)
  return chronicleChatCap.value > 0 && !!profile?.enabled && profile.api_key_configured
    && profile.protocol === 'openai_responses' && profile.model_name === 'gpt-6.1-sol'
    && profile.base_url.replace(/\/+$/, '') === 'https://api.openai.com/v1'
})
const promptBlocks = ref<PromptBlock[]>([])
const memories = ref<Memory[]>([])
const mcpServers = ref<McpServer[]>([])
const mcpTools = ref<McpTool[]>([])
const plugins = ref<Plugin[]>([])
const pluginBusy = ref(false)
const pluginStatus = ref('')
const pluginFileInput = ref<HTMLInputElement|null>(null)
const backupFileInput = ref<HTMLInputElement|null>(null)
const backupStatus = ref('')
const backupBusy = ref(false)
let backupRequestAbort: AbortController | null = null
const backupPassphrase = ref('')
const mcpDraft = ref({name:'',transport:'streamable_http' as 'streamable_http'|'sse',endpoint:'',auth_type:'none' as 'none'|'bearer'|'api_key',auth_secret:''})
const mcpSelectedId = ref<number|null>(null)
const mcpBusy = ref(false)
const mcpStatus = ref('')
const memorySearch = ref('')
const memoryDraft = ref({scope:'long' as 'long'|'temporary', content:''})
const memoryBusy = ref(false)
const contextSettings = ref({history_limit:100,memory_enabled:true,window_mode:'strict',window_size:50,window_keep:40,reasoning_prompt:'',monologue_enabled:false,monologue_prompt:'描写角色此刻的内心独白，不描述模型推理过程。'})
const summaryDraft = ref('')
const summaryEnabled = ref(false)
const voiceLocale = ref(localStorage.getItem('YOSAKI.voice.locale') || 'zh-CN')
const voiceRate = ref(Number(localStorage.getItem('YOSAKI.voice.rate') || '1'))
const selectedVoiceURI = ref(localStorage.getItem('YOSAKI.voice.voiceURI') || '')
const speechVoices = ref<SpeechSynthesisVoice[]>([])
const speechStatus = ref('')
const speakingMessageId = ref<number|string|null>(null)
const speechConsentPrompt = ref(false)
const speechConsentDialog = ref<HTMLElement>()
let speechReturnFocus: HTMLElement | null = null
watch(speechConsentPrompt, async visible => {
  if (visible) { speechReturnFocus = document.activeElement as HTMLElement; await nextTick(); speechConsentDialog.value?.querySelector<HTMLButtonElement>('button')?.focus() }
  else { await nextTick(); speechReturnFocus?.focus(); speechReturnFocus = null }
})
function onSpeechConsentKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') { event.preventDefault(); speechConsentPrompt.value = false; return }
  if (event.key !== 'Tab') return
  const buttons = speechConsentDialog.value?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')
  if (!buttons?.length) return
  const first = buttons[0], last = buttons[buttons.length - 1]
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
}
const speechRecognitionRunning = ref(false)
const activeRecognition = ref<any>(null)
// The control is opt-in for each browser session when its startup flag is enabled.
const isolatedAutoRead = import.meta.env.VITE_YOSAKI_VOICE_AUTO_READ_ISOLATED === '1'
const scribeDraftVisible = import.meta.env.VITE_YOSAKI_SCRIBE_DRAFT_ISOLATED === '1' ||
  import.meta.env.VITE_YOSAKI_SCRIBE_DAILY === '1'
const scribeCancelEpoch = ref(0)
const scribeRunning = ref(false)
const scribeInputDeviceId = ref('')
const scribeInputDevices = ref<{id:string;label:string}[]>([])
const scribeInputDeviceNotice = ref('')
const scribeDevicePermissionBusy = ref(false)
async function refreshScribeInputDevices() {
  if (!navigator.mediaDevices?.enumerateDevices) return
  try {
    const devices=await navigator.mediaDevices.enumerateDevices()
    scribeInputDevices.value=devices.filter(item=>item.kind==='audioinput' && item.deviceId && item.deviceId!=='default')
      .map((item,index)=>({id:item.deviceId,label:item.label||`麦克风 ${index+1}`}))
    scribeInputDeviceNotice.value=scribeInputDeviceId.value &&
      !scribeInputDevices.value.some(item=>item.id===scribeInputDeviceId.value)
      ? '之前选择的麦克风未连接；请重新选择。' : ''
  } catch { scribeInputDevices.value=[]; scribeInputDeviceNotice.value='无法读取输入设备列表。' }
}
function onScribeDeviceChange() { void refreshScribeInputDevices() }
async function showScribeInputDevices() {
  if (!navigator.mediaDevices?.getUserMedia || scribeDevicePermissionBusy.value) return
  scribeDevicePermissionBusy.value = true
  try {
    // This brief local capture grants device labels; it is never sent to Scribe.
    const stream = await navigator.mediaDevices.getUserMedia({audio:true})
    stream.getTracks().forEach(track => track.stop())
    await refreshScribeInputDevices()
    scribeInputDeviceNotice.value = '麦克风列表已更新；请选正在使用的设备。没有连接识别服务。'
  } catch {
    scribeInputDeviceNotice.value = '无法读取麦克风；请检查此页面的麦克风权限。没有连接识别服务。'
  } finally { scribeDevicePermissionBusy.value = false }
}
function saveScribeInputDevice() {
  try { localStorage.setItem('YOSAKI.scribe.input-device.v1',scribeInputDeviceId.value) } catch { /* preference only */ }
  void refreshScribeInputDevices()
}
const autoReadEnabled = ref(false)
const autoReadMessageId = ref<number|null>(null)
const continuousDaily = import.meta.env.VITE_YOSAKI_VOICE_CONTINUOUS_DAILY === '1'
const wsPlaybackKey = 'YOSAKI.voice.continuous-ws.v1'
const wsPlayback = ref(false)
try { wsPlayback.value = localStorage.getItem(wsPlaybackKey) === '1' } catch { /* HTTP default */ }
const callPlaybackWs = ref(false)
function setWsPlayback(enabled:boolean) {
  wsPlayback.value = enabled
  try { localStorage.setItem(wsPlaybackKey, enabled ? '1' : '0') } catch { /* session preference */ }
}
const callSettingsEnabled = import.meta.env.VITE_YOSAKI_VOICE_CALL_SETTINGS === '1'
const bargeInExperimental = import.meta.env.VITE_YOSAKI_VOICE_BARGE_IN_ISOLATED === '1'
const headsetBargeAcceptance = continuousDaily &&
  import.meta.env.VITE_YOSAKI_HEADSET_BARGE_ACCEPTANCE === '1'
const singleButtonAcceptance = import.meta.env.VITE_YOSAKI_SINGLE_BUTTON_ACCEPTANCE === '1'
const bargeInAvailable = !singleButtonAcceptance && (continuousDaily || bargeInExperimental)
const continuousAvailable = continuousDaily || import.meta.env.VITE_YOSAKI_VOICE_CONTINUOUS_ISOLATED === '1'
type CallAvailability = {conversation_id:number;conversation_title:string;available_rounds:number;active_call:boolean;conversation_incomplete:boolean;model_path_ready?:boolean;model_path_reason?:string|null;limits?:{limits:{rounds:number;seconds:number;tts_requests:number;tts_characters:number}};scribe_remaining:{attempts:number;connections:number;audio_seconds:number};tts_remaining:BudgetCounts}
function callModelPathNotice(reason:string|null|undefined) {
  if (reason==='voice_call_model_disabled') return '模型连接开关未启用，录音不会开始。'
  if (reason==='voice_call_model_authorization_unreadable') return '本机模型授权无法读取，请检查模型连接；录音不会开始。'
  if (reason==='voice_call_model_authorization_missing' || reason==='voice_call_model_refresh_unavailable') return '本机模型授权需要重新连接；录音不会开始。'
  if (reason==='voice_call_model_local_token_expired') return '本机 ChatGPT 套餐授权已过期或即将过期。请续期后再开始验收。'
  return '模型连接未就绪，录音不会开始。'
}
const callAvailability = ref<CallAvailability|null>(null)
type CallPhase = 'idle'|'listening'|'transcribing'|'waiting'|'playing'|'ended'
const callId = ref<string|null>(null)
const callPhase = ref<CallPhase>('idle')
const callEpoch = ref(0)
const callTurnId = ref<string|null>(null)
const callPlaybackMessageId = ref<number|null>(null)
const callAcknowledged = ref(false)
const callStartBusy = ref(false)
const callStatus = ref('')
const callPlaybackStarted = ref(false)
const callPhaseLabel = computed(() => {
  if (callPhase.value === 'listening') return '正在听取'
  if (callPhase.value === 'transcribing') return '正在停止旧轮次或转写'
  if (callPhase.value === 'waiting') return isStreaming.value ? '模型正在生成' : '正在等待模型回复'
  if (callPhase.value === 'playing') return callPlaybackStarted.value ? '声音正在播放' : '回复已完成，正在合成音频'
  if (callPhase.value === 'ended') return '通话已结束'
  return '尚未开始'
})
const bargeInRequested = ref(false)
const bargeReady = ref(false)
let bargeTransferred = false
const bargeMonitor = new LocalBargeMonitor()
const bargeCapture = shallowRef<BargeCapture|null>(null)
const callLimits = ref<{seconds:number;model_turns:number}|null>(null)
let callTimer: ReturnType<typeof setTimeout>|null = null
let callGenerationTask: Promise<void>|null = null
const playbackDrainClaims = new Set<string>()
watch([callId,callPhase,bargeInRequested,callPlaybackStarted], ([id,phase,enabled,started]) => {
  bargeMonitor.stop()
  bargeReady.value=false
  if (!bargeInAvailable || !enabled ||
      !id || phase!=='playing' || !started || (headsetBargeAcceptance && bargeTransferred)) return
  const epoch = callEpoch.value
  const isCurrentPlayback = () => callId.value===id && callEpoch.value===epoch &&
    callPhase.value==='playing' && callPlaybackStarted.value
  void bargeMonitor.start(capture => {
    if (isCurrentPlayback() && bargeReady.value) {
      const retained=bargeMonitor.take(capture)
      if(!retained)return
      bargeTransferred=true
      bargeCapture.value=retained
      callStatus.value='检测到插话，已停止旧声音；正在接续识别这次插话。'
      void interruptContinuousCall(retained)
    }
  }, () => {
    if (isCurrentPlayback()) {
      bargeReady.value=false
      bargeInRequested.value=false
      callStatus.value='插话监听不可用；请使用“打断”按钮。'
    }
  }, scribeInputDeviceId.value, () => {
    if (isCurrentPlayback()) {
      bargeReady.value=true
      callStatus.value='声音正在播放，麦克风监听已就绪。现在可以插话。'
    }
  })
})
type BudgetCounts = { jobs:number; requests:number; characters:number }
type DailyAutoBudget = { local_day:string; reset_at:string; limits:BudgetCounts; used:BudgetCounts; remaining:BudgetCounts }
const sessionAutoBudget = ref<{limits:BudgetCounts; used:BudgetCounts}|null>(null)
const dailyAutoBudget = ref<DailyAutoBudget|null>(null)
const autoBudgetDescription = computed(() => {
  const session = sessionAutoBudget.value
  const sessionCopy = session
    ? `本次登录剩余 ${Math.max(0,session.limits.jobs-session.used.jobs)} 次、${Math.max(0,session.limits.requests-session.used.requests)} 个请求、${Math.max(0,session.limits.characters-session.used.characters)} 字。`
    : '每次登录最多自动合成 2 次、2 个请求、160 字。'
  const daily = dailyAutoBudget.value
  const dailyCopy = daily
    ? `每日最多 ${daily.limits.jobs} 次、${daily.limits.requests} 个请求、${daily.limits.characters} 字；今日剩余 ${daily.remaining.jobs} 次、${daily.remaining.requests} 个请求、${daily.remaining.characters} 字（香港时间零点重置）。`
    : ''
  return `${dailyCopy}${daily ? '' : sessionCopy} 额度用尽后文字聊天继续可用，缓存仍可播放。`
})
async function refreshAutoBudget() {
  if (!isolatedAutoRead) return
  try {
    const response = await fetch('/api/voice/auto-budget', {credentials:'same-origin'})
    if (!response.ok) { sessionAutoBudget.value = null; dailyAutoBudget.value = null; return }
    const result = await response.json()
    sessionAutoBudget.value = result.limits && result.used ? {limits:result.limits,used:result.used} : null
    dailyAutoBudget.value = result.daily ?? null
  } catch { sessionAutoBudget.value = null; dailyAutoBudget.value = null }
}
function stopAutoRead() { autoReadMessageId.value = null; voiceAudioPlayer.stop() }
function acceptScribeDraft(text:string) { draft.value=[draft.value.trim(),text.trim()].filter(Boolean).join(' '); speechStatus.value='转写已放入输入框；请检查后自行发送。' }
async function refreshCallAvailability() {
  callAvailability.value=null
  if (!continuousDaily || !authenticated.value || callId.value || !activeConversation.value || !modelProfileId.value) return
  const conversation=activeConversation.value, character=activeCharacter.value, modelId=modelProfileId.value
  try {
    const result=await api<CallAvailability>(`/api/voice/calls/availability?character_id=${character}&conversation_id=${conversation}&model_profile_id=${modelId}`)
    if (conversation===activeConversation.value && character===activeCharacter.value && modelId===modelProfileId.value && !callId.value) callAvailability.value=result
  } catch { callAvailability.value=null }
}
watch([activeCharacter,activeConversation,modelProfileId,authenticated],()=>{void refreshCallAvailability()})
async function startContinuousCall() {
  if (!continuousAvailable || callId.value || callStartBusy.value || !callAcknowledged.value ||
      modelProfileId.value === null || isStreaming.value) return
  callStartBusy.value=true
  try {
    const characterId=activeCharacter.value, modelId=modelProfileId.value,
      selectedConversation=activeConversation.value, epoch=callEpoch.value
    const batch=continuousDaily
      ? await api<{character_id:number;conversation_id:number;model_profile_id:number;available_rounds:number}>(
          `/api/voice/calls/availability?character_id=${characterId}&conversation_id=${selectedConversation}&model_profile_id=${modelId}`)
      : await api<{character_id:number;conversation_id:number;model_profile_id:number;available_rounds?:number}>('/api/voice/calls/batch')
    if ((continuousDaily && (!selectedConversation || batch.conversation_id!==selectedConversation ||
        (batch.available_rounds ?? 0)<(headsetBargeAcceptance ? 2 : 1))) ||
        batch.character_id!==characterId || batch.model_profile_id!==modelId ||
        characterId!==activeCharacter.value || modelId!==modelProfileId.value ||
        epoch!==callEpoch.value || callId.value) throw new Error('目标对话或当前可用额度已变化，请重新检查。')
    if (headsetBargeAcceptance) {
      const capability = await api<{available:boolean}>('/api/voice/realtime/daily-status')
      if (!capability.available) throw new Error('WS 播放服务未就绪，耳机插话验收未开始。')
      if (epoch!==callEpoch.value || selectedConversation!==activeConversation.value ||
          characterId!==activeCharacter.value || modelId!==modelProfileId.value) return
    }
    const response=await api<{call_id:string;conversation_id:number;limits:{seconds:number;model_turns:number;stt_connections:number;tts_requests:number;tts_characters:number}}>('/api/voice/calls', {method:'POST',body:JSON.stringify({
      character_id:characterId,conversation_id:batch.conversation_id,
      model_profile_id:modelId,acknowledge_auto_send:true})})
    if (epoch!==callEpoch.value || selectedConversation!==activeConversation.value ||
        characterId!==activeCharacter.value ||
        modelId!==modelProfileId.value) {
      void api(`/api/voice/calls/${response.call_id}`,{method:'DELETE'}).catch(()=>{})
      return
    }
    if (response.conversation_id!==batch.conversation_id) {
      void api(`/api/voice/calls/${response.call_id}`,{method:'DELETE'}).catch(()=>{})
      throw new Error('通话绑定对话不一致。')
    }
    if (headsetBargeAcceptance && response.limits.model_turns!==2) {
      void api(`/api/voice/calls/${response.call_id}`,{method:'DELETE'}).catch(()=>{})
      throw new Error('耳机插话验收需要服务端绑定两轮上限。')
    }
    if (singleButtonAcceptance && (response.limits.model_turns!==1 ||
        response.limits.stt_connections!==1 || response.limits.tts_requests!==1 ||
        response.limits.tts_characters>80)) {
      void api(`/api/voice/calls/${response.call_id}`,{method:'DELETE'}).catch(()=>{})
      throw new Error('单轮验收的服务端额度不符。')
    }
    callId.value=response.call_id
    callPlaybackWs.value=!singleButtonAcceptance && continuousDaily && (headsetBargeAcceptance || wsPlayback.value)
    bargeTransferred=false
    if (headsetBargeAcceptance) bargeInRequested.value=true
    callLimits.value=response.limits
    activeConversation.value=batch.conversation_id
    await loadConversations()
    await loadMessages(batch.conversation_id)
    if (epoch!==callEpoch.value || callId.value!==response.call_id) return
    callEpoch.value++; callPlaybackStarted.value=false; callPhase.value='listening'
    callStatus.value=`已绑定对话 ${batch.conversation_id}，正在打开麦克风；请等页面显示“正在听取”后再说话。最终转写会自动发送。`
    callTimer=setTimeout(() => { void endContinuousCall('通话已达到时间上限。') }, response.limits.seconds * 1000)
  } catch(e) {
    if (callId.value) await endContinuousCall('通话准备失败，已结束。')
    const detail=e instanceof Error?e.message:''
    callStatus.value=detail.includes('voice_batch_conversation_changed')
      ? '本批对话已有未完成轮次，不能重新开始；不会重复发送或重置额度。'
      : detail.includes('voice_batch_exhausted')
        ? '本批通话次数已用完；文字聊天和缓存仍可使用。'
        : detail.includes('voice_call_conversation_incomplete')
          ? '上一条消息已保存，但回复未完成。可在原对话手动继续文字回复；不会自动补发。'
        : detail.includes('voice_call_model_')
          ? callModelPathNotice(detail)
        : detail||'无法开始连续通话。'
  }
  finally { callStartBusy.value=false }
}
async function endContinuousCall(reason='通话已结束。', pageExiting=false) {
  const old=callId.value
  bargeMonitor.stop(); bargeInRequested.value=false
  bargeReady.value=false
  bargeCapture.value?.close(); bargeCapture.value=null
  callId.value=null; callEpoch.value++; callPhase.value='ended'
  callPlaybackWs.value=false
  playbackDrainClaims.clear()
  callLimits.value=null
  callTurnId.value=null; callPlaybackMessageId.value=null; callPlaybackStarted.value=false; callStatus.value=reason
  callAcknowledged.value=false
  scribeCancelEpoch.value++; voiceAudioPlayer.stop()
  if (isStreaming.value && abortController.value) {
    streamStopped.value=true; abortController.value.abort()
  }
  if (callTimer) clearTimeout(callTimer); callTimer=null
  if (old) {
    const path=`/api/voice/calls/${old}`
    if (pageExiting) void fetch(path,{method:'DELETE',credentials:'same-origin',keepalive:true}).catch(() => {})
    else void api(path,{method:'DELETE'}).catch(() => {})
  }
  if (!pageExiting) void refreshCallAvailability()
}
function onCallPageHide() {
  if (callId.value) void endContinuousCall('已离开页面，通话结束。', true)
}
async function interruptContinuousCall(handoff?:BargeCapture) {
  const current=callId.value
  if (!current) return
  if (headsetBargeAcceptance && !handoff) {
    await endContinuousCall('按钮已结束验收；本轮未测到插话接续。')
    return
  }
  const clickMs=performance.now(), phase=callPhase.value, oldTurn=callTurnId.value
  callStatus.value=phase==='playing'
    ? '已按打断，正在立即停止声音并清空队列。'
    : phase==='waiting' ? '已按打断，正在取消模型生成。' : '已按打断，正在停止当前轮次。'
  bargeMonitor.stop()
  if(!handoff){bargeCapture.value?.close();bargeCapture.value=null}
  const wasGenerating=isStreaming.value
  const oldGeneration=callGenerationTask
  const playbackStarted=callPlaybackStarted.value
  callEpoch.value++; callTurnId.value=null; callPlaybackMessageId.value=null
  scribeCancelEpoch.value++; voiceAudioPlayer.stop()
  callPlaybackStarted.value=false
  if (wasGenerating && abortController.value) {
    streamStopped.value=true; abortController.value.abort()
  }
  callPhase.value='transcribing'
  await nextTick()
  if (callId.value!==current) return
  const audioStopMs=performance.now()
  try {
    const result=await api<{cancel_generation:boolean;can_continue:boolean}>(
      `/api/voice/calls/${current}/interrupt`, {method:'POST',body:JSON.stringify({
        phase,client_click_ms:clickMs,client_audio_stop_ms:audioStopMs,
        turn_id:oldTurn,client_playback_started:singleButtonAcceptance ? playbackStarted : undefined})})
    if (oldGeneration) await oldGeneration
    if (callId.value!==current) return
    if (!result.can_continue) {
      await endContinuousCall(singleButtonAcceptance
        ? '单轮验收已结束，已停止播放并收麦。'
        : '已停止当前回复或声音；本次通话已达到轮数或每日额度上限。没有重新打开麦克风。')
      return
    }
    callStatus.value=handoff ? '旧声音已停止，正在识别刚才的插话。'
      : '旧轮次已停止，正在重新打开麦克风；等“正在听取”后再说话。'
    callPhase.value='listening'
  } catch(e) { await endContinuousCall(e instanceof Error?e.message:'打断失败，通话已结束。') }
}
function onContinuousTranscript(text:string, turnId?:string) {
  if (!callId.value || callPhase.value!=='listening' || !turnId) return
  if (isStreaming.value) {
    void endContinuousCall('上轮生成尚未清理；本次转写未派发，通话已停止。')
    return
  }
  bargeCapture.value=null
  callPhase.value='waiting'; callTurnId.value=turnId
  callStatus.value='最终转写已发送，正在请求模型；回复尚未通过展示核验。'
  const task=sendText(text, turnId)
  callGenerationTask=task
  void task.finally(() => { if (callGenerationTask===task) callGenerationTask=null })
}
function onContinuousFailure(code:string) {
  bargeCapture.value?.close();bargeCapture.value=null
  if (callId.value) void endContinuousCall(code==='no_speech'
    ? '本地音量检测没有确认说话，通话已停止。麦克风曾打开；请检查输入设备和音量，并在“正在听取”出现后再说话。没有自动重连或发送。'
    : `识别未完成（${code}），通话已停止；没有自动重连或发送。`)
}
function onContinuousCaptureReady() {
  if (callId.value && callPhase.value==='listening')
    {
      callCommitMs.value=null
      callSpeechEndMs.value=null
      callSoftwareLatency.value=null
      callStatus.value=bargeCapture.value ? '正在识别刚才的插话。'
        : '正在听取；现在可以说话。最终转写会自动发送。'
    }
}
const callCommitMs = ref<number|null>(null)
const callSpeechEndMs = ref<number|null>(null)
const callSoftwareLatency = ref<number|null>(null)
function onCallAudioCommit(browserMs:number, lastAudibleMs:number|null) {
  callCommitMs.value=browserMs
  callSpeechEndMs.value=lastAudibleMs
}

async function onCallPlaybackState(messageId:number, state:PlaybackState, reason?:string) {
  if (!callId.value && autoReadMessageId.value===messageId && ['ended','stopped','failed'].includes(state)) {
    autoReadMessageId.value=null
  }
  if (!callId.value || callPlaybackMessageId.value!==messageId) return
  if (state==='paused') {
    callPlaybackStarted.value=false
    bargeReady.value=false
    bargeMonitor.stop()
    callStatus.value='声音已暂停，插话监听已关闭。'
    return
  }
  if (state==='stopped') {
    await endContinuousCall('已停止播放，通话已结束。')
    return
  }
  if (state==='playing') {
    if (callPlaybackStarted.value) return
    callPlaybackStarted.value=true
    if (callCommitMs.value!==null)
      callSoftwareLatency.value=Math.round(performance.now()-(callSpeechEndMs.value ?? callCommitMs.value))
    callStatus.value=bargeInRequested.value && (!headsetBargeAcceptance || !bargeTransferred)
      ? '声音正在播放，正在准备插话监听。'
      : '声音正在播放；麦克风关闭，可点“打断”立即停音。'
    return
  }
  if (state==='ended') {
    const current=callId.value, turn=callTurnId.value, epoch=callEpoch.value
    if (!turn) { await endContinuousCall('播放轮次丢失，通话已结束。'); return }
    const drainKey=`${current}:${turn}:${messageId}`
    if (playbackDrainClaims.has(drainKey)) return
    playbackDrainClaims.add(drainKey) // Claim before awaiting; two player callbacks may race.
    callPlaybackStarted.value=false
    bargeReady.value=false
    bargeMonitor.stop()
    const bargeMissed = headsetBargeAcceptance && !bargeTransferred
    let drained: {drained:boolean;can_continue:boolean}
    try { drained=await api(`/api/voice/calls/${current}/playback-drained`, {method:'POST',
      body:JSON.stringify({turn_id:turn,message_id:messageId})}) }
    catch(e) {
      if (callId.value===current && callEpoch.value===epoch)
        await endContinuousCall(e instanceof Error?e.message:'播放确认失败，通话已结束。')
      return
    }
    if (callId.value!==current || callEpoch.value!==epoch) return
    if (bargeMissed) {
      await endContinuousCall('本轮未测到插话。第一轮音频已播完，验收已结束。')
      return
    }
    if (!drained.can_continue) {
      await endContinuousCall('本次通话已达到设定轮数或每日额度上限；音频已播完。')
      return
    }
    callPlaybackMessageId.value=null; callTurnId.value=null; callPlaybackStarted.value=false
    callPhase.value='listening'; callStatus.value='回复已播完，正在重新打开麦克风；请等页面显示“正在听取”后再说话。'
  } else if (state==='failed') {
    if (callPlaybackWs.value) {
      void endContinuousCall(`${reason || 'WS 播放失败。'} 通话已结束，文字回复及已计用量保留。可在语音设置切回 HTTP，或在消息下手动选择朗读。系统不会自动再次合成此回复。`)
      void refreshAutoBudget()
    } else void endContinuousCall('本轮朗读失败，通话已停止；文字回复仍可查看。')
  }
}

const modelDraft = ref({ name: '', provider_name: 'OpenAI Compatible', base_url: 'https://api.openai.com/v1', model_name: '', protocol: 'openai_compatible', temperature: 0.8, top_p: 1, max_tokens: 2048, context_limit: 32768, supports_streaming: true, enabled: true, reasoning_effort:null as string|null })
const modelKeyInput = ref('')
const editingModelId = ref<number | null>(null)
const modelEditorReady=ref(false),modelFeedbackError=ref(false)
const MODEL_EDITOR_KEY='YOSAKI.model-editor.profile'
let modelLoadEpoch=0,modelEditorEpoch=0
function effortName(value:string|null|undefined){return ({low:'低',medium:'中',high:'高'} as Record<string,string>)[value??'']??'未指定（服务商默认）'}
const savedModelProfile=computed(()=>modelProfiles.value.find(p=>p.id===editingModelId.value))
const modelSaving = ref(false)
const modelTestStatus = ref('')
const messages = ref<Message[]>([])
const conversations = ref<Conversation[]>([])
const conversationBusy = ref(false)
const conversationStatus = ref('')
const conversationSearch = ref('')
type ChatPanel = 'conversations' | 'voice' | 'read' | 'memory' | 'model' | 'status' | 'enhance'
const enhancementMessage=ref<Message|null>(null)
const enhancementHead=ref(0)
const enhancementBusy=ref(false)
const enhancementRef=ref<InstanceType<typeof ChatEnhancementPanel>>()
const attachmentRef=ref<InstanceType<typeof AttachmentInput>>()
function setEnhancementRef(value:unknown){enhancementRef.value=value as InstanceType<typeof ChatEnhancementPanel>|undefined}
function setAttachmentRef(value:unknown){attachmentRef.value=value as InstanceType<typeof AttachmentInput>|undefined}
const pendingAttachments=ref<any[]>([])
const pendingQuote=ref<{message_id:number;excerpt:string}|null>(null)
const nextOverrides=ref<Record<string,any>>({})
const nextSettingsReady=ref(false),nextSettingsNotice=ref('')
const nextSettingsStore=pendingSettings({getItem:k=>sessionStorage.getItem(k),setItem:(k,v)=>sessionStorage.setItem(k,v),removeItem:k=>sessionStorage.removeItem(k)})
let startupNextSettings:PendingSettings|null=null
const nextSettingsScope=computed(()=>{const p=modelProfiles.value.find(p=>p.id===modelProfileId.value);return authenticated.value&&authUser.value&&p?.enabled?{user:authUser.value,character:activeCharacter.value,conversation:activeConversation.value,model:p.id,revision:modelRevision(p)}:null})
function clearNextSettings(message='已清除单次设置，下一次使用模型默认设置'){
  nextSettingsStore.clear();nextOverrides.value={};nextSettingsNotice.value=message
}
async function applyNextOverrides(value:Overrides){
  if(!nextSettingsReady.value)throw new Error('单次设置正在恢复核对，请稍候')
  if(!Object.keys(value).length){clearNextSettings();return}
  const scope=nextSettingsScope.value
  if(!scope)throw new Error('当前用户或模型身份不可用，未应用单次设置')
  try{nextSettingsStore.apply(scope,value)}catch{throw new Error('浏览器暂存失败，单次设置未应用；原设置保留')}
  nextOverrides.value={...value};nextSettingsNotice.value='单次设置已应用；在当前标签页刷新后保留，尚未消耗'
}
function startupSettingsMatch(){
  const p=startupNextSettings,model=p&&modelProfiles.value.find(m=>m.id===p.scope.model)
  return p?.phase==='ready'&&p.scope.user===authUser.value&&p.scope.character===activeCharacter.value&&(p.scope.conversation===null||conversations.value.some(c=>c.id===p.scope.conversation))&&model?.enabled&&modelRevision(model)===p.scope.revision
}
async function restoreNextSettings(){
  const p=startupNextSettings
  if(!p)return
  if(p.phase==='sending'){clearNextSettings('上次请求曾提交，接受状态未确认；未恢复单次设置，以免重复使用。可查看请求详情');return}
  if(!sameScope(p.scope,nextSettingsScope.value)){clearNextSettings('用户、角色、会话或模型配置已变化，未恢复原单次设置');return}
  try{
    await api('/api/chat/context-preview',{method:'POST',body:JSON.stringify({character_id:activeCharacter.value,conversation_id:activeConversation.value,model_profile_id:modelProfileId.value,message:'',overrides:p.value})})
    if(!sameScope(p.scope,nextSettingsScope.value))throw new Error('恢复期间身份发生变化')
    nextOverrides.value={...p.value};nextSettingsNotice.value='已恢复此前应用且未消耗的单次设置，身份及参数校验通过'
  }catch(e){clearNextSettings('单次设置未恢复：'+(e instanceof Error?e.message:'参数校验失败'))}
}
function reserveNextSettings(){
  if(!Object.keys(nextOverrides.value).length)return null
  const scope=nextSettingsScope.value
  if(!scope)throw new Error('单次设置身份不可用，请重新应用')
  const ticket=nextSettingsStore.reserve(scope)
  if(!ticket)throw new Error('已应用的单次设置暂存缺失，请重新应用')
  nextOverrides.value={};nextSettingsNotice.value='单次设置正在随请求提交，等待后端接受'
  return ticket
}
function rejectNextSettings(ticket:PendingSettings|null){
  if(!ticket)return
  try{const p=nextSettingsStore.reject(ticket,nextSettingsScope.value);if(p){nextOverrides.value={...p.value};nextSettingsNotice.value='后端拒绝请求，单次设置保留且尚未消耗'}}catch{nextSettingsNotice.value='请求被拒绝，但浏览器暂存恢复失败，请重新应用单次设置'}
}
function consumeNextSettings(ticket:PendingSettings|null){
  if(!ticket)return
  try{if(nextSettingsStore.read()?.id!==ticket.id)return}catch{return}
  try{nextSettingsStore.consume(ticket)}catch{} // A persisted sending marker also prevents replay.
  if(sameScope(ticket.scope,nextSettingsScope.value)&&!Object.keys(nextOverrides.value).length)nextSettingsNotice.value='请求已获后端接受，单次设置已消耗；刷新不会恢复'
}
watch(nextSettingsScope,(scope,previous)=>{if(nextSettingsReady.value&&JSON.stringify(scope)!==JSON.stringify(previous)){try{clearNextSettings('已切换用户、角色、会话或模型配置，单次设置已清除')}catch{nextOverrides.value={};nextSettingsNotice.value='单次设置已停用，但浏览器暂存清除失败'}}},{flush:'sync'})
let chatUiEpoch=0
async function reloadEnhanced(cid:number){stopAutoRead();stopSpeaking();chatUiEpoch++;activeConversation.value=cid;enhancementMessage.value=null;pendingQuote.value=null;await loadConversations();await loadMessages(cid)}
function inspectMessage(message:Message){enhancementMessage.value=message;showChatPanel('enhance')}
function acceptQuote(quote:{message_id:number;excerpt:string}){pendingQuote.value=quote;closeChatPanel()}
async function copyChatMessage(message:Message){try{await navigator.clipboard.writeText(message.content)}catch{errorMessage.value='复制失败，请选择正文复制'}}
function quoteChatMessage(message:Message){if(!message.id)return;const selected=window.getSelection()?.toString()||'';pendingQuote.value={message_id:message.id,excerpt:selected&&message.content.includes(selected)?selected:message.content}}
function leaveChatDraft(){if(enhancementRef.value&&!enhancementRef.value.canClose())return false;return !(pendingAttachments.value.length||pendingQuote.value)||window.confirm('附件或引用尚未发送，离开此对话并保留文件在本地？')}
const chatPanel = ref<ChatPanel|null>(null)
const chatPanelTitle = computed(() => ({conversations:'对话管理',voice:'语音与通话',read:'自动朗读',memory:'本条记忆补查',model:'当前模型',status:'当前状态',enhance:'消息、请求与上下文'})[chatPanel.value ?? 'status'])
const scribeStatus = ref('')
const scribeControl = shallowRef<{finish:()=>void;stop:()=>void}|null>(null)
function setScribeControl(value:unknown) { scribeControl.value=value as typeof scribeControl.value }
function closeChatPanel() {
  if(chatPanel.value==='enhance' && enhancementRef.value && !enhancementRef.value.canClose())return false
  if(chatPanel.value==='voice' && !callId.value) { callAcknowledged.value=false; bargeInRequested.value=false }
  chatPanel.value=null
  return true
}
function showChatPanel(panel:ChatPanel) { if(chatPanel.value==='enhance'&&panel!=='enhance'&&!closeChatPanel())return;chatPanel.value=panel;if(panel==='memory')void loadChronicleChatStatus() }
const pendingToolCount = computed(() => messages.value.flatMap(message=>message.toolRuns??[]).filter(run=>run.status==='pending_confirmation'||run.status==='running').length)
const chatNotice = computed(() => errorMessage.value || scribeStatus.value || speechStatus.value || callStatus.value ||
  (continuousDaily && callAvailability.value && !callId.value && callAvailability.value.available_rounds<1 ? '通话暂不可开始；请查看语音额度与当前对话状态。' : ''))
watch(draft, () => { clearChronicleChatConsent() })
watch([activeConversation,activeCharacter], () => { scribeStatus.value=''; closeChatPanel() })

const openWindows = ref<WindowInstance[]>([])
const fullPageKind = ref<FeatureKind | null>(null)
const zCounter = ref(10)
const dragState = ref<{ id: string; dx: number; dy: number } | null>(null)
const current = computed(() => characters.value.find(c => c.id === activeCharacter.value) ?? characters.value[0])
const activeWindows = computed(() => [...openWindows.value].sort((a, b) => a.zIndex - b.zIndex))
const filteredConversations = computed(() => { const q=conversationSearch.value.trim().toLowerCase(); return q ? conversations.value.filter(item => (item.title || '新的对话').toLowerCase().includes(q)) : conversations.value })
const dateLabel = computed(() => new Date().toLocaleDateString('zh-CN', { month: 'long', day: 'numeric' }))
function timeOf(value?: string) { return value ? new Date(value).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) }
function characterFromApi(item: any, index: number): Character { return { id: item.id, name: item.name, subtitle: item.subtitle || '等待你的设定', mood: item.mood || '正在等待新的对话', color: palette[index % palette.length], online: true, last: item.greeting || '创建一个属于你的角色。', system_prompt: item.system_prompt || '', greeting: item.greeting || '', enabled: item.enabled !== false, default_model_id: item.default_model_id ?? null } }
async function api<T>(url: string, options?: RequestInit): Promise<T> { const response = await fetch(url, { credentials:'same-origin', headers: { 'Content-Type': 'application/json', ...(options?.headers || {}) }, ...options }); if(response.status===401&&!url.startsWith('/api/auth/')){authenticated.value=false;authReady.value=true;openWindows.value=[];messages.value=[]} if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || `请求失败（${response.status}）`); if (response.status === 204) return undefined as T; return response.json() }
async function loadPlugins(){try{plugins.value=await api<Plugin[]>("/api/plugins")}catch{plugins.value=[]}}
async function importPluginFile(event:Event){const input=event.target as HTMLInputElement;const file=input.files?.[0];if(!file)return;pluginBusy.value=true;pluginStatus.value="";try{if(file.size>256*1024)throw new Error("插件清单文件不能超过 256 KB");const manifest=JSON.parse(await file.text());const saved=await api<Plugin>("/api/plugins/install",{method:"POST",body:JSON.stringify(manifest)});plugins.value.unshift(saved);pluginStatus.value=`已安装 ${saved.name} v${saved.version}。为安全起见，插件和工具均默认停用。`}catch(error){pluginStatus.value=error instanceof Error?error.message:"插件清单无效"}finally{pluginBusy.value=false;input.value=""}}
async function togglePlugin(plugin:Plugin){pluginBusy.value=true;try{const result=await api<Plugin>(`/api/plugins/${plugin.id}`,{method:"PATCH",body:JSON.stringify({enabled:!plugin.enabled})});Object.assign(plugin,result);pluginStatus.value=plugin.enabled?"插件已启用；其中各项工具仍需分别授权。":"插件已停用"}catch(e){pluginStatus.value=e instanceof Error?e.message:"更新插件状态失败"}finally{pluginBusy.value=false}}
async function togglePluginTool(plugin:Plugin,tool:PluginTool){try{const result=await api<{enabled:boolean}>(`/api/plugins/tools/${tool.id}`,{method:"PATCH",body:JSON.stringify({enabled:!tool.enabled})});tool.enabled=result.enabled;pluginStatus.value=tool.enabled?"工具已授权；每次执行仍需要你确认。":"工具已停用"}catch(e){pluginStatus.value=e instanceof Error?e.message:"更新工具权限失败"}}
async function removePlugin(plugin:Plugin){if(!window.confirm(`卸载插件“${plugin.name}”？这只会移除插件清单和授权，不会删除 MCP 服务或聊天记录。`))return;try{await api(`/api/plugins/${plugin.id}`,{method:"DELETE"});plugins.value=plugins.value.filter(p=>p.id!==plugin.id);pluginStatus.value="插件已卸载"}catch(e){pluginStatus.value=e instanceof Error?e.message:"卸载失败"}}
function downloadPluginExample(){const server=mcpServers.value.find(item=>item.id===mcpSelectedId.value)||mcpServers.value[0];const enabledTools=server&&server.id===mcpSelectedId.value?mcpTools.value.filter(tool=>tool.enabled):[];const refs=enabledTools.length?enabledTools.map(tool=>({server_id:server!.id,tool_name:tool.name,alias:tool.name})): [{server_id:server?.id||1,tool_name:"replace-with-refreshed-tool-name",alias:"请替换为工具显示名"}];const example={id:"sample.echo.pack",name:"示例 MCP 工具包",version:"1.0.0",description:"只将已发现的 MCP 工具整理成一个可单独授权的包；不会运行代码",author:"你",permissions:["调用清单中列出的 MCP 工具"],tools:refs};const blob=new Blob([JSON.stringify(example,null,2)],{type:"application/json"});const url=URL.createObjectURL(blob);const a=document.createElement("a");a.href=url;a.download="yosaki-plugin-example.json";a.click();URL.revokeObjectURL(url)}

async function loadMcp() { try { mcpServers.value=await api<McpServer[]>('/api/mcp/servers'); if(mcpSelectedId.value && mcpServers.value.some(s=>s.id===mcpSelectedId.value)) await loadMcpTools(mcpSelectedId.value) } catch { mcpServers.value=[] } }
async function loadMcpTools(id:number) { try { mcpSelectedId.value=id; mcpTools.value=await api<McpTool[]>(`/api/mcp/servers/${id}/tools`) } catch { mcpTools.value=[] } }
async function saveMcp() { mcpBusy.value=true; try { const saved=await api<McpServer>(mcpSelectedId.value?`/api/mcp/servers/${mcpSelectedId.value}`:'/api/mcp/servers',{method:mcpSelectedId.value?'PATCH':'POST',body:JSON.stringify({...mcpDraft.value,auth_secret:mcpDraft.value.auth_secret||null})}); const i=mcpServers.value.findIndex(s=>s.id===saved.id); if(i>=0)mcpServers.value[i]=saved;else mcpServers.value.unshift(saved);mcpSelectedId.value=saved.id;mcpStatus.value='配置已保存，下一步可以连接并刷新工具';mcpDraft.value.auth_secret='' } catch(e){mcpStatus.value=e instanceof Error?e.message:'保存失败'} finally{mcpBusy.value=false} }
async function connectMcp() { if(!mcpSelectedId.value){mcpStatus.value='先保存一个 MCP 服务';return};mcpBusy.value=true;try{const r=await api<any>(`/api/mcp/servers/${mcpSelectedId.value}/connect`,{method:'POST'});mcpStatus.value=`已连接：${r.server_info?.name||'服务已响应'}`;await refreshMcpTools()}catch(e){mcpStatus.value=e instanceof Error?e.message:'连接失败'}finally{mcpBusy.value=false} }
async function refreshMcpTools() { if(!mcpSelectedId.value)return;mcpBusy.value=true;try{const r=await api<any>(`/api/mcp/servers/${mcpSelectedId.value}/refresh-tools`,{method:'POST'});mcpStatus.value=`已刷新 ${r.tools_count} 个工具`;await loadMcpTools(mcpSelectedId.value)}catch(e){mcpStatus.value=e instanceof Error?e.message:'刷新工具失败'}finally{mcpBusy.value=false} }
async function setMcpToolEnabled(tool:McpTool){try{const saved=await api<McpTool>(`/api/mcp/tools/${tool.id}`,{method:'PATCH',body:JSON.stringify({enabled:!tool.enabled})});tool.enabled=saved.enabled;mcpStatus.value=tool.enabled?'工具已允许模型提出调用（执行仍需你确认）':'工具已停用，不会提供给模型'}catch(e){mcpStatus.value=e instanceof Error?e.message:'更新权限失败'}}
function editMcp(server:McpServer){mcpSelectedId.value=server.id;mcpDraft.value={name:server.name,transport:server.transport,endpoint:server.endpoint,auth_type:server.auth_type,auth_secret:''};void loadMcpTools(server.id)}
function refreshSpeechVoices(){ if(typeof window==='undefined'||!('speechSynthesis' in window))return; speechVoices.value=window.speechSynthesis.getVoices(); if(selectedVoiceURI.value&&!speechVoices.value.some(v=>v.voiceURI===selectedVoiceURI.value))selectedVoiceURI.value=''; }
function saveVoiceSettings(){localStorage.setItem('YOSAKI.voice.locale',voiceLocale.value);localStorage.setItem('YOSAKI.voice.rate',String(voiceRate.value));localStorage.setItem('YOSAKI.voice.voiceURI',selectedVoiceURI.value);speechStatus.value='语音偏好已保存在当前浏览器。'}
function speakText(text:string,key:string|number){if(!('speechSynthesis' in window)){speechStatus.value='此浏览器不支持系统语音朗读。';return};voiceAudioPlayer.stop();window.speechSynthesis.cancel();const utterance=new SpeechSynthesisUtterance(text);utterance.lang=voiceLocale.value;utterance.rate=voiceRate.value;utterance.voice=speechVoices.value.find(v=>v.voiceURI===selectedVoiceURI.value)||speechVoices.value.find(v=>v.lang.toLowerCase().startsWith(voiceLocale.value.toLowerCase()))||null;utterance.onstart=()=>{speakingMessageId.value=key;speechStatus.value='正在朗读'};utterance.onend=()=>{speakingMessageId.value=null;speechStatus.value='朗读完成'};utterance.onerror=()=>{speakingMessageId.value=null;speechStatus.value='朗读失败或被系统中断'};window.speechSynthesis.speak(utterance)}
function speakMessage(message:Message,index:number){speakText(message.content, message.id ?? `message-${index}`)}
function stopSpeaking(){if('speechSynthesis' in window)window.speechSynthesis.cancel();speakingMessageId.value=null;speechStatus.value='朗读已停止'}
async function startSpeechRecognition(allowRemote=false){const Recognition=(window as any).SpeechRecognition||(window as any).webkitSpeechRecognition;if(!Recognition){speechStatus.value='当前浏览器没有可用的语音识别接口。';return};let localAvailable=false;if(typeof Recognition.available==='function'){try{localAvailable=await Recognition.available({langs:[voiceLocale.value],processLocally:true})==='available'}catch{localAvailable=false}}if(!localAvailable&&!allowRemote){speechConsentPrompt.value=true;return};speechConsentPrompt.value=false;try{const recognizer=new Recognition();activeRecognition.value=recognizer;recognizer.lang=voiceLocale.value;recognizer.continuous=false;recognizer.interimResults=true;if('processLocally' in recognizer)recognizer.processLocally=localAvailable;let receivedFinalText=false;let recognitionError=false;recognizer.onstart=()=>{speechRecognitionRunning.value=true;speechStatus.value=localAvailable?'本机识别中…':'浏览器语音识别服务处理中…'};recognizer.onresult=(event:any)=>{let finalText='';let interimText='';for(let i=event.resultIndex;i<event.results.length;i++){const result=event.results[i];if(result.isFinal)finalText+=result[0].transcript;else interimText+=result[0].transcript}if(finalText.trim()){draft.value=[draft.value.trim(),finalText.trim()].filter(Boolean).join(' ');receivedFinalText=true;speechStatus.value='转写完成，文字已放入输入框；请检查后再发送。'}else if(interimText)speechStatus.value=`识别中：${interimText}`};recognizer.onerror=(event:any)=>{recognitionError=true;speechStatus.value=event.error==='not-allowed'?'麦克风权限未允许。':`语音识别失败：${event.error||'未知错误'}`};recognizer.onend=()=>{speechRecognitionRunning.value=false;activeRecognition.value=null;if(receivedFinalText)speechStatus.value='转写完成，文字已放入输入框；请检查后再发送。';else if(!recognitionError)speechStatus.value='识别结束，没有收到可用文字。'};recognizer.start()}catch(error){speechRecognitionRunning.value=false;speechStatus.value=error instanceof Error?`无法启动语音识别：${error.message}`:'无法启动语音识别。'}}
function requestSpeechInput(){if(scribeRunning.value)return;if(speechRecognitionRunning.value){stopSpeechRecognition();return};stopAutoRead();stopSpeaking();void startSpeechRecognition(false)}
function stopSpeechRecognition(){try{activeRecognition.value?.stop();speechStatus.value='正在停止识别…'}catch{speechRecognitionRunning.value=false;activeRecognition.value=null}}
function confirmRemoteSpeech(){speechConsentPrompt.value=false;void startSpeechRecognition(true)}
function onSpeechVoicesChanged(){refreshSpeechVoices()}

async function loadMemories() { if (!apiOnline.value) return; try { const query = encodeURIComponent(memorySearch.value); memories.value = await api<Memory[]>(`/api/memories?character_id=${activeCharacter.value}&conversation_id=${activeConversation.value || ''}&q=${query}`); const savedSettings = await api<typeof contextSettings.value>(`/api/characters/${activeCharacter.value}/context-settings`); contextSettings.value = {...savedSettings, memory_enabled:!!savedSettings.memory_enabled, monologue_enabled:!!savedSettings.monologue_enabled}; if (activeConversation.value) { const summary = await api<{content:string;enabled:boolean}>(`/api/conversations/${activeConversation.value}/summary`); summaryDraft.value = summary.content || ''; summaryEnabled.value = !!summary.enabled } } catch { memories.value = [] } }
async function createMemory() { if (!memoryDraft.value.content.trim()) return; memoryBusy.value=true; try { const created=await api<Memory>('/api/memories',{method:'POST',body:JSON.stringify({character_id:activeCharacter.value,conversation_id:memoryDraft.value.scope==='temporary'?activeConversation.value:null,scope:memoryDraft.value.scope,content:memoryDraft.value.content,enabled:true})}); memories.value.push(created); memoryDraft.value.content=''; promptNotice.value='记忆已保存' } catch(error) { promptNotice.value=error instanceof Error?error.message:'记忆保存失败' } finally { memoryBusy.value=false } }
async function toggleMemory(memory: Memory) { if(memory.managed_by_learning){promptNotice.value='这条记忆由长期学习管理，请在那里审阅或归档';return} try{const saved=await api<Memory>(`/api/memories/${memory.id}`,{method:'PUT',body:JSON.stringify({...memory,enabled:!memory.enabled})});memory.enabled=saved.enabled;promptNotice.value='记忆状态已保存'}catch(error){promptNotice.value=error instanceof Error?error.message:'记忆状态更新失败'} }
async function deleteMemory(memory: Memory) { if(memory.managed_by_learning){promptNotice.value='这条记忆由长期学习管理，请在那里审阅或归档';return} try{await api(`/api/memories/${memory.id}`,{method:'DELETE'});memories.value=memories.value.filter(item=>item.id!==memory.id);promptNotice.value='记忆已删除'}catch(error){promptNotice.value=error instanceof Error?error.message:'删除记忆失败'} }
async function saveContextSettings() { await api(`/api/characters/${activeCharacter.value}/context-settings`,{method:'PUT',body:JSON.stringify(contextSettings.value)}); promptNotice.value='上下文设置已保存' }
async function saveSummary() { if (!activeConversation.value) { promptNotice.value='先打开或创建一个对话，才能保存摘要'; return }; await api(`/api/conversations/${activeConversation.value}/summary`,{method:'PUT',body:JSON.stringify({content:summaryDraft.value,enabled:summaryEnabled.value})}); promptNotice.value='对话摘要已保存' }
async function loadPromptBlocks() { try { promptBlocks.value = await api<PromptBlock[]>(`/api/characters/${activeCharacter.value}/prompt-blocks`) } catch { promptBlocks.value = [] } }
async function addPromptBlock() { if (!apiOnline.value) { errorMessage.value = '后端未连接，无法添加提示词块'; return }; const created = await api<PromptBlock>(`/api/characters/${activeCharacter.value}/prompt-blocks`, { method: 'POST', body: JSON.stringify({ name: '新的提示词块', content: '在这里写入固定规则或记忆。', injection_position: 'character_profile', order_index: promptBlocks.value.length, enabled: true }) }); promptBlocks.value.push(created) }
async function savePromptBlock(block: PromptBlock) {
  try { await api(`/api/prompt-blocks/${block.id}`, { method: 'PATCH', body: JSON.stringify(block) }); promptNotice.value = '已保存'; previewMessages.value = [] }
  catch { promptNotice.value = '保存失败，请检查后端连接和内容后重试'; }
}
const promptNotice = ref('')
const previewMessages = ref<{role:string;content:string}[]>([])
const previewBusy = ref(false)
const previewChronicle=ref<any>(null)
const previewWire=ref<any>(null)
const draggedPrompt = ref<number | null>(null)
const deletingPrompt = ref<number | null>(null)
async function removePrompt(id:number) {
  try { await api(`/api/prompt-blocks/${id}`, {method:'DELETE'}); promptBlocks.value = promptBlocks.value.filter(b=>b.id!==id); deletingPrompt.value=null; previewMessages.value=[]; promptNotice.value='已删除' }
  catch { promptNotice.value='删除失败，原数据未从界面移除' }
}
async function movePrompt(id:number, target:number) {
  const list=[...promptBlocks.value]; const from=list.findIndex(b=>b.id===id);
  if(from<0 || target<0 || target>=list.length) return;
  list.splice(target,0,list.splice(from,1)[0]);
  try { promptBlocks.value=await api<PromptBlock[]>(`/api/characters/${activeCharacter.value}/prompt-blocks/reorder`, {method:'POST',body:JSON.stringify({ids:list.map(b=>b.id)})}); previewMessages.value=[]; promptNotice.value='顺序已保存（位置优先，同位置按此顺序）' }
  catch { promptNotice.value='排序失败，已保留原顺序' }
}
async function previewContext() {
  previewBusy.value=true; previewMessages.value=[]; previewChronicle.value=null;previewWire.value=null;
  try { const result=await api<{messages:{role:string;content:string}[];chronicle:any;serialized:any;parameters:any;sources:any}>('/api/chat/context-preview',{method:'POST',body:JSON.stringify({character_id:activeCharacter.value,conversation_id:activeConversation.value,message:draft.value,model_profile_id:modelProfileId.value,overrides:nextOverrides.value,attachment_ids:pendingAttachments.value.map(x=>x.id),quote:pendingQuote.value})}); previewMessages.value=result.messages; previewChronicle.value=result.chronicle;previewWire.value={serialized:result.serialized,parameters:result.parameters,sources:result.sources}; promptNotice.value='预览已保存配置；不调用模型、不产生模型费用。未保存编辑不包含在内。' }
  catch { promptNotice.value='预览失败，请确认后端已更新并启动' }
  finally { previewBusy.value=false }
}
async function exportBackup(){if(backupPassphrase.value.length<12){backupStatus.value='请设置至少 12 个字符的备份密码；YOSAKI 不会保存这个密码。';return};backupBusy.value=true;backupStatus.value='';backupRequestAbort=new AbortController();try{const form=new FormData();form.set('passphrase',backupPassphrase.value);const response=await fetch('/api/system/backup/v2/prepare',{method:'POST',credentials:'same-origin',body:form,signal:backupRequestAbort.signal});if(response.status===503 && (await response.clone().json().catch(()=>({}))).detail==='H5-A 备份入口尚未启用'){const legacy=await fetch('/api/system/backup/export',{method:'POST',credentials:'same-origin',body:form,signal:backupRequestAbort.signal});if(!legacy.ok){const error=await legacy.json().catch(()=>({}));throw new Error(error.detail||`旧版备份失败（${legacy.status}）`)}const url=URL.createObjectURL(await legacy.blob());const link=document.createElement('a');link.href=url;link.download='yosaki-backup-v1.ybackup';link.click();setTimeout(()=>URL.revokeObjectURL(url),30000);backupStatus.value='旧版加密备份已导出（数据库上限 64 MiB）；请保管密码。大容量 V2 尚未启用。';return}const body=await response.json().catch(()=>({}));if(!response.ok)throw new Error(body.detail||`备份准备失败（${response.status}）`);const formDownload=document.createElement('form');formDownload.method='POST';formDownload.action='/api/system/backup/v2/download';formDownload.style.display='none';const ticketInput=document.createElement('input');ticketInput.type='hidden';ticketInput.name='ticket';ticketInput.value=body.download_ticket;formDownload.append(ticketInput);document.body.append(formDownload);formDownload.submit();setTimeout(()=>formDownload.remove(),1000);backupStatus.value='V2 加密备份已验证并开始由浏览器直接下载；请确认文件保存完成，并将备份密码分开保管。若浏览器无法保存，请改用受保护本地目录备份，不要让页面读取整份文件。'}catch(e){backupStatus.value=e instanceof DOMException && e.name==='AbortError'?'已中止页面请求；服务端清理以取消接口结果为准。':e instanceof Error?e.message:'备份准备失败'}finally{backupBusy.value=false;backupRequestAbort=null}}
async function cancelBackupOperation(){const response=await fetch('/api/system/backup/v2/abort',{method:'POST',credentials:'same-origin'}).catch(()=>null);backupRequestAbort?.abort();backupStatus.value=response?.ok?'已请求取消，服务端正在清理本次临时文件。':'已中止页面请求；若是旧版备份，服务端完成状态尚未确认。'}
async function importBackup(event:Event){const input=event.target as HTMLInputElement;const file=input.files?.[0];if(!file)return;try{if(file.size>1088*1024*1024)throw new Error('备份文件超过当前 H5 隔离准入上限；请改用经审核的离线恢复流程');if(backupPassphrase.value.length<12)throw new Error('请输入创建该备份时使用的至少 12 位密码');backupBusy.value=true;backupRequestAbort=new AbortController();const form=new FormData();form.set('passphrase',backupPassphrase.value);form.set('backup',file,file.name);const response=await fetch('/api/system/backup/verify',{method:'POST',credentials:'same-origin',body:form,signal:backupRequestAbort.signal});const body=await response.json().catch(()=>({}));if(!response.ok)throw new Error((Array.isArray(body.detail)?body.detail.map((x:any)=>x.msg).join('；'):body.detail)||`验证失败（${response.status}）`);backupStatus.value=`V${body.format_version} 备份已在隔离副本验证，SHA-256：${body.database_sha256}。当前工作区未改变；正式数据切换需单独审核。`}catch(e){backupStatus.value=e instanceof DOMException && e.name==='AbortError'?'已中止页面请求；服务端清理以取消接口结果为准。':e instanceof Error?e.message:'备份文件无效或验证失败'}finally{backupBusy.value=false;backupRequestAbort=null;input.value=''}}

async function loadWorkspaceData(){
  nextSettingsReady.value=false;nextOverrides.value={};startupNextSettings=null
  try{startupNextSettings=nextSettingsStore.read()}catch{try{nextSettingsStore.clear()}catch{}nextSettingsNotice.value='已暂存的单次设置无效或不可读取，未恢复'}
  restoreLayout();const directPage=routeWindowKind(window.location.pathname);const linkedWindow=directPage??queryWindowKind()??initialWindowKind
  if(linkedWindow)openWindow(linkedWindow,false);fullPageKind.value=directPage
  try{await Promise.all([loadModels(),loadMcp(),loadPlugins(),loadCharacters(),refreshAutoBudget()]);await restoreNextSettings()}
  finally{startupNextSettings=null;nextSettingsReady.value=true}
}
async function refreshAuth(){authError.value='';try{const state=await fetch('/api/auth/status',{credentials:'same-origin'}).then(async r=>{if(!r.ok)throw new Error('登录服务暂不可用');return r.json()});setupRequired.value=!!state.setup_required;authUser.value=state.authenticated?state.user?.username??'':'';authenticated.value=!!state.authenticated;authReady.value=true;if(authenticated.value)await loadWorkspaceData()}catch(e){authReady.value=true;authUser.value='';authenticated.value=false;authError.value=e instanceof Error?e.message:'无法连接登录服务'}}
async function submitAuth(){authError.value='';if(setupRequired.value&&!/^[A-Za-z0-9_.-]{3,40}$/.test(authDraft.value.username)){authError.value='账户名称需为 3–40 位英文、数字、点、下划线或连字符。';return}if(authDraft.value.password.length<12){authError.value='密码至少需要 12 个字符。';return};if(setupRequired.value&&authDraft.value.password!==authDraft.value.confirmPassword){authError.value='两次输入的密码不一致。';return};authBusy.value=true;try{const path=setupRequired.value?'/api/auth/setup':'/api/auth/login';const response=await fetch(path,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:authDraft.value.username,password:authDraft.value.password})});const body=await response.json().catch(()=>({}));if(!response.ok)throw new Error((Array.isArray(body.detail)?body.detail.map((x:any)=>x.msg).join('；'):body.detail)||`请求失败（${response.status}）`);authDraft.value.password='';authDraft.value.confirmPassword='';await refreshAuth()}catch(e){authError.value=e instanceof Error?e.message:'认证失败'}finally{authBusy.value=false}}
async function logout(){if(!confirmLeaveCharacterDraft())return;if(callId.value)void endContinuousCall("已退出登录，通话结束。");stopAutoRead();authBusy.value=true;try{await fetch('/api/auth/logout',{method:'POST',credentials:'same-origin'});authenticated.value=false;authUser.value='';nextSettingsReady.value=false;openWindows.value=[];messages.value=[];modelProfiles.value=[];mcpServers.value=[];plugins.value=[];workspaceView.value='welcome';window.history.pushState({},'','/');saveLayout()}catch(e){authError.value=e instanceof Error?e.message:'退出失败'}finally{authBusy.value=false}}

async function loadModels() {
  const ticket=++modelLoadEpoch,editorTicket=modelEditorEpoch
  try {
    const profiles=await api<ModelProfile[]>('/api/models')
    if(ticket!==modelLoadEpoch||!authenticated.value)return
    modelProfiles.value=profiles
    if(!modelProfileId.value&&profiles.length){modelProfileId.value=profiles[0].id;model.value=profiles[0].name}
    if(!modelEditorReady.value&&editorTicket===modelEditorEpoch){
      let preferred:number|null=null
      try{preferred=Number(localStorage.getItem(MODEL_EDITOR_KEY))||null}catch{}
      const selected=profiles.find(p=>p.id===preferred)??profiles.find(p=>p.id===modelProfileId.value)??profiles[0]
      if(selected)hydrateModel(selected)
      else modelEditorReady.value=true
    }
  }catch(e){if(ticket===modelLoadEpoch){modelFeedbackError.value=true;modelTestStatus.value='模型档案读取失败：'+(e instanceof Error?e.message:'请重试')}}
}
async function loadChronicleChatStatus() {
  chronicleChatCap.value = 0
  if (!chronicleAvailable || !authenticated.value) return
  try {
    const status = await api<{enabled:boolean;maxMicrousd:number;workspaceEnabled?:boolean;reason?:string;ratesVerifiedDate?:string;utcDate?:string}>('/api/chat/chronicle-supplement-status')
    if (!authenticated.value) return
    chronicleChatInstalled.value = status.workspaceEnabled ?? chronicleAvailable
    if (status.enabled) chronicleChatCap.value = status.maxMicrousd
    else clearChronicleChatConsent()
    chronicleChatStatusText.value = status.reason === 'chronicle_daily_rates_verification_required'
      ? `补查核价已到期（上次 ${status.ratesVerifiedDate || '未核验'}；当前 UTC ${status.utcDate || '未知'}）。本条不会发送补查请求；普通聊天与本地召回仍可使用。`
      : '记忆补查当前未启用；普通聊天与本地召回仍可使用。'
  } catch { clearChronicleChatConsent();chronicleChatStatusText.value='无法读取补查状态，本条不发起补查；普通聊天仍可使用。' }
}
watch(authenticated, () => { void loadChronicleChatStatus() })
watch([activeCharacter,activeConversation,modelProfileId,authenticated], () => { clearChronicleChatConsent() })
function hydrateModel(profile:ModelProfile){
  editingModelId.value=profile.id;modelEditorReady.value=true;modelKeyInput.value=''
  modelDraft.value={name:profile.name,provider_name:profile.provider_name,base_url:profile.base_url,model_name:profile.model_name,protocol:profile.protocol,temperature:profile.temperature,top_p:profile.top_p,max_tokens:profile.max_tokens,context_limit:profile.context_limit,supports_streaming:profile.supports_streaming,enabled:profile.enabled,reasoning_effort:profile.reasoning_effort}
  try{localStorage.setItem(MODEL_EDITOR_KEY,String(profile.id))}catch{}
}
function startNewModel(){
  if(modelSaving.value)return
  modelEditorEpoch++;modelEditorReady.value=true;editingModelId.value=null;modelKeyInput.value='';modelTestStatus.value='';modelFeedbackError.value=false
  modelDraft.value={name:'我的模型',provider_name:'OpenAI Compatible',base_url:'https://api.openai.com/v1',model_name:'',protocol:'openai_compatible',temperature:0.8,top_p:1,max_tokens:2048,context_limit:32768,supports_streaming:true,enabled:true,reasoning_effort:null}
}
function editModel(profile:ModelProfile){if(modelSaving.value)return;modelEditorEpoch++;hydrateModel(profile);modelTestStatus.value='';modelFeedbackError.value=false}
async function saveModel(){
  if(modelSaving.value)return
  if(!apiOnline.value){modelFeedbackError.value=true;modelTestStatus.value='后端未连接，无法保存';return}
  const ticket=++modelEditorEpoch;modelLoadEpoch++
  const id=editingModelId.value,payload={...modelDraft.value,api_key:modelDraft.value.protocol==='chatgpt_plan'?null:modelKeyInput.value||null}
  modelSaving.value=true;modelTestStatus.value='';modelFeedbackError.value=false
  try{
    const saved=await api<ModelProfile>(id?`/api/models/${id}`:'/api/models',{method:id?'PATCH':'POST',body:JSON.stringify(payload)})
    if(ticket!==modelEditorEpoch||!authenticated.value)return
    if(saved.reasoning_effort!==payload.reasoning_effort)throw new Error('后端返回的推理强度与提交值不一致，请重新读取档案核对')
    const index=modelProfiles.value.findIndex(p=>p.id===saved.id)
    if(index>=0)modelProfiles.value[index]=saved;else modelProfiles.value.unshift(saved)
    modelProfileId.value=saved.id;model.value=saved.name;hydrateModel(saved)
    modelTestStatus.value='档案已保存；推理强度：'+effortName(saved.reasoning_effort)+'。刷新后会重新读取此档案。'
  }catch(e){if(ticket===modelEditorEpoch){modelFeedbackError.value=true;modelTestStatus.value='保存失败：'+(e instanceof Error?e.message:'请重试')}}
  finally{modelSaving.value=false}
}
async function testModel() { if (!editingModelId.value) { modelTestStatus.value = '请先保存配置，再测试连接'; return }; modelTestStatus.value = '正在发送真实测试请求…'; try { const result = await api<{ reply_preview: string }>(`/api/models/${editingModelId.value}/test`, { method: 'POST' }); modelTestStatus.value = `连接成功：${result.reply_preview || '服务已响应'}` } catch (error) { modelTestStatus.value = error instanceof Error ? error.message : '连接测试失败' } }
const characterStatus=ref('')
const characterBusy=ref(false)
const characterDeletePending=ref(false)
const characterCardBusy=ref(false)
const characterCardFileInput=ref<HTMLInputElement|null>(null)
type CharacterDraft = Pick<Character, 'name'|'subtitle'|'mood'|'system_prompt'|'greeting'|'enabled'|'default_model_id'>
const savedCharacterDrafts = ref<Record<number, CharacterDraft>>({})
function characterDraft(character: Character): CharacterDraft { return { name: character.name, subtitle: character.subtitle, mood: character.mood, system_prompt: character.system_prompt, greeting: character.greeting, enabled: character.enabled, default_model_id: character.default_model_id } }
function rememberCharacter(character: Character) { savedCharacterDrafts.value[character.id] = characterDraft(character) }
const characterDirty = computed(() => !!current.value && !!savedCharacterDrafts.value[current.value.id] && JSON.stringify(characterDraft(current.value)) !== JSON.stringify(savedCharacterDrafts.value[current.value.id]))
function discardCharacterDraft() { const saved = current.value && savedCharacterDrafts.value[current.value.id]; if (saved) Object.assign(current.value, saved) }
function confirmLeaveCharacterDraft() { if (!characterDirty.value) return true; if (!window.confirm('当前角色资料尚未保存。确定放弃这些修改吗？')) return false; discardCharacterDraft(); return true }
function onCharacterSelection(event: Event) { const input = event.target as HTMLSelectElement; const next = Number(input.value); if (next === activeCharacter.value) return; if (!confirmLeaveCharacterDraft()) { input.value = String(activeCharacter.value); return } void selectCharacter(next) }
function warnUnsavedCharacter(event: BeforeUnloadEvent) { if (!characterDirty.value) return; event.preventDefault(); event.returnValue = '' }
async function persistActiveCharacter(){try{localStorage.setItem('YOSAKI.active-character',String(activeCharacter.value))}catch{}}
async function selectCharacter(characterId:number){if(!leaveChatDraft())return;chatUiEpoch++;abortController.value?.abort();isStreaming.value=false;enhancementHead.value=0;pendingAttachments.value=[];pendingQuote.value=null;if(callId.value)void endContinuousCall("已切换角色，通话结束。");stopAutoRead();activeCharacter.value=characterId;conversations.value=[];activeConversation.value=null;messages.value=[];characterDeletePending.value=false;modelProfileId.value=current.value.default_model_id;model.value=modelProfiles.value.find(profile=>profile.id===modelProfileId.value)?.name||'YOSAKI · demo';await persistActiveCharacter();await Promise.all([loadConversations(),loadPromptBlocks(),loadMemories()]);const latest=conversations.value[0];if(latest)await selectConversation(latest);else{await loadMemories();conversationStatus.value='该角色还没有对话'}}
async function exportCharacterCard(){if(!current.value)return;characterCardBusy.value=true;characterStatus.value='';try{const response=await fetch(`/api/characters/${current.value.id}/export`,{credentials:'same-origin'});if(!response.ok)throw new Error((await response.json().catch(()=>null))?.detail||`导出失败（${response.status}）`);const blob=await response.blob();const link=document.createElement('a');const url=URL.createObjectURL(blob);link.href=url;link.download=`yosaki-character-${current.value.id}.json`;link.click();URL.revokeObjectURL(url);characterStatus.value='角色卡 JSON 已导出；文件不含对话、记忆和密钥。'}catch(error){characterStatus.value=error instanceof Error?error.message:'角色卡导出失败'}finally{characterCardBusy.value=false}}
async function importCharacterCard(event:Event){const input=event.target as HTMLInputElement;const file=input.files?.[0];if(!file)return;characterCardBusy.value=true;characterStatus.value='';try{if(file.size>256*1024)throw new Error('角色卡文件不能超过 256 KB');let card:any;try{card=JSON.parse(await file.text())}catch{throw new Error('文件不是有效的 JSON')};if(card.format!=='yosaki-character-card'||card.version!==1||!card.character)throw new Error('不支持的 YOSAKI 角色卡格式');const blockCount=Array.isArray(card.prompt_blocks)?card.prompt_blocks.length:0;if(!window.confirm(`将导入角色“${card.character.name||'未命名角色'}”和 ${blockCount} 个提示词块，并作为新角色添加；不会覆盖现有角色，也不会导入对话、记忆或默认模型。继续吗？`))return;if(!confirmLeaveCharacterDraft())return;const created=await api<Character>('/api/characters/import',{method:'POST',body:JSON.stringify(card)});const imported=characterFromApi(created,characters.value.length);characters.value.unshift(imported);rememberCharacter(imported);await selectCharacter(created.id);characterStatus.value=`角色卡已导入为新角色“${created.name}”`}catch(error){characterStatus.value=error instanceof Error?error.message:'角色卡导入失败'}finally{characterCardBusy.value=false;input.value=''}}

async function createCharacter(){const name=window.prompt('新角色名称','新角色');if(!name)return;const trimmed=name.trim();if(!trimmed||!confirmLeaveCharacterDraft())return;characterBusy.value=true;characterStatus.value='';try{const created=await api<Character>('/api/characters',{method:'POST',body:JSON.stringify({name:trimmed,subtitle:'由你创建的新角色',mood:'等待第一次对话',system_prompt:'',greeting:'你好，我是新角色。',enabled:true})});const added=characterFromApi(created,characters.value.length);characters.value.unshift(added);rememberCharacter(added);await selectCharacter(created.id);characterStatus.value=`已创建角色：${created.name}`}catch(error){characterStatus.value=error instanceof Error?error.message:'创建角色失败'}finally{characterBusy.value=false}}
async function duplicateCurrentCharacter(){if(!current.value)return;if(!window.confirm(`复制“${current.value.name}”？会复制角色资料与提示词块，不会复制聊天和记忆。`))return;if(!confirmLeaveCharacterDraft())return;characterBusy.value=true;characterStatus.value='';try{const created=await api<Character>(`/api/characters/${current.value.id}/duplicate`,{method:'POST'});const added=characterFromApi(created,characters.value.length);characters.value.unshift(added);rememberCharacter(added);await selectCharacter(created.id);characterStatus.value=`已复制角色：${created.name}`}catch(error){characterStatus.value=error instanceof Error?error.message:'复制角色失败'}finally{characterBusy.value=false}}
async function confirmDeleteCharacter(){if(!current.value)return;if(characters.value.length<=1){characterStatus.value='工作区至少要保留一个角色；请先新建或复制其他角色。';characterDeletePending.value=false;return}if(!characterDeletePending.value){characterDeletePending.value=true;characterStatus.value='删除会同时清空该角色的对话、工具记录、记忆和摘要，无法恢复。';return}characterBusy.value=true;characterStatus.value='';try{const deletedId=current.value.id;await api(`/api/characters/${deletedId}`,{method:'DELETE'});characters.value=characters.value.filter(item=>item.id!==deletedId);delete savedCharacterDrafts.value[deletedId];characterDeletePending.value=false;if(characters.value.length){await selectCharacter(characters.value[0].id)}else{characterStatus.value='工作区至少要保留一个角色。'}characterStatus.value='角色已删除'}catch(error){characterStatus.value=error instanceof Error?error.message:'删除角色失败'}finally{characterBusy.value=false}}
async function loadCharacters() { try { const data = await api<any[]>('/api/characters'); if (data.length) { characters.value = data.map(characterFromApi); savedCharacterDrafts.value = Object.fromEntries(characters.value.map(character => [character.id, characterDraft(character)])); const savedId=Number(localStorage.getItem('YOSAKI.active-character')||0); if (savedId && characters.value.some(c=>c.id===savedId)) activeCharacter.value=savedId; else if (!characters.value.some(c => c.id === activeCharacter.value)) activeCharacter.value = characters.value[0].id; apiOnline.value = true; await loadPromptBlocks(); await loadLatestConversation(); await loadMemories() } } catch { apiOnline.value = false } }
async function exportConversation(conversation:Conversation,format:'json'|'markdown') { try { const response=await fetch(`/api/conversations/${conversation.id}/export?format=${format}`,{credentials:'same-origin'}); if(!response.ok)throw new Error((await response.json().catch(()=>null))?.detail||`导出失败（${response.status}）`);const blob=await response.blob();const link=document.createElement('a');const url=URL.createObjectURL(blob);link.href=url;link.download=`${(conversation.title||'conversation').replace(/[^\p{L}\p{N}._-]+/gu,'_').slice(0,60)||'conversation'}.${format==='json'?'json':'md'}`;link.click();URL.revokeObjectURL(url);conversationStatus.value=`已导出 ${format==='json'?'JSON':'Markdown'}`}catch(error){conversationStatus.value=error instanceof Error?error.message:'导出失败'} }
async function loadConversations(){try{conversations.value=await api<Conversation[]>(`/api/conversations?character_id=${activeCharacter.value}`)}catch{conversations.value=[]}}
async function loadLatestConversation(){
  try{
    await loadConversations()
    // Await the existing shared model load before deciding whether a saved scope can be restored.
    if(!nextSettingsReady.value)await loadModels()
    const restoring=!nextSettingsReady.value&&startupSettingsMatch(),saved=startupNextSettings
    const selected=restoring&&saved?.scope.conversation===null?null:restoring?conversations.value.find(c=>c.id===saved?.scope.conversation)??conversations.value[0]:conversations.value[0]
    activeConversation.value=selected?.id??null
    modelProfileId.value=restoring?saved!.scope.model:selected?.model_profile_id??current.value.default_model_id
    model.value=modelProfiles.value.find(p=>p.id===modelProfileId.value)?.name||'YOSAKI · demo'
    if(selected)await loadMessages(selected.id);else messages.value=[]
  }catch{ /* backend state remains unavailable */ }
}
async function renameConversation(conversation: Conversation) { const next=window.prompt('重命名对话',conversation.title||'新的对话'); if(next===null)return; const title=next.trim(); if(!title)return; try { const saved=await api<Conversation>(`/api/conversations/${conversation.id}`,{method:'PATCH',body:JSON.stringify({title})}); Object.assign(conversation,saved); conversationStatus.value='对话名称已更新' } catch(error) { conversationStatus.value=error instanceof Error?error.message:'重命名失败' } }
async function selectConversation(conversation: Conversation) { if(conversationBusy.value||!leaveChatDraft())return;chatUiEpoch++;abortController.value?.abort();isStreaming.value=false;enhancementHead.value=0;pendingAttachments.value=[];pendingQuote.value=null; if(callId.value)void endContinuousCall("已切换对话，通话结束。"); stopAutoRead(); conversationBusy.value=true; conversationStatus.value=''; try { activeConversation.value=conversation.id; modelProfileId.value=conversation.model_profile_id ?? current.value.default_model_id; model.value=modelProfiles.value.find(profile=>profile.id===modelProfileId.value)?.name||'YOSAKI · demo'; await loadMessages(conversation.id); await loadMemories(); conversationStatus.value=`已切换：${conversation.title||'新的对话'}` } catch(error) { conversationStatus.value=error instanceof Error?error.message:'加载对话失败' } finally { conversationBusy.value=false } }
async function deleteConversation(conversation:Conversation){if(!window.confirm(`删除“${conversation.title||'新的对话'}”？此操作会删除该对话的消息、工具记录和摘要。`))return;conversationBusy.value=true;try{await api(`/api/conversations/${conversation.id}`,{method:'DELETE'});conversations.value=conversations.value.filter(item=>item.id!==conversation.id);if(activeConversation.value===conversation.id){stopAutoRead();activeConversation.value=null;messages.value=[];summaryDraft.value='';await loadMemories()}conversationStatus.value='对话已删除'}catch(error){conversationStatus.value=error instanceof Error?error.message:'删除对话失败'}finally{conversationBusy.value=false}}
async function loadMessages(conversationId: number) { const epoch=chatUiEpoch; const data = await api<any[]>(`/api/conversations/${conversationId}/messages`); const state=await api<any>(`/api/chat/conversations/${conversationId}/state`); if(epoch!==chatUiEpoch||activeConversation.value!==conversationId)return; messages.value = data.map(item => ({ id:item.id, role:item.role, content:item.content||'', model:item.model||undefined, time:timeOf(item.created_at), toolRuns:item.tool_runs, toolBatchRunIds:item.tool_batch_run_ids, toolBatchStatus:item.tool_batch_status, reasoningContent:item.reasoning_content||'', monologueContent:item.monologue_content||'', generationStatus:item.generation_status||'', usage:item.usage_json?JSON.parse(item.usage_json):null,attachments:item.attachments,quotes:item.quotes,reasoningSource:item.reasoning_source })).filter(item => item.role === 'user' || item.role === 'assistant'); enhancementHead.value=state.head }
function rememberRoute() { try { localStorage.setItem('YOSAKI.last-route', window.location.pathname + window.location.search) } catch { /* storage may be unavailable */ } }
function queryWindowKind(): FeatureKind | null { const kind = new URLSearchParams(window.location.search).get('window'); return features.some(item => item.kind === kind) ? kind as FeatureKind : null }
function syncWindowUrl(kind: FeatureKind | null, replace = false) { const target = kind ? `/hub?window=${encodeURIComponent(kind)}` : '/hub'; (replace ? window.history.replaceState : window.history.pushState).call(window.history, {}, '', target); rememberRoute() }
function openFeaturePage(kind: FeatureKind) { workspaceView.value='hub'; desktopVisible.value=false; const existing=openWindows.value.find(item=>item.kind===kind);if(existing){existing.minimized=false;focusWindow(existing.id,false)}else { openWindow(kind,false); } desktopVisible.value=false;fullPageKind.value=kind;window.history.pushState({},'','/'+kind);rememberRoute();clampWindowsToViewport() }
function goHub() { if(!confirmLeaveCharacterDraft())return; workspaceView.value='hub'; fullPageKind.value=null; desktopVisible.value=false; syncWindowUrl(null) }
function goWelcome() { if(!confirmLeaveCharacterDraft())return; closeAll(); workspaceView.value = 'welcome'; window.history.pushState({}, '', '/'); rememberRoute() }
async function newChat() {
  if (conversationBusy.value||!leaveChatDraft()) return
  chatUiEpoch++;abortController.value?.abort();isStreaming.value=false;enhancementHead.value=0;pendingAttachments.value=[];pendingQuote.value=null
  if (callId.value) void endContinuousCall('已新建对话，通话结束。')
  stopAutoRead()
  const characterId=activeCharacter.value
  const selectedModelId=continuousDaily ? (modelProfileId.value ?? current.value.default_model_id) : current.value.default_model_id
  activeConversation.value=null
  messages.value=[]
  modelProfileId.value=selectedModelId
  model.value=modelProfiles.value.find(profile=>profile.id===selectedModelId)?.name||'YOSAKI · demo'
  conversationStatus.value='新对话'
  openFeaturePage('chat')
  if (!continuousDaily || selectedModelId===null) return
  conversationBusy.value=true
  try {
    const conversation=await api<Conversation>('/api/conversations',{method:'POST',body:JSON.stringify({
      character_id:characterId,title:'新的对话',model_profile_id:selectedModelId,
    })})
    await loadConversations()
    if (activeCharacter.value===characterId && activeConversation.value===null && modelProfileId.value===selectedModelId) {
      activeConversation.value=conversation.id
      conversationStatus.value=`已准备通话对话 ${conversation.id}`
    }
  } catch (error) {
    conversationStatus.value=error instanceof Error?error.message:'新对话准备失败'
  } finally { conversationBusy.value=false }
}
function feature(kind: FeatureKind) { return features.find(item => item.kind === kind)! }
function windowLeftInset() { return window.innerWidth > 900 ? (showNav.value ? 232 : 76) : 0 }
function windowTopInset() { return window.innerWidth > 900 ? 86 : 72 }
watch(showNav, () => { clampWindowsToViewport(); saveLayout() })
function openWindow(kind: FeatureKind, syncUrl = true) { desktopVisible.value=true; workspaceView.value = 'hub'; fullPageKind.value=null; const existing = openWindows.value.find(item => item.kind === kind); if (existing) { existing.minimized = false; focusWindow(existing.id,syncUrl) } else { const index = openWindows.value.length; openWindows.value.push({ id: `${kind}-${Date.now()}`, kind, title: feature(kind).title, x: 110 + (index % 4) * 28, y: 72 + (index % 4) * 24, width: kind === 'chat' ? 720 : 560, height: kind === 'chat' ? 650 : 520, zIndex: ++zCounter.value, minimized: false, maximized: false }) }; if(syncUrl)syncWindowUrl(kind); clampWindowsToViewport(); saveLayout() }
function closeWindow(id: string) { const closing=openWindows.value.find(item=>item.id===id);if(closing?.kind==='characters'&&!confirmLeaveCharacterDraft())return;if(closing?.kind==="chat" && callId.value)void endContinuousCall("已离开聊天，通话结束。");openWindows.value = openWindows.value.filter(item => item.id !== id);if(!openWindows.value.length)desktopVisible.value=false;if(closing&&fullPageKind.value===closing.kind){fullPageKind.value=null;window.history.replaceState({},'','/hub');rememberRoute()}else if(closing&&queryWindowKind()===closing.kind){const next=[...openWindows.value].sort((a,b)=>b.zIndex-a.zIndex)[0];syncWindowUrl(next?.kind??null,true)}saveLayout() }
function closeAll() { openWindows.value = []; fullPageKind.value=null; if(window.location.pathname==='/hub'&&window.location.search)syncWindowUrl(null,true);saveLayout() }
function focusWindow(id: string, syncUrl = true) { const item = openWindows.value.find(window => window.id === id); if (item) { item.zIndex = ++zCounter.value; if(syncUrl&&!fullPageKind.value&&window.location.pathname==='/hub'&&queryWindowKind()!==item.kind)syncWindowUrl(item.kind,true) } }
function toggleMinimize(item: WindowInstance) { item.minimized = !item.minimized; focusWindow(item.id); saveLayout() }
function toggleMaximize(item: WindowInstance) { item.maximized = !item.maximized; item.minimized = false; focusWindow(item.id); saveLayout() }
function windowStyle(item: WindowInstance) { if (item.minimized) return { zIndex: item.zIndex, left: `${item.x}px`, top: `${item.y}px`, width: '340px', height: '56px' }; if (item.maximized) return { zIndex: item.zIndex }; return { zIndex: item.zIndex, left: `${item.x}px`, top: `${item.y}px`, width: `${item.width}px`, height: `${item.height}px` } }
function startDrag(event: PointerEvent, item: WindowInstance) { const target = event.target as HTMLElement; if (target.closest('button, input, textarea, select') || item.maximized || fullPageKind.value===item.kind) return; focusWindow(item.id); dragState.value = { id: item.id, dx: event.clientX - item.x, dy: event.clientY - item.y }; (event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId) }
function onPointerMove(event: PointerEvent) { const state = dragState.value; if (!state) return; const item = openWindows.value.find(window => window.id === state.id); if (item) { item.x = Math.max(windowLeftInset()+8, Math.min(window.innerWidth-item.width-8,event.clientX-state.dx)); item.y = Math.max(windowTopInset()+8, Math.min(window.innerHeight-64,event.clientY-state.dy)) } }
function stopDrag() { const wasDragging=!!dragState.value; dragState.value = null; const resized=syncWindowSizes(); if(wasDragging||resized){clampWindowsToViewport();saveLayout()} }
function clampWindowsToViewport() {
  const left = windowLeftInset(), top = windowTopInset()
  for (const item of openWindows.value) {
    item.width = Math.min(Math.max(320, item.width), window.innerWidth - left - 16)
    item.height = Math.min(Math.max(180, item.height), window.innerHeight - top - 16)
    item.x = Math.max(left + 8, Math.min(item.x, window.innerWidth - item.width - 8))
    item.y = Math.max(top + 8, Math.min(item.y, window.innerHeight - item.height - 8))
  }
}
function syncWindowSizes() { let changed=false; for(const item of openWindows.value){if(item.minimized||item.maximized||fullPageKind.value===item.kind)continue;const element=document.querySelector<HTMLElement>(`[data-window-id="${CSS.escape(item.id)}"]`);if(!element)continue;const width=Math.round(element.getBoundingClientRect().width),height=Math.round(element.getBoundingClientRect().height);if(width>0&&height>0&&(item.width!==width||item.height!==height)){item.width=width;item.height=height;changed=true}}return changed }
function saveLayout() { try { const serialized=JSON.stringify(openWindows.value); localStorage.setItem('YOSAKI.window-layout.v1', serialized); localStorage.setItem('YOSAKI.last-open-windows', serialized); rememberRoute() } catch { /* storage may be unavailable */ } }
function restoreLayout() { try { const saved = JSON.parse(localStorage.getItem('YOSAKI.window-layout.v1') || '[]'); if (!Array.isArray(saved)) return; const allowed = new Set(features.map(item => item.kind)); const valid = saved.filter((item: any): item is WindowInstance => item && typeof item === 'object' && allowed.has(item.kind) && typeof item.id === 'string').map((item: WindowInstance) => ({ ...item, x: Number.isFinite(item.x) ? Math.max(8,item.x) : 110, y: Number.isFinite(item.y) ? Math.max(58,item.y) : 72, width: Number.isFinite(item.width) ? Math.max(320,item.width) : 560, height: Number.isFinite(item.height) ? Math.max(240,item.height) : 520, zIndex: Number.isFinite(item.zIndex) ? item.zIndex : 10, minimized: item.minimized === true, maximized: item.maximized === true })); openWindows.value = valid.filter((item,index,array)=>array.findIndex(candidate=>candidate.kind===item.kind)===index); clampWindowsToViewport(); zCounter.value = Math.max(zCounter.value, ...openWindows.value.map(item => Number(item.zIndex) || 0)) } catch { openWindows.value = [] } }
function appendFallback(text: string) { messages.value.push({ role: 'assistant', content: `我听见了，${current.value.name}会把这句话放在心里。\n\n这件事可以从很小的地方开始：先保留一个能让你安心回来的角落，再让它一点一点长成你想要的样子。\n\n你刚才说的是：${text}`, time: timeOf(), model: 'YOSAKI · demo' }) }
async function consumeChatStream(response: Response, target?: Message,
                                 stillCurrent: () => boolean = () => true) {
  if (!response.ok || !response.body) throw new Error('聊天服务不可用')
  apiOnline.value = true
  const reader=response.body.getReader(), decoder=new TextDecoder(); let buffer=''; let assistantMessage=target
  let completedMessageId:number|null = null
  while(true){
    if (!stillCurrent()) { await reader.cancel(); return null }
    const {value,done}=await reader.read(); if(done) break
    if (!stillCurrent()) { await reader.cancel(); return null }
    buffer+=decoder.decode(value,{stream:true}); const events=buffer.split('\n\n'); buffer=events.pop()||''
    for(const event of events){
      if (!stillCurrent()) { await reader.cancel(); return null }
      const eventName=event.match(/^event: (.+)$/m)?.[1], dataText=event.match(/^data: (.+)$/m)?.[1]; if(!dataText) continue
      const data=JSON.parse(dataText)
      if(eventName==='message_start'){
        if(data.conversation_id) activeConversation.value=data.conversation_id
        if(callId.value && callPhase.value==='waiting')
          callStatus.value='模型正在生成；正文尚未通过最终展示核验，可点“打断”停止本轮。'
        if(!assistantMessage){assistantMessage={role:'assistant',content:'',time:timeOf(),model:data.model,toolRuns:[]};messages.value.push(assistantMessage)}
      } else if(eventName==='text_delta' && assistantMessage){assistantMessage.content+=data.text}
      else if(eventName==='reasoning_delta' && assistantMessage){assistantMessage.reasoningContent=(assistantMessage.reasoningContent||'')+data.text}
      else if(eventName==='monologue_delta' && assistantMessage){assistantMessage.monologueContent=(assistantMessage.monologueContent||'')+data.text}
      else if(eventName==='usage' && assistantMessage){assistantMessage.usage=data}
      else if(eventName==='tool_waiting_confirmation'){
        if(!assistantMessage){assistantMessage={role:'assistant',content:'',time:timeOf(),toolRuns:[]};messages.value.push(assistantMessage)}
        assistantMessage.toolRuns ||= []
        assistantMessage.toolRuns.push({...data,status:'pending_confirmation'})
      } else if(eventName==='tool_batch_waiting' && assistantMessage){assistantMessage.toolBatchRunIds=data.run_ids;assistantMessage.toolBatchStatus='awaiting_confirmation'}
      else if(eventName==='error'){if(assistantMessage)assistantMessage.toolBatchStatus='failed';errorMessage.value=data.message || '聊天请求失败'}
      else if(eventName==='message_complete' && assistantMessage){assistantMessage.toolBatchStatus='completed';assistantMessage.generationStatus=data.generation_status; if(callId.value && callPhase.value==='waiting')callStatus.value='模型已完成，正在核对保存与展示结果。'; if(Number.isInteger(data.message_id)){assistantMessage.id=data.message_id; if(data.generation_status==='completed' && data.voice_auto_read_eligible===true && !errorMessage.value)completedMessageId=data.message_id}}
    }
  }
  return completedMessageId
}
function persistStoppedAssistant(){const message=[...messages.value].reverse().find(item=>item.role==='assistant');if(message)message.toolBatchStatus='stopped'}
function stopGeneration(){if(callId.value){void interruptContinuousCall();return}stopAutoRead();if(!isStreaming.value||!abortController.value)return;streamStopped.value=true;abortController.value.abort()}
async function regenerateLast(targetId?:number){
  if(isStreaming.value||enhancementBusy.value||callId.value||activeConversation.value===null||!nextSettingsReady.value)return
  if(!closeChatPanel())return
  const sentOverrides={...nextOverrides.value};let settingsTicket:PendingSettings|null=null
  try{settingsTicket=reserveNextSettings()}catch(e){errorMessage.value=e instanceof Error?e.message:'单次设置暂存失败';return}
  stopAutoRead();stopSpeaking();isStreaming.value=true;errorMessage.value='';abortController.value=new AbortController()
  const epoch=chatUiEpoch, character=activeCharacter.value
  try{const response=await fetch('/api/chat/regenerate',{method:'POST',headers:{'Content-Type':'application/json'},signal:abortController.value.signal,body:JSON.stringify({character_id:character,conversation_id:activeConversation.value,message:'',model:model.value,model_profile_id:modelProfileId.value,target_message_id:targetId??null,expected_head:enhancementHead.value,overrides:sentOverrides})})
    if(!response.ok){rejectNextSettings(settingsTicket);throw new Error((await response.json().catch(()=>null))?.detail||'重新生成失败')}
    consumeNextSettings(settingsTicket);await consumeChatStream(response,undefined,()=>epoch===chatUiEpoch&&character===activeCharacter.value)
    if(epoch===chatUiEpoch&&activeConversation.value!==null){await loadMessages(activeConversation.value);await loadConversations()}
  }catch(e){if(epoch!==chatUiEpoch)return;if((e as Error).name!=='AbortError')errorMessage.value=e instanceof Error?e.message:'重新生成失败';if(activeConversation.value!==null)await loadMessages(activeConversation.value)}finally{if(epoch===chatUiEpoch){isStreaming.value=false;abortController.value=null}}
}

async function send() {
  if(pendingToolCount.value){errorMessage.value='请先处理正文中的工具确认或等待执行完成。';return}
  const cap = chronicleChatConsent.value && chronicleChatAvailable.value ? chronicleChatCap.value : 0
  const waitSeconds = chronicleChatWaitSeconds.value
  await sendText(draft.value.trim(), undefined, cap, waitSeconds)
}
async function sendText(text:string, callTurn?:string, chronicleCap=0, chronicleWait:12|30=12) {
  if((!text&&!pendingAttachments.value.length)||isStreaming.value||enhancementBusy.value)return
  if(!callTurn&&!nextSettingsReady.value){errorMessage.value='单次设置正在恢复核对，请稍候';return}
  if(callId.value && !callTurn){errorMessage.value='连续通话期间请先结束通话，再手动发送文字。';return}
  const sentEpoch=chatUiEpoch, sentCharacterId=activeCharacter.value
  const sentAttachments=callTurn?[]:[...pendingAttachments.value], sentQuote=callTurn?null:pendingQuote.value
  const sentOverrides=callTurn?{}:{...nextOverrides.value}
  const activeCall=callTurn ? callId.value : null
  const activeCallEpoch=callEpoch.value
  if(callTurn && (!activeCall || callTurnId.value!==callTurn)) return
  let settingsTicket:PendingSettings|null=null
  try{if(!activeCall)settingsTicket=reserveNextSettings()}catch(e){errorMessage.value=e instanceof Error?e.message:'单次设置暂存失败';return}
  if(!activeCall)clearChronicleChatConsent()
  scribeCancelEpoch.value++
  stopAutoRead()
  const optimistic:Message={role:'user',content:text||'[附件]',time:timeOf()}; messages.value.push(optimistic); if(!callTurn)draft.value=''; isStreaming.value=true; errorMessage.value=''
  abortController.value=new AbortController();streamStopped.value=false
  try { const response=await fetch('/api/chat/stream',{method:'POST',headers:{'Content-Type':'application/json'},signal:abortController.value.signal,body:JSON.stringify({character_id:activeCharacter.value,conversation_id:activeConversation.value,message:text,model:model.value,model_profile_id:modelProfileId.value,expected_head:activeCall?null:enhancementHead.value,attachment_ids:sentAttachments.map(x=>x.id),quote:sentQuote,overrides:sentOverrides,...(chronicleCap&&!activeCall?{chronicle_supplement_max_microusd:chronicleCap,chronicle_supplement_wait_seconds:chronicleWait}:{}),voice_auto_read_requested:isolatedAutoRead && (autoReadEnabled.value || Boolean(activeCall)),...(activeCall?{voice_call_id:activeCall,voice_call_turn_id:callTurn}:{})})}); if(!response.ok){rejectNextSettings(settingsTicket);if(sentEpoch!==chatUiEpoch)return;if(!activeCall){draft.value=text;messages.value=messages.value.filter(row=>row!==optimistic);if(response.status===409&&activeConversation.value!==null)await loadMessages(activeConversation.value)}apiOnline.value=true;const detail=(await response.json().catch(()=>null))?.detail;errorMessage.value=detail||`聊天请求失败（${response.status}）`;if(activeCall && callId.value===activeCall && callEpoch.value===activeCallEpoch){await endContinuousCall(`回复请求被拒绝（${response.status}）：${errorMessage.value.slice(0,120)}；通话已停止。`);if(activeConversation.value!==null)await loadMessages(activeConversation.value)}return}; if(!activeCall){pendingAttachments.value=[];pendingQuote.value=null;consumeNextSettings(settingsTicket)} const sentCharacter=activeCharacter.value; const completedId=await consumeChatStream(response,undefined,()=>sentEpoch===chatUiEpoch&&sentCharacterId===activeCharacter.value&&(!activeCall || (callId.value===activeCall && callEpoch.value===activeCallEpoch))); if(sentEpoch!==chatUiEpoch)return; if(activeCall && (callId.value!==activeCall || callEpoch.value!==activeCallEpoch))return; await loadConversations(); if(activeConversation.value!==null)await loadMessages(activeConversation.value); if(activeCall){if(!completedId || errorMessage.value || streamStopped.value){
    const lastSaved=messages.value[messages.value.length-1]
    const reason=errorMessage.value ? `回复处理未完成：${errorMessage.value.slice(0,160)}；通话已停止。`
      : streamStopped.value ? '本轮生成已停止；已保存的文字仍可查看，语音未派发。'
      : lastSaved?.role==='assistant' && lastSaved.generationStatus==='incomplete'
        ? '模型回复截断，已保存可见文字；语音未派发，通话已停止。'
        : lastSaved?.role==='assistant' && lastSaved.generationStatus==='completed'
          ? '回复已保存，但未收到本轮语音展示确认；语音未派发，通话已停止。'
          : '回复流结束但未取得完整保存凭证；语音未派发，通话已停止。'
    await endContinuousCall(reason);return}
    callPlaybackMessageId.value=completedId;callPlaybackStarted.value=false;callPhase.value='playing';callStatus.value='回复已完整保存并通过展示核验，正在合成音频；麦克风关闭。'}else if(isolatedAutoRead && autoReadEnabled.value && !streamStopped.value && !errorMessage.value && sentCharacter===activeCharacter.value && completedId && messages.value.some(item=>item.id===completedId && item.role==='assistant' && Boolean(item.content))) autoReadMessageId.value=completedId }
  catch(error){if(sentEpoch!==chatUiEpoch)return;if((error as Error)?.name==='AbortError'){errorMessage.value='已停止生成';persistStoppedAssistant();if(activeConversation.value!==null)await loadMessages(activeConversation.value)}else{apiOnline.value=false;errorMessage.value=error instanceof Error?error.message:'后端不可用';if(!activeCall)errorMessage.value+='；原请求状态请在详情查看，不会伪造备用回复'}if(activeCall && callId.value===activeCall && callEpoch.value===activeCallEpoch)await endContinuousCall('聊天未完成，通话已停止。')}
  finally{if(sentEpoch===chatUiEpoch){isStreaming.value=false;abortController.value=null}}
}
function canContinueMemory(message:Message,run:ToolRunUi){
  return run.server_name==='Chronicle' && ['yosaki_chronicle_search','yosaki_chronicle_expand'].includes(run.tool_name)
    && !!message.toolBatchRunIds?.includes(run.run_id) && message.toolRuns?.every(item=>item.run_id===run.run_id||item.status==='success')
}
async function decideTool(message:Message, run:ToolRunUi, approve:boolean, continueMemory=false){
  const epoch=chatUiEpoch, conversation=activeConversation.value
  if(toolDecisionBusy.value!==null)return
  toolDecisionBusy.value=run.run_id
  try{const result=await api<any>(`/api/chat/tool-runs/${run.run_id}/decision`,{method:'POST',body:JSON.stringify({approve})});run.status=result.status;run.result=result.result;run.error=result.error;message.toolBatchStatus='awaiting_confirmation'
    if(continueMemory && approve && result.status==='success' && epoch===chatUiEpoch && activeConversation.value===conversation && canContinueMemory(message,run)){
      await continueAfterTools(message)
    }
  }
  catch(error){errorMessage.value=error instanceof Error?error.message:'工具确认/执行失败'}
  finally{toolDecisionBusy.value=null}
}
async function continueAfterTools(message:Message){
  const runId=message.toolBatchRunIds?.[0]; if(!runId||isStreaming.value||enhancementBusy.value||message.toolRuns?.some(run=>['pending_confirmation','running'].includes(run.status)))return
  const epoch=chatUiEpoch;stopAutoRead();stopSpeaking();isStreaming.value=true;errorMessage.value='';abortController.value=new AbortController()
  try{const response=await fetch(`/api/chat/tool-runs/${runId}/continue`,{method:'POST',signal:abortController.value.signal});if(!response.ok)throw new Error((await response.json()).detail||'继续请求未获接受');await consumeChatStream(response,undefined,()=>epoch===chatUiEpoch);if(epoch===chatUiEpoch&&activeConversation.value!==null)await loadMessages(activeConversation.value)}
  catch(error){if(epoch===chatUiEpoch)errorMessage.value=(error as Error).name==='AbortError'?'已停止续答；用量仍保留':error instanceof Error?error.message:'继续生成失败'}
  finally{if(epoch===chatUiEpoch){isStreaming.value=false;abortController.value=null}}
}

async function setCharacterDefaultModel(){if(!current.value)return;modelProfileId.value=current.value.default_model_id;model.value=modelProfiles.value.find(profile=>profile.id===modelProfileId.value)?.name||'YOSAKI · demo';await saveCharacter()}
async function saveCharacter() { if (!current.value || !apiOnline.value) return; const character=current.value; const payload=characterDraft(character); saving.value = true; characterStatus.value=''; try { await api(`/api/characters/${character.id}`, { method: 'PATCH', body: JSON.stringify(payload) }); savedCharacterDrafts.value[character.id]=payload; character.last = character.greeting || character.last; characterStatus.value='角色资料已保存' } catch (error) { characterStatus.value = error instanceof Error ? error.message : '保存失败' } finally { saving.value = false } }
function handlePopState() { const pageKind=routeWindowKind(window.location.pathname);workspaceView.value = window.location.pathname === '/hub' || !!pageKind ? 'hub' : 'welcome';const linkedWindow=pageKind ?? (workspaceView.value==='hub'?queryWindowKind():null);if(callId.value && linkedWindow!=='chat')void endContinuousCall('已离开聊天，通话结束。');if(linkedWindow)openWindow(linkedWindow,false);fullPageKind.value=pageKind;rememberRoute();clampWindowsToViewport() }
onMounted(() => { try { scribeInputDeviceId.value=localStorage.getItem('YOSAKI.scribe.input-device.v1')||'' } catch {} void refreshScribeInputDevices(); navigator.mediaDevices?.addEventListener?.('devicechange',onScribeDeviceChange); if(window.innerWidth<=900)showNav.value=false; rememberRoute(); window.addEventListener('resize',clampWindowsToViewport); window.addEventListener('beforeunload',warnUnsavedCharacter); window.addEventListener('pagehide',onCallPageHide); refreshSpeechVoices(); if ('speechSynthesis' in window) window.speechSynthesis.addEventListener('voiceschanged',onSpeechVoicesChanged); void refreshAuth(); window.addEventListener('pointermove', onPointerMove); window.addEventListener('pointerup', stopDrag); window.addEventListener('popstate', handlePopState); window.addEventListener('yosaki-voice-auto-budget-changed', refreshAutoBudget) })
onBeforeUnmount(() => { navigator.mediaDevices?.removeEventListener?.('devicechange',onScribeDeviceChange); bargeMonitor.stop(); if(callId.value)void endContinuousCall("已离开页面，通话结束。"); if ('speechSynthesis' in window) window.speechSynthesis.removeEventListener('voiceschanged',onSpeechVoicesChanged); stopAutoRead(); stopSpeaking(); stopSpeechRecognition(); window.removeEventListener('pointermove', onPointerMove); window.removeEventListener('pointerup', stopDrag); window.removeEventListener('popstate', handlePopState); window.removeEventListener('resize',clampWindowsToViewport); window.removeEventListener('beforeunload',warnUnsavedCharacter); window.removeEventListener('pagehide',onCallPageHide); window.removeEventListener('yosaki-voice-auto-budget-changed', refreshAutoBudget) })
</script>
<template>
<div class="app-shell" :class="{ 'welcome-shell': workspaceView === 'welcome', 'nav-collapsed': !showNav }">
  <section v-if="!authReady" class="auth-page"><div class="auth-card"><div class="brand-mark">Y</div><h1>正在检查私人工作区…</h1><p>请稍候</p></div></section>
  <section v-else-if="!authenticated" class="auth-page"><ThemeToggle class="auth-theme"/><div class="auth-orbit"></div><div class="auth-card"><div class="brand-mark">Y</div><span class="auth-kicker">YOSAKI · PRIVATE WORKSPACE</span><h1>{{setupRequired?'设置你的私人账户':'欢迎回来'}}</h1><p>{{setupRequired?'创建唯一的管理员账户。密码仅在服务器保存为加盐哈希。':'登录后继续进入你的工作台。'}}</p><form @submit.prevent="submitAuth"><label>账户名称<input v-model="authDraft.username" required minlength="3" maxlength="40" autocomplete="username" placeholder="例如：owner"/></label><label>密码<input v-model="authDraft.password" required minlength="12" maxlength="256" type="password" :autocomplete="setupRequired?'new-password':'current-password'" placeholder="至少 12 个字符"/></label><label v-if="setupRequired">确认密码<input v-model="authDraft.confirmPassword" required minlength="12" maxlength="256" type="password" autocomplete="new-password" placeholder="再次输入密码"/></label><p v-if="authError" class="auth-error" role="alert">{{authError}}</p><button class="enter-btn auth-submit" :disabled="authBusy">{{authBusy?'请稍候…':setupRequired?'创建账户并进入':'登录并进入'}} <ArrowRight :size="17"/></button></form><div class="auth-privacy"><ShieldCheck :size="14"/> 单用户 · HttpOnly 会话 · 仅本地设置密码</div></div><footer class="auth-footer">尚未设置密码前，所有应用数据接口保持锁定。</footer></section>
  <section v-else-if="workspaceView === 'welcome'" class="welcome-page">
    <GlassSurface level="hero" class="welcome-panel">
      <header class="welcome-brand"><div class="brand-mark"><Sparkles :size="20"/></div><div><strong>YOSAKI</strong><span>私人 AI 工作台</span></div><div class="welcome-top-actions"><span>对话 · 记忆 · 探索</span><ThemeToggle/><button class="text-btn" @click="goHub">我的空间 <ArrowRight :size="16"/></button></div></header>
      <div class="welcome-content">
        <div class="welcome-copy"><span class="welcome-kicker">属于你，也属于阿夜</span><h1>欢迎回到<em>YOSAKI</em></h1><p>与阿夜对话，留住经历，<br/>慢慢探索。</p><button class="enter-btn" @click="goHub">进入工作台 <ArrowRight :size="18"/></button><div class="welcome-note"><ShieldCheck :size="15"/> 本地优先 · 私人空间</div></div>
        <div class="welcome-earth"><YosakiGlobe/>
          <GlassSurface class="orbital-card orbit-chat" refract><button @click="visitFeature('chat')"><MessageCircle :size="23"/><span><strong>对话</strong><small>从一句话开始</small></span></button></GlassSurface>
          <GlassSurface class="orbital-card orbit-memory" refract><button @click="chronicleAvailable ? visitChronicle() : visitFeature('memory')"><BrainCircuit :size="23"/><span><strong>记忆与经历</strong><small>让走过的日子有迹可循</small></span></button></GlassSurface>
          <GlassSurface class="orbital-card orbit-tools" refract><button @click="visitFeature('mcp')"><Wrench :size="22"/><span><strong>工具与连接</strong><small>探索更多可能</small></span></button></GlassSurface>
        </div>
      </div>
    </GlassSurface>
    <footer class="welcome-footer"><span>YOSAKI / 你的私人空间</span><span>把时间留给值得的对话。</span></footer>
  </section>
  <section v-else class="workspace-shell" :class="{'chat-reading-layout':fullPageKind==='chat'}">
    <button v-if="showNav" class="mobile-nav-scrim" aria-label="收起导航" @click="showNav=false"></button>
    <aside class="workspace-rail" :class="{collapsed:!showNav}">
      <div class="rail-brand"><div v-if="showNav" class="brand-mark"><Sparkles :size="18"/></div><div v-if="showNav"><strong>YOSAKI</strong><span>你的私人空间</span></div><button class="icon-btn quiet" :aria-label="showNav?'收起导航':'展开导航'" @click="showNav=!showNav"><PanelLeft :size="18"/></button></div>
      <nav v-if="showNav" class="rail-body" aria-label="主导航">
        <button class="home-link" :class="{active:!fullPageKind&&!desktopVisible}" @click="goHub"><Home :size="17"/>工作台</button>
        <div v-for="group in navGroups" :key="group.title" class="nav-group"><div class="rail-group-label">{{group.title}}</div><button v-for="kind in group.kinds" :key="kind" class="rail-link" :class="{active:fullPageKind===kind}" :aria-current="fullPageKind===kind?'page':undefined" @click="visitFeature(kind)"><component :is="feature(kind).icon" :size="17"/><span>{{navTitle(kind)}}</span></button><button v-if="group.title==='日常' && chronicleAvailable" class="rail-link" @click="visitChronicle"><BrainCircuit :size="17"/>记忆年历</button></div>
        <div class="rail-bottom"><div class="rail-status"><span :class="{online:apiOnline}"></span>{{apiOnline?'已连接工作区':'连接尚未就绪'}}</div><button class="home-link muted" @click="goWelcome"><Home :size="16"/>返回欢迎页</button><button class="home-link muted" :disabled="authBusy" @click="logout"><LogOut :size="16"/>退出登录</button></div>
      </nav>
      <nav v-else class="rail-mini" aria-label="快捷导航"><button class="icon-btn" aria-label="工作台" @click="goHub"><Home :size="19"/></button><button class="icon-btn" aria-label="对话" @click="visitFeature('chat')"><MessageCircle :size="19"/></button><button class="icon-btn" aria-label="记忆" @click="chronicleAvailable?visitChronicle():visitFeature('memory')"><BrainCircuit :size="19"/></button><button class="icon-btn" aria-label="系统设置" @click="visitFeature('settings')"><Settings2 :size="19"/></button></nav>
    </aside>
    <main class="workspace-main">
      <header class="workspace-topbar"><div><span class="top-kicker">YOSAKI / PERSONAL SPACE</span><h1>{{fullPageKind?navTitle(fullPageKind):'工作台'}}</h1></div><div class="workspace-actions"><span class="date-chip">{{dateLabel}}</span><ThemeToggle/><button class="icon-btn" aria-label="系统设置" @click="visitFeature('settings')"><Settings2 :size="18"/></button><button class="icon-btn mobile-only" aria-label="打开导航" @click="showNav=!showNav"><PanelLeft :size="18"/></button></div></header>
      <section v-if="!fullPageKind" class="hub-content">
        <div class="hub-layout"><div class="hub-explore"><div class="hub-intro"><span class="hub-greeting">欢迎回来，</span><h2>与{{current.name}}一起，<br/>探索日常的更多可能。</h2><p>对话、记忆，还有想做的事。<br/>从你喜欢的地方开始。</p></div>
          <div class="hub-earth"><YosakiGlobe :active="!desktopVisible"/>
            <GlassSurface class="orbital-card orbit-chat" refract><button @click="visitFeature('chat')"><MessageCircle :size="23"/><span><strong>与{{current.name}}对话</strong><small>继续你们的故事</small></span></button></GlassSurface>
            <GlassSurface class="orbital-card orbit-memory" refract><button @click="chronicleAvailable?visitChronicle():visitFeature('memory')"><BrainCircuit :size="23"/><span><strong>记忆与经历</strong><small>回到那些值得记得的时刻</small></span></button></GlassSurface>
            <GlassSurface class="orbital-card orbit-tools" refract><button @click="visitFeature('mcp')"><Wrench :size="22"/><span><strong>工具与连接</strong><small>把想法变成行动</small></span></button></GlassSurface>
          </div>
        </div><aside class="hub-status-stack" aria-label="当前工作区">
          <GlassSurface class="status-card"><h3>此刻的空间</h3><div class="status-person"><div class="avatar small">{{current.name.slice(0,1)}}</div><strong>{{current.name}}</strong></div><p>{{current.subtitle}}</p><dl><div><dt>工作区</dt><dd>{{apiOnline?'已连接':'连接尚未就绪'}}</dd></div><div><dt>当前模型</dt><dd>{{model}}</dd></div><div v-if="isStreaming"><dt>对话</dt><dd>正在生成</dd></div><div v-if="callId"><dt>语音</dt><dd>通话进行中</dd></div><div v-if="characterDirty"><dt>角色资料</dt><dd>有未保存修改</dd></div></dl></GlassSurface>
          <GlassSurface class="status-card"><h3>随手开始</h3><div class="status-actions"><button @click="newChat"><Plus :size="18"/>新对话</button><button @click="visitFeature('characters')"><UserRound :size="18"/>角色卡</button><button @click="visitFeature('voice')"><Volume2 :size="18"/>语音设置</button><button @click="visitFeature('models')"><Cpu :size="18"/>切换模型</button></div></GlassSurface>
          <GlassSurface v-if="errorMessage || callStatus && callId" class="status-card"><h3>需要留意</h3><p role="status">{{errorMessage || callStatus}}</p></GlassSurface>
        </aside></div>
        <div v-if="openWindows.length" class="window-dock"><details><summary>打开的窗口 · {{openWindows.length}}</summary><div class="window-dock-list"><button v-for="win in openWindows" :key="win.id" @click="openWindow(win.kind)">{{win.title}}</button></div></details><button v-if="desktopVisible" class="dock-hide" @click="desktopVisible=false">收起浮窗</button></div>
      </section>
    </main>
    <section v-if="fullPageKind || desktopVisible" class="window-layer">
      <article v-for="item in (fullPageKind ? openWindows.filter(window=>window.kind===fullPageKind) : activeWindows)" :key="item.id" class="floating-window" :data-window-id="item.id" :class="{ minimized:item.minimized, maximized:item.maximized && !item.minimized, 'full-page-feature':fullPageKind===item.kind }" :style="windowStyle(item)" @pointerdown="focusWindow(item.id)">
        <header v-if="item.kind!=='chat' || fullPageKind!=='chat'" class="window-header" @pointerdown="startDrag($event,item)"><div class="window-title"><component :is="feature(item.kind).icon" :size="15" :style="{color:feature(item.kind).accent}"/><strong>{{item.title}}</strong></div><div class="window-controls"><button v-if="fullPageKind===item.kind" title="在浮窗打开" aria-label="在浮窗打开" @click.stop="openWindow(item.kind)"><Minimize2 :size="15"/></button><button v-if="fullPageKind!==item.kind" title="作为独立页面打开" :aria-label="`将${item.title}作为独立页面打开`" @click.stop="openFeaturePage(item.kind)"><ArrowUpRight :size="14"/></button><button v-if="fullPageKind!==item.kind" :aria-label="item.minimized?'还原窗口':'最小化窗口'" @click.stop="toggleMinimize(item)"><Minus :size="14"/></button><button v-if="fullPageKind!==item.kind" :aria-label="item.maximized?'还原窗口大小':'最大化窗口'" @click.stop="toggleMaximize(item)"><Minimize2 v-if="item.maximized" :size="14"/><Maximize2 v-else :size="14"/></button><button :title="fullPageKind===item.kind?'返回工作台':'关闭窗口'" :aria-label="fullPageKind===item.kind?'返回工作台':`关闭${item.title}窗口`" @click.stop="closeWindow(item.id)"><X :size="14"/></button></div></header>
        <div v-if="!item.minimized" class="window-content">
            <div v-if="item.kind==='chat'" class="window-chat"><div class="chat-main" :inert="chatPanel!==null">
            <div class="chat-window-toolbar chat-compact-toolbar">
              <div class="avatar small" :style="{background:current.color}">{{current.name.slice(0,1)}}</div>
              <div class="chat-identity"><strong>{{current.name}}</strong><span>{{conversations.find(c=>c.id===activeConversation)?.title || '新的对话'}}</span></div>
              <button class="chat-model-entry" @click="showChatPanel('model')" aria-label="选择聊天模型">{{modelProfiles.find(p=>p.id===modelProfileId)?.name || 'YOSAKI · demo'}}<ChevronDown :size="14"/></button>
              <button class="chat-manage-entry" @click="showChatPanel('conversations')">对话管理</button>
              <button v-if="fullPageKind==='chat'" class="icon-btn" aria-label="在浮窗打开" @click="openWindow('chat')"><Minimize2 :size="16"/></button>
            </div>
<div class="chat-scroll"><div v-if="!messages.length" class="empty-state"><MessageCircle :size="25"/><strong>{{current.greeting || '从一句话开始吧'}}</strong><span>对话内容会保存到本地 YOSAKI 数据库。</span></div><div class="messages"><div v-for="(message,index) in messages" :key="message.id || index" class="message-row" :class="message.role"><div v-if="message.role==='assistant'" class="avatar tiny" :style="{background:current.color}">{{current.name.slice(0,1)}}</div><div class="message-body"><details v-if="message.reasoningContent" class="v2-reasoning"><summary>{{message.reasoningSource==='responses_summary'?'公开推理摘要':'服务商公开推理字段'}} · {{message.reasoningContent.length}} 字</summary><pre>{{message.reasoningContent}}</pre></details><details v-if="message.monologueContent" class="v2-reasoning"><summary>阿夜的内心独白</summary><pre>{{message.monologueContent}}</pre></details><div v-if="message.generationStatus==='incomplete'" class="v2-incomplete">回复未完整生成，已收到内容保留</div><div v-if="message.content" class="bubble" :class="message.role"><p v-for="(line,i) in message.content.split('\n')" :key="i" :class="{'blank-line':!line.trim()}">{{line||' '}}</p></div><div v-for="file in message.attachments||[]" :key="file.id" class="chat-attachment-card"><img v-if="file.mime.startsWith('image/')" :src="`/api/chat/attachments/${file.id}?character_id=${activeCharacter}`" :alt="file.name"/><a :href="`/api/chat/attachments/${file.id}?character_id=${activeCharacter}`" target="_blank" rel="noopener">{{file.name}}</a></div><blockquote v-for="quote in message.quotes||[]" :key="quote.source_message_id" class="chat-reading-text">{{quote.excerpt}}</blockquote><div v-if="message.id" class="chat-message-actions"><button :disabled="isStreaming||enhancementBusy||Boolean(callId)" @click="inspectMessage(message)">消息操作／详情</button><button @click="copyChatMessage(message)">复制</button><button :disabled="isStreaming||Boolean(callId)" @click="quoteChatMessage(message)">引用选中片段</button></div><div v-for="run in message.toolRuns||[]" :key="run.run_id" class="tool-confirm-card"><div class="tool-confirm-heading"><strong>{{run.plugin_name ? run.plugin_name + ' · ' : run.server_name + ' · '}}{{run.tool_name}}</strong><span :class="run.status">{{run.status==='pending_confirmation'?'等待你的确认':run.status==='success'?'已执行':run.status==='rejected'?'已拒绝':run.status==='running'?'执行中':run.status==='revoked'?'已撤销':run.status==='archived'?'历史记录':'执行失败'}}</span></div><p>{{run.description}}</p><pre>{{JSON.stringify(run.arguments,null,2)}}</pre><div v-if="run.status==='pending_confirmation'" class="tool-confirm-actions"><button v-if="canContinueMemory(message,run)" :disabled="toolDecisionBusy!==null||isStreaming||enhancementBusy" @click="decideTool(message,run,true,true)">确认并交给当前模型续答</button><button :disabled="toolDecisionBusy!==null" @click="decideTool(message,run,true)">确认执行</button><button class="reject" :disabled="toolDecisionBusy!==null" @click="decideTool(message,run,false)">拒绝</button></div><details v-if="run.result"><summary>查看工具结果</summary><pre>{{JSON.stringify(run.result,null,2)}}</pre></details><small v-if="run.error">{{run.error}}</small></div><div v-if="message.toolBatchRunIds?.length && !message.toolRuns?.some(run=>run.status==='pending_confirmation') && message.toolBatchStatus==='awaiting_confirmation'" class="continue-tools"><span>工具处理完毕，可以让阿夜继续回复</span><button  @click="continueAfterTools(message)">继续对话</button></div><div class="message-meta">{{message.time}} <details v-if="message.usage || message.model" class="message-model-details"><summary>模型详情</summary><span v-if="message.model">{{message.model}}</span><span v-if="message.usage">输入 {{message.usage.prompt_tokens??message.usage.input_tokens??'未提供'}} / 输出 {{message.usage.completion_tokens??message.usage.output_tokens??'未提供'}} / 缓存 {{message.usage.prompt_tokens_details?.cached_tokens??message.usage.input_tokens_details?.cached_tokens??'未提供'}}</span></details><button v-if="message.role==='assistant' && message.content" class="message-speak" title="朗读回复" @click="speakMessage(message,index)"><Volume2 :size="13"/></button><MessageAudioPlayer v-if="message.role==='assistant' && message.id && message.content" :auto-start="(isolatedAutoRead && autoReadEnabled && autoReadMessageId===message.id) || (!callPlaybackWs && Boolean(callId) && callPlaybackMessageId===message.id)" :voice-call-id="callPlaybackMessageId===message.id ? callId ?? undefined : undefined" :voice-call-turn-id="callPlaybackMessageId===message.id ? callTurnId ?? undefined : undefined" :message-id="message.id" :following-message-ids="messages.slice(index+1).filter(item=>item.role==='assistant' && item.id).map(item=>item.id as number)" @play="stopSpeaking" @budget-changed="refreshAutoBudget" @playback-state="message.id && onCallPlaybackState(message.id,$event)"/><MessageRealtimeOutput v-if="(v4ProductOutputIsolated || (callPlaybackWs && callPlaybackMessageId===message.id)) && message.role==='assistant' && message.id && activeConversation && message.generationStatus==='completed'" :message-id="message.id" :conversation-id="activeConversation" :character-id="activeCharacter" :daily-call-id="callPlaybackWs && callPlaybackMessageId===message.id ? callId ?? undefined : undefined" :daily-turn-id="callPlaybackWs && callPlaybackMessageId===message.id ? callTurnId ?? undefined : undefined" :auto-start="callPlaybackWs && callPlaybackMessageId===message.id" @play="stopSpeaking" @playback-state="(state,reason) => message.id && onCallPlaybackState(message.id,state,reason)"/><button v-if="message.role==='assistant' && index===messages.length-1" class="message-speak" title="重新生成" :disabled="isStreaming||enhancementBusy||Boolean(callId)" @click="regenerateLast()"><RefreshCw :size="13"/></button></div></div></div><div v-if="isStreaming" class="message-row assistant"><div class="avatar tiny" :style="{background:current.color}">{{current.name.slice(0,1)}}</div><div class="bubble assistant typing"><span></span><span></span><span></span></div></div></div></div>
            <div class="composer-wrap chat-compact-composer" @paste="attachmentRef?.paste($event)">
              <div class="chat-message-actions"><button :disabled="isStreaming||Boolean(callId)" @click="enhancementMessage=null;showChatPanel('enhance')">请求／分支／上下文</button></div>
              <AttachmentInput :ref="setAttachmentRef" v-model="pendingAttachments" :character-id="activeCharacter" :disabled="isStreaming||enhancementBusy||Boolean(callId)" @error="errorMessage=$event"/>
              <div v-if="pendingQuote" class="chat-quote-draft"><button @click="pendingQuote=null">取消引用</button>{{pendingQuote.excerpt}}</div>
              <div v-if="callId || scribeRunning || speechRecognitionRunning || autoReadMessageId" class="chat-live-status" role="status">
                <span>{{callId ? callPhaseLabel : scribeRunning ? '正在录音' : speechRecognitionRunning ? '正在识别' : '正在朗读'}}</span>
                <template v-if="callId"><button @click="interruptContinuousCall()">打断</button><button @click="endContinuousCall()">结束通话</button></template>
                <template v-else-if="scribeRunning"><button @click="scribeControl?.finish()">结束并转写</button><button @click="scribeControl?.stop()">取消识别</button></template>
                <button v-else-if="speechRecognitionRunning" @click="stopSpeechRecognition">停止识别</button>
                <button v-else @click="stopAutoRead">停止朗读</button>
              </div>
              <div v-if="chatNotice || pendingToolCount" class="chat-notice" role="status"><span>{{pendingToolCount ? '工具等待确认或执行中，请在正文处理后继续。' : chatNotice}}</span><button @click="showChatPanel('status')">详情</button></div>
              <div class="composer">
                <textarea v-model="draft" :disabled="Boolean(callId)" @keydown="onComposerKeydown" placeholder="写下你想说的话…" aria-label="消息" rows="1"></textarea>
                <div class="composer-tools">
                  <div class="chat-tool-entries">
                    <button @click="showChatPanel('voice')"><Mic :size="16"/>语音</button>
                    <button v-if="isolatedAutoRead" :aria-pressed="autoReadEnabled" @click="showChatPanel('read')">朗读：{{autoReadEnabled?'开':'关'}}</button>
                    <button v-if="chronicleChatInstalled && !callId" :aria-pressed="chronicleChatConsent" @click="showChatPanel('memory')">{{chronicleChatConsent ? '本条补查：允许' : '本条补查'}}</button>
                  </div>
                  <button v-if="isStreaming" class="send-btn ready" aria-label="停止生成" @click="stopGeneration"><X :size="16"/></button>
                  <button v-else class="send-btn" :disabled="Boolean(callId) || !!pendingToolCount || enhancementBusy || !nextSettingsReady || (!draft.trim() && !pendingAttachments.length)" :class="{ready:draft.trim()}" aria-label="发送消息" @click="send"><Send :size="16"/></button>
                </div>
              </div>
              <div v-if="chronicleChatConsent" class="chat-paid-selection">仅本条允许补查 · 最多等待 {{chronicleChatWaitSeconds}} 秒 · 上限 USD {{(chronicleChatCap/1000000).toFixed(3)}}<button @click="clearChronicleChatConsent">撤销</button></div>
            </div>
            </div><ChatDrawer :open="chatPanel!==null" :title="chatPanelTitle" @close="closeChatPanel"><ChatEnhancementPanel v-if="chatPanel==='enhance'" :ref="setEnhancementRef" :conversation-id="activeConversation" :character-id="activeCharacter" :message="enhancementMessage" :head="enhancementHead" :draft="draft" :attachment-ids="pendingAttachments.map((a:any)=>a.id)" :quote-draft="pendingQuote" :model-id="modelProfileId" :models="modelProfiles" :disabled="isStreaming||Boolean(callId)" :overrides="nextOverrides" :apply-overrides="applyNextOverrides" :settings-notice="nextSettingsNotice" :settings-ready="nextSettingsReady" @reload="reloadEnhanced" @regenerate="regenerateLast" @quote="acceptQuote" @busy="enhancementBusy=$event"/>
<template #activity>              <div v-if="callId || scribeRunning || speechRecognitionRunning || autoReadMessageId" class="chat-live-status" role="status">
                <span>{{callId ? callPhaseLabel : scribeRunning ? '正在录音' : speechRecognitionRunning ? '正在识别' : '正在朗读'}}</span>
                <template v-if="callId"><button @click="interruptContinuousCall()">打断</button><button @click="endContinuousCall()">结束通话</button></template>
                <template v-else-if="scribeRunning"><button @click="scribeControl?.finish()">结束并转写</button><button @click="scribeControl?.stop()">取消识别</button></template>
                <button v-else-if="speechRecognitionRunning" @click="stopSpeechRecognition">停止识别</button>
                <button v-else @click="stopAutoRead">停止朗读</button>
              </div>
</template>
<section v-show="chatPanel==='conversations'" class="chat-conversation-manager">
<button class="primary-btn" @click="newChat();closeChatPanel()"><Plus :size="16"/>新对话</button>
<label>搜索对话<input v-model="conversationSearch" placeholder="按对话名称搜索"/></label>
<div v-for="conversation in filteredConversations" :key="conversation.id" class="chat-history-row" :class="{active:activeConversation===conversation.id}">
<button @click="selectConversation(conversation);closeChatPanel()">{{conversation.title || '新的对话'}}</button>
<div><button @click="renameConversation(conversation)">重命名</button><button @click="exportConversation(conversation,'json')">JSON</button><button @click="exportConversation(conversation,'markdown')">Markdown</button><button @click="deleteConversation(conversation)">删除</button></div></div>
<p v-if="!filteredConversations.length">没有匹配的对话。</p><p role="status">{{conversationStatus}}</p><ArchivePanel :character-id="activeCharacter" :conversations="conversations" @imported="loadConversations"/></section>
              <section v-show="chatPanel==='voice'" class="chat-voice-panel"><label v-if="scribeDraftVisible || continuousAvailable" class="voice-call-device">麦克风输入设备 <select v-model="scribeInputDeviceId" :disabled="Boolean(callId) || scribeRunning" @change="saveScribeInputDevice"><option value="">系统默认</option><option v-for="device in scribeInputDevices" :key="device.id" :value="device.id">{{device.label}}</option></select></label><button type="button" class="text-btn" :disabled="Boolean(callId) || scribeRunning || scribeDevicePermissionBusy" @click="showScribeInputDevices">选择麦克风</button><span v-if="scribeInputDeviceNotice" class="voice-status" role="status">{{scribeInputDeviceNotice}}</span><ScribeDraftInput :ref="setScribeControl" @status-change="scribeStatus=$event" :input-device-id="scribeInputDeviceId" v-if="scribeDraftVisible && !callId" :cancel-epoch="scribeCancelEpoch" :character-id="activeCharacter" :conversation-id="activeConversation" :disabled="speechRecognitionRunning" @running-change="scribeRunning=$event" @committed="acceptScribeDraft"/><div v-if="continuousAvailable" class="voice-call-controls"><p v-if="headsetBargeAcceptance && !callId">耳机插话验收。戴耳机并选择麦克风，开始后读第一句：「夜の散歩について、日本語で百字ほど話してください。」听到声音并看到「现在可以插话」后，说一次「少しゆっくり話してください。」</p><p v-if="continuousDaily && !callId">目标对话：{{conversations.find(item=>item.id===activeConversation)?.title || "未选择"}}（ID {{activeConversation ?? "—"}}）；当前最多可开始 {{callAvailability?.available_rounds ?? 0}} 轮；今日剩余录音 {{callAvailability?.scribe_remaining?.attempts ?? 0}} 次、连接 {{callAvailability?.scribe_remaining?.connections ?? 0}} 条、朗读 {{callAvailability?.tts_remaining?.requests ?? 0}} 个请求／{{callAvailability?.tts_remaining?.characters ?? 0}} 字。{{callAvailability?.conversation_incomplete ? "上一条消息已保存，但回复未完成；可手动继续文字聊天，或新建对话开始通话。系统不会自动补发。" : callAvailability?.model_path_ready===false ? callModelPathNotice(callAvailability.model_path_reason) : callAvailability?.active_call ? "已有通话进行中；刷新不会重置额度。" : "回复过长或每日额度不足时保留完整文字并停止语音。" }}</p><button v-if="continuousDaily && !callId && callAvailability?.conversation_incomplete" type="button" :disabled="isStreaming" @click="regenerateLast()">手动继续上一条回复（会调用模型）</button><ContinuousCallSettings v-if="continuousDaily && callSettingsEnabled && !callId" @saved="refreshCallAvailability"/><label v-if="!callId"><input v-model="callAcknowledged" type="checkbox"/>开始通话后，最终转写会自动发送；临时转写不会发送。{{headsetBargeAcceptance ? "本次播放首轮回复时，会在本机监听插话。只把检测到的这一句交给语音识别。" : "等待回复时关闭麦克风。"}} </label><label v-if="!callId && bargeInAvailable && !headsetBargeAcceptance"><input v-model="bargeInRequested" type="checkbox" :disabled="callStartBusy"/>耳机插话（仅本次通话）</label><p v-if="!callId && bargeInAvailable && !headsetBargeAcceptance">勾选后，播放期间会开启麦克风在本机监听。检测到插话会停止旧声音，并把这次插话发送给语音识别。请戴耳机，避免扬声器回声误触发。</p><button v-if="!callId" type="button" :disabled="!callAcknowledged || !modelProfileId || callStartBusy || (continuousDaily && (!activeConversation || (callAvailability?.available_rounds ?? 0)<(headsetBargeAcceptance ? 2 : 1))) || (headsetBargeAcceptance && callPhase==='ended')" @click="startContinuousCall">开始通话</button><button v-if="callId" type="button" @click="interruptContinuousCall()">打断</button><button v-if="callId" type="button" @click="endContinuousCall()">结束通话</button><span role="status">{{callStatus}}</span><details v-if="callId && callSoftwareLatency!==null"><summary>开发详情：播放计时</summary><small data-call-software-latency>{{callSpeechEndMs===null ? "提交录音" : "本地判定说完"}}到开始播放（软件计时）：{{callSoftwareLatency}} 毫秒</small></details><small data-voice-call-phase>状态：{{callPhaseLabel}}。首轮建议戴耳机；扬声器回声仍待真实验收。最长 {{callLimits?.seconds ?? callAvailability?.limits?.limits?.seconds ?? 180}} 秒、{{callLimits?.model_turns ?? callAvailability?.limits?.limits?.rounds ?? 2}} 轮；同时受每日额度限制。</small><ScribeDraftInput :ref="setScribeControl" @status-change="scribeStatus=$event" :input-device-id="scribeInputDeviceId" v-if="callId && callPhase==='listening'" continuous-mode :call-id="callId" :start-signal="callEpoch" :cancel-epoch="scribeCancelEpoch" :barge-capture="bargeCapture" :character-id="activeCharacter" :conversation-id="activeConversation" @committed="onContinuousTranscript" @capture-ready="onContinuousCaptureReady" @audio-commit="onCallAudioCommit" @failed="onContinuousFailure"/></div><section class="chat-device-speech"><h3>设备语音输入</h3><p>识别结果先填入草稿，由你检查后发送。本机识别不可用时，会先询问是否允许浏览器供应商处理音频。</p><button :disabled="scribeRunning || Boolean(callId)" @click="requestSpeechInput">{{speechRecognitionRunning?'停止语音识别':'开始设备识别'}}</button></section></section>
              <section v-show="chatPanel==='read'"><div v-if="isolatedAutoRead" class="voice-auto-controls"><label><input v-model="autoReadEnabled" type="checkbox" @change="!autoReadEnabled && stopAutoRead()"/>回复完成后自动朗读（默认关闭）</label><button v-if="autoReadMessageId" type="button" @click="stopAutoRead">停止语音</button><span>{{autoBudgetDescription}} 设备识别只填写输入框，请核对后自行发送。自动朗读仅用于演示回复或已完成展示核验的 ChatGPT Plan 回复。</span></div></section>
              <section v-show="chatPanel==='memory'"><p v-if="!chronicleChatAvailable" role="status">{{chronicleChatUnavailableText}}</p><div v-if="chronicleChatAvailable && !callId" class="chronicle-chat-consent"><label><input v-model="chronicleChatConsent" :disabled="isStreaming" type="checkbox"/> 允许本条消息在普通召回为空时补搜记忆（最多 USD {{ (chronicleChatCap/1000000).toFixed(3) }}）</label><label>补查等待上限<select v-model="chronicleChatWaitSeconds" :disabled="!chronicleChatConsent || isStreaming" aria-label="补查等待上限"><option :value="12">12 秒（默认）</option><option :value="30">30 秒（仅本条）</option></select></label><small>这是补查等待上限，后续聊天生成另需时间。选择30秒本身不授予费用或材料权限。</small><small>OpenAI 只接收本条消息和最多三条已确认的标题与梗概。此同意限本条消息。</small></div><p>发送一次后同意自动清除；修改草稿、切换角色、对话或模型也会清除。关闭面板后，输入区仍会显示本条选择及费用上限，可随时撤销。</p></section>
              <section v-show="chatPanel==='model'"><label>聊天模型<select class="model-select model-switch" v-model="modelProfileId" @change="stopAutoRead(); model = modelProfiles.find(profile => profile.id === modelProfileId)?.name || 'YOSAKI · demo'"><option :value="null">YOSAKI · demo</option><option v-for="profile in modelProfiles" :key="profile.id" :value="profile.id">{{profile.name}} · {{profile.model_name}}</option></select></label><button @click="visitFeature('models')">管理模型配置</button></section>
              <section v-show="chatPanel==='status'"><p v-if="errorMessage">{{errorMessage}}</p><p v-if="scribeStatus">{{scribeStatus}}</p><p v-if="speechStatus">{{speechStatus}}</p><p v-if="callStatus">{{callStatus}}</p><p v-if="pendingToolCount">工具正在等待你的确认或执行结果；关闭面板后在正文查看工具及参数。</p><button @click="showChatPanel('voice')">查看语音额度与设置</button></section>
            </ChatDrawer>
          </div>
                    <div v-else-if="item.kind==='characters'" class="window-form"><div class="window-section-heading"><div><span class="section-eyebrow">CHARACTER CARD</span><h2>管理角色</h2><p>这些内容会进入角色的稳定提示词。</p></div><button class="primary-btn" :disabled="saving" @click="saveCharacter"><Save :size="15"/> {{saving?'保存中':'保存'}}</button></div><div class="character-management"><button class="secondary-btn" :disabled="characterBusy" @click="createCharacter">新建角色</button><button class="secondary-btn" :disabled="characterBusy" @click="duplicateCurrentCharacter">复制当前角色</button><button class="secondary-btn" :disabled="characterCardBusy" @click="exportCharacterCard">导出角色卡 JSON</button><button class="secondary-btn" :disabled="characterCardBusy" @click="characterCardFileInput?.click()">导入角色卡 JSON</button><input ref="characterCardFileInput" class="plugin-file-input" type="file" accept=".json,application/json" @change="importCharacterCard"/><button class="secondary-btn danger" :disabled="characterBusy" @click="confirmDeleteCharacter">{{characterDeletePending?'确认删除':'删除当前角色'}}</button><button v-if="characterDeletePending" class="text-btn" @click="characterDeletePending=false">取消删除</button></div><p class="character-status" role="status">{{characterDirty ? '角色资料有未保存修改' : '角色资料已保存'}}<span v-if="characterStatus && characterStatus!=='角色资料已保存'"> · {{characterStatus}}</span></p><label>当前角色<select :value="activeCharacter" @change="onCharacterSelection"><option v-for="character in characters" :key="character.id" :value="character.id">{{character.name}}</option></select></label><label>默认模型<select v-model="current.default_model_id" @change="setCharacterDefaultModel"><option :value="null">跟随当前聊天选择</option><option v-for="profile in modelProfiles" :key="profile.id" :value="profile.id">{{profile.name}} · {{profile.model_name}}</option></select></label><label>角色名称<input v-model="current.name" @input="characterStatus=''"/></label><label>角色简介<textarea v-model="current.subtitle" rows="2" @input="characterStatus=''"></textarea></label><label>核心人设<textarea v-model="current.system_prompt" rows="6" @input="characterStatus=''"></textarea></label><label>开场白<textarea v-model="current.greeting" rows="2" @input="characterStatus=''"></textarea></label><p role="status">{{promptNotice}}</p><button class="text-btn" @click="openWindow('memory'); previewContext()">查看已保存上下文</button><div class="prompt-block-editor"><div class="prompt-block-heading"><div><strong>提示词注入块</strong><span>按位置组合到模型系统提示中</span></div><button class="text-btn" @click="addPromptBlock"><Plus :size="14"/> 添加</button></div><div v-for="(block, blockIndex) in promptBlocks" :key="block.id" class="prompt-block-edit" @dragover.prevent @drop.prevent="draggedPrompt !== null && movePrompt(draggedPrompt, blockIndex)"><div class="prompt-actions"><button draggable="true" @dragstart="draggedPrompt=block.id" @dragend="draggedPrompt=null" title="拖动排序">⠿ 拖动</button><button :disabled="blockIndex===0" @click="movePrompt(block.id,blockIndex-1)">上移</button><button :disabled="blockIndex===promptBlocks.length-1" @click="movePrompt(block.id,blockIndex+1)">下移</button><label><input type="checkbox" v-model="block.enabled" @change="savePromptBlock(block)"/>启用</label><button @click="deletingPrompt=block.id">删除</button><template v-if="deletingPrompt===block.id"><button @click="removePrompt(block.id)">确认删除</button><button @click="deletingPrompt=null">取消</button></template></div><div class="prompt-block-row"><input v-model="block.name" @change="savePromptBlock(block)"/><select v-model="block.injection_position" @change="savePromptBlock(block)"><option value="system_start">system_start</option><option value="character_profile">character_profile</option><option value="scenario">scenario</option><option value="memory">memory</option><option value="examples">examples</option><option value="before_history">before_history</option><option value="after_history">after_history</option><option value="system_end">system_end</option></select></div><textarea v-model="block.content" rows="2" @change="savePromptBlock(block)"></textarea></div></div></div>          <div v-else-if="item.kind==='models'" class="window-form model-window"><div class="window-section-heading"><div><span class="section-eyebrow">MODEL PROFILES</span><h2>模型配置</h2><p>密钥只发送给后端保存，不会写入浏览器存储。</p></div><button class="primary-btn" :disabled="modelSaving" @click="startNewModel"><Plus :size="15"/> 新建</button></div><ChatGPTPlanPanel @model-saved="loadModels"/><div v-if="modelProfiles.length" class="model-list"><button v-for="profile in modelProfiles" :key="profile.id" class="model-list-item" :disabled="modelSaving" :class="{selected:editingModelId===profile.id}" @click="editModel(profile)"><span class="model-state" :class="{configured:profile.api_key_configured||profile.protocol==='chatgpt_plan'}"></span><span><strong>{{profile.name}}</strong><small>{{profile.provider_name}} · {{profile.model_name}}</small></span><ChevronDown :size="14"/></button></div><p role="status">{{modelEditorReady ? (savedModelProfile ? `当前编辑：${savedModelProfile.name} · 档案已保存推理强度：${effortName(savedModelProfile.reasoning_effort)}` : '新建档案，尚未保存') : '正在读取模型档案…'}}</p><fieldset class="model-form-grid" :disabled="modelSaving||!modelEditorReady" style="border:0;padding:0;margin:0;min-width:0" @input="modelTestStatus='';modelFeedbackError=false" @change="modelTestStatus='';modelFeedbackError=false"><label>配置名称<input v-model="modelDraft.name" placeholder="例如：DeepSeek 日常"/></label><label>服务商<input v-model="modelDraft.provider_name" placeholder="OpenAI Compatible"/></label><label v-if="modelDraft.protocol!=='chatgpt_plan'" class="full-span">Base URL<input v-model="modelDraft.base_url" placeholder="https://api.example.com/v1"/></label><label>接口协议<select v-model="modelDraft.protocol"><option value="openai_compatible">OpenAI-compatible</option><option value="chatgpt_plan">ChatGPT Plan (OAuth)</option><option value="openai_responses">OpenAI Responses（API Key）</option></select></label><label>模型名称<input v-model="modelDraft.model_name" placeholder="例如：gpt-4o-mini"/></label><label v-if="modelDraft.protocol!=='chatgpt_plan'">API Key<input v-model="modelKeyInput" type="password" autocomplete="new-password" :placeholder="editingModelId ? '留空则保留已有密钥' : '只在这里输入，不会回显'"/></label><label v-if="modelDraft.protocol!=='chatgpt_plan'">Temperature<input v-model.number="modelDraft.temperature" type="number" min="0" max="2" step="0.1"/></label><label v-if="modelDraft.protocol!=='chatgpt_plan'">Max Tokens<input v-model.number="modelDraft.max_tokens" type="number" min="1" step="1"/></label><label>推理强度<select v-model="modelDraft.reasoning_effort"><option :value="null">未指定参数（服务商默认）</option><option value="low">低</option><option value="medium">中</option><option value="high">高</option></select></label></fieldset><div class="model-form-actions"><button class="secondary-btn" :disabled="modelSaving||!modelEditorReady" @click="saveModel"><Save :size="15"/> {{modelSaving?'保存中…':'保存配置'}}</button><button class="text-btn" :disabled="!editingModelId" @click="testModel"><Zap :size="14"/> 测试真实连接</button></div><p v-if="modelTestStatus" class="model-feedback" :role="modelFeedbackError?'alert':'status'">{{modelTestStatus}}</p></div>          <div v-else-if="item.kind==='memory'" class="window-form memory-window"><div class="window-section-heading"><div><span class="section-eyebrow">MEMORY & CONTEXT</span><h2>把重要的事留下</h2><p>原始聊天记录不会被覆盖；记忆只是发送给模型时的参考材料。</p></div><button class="primary-btn" :disabled="previewBusy" @click="previewContext">{{previewBusy?'读取中…':'预览上下文'}}</button></div><div class="memory-controls"><input v-model="memorySearch" @change="loadMemories" placeholder="搜索已保存记忆"/><button class="secondary-btn" @click="loadMemories">搜索</button></div><div class="memory-settings"><label>旧版最近消息条数<input v-model.number="contextSettings.history_limit" type="number" min="0" max="1000" @change="saveContextSettings"/></label><label>窗口模式<select v-model="contextSettings.window_mode" @change="saveContextSettings"><option value="legacy">沿用旧设置</option><option value="strict">严格最近 N 轮</option><option value="chunked">分段窗口</option></select></label><label v-if="contextSettings.window_mode!=='legacy'">窗口上限<input v-model.number="contextSettings.window_size" type="number" min="1" max="1000" @change="saveContextSettings"/></label><label v-if="contextSettings.window_mode==='chunked'">裁剪后保留<input v-model.number="contextSettings.window_keep" type="number" min="1" :max="contextSettings.window_size-1" @change="saveContextSettings"/></label><label class="check-label"><input v-model="contextSettings.memory_enabled" type="checkbox" @change="saveContextSettings"/>发送记忆</label><span>当前对话：{{activeConversation?'已选择':'尚未选择'}}</span></div><div class="summary-editor"><strong>回答指导提示词</strong><textarea v-model="contextSettings.reasoning_prompt" rows="2" @change="saveContextSettings"/><label><input v-model="contextSettings.monologue_enabled" type="checkbox" @change="saveContextSettings"/>生成角色内心独白</label><textarea v-if="contextSettings.monologue_enabled" v-model="contextSettings.monologue_prompt" rows="2" @change="saveContextSettings"/></div><a v-if="chronicleAvailable" href="/chronicle" class="text-button">打开 Chronicle · 梗概与完整精修记忆</a><div class="memory-create"><select v-model="memoryDraft.scope"><option value="long">长期记忆（角色）</option><option value="temporary">临时记忆（当前对话）</option></select><textarea v-model="memoryDraft.content" rows="2" placeholder="例如：用户喜欢在夜里讨论创作，不喜欢被催促。"></textarea><button class="primary-btn" :disabled="memoryBusy" @click="createMemory">保存记忆</button></div><div class="memory-list"><article v-for="memory in memories" :key="memory.id" class="memory-item"><div><span class="memory-scope">{{memory.scope==='long'?'长期':'本对话'}}</span><p>{{memory.content}}</p><small>{{memory.managed_by_learning?'由长期学习管理 · 请在那里审阅或归档':(memory.enabled?'已启用':'已停用')}}</small></div><div class="memory-item-actions"><button :disabled="memory.managed_by_learning" @click="toggleMemory(memory)">{{memory.enabled?'停用':'启用'}}</button><button :disabled="memory.managed_by_learning" @click="deleteMemory(memory)">删除</button></div></article><p v-if="!memories.length" class="memory-empty">还没有匹配的记忆。</p></div><div class="summary-editor"><div><strong>当前对话摘要</strong><span>手动摘要会作为参考资料进入上下文，不会删除原始消息。</span></div><textarea v-model="summaryDraft" rows="4" placeholder="可选：记录这段对话的关键进展、约定和未完成事项。"></textarea><label class="check-label"><input v-model="summaryEnabled" type="checkbox"/>启用摘要</label><button class="text-btn" @click="saveSummary">保存摘要</button></div><p role="status">{{promptNotice}}</p><ContextMemoryPreview v-if="previewMessages.length" :report="previewChronicle"/><details v-if="previewWire"><summary>当前模型的最终序列化预览</summary><p>这是当前配置的预览；实际派发后请查看对应请求快照。</p><pre class="chat-reading-text">{{JSON.stringify(previewWire,null,2)}}</pre></details><article v-for="(part,index) in previewMessages" :key="index" class="context-part"><strong>{{index+1}} · {{part.role}}</strong><pre>{{part.content || '（当前草稿为空）'}}</pre></article></div><DesirePanel v-else-if="item.kind==='desires'" :character-id="activeCharacter" :models="modelProfiles"/><LearningPanel v-else-if="item.kind==='learning'" :character-id="activeCharacter" :models="modelProfiles"/><PsychologyPanel v-else-if="item.kind==='psychology'" :character-id="activeCharacter"/><div v-else-if="item.kind==='mcp'" class="window-form mcp-window"><div class="window-section-heading"><div><span class="section-eyebrow">MCP CONNECTORS</span><h2>MCP 与工具</h2><p>首版执行链默认要求用户确认；服务密钥只由后端保存。</p></div><button class="primary-btn" :disabled="mcpBusy" @click="saveMcp">{{mcpBusy?'处理中…':'保存服务'}}</button></div><div class="mcp-server-list"><button v-for="server in mcpServers" :key="server.id" class="model-list-item" :class="{selected:mcpSelectedId===server.id}" @click="editMcp(server)"><span class="model-state" :class="{configured:server.status==='connected'}"></span><span><strong>{{server.name}}</strong><small>{{server.transport}} · {{server.status}}</small></span><ChevronDown :size="14"/></button></div><div class="model-form-grid"><label>服务名称<input v-model="mcpDraft.name" placeholder="我的 MCP 服务"/></label><label>传输<select v-model="mcpDraft.transport"><option value="streamable_http">Streamable HTTP</option><option value="sse">Legacy SSE（仅保存配置）</option></select></label><label class="full-span">Endpoint<input v-model="mcpDraft.endpoint" placeholder="https://example.com/mcp"/></label><label>鉴权<select v-model="mcpDraft.auth_type"><option value="none">无</option><option value="bearer">Bearer</option><option value="api_key">X-API-Key</option></select></label><label>密钥<input v-model="mcpDraft.auth_secret" type="password" placeholder="留空则保留已有密钥"/></label></div><div class="mcp-actions"><button class="secondary-btn" :disabled="!mcpSelectedId||mcpBusy" @click="connectMcp">连接测试</button><button class="text-btn" :disabled="!mcpSelectedId||mcpBusy" @click="refreshMcpTools">刷新工具</button></div><p role="status">{{mcpStatus}}</p><div class="tool-list"><article v-for="tool in mcpTools" :key="tool.id" class="tool-item"><strong>{{tool.name}}</strong><span>{{tool.description || '未提供描述'}}</span><small>{{tool.enabled?'已允许提出调用 · 执行前仍需确认':'默认停用'}}</small><button class="text-btn" @click="setMcpToolEnabled(tool)">{{tool.enabled?'停用':'启用工具'}}</button></article><p v-if="!mcpTools.length" class="memory-empty">连接并刷新后，这里会显示 MCP 工具。</p></div></div>          <div v-else-if="item.kind==='plugins'" class="window-form plugin-window"><div class="window-section-heading"><div><span class="section-eyebrow">CONTROLLED TOOL PACKS</span><h2>插件工具包</h2><p>仅导入 JSON 清单并绑定已发现的 MCP 工具；不会加载或运行插件代码。</p></div><div class="plugin-head-actions"><button class="secondary-btn" @click="downloadPluginExample">下载清单示例</button><button class="primary-btn" :disabled="pluginBusy" @click="pluginFileInput?.click()">{{pluginBusy?'安装中…':'导入 JSON 清单'}}</button><input ref="pluginFileInput" class="plugin-file-input" type="file" accept=".json,application/json" @change="importPluginFile"/></div></div><div class="plugin-safety-note"><ShieldCheck :size="15"/><span>插件与插件工具默认停用。先启用 MCP 源工具，再分别启用插件和插件工具；模型每次调用仍需在聊天中单独确认。</span></div><p class="plugin-feedback" role="status">{{pluginStatus}}</p><div class="plugin-list"><article v-for="plugin in plugins" :key="plugin.id" class="plugin-card"><div class="plugin-card-head"><div><strong>{{plugin.name}}</strong><span>v{{plugin.version}} · {{plugin.author||'未署名'}}</span></div><div class="plugin-card-actions"><button @click="togglePlugin(plugin)">{{plugin.enabled?'停用插件':'启用插件'}}</button><button class="plugin-remove" @click="removePlugin(plugin)">卸载</button></div></div><p>{{plugin.description}}</p><div v-if="plugin.permissions.length" class="plugin-permissions"><span v-for="permission in plugin.permissions" :key="permission">{{permission}}</span></div><div class="plugin-tools"><div v-for="tool in plugin.tools" :key="tool.id" class="plugin-tool-row"><div><strong>{{tool.alias}}</strong><small>{{tool.server_name}} / {{tool.tool_name}} · {{tool.base_tool_enabled?'源工具已启用':'请先启用 MCP 源工具'}} · 每次确认</small></div><button :disabled="!plugin.enabled" @click="togglePluginTool(plugin,tool)">{{tool.enabled?'停用工具':'授权工具'}}</button></div></div><small class="plugin-checksum">清单 SHA-256：{{plugin.checksum.slice(0,16)}}…</small></article><div v-if="!plugins.length" class="plugin-empty"><Puzzle :size="24"/><strong>还没有安装插件</strong><span>导入 JSON 工具包清单。清单只可绑定已发现的 MCP 工具，不能携带可执行代码。</span></div></div></div><div v-else-if="item.kind==='voice'" class="window-form voice-window"><VoiceSettings :character-id="activeCharacter" :ws-playback="wsPlayback" :call-active="Boolean(callId) || callStartBusy" @update:ws-playback="setWsPlayback"/><DynamicChatOutput v-if="v4DynamicChatIsolated && activeConversation" :character-id="activeCharacter" :conversation-id="activeConversation"/><DynamicChatOutput v-if="v4DynamicCandidateIsolated && activeConversation" :character-id="activeCharacter" :conversation-id="activeConversation" candidate/><VoiceV4OutputPanel v-if="v4OutputIsolated" :character-id="activeCharacter" :conversation-id="activeConversation"/><div class="window-section-heading"><div><span class="section-eyebrow">VOICE</span><h2>设备语音</h2><p>v1 使用当前浏览器和系统提供的语音能力，不保存音频文件。</p></div></div><section class="voice-section"><h3>文字转语音</h3><p>使用浏览器可用的系统语音，不通过 YOSAKI 后端发送文字；所选系统语音是否使用云端服务由浏览器/操作系统决定。</p><label>语言<select v-model="voiceLocale" @change="saveVoiceSettings"><option value="zh-CN">中文（普通话）</option><option value="en-US">English (US)</option><option value="ja-JP">日本語</option></select></label><label>设备语音<select v-model="selectedVoiceURI" @change="saveVoiceSettings"><option value="">自动选择 {{voiceLocale}} 语音</option><option v-for="voice in speechVoices" :key="voice.voiceURI" :value="voice.voiceURI">{{voice.name}} · {{voice.lang}}</option></select></label><label>语速：{{voiceRate.toFixed(1)}}<input v-model.number="voiceRate" type="range" min="0.6" max="1.4" step="0.1" @change="saveVoiceSettings"/></label><div class="voice-actions"><button class="secondary-btn" @click="speakText('你好，我是阿夜。语音朗读测试完成。','voice-test')">测试朗读</button><button class="text-btn" @click="stopSpeaking"><VolumeX :size="14"/>停止</button></div></section><section class="voice-section"><h3>语音转文字</h3><p>优先使用浏览器支持的本机识别；识别文字会放入聊天输入框，由你检查后再发送。</p><button class="primary-btn" @click="requestSpeechInput">{{speechRecognitionRunning?'停止语音识别':'开始语音输入'}}</button><p class="voice-status" role="status">{{speechStatus}}</p><p class="voice-privacy">如果本机识别不可用，YOSAKI 会先询问你是否允许使用浏览器语音识别服务；该服务可能将本次音频发送到浏览器供应商处理。默认不会启动此模式。</p></section></div><div v-else-if="item.kind==='settings'" class="window-form"><div class="window-section-heading"><div><span class="section-eyebrow">WORKSPACE SETTINGS</span><h2>工作台设置</h2><p>窗口布局会自动保存在当前浏览器。</p></div><button class="text-btn" @click="restoreLayout"><RefreshCw :size="14"/>恢复布局</button></div><div class="setting-tile"><LayoutGrid :size="17"/><div><strong>浮动窗口布局</strong><span>支持拖动、缩放、最小化和多窗口叠加。</span></div></div><div class="setting-tile"><ShieldCheck :size="17"/><div><strong>隐私优先</strong><span>当前页面不会把密钥写入浏览器存储。</span></div></div><div class="backup-panel"><h3>数据备份</h3><p>导出完整工作区快照并使用备份密码加密。账户信息也包含在加密文件内；模型 API Key、MCP 密钥和登录会话会主动剔除，隔离验证不会覆盖当前工作区；正式切换需另行审核。恢复后仍需重新配置密钥。</p><label class="backup-passphrase">备份密码<input v-model="backupPassphrase" type="password" autocomplete="new-password" minlength="12" placeholder="至少 12 个字符；单独安全保管"/></label><div class="backup-actions"><button class="secondary-btn" :disabled="backupBusy" @click="exportBackup">导出加密备份</button><button class="text-btn" :disabled="backupBusy" @click="backupFileInput?.click">验证加密备份</button><button v-if="backupBusy" class="text-btn" @click="cancelBackupOperation">取消当前备份操作</button><input ref="backupFileInput" class="plugin-file-input" type="file" accept=".ybackup,application/vnd.yosaki.backup,application/octet-stream" @change="importBackup"/></div><p class="backup-status" role="status">{{backupStatus}}</p><BackupPlanPanel/></div></div>
          <div v-else class="placeholder-panel"><div class="placeholder-icon" :style="{color:feature(item.kind).accent}"><component :is="feature(item.kind).icon" :size="25"/></div><h2>{{feature(item.kind).title}}</h2><p>{{feature(item.kind).description}}</p><span class="placeholder-status">{{feature(item.kind).status}}</span><div class="placeholder-line"><Grip :size="15"/> 这个功能会沿用同一套可移动窗口。</div></div>
        </div>
      </article>
    </section>
  </section>
  <div v-if="speechConsentPrompt" class="speech-consent-backdrop" role="presentation"><section ref="speechConsentDialog" class="speech-consent" @keydown="onSpeechConsentKeydown" role="dialog" aria-modal="true" aria-labelledby="speech-consent-title"><h2 id="speech-consent-title">本机语音识别不可用</h2><p>继续后将启动浏览器语音识别服务；当前音频可能发送至浏览器供应商处理。YOSAKI 不会保存音频，也不会自动发送识别出的文字。</p><div><button class="secondary-btn" @click="speechConsentPrompt=false">取消</button><button class="primary-btn" @click="confirmRemoteSpeech">继续并开始识别</button></div></section></div></div>
</template>
