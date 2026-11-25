<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'

type DriveChange = {drive_id:string;before:{activation:number;satisfaction:number;inhibition:number};after:{activation:number;satisfaction:number;inhibition:number};reason:string}
type Evidence = {operationId:string|null;kind:string;status:string;result:{summary?:string;error?:string;source?:string}|null;expectedEnjoyment:number|null}
type Review = {enjoyment:number|null;fulfillment:number|null;frustration:number|null;fatigue:number|null;novelty:number|null;wantsToContinue:boolean|null;reflectionSummary:string;newInterests:string[];confidence:number|null}
type Feedback = {id:string;version:number;title:string;objective_outcome:string;end_reason:string;status:string;review_error:string|null;reevaluation_suggested:number;expected_enjoyment:number|null;expectation_gap:number|null;evidence:Evidence[];review:Review|null;proposal:DriveChange[];created_at:string}
type Timeline = {id:number;drive_id:string;feedback_id:string;before_json:string;after_json:string;reason:string;created_at:string}
type Interest = {id:string;description:string;expectation_gap:number|null;status:string;created_at:string}

const props = defineProps<{characterId:number}>()
const emit = defineEmits<{updated:[]}>()
const items = ref<Feedback[]>([])
const timeline = ref<Timeline[]>([])
const interests = ref<Interest[]>([])
const notice = ref('')
const busy = ref(false)
const selected = ref('')
const simulateCount = ref(100)
const resolvedOutcome = ref<'success'|'failure'>('success')
const evidenceNote = ref('')
const simulated = ref<{step:number;drives:Record<string,{activation:number;satisfaction:number;inhibition:number}>}[]>([])
let poller:ReturnType<typeof setInterval>|undefined
const base = () => `/api/desires/${props.characterId}/feedback-v4`
const current = computed(() => items.value.find(item=>item.id===selected.value) || null)
const simulationEnd = computed(() => simulated.value[simulated.value.length-1])
const score = (value:number|null|undefined) => value == null ? '未知' : `${Math.round(value*100)}/100`
const gap = (value:number|null) => value == null ? '未知' : `${value>=0?'+':''}${Math.round(value*100)}`

async function request<T>(path:string,method='GET',body?:unknown):Promise<T>{
  const response=await fetch(path,{method,credentials:'same-origin',headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)})
  if(!response.ok)throw new Error((await response.json().catch(()=>null))?.detail || `请求失败（${response.status}）`)
  return response.json()
}
async function refresh(){
  const [feedback,changes,candidates]=await Promise.all([
    request<Feedback[]>(base()),request<Timeline[]>(`${base()}/timeline/events`),request<Interest[]>(`${base()}/interests/candidates`),
  ])
  items.value=feedback;timeline.value=changes;interests.value=candidates
  if(!feedback.some(item=>item.id===selected.value))selected.value=feedback[0]?.id||''
}
async function perform(task:()=>Promise<unknown>,message:string){
  busy.value=true;notice.value=''
  try{await task();await refresh();notice.value=message;emit('updated')}
  catch(error){notice.value=error instanceof Error?error.message:'操作失败';await refresh().catch(()=>{})}
  finally{busy.value=false}
}
async function decide(apply:boolean){
  if(!current.value)return
  const item=current.value
  await perform(()=>request(`${base()}/${item.id}/decision`,'POST',{schemaVersion:4,requestId:crypto.randomUUID(),expectedVersion:item.version,apply}),apply?'反馈已应用；下一次意愿判断可参考更新状态':'反馈已跳过')
}
async function rereview(){
  if(!current.value)return
  const item=current.value
  await perform(()=>request(`${base()}/${item.id}/review`,'POST',{schemaVersion:4,requestId:crypto.randomUUID(),expectedVersion:item.version,maxOutputTokens:600}),'体验评价已更新')
}
async function resolve(){
  if(!current.value || !evidenceNote.value.trim())return
  const item=current.value
  await perform(()=>request(`${base()}/${item.id}/resolve`,'POST',{schemaVersion:4,requestId:crypto.randomUUID(),expectedVersion:item.version,outcome:resolvedOutcome.value,evidenceNote:evidenceNote.value.trim()}),'人工核实已记录；来源会明确标为用户说明')
  evidenceNote.value=''
}
async function simulate(){
  const driveId='exploration.curiosity'
  const count=Math.max(1,Math.min(500,Math.floor(simulateCount.value||100)))
  await perform(async()=>{const result=await request<{trajectory:typeof simulated.value}>(`${base()}/simulate`,'POST',{schemaVersion:4,events:Array.from({length:count},(_,index)=>({driveId,relevance:0.8,confidence:0.7,fulfillment:index%2?0.3:0.8,novelty:index%5?0.1:0.7,frustration:index%2?0.4:0.1,repeatCount:index}))});simulated.value=result.trajectory},'隔离模拟完成；正式状态未变')
}
watch(()=>props.characterId,()=>{selected.value='';void refresh().catch(error=>notice.value=String(error))})
onMounted(()=>{void refresh().catch(error=>notice.value=String(error));poller=setInterval(()=>{if(!busy.value)void refresh().catch(()=>{})},3000)})
onUnmounted(()=>{if(poller)clearInterval(poller)})
</script>

<template>
  <section class="desire-section feedback-panel">
    <h3>体验反馈与预期差</h3>
    <p>实际操作是证据，模拟感受是可审阅的评价。只有你确认后，短期驱力才会改变；长期人格和目标不会被反馈自动改写。</p>
    <p role="status">{{notice}}</p>
    <label v-if="items.length">选择活动<select v-model="selected"><option v-for="item in items" :key="item.id" :value="item.id">{{item.created_at}} · {{item.title}} · {{item.status}}</option></select></label>
    <p v-else>暂无自由活动反馈。结束一段有实际活动的自由时间后，这里会显示待审阅记录。</p>
    <template v-if="current">
      <div class="feedback-card"><strong>{{current.title}}</strong><span>目标结果：{{current.objective_outcome}} · 会话结束：{{current.end_reason}} · 状态：{{current.status}}</span>
        <span>活动前预估：{{score(current.expected_enjoyment)}} · 事后乐趣：{{score(current.review?.enjoyment)}} · 预期差：{{gap(current.expectation_gap)}}</span>
        <small>预期差是体验证据，不直接作为驱力增量。未知值不会补造。</small>
      </div>
      <p v-if="current.review">模拟体验：{{current.review.reflectionSummary||'没有明显感受'}} · 满足 {{score(current.review.fulfillment)}} · 新颖 {{score(current.review.novelty)}} · 疲劳 {{score(current.review.fatigue)}}</p>
      <p v-if="current.reevaluation_suggested">下次明确进行意愿判断时可重新考虑；当前意愿及长期目标未自动改变。</p>
      <p v-if="current.review_error">评价未完成：{{current.review_error}}。客观结果已保留。</p>
      <details><summary>实际操作证据（{{current.evidence.length}}）</summary><article v-for="[index,entry] in current.evidence.entries()" :key="entry.operationId||index" class="feedback-row"><strong>{{entry.kind}} · {{entry.status}}</strong><span>{{entry.result?.summary||entry.result?.error||'无确定结果'}}</span><small v-if="entry.result?.source">来源：{{entry.result.source}}</small></article></details>
      <div v-if="current.status==='awaiting_evidence'" class="feedback-card"><strong>操作结果未知，等待核实</strong><p>这里不会把未知结果算成成功或失败。若你已独立核实，可明确记录结果和依据；该说明会标为用户提供。</p><label>核实结果<select v-model="resolvedOutcome"><option value="success">已核实成功</option><option value="failure">已核实失败</option></select></label><label>核实依据<input v-model="evidenceNote" maxlength="500" placeholder="描述你如何核实实际结果"/></label><button class="secondary-btn" :disabled="busy||!evidenceNote.trim()" @click="resolve">记录人工核实</button></div>
      <div v-if="current.proposal.length" class="feedback-card"><strong>{{current.status==='applied'?'已确认的短期变化':'待确认的短期变化'}}</strong><div v-for="change in current.proposal" :key="change.drive_id" class="feedback-row"><span>{{change.drive_id}}：激活 {{change.before.activation.toFixed(2)}} → {{change.after.activation.toFixed(2)}}；满足 {{change.before.satisfaction.toFixed(2)}} → {{change.after.satisfaction.toFixed(2)}}；抑制 {{change.before.inhibition.toFixed(2)}} → {{change.after.inhibition.toFixed(2)}}</span><small>{{change.reason}}</small></div></div>
      <p v-else>没有足够证据支持数值变化；可以只保存客观记录。</p>
      <div v-if="current.status==='ready'" class="feedback-actions"><button class="primary-btn" :disabled="busy" @click="decide(true)">确认应用</button><button class="text-btn" :disabled="busy" @click="decide(false)">跳过</button><button class="secondary-btn" :disabled="busy" @click="rereview">重新评价（额外模型调用）</button></div>
      <button v-if="current.status==='review_failed'" class="secondary-btn" :disabled="busy" @click="rereview">手动重新评价（额外模型调用）</button>
    </template>
    <details><summary>驱力变化时间线（{{timeline.length}}）</summary><article v-for="event in timeline" :key="event.id" class="feedback-row"><time>{{event.created_at}}</time><span>{{event.drive_id}} · {{event.reason}}</span></article></details>
    <details><summary>新兴趣证据（{{interests.length}}）</summary><p v-for="item in interests" :key="item.id">{{item.description}} · {{item.status}} · 预期差 {{gap(item.expectation_gap)}}</p></details>
    <details><summary>隔离反馈模拟</summary><label>模拟次数<input v-model.number="simulateCount" type="number" min="1" max="500"/></label><button class="secondary-btn" :disabled="busy" @click="simulate">运行隔离模拟</button><p v-if="simulationEnd">第 {{simulationEnd.step}} 次：探索激活 {{simulationEnd.drives['exploration.curiosity']?.activation.toFixed(3)}}；正式状态未更改。</p></details>
  </section>
</template>

<style scoped>
.feedback-panel{display:grid;gap:10px}.feedback-panel h3,.feedback-panel p{margin:0}.feedback-panel label{display:grid;gap:5px;max-width:520px}.feedback-panel select,.feedback-panel input{width:100%;box-sizing:border-box}.feedback-card{display:grid;gap:6px;padding:11px;border:1px solid var(--ui-line-soft);border-radius:10px}.feedback-row{display:flex;flex-wrap:wrap;gap:8px;padding:7px 0;border-top:1px solid var(--ui-line-soft);overflow-wrap:anywhere}.feedback-row small{opacity:1}.feedback-actions{display:flex;flex-wrap:wrap;gap:8px}
</style>
