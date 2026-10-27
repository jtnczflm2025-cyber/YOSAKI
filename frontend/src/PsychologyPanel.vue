<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'

type Snapshot = { characterId:number; revision:number; timestamp:string; ruleVersion:string; emotion:Record<string,number>; body:Record<string,number>; emotionHypotheses:{label:string;intensity:number;confidence:number}[]; driveHints:Record<string,number>; conflicts:{id:string;type:string;description:string;confidence:number;suggestedOptions:string[]}[]; secondOrderDesires:{id:string;target_type:string;target_id:string|null;attitude:string;strength:number;confidence:number;reason_summary:string;status:string;version:number;source_event_ids_json:string}[] }
type Edge = {id:string;source_type:string;source_id:string;target_type:string;target_id:string;relation_type:string;weight:number;confidence:number;source:string;status:string;enabled:number;version:number;reason:string}
type Event = {id:number;kind:string;source_ref:string;created_at:string;appraisal:Record<string,unknown>;graph:Record<string,number>;event_id:string}
type Run = {session_id:string;status:string;token_cap:number;token_used:number;error:string|null}
type ConflictDecision = {id:number;conflict_id:string;decision_ref:string;choice:string;outcome_ref:string|null;created_at:string}
const props = defineProps<{characterId:number}>()
const snapshot = ref<Snapshot|null>(null)
const edges = ref<Edge[]>([])
const events = ref<Event[]>([])
const runs = ref<Run[]>([])
const conflictHistory = ref<ConflictDecision[]>([])
const busy = ref(false)
const notice = ref('')
const selectedEvent = ref<number|null>(null)
const simCount = ref(100)
const trajectory = ref<{step:number;emotion:Record<string,number>;body:Record<string,number>}[]>([])
const proposal = ref({sourceType:'body_state',sourceId:'fatigue',targetType:'drive',targetId:'physiological.rest',relationType:'amplify',weight:.05,confidence:.5,reason:''})
const emotionNames:Record<string,string>={valence:'情绪倾向',arousal:'激活程度',tension:'紧张程度',senseOfControl:'掌控感'}
const bodyNames:Record<string,string>={energy:'模拟能量',fatigue:'模拟疲劳',cognitiveLoad:'认知负荷',socialSaturation:'社交负荷',recoveryNeed:'恢复倾向'}
const labels:Record<string,string>={joy:'愉悦',frustration:'挫折',curiosity:'好奇',anticipation:'期待',relief:'释然',endorse:'认同',resist:'抵触',cultivate:'培养',reduce:'减弱',uncertain:'尚未决定'}
const base=()=>`/api/desires/${props.characterId}/psychology`
const percent=(value:number)=>`${Math.round(value*100)}`
async function request<T>(url:string,method='GET',body?:unknown):Promise<T>{
  const response=await fetch(url,{method,credentials:'same-origin',headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)})
  if(!response.ok)throw new Error((await response.json().catch(()=>null))?.detail||`请求失败（${response.status}）`)
  return response.json()
}
async function refresh(){
  const [state,relation,history,appraisals,conflicts]=await Promise.all([request<Snapshot>(base()),request<Edge[]>(`${base()}/edges`),request<Event[]>(`${base()}/events`),request<Run[]>(`${base()}/appraisals`),request<ConflictDecision[]>(`${base()}/conflicts/history`)])
  snapshot.value=state;edges.value=relation;events.value=history;runs.value=appraisals;conflictHistory.value=conflicts
}
async function perform(task:()=>Promise<unknown>,message:string){busy.value=true;notice.value='';try{await task();await refresh();notice.value=message}catch(error){notice.value=error instanceof Error?error.message:'操作失败'}finally{busy.value=false}}
async function tick(){if(!snapshot.value)return;await perform(()=>request(`${base()}/tick`,'POST',{schemaVersion:6,requestId:crypto.randomUUID(),expectedRevision:snapshot.value!.revision}),'已按真实经过时间记录一次状态推进')}
async function changeDesire(item:Snapshot['secondOrderDesires'][number],action:'archive'|'restore'){await perform(()=>request(`${base()}/second-order/${item.id}/transition`,'POST',{schemaVersion:6,requestId:crypto.randomUUID(),expectedVersion:item.version,action}),action==='archive'?'已归档反思倾向':'已恢复反思倾向')}
async function propose(){await perform(()=>request(`${base()}/edges`,'POST',{schemaVersion:6,requestId:crypto.randomUUID(),...proposal.value}),'关联已提出，等待审阅')}
async function decide(edge:Edge,action:'approve'|'reject'|'disable'|'enable'){await perform(()=>request(`${base()}/edges/${edge.id}/decision`,'POST',{schemaVersion:6,requestId:crypto.randomUUID(),expectedVersion:edge.version,action}),'关联状态已更新')}
async function simulate(){const count=Math.max(1,Math.min(500,Math.floor(simCount.value)));await perform(async()=>{const result=await request<{trajectory:typeof trajectory.value}>(`${base()}/simulate`,'POST',{schemaVersion:6,events:Array.from({length:count},(_,i)=>i%3===0?{kind:'feedback',enjoyment:i%2?.8:.25}:i%7===0?{kind:'tick',hours:12}:{kind:'operation',status:'succeeded',activityType:i%5===0?'rest':'research'})});trajectory.value=result.trajectory},`隔离模拟 ${count} 次完成；正式状态未改变`)}
watch(()=>props.characterId,()=>{snapshot.value=null;refresh().catch(error=>notice.value=error.message)})
onMounted(()=>refresh().catch(error=>notice.value=error.message))
</script>

<template>
  <div class="psychology-panel">
    <div class="psychology-head"><div><span class="section-eyebrow">SIMULATED PSYCHOLOGY · PHASE 6</span><h2>心理状态</h2><p>这里展示计算性模拟，不能当作真实感受或健康测量；它只为意愿判断提供参考。</p></div><button class="secondary-btn" :disabled="busy" @click="refresh">刷新</button></div>
    <p v-if="notice" role="status" class="psychology-notice">{{notice}}</p>
    <template v-if="snapshot">
      <p class="psychology-meta">角色版本 {{snapshot.revision}} · 规则 {{snapshot.ruleVersion}} · 上次记录 {{snapshot.timestamp}}</p>
      <div class="psychology-grid">
        <section class="psychology-card"><h3>模拟情绪</h3><div v-for="(value,key) in snapshot.emotion" :key="key" class="psychology-meter"><span>{{emotionNames[key]||key}}</span><strong>{{percent(value)}}</strong><progress :value="key==='valence'?(value+1)/2:value" max="1"/></div><p v-if="!snapshot.emotionHypotheses.length">目前没有足够依据形成显著情绪假设。</p><div v-for="item in snapshot.emotionHypotheses" :key="item.label" class="psychology-chip">{{labels[item.label]||item.label}} · 强度 {{percent(item.intensity)}} · 可信度 {{percent(item.confidence)}}</div></section>
        <section class="psychology-card"><h3>虚拟身体状态</h3><div v-for="(value,key) in snapshot.body" :key="key" class="psychology-meter"><span>{{bodyNames[key]||key}}</span><strong>{{percent(value)}}</strong><progress :value="value" max="1"/></div><button class="text-btn" :disabled="busy" @click="tick">记录时间推进</button></section>
      </div>
      <section class="psychology-card"><h3>二阶欲望</h3><p>认同与抵触可以同时保留；归档只影响后续参考，不修改底层驱力或目标。</p><div v-if="!snapshot.secondOrderDesires.length">尚无明确表达的二阶倾向。</div><div v-for="item in snapshot.secondOrderDesires" :key="item.id" class="psychology-row"><span><strong>{{labels[item.attitude]||item.attitude}} · {{item.target_type}} / {{item.target_id||'未指定'}}</strong><small>{{item.reason_summary||'未提供理由'}} · 强度 {{percent(item.strength)}} · 可信度 {{percent(item.confidence)}} · 来源 {{item.source_event_ids_json}}</small></span><button class="text-btn" :disabled="busy" @click="changeDesire(item,item.status==='active'?'archive':'restore')">{{item.status==='active'?'归档':'恢复'}}</button></div></section>
      <section class="psychology-card"><h3>内部冲突与短期驱力参考</h3><p v-if="!snapshot.conflicts.length">当前没有有证据的实质冲突。</p><div v-for="item in snapshot.conflicts" :key="item.id" class="psychology-row"><span><strong>{{item.description}}</strong><small>可信度 {{percent(item.confidence)}} · 可考虑：{{item.suggestedOptions.join('、')}}</small></span></div><div v-for="(value,key) in snapshot.driveHints" :key="key" class="psychology-chip">{{key}}：{{value>=0?'+':''}}{{percent(value)}}（仅参考）</div><div v-for="entry in conflictHistory.slice(0,8)" :key="entry.id" class="psychology-row"><small>{{entry.created_at}} · {{entry.conflict_id}} · 意愿 {{entry.choice}} · {{entry.outcome_ref?'后续操作 '+entry.outcome_ref:'尚无操作结果'}}</small></div></section>
      <section class="psychology-card"><h3>驱力关联图</h3><p>关系最多传播两层。新关系先提出，再单独审阅；不会直接写入驱力 baseline。</p><div v-for="edge in edges" :key="edge.id" class="psychology-row"><span><strong>{{edge.source_type}}:{{edge.source_id}} → {{edge.target_type}}:{{edge.target_id}}</strong><small>{{edge.relation_type}} · 权重 {{percent(edge.weight)}} · {{edge.source}} · {{edge.status}} · {{edge.reason}}</small></span><span v-if="edge.status==='pending'"><button class="text-btn" :disabled="busy" @click="decide(edge,'approve')">批准</button><button class="text-btn" :disabled="busy" @click="decide(edge,'reject')">拒绝</button></span><button v-else-if="edge.status==='approved'" class="text-btn" :disabled="busy" @click="decide(edge,'disable')">停用</button><button v-else-if="edge.status==='disabled'" class="text-btn" :disabled="busy" @click="decide(edge,'enable')">启用</button></div><div class="psychology-edge-form"><label>来源类型<select v-model="proposal.sourceType"><option value="body_state">身体状态</option><option value="emotion">情绪</option><option value="drive">驱力</option></select></label><label>来源 ID<input v-model="proposal.sourceId"/></label><label>目标类型<select v-model="proposal.targetType"><option value="drive">驱力</option><option value="emotion">情绪</option><option value="body_state">身体状态</option></select></label><label>目标 ID<input v-model="proposal.targetId"/></label><label>关系<select v-model="proposal.relationType"><option value="amplify">增强</option><option value="suppress">抑制</option><option value="compete">竞争</option></select></label><label>权重<input v-model.number="proposal.weight" type="number" min="0" max="0.3" step="0.01"/></label><label>可信度<input v-model.number="proposal.confidence" type="number" min="0" max="1" step="0.05"/></label><label>原因<input v-model="proposal.reason" maxlength="500"/></label><button class="secondary-btn" :disabled="busy||!proposal.reason.trim()" @click="propose">提出关联</button></div></section>
      <section class="psychology-card"><h3>变化来源与模型预算</h3><div v-for="run in runs" :key="run.session_id" class="psychology-row"><span>会话 {{run.session_id.slice(0,8)}} · 情境评价 {{run.status}} · 输出 token {{run.token_used}}/{{run.token_cap}} <small v-if="run.error">{{run.error}}</small></span></div><div v-for="event in events" :key="event.id" class="psychology-row"><span><strong>{{event.kind}} · {{event.created_at}}</strong><small>来源 {{event.source_ref}} · {{event.event_id}}</small><pre v-if="selectedEvent===event.id">评价：{{JSON.stringify(event.appraisal,null,2)}}
关联影响：{{JSON.stringify(event.graph,null,2)}}</pre></span><button class="text-btn" @click="selectedEvent=selectedEvent===event.id?null:event.id">{{selectedEvent===event.id?'收起':'查看依据'}}</button></div></section>
      <section class="psychology-card"><h3>隔离模拟</h3><p>只计算内存轨迹，不写入正式角色。支持 100～500 次稳定性检查。</p><label>事件数<input v-model.number="simCount" type="number" min="1" max="500"/></label><button class="secondary-btn" :disabled="busy" @click="simulate">运行模拟</button><p v-if="trajectory.length">最后一步 {{trajectory[trajectory.length-1].step}} · 模拟疲劳 {{percent(trajectory[trajectory.length-1].body.fatigue)}} · 情绪倾向 {{percent(trajectory[trajectory.length-1].emotion.valence)}}</p></section>
    </template>
  </div>
</template>

<style scoped>
.psychology-panel{display:grid;gap:16px;padding:20px;color:inherit}.psychology-head{display:flex;justify-content:space-between;gap:18px;align-items:start}.psychology-head h2{margin:5px 0}.psychology-head p,.psychology-card p,.psychology-meta{opacity:.75;line-height:1.5}.psychology-notice{padding:9px 12px;border:1px solid #8178a2;border-radius:9px}.psychology-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(255px,1fr));gap:14px}.psychology-card{padding:16px;border:1px solid rgba(150,145,186,.25);border-radius:15px;background:rgba(150,145,186,.055)}.psychology-card h3{margin:0 0 10px}.psychology-meter{display:grid;grid-template-columns:1fr auto;gap:3px 12px;margin:8px 0}.psychology-meter progress{grid-column:1/3;width:100%;accent-color:#9f91e7}.psychology-chip{display:inline-block;padding:5px 9px;margin:3px;border:1px solid rgba(150,145,186,.3);border-radius:20px}.psychology-row{display:flex;justify-content:space-between;gap:10px;align-items:start;padding:8px 0;border-top:1px solid rgba(150,145,186,.18);overflow-wrap:anywhere}.psychology-row span:first-child{min-width:0}.psychology-row small{display:block;opacity:.7;margin-top:3px}.psychology-row pre{white-space:pre-wrap;overflow-wrap:anywhere}.psychology-edge-form{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:9px;margin-top:14px}.psychology-edge-form label{display:grid;gap:4px}.psychology-edge-form input,.psychology-edge-form select{min-width:0;width:100%}@media(max-width:600px){.psychology-panel{padding:12px}.psychology-head{display:block}}
</style>
