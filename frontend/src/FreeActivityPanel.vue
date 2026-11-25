<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'

type Model = { id:number; name:string; enabled:boolean }
type Tool = { id:number; name:string; server_name:string; enabled:number; server_status:string; read_only_acknowledged:number }
type Session = { id:string; mode:string; status:string; revision:number; environment_id:string; started_at:string; deadline_at:string; model_calls:number; max_model_calls:number; tool_calls:number; max_tool_calls:number; output_tokens_used:number; max_output_tokens:number; feedback_review_limit:number; feedback_review_calls_used:number; feedback_review_tokens:number; feedback_review_output_used:number; psychology_appraisal_limit:number; psychology_appraisal_tokens:number; termination_reason:string|null; decision:{choice:string;goalTitle:string;reasonSummary:string}|null; plan:{id:string;title:string;status:string;steps:{id:string;description:string;status:string}[]}|null }
type Operation = { id:string;kind:string;status:string;action_id:string|null;tool_id:number|null;arguments:Record<string,unknown>;result:{summary?:string;error?:string}|null }
type Timeline = { id:number;kind:string;detail:Record<string,unknown>;evidence_type:string;created_at:string }
type Environment = { id:string;name:string;description:string;objects:{id:string;name:string;inspected:boolean}[];discoveries:{id:string;summary:string;created_at:string}[];availableActions:{id:string;description:string}[] }

const props = defineProps<{characterId:number;models:Model[];tools:Tool[]}>()
const emit = defineEmits<{updated:[]}>()
const busy = ref(false)
const notice = ref('')
const sessions = ref<Session[]>([])
const selectedId = ref('')
const current = ref<Session|null>(null)
const operations = ref<Operation[]>([])
const timeline = ref<Timeline[]>([])
const environment = ref<Environment|null>(null)
const environments = ref<{id:string;name:string}[]>([])
const environmentId = ref('local-studio-v1')
const modelId = ref<number|null>(null)
const duration = ref(10)
const maxModels = ref(3)
const maxActions = ref(3)
const maxOutputTokens = ref(2400)
const feedbackReviewCalls = ref(1)
const feedbackReviewTokens = ref(600)
const psychologyAppraisalCalls = ref(1)
const psychologyAppraisalTokens = ref(600)
const selectedTools = ref<number[]>([])
let timer:ReturnType<typeof setInterval>|undefined

const base = () => `/api/desires/${props.characterId}`
const running = computed(() => current.value && !['completed','cancelled','failed'].includes(current.value.status))
const pendingOperation = computed(() => operations.value.find(item => item.status === 'pending_confirmation'))
const availableTools = computed(() => props.tools.filter(item => item.enabled && item.server_status === 'connected'))

async function request<T>(path:string,method='GET',body?:unknown):Promise<T>{
  const response = await fetch(path,{method,credentials:'same-origin',headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)})
  if(!response.ok) throw new Error((await response.json().catch(()=>null))?.detail || `请求失败（${response.status}）`)
  return response.json()
}
async function refresh(){
  const [all,choices] = await Promise.all([request<Session[]>(`${base()}/awakening`),request<{id:string;name:string}[]>(`${base()}/environments`)])
  sessions.value = all.filter(item => item.mode === 'free')
  environments.value = choices
  if(!choices.some(item=>item.id===environmentId.value))environmentId.value=choices[0]?.id||''
  if(!sessions.value.some(item => item.id === selectedId.value)) selectedId.value = sessions.value[0]?.id || ''
  if(selectedId.value){
    const [detail,ops,events] = await Promise.all([
      request<Session>(`${base()}/awakening/${selectedId.value}`),
      request<Operation[]>(`${base()}/awakening/${selectedId.value}/operations`),
      request<Timeline[]>(`${base()}/awakening/${selectedId.value}/timeline`),
    ])
    current.value=detail;operations.value=ops;timeline.value=events
  }else{current.value=null;operations.value=[];timeline.value=[]}
  const displayedEnvironment=(current.value && !['completed','cancelled','failed'].includes(current.value.status) ? current.value.environment_id : environmentId.value)
  environment.value=displayedEnvironment?await request<Environment>(`${base()}/environments/${displayedEnvironment}`):null
  if(!modelId.value || !props.models.some(item=>item.id===modelId.value && item.enabled)) modelId.value=props.models.find(item=>item.enabled)?.id ?? null
}
async function perform(action:()=>Promise<unknown>,message:string){
  busy.value=true;notice.value=''
  try{await action();await refresh();notice.value=message;emit('updated')}
  catch(error){notice.value=error instanceof Error?error.message:'操作失败';await refresh().catch(()=>{})}
  finally{busy.value=false}
}
async function start(){
  if(!modelId.value)return
  await perform(async()=>{
    const result=await request<Session>(`${base()}/awakening`,'POST',{
      mode:'free',request_id:crypto.randomUUID(),model_profile_id:modelId.value,
      environment_id:environmentId.value,duration_minutes:duration.value,max_model_calls:maxModels.value,
      max_tool_calls:maxActions.value,max_output_tokens:maxOutputTokens.value,allowed_tool_ids:selectedTools.value,
      feedback_review_calls:feedbackReviewCalls.value,feedback_review_tokens:feedbackReviewTokens.value,
      psychology_appraisal_calls:psychologyAppraisalCalls.value,psychology_appraisal_tokens:psychologyAppraisalTokens.value,
    })
    selectedId.value=result.id
  },'自由活动已开始；关闭页面后，后端仍会在授权时窗内继续。')
}
async function control(action:'pause'|'resume'|'stop'|'cancel'){
  if(!current.value)return
  const item=current.value
  await perform(()=>request(`${base()}/awakening/${item.id}/${action}`,'POST',{schemaVersion:3,requestId:crypto.randomUUID(),expectedRevision:item.revision}),action==='stop'?'已正常提前结束':'会话已更新')
}
async function decide(approve:boolean){
  if(!current.value || !pendingOperation.value)return
  const item=current.value,operation=pendingOperation.value
  await perform(()=>request(`${base()}/awakening/${item.id}/operations/${operation.id}/decision`,'POST',{schemaVersion:3,requestId:crypto.randomUUID(),expectedRevision:item.revision,approve}),approve?'已批准本次操作':'已拒绝本次操作')
}
function describe(entry:Timeline){
  if(entry.kind==='decision')return `${entry.detail.choice ?? '决定'} · ${entry.detail.goalTitle ?? ''} ${entry.detail.reasonSummary ?? ''}`
  if(entry.kind==='operation_result')return String((entry.detail.result as {summary?:string;error?:string}|undefined)?.summary || (entry.detail.result as {error?:string}|undefined)?.error || '操作已返回')
  return String(entry.detail.summary || entry.detail.reason || entry.detail.action || entry.detail.operationId || entry.kind)
}
watch(()=>props.characterId,()=>{selectedId.value='';selectedTools.value=[];void refresh().catch(error=>notice.value=String(error))})
watch(selectedId,()=>{if(selectedId.value)void refresh().catch(error=>notice.value=String(error))})
watch(environmentId,()=>{if(!running.value)void refresh().catch(error=>notice.value=String(error))})
onMounted(()=>{void refresh().catch(error=>notice.value=String(error));timer=setInterval(()=>{if(!busy.value)void refresh().catch(()=>{})},2000)})
onUnmounted(()=>{if(timer)clearInterval(timer)})
</script>

<template>
  <section class="free-activity desire-section">
    <div><h3>自主活动</h3><p>手动分配自由时间。阿夜可以探索、改主意、休息或提前结束；未用完时间不是失败。</p></div>
    <p role="status">{{notice}}</p>
    <div v-if="environment" class="scene-card">
      <strong>{{environment.name}}</strong><span>{{environment.description}}</span>
      <div class="scene-objects"><span v-for="object in environment.objects" :key="object.id">{{object.name}} · {{object.inspected?'已查看':'尚未查看'}}</span></div>
      <small>这是持久化的本地示例场景；其中的资料不是现实世界事实。实际发现会保留到下次唤醒。</small>
    </div>
    <template v-if="!running">
      <div class="free-fields">
        <label>模型<select v-model="modelId"><option :value="null">选择模型</option><option v-for="model in models.filter(item=>item.enabled)" :key="model.id" :value="model.id">{{model.name}}</option></select></label>
        <label>活动环境<select v-model="environmentId"><option v-for="item in environments" :key="item.id" :value="item.id">{{item.name}}</option></select></label>
        <label>时长（分钟）<input v-model.number="duration" type="number" min="1" max="60"/></label>
        <label>模型调用上限<input v-model.number="maxModels" type="number" min="1" max="10"/></label>
        <label>环境及工具动作上限<input v-model.number="maxActions" type="number" min="0" max="10"/></label>
        <label>累计输出 token 上限<input v-model.number="maxOutputTokens" type="number" min="128" max="16000" step="100"/></label>
        <label>体验回顾调用上限<select v-model.number="feedbackReviewCalls"><option :value="1">最多 1 次</option><option :value="0">关闭</option></select></label>
        <label>回顾输出 token 预留<input v-model.number="feedbackReviewTokens" type="number" min="128" max="1000" step="50"/></label>
        <label>心理情境评价调用上限<select v-model.number="psychologyAppraisalCalls"><option :value="1">最多 1 次</option><option :value="0">关闭</option></select></label>
        <label>心理评价输出 token 预留<input v-model.number="psychologyAppraisalTokens" type="number" min="128" max="1200" step="50"/></label>
      </div>
      <p>体验回顾使用所选模型，独立计入上方预留预算；生成后仍需你审阅确认，才会改变短期驱力。</p>
      <div class="free-tools"><strong>本次允许的 MCP 工具（默认无）</strong><label v-for="tool in availableTools" :key="tool.id"><input v-model="selectedTools" type="checkbox" :value="tool.id"/>{{tool.server_name}} / {{tool.name}} · {{tool.read_only_acknowledged?'你已标记只读':'逐次确认'}}</label><p>第三方 MCP 工具的副作用无法由 YOSAKI 自动证明；写入、发送、删除、付费及未核实只读的操作必须逐次确认。</p></div>
      <button class="primary-btn" :disabled="busy||!modelId||!!sessions.find(item=>!['completed','cancelled','failed'].includes(item.status))" @click="start">开始自由活动</button>
    </template>
    <div v-if="sessions.length" class="free-history"><label>查看活动时段<select v-model="selectedId"><option v-for="item in sessions" :key="item.id" :value="item.id">{{item.started_at}} · {{item.status}}</option></select></label></div>
    <template v-if="current">
      <p>状态：{{current.status}} · 模型 {{current.model_calls}}/{{current.max_model_calls}} · 动作 {{current.tool_calls}}/{{current.max_tool_calls}} · 输出 token {{current.output_tokens_used}}/{{current.max_output_tokens}} · 体验回顾 {{current.feedback_review_calls_used}}/{{current.feedback_review_limit}} · 回顾输出 token {{current.feedback_review_output_used}}/{{current.feedback_review_tokens}}</p>
      <p v-if="current.termination_reason">结束原因：{{current.termination_reason}}</p>
      <div v-if="current.plan" class="scene-card"><strong>当前计划：{{current.plan.title}}</strong><span v-for="step in current.plan.steps" :key="step.id">{{step.description}} · {{step.status}}</span></div>
      <div v-if="pendingOperation" class="scene-card"><strong>等待本次确认：{{pendingOperation.kind==='tool'?'MCP 工具':'环境动作'}} {{pendingOperation.tool_id||pendingOperation.action_id}}</strong><pre>{{JSON.stringify(pendingOperation.arguments,null,2)}}</pre><div class="free-actions"><button class="secondary-btn" :disabled="busy" @click="decide(true)">批准本次</button><button class="text-btn" :disabled="busy" @click="decide(false)">拒绝</button></div></div>
      <div v-if="running" class="free-actions"><button v-if="current.status!=='paused'" class="secondary-btn" :disabled="busy" @click="control('pause')">暂停</button><button v-if="current.status==='paused'" class="secondary-btn" :disabled="busy" @click="control('resume')">恢复</button><button class="text-btn" :disabled="busy" @click="control('stop')">正常提前结束</button><button class="text-btn" :disabled="busy" @click="control('cancel')">取消</button></div>
      <details><summary>活动时间线（{{timeline.length}}）</summary><article v-for="entry in timeline" :key="entry.id" class="timeline-row"><time>{{entry.created_at}}</time><span>{{describe(entry)}}</span><small>{{entry.evidence_type==='fact'?'实际记录':entry.evidence_type==='reflection'?'模拟自述':entry.evidence_type==='unknown'?'结果未知':'计划'}}</small></article></details>
      <details><summary>操作结果与发现</summary><article v-for="op in operations" :key="op.id" class="timeline-row"><strong>{{op.kind}} · {{op.status}}</strong><span>{{op.result?.summary||op.result?.error||op.action_id||op.tool_id}}</span></article><p v-for="discovery in environment?.discoveries||[]" :key="discovery.id">{{discovery.summary}}</p></details>
    </template>
  </section>
</template>

<style scoped>
.free-activity{gap:12px}.free-activity h3,.free-activity p{margin:0}.free-fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:10px}.free-fields label,.free-history label{display:grid;gap:5px}.free-fields input,.free-fields select,.free-history select{width:100%;box-sizing:border-box}.scene-card{display:grid;gap:7px;padding:12px;border:1px solid var(--ui-line-soft);border-radius:12px;background:var(--ui-raised)}.scene-objects{display:flex;flex-wrap:wrap;gap:8px}.scene-objects span{padding:4px 8px;border-radius:9px;background:var(--ui-raised)}.free-tools{display:grid;gap:6px}.free-tools label{display:flex;align-items:center;gap:6px}.free-actions{display:flex;flex-wrap:wrap;gap:8px}.timeline-row{display:flex;flex-wrap:wrap;gap:8px;padding:7px 0;border-top:1px solid var(--ui-line-soft);overflow-wrap:anywhere}.timeline-row time,.timeline-row small{opacity:1}.free-activity pre{white-space:pre-wrap;overflow-wrap:anywhere}
</style>
