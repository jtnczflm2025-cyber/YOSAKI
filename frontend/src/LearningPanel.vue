<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

type Model = { id:number;name:string;enabled:boolean }
type Evidence = { id:string;feedback_id:string;subject:string;context_key:string;selection_kind:string;objective_outcome:string;enjoyment:number|null;interpretation:string;source:{feedbackId:string;operationIds:string[];endReason:string};created_at:string }
type Preference = { id:string;subject:string;context_key:string;estimate:number;confidence:number;status:string;version:number }
type Proposal = { id:string;preference_id:string;version:number;status:string;evidence_ids:string[];before:{estimate:number;confidence:number};after:{estimate:number;confidence:number;status:string;evidenceCount:number;positiveCount:number;negativeCount:number} }
type Memory = { id:string;subject:string;fact:string;interpretation:string;content:string;source_ids:string[];conflict_ids:string[];merged_ids:string[];status:string;version:number;confidence:number }
type Strategy = { id:string;name:string;description:string;condition_text:string;source_ids:string[];confidence:number;effectiveness:number;usage_count:number;success_count:number;failure_count:number;status:string;version:number }
type SubjectSuggestion = { id:string;source_ids:string[];subject:string;context_key:string;reason:string;status:string;version:number }
type Goal = { id:string;goal_id:string;feedback_id:string;summary:string;status:string;version:number;milestones:{description:string;operation_id:string}[];open_questions:string[];resume_hint:string;review_status:string }
type Overview = { newEvidence:number;evidence:Evidence[];preferences:Preference[];proposals:Proposal[];memories:Memory[];strategies:Strategy[];subjects:SubjectSuggestion[];goals:Goal[] }
type History = { id:number;action:string;before:{estimate:number;confidence:number};after:{estimate:number;confidence:number};evidence_ids:string[];reason:string;created_at:string;proposal_id:string }
const props = defineProps<{characterId:number;models:Model[]}>()
const data = ref<Overview|null>(null)
const busy = ref(false)
const notice = ref('')
const modelId = ref<number|null>(null)
const tokenCap = ref(1200)
const selected = ref<string|null>(null)
const timeline = ref<History[]>([])
const selectedEvidence = ref<string|null>(null)
const simulation = ref<{step:number;estimate:number;confidence:number}[]>([])
const estimateOverride = ref<Record<string,number|null>>({})
const confidenceOverride = ref<Record<string,number|null>>({})
const selectedPreference = computed(()=>data.value?.preferences.find(item=>item.id===selected.value))
const evidence = computed(()=>data.value?.evidence.find(item=>item.id===selectedEvidence.value))
const chart = computed(()=>{
  const points = [{estimate:0.5},...timeline.value.map(item=>item.after)]
  return points.map((item,index)=>`${20+index*480/Math.max(1,points.length-1)},${154-item.estimate*130}`).join(' ')
})
function base(){return `/api/desires/${props.characterId}/learning`}
function id(){return crypto.randomUUID()}
async function request<T>(path:string,method='GET',body?:unknown):Promise<T>{
  const response=await fetch(path,{method,credentials:'same-origin',headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)})
  if(!response.ok)throw new Error((await response.json().catch(()=>null))?.detail||`请求失败（${response.status}）`)
  return response.json()
}
async function refresh(){
  data.value=await request<Overview>(base())
  notice.value=''
  if(!modelId.value||!props.models.some(item=>item.id===modelId.value))modelId.value=props.models.find(item=>item.enabled)?.id??null
  if(selected.value&&!data.value.preferences.some(item=>item.id===selected.value)){selected.value=null;timeline.value=[]}
  if(selected.value)await loadTimeline(selected.value)
}
async function run(action:()=>Promise<unknown>,message:string){busy.value=true;notice.value='';try{await action();await refresh();notice.value=message}catch(error){notice.value=error instanceof Error?error.message:'操作失败'}finally{busy.value=false}}
async function loadTimeline(preferenceId:string){selected.value=preferenceId;timeline.value=await request<History[]>(`${base()}/preferences/${preferenceId}/timeline`)}
async function decidePreference(item:Proposal,approve:boolean){const estimate=estimateOverride.value[item.id],confidence=confidenceOverride.value[item.id];await run(()=>request(`${base()}/preferences/${item.id}/decision`,'POST',{schemaVersion:5,requestId:id(),expectedVersion:item.version,approve,adjustedEstimate:approve&&typeof estimate==='number'?estimate:null,adjustedConfidence:approve&&typeof confidence==='number'?confidence:null}),'偏好建议已处理')}
async function rollback(history:History){
  const item=data.value?.proposals.find(x=>x.id===history.proposal_id)
  const response=await request<{version:number}>(`${base()}/preferences/${history.proposal_id}/revision`).catch(()=>null)
  const version=response?.version??item?.version
  if(!version){notice.value='请刷新后查看可撤销的版本';return}
  await run(()=>request(`${base()}/preferences/${history.proposal_id}/rollback`,'POST',{schemaVersion:5,requestId:id(),expectedVersion:version}),'偏好更新已撤销并重新计算')
}
async function memoryAction(item:Memory,action:'accept'|'reject'|'archive'){await run(()=>request(`${base()}/memories/${item.id}/decision`,'POST',{schemaVersion:5,requestId:id(),expectedVersion:item.version,action}),'记忆候选已处理')}
async function merge(item:Memory){const other=data.value?.memories.find(x=>x.id!==item.id&&x.subject===item.subject&&x.status==='candidate');if(!other)return;await run(()=>request(`${base()}/memories/${item.id}/merge`,'POST',{schemaVersion:5,requestId:id(),expectedVersion:item.version,otherId:other.id,otherExpectedVersion:other.version}),'相似候选已合并，来源仍保留')}
async function strategyAction(item:Strategy,action:'approve'|'reject'|'pause'|'resume'|'retire'|'evaluate'){await run(()=>request(`${base()}/strategies/${item.id}/decision`,'POST',{schemaVersion:5,requestId:id(),expectedVersion:item.version,action}),'策略已更新')}
async function subjectAction(item:SubjectSuggestion,action:'approve'|'reject'){await run(()=>request(`${base()}/subjects/${item.id}/decision`,'POST',{schemaVersion:5,requestId:id(),expectedVersion:item.version,action}),'主题归类建议已处理')}
async function goalAction(item:Goal,action:'accept'|'reject'){await run(()=>request(`${base()}/goals/${item.id}/decision`,'POST',{schemaVersion:5,requestId:id(),expectedVersion:item.version,action}),'目标进度参考已处理')}
async function consolidate(){if(!modelId.value)return;await run(()=>request(`${base()}/consolidate`,'POST',{schemaVersion:5,requestId:id(),modelProfileId:modelId.value,maxOutputTokens:tokenCap.value}),'整理完成；新内容仍需审阅')}
async function simulate(){await run(async()=>{const result=await request<{trajectory:{step:number;estimate:number;confidence:number}[]}>(`${base()}/simulate`,'POST',Array.from({length:100},(_,i)=>i%2?0.8:0.2));simulation.value=result.trajectory},'100 次隔离模拟完成，正式状态未变化')}
watch(()=>props.characterId,()=>{data.value=null;selected.value=null;timeline.value=[];void refresh().catch(error=>notice.value=String(error))})
onMounted(()=>{void refresh().catch(error=>notice.value=String(error))})
</script>

<template>
  <div class="learning-panel">
    <div class="window-section-heading"><div><span class="section-eyebrow">LONG-TERM LEARNING</span><h2>长期学习</h2><p>经历与判断分开保存。正式偏好、策略及记忆需要审阅；任何结果都只是下次选择的参考。</p></div><button class="secondary-btn" :disabled="busy" @click="refresh">刷新</button></div>
    <p class="learning-notice" role="status">{{notice}}</p>
    <p v-if="!data">{{notice?'请先处理上方提示。':'正在读取学习记录…'}}</p>
    <template v-else>
      <section class="learning-section"><h3>增量证据</h3><p>本次新归集 {{data.newEvidence}} 条；重复来源不会再次入账。模拟自述与客观结果分别标记。</p><article v-for="item in data.evidence.slice(0,12)" :key="item.id" class="learning-card"><strong>{{item.subject}}</strong><span>{{item.context_key}} · {{item.selection_kind==='voluntary'?'主动选择':'选择来源不明'}} · 操作结果 {{item.objective_outcome}} · 体验 {{item.enjoyment===null?'未知':Math.round(item.enjoyment*100)+'/100'}}</span><button class="text-btn" @click="selectedEvidence=selectedEvidence===item.id?null:item.id">{{selectedEvidence===item.id?'收起来源':'查看来源'}}</button><div v-if="selectedEvidence===item.id"><p>模拟解释：{{item.interpretation||'无'}}</p><p>Phase 4 反馈 {{item.feedback_id}} · 操作 {{item.source.operationIds.join('、')||'无'}} · 结束原因 {{item.source.endReason}}</p></div></article><p v-if="!data.evidence.length">尚无已确认的自由活动反馈。</p></section>
      <section class="learning-section"><h3>长期偏好与变化时间线</h3><p>估计值表示当前倾向；置信度表示证据是否充分。两者不会自动修改驱力基线。</p><article v-for="item in data.proposals" :key="item.id" class="learning-card"><strong>{{data.preferences.find(x=>x.id===item.preference_id)?.subject}} · 待审阅</strong><span>估计 {{Math.round(item.before.estimate*100)}} → {{Math.round(item.after.estimate*100)}} · 置信度 {{Math.round(item.before.confidence*100)}} → {{Math.round(item.after.confidence*100)}} · 正/反证据 {{item.after.positiveCount}}/{{item.after.negativeCount}}</span><p>规则 learning-v1；来源 {{item.evidence_ids.join('、')}}</p><div class="learning-actions"><label>修正估计（可选，0～1）<input v-model.number="estimateOverride[item.id]" type="number" min="0" max="1" step="0.01"/></label><label>修正置信度（可选，0～1）<input v-model.number="confidenceOverride[item.id]" type="number" min="0" max="1" step="0.01"/></label><button class="primary-btn" :disabled="busy" @click="decidePreference(item,true)">批准</button><button class="text-btn" :disabled="busy" @click="decidePreference(item,false)">拒绝</button></div></article><article v-for="item in data.preferences" :key="item.id" class="learning-card"><strong>{{item.subject}} · {{item.context_key}}</strong><span>估计 {{Math.round(item.estimate*100)}}/100 · 置信度 {{Math.round(item.confidence*100)}}/100 · {{item.status}}</span><button class="text-btn" @click="loadTimeline(item.id)">查看变化</button></article><div v-if="selectedPreference" class="learning-chart"><h4>{{selectedPreference.subject}} · 历史变化</h4><svg viewBox="0 0 520 180" role="img" :aria-label="`${selectedPreference.subject}偏好变化曲线`"><path d="M20 24H500 M20 89H500 M20 154H500" stroke="currentColor" opacity=".16"/><text x="0" y="27" fill="currentColor" font-size="10">1</text><text x="0" y="92" fill="currentColor" font-size="10">.5</text><text x="0" y="157" fill="currentColor" font-size="10">0</text><polyline :points="chart" fill="none" stroke="#80b7ff" stroke-width="3"/><circle v-for="(point,index) in [{estimate:0.5},...timeline.map(x=>x.after)]" :key="index" :cx="20+index*480/Math.max(1,timeline.length)" :cy="154-point.estimate*130" r="4" fill="#80b7ff"/></svg><p v-for="entry in timeline" :key="entry.id">{{entry.created_at}} · {{entry.action}} · {{Math.round(entry.before.estimate*100)}} → {{Math.round(entry.after.estimate*100)}} · 来源 <button v-for="source in entry.evidence_ids" :key="source" class="text-btn" @click="selectedEvidence=source">{{source}}</button><button v-if="entry.action==='approve' && !timeline.some(x=>x.action==='rollback' && x.proposal_id===entry.proposal_id)" class="text-btn" @click="rollback(entry)">撤销此更新</button></p><p v-if="evidence">所选来源：{{evidence.subject}} · {{evidence.objective_outcome}} · 体验 {{evidence.enjoyment===null?'未知':Math.round(evidence.enjoyment*100)+'/100'}} · {{evidence.interpretation||'无模拟解释'}}</p><p v-if="!timeline.length">尚无已批准的变化。</p></div></section>
      <section class="learning-section"><h3>记忆整合</h3><p>事实与解释分开；合并保留来源，冲突不会被覆盖。</p><article v-for="item in data.memories" :key="item.id" class="learning-card"><strong>{{item.subject}} · {{item.status}}</strong><p>事实：{{item.fact||'未形成可核验事实'}}</p><p>模拟解释：{{item.interpretation||'无'}}</p><span>置信度 {{Math.round(item.confidence*100)}}/100 · 来源 {{item.source_ids.join('、')}} <template v-if="item.conflict_ids.length"> · 可能冲突 {{item.conflict_ids.join('、')}}</template></span><div v-if="item.status==='candidate'" class="learning-actions"><button class="secondary-btn" :disabled="busy" @click="memoryAction(item,'accept')">接受到现有记忆</button><button class="text-btn" :disabled="busy" @click="memoryAction(item,'reject')">拒绝</button><button v-if="data.memories.some(x=>x.id!==item.id&&x.subject===item.subject&&x.status==='candidate')" class="text-btn" :disabled="busy" @click="merge(item)">合并同主题候选</button></div><button v-if="item.status==='accepted'" class="text-btn" :disabled="busy" @click="memoryAction(item,'archive')">归档</button></article></section>
      <section class="learning-section"><h3>自我调节策略</h3><p>只有明确记录采用的策略才统计效果；策略只是可选方法。</p><article v-for="item in data.strategies" :key="item.id" class="learning-card"><strong>{{item.name}} · {{item.status}}</strong><p>{{item.description}}</p><span>适用条件：{{item.condition_text}} · 使用 {{item.usage_count}} · 成功/失败 {{item.success_count}}/{{item.failure_count}} · 效果估计 {{Math.round(item.effectiveness*100)}}/100 · 置信度 {{Math.round(item.confidence*100)}}/100</span><div class="learning-actions"><button v-if="item.status==='candidate'" class="secondary-btn" :disabled="busy" @click="strategyAction(item,'approve')">批准</button><button v-if="item.status==='candidate'" class="text-btn" :disabled="busy" @click="strategyAction(item,'reject')">拒绝</button><button v-if="item.status==='available'" class="text-btn" :disabled="busy" @click="strategyAction(item,'evaluate')">审阅并更新效果</button><button v-if="item.status==='available'" class="text-btn" :disabled="busy" @click="strategyAction(item,'pause')">暂停推荐</button><button v-if="item.status==='paused'" class="text-btn" :disabled="busy" @click="strategyAction(item,'resume')">恢复</button></div></article><p v-if="!data.strategies.length">尚无策略候选；可在下方手动整理经历。</p></section>
      <section class="learning-section"><h3>主题归类建议</h3><p>模型提出的同义主题需先审阅；已有正式偏好贡献的证据要先撤销对应更新。</p><article v-for="item in data.subjects.filter(x=>x.status==='candidate')" :key="item.id" class="learning-card"><strong>{{item.subject}} · {{item.context_key}}</strong><p>{{item.reason||'未提供归类理由'}}</p><span>来源 {{item.source_ids.join('、')}}</span><div class="learning-actions"><button class="secondary-btn" :disabled="busy" @click="subjectAction(item,'approve')">采用归类</button><button class="text-btn" :disabled="busy" @click="subjectAction(item,'reject')">拒绝</button></div></article><p v-if="!data.subjects.some(x=>x.status==='candidate')">暂无待审归类。</p></section>
      <section class="learning-section"><h3>长期目标连续性</h3><article v-for="item in data.goals" :key="item.id" class="learning-card"><strong>目标 {{item.goal_id}} · {{item.status}} · {{item.review_status}}</strong><p>{{item.summary}}</p><p v-if="item.milestones.length">已核验步骤：{{item.milestones.map(x=>x.description).join('、')}}</p><p v-if="item.resume_hint">恢复位置：{{item.resume_hint}}</p><span>反馈来源 {{item.feedback_id}}；进度记录不代表目标完成。</span><div v-if="item.status==='pending'" class="learning-actions"><button class="secondary-btn" :disabled="busy" @click="goalAction(item,'accept')">保存进度参考</button><button class="text-btn" :disabled="busy" @click="goalAction(item,'reject')">忽略</button></div></article><p v-if="!data.goals.length">暂无目标进度记录。</p></section>
      <section class="learning-section"><h3>手动整理与隔离模拟</h3><p>整理最多调用一次所选模型。模型只提出候选，不自动批准学习结果。</p><div class="learning-actions"><label>模型<select v-model="modelId"><option :value="null">选择模型</option><option v-for="model in models.filter(x=>x.enabled)" :key="model.id" :value="model.id">{{model.name}}</option></select></label><label>输出 token 上限<input v-model.number="tokenCap" type="number" min="128" max="4096"/></label><button class="secondary-btn" :disabled="busy||!modelId" @click="consolidate">整理候选</button><button class="text-btn" :disabled="busy" @click="simulate">100 次隔离模拟</button></div><p v-if="simulation.length">模拟末次：估计 {{Math.round(simulation[simulation.length-1].estimate*100)}}/100 · 置信度 {{Math.round(simulation[simulation.length-1].confidence*100)}}/100；正式状态未变。</p></section>
    </template>
  </div>
</template>

<style scoped>
.learning-panel{height:100%;overflow:auto;padding:22px;display:grid;align-content:start;gap:16px}.learning-section{display:grid;gap:10px;padding:16px;border:1px solid var(--ui-line-soft);border-radius:14px;background:var(--ui-raised)}.learning-section h3,.learning-chart h4{margin:0}.learning-section p{margin:0;line-height:1.5}.learning-card{display:grid;gap:7px;padding:10px 0;border-top:1px solid var(--ui-line-soft);overflow-wrap:anywhere}.learning-card span{font-size:13px;opacity:1}.learning-actions{display:flex;gap:8px;flex-wrap:wrap;align-items:end}.learning-actions label{display:grid;gap:4px}.learning-actions input,.learning-actions select{width:170px}.learning-chart{display:grid;gap:8px;border:1px solid var(--ui-line-soft);border-radius:10px;padding:12px}.learning-chart svg{width:100%;max-width:600px;height:auto}.learning-notice{min-height:1.2em}
</style>
