<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import VolitionPanel from './VolitionPanel.vue'
import FreeActivityPanel from './FreeActivityPanel.vue'
import FeedbackPanel from './FeedbackPanel.vue'

type Drive = { drive_id:string; category:string; name:string; baseline:number; activation:number; satisfaction:number; inhibition:number; version:number }
type State = { enabled:boolean; drives:Drive[]; advisory:{ tendencies:{drive_id:string;name:string;activation:number}[]; conflicts:string[]; note:string }|null }
type Model = { id:number; name:string; enabled:boolean }
type Tool = { id:number;name:string;description:string;server_name:string;enabled:number;server_status:string;read_only_acknowledged:number }
type Session = { id:string;status:string;started_at:string;model_calls:number;max_model_calls:number;tool_calls:number;max_tool_calls:number;deadline_at:string;decision:{decision:string;goal:string;reasonSummary:string;suggestedEffects?:{drive_id:string;activation_delta:number;satisfaction_delta:number;inhibition_delta:number}[]}|null;tool_request:{tool_id:number;name:string;arguments:Record<string,unknown>;read_only_preapproved:boolean}|null;history:{id:number;kind:string;detail:string;created_at:string}[] }
type Preference = { id:number;drive_id:string;before_value:number;after_value:number;evidence_count:number;status:string }
type Goal = { id:number;description:string;status:string;reason_summary:string;created_at:string }
const props = defineProps<{ characterId:number; models:Model[] }>()
const state = ref<State|null>(null)
const sessions = ref<Session[]>([])
const tools = ref<Tool[]>([])
const preferences = ref<Preference[]>([])
const goals = ref<Goal[]>([])
const volitionRevision = ref(0)
const events = ref<{id:number;event_id:string;source:string;description:string;created_at:string}[]>([])
const snapshots = ref<{id:string;created_at:string}[]>([])
const busy = ref(false)
const notice = ref('')
const selectedDrive = ref('exploration.curiosity')
const eventDescription = ref('测试事件')
const activationDelta = ref(0.2)
const satisfactionDelta = ref(0)
const simulateHours = ref(24)
const simulated = ref<Drive[]|null>(null)
const modelId = ref<number|null>(null)
const duration = ref(10)
const maxModelCalls = ref(3)
const maxToolCalls = ref(2)
const allowedIds = ref<number[]>([])
const feedbackOutcome = ref<'positive'|'neutral'|'negative'>('positive')
const feedbackReflection = ref('')
const activeSession = computed(() => sessions.value.find(item => !['completed','cancelled','failed'].includes(item.status)) ?? sessions.value[0] ?? null)
const grouped = computed(() => Object.entries((state.value?.drives ?? []).reduce<Record<string,Drive[]>>((result,drive) => { (result[drive.category] ??= []).push(drive); return result },{})))

async function request<T>(path:string, method='GET', body?:unknown):Promise<T> {
  const response = await fetch(path,{method,credentials:'same-origin',headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)})
  if(!response.ok) throw new Error((await response.json().catch(()=>null))?.detail || `请求失败（${response.status}）`)
  return response.json()
}
function base(){return `/api/desires/${props.characterId}`}
async function refresh(){
  state.value=await request<State>(base())
  if(state.value.enabled){
    const [s,t,p,e,shots,g]=await Promise.all([request<Session[]>(`${base()}/awakening`),request<Tool[]>(`${base()}/tools`),request<Preference[]>(`${base()}/preferences`),request<typeof events.value>(`${base()}/events`),request<typeof snapshots.value>(`${base()}/snapshots`),request<Goal[]>(`${base()}/goals`)])
    sessions.value=s;tools.value=t;preferences.value=p;events.value=e;snapshots.value=shots;goals.value=g
  }else{sessions.value=[];tools.value=[];preferences.value=[];events.value=[];snapshots.value=[];goals.value=[]}
  if(!modelId.value || !props.models.some(item=>item.id===modelId.value))modelId.value=props.models[0]?.id ?? null
  volitionRevision.value++
}
async function run(action:()=>Promise<unknown>,success:string){busy.value=true;notice.value='';try{await action();await refresh();notice.value=success}catch(error){notice.value=error instanceof Error?error.message:'操作失败'}finally{busy.value=false}}
function id(){return crypto.randomUUID()}
async function toggleEnabled(){await run(()=>request(`${base()}/enabled`,'PUT',{enabled:!state.value?.enabled}),'已更新启用状态')}
async function addEvent(){await run(()=>request(`${base()}/events`,'POST',{event_id:id(),source:'user',description:eventDescription.value,effects:[{drive_id:selectedDrive.value,activation_delta:activationDelta.value,satisfaction_delta:satisfactionDelta.value}]}),'事件已记录')}
async function simulate(){await run(async()=>{const result=await request<{drives:Drive[]}>(`${base()}/simulate`,'POST',{hours:simulateHours.value,events:[{event_id:id(),source:'user',description:'隔离模拟',effects:[{drive_id:selectedDrive.value,activation_delta:activationDelta.value,satisfaction_delta:satisfactionDelta.value}]}]});simulated.value=result.drives},'模拟完成；正式状态未更改')}
async function makeSnapshot(){await run(()=>request(`${base()}/snapshots`,'POST',{}),'已保存状态快照')}
async function restoreSnapshot(snapshotId:string){if(!window.confirm('恢复快照会覆盖此角色当前的驱力数值，继续吗？'))return;await run(()=>request(`${base()}/snapshots/${snapshotId}/restore`,'POST',{}),'快照已恢复')}
async function start(){if(!modelId.value)return;await run(()=>request(`${base()}/awakening`,'POST',{model_profile_id:modelId.value,duration_minutes:duration.value,max_model_calls:maxModelCalls.value,max_tool_calls:maxToolCalls.value,allowed_tool_ids:allowedIds.value}),'手动唤醒会话已开始')}
async function sessionAction(action:string,body?:unknown){const current=activeSession.value;if(!current)return;await run(()=>request(`${base()}/awakening/${current.id}/${action}`,'POST',body??{}),'会话已更新')}
async function setReadOnly(tool:Tool){if(!tool.read_only_acknowledged&&!window.confirm('仅当你已核实此工具不会写入、发送、删除或产生费用时才标记为只读。YOSAKI 无法自动证明第三方工具没有副作用。继续吗？'))return;await run(()=>request(`${base()}/tools/${tool.id}/policy`,'PUT',{acknowledged_read_only:!tool.read_only_acknowledged}),'工具分类已更新；已有会话的预授权不会随之扩大')}
async function addFeedback(){const current=activeSession.value;if(!current)return;const suggested=current.decision?.suggestedEffects||[];const effects=suggested.length?suggested:[{drive_id:selectedDrive.value,activation_delta:activationDelta.value,satisfaction_delta:satisfactionDelta.value,inhibition_delta:0}];await run(async()=>{const result=await request<{applied:boolean}>(`${base()}/feedback`,'POST',{event_id:id(),source:'feedback',description:'用户确认活动体验',activity_id:current.id,completed:true,outcome:feedbackOutcome.value,reflection:feedbackReflection.value,effects});if(!result.applied)throw new Error('该活动已有反馈，未重复计入')},'体验反馈已记录')}
async function preferenceAction(item:Preference,action:'decision'|'rollback',approve?:boolean){await run(()=>request(`${base()}/preferences/${item.id}/${action}`,'POST',action==='decision'?{approve}:{}),'偏好变更已处理')}
watch(()=>props.characterId,()=>{state.value=null;simulated.value=null;allowedIds.value=[];void refresh().catch(error=>notice.value=String(error))})
onMounted(()=>{void refresh().catch(error=>notice.value=String(error))})
</script>

<template>
  <div class="desire-panel">
    <div class="window-section-heading"><div><span class="section-eyebrow">NATIVE DESIRE SYSTEM</span><h2>欲望与意愿</h2><p>模拟倾向只供参考。角色可以拒绝、推迟或选择休息。</p></div><button class="secondary-btn" :disabled="busy||!state" @click="toggleEnabled">{{state?.enabled?'停用此角色':'启用此角色'}}</button></div>
    <p role="status" class="desire-notice">{{notice}}</p>
    <p v-if="!state">正在读取状态…</p>
    <p v-else-if="!state.enabled">此角色的欲望系统默认关闭。启用后会创建独立状态，不会复制其他角色的经历。</p>
    <template v-else>
      <VolitionPanel :character-id="characterId" :models="models" :refresh-key="volitionRevision" />
      <FreeActivityPanel :character-id="characterId" :models="models" :tools="tools" @updated="refresh" />
      <FeedbackPanel :character-id="characterId" @updated="refresh" />
      <section class="desire-section"><h3>内部驱力</h3><div v-for="[category,drives] in grouped" :key="category" class="drive-group"><h4>{{category}}</h4><article v-for="drive in drives" :key="drive.drive_id" class="drive-row"><strong>{{drive.name}}</strong><span>激活 {{drive.activation.toFixed(2)}} · 满足 {{drive.satisfaction.toFixed(2)}} · 基线 {{drive.baseline.toFixed(2)}}</span><progress :value="drive.activation" max="1"/></article></div></section>
      <section class="desire-section"><h3>事件与调试</h3><p>下方模拟不会写入正式状态；正式事件需由你明确提交。</p><div class="desire-controls"><label>驱力<select v-model="selectedDrive"><option v-for="drive in state.drives" :key="drive.drive_id" :value="drive.drive_id">{{drive.name}}</option></select></label><label>激活变化<input v-model.number="activationDelta" type="number" min="-1" max="1" step="0.05"/></label><label>满足变化<input v-model.number="satisfactionDelta" type="number" min="-1" max="1" step="0.05"/></label><label>模拟经过小时<input v-model.number="simulateHours" type="number" min="0" max="720"/></label></div><textarea v-model="eventDescription" rows="2" maxlength="500" placeholder="事件说明"/><div class="desire-actions"><button class="secondary-btn" :disabled="busy" @click="simulate">隔离模拟</button><button class="secondary-btn" :disabled="busy" @click="addEvent">记录正式事件</button><button class="text-btn" :disabled="busy" @click="makeSnapshot">保存快照</button></div><p v-if="simulated">模拟结果：{{simulated.find(item=>item.drive_id===selectedDrive)?.activation.toFixed(2)}}（未保存）</p><details><summary>快照与近期事件</summary><div v-for="shot in snapshots" :key="shot.id" class="desire-log"><span>{{shot.created_at}}</span><button class="text-btn" @click="restoreSnapshot(shot.id)">恢复</button></div><p v-for="event in events" :key="event.id">{{event.created_at}} · {{event.description}} · ID {{event.event_id}}</p></details></section>
      <section class="desire-section"><h3>手动唤醒</h3><p>会话仅在你点击后运行。模型用量和工具调用受本次上限约束；不会定时唤醒。</p><div v-if="!activeSession || ['completed','cancelled','failed'].includes(activeSession.status)" class="desire-controls"><label>模型<select v-model="modelId"><option :value="null">选择模型</option><option v-for="profile in models" :key="profile.id" :value="profile.id">{{profile.name}}</option></select></label><label>时长（分钟）<input v-model.number="duration" type="number" min="1" max="60"/></label><label>模型调用上限<input v-model.number="maxModelCalls" type="number" min="1" max="10"/></label><label>工具调用上限<input v-model.number="maxToolCalls" type="number" min="0" max="10"/></label></div><div class="desire-tool-list"><p>第三方 MCP 工具是否有副作用无法自动证明。只有你核实并标记为只读的工具，才能在本次会话内免逐次确认；其他工具每次调用都需要确认。</p><label v-for="tool in tools.filter(item=>item.enabled&&item.server_status==='connected')" :key="tool.id"><input v-model="allowedIds" type="checkbox" :value="tool.id"/><span>{{tool.server_name}} / {{tool.name}}</span><button class="text-btn" @click.prevent="setReadOnly(tool)">{{tool.read_only_acknowledged?'撤销只读标记':'标记只读'}}</button></label></div><button v-if="!activeSession || ['completed','cancelled','failed'].includes(activeSession.status)" class="primary-btn" :disabled="busy||!modelId" @click="start">开始手动唤醒</button><template v-else><p>状态：{{activeSession.status}} · 模型 {{activeSession.model_calls}}/{{activeSession.max_model_calls}} · 工具 {{activeSession.tool_calls}}/{{activeSession.max_tool_calls}}</p><p v-if="activeSession.decision">{{activeSession.decision.goal}} · {{activeSession.decision.reasonSummary}}</p><div v-if="activeSession.tool_request"><strong>工具请求：{{activeSession.tool_request.name}}</strong><pre>{{JSON.stringify(activeSession.tool_request.arguments,null,2)}}</pre><p>{{activeSession.tool_request.read_only_preapproved?'已预授权的只读工具':'需要本次确认'}}</p><button class="secondary-btn" :disabled="busy" @click="sessionAction('tools/decision',{approve:true})">批准本次调用</button><button class="text-btn" :disabled="busy" @click="sessionAction('tools/decision',{approve:false})">拒绝</button></div><div class="desire-actions"><button v-if="activeSession.status==='ready'" class="primary-btn" :disabled="busy" @click="sessionAction('step')">进行一次意愿判断</button><button v-if="activeSession.status==='ready'||activeSession.status==='awaiting_confirmation'" class="secondary-btn" :disabled="busy" @click="sessionAction('pause')">暂停</button><button v-if="activeSession.status==='paused'" class="secondary-btn" :disabled="busy" @click="sessionAction('resume')">恢复</button><button class="text-btn" :disabled="busy" @click="sessionAction('cancel')">结束会话</button></div></template><div v-if="goals.length"><h4>意愿目标</h4><article v-for="goal in goals" :key="goal.id" class="desire-log"><strong>{{goal.description}}</strong><span>{{goal.status}} · {{goal.reason_summary}}</span></article></div><details v-if="sessions.length"><summary>近期活动</summary><article v-for="item in sessions" :key="item.id" class="desire-log"><strong>{{item.started_at}} · {{item.status}}</strong><p v-for="entry in item.history" :key="entry.id">{{entry.kind}} · {{entry.detail}}</p></article></details></section>
      <section class="desire-section"><h3>体验反馈与长期偏好</h3><p>完成活动不会自动清零欲望。由你确认模拟体验后，反馈才会更新正式状态。</p><div class="desire-controls"><label>结果<select v-model="feedbackOutcome"><option value="positive">正向</option><option value="neutral">中性</option><option value="negative">负向</option></select></label><label>体验说明<input v-model="feedbackReflection" maxlength="1000" placeholder="可选"/></label></div><p v-if="activeSession?.decision?.suggestedEffects?.length">模型提出的反馈候选：{{activeSession.decision.suggestedEffects.map(item=>`${item.drive_id}：激活 ${item.activation_delta}、满足 ${item.satisfaction_delta}`).join("；")}}。只有你确认后才入账。</p><button class="secondary-btn" :disabled="busy||!activeSession" @click="addFeedback">确认并记录反馈</button><article v-for="item in preferences" :key="item.id" class="desire-log"><span>{{item.drive_id}} · {{item.before_value.toFixed(2)}} → {{item.after_value.toFixed(2)}} · {{item.evidence_count}} 条证据 · {{item.status}}</span><button v-if="item.status==='pending'" class="text-btn" @click="preferenceAction(item,'decision',true)">应用</button><button v-if="item.status==='pending'" class="text-btn" @click="preferenceAction(item,'decision',false)">拒绝</button><button v-if="item.status==='applied'" class="text-btn" @click="preferenceAction(item,'rollback')">撤销</button></article></section>
    </template>
  </div>
</template>

<style scoped>
.desire-panel{padding:22px;display:grid;gap:18px;overflow:auto;height:100%}.desire-section{padding:17px;border:1px solid var(--ui-line-soft);border-radius:14px;background:var(--ui-raised);display:grid;gap:10px}.desire-section h3,.drive-group h4{margin:0}.desire-section p{margin:0;line-height:1.5}.drive-group{display:grid;gap:7px}.drive-row{display:grid;grid-template-columns:minmax(90px,1fr) minmax(170px,2fr);gap:7px;align-items:center}.drive-row progress{grid-column:1/-1;width:100%}.drive-row span{font-size:13px;opacity:1}.desire-controls{display:flex;flex-wrap:wrap;gap:12px}.desire-controls label{display:grid;gap:5px;min-width:140px;flex:1}.desire-controls input,.desire-controls select,.desire-section textarea{width:100%;box-sizing:border-box}.desire-actions{display:flex;flex-wrap:wrap;gap:8px}.desire-tool-list{display:grid;gap:7px}.desire-tool-list label{display:flex;gap:8px;align-items:center}.desire-log{display:flex;gap:8px;flex-wrap:wrap;align-items:center;padding:7px 0;border-top:1px solid var(--ui-line-soft)}.desire-log p{width:100%;font-size:13px;overflow-wrap:anywhere}.desire-notice{min-height:1.2em}.desire-section pre{white-space:pre-wrap;overflow-wrap:anywhere}
</style>
