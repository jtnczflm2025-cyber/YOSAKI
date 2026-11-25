<script setup lang="ts">
import { ref, watch, computed, onMounted, onBeforeUnmount } from 'vue'
const props = defineProps<{ characterId: number; entryId: string }>()
const emit = defineEmits<{ saved: [] }>()
type Field = { value: unknown; token: string; lifecycleStatus: string }
type RecordView = { entry: { entryId: string; versionId: string; qualifications?:{p2Projection:boolean}; fields: Record<string, Field>; timeEvidence: {occurredAt?:string;occurredDate?:string;rawTime?:string;precision:string}[] }; raw: {kind:string;id:string;speaker:string;text:string}[]; narrative: null | {id:string;body:string;input_hash:string;output_hash:string;status:string;review_status:string} }
const record = ref<RecordView|null>(null)
const title = ref(''), synopsis = ref(''), dialogue = ref('')
const message = ref(''), error = ref(''), busy = ref(false), dirty = ref(false), rawOpen = ref(false)
const correctionDate=ref(''), correctionReason=ref('')
const editing = ref(false)
const labelsOpen=ref(false)
const keywordSuggestions=ref<any>(null), selectedSuggestions=ref<string[]>([])
async function suggestKeywords(){
  busy.value=true;error.value='';feedbackArea.value='labels'
  try {const result=await request(`${url()}/keyword-suggestions?characterId=${props.characterId}`)
    if(result.versionId!==record.value?.entry.versionId) throw new Error('内容版本已变化，请重新打开后提取建议。')
    keywordSuggestions.value=result;selectedSuggestions.value=[]
  } catch(e) {error.value=e instanceof Error?e.message:'建议读取失败'} finally {busy.value=false}
}
function applyKeywordSuggestions(){
  if(!keywordSuggestions.value || keywordSuggestions.value.versionId!==record.value?.entry.versionId)return
  keywordText.value=[...new Set([...keywordWords(keywordText.value),...selectedSuggestions.value])].join('、')
  message.value='所选建议已加入编辑框；请检查后保存，再独立确认。';feedbackArea.value='labels'
}
type FeedbackArea='text'|'narrative'|'synopsis'|'labels'|'review'|'publish'|'date'|'curation'
const feedbackArea=ref<FeedbackArea>('text')
const generationEnabled=ref(false), generationModel=ref(0), models=ref<{id:number;name:string}[]>([])
const outputTokens=ref(6144), generationMaterial=ref<any>(null), activeGeneration=ref('')
const synopsisTaskStatus=ref<any>(null)
const generationCap=computed(()=>outputTokens.value===8192 ? 147456 : 122880)
onMounted(async()=>{ try {generationEnabled.value=(await request('/api/memory-v2/workspace/generation-status')).enabled;const all=await request('/api/models');models.value=(Array.isArray(all)?all:all.items||[]).filter((m:any)=>m.enabled&&m.protocol==='openai_responses'&&m.model_name==='gpt-6.1-sol');generationModel.value=models.value[0]?.id||0} catch {generationEnabled.value=false} })
const confirmation = ref('')
let resolveConfirmation: ((answer:boolean)=>void)|null=null
function askConfirmation(text:string):Promise<boolean> {
  if(resolveConfirmation) resolveConfirmation(false)
  confirmation.value=text
  return new Promise(resolve=>{resolveConfirmation=resolve})
}
function answerConfirmation(answer:boolean) {
  const resolve=resolveConfirmation; resolveConfirmation=null;confirmation.value='';resolve?.(answer)
}
onBeforeUnmount(()=>answerConfirmation(false))
const choicesDirty=ref(false)
function keywordWords(value:string):string[]{return [...new Set(value.split(/[、,，\r\n]/u).map(x=>x.trim()).filter(Boolean))]}
function labelKey(categories:string[],tags:string[],words:string[]){return JSON.stringify([[...new Set(categories)].sort(),[...new Set(tags)].sort(),[...new Set(words)].sort()])}
const savedKeywordDisplay=computed(()=>{const v=record.value?.entry.fields.keywords.value;return Array.isArray(v)?v.map((x:{text:string})=>x.text).join('、') || '尚未保存关键词':'尚未保存关键词'})
const labelBaseline=ref('')
const labelsDirty=computed(()=>!!record.value && labelKey(categoryIds.value,tagIds.value,keywordWords(keywordText.value))!==labelBaseline.value)
const hasUnsaved=computed(()=>dirty.value || labelsDirty.value || choicesDirty.value)
const dialogueConfirmed=computed(()=>!!record.value?.narrative && record.value.entry.fields.narrative.value===record.value.narrative.id && record.value.entry.fields.narrative.lifecycleStatus==='available')
const canPublish=computed(()=>!!record.value && record.value.entry.fields.title.lifecycleStatus==='available' && (!record.value.narrative || dialogueConfirmed.value) && (!record.value.entry.fields.synopsis.value || record.value.entry.fields.synopsis.lifecycleStatus==='available'))
const sourceDates=computed(()=>[...new Set((record.value?.entry.timeEvidence || []).map(t=>{
  const value=t.occurredDate || t.occurredAt || t.rawTime
  if(!value) return '发生时间未知'
  return t.precision==='instant' || t.precision==='day' ? value : `${value}（未核实）`
}))].join(' — '))
const labelFields = ['categories', 'tags', 'keywords']
const hasSavedLabels = computed(() => labelFields.some(name => {
  const value = record.value?.entry.fields[name]?.value
  return Array.isArray(value) && value.length > 0
}))
const titleConfirmed=computed(()=>record.value?.entry.fields.title.lifecycleStatus==='available')
const synopsisConfirmed=computed(()=>!!synopsis.value && record.value?.entry.fields.synopsis.lifecycleStatus==='available')
const labelsConfirmed=computed(()=>hasSavedLabels.value && labelFields.filter(name=>{
  const value=record.value?.entry.fields[name]?.value
  return Array.isArray(value)&&value.length>0
}).every(name=>record.value?.entry.fields[name]?.lifecycleStatus==='available'))
// Existing server current-version qualification; this display grants no permission.
const published=computed(()=>record.value?.entry.qualifications?.p2Projection===true)
const publishReasons=computed(()=>{
  const reasons:string[]=[]
  if(busy.value) reasons.push('正在处理，请等待当前操作结束。')
  if(hasUnsaved.value) reasons.push('有未保存的修改，请先保存或取消。')
  if(record.value && !titleConfirmed.value) reasons.push('标题尚未确认。')
  if(record.value?.narrative && !dialogueConfirmed.value) reasons.push('当前精修记录尚未确认并关联。')
  if(record.value?.entry.fields.synopsis.value && !synopsisConfirmed.value) reasons.push('当前梗概尚未确认。')
  return reasons
})
const operationHint=computed(()=>busy.value?'正在处理，请等待当前操作结束。':hasUnsaved.value?'有未保存的修改；保存或取消后再确认、生成或发布。':'')
function leave(event:BeforeUnloadEvent) { if(hasUnsaved.value) {event.preventDefault();event.returnValue=''} }
onMounted(()=>window.addEventListener('beforeunload',leave))
onBeforeUnmount(()=>window.removeEventListener('beforeunload',leave))
const choices = ref({ revision:0, installed:false, important:false, foundational:false, permanent:false, fixed:false, reason:'' })
const vocabulary=ref<{vocabulary_id:string;kind:string;name:string;dimension:string|null}[]>([])
const categoryIds=ref<string[]>([]), tagIds=ref<string[]>([]), keywordText=ref(''), labelSource=ref(0)
let epoch = 0
const url = () => `/api/memory-v2/workspace/entries/${encodeURIComponent(props.entryId)}`
async function request(path:string, method='GET', body?:unknown) {
  const response = await fetch(path, {method, credentials:'same-origin', headers: body ? {'Content-Type':'application/json'} : {}, body: body ? JSON.stringify(body) : undefined})
  if (!response.ok) {
    const detail=await response.json().catch(()=>({}))
    const reason=String(detail.detail || '')
    const errors:Record<string,string>={version_conflict:'另一窗口已更新这条记忆，当前编辑已保留。请先复制要保留的修改，再重新打开核对最新版本。',review_snapshot_changed:'保存版本已变化，未确认任何旧内容；请重新读取当前版本。',literal_keyword_not_in_source:'有关键词未逐字出现在当前来源中，未保存；请核对原文，编辑已保留。',keyword_invalid:'关键词不能为空且每个不得超过80字符；编辑已保留。',vocabulary_unavailable:'所选分类或标签已停用或不可用，未保存；请核对词库，编辑已保留。',vocabulary_not_found:'所选词库项目已不存在，未保存；编辑已保留。',source_outside_entry:'所选分类标签依据已不属于当前条目，未保存；请重新打开核对。',duplicate_label:'分类或标签重复，未保存；编辑已保留。',synopsis_real_scope_over_limit:'完整来源超出本次单批容量；未截断或发送，请先按完整经历边界整理。',synopsis_input_over_limit:'完整来源包装超出容量，未发送。',synopsis_diagnostic_sink_required:'公开响应留存尚未配置，未发送。',paid_generation_not_enabled:'模型加工尚未开启，未发送。',synopsis_input_token_limit:'实际输入计数超限，未生成。',synopsis_trial_not_claimable:'任务已停止、未知或正在执行，不会再次派发。',semantic_scope_unknown:'同一范围有未明请求，预留保留，不会补发。',chronicle_daily_rates_verification_required:'需先核对当日费率，未发送。'}
    throw new Error(errors[reason] || (response.status === 409 ? '内容、来源或任务条件已变化；原有内容保留，请重新打开检查状态。' : response.status === 401 ? '请重新登录。' : '操作未完成，请查看服务状态。'))
  }
  return await response.json()
}
async function load(preserveExtras=false) {
  const edits=preserveExtras ? {labels:labelsDirty.value,choices:choicesDirty.value,categories:[...categoryIds.value],tags:[...tagIds.value],words:keywordText.value,source:labelSource.value,flags:{...choices.value}} : null
  answerConfirmation(false)
  const ticket = ++epoch
  keywordSuggestions.value=null;selectedSuggestions.value=[];record.value = null; error.value=''; dirty.value=false; choicesDirty.value=false
  try {
    const [view, flags, words, task] = await Promise.all([request(`${url()}?characterId=${props.characterId}`),request(`${url()}/curation?characterId=${props.characterId}`),request(`/api/memory-v2/workspace/vocabulary?characterId=${props.characterId}`),request(`${url()}/synopsis-status?characterId=${props.characterId}`)])
    if(ticket !== epoch) return
    record.value=view; synopsisTaskStatus.value=task; editing.value=false; title.value=String(view.entry.fields.title.value ?? '')
    synopsis.value=String(view.entry.fields.synopsis.value ?? ''); dialogue.value=view.narrative?.body ?? ''; choices.value=flags
    vocabulary.value=words.items
    categoryIds.value=(view.entry.fields.categories.value || []).map((x:{id:string})=>x.id)
    tagIds.value=(view.entry.fields.tags.value || []).map((x:{id:string})=>x.id)
    keywordText.value=(view.entry.fields.keywords.value || []).map((x:{text:string})=>x.text).join('、')
    labelBaseline.value=labelKey(categoryIds.value,tagIds.value,keywordWords(keywordText.value))
    if(edits?.labels) { categoryIds.value=edits.categories;tagIds.value=edits.tags;keywordText.value=edits.words;labelSource.value=edits.source }
    if(edits?.choices) { choices.value=edits.flags;choicesDirty.value=true }
  } catch(e) { if(ticket===epoch) error.value=String((e as Error).message) }
}
async function act(action:()=>Promise<void>, success:string, area:FeedbackArea='text') {
  if(busy.value) return
  feedbackArea.value=area
  busy.value=true; error.value='';message.value=''
  try { await action(); message.value=success; emit('saved') } catch(e) { error.value=(e as Error).message } finally {busy.value=false}
}
async function saveText() {
  if(!record.value) return
  const current=record.value
  const savedArea:FeedbackArea=dialogue.value!==(current.narrative?.body ?? '')?'narrative':synopsis.value!==(current.entry.fields.synopsis.value ?? '')?'synopsis':'text'
  await act(async()=>{
    if(!dialogue.value.trim() && current.narrative?.body) throw new Error('精修记录不能为空；原有记录未删除，请保留正文或取消修改。')
    const changes:Record<string,string>={}
    if(title.value !== current.entry.fields.title.value) changes.title=title.value
    if(synopsis.value !== (current.entry.fields.synopsis.value ?? '')) changes.synopsis=synopsis.value
    if(Object.keys(changes).length) {
      const d=await request(`/api/memory-v2/entry-drafts/${props.entryId}/revisions`,'POST',{characterId:props.characterId,expectedVersion:current.entry.versionId,changes})
      current.entry=d
    }
    if(dialogue.value && dialogue.value !== (current.narrative?.body ?? '')) {
      await request(`${url()}/narrative`,'POST',{characterId:props.characterId,expectedVersion:current.entry.versionId,expectedNarrativeVersionId:current.narrative?.id ?? null,body:dialogue.value})
    }
    await load(true)
  },'修改已保存并重新读取；修改过的内容需重新确认，其余状态见各分区。',savedArea)
}
async function cancelEdit() { if(!dirty.value || await askConfirmation('放弃未保存的文字修改？')) void load(true) }
async function refine() {
  if(!record.value || dirty.value) return
  const current=record.value
  if(current.narrative && !await askConfirmation('按原始来源重新整理为一份待审精修？当前精修保留在历史中，新版本需要重新确认；梗概不会被替换。')) return
  await act(async()=>{await request(`${url()}/refine`,'POST',{characterId:props.characterId,expectedVersion:current.entry.versionId,expectedNarrativeVersionId:current.narrative?.id ?? null});await load(true)},'已按消息顺序生成精修草稿；没有生成或覆盖梗概。','narrative')
}
async function generateSynopsis() {
  if(!record.value || hasUnsaved.value || !generationModel.value) return
  try {generationMaterial.value=await request(`${url()}/synopsis-material?characterId=${props.characterId}&outputTokens=${outputTokens.value}`)}
  catch(e){error.value=(e as Error).message;return}
  const material=generationMaterial.value, modelName=models.value.find(m=>m.id===generationModel.value)?.name || ''
  if(!await askConfirmation(`发送本条完整的 ${material.sourceCount} 条原始对话及必要来源标识（当前来源包装 ${material.inputCharacters} 字符，不含其余指令与Schema），账户档案：${modelName}，模型 gpt-6.1-sol Standard／medium，store:false。最多一次计数＋一次流式生成；输入最多8192 token，总输出最多${outputTokens.value} token（含推理），梗概正文仍最多3000字符。费用硬上限 USD ${(generationCap.value/1000000).toFixed(6)}；不重试，不确认或发布。是否批准本次材料外发和费用？`)) return
  await act(async()=>{
    const p=await request(`${url()}/synopsis-plan`,'POST',{characterId:props.characterId,expectedVersion:record.value!.entry.versionId,modelId:generationModel.value,approveMaxMicrousd:generationCap.value,generationVersion:'stream-v4',outputTokens:outputTokens.value,materialHash:material.scopeHash})
    activeGeneration.value=p.jobId
    message.value='正在流式接收；读取等待上限180秒。取消会阻止候选提交，已发生费用仍结算。'
    const result=await request(`/api/memory-v2/workspace/synopsis-jobs/${p.jobId}/execute?characterId=${props.characterId}`,'POST')
    activeGeneration.value=''
    await load(true)
    if(result.status!=='completed') throw new Error(`生成已停止（${result.status==='unknown'?'结果或用量未知，原预留保留':'未形成有效候选'}）。可靠用量照常结算；不会自动重试。`)
  },'梗概已保存为待审版本。请阅读后决定是否确认。','synopsis')
}
async function cancelGeneration(){
  if(!activeGeneration.value)return
  try{
    await request(`/api/memory-v2/workspace/synopsis-jobs/${activeGeneration.value}/cancel?characterId=${props.characterId}`,'POST')
    message.value='已记录取消意图；已发请求不视为免费，未知用量继续保留预留。'
  }catch(e){error.value=(e as Error).message}
}

async function prepareLocal() {
  if(!record.value || dirty.value) return
  await act(async()=>{
    const response=await request(`${url()}/prepare-local`,'POST',{characterId:props.characterId,expectedVersion:record.value!.entry.versionId})
    await load(true)
    if(response.results.some((r:{status?:string})=>r.status==='blocked')) throw new Error('整理已保守停止；现有人工内容保留。请查看开发详情中的任务状态。')
  },'已完成可用的本地整理；未配置的可选词库保持原样，没有调用模型或自动确认内容。','labels')
}
async function saveLabels() {
  if(!record.value || dirty.value) return
  await act(async()=>{
    const words=keywordWords(keywordText.value)
    if(words.length>32) throw new Error(`共有${words.length}个关键词，最多32个；请减少后保存。编辑内容已保留。`)
    const invalid=words.filter(word=>[...word].length>80 || !record.value!.raw.some(source=>source.text.includes(word)))
    if(invalid.length) throw new Error(`以下词项没有完整、逐字匹配当前原文，或超过80字符：${invalid.slice(0,5).join('、')}。请核对原文；编辑内容已保留。`)
    const saved=await request(`${url()}/labels`,'POST',{characterId:props.characterId,expectedVersion:record.value!.entry.versionId,categories:[...new Set(categoryIds.value)].sort(),tags:[...new Set(tagIds.value)].sort(),keywords:words,sourceOrdinal:labelSource.value})
    record.value!.entry=saved
    categoryIds.value=(saved.fields.categories.value || []).map((x:{id:string})=>x.id)
    tagIds.value=(saved.fields.tags.value || []).map((x:{id:string})=>x.id)
    keywordText.value=(saved.fields.keywords.value || []).map((x:{text:string})=>x.text).join('、')
    labelBaseline.value=labelKey(categoryIds.value,tagIds.value,keywordWords(keywordText.value))
    await load(true)
  },'分类、标签与原文关键词已保存并重新读取。未变内容保留原确认；自动整理不会覆盖手动修订。','labels')
}
const keywordEvidence=computed(()=>{
  const value=record.value?.entry.fields.keywords.value
  if(!Array.isArray(value))return []
  return value.map((word:any)=>({text:String(word.text),points:(word.evidence || []).map((point:any)=>{
    const ordinal=record.value!.raw.findIndex(source=>source.kind===point.kind && String(source.id)===String(point.id))
    if(ordinal<0)return {valid:false}
    const source=record.value!.raw[ordinal], chars=[...source.text]
    const valid=Number.isInteger(point.start)&&Number.isInteger(point.end)&&point.start>=0&&point.end<=chars.length&&point.start<point.end&&chars.slice(point.start,point.end).join('')===word.text
    return valid?{valid,ordinal:ordinal+1,speaker:source.speaker==='assistant'?'阿夜':'イキヤ',before:chars.slice(Math.max(0,point.start-24),point.start).join(''),quote:chars.slice(point.start,point.end).join(''),after:chars.slice(point.end,point.end+24).join('')}:{valid:false}
  })}))
})
function resetLabels(){
  if(!record.value)return
  categoryIds.value=(Array.isArray(record.value.entry.fields.categories.value) ? record.value.entry.fields.categories.value : []).map((x:{id:string})=>x.id)
  tagIds.value=(Array.isArray(record.value.entry.fields.tags.value) ? record.value.entry.fields.tags.value : []).map((x:{id:string})=>x.id)
  keywordText.value=(Array.isArray(record.value.entry.fields.keywords.value) ? record.value.entry.fields.keywords.value : []).map((x:{text:string})=>x.text).join('、')
  error.value='';message.value='已取消未保存的分类与关键词修改。';feedbackArea.value='labels'
}
async function confirmDialogue() {
  if(!record.value?.narrative || hasUnsaved.value) return
  const v=record.value.narrative
  if(!await askConfirmation('确认已阅读这份完整精修记录，内容符合原始对话，并关联到当前条目？此操作不会发布条目。')) return
  await act(async()=>{await request(`${url()}/confirm-narrative`,'POST',{characterId:props.characterId,expectedVersion:record.value!.entry.versionId,versionId:v.id,inputHash:v.input_hash,outputHash:v.output_hash});await load(true)},'精修记录已确认并关联。条目是否发布是独立操作。','narrative')
}
async function confirmFields(labels = false) {
  if(!record.value || hasUnsaved.value) return
  const names = labels ? labelFields : ['title', 'synopsis']
  const description = labels ? '分类、标签与关键词' : '标题与梗概'
  if(!await askConfirmation(`确认当前已保存的${description}准确？此操作不会发布条目。`)) return
  await act(async()=>{
    for(const name of names) {
      const f=record.value!.entry.fields[name]
      if(f.value && (!Array.isArray(f.value) || f.value.length)) {
        await request(`/api/memory-v2/entry-drafts/${props.entryId}/review`,'POST',{characterId:props.characterId,field:name,token:f.token})
      }
    }
    await load(true)
  },`已确认当前${description}。未自动发布。`,labels?'labels':'review')
}
async function publish() {
  if(!record.value || hasUnsaved.value || !canPublish.value) return
  const current=record.value
  if(!await askConfirmation('发布当前已确认的条目，使具备资格的内容可用于记忆检索？未确认内容不会因此取得资格。')) return
  await act(async()=>{await request(`/api/memory-v2/entry-drafts/${props.entryId}/publish`,'POST',{characterId:props.characterId,expectedVersion:current.entry.versionId,expectedNarrativeVersionId:current.narrative?.id ?? null});await load(true)},'发布操作已保存；检索仍按来源与各字段资格核验。','publish')
}
async function saveDate() {
  if(!record.value || hasUnsaved.value) return
  const current=record.value
  if(!await askConfirmation('将整条经历的发生日期设为 '+(correctionDate.value || '未知')+'？原始时间保留，条目须重新发布。')) return
  await act(async()=>{await request(`${url()}/date`,'POST',{characterId:props.characterId,expectedVersion:current.entry.versionId,occurredDate:correctionDate.value||null,reason:correctionReason.value});await load(true)},'日期修订已保存；原始记录未改动。','date')
}
async function saveChoices() {
  await act(async()=>{const c=choices.value; choices.value=await request(`${url()}/curation`,'POST',{characterId:props.characterId,expectedRevision:c.revision,important:c.important,foundational:c.foundational,permanent:c.permanent,fixed:c.fixed,reason:c.reason});choicesDirty.value=false},'记忆设置已保存。重要与奠基不会自动变成常驻。','curation')
}
watch(()=>[props.characterId,props.entryId],()=>{message.value='';void load()},{immediate:true})
defineExpose({dirty:hasUnsaved})
</script>

<template>
  <section class="chronicle-editor" aria-label="记忆条目编辑" :aria-busy="busy">
    <header class="editor-heading">
      <div><p class="editor-kicker">记忆整理</p><h2>整理这条记忆</h2><p class="editor-help">梗概与完整精修分别保存；内容确认与发布是独立操作。</p></div>
      <span v-if="record" class="state-badge" :class="published ? 'is-confirmed' : 'is-pending'">{{published ? '已发布' : '未发布'}}</span>
    </header>
    <p v-if="error" class="editor-feedback is-error" role="alert">{{error}}</p>
    <p v-if="message && feedbackArea==='text'" class="editor-feedback" role="status">{{message}}</p>
    <p v-if="busy" class="editor-progress" role="status">正在处理，请稍候。原有内容保留。</p>
    <div v-if="confirmation" class="confirmation" role="dialog" aria-modal="true" aria-label="确认操作">
      <h3>请确认本次操作</h3><p>{{confirmation}}</p>
      <div class="editor-actions"><button @click="answerConfirmation(false)">取消，保持原状</button><button class="c-primary" @click="answerConfirmation(true)">确认执行</button></div>
    </div>
    <div v-if="record" :inert="confirmation ? true : undefined">
      <nav class="editor-jumps" aria-label="条目阅读与操作分区">
        <a :href="`#synopsis-${entryId}`">单经历梗概</a><a :href="`#dialogue-${entryId}`">完整精修记录</a><a :href="`#review-${entryId}`">审核与发布</a><a :href="`#curation-${entryId}`">记忆保留设置</a>
      </nav>
      <section class="editor-section" aria-label="标题与日期">
        <div class="section-heading"><h3>标题与日期</h3><span class="state-badge" :class="titleConfirmed?'is-confirmed':'is-pending'">标题{{titleConfirmed?'已确认':'待确认'}}</span></div>
        <label v-if="editing">记忆标题<input v-model="title" @input="dirty=true" maxlength="200" :disabled="busy" /></label>
        <h4 v-else class="entry-title">{{title || '标题尚未填写'}}</h4>
        <p class="date-reading">来源日期：{{sourceDates || '发生时间未知'}}<span>不以导入时间代替发生时间。</span></p>
        <div class="editor-actions"><button v-if="!editing" :disabled="busy" @click="editing=true">修改文字</button><template v-else><button class="c-primary" :disabled="busy || !dirty" @click="saveText">保存修改</button><button :disabled="busy" @click="cancelEdit">取消修改</button><small>{{dirty?'文字有未保存修改':'尚未修改文字，无需保存。'}}</small></template></div>
        <details class="editor-disclosure"><summary>修正发生日期</summary><div class="disclosure-content"><p class="editor-help">明确修正整条经历的发生日期；留空表示未知。原始记录时间保留，修订后须重新发布。</p><div class="date-edit"><label>发生日期<input type="date" v-model="correctionDate" /></label><label>日期修正依据<input v-model="correctionReason" placeholder="填写修正依据" maxlength="1000" /></label></div><button :disabled="busy || hasUnsaved || !correctionReason.trim()" @click="saveDate">保存日期修订</button><p v-if="!correctionReason.trim()" class="editor-help">请填写日期修正依据后保存。</p><p v-if="feedbackArea==='date' && message" class="editor-feedback" role="status">{{message}}</p></div></details>
      </section>

      <section :id="`synopsis-${entryId}`" class="editor-section synopsis-section" aria-label="单经历梗概">
        <div class="section-heading"><h3>单经历梗概</h3><span class="state-badge" :class="synopsisConfirmed?'is-confirmed':'is-pending'">{{!synopsis?'未准备':synopsisConfirmed?'已确认':'待确认'}}</span></div>
        <p class="editor-help">对这一次经历适度概括；完整对话内容保留在下方精修记录中。</p>
        <label v-if="editing">梗概正文<textarea v-model="synopsis" @input="dirty=true" rows="7" maxlength="3000" :disabled="busy" placeholder="梗概尚未生成，可先保留为空。" /></label>
        <div v-else class="source-reading">{{synopsis || '梗概尚未生成。'}}</div>
        <details class="editor-disclosure"><summary>生成待审梗概与任务状态</summary><div class="disclosure-content">
          <template v-if="generationEnabled"><div class="generation-options"><label>梗概生成模型<select v-model="generationModel" :disabled="busy"><option v-for="m in models" :key="m.id" :value="m.id">{{m.name}}</option></select></label><label>梗概输出容量<select v-model.number="outputTokens" :disabled="busy"><option :value="6144">6144 总输出token · 最高USD0.122880</option><option :value="8192">8192 总输出token · 最高USD0.147456</option></select></label></div><p class="editor-help">发送本条完整原始对话，由程序唯一定位引文；总输出含推理，正文仍限3000字符。每次先展示材料，由您授权；不自动重试。</p><button class="c-primary" :disabled="busy || hasUnsaved || !generationModel" @click="generateSynopsis">生成待审梗概</button><p v-if="!generationModel" class="editor-help">尚无符合要求的生成模型档案。</p></template>
          <p v-else class="editor-help">模型加工尚未开启；完整精修与手动编辑可独立使用。</p>
          <button v-if="activeGeneration" @click="cancelGeneration">取消本次梗概生成</button>

          <details v-if="generationMaterial" class="editor-disclosure"><summary>本次生成材料</summary><div class="disclosure-content"><p>{{generationMaterial.sourceCount}}条完整来源 · 输入上限8192 token · 总输出{{generationMaterial.outputTokens}} token · 正文3000字符</p><p v-for="(source,index) in generationMaterial.sources" :key="source.id">{{index+1}} · {{source.speaker==='user'?'イキヤ':'阿夜'}} · {{source.characters}}字符</p></div></details>
        </div></details>
<p v-if="synopsisTaskStatus && synopsisTaskStatus.status!=='not_started'" class="task-reading">最近任务：{{({completed:'已完成，内容另行确认',unknown:'结果或用量未知，不会自动补发',failed:'已停止，未形成可用结果',cancelled:'已取消',queued:'等待执行',running:'正在处理',paused:'已暂停'} as Record<string,string>)[synopsisTaskStatus.status] || '已停止'}}<template v-if="synopsisTaskStatus.reserved_microusd"> · 保留预留 USD {{(synopsisTaskStatus.reserved_microusd/1000000).toFixed(6)}}</template>。</p>
        <p v-if="feedbackArea==='synopsis' && message" class="editor-feedback" role="status">{{message}}</p>
      </section>

      <section :id="`dialogue-${entryId}`" class="editor-section narrative-section" aria-label="完整精修记录">
        <div class="section-heading"><h3>完整精修记录</h3><span class="state-badge" :class="dialogueConfirmed?'is-confirmed':'is-pending'">{{dialogueConfirmed?'已确认并关联':record.narrative?'待确认':'未准备'}}</span></div>
        <p class="editor-help">按原消息顺序保留双方内容。精修记录与梗概独立，不以摘要替代全文。</p>
        <label v-if="editing">精修后的对话记录<textarea v-model="dialogue" @input="dirty=true" rows="20" :disabled="busy" placeholder="尚无精修正文。不会用梗概替代。" /></label>
        <div v-else class="source-reading">{{dialogue || '尚无精修正文。不会用梗概替代。'}}</div>
        <div class="editor-actions"><button :disabled="busy || dirty" @click="refine">按原顺序整理对话</button><button v-if="editing" class="c-primary" :disabled="busy || !dirty" @click="saveText">保存修改</button><button v-if="editing" :disabled="busy" @click="cancelEdit">取消修改</button></div>
        <p v-if="dirty" class="editor-help">文字尚未保存；保存或取消后才能重新整理。</p>
        <p class="editor-help">本地有限转换会保留无法确定的括号和指代；不会筛选事实或合并重复对话。您可在阅读时修改。</p>
        <p v-if="feedbackArea==='narrative' && message" class="editor-feedback" role="status">{{message}}</p>
        <details class="editor-disclosure raw-disclosure" :open="rawOpen" @toggle="rawOpen=($event.target as HTMLDetailsElement).open"><summary>查看原始对话 <span>{{record.raw.length}}条有序来源 · 仅供核对</span></summary><div class="disclosure-content"><article v-for="(row,i) in record.raw" :key="row.id" class="raw-message"><strong>{{i+1}} · {{row.speaker==='assistant'?'阿夜':'イキヤ'}}</strong><p class="source-reading">{{row.text}}</p></article></div></details>
      </section>

      <details class="editor-disclosure labels-disclosure" :open="labelsOpen" @toggle="labelsOpen=($event.target as HTMLDetailsElement).open"><summary>分类、标签与关键词 <span>{{hasSavedLabels ? labelsConfirmed?'已确认':'待确认' : '未设置'}}</span></summary><div class="disclosure-content">
        <p class="editor-help">使用既有词库与原文，不自动创建实体；这些设置不代替内容确认。</p><button :disabled="busy || dirty" @click="prepareLocal">一起整理可本地识别的项目</button>
        <label>此次分类／标签依据<select v-model.number="labelSource"><option v-for="(row,i) in record.raw" :key="row.id" :value="i">第 {{i+1}} 条 · {{row.speaker==='assistant'?'阿夜':'イキヤ'}}</option></select></label>
        <fieldset><legend>分类</legend><div class="label-choices"><label v-for="term in vocabulary.filter(t=>t.kind==='category')" :key="term.vocabulary_id"><input v-model="categoryIds" type="checkbox" :value="term.vocabulary_id" />{{term.name}}</label></div><p v-if="!vocabulary.some(t=>t.kind==='category')" class="editor-help">当前角色尚无已确认的分类词项。</p></fieldset>
        <fieldset><legend>标签</legend><div class="label-choices"><label v-for="term in vocabulary.filter(t=>t.kind==='tag')" :key="term.vocabulary_id"><input v-model="tagIds" type="checkbox" :value="term.vocabulary_id" />{{term.name}}</label></div><p v-if="!vocabulary.some(t=>t.kind==='tag')" class="editor-help">当前角色尚无已确认的标签词项。</p></fieldset>
        <section class="review-item"><h4>当前保存值与新建议</h4><p>{{savedKeywordDisplay}}</p>
          <button :disabled="busy || dirty" @click="suggestKeywords">重新提取待选建议（本地）</button>
          <template v-if="keywordSuggestions"><p class="editor-help">保存来源：{{keywordSuggestions.savedProvenance || '无'}} · {{keywordSuggestions.savedRuleVersion || '无版本'}}；建议：{{keywordSuggestions.ruleVersion}}</p>
          <p class="editor-help">{{keywordSuggestions.limitation}}</p><p v-if="!keywordSuggestions.candidates.length">未找到有把握的候选，可以保持为空或手动填写。</p>
          <label class="keyword-suggestion-option" v-for="item in keywordSuggestions.candidates" :key="item.text"><input type="checkbox" v-model="selectedSuggestions" :value="item.text" />{{item.text}}<small> — {{item.reason}}</small><p class="editor-help">原文上下文：{{item.context}}</p></label>
          <button :disabled="busy || !selectedSuggestions.length" @click="applyKeywordSuggestions">将选中建议加入编辑框</button><p class="editor-help">不会自动保存或确认；现有词项保留，您可在编辑框删除不需要的词。</p>
          </template></section>
        <label>原文关键词（顿号、逗号或换行分隔）<textarea class="keyword-editor" v-model="keywordText" rows="3" /></label><p class="editor-help">最多32个，每个最多80字符；重复词与分隔空白不算新修改。只保存能逐字回到原文的词项，不代表事实认定。</p><div class="editor-actions"><button :disabled="busy || dirty" @click="saveLabels">保存分类与关键词</button><button :disabled="busy || !labelsDirty" @click="resetLabels">取消分类与关键词修改</button><small>{{labelsDirty ? '分类或关键词有未保存修改。' : '与当前保存版本一致，无未保存修改。'}}</small></div>
        <details v-if="keywordEvidence.length" class="editor-disclosure"><summary>核对已保存关键词的来源</summary><div class="disclosure-content"><p class="editor-help">高亮为原文词项，前后是局部上下文；完整原文见原文核对区。这里只说明提及，不把否定、玩笑或他人转述认定成事实。未保存修改尚无来源绑定。</p><article v-for="word in keywordEvidence" :key="word.text"><strong>{{word.text}}</strong><p v-for="(point,index) in word.points" :key="index" class="keyword-source"><template v-if="point.valid">第{{point.ordinal}}条 · {{point.speaker}}：{{point.before}}<mark>{{point.quote}}</mark>{{point.after}}</template><template v-else>引用与当前来源不一致，请重新核对。</template></p></article></div></details>
        <p v-if="feedbackArea==='labels' && message" class="editor-feedback" role="status">{{message}}</p><p v-if="feedbackArea==='labels' && error" class="editor-feedback is-error" role="alert">{{error}}</p>
      </div></details>

      <section :id="`review-${entryId}`" class="editor-section review-section" aria-label="审核与发布">
        <div class="section-heading"><h3>审核与发布</h3><span class="state-badge" :class="published?'is-confirmed':'is-pending'">{{published?'已发布':'未发布'}}</span></div>
        <p class="editor-help">请分别确认已经阅读的内容。确认不会自动发布；修改后的内容会按当前版本重新核验。</p>
        <p v-if="operationHint" class="editor-warning" role="status">{{operationHint}}</p>
        <div class="review-grid">
          <div class="review-item"><h4>完整精修记录</h4><span class="state-badge" :class="dialogueConfirmed?'is-confirmed':'is-pending'">{{dialogueConfirmed?'已确认并关联':record.narrative?'待确认':'未准备'}}</span><p>确认当前完整精修并关联到条目。</p><button class="c-primary" :disabled="busy || hasUnsaved || !record.narrative" @click="confirmDialogue">确认这份精修记录</button><small v-if="!record.narrative">请先准备并保存精修记录。</small><p v-if="feedbackArea==='narrative' && message" class="editor-feedback" role="status">{{message}}</p></div>
          <div class="review-item"><h4>标题与梗概</h4><div class="review-states"><span class="state-badge" :class="titleConfirmed?'is-confirmed':'is-pending'">标题{{titleConfirmed?'已确认':'待确认'}}</span><span class="state-badge" :class="synopsisConfirmed?'is-confirmed':'is-pending'">梗概{{!synopsis?'未准备':synopsisConfirmed?'已确认':'待确认'}}</span></div><p>核对标题及已保存的单经历梗概。</p><button class="c-primary" :disabled="busy || hasUnsaved" @click="confirmFields()">确认标题与梗概</button><small v-if="!synopsis">梗概尚未准备；本次只确认已有内容。</small><p v-if="feedbackArea==='review' && message" class="editor-feedback" role="status">{{message}}</p></div>
          <div class="review-item"><h4>分类与检索词</h4><span class="state-badge" :class="labelsConfirmed?'is-confirmed':'is-pending'">{{hasSavedLabels ? labelsConfirmed?'已确认':'待确认' : '未设置'}}</span><p>分类、标签与关键词为可选项；填写后独立确认，未确认的词不取得检索资格。</p><div class="review-states"><span v-for="name in labelFields" :key="name" class="state-badge">{{({categories:'分类',tags:'标签',keywords:'关键词'} as Record<string,string>)[name]}}：{{!((record.entry.fields[name]?.value || []) as unknown[]).length ? '未设置（可选）' : record.entry.fields[name].lifecycleStatus==='available' ? '已确认' : record.entry.fields[name].lifecycleStatus==='stale' ? '已失效' : '待确认'}}</span></div><button :disabled="busy || hasUnsaved || !hasSavedLabels" @click="confirmFields(true)">确认分类、标签与关键词</button><small v-if="!hasSavedLabels">当前没有已保存的分类、标签或关键词；不影响现有发布条件。</small><p v-if="feedbackArea==='labels' && message" class="editor-feedback" role="status">{{message}}</p></div>
        </div>
        <div class="publish-area" aria-label="发布已确认内容">
          <div><h4>发布到可用记忆</h4><p>{{published?'条目已有已发布版本。新修改仍须按对应内容重新确认。':'保存和确认完成后，再决定是否发布。'}}</p></div>
          <button class="c-primary" :disabled="busy || hasUnsaved || !canPublish" @click="publish">发布已确认内容</button>
          <ul v-if="publishReasons.length" class="publish-reasons"><li v-for="reason in publishReasons" :key="reason">{{reason}}</li></ul><p v-else class="editor-help">当前已满足现有发布条件；发布仍会请您确认。</p>
          <p v-if="feedbackArea==='publish' && message" class="editor-feedback" role="status">{{message}}</p>
        </div>
      </section>

      <section :id="`curation-${entryId}`" class="editor-section" aria-label="记忆保留设置">
        <div class="section-heading"><h3>记忆保留设置</h3><span v-if="choicesDirty" class="state-badge is-pending">设置未保存</span></div>
        <p class="editor-help">重要和奠基是独立标记。常驻与固定参考优先带入已确认梗概，仍共用1024估算token／5块预算，完整精修按需展开。</p>
        <fieldset class="retention-settings" @change="choicesDirty=true" @input="choicesDirty=true" :disabled="busy || !choices.installed"><legend>选择这条记忆的保留方式</legend><div class="retention-grid">
          <label><input type="checkbox" v-model="choices.important" /><span><strong>重要</strong><small>独立标记，不自动常驻。</small></span></label>
          <label><input type="checkbox" v-model="choices.foundational" /><span><strong>奠基经历</strong><small>标记奠基性质，不自动常驻。</small></span></label>
          <label><input type="checkbox" v-model="choices.permanent" /><span><strong>常驻记忆</strong><small>优先保留已确认梗概，受预算限制。</small></span></label>
          <label><input type="checkbox" v-model="choices.fixed" /><span><strong>我固定的参考</strong><small>独立选择，与常驻合并去重。</small></span></label>
        </div><label>保留理由（可选）<input v-model="choices.reason" maxlength="1000" placeholder="说明为什么希望保留" /></label><div class="editor-actions"><button class="c-primary" @click="saveChoices">保存记忆设置</button></div></fieldset>
        <p v-if="!choices.installed" class="editor-warning">记忆设置的独立存储尚未安装；当前不能保存，不会自动迁移。</p><p class="editor-help">重要约定请先核对梗概中的条件，再决定是否保留。取消常驻时也请检查是否仍勾选固定参考。</p><p v-if="feedbackArea==='curation' && message" class="editor-feedback" role="status">{{message}}</p>
      </section>
      <details class="editor-disclosure developer-details"><summary>开发详情</summary><div class="disclosure-content"><pre>{{ {version:record.entry.versionId,narrativeStatus:record.narrative?.status,fieldStatus:Object.fromEntries(Object.entries(record.entry.fields).map(([k,v])=>[k,v.lifecycleStatus]))} }}</pre></div></details>
    </div>
    <p v-else-if="!error" role="status" class="editor-help">正在读取当前条目与来源…</p>
  </section>
</template>

<style scoped>
.chronicle-editor{max-width:960px;margin:24px auto;padding:clamp(16px,2.4vw,32px);color:var(--ui-ink);background:var(--ui-surface);border:1px solid var(--ui-line);border-radius:20px;line-height:1.65;box-shadow:var(--ui-shadow-small)}
.editor-heading,.section-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}.editor-heading h2{margin:0 0 8px;font-size:26px}.editor-kicker{font-size:13px;letter-spacing:.1em;color:var(--ui-accent);font-weight:700;margin:0 0 6px}.editor-help{color:var(--ui-muted);font-size:14px;line-height:1.75;margin:8px 0 16px}.editor-jumps{display:flex;flex-wrap:wrap;gap:8px;margin:20px 0}.editor-jumps a{display:flex;align-items:center;min-height:44px;padding:8px 13px;border:1px solid var(--ui-line);border-radius:10px;text-decoration:none;font-size:14px;background:var(--ui-raised)}
.editor-section{padding:24px;margin:20px 0;border:1px solid var(--ui-line);border-radius:16px;background:var(--ui-surface);scroll-margin-top:100px}.synopsis-section{background:var(--ui-raised);border-left:4px solid var(--ui-accent)}.narrative-section{border-top:3px solid var(--ui-line)}.section-heading{align-items:center;flex-wrap:wrap;margin-bottom:16px}.section-heading h3{font-size:21px;margin:0}.entry-title{font-size:24px;line-height:1.5;overflow-wrap:anywhere;margin:8px 0 12px}.date-reading{font-size:14px;color:var(--ui-muted);line-height:1.7}.date-reading span{display:block}.source-reading{max-width:860px;white-space:pre-wrap;overflow-wrap:anywhere;word-break:normal;font-size:17px;line-height:1.75;margin:16px 0 24px}.chronicle-editor label:not(:has(input[type=checkbox])){display:grid;gap:8px;margin:16px 0;font-size:14px;font-weight:600}.chronicle-editor :is(input:not([type=checkbox]),textarea,select){width:100%;min-width:0;box-sizing:border-box;border:1px solid var(--ui-line);border-radius:10px;padding:12px;background:var(--ui-surface);color:var(--ui-ink);font:inherit}.chronicle-editor textarea{font-size:17px;line-height:1.75;resize:vertical;min-height:160px}.editor-actions{display:flex;align-items:center;flex-wrap:wrap;gap:10px;margin:18px 0}.editor-actions small,.review-item small{display:block;color:var(--ui-muted);font-size:13px;line-height:1.7}.date-edit,.generation-options{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.state-badge{display:inline-flex;align-items:center;white-space:nowrap;gap:6px;min-height:28px;padding:4px 10px;border:1px solid var(--ui-line);border-radius:8px;font-size:13px;font-weight:600;background:var(--ui-raised);color:var(--ui-muted)}.state-badge.is-confirmed{color:var(--ui-success);border-color:var(--ui-success)}.state-badge.is-pending{color:var(--ui-warning);border-color:var(--ui-warning)}.review-states{display:flex;flex-wrap:wrap;gap:6px}.editor-feedback,.editor-warning,.editor-progress{padding:12px 14px;border:1px solid var(--ui-success);border-left-width:3px;border-radius:10px;background:var(--ui-raised);color:var(--ui-success);font-size:14px;line-height:1.7;margin:16px 0}.editor-feedback.is-error{border-color:var(--ui-danger);color:var(--ui-danger)}.editor-warning,.editor-progress{border-color:var(--ui-warning);color:var(--ui-warning)}
.editor-disclosure{margin:20px 0;border:1px solid var(--ui-line);border-radius:12px;overflow:hidden;background:var(--ui-surface)}.disclosure-content{padding:8px 18px 18px}.raw-message{padding:18px 0;border-bottom:1px solid var(--ui-line-soft)}.raw-message:last-child{border-bottom:0}.raw-message strong{font-size:14px;color:var(--ui-accent)}.raw-message .source-reading{margin:12px 0}.chronicle-editor fieldset{border:1px solid var(--ui-line);border-radius:12px;padding:16px;margin:20px 0;min-width:0}.chronicle-editor legend{padding:0 8px;color:var(--ui-muted);font-size:14px}.label-choices{display:flex;flex-wrap:wrap;gap:10px}.label-choices label{padding:8px 12px;border:1px solid var(--ui-line);border-radius:8px}
.review-section{background:var(--ui-raised)}.review-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.review-item{display:flex;align-items:flex-start;flex-direction:column;padding:18px;border:1px solid var(--ui-line);border-radius:12px;background:var(--ui-surface);gap:12px;min-width:0}.review-item:last-child{grid-column:1/-1}.review-item h4,.publish-area h4{font-size:17px;margin:0}.review-item p,.publish-area p{font-size:14px;line-height:1.75;margin:0;color:var(--ui-muted)}.review-item button{margin-top:auto;max-width:100%}.review-item .editor-feedback{color:var(--ui-success);margin:0;width:100%}.publish-area{margin-top:24px;padding:24px 0 0;border-top:2px solid var(--ui-line);display:grid;grid-template-columns:1fr auto;align-items:start;gap:14px}.publish-reasons,.publish-area>.editor-help,.publish-area>.editor-feedback{grid-column:1/-1}.publish-reasons{margin:0;padding-left:24px;color:var(--ui-warning);font-size:14px;line-height:1.8}
.retention-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.retention-grid label{display:flex!important;gap:12px;padding:16px;min-height:80px!important;border:1px solid var(--ui-line);border-radius:12px;background:var(--ui-raised);cursor:pointer;align-items:flex-start!important}.retention-grid label:has(input:checked){border-color:var(--ui-accent);box-shadow:inset 0 0 0 1px var(--ui-accent)}.retention-grid label strong{display:block;font-size:15px}.retention-grid label small{display:block;color:var(--ui-muted);font-size:13px;font-weight:400;margin-top:4px}.retention-grid input{flex:none}.retention-settings:disabled label{cursor:not-allowed}.developer-details pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;line-height:1.7}.task-reading{font-size:14px;line-height:1.75}
.confirmation{position:fixed;z-index:10000;top:15%;left:50%;transform:translateX(-50%);width:min(620px,calc(100vw - 32px));max-height:75dvh;overflow:auto;background:var(--ui-surface);color:var(--ui-ink);padding:28px;border:2px solid var(--ui-line);border-radius:20px;box-shadow:0 0 0 100vmax rgb(20 28 38 / 40%),var(--ui-shadow)}.confirmation h3{font-size:21px;margin:0 0 16px}.confirmation p{font-size:16px;line-height:1.8}
@media(max-width:1100px){.review-grid,.retention-grid{grid-template-columns:1fr}.review-item:last-child{grid-column:auto}}
@media(max-width:600px){.chronicle-editor{padding:14px;border-radius:14px}.editor-section{padding:16px 12px}.editor-heading h2{font-size:24px}.entry-title{font-size:22px}.date-edit,.generation-options,.publish-area{grid-template-columns:1fr}.publish-area>button{width:100%}.source-reading,.chronicle-editor textarea{font-size:17px}.editor-jumps a{flex:1 1 44%;justify-content:center}.disclosure-content{padding:8px 12px 14px}.confirmation{padding:20px}.section-heading{gap:10px}}
.chronicle-editor textarea.keyword-editor{min-height:100px;font-size:15px;line-height:1.7}
.keyword-source{font-size:15px;line-height:1.75;white-space:pre-wrap;overflow-wrap:anywhere}.keyword-source mark{background:var(--ui-accent-bg);color:var(--ui-ink);padding:1px 3px;border-radius:3px}
.chronicle-editor label.keyword-suggestion-option{display:block;padding:12px;margin:8px 0;border:1px solid var(--ui-line);border-radius:8px}.keyword-suggestion-option input{margin-right:10px}.keyword-suggestion-option small{display:block;margin-top:6px}.keyword-suggestion-option p{display:block;margin:6px 0 0}
</style>
