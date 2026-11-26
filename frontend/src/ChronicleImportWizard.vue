<script setup lang="ts">
import type {ReviewChange} from './chronicleReviewChange'
import ChronicleEditor from './ChronicleEditor.vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'

const emit=defineEmits<{changed:[]; review:[ids:string[],selectAll?:boolean]; revised:[change:ReviewChange]}>()
const editors=ref<{dirty:boolean}[]>([])
const dirty=computed(()=>editors.value.some(editor=>editor.dirty))
defineExpose({dirty})
type Message = {ordinal:number; role:string; contentPreview:string|null; externalMessageId:string|null; time:{original:string|null; precision:string; timezoneBasis?:string; occurredAt:string|null; occurredDate:string|null}}
type Preview = {name:string; format:string; title:string; messageCount:number; fileSha256:string; legacyDuplicate:boolean; chronicleDuplicate:boolean; externalConversationId:string|null;externalIdentityConflicts:string[]; warnings:string[]; messages:Message[]}
type Segment = {id:string; file_id:string; ordinal:number; start_ordinal:number; end_ordinal:number; title:string; currentTitle?:string|null; entry_id:string|null}
type FileState = {has_entry_links?:boolean;id:string; name:string; cursor:number; total:number; status:string; conversation_id:number|null; conversion_note:string; reading_ranges_pending:number}
type Processing = {status:string;reason:string|null;entries:number;synopses:number;unsent?:number;exceptions?:{ordinal:number;state:string;reason:string|null;resolved?:boolean}[]}
type LocalTask = {id:string; mode:string; status:string; stage:string; version:number; reason:string|null; indexed:number}
type Batch = {localTask?:LocalTask|null;processing?:Processing|null;batchId:string; status:string; processed:number; total:number; planVersion:number; previewFingerprint:string|null; files:FileState[]; segments:Segment[]; errorCode:string|null}
type Draft = {entryId:string; segmentId:string; titleSuggestion:string; sourceRefs:{kind:string;id:string}[];status:string}
type RawItem = {messageId:number; ordinal:number; role:string; content:string; originalTime:string|null; occurredDate:string|null;timePrecision:string;conversionNote:string}
type RawFile = {id:string;batch_id:string;name:string;title:string|null;status:string;sourceStatus:string;cursor:number;total:number;first_date:string|null;last_date:string|null;unverified_local_count:number}
type RawHit = {messageId:number;ordinal:number;fileId:string;batchId:string;conversationTitle:string|null;role:string;text:string;occurredDate:string|null;timePrecision?:string}

function processingReason(reason:string) {
  if(reason==='synopsis_quote_ambiguous')return '引文在原消息中出现多次，无法确定位置。'
  if(reason==='synopsis_quote_not_found')return '引文与原消息不完全一致。'
  if(reason==='synopsis_explicit_speaker_conflict')return '梗概的发言归属与引用不一致。'
  if(reason==='plan_incomplete')return '模型返回了未完成的梗概。'
  if(reason==='plan_usage_unknown'||reason.startsWith('plan_http_')||reason==='plan_transport_unknown')return '套餐连接或用量未能确认，已保留原请求，未自动重发。'
  const known:Record<string,string>={plan_output_rejected:'部分梗概没有通过引文或正文校验。',local_fields_incomplete:'部分本地字段未就绪。',plan_profile_missing:'请在模型设置中连接 ChatGPT 套餐。',plan_input_capacity:'完整输入超出模型容量。'}
  return reason==='import_filing_incomplete'?'部分分类、标签或关键词缺少可定位的依据，可在批量审核中查看。':known[reason]||reason
}
const props = defineProps<{characterId:number;reviewBusy?:boolean}>()
const files = ref<File[]>([])
const confirmTimezone = ref(false)
const timezoneOffset = ref(480)
const previews = ref<Preview[]>([])
const previewFingerprint = ref('')
const needsOffset = computed(() => previews.value.some(file => file.messages.some(item => item.time.precision === 'local_datetime_unverified' || item.time.timezoneBasis === 'user_confirmed_offset')))
function timeLabel(item:Message) {
  if (item.time.timezoneBasis === 'user_confirmed_offset') return '按本批确认的偏移换算'
  return ({instant:'来源含时区', day:'来源只给出日期', local_datetime_unverified:'时区未注明', unknown:'没有可靠的消息时间'} as Record<string,string>)[item.time.precision] || '时间依据未识别'
}
function timeIssues(file:Preview) {
  return file.messages.filter(item => ['local_datetime_unverified','unknown'].includes(item.time.precision))
}
const batch = ref<Batch|null>(null)
const allRecovered = computed(()=>{
  const p=batch.value?.processing
  return !!p && p.entries>0 && p.synopses===p.entries && !!p.exceptions?.length
    && p.exceptions.every(issue=>issue.resolved)
    && ['plan_usage_unknown','plan_output_rejected','import_worker_interrupted'].includes(p.reason||'')
})
function entryRevised(change:ReviewChange){
  const draft=drafts.value.find(item=>item.entryId===change.entryId)
  const title=change.fields.find(field=>field.label==='标题')
  if(draft && title)draft.titleSuggestion=title.after
  emit('revised',change)
}
const previous = ref<{batchId:string;status:string;processed:number;total:number;localStatus?:string}[]>([])
const rawFiles = ref<RawFile[]>([])
const rawQuery = ref('')
const rawFrom = ref('')
const rawTo = ref('')
const rawHits = ref<RawHit[]>([])
const rawSearchNext = ref<number|null>(null)
const rawSearchSnapshot = ref<string|null>(null)
const drafts = ref<Draft[]>([])
const opened = ref(new Set<string>())
const progressError=ref('')
let mounted = true
const localActive=computed(()=>['queued','running'].includes(batch.value?.localTask?.status||''))
const localStatus:Record<string,string>={queued:'已排队',running:'进行中',paused:'已暂停',failed:'需要处理',cancelled:'已取消，已完成部分保留',completed:'已完成'}
const localStage:Record<string,string>={archive:'原文归档',index:'建立索引',drafts:'整理分段草稿',done:'处理结束'}
const localReason:Record<string,string>={job_already_open:'已有其他索引任务，请先完成或结束它，再继续本批。',archive_order_invalid:'来源顺序需要核对，修正后可继续。',source_changed_during_index:'来源已改变，核对后可继续。',local_step_failed:'本步未完成，已保留进度。检查服务状态后可继续。',access_or_source_changed:'请检查角色权限和原始档案是否仍可读取。'}
interface IndexJob {job_id:string;status:string;processed:number}
let refreshTimer:ReturnType<typeof setTimeout> | undefined
onBeforeUnmount(()=>{clearTimeout(refreshTimer);mounted=false})
const draftConfirmation = ref<string | null>(null)
const raw = ref<RawItem[]>([])
const rawFile = ref('')
const rawNext = ref<number|null>(null)
const rawSnapshot = ref<string|null>(null)
const indexJob = ref<string|null>(null)
const indexState = ref('')
const busy = ref(false)

const boundaryDirty = ref(false)
const message = ref('')
const error = ref('')
const metadata = ref<Record<string,{title:string;synopsis:string;keywords:string;tags:string;narrativeVersionId:string;versionId?:string;metadataHash?:string;sourceHash?:string;status:string}>>({})

async function request<T>(path:string, method='GET', body?:BodyInit):Promise<T> {
  if(!mounted) throw new Error('导入面板已关闭，可从原批次继续。')
  const requestedCharacter = props.characterId
  const response = await fetch(path,{method,body,credentials:'same-origin',cache:'no-store',
    headers:body instanceof FormData || !body ? undefined : {'Content-Type':'application/json'}})
  if (!response.ok) {
    const detail = await response.json().catch(()=>({detail:`HTTP ${response.status}`})) as {detail?:string}
    throw Object.assign(new Error(typeof detail.detail === 'string' ? detail.detail : `HTTP ${response.status}`),{status:response.status})
  }
  const result = await response.json() as T
  if (!mounted || props.characterId !== requestedCharacter) throw new Error('角色或面板已切换，可从原批次继续。')
  return result
}
function path(suffix:string){return `/api/memory-v2/imports${suffix}`}
function params(extra=''){return `characterId=${props.characterId}${extra}`}
async function action(work:()=>Promise<void>){if(busy.value)return;busy.value=true;error.value='';message.value='';try{await work()}catch(exc){error.value=exc instanceof Error?exc.message:'操作失败'}finally{busy.value=false}}
function selectFiles(selected:File[]){
  confirmTimezone.value=false
  omitted.value=[]
  files.value=selected
  previews.value=[]
  previewFingerprint.value=''
  if(selected.length)void preview()
}
function choose(event:Event){selectFiles(Array.from((event.target as HTMLInputElement).files||[]))}
function synthetic(){omitted.value=[];const data={format:'yosaki-conversation',version:2,title:'合成旧聊天',conversation:{id:'synthetic-archive-1'},messages:[
  {id:'s1',role:'user',content:'如果明天下雨，就先去图书馆。',created_at:'2024-05-06T10:00:00+08:00'},
  {id:'s2',role:'assistant',content:'うん、先に図書館へ行こう。',created_at:'2024-05-06T10:03:00+08:00'},
  {id:'s3',role:'user',content:'不是明天，是今天。',created_at:'2024-05-06T10:35:00+08:00'}]}
  selectFiles([new File([JSON.stringify(data)],'synthetic-history.json',{type:'application/json'})])
}
function form(selected:File[]=files.value){const body=new FormData();body.append('characterId',String(props.characterId));selected.forEach(file=>body.append('files',file));if(confirmTimezone.value)body.append('confirmedOffsetMinutes',String(timezoneOffset.value));return body}
async function preview(){await action(async()=>{if(!files.value.length)throw new Error('请先选择文件');const result=await request<{files:Preview[];previewFingerprint:string}>(path('/preview'),'POST',form());previews.value=result.files;previewFingerprint.value=result.previewFingerprint;message.value='只读预览完成；尚未写入消息。'})}
async function prepare(){await action(async()=>{const body=form();if(previewFingerprint.value)body.append('expectedPreviewFingerprint',previewFingerprint.value);const result=await request<{batchId:string}>(path('/batches'),'POST',body);await load(result.batchId);previews.value=[];previewFingerprint.value='';files.value=[];message.value='预览批次已暂存。确认前可调整时间边界；尚未写入 RAW。'})}
async function load(id:string, progressOnly=false){
  const role=props.characterId
  const saved=await request<Batch>(path(`/batches/${encodeURIComponent(id)}?${params()}`))
  if(!mounted || role!==props.characterId || (progressOnly && batch.value?.batchId!==id))return
  if(batch.value?.batchId!==id)opened.value=new Set()
  const finished=progressOnly && batch.value?.processing?.status!==saved.processing?.status && !['running','queued'].includes(saved.processing?.status||'')
  const localFinished=progressOnly && localActive.value && !['queued','running'].includes(saved.localTask?.status||'')
  batch.value=saved;boundaryDirty.value=false;progressError.value=''
  clearTimeout(refreshTimer)
  if(localActive.value || (saved.processing && ['queued','running'].includes(saved.processing.status)))
    scheduleProgress(id)
  const locationUrl=new URL(location.href)
  locationUrl.searchParams.set('batch',saved.batchId)
  history.replaceState(history.state,'',locationUrl)
  drafts.value=saved.files.flatMap(file=>saved.segments.filter(s=>s.file_id===file.id && s.entry_id).map(s=>({
    entryId:s.entry_id!, segmentId:s.id, titleSuggestion:s.currentTitle || `第 ${s.ordinal} 段 · 暂无有效标题`, sourceRefs:[], status:'saved'
  })))
  if(finished || localFinished){emit('changed');void loadRawFiles();void loadPrevious()}
  if(!progressOnly){raw.value=[];rawFile.value='';rawNext.value=null;rawSnapshot.value=null;if(saved.status==='raw_complete'){
    try{await readIndex()}catch{indexJob.value=null;indexState.value='进度暂不可读'}
  }}
}
function scheduleProgress(id:string, failures=0){
  refreshTimer=setTimeout(async()=>{
    if(!mounted || batch.value?.batchId!==id)return
    try {await load(id,true)} catch(e) {
      if(!mounted || batch.value?.batchId!==id)return
      const retry=failures<2 && (e instanceof TypeError || (e instanceof Error && 'status' in e && Number(e.status)>=500))
      progressError.value=retry?'暂时读不到整理进度，稍后自动查看。':'进度读取失败，可重新查看本批。'
      if(retry)scheduleProgress(id,failures+1)
    }
  },(localActive.value?900:3500)*(failures+1))
}
async function startLocal(mode:'raw'|'index'|'organize'){
  const saved=batch.value
  if(!saved)return
  const query=new URLSearchParams({characterId:String(props.characterId),mode,expectedVersion:String(saved.planVersion)})
  if(saved.previewFingerprint)query.set('expectedPreviewFingerprint',saved.previewFingerprint)
  await request(path(`/batches/${saved.batchId}/local?${query}`),'POST')
  await load(saved.batchId)
}
async function controlLocal(command:'pause'|'cancel'|'resume'|'restart'){await action(async()=>{
  const saved=batch.value
  if(!saved?.localTask)return
  try{await request(path(`/batches/${saved.batchId}/local/${command}?${params()}&version=${saved.localTask.version}`),'POST')}
  finally{await load(saved.batchId)}
})}
const omitted=ref<string[]>([])
async function autoImport(withPlan:boolean){await action(async()=>{
  let selected=[...files.value]
  omitted.value=[]
  type FilePreview={previewFingerprint:string;files:Preview[]}
  const readPreview=(material:File[])=>request<FilePreview>(path('/preview'),'POST',form(material))
  let preview:FilePreview
  try{preview=previewFingerprint.value ? {files:previews.value,previewFingerprint:previewFingerprint.value} : await readPreview(selected)}catch(e){
    if(selected.length<2 || !(e instanceof Error) || !('status' in e) || e.status!==422)throw e
    const valid:File[]=[]
    for(const file of selected){
      try{await readPreview([file]);valid.push(file)}catch(issue){
        if(!(issue instanceof Error) || !('status' in issue) || issue.status!==422)throw issue
        omitted.value.push(file.name+' · '+issue.message)
      }
    }
    if(!valid.length){message.value='档案格式或内容需要处理。';return}
    selected=valid;preview=await readPreview(selected)
  }
  const onePerFile=preview.files.length===selected.length &&
    selected.every((file,i)=>!file.name.toLowerCase().endsWith('.zip') && file.name===preview.files[i].name)
  const groups:Preview[][]=[]
  if(selected.length===1)groups.push(preview.files)
  else if(onePerFile)groups.push(...preview.files.map(item=>[item]))
  else for(const file of selected)groups.push((await readPreview([file])).files)
  const identityFiles=new Map<string,Set<string>>()
  const keysFor=(item:Preview)=>item.externalConversationId==null?[]:item.messages
    .filter(m=>m.externalMessageId!=null).map(m=>JSON.stringify([item.externalConversationId,m.externalMessageId]))
  for(const item of groups.flat()){
    for(const key of keysFor(item)){
      const hashes=identityFiles.get(key)??new Set<string>()
      hashes.add(item.fileSha256);identityFiles.set(key,hashes)
    }
  }
  const seen=new Set<string>()
  const accepted=selected.filter((file,i)=>{
    const group=groups[i], hashes=new Set<string>()
    for(const item of group){
      const duplicate=item.legacyDuplicate||item.chronicleDuplicate||seen.has(item.fileSha256)||hashes.has(item.fileSha256)
      const conflict=item.externalIdentityConflicts.length || keysFor(item).some(key=>identityFiles.get(key)!.size>1)
      if(duplicate||conflict){
        omitted.value.push(file.name+(group.length>1?'（'+item.name+'）':'')+' · '+(duplicate?'重复档案':'消息来源冲突'))
        return false
      }
      hashes.add(item.fileSha256)
    }
    hashes.forEach(hash=>seen.add(hash))
    return true
  })
  if(!accepted.length){message.value='所选档案均已跳过。';return}
  if(accepted.length!==selected.length){selected=accepted;preview=await readPreview(selected)}
  const body=form(selected)
  body.append('expectedPreviewFingerprint',preview.previewFingerprint)
  body.append(withPlan?'withPlanSynopsis':'localAuto','true')
  const staged=await request<{batchId:string}>(path('/batches'),'POST',body)
  files.value=[];previews.value=[];previewFingerprint.value=''
  if(withPlan){
    await load(staged.batchId);await loadPrevious();message.value=batch.value?.processing?'已读取后台整理状态，可关闭页面，稍后回来审核。':'已暂存本批，尚未取得后台整理回执。'
  }else{
    await load(staged.batchId);await loadPrevious();if(batch.value?.localTask?.status==='completed'){emit('changed');void loadRawFiles()}
  }
})}
async function removedEntry(id:string) {
  drafts.value=drafts.value.filter(d=>d.entryId!==id)
  message.value='已删除这条经历，原始聊天仍可核对。'
  emit('changed')
  if(batch.value) await load(batch.value.batchId)
}
async function loadPrevious(){try{previous.value=(await request<{items:typeof previous.value}>(path(`/batches?${params()}`))).items}catch{previous.value=[]}}
const rawListError=ref('')
let rawListRequest=0
async function loadRawFiles(){
  const sequence=++rawListRequest
  rawListError.value=''
  rawFiles.value=[]
  try {
    const result=await request<{items:RawFile[]}>(path(`/raw-files?${params(`&fromDate=${rawFrom.value}&toDate=${rawTo.value}`)}`))
    if(sequence===rawListRequest)rawFiles.value=result.items
  } catch(e) {
    if(mounted && sequence===rawListRequest)rawListError.value=e instanceof Error?e.message:'无法读取原始聊天'
  }
}
watch([rawFrom,rawTo],loadRawFiles)
async function searchRaw(more=false){await action(async()=>{if(rawQuery.value.trim().length<2)throw new Error('至少输入两个有效字符');const previous=more?[...rawHits.value]:[];const after=more?rawSearchNext.value||0:0;const snapshot=more?rawSearchSnapshot.value:null;rawHits.value=[];rawSearchNext.value=null;rawSearchSnapshot.value=null;const result=await request<{items:RawHit[];nextAfter:number|null;snapshot:string|null}>(path(`/raw-search?${params(`&q=${encodeURIComponent(rawQuery.value)}&afterId=${after}${snapshot?`&snapshot=${encodeURIComponent(snapshot)}`:''}`)}`));rawHits.value=more?[...previous,...result.items]:result.items;rawSearchNext.value=result.nextAfter;rawSearchSnapshot.value=result.snapshot})}
async function openRawHit(hit:RawHit){await action(async()=>{await load(hit.batchId)});await loadRaw(hit.fileId,false,hit.ordinal-1)}
watch(()=>props.characterId,()=>{batch.value=null;files.value=[];previews.value=[];previewFingerprint.value='';drafts.value=[];raw.value=[];rawHits.value=[];rawFile.value='';rawNext.value=null;rawSnapshot.value=null;rawSearchNext.value=null;rawSearchSnapshot.value=null;indexJob.value=null;metadata.value={};void loadPrevious().then(()=>{
  const hint=new URLSearchParams(location.search).get('batch')
  const target=hint && (/^[\da-f]{8}-[\da-f-]{27}$/i.test(hint)?hint:previous.value.find(x=>x.batchId.slice(0,8)===hint)?.batchId)
  const selected=target||previous.value.find(x=>['queued','running','paused','failed'].includes(x.localStatus||''))?.batchId
  if(selected)void action(()=>load(selected))
});void loadRawFiles()},{immediate:true})
function updateTimePreview(){previews.value=[];previewFingerprint.value='';if(files.value.length)void preview()}
async function saveBoundaries(file:FileState){await action(async()=>{if(!batch.value)return;const segments=batch.value.segments.filter(item=>item.file_id===file.id).map(item=>({start:item.start_ordinal,end:item.end_ordinal,title:item.title}));batch.value=await request<Batch>(path(`/batches/${batch.value.batchId}/boundaries`),'PUT',JSON.stringify({characterId:props.characterId,fileId:file.id,expectedVersion:batch.value.planVersion,segments}));boundaryDirty.value=false;message.value='人工边界已保存，仍未导入 RAW。'})}
function split(segment:Segment){if(!batch.value||segment.start_ordinal>=segment.end_ordinal)return;const list=batch.value.segments;const at=list.indexOf(segment);const oldEnd=segment.end_ordinal;segment.end_ordinal=segment.start_ordinal;list.splice(at+1,0,{...segment,id:`new-${Date.now()}`,ordinal:segment.ordinal+1,start_ordinal:segment.start_ordinal+1,end_ordinal:oldEnd,title:'待整理片段'});boundaryDirty.value=true}
function merge(segment:Segment){if(!batch.value)return;const list=batch.value.segments;const at=list.indexOf(segment);const next=list[at+1];if(next&&next.file_id===segment.file_id){segment.end_ordinal=next.end_ordinal;list.splice(at+1,1);boundaryDirty.value=true}}
async function discard(){await action(async()=>{if(!batch.value)return;await request(path(`/batches/${batch.value.batchId}?${params()}`),'DELETE');batch.value=null;await loadPrevious();message.value='只清理了未确认的服务端暂存；你保留的原始文件未被删除。'})}
async function loadRaw(fileId:string,more=false,startAfter=0){await action(async()=>{if(!batch.value)return;const previous=more?[...raw.value]:[];const after=more?rawNext.value||0:startAfter;const snapshot=rawSnapshot.value;raw.value=[];rawNext.value=null;rawFile.value='';const link=path(`/batches/${batch.value.batchId}/raw/${encodeURIComponent(fileId)}?${params(`&after=${after}&limit=30${more&&snapshot?`&snapshot=${snapshot}`:''}`)}`);const result=await request<{items:RawItem[];nextAfter:number|null;snapshot:string}>(link);raw.value=more?[...previous,...result.items]:result.items;rawNext.value=result.nextAfter;rawSnapshot.value=result.snapshot;rawFile.value=fileId})}
function showIndex(job:IndexJob|null){
  indexJob.value=job?.job_id??null
  indexState.value=job ? (({completed:'已完成',running:'可继续',paused:'已暂停',failed:'失败'} as Record<string,string>)[job.status]||job.status)+' · '+job.processed+' 条' : ''
}
async function readIndex(){
  const result=await request<{latestJob:IndexJob|null}>('/api/memory-v2/status?'+params())
  showIndex(result.latestJob)
  return result.latestJob
}
async function makeDrafts(){await action(async()=>{
  if(!batch.value || draftConfirmation.value !== batch.value.batchId) return
  draftConfirmation.value = null
  const found:Draft[]=[]
  for(const file of batch.value.files.filter(f=>f.reading_ranges_pending)){
    const base=`/api/memory-v2/workspace/imports/${batch.value.batchId}/${file.id}/boundaries`
    const plan=await request<{sourceSnapshot:string;segments:{start:number;end:number;title:string}[]}>(`${base}?${params()}`)
    const result=await request<{items:{entryId:string;start:number;end:number;title:string}[]}>(base,'POST',JSON.stringify({characterId:props.characterId,sourceSnapshot:plan.sourceSnapshot,segments:plan.segments}))
    for(const item of result.items) found.push({entryId:item.entryId,segmentId:'',titleSuggestion:item.title,sourceRefs:[],status:'draft_needs_metadata'})
  }
  await load(batch.value.batchId);message.value=`已确认 ${found.length} 段的来源范围。内容仍按各自审核状态保存。`
})}
async function saveMetadata(draft:Draft){await action(async()=>{const item=metadata.value[draft.entryId];if(!item?.synopsis.trim())throw new Error('请填写有来源支持的单经历梗概');const result=await request<{versionId:string;metadataHash:string;sourceHash:string}>(`/api/memory-v2/entries/${draft.entryId}/versions`,'POST',JSON.stringify({characterId:props.characterId,sourceRefs:draft.sourceRefs,title:item.title,keywords:item.keywords.split(/[,，\s]+/).filter(Boolean),tags:item.tags.split(/[,，\s]+/).filter(Boolean),synopsis:item.synopsis,narrativeVersionId:item.narrativeVersionId||null}));Object.assign(item,result,{status:'草稿待审核'});message.value='元数据新版本已保存，尚未审核或激活。'})}
async function review(draft:Draft){await action(async()=>{const item=metadata.value[draft.entryId];if(!item.versionId||!item.metadataHash||!item.sourceHash)throw new Error('请先保存版本');if(!item.narrativeVersionId)throw new Error('P1-A 激活门槛要求已审核 NARRATIVE；当前 RAW 草稿可先保留。');await request(`/api/memory-v2/entries/${draft.entryId}/versions/${item.versionId}/review`,'POST',JSON.stringify({characterId:props.characterId,metadataHash:item.metadataHash,sourceHash:item.sourceHash}));item.status='已人工审核，待激活';message.value='审核只适用于当前版本与来源快照。'})}
async function activate(draft:Draft){await action(async()=>{const item=metadata.value[draft.entryId];if(!item.versionId)throw new Error('尚无版本');await request(`/api/memory-v2/entries/${draft.entryId}/versions/${item.versionId}/activate?${params()}`,'POST');item.status='已激活';message.value='条目已激活。';emit('changed')})}
</script>

<template>
  <section class="import-workbench" aria-label="历史聊天导入">
    <div class="import-heading"><h2>历史聊天导入</h2></div>
    <p class="import-note">选好档案后会自动预览消息、日期和重复项。本地任务在服务中继续，关页或重启后可查看进度。整理好的草稿由你决定是否发布。</p>
    <div class="import-actions"><input :disabled="busy" type="file" multiple accept=".json,.md,.txt,.zip" @change="choose" aria-label="选择聊天档案"/><button :disabled="busy||!files.length" @click="autoImport(false)">仅在本机导入并分段</button><button v-if="files.length&&!previews.length&&!busy" @click="preview">重新预览</button><button :disabled="busy||!previews.length||previews.some(x=>x.legacyDuplicate||x.chronicleDuplicate||x.externalIdentityConflicts.length)" @click="prepare">暂存预览批次</button></div>
    <p v-if="files.length" class="import-note">用 ChatGPT 套餐整理时，本批对话、说话人及来源编号会发给 OpenAI，用于拟题、写梗概、归类和提取关键词。 <button :disabled="busy" @click="autoImport(true)">使用套餐导入并自动整理</button></p><details class="import-recent"><summary>试用合成示例</summary><button :disabled="busy" @click="synthetic">载入合成示例</button></details>
    <div v-if="needsOffset||confirmTimezone" class="import-actions"><label><input @change="updateTimePreview" v-model="confirmTimezone" :disabled="busy" type="checkbox"/> 我确认无时区时间使用此固定 UTC 偏移</label><label v-if="confirmTimezone">偏移分钟 <input v-model.number="timezoneOffset" @change="updateTimePreview" :disabled="busy" type="number" min="-840" max="840"/></label><small>未确认时保留时间原文，不换算精确发生时刻。</small></div>
    <details v-if="omitted.length"><summary>已跳过 {{omitted.length}} 个档案</summary><ul><li v-for="(item,i) in omitted" :key="i">{{item}}</li></ul></details><p v-if="error" class="import-error" role="alert">{{ error }}</p><p v-if="message" class="import-info" role="status">{{ message }}</p>
    <div v-for="file in previews" :key="file.fileSha256" class="import-file"><h3>{{ file.title }} <small>{{ file.name }} · {{ file.messageCount }} 条 · {{ file.format.toUpperCase() }}</small></h3><p v-if="file.legacyDuplicate||file.chronicleDuplicate" class="import-error">与已导入或待处理档案重复，不能直接再次写入。</p><p v-if="file.externalIdentityConflicts.length" class="import-error">外部消息身份冲突：{{ file.externalIdentityConflicts.slice(0,3).join('、') }}；请先检查重叠来源。</p><p v-for="warning in file.warnings" :key="warning" class="import-warning">{{ warning }}</p><p>采用档案标注的消息时间。正文提及的事件日期另行核对。</p><details v-if="timeIssues(file).length"><summary>时间尚有疑问的 {{timeIssues(file).length}} 条消息</summary><ol><li v-for="item in timeIssues(file)" :key="item.ordinal">第 {{item.ordinal}} 条 · {{item.time.original||'未注明'}} · {{timeLabel(item)}}</li></ol></details><details><summary>查看原始顺序、说话人与时间依据</summary><p v-for="item in file.messages.slice(0,20)" :key="item.ordinal">{{ item.ordinal }} · {{ item.role }} · {{ item.time.original || '时间未知' }} · {{ timeLabel(item) }}<br/>{{ item.contentPreview }}</p><small v-if="file.messages.length>20">仅显示前 20 条正文预览；全部 {{ file.messages.length }} 条已校验。</small></details></div>
    <details class="import-recent"><summary>阅读已导入的批次</summary><button v-for="item in previous" :key="item.batchId" :disabled="busy" @click="action(()=>load(item.batchId))">{{ item.status==='raw_complete'?'已归档':item.status }} · {{ item.processed }}/{{ item.total }} · {{ item.batchId.slice(0,8) }}</button><p v-if="!previous.length">当前角色暂无批次。</p></details>
    <details class="import-recent" open><summary>按会话和日期查找原始聊天</summary><div class="import-actions"><label>起始日期<input v-model="rawFrom" type="date"/></label><label>结束日期<input v-model="rawTo" type="date"/></label><button v-if="rawListError" @click="loadRawFiles">重新读取会话</button></div><button v-for="item in rawFiles" :key="item.id" :disabled="item.sourceStatus!=='valid'" @click="openRawHit({messageId:0,ordinal:1,fileId:item.id,batchId:item.batch_id,conversationTitle:item.title||'',role:'',text:'',occurredDate:item.first_date})">{{ item.sourceStatus==='valid'?item.title:'来源失效，请刷新或检查' }} · {{ item.first_date||'时间未知' }}—{{ item.last_date||'时间未知' }}<span v-if="item.unverified_local_count"> · 含未核实时区日期</span> · {{ item.cursor }}/{{ item.total }}</button><p v-if="rawListError" role="alert">{{rawListError}}</p><p v-else-if="!rawFiles.length">当前日期范围内没有已归档的聊天。</p><div class="import-actions"><input v-model="rawQuery" placeholder="搜索原始聊天正文" @keyup.enter="searchRaw()"/><button @click="searchRaw()">检索</button></div><p v-for="hit in rawHits" :key="hit.messageId"><button @click="openRawHit(hit)">{{ hit.conversationTitle||'来源标题失效' }} · 第 {{ hit.ordinal }} 条 · {{ hit.occurredDate||'时间未知' }}<span v-if="hit.timePrecision==='local_datetime_unverified'">（时区未核实）</span><br/>{{ hit.text }}</button></p><button v-if="rawSearchNext!==null" @click="searchRaw(true)">加载更多命中</button></details>
    <template v-if="batch"><p v-if="progressError" role="status">{{progressError}} <button :disabled="busy" @click="action(()=>load(batch!.batchId))">查看进度</button></p><div class="import-progress"><strong>原文归档 · {{({preview:'待确认',confirmed:'已确认',running:'进行中',paused:'已暂停',raw_complete:'已完成',failed:'需要处理'} as Record<string,string>)[batch.status]||batch.status}}</strong><span>{{ batch.processed }}/{{ batch.total }} 条消息</span><details><summary>批次详情</summary><small>{{batch.batchId}} · {{batch.status}} · {{batch.processing?.reason}}</small></details></div>
      <button v-if="!batch.processing && !localActive && batch.status==='raw_complete'" :disabled="busy" @click="action(async()=>{const id=batch!.batchId;await request(path('/batches/'+id+'/prepare-review?'+params()),'POST');await load(id)})">用 ChatGPT 套餐自动整理本批</button>
      <button v-if="batch.processing?.status==='paused' && batch.processing.unsent" :disabled="busy" @click="action(async()=>{const id=batch!.batchId;await request(path('/batches/'+id+'/prepare-review?'+params('&onlyUnsent=true')),'POST');await load(id)})">继续整理尚未发送的 {{batch.processing.unsent}} 段</button>
      <p v-if="batch.processing" role="status">ChatGPT 套餐 · {{allRecovered ? '梗概已齐，可进行最终审核' : ({queued:'等待整理',running:'正在自动整理',completed:'整理完成，可进行最终审核',failed:'部分内容待处理',paused:'整理已暂停'} as Record<string,string>)[batch.processing.status]}} · 已保存梗概 {{batch.processing.synopses}} / {{batch.processing.entries}} 份 <span v-if="batch.processing.reason && !allRecovered"> · {{processingReason(batch.processing.reason)}}</span></p>
      <details v-if="batch.processing?.exceptions?.length"><summary>查看历次整理记录（{{batch.processing.exceptions.length}}）</summary>
        <p v-for="(issue,i) in batch.processing.exceptions" :key="i">第 {{issue.ordinal}} 段 · {{issue.resolved?'已有可审梗概。原请求记录：':''}}{{issue.state==='unknown'?'请求结果未确认，已暂停。':processingReason(issue.reason||'梗概未通过校验。')}}</p>
      </details>
      <section v-if="batch.localTask" class="import-note" aria-label="本地任务">
        <strong role="status">本地任务 · {{localStatus[batch.localTask.status]}} · {{localStage[batch.localTask.stage]}}</strong>
        <p v-if="batch.localTask.stage==='index'">索引已处理 {{batch.localTask.indexed}} 条来源</p>
        <p v-if="batch.localTask.reason">{{localReason[batch.localTask.reason]||'本步需要检查。已完成的部分保留，可查看批次详情。'}}</p>
        <button v-if="batch.localTask.status==='cancelled'" :disabled="busy" @click="controlLocal('restart')">继续已取消的本地任务</button>
        <button v-if="localActive" :disabled="busy" @click="controlLocal('pause')">暂停本地任务</button>
        <button v-if="['paused','failed'].includes(batch.localTask.status)" :disabled="busy" @click="controlLocal('resume')">从保存处继续</button>
        <button v-if="['queued','running','paused','failed'].includes(batch.localTask.status)" :disabled="busy" @click="controlLocal('cancel')">取消后续处理</button>
      </section>
      <template v-if="batch.status==='preview' && !batch.processing && !batch.localTask"><p class="import-note">以下是时间间隔建议，不代表已确认的语义话题。可手动调整后逐文件保存。</p><div v-for="file in batch.files" :key="file.id" class="import-file"><h3>{{ file.name }}</h3><div v-for="segment in batch.segments.filter(x=>x.file_id===file.id)" :key="segment.id" class="import-boundary"><label>起始序号<input v-model.number="segment.start_ordinal" type="number" min="1" @input="boundaryDirty=true"/></label><label>结束序号<input v-model.number="segment.end_ordinal" type="number" min="1" @input="boundaryDirty=true"/></label><label>建议标题<input v-model="segment.title" maxlength="200" @input="boundaryDirty=true"/></label><button @click="split(segment)">拆分</button><button @click="merge(segment)">并入下一段</button></div><button :disabled="busy" @click="saveBoundaries(file)">保存此文件边界</button></div><div class="import-actions"><button :disabled="busy||boundaryDirty" @click="action(()=>startLocal('organize'))">导入并整理为分段经历</button><button :disabled="busy||boundaryDirty" @click="action(()=>startLocal('raw'))">仅写入原文归档</button><span v-if="boundaryDirty">先保存边界修改</span><button :disabled="busy" @click="discard">清理暂存批次</button></div></template>
      <div v-if="!batch.processing && (!batch.localTask || batch.localTask.status==='completed')" class="import-actions">
        <button v-if="['confirmed','running','paused'].includes(batch.status)" :disabled="busy" @click="action(()=>startLocal('raw'))">继续原文归档</button>
        <button v-if="batch.status==='raw_complete' && (!batch.localTask||batch.localTask.mode==='raw')" :disabled="busy" @click="action(()=>startLocal('index'))">更新本地索引</button>
        <span v-if="!batch.localTask && indexJob" role="status">来源索引 · {{indexState}}</span>
        <button v-if="batch.status!=='preview' && batch.files.some(f=>!f.has_entry_links && !batch!.segments.some(s=>s.file_id===f.id && s.entry_id))" :disabled="busy" @click="action(()=>startLocal('organize'))">继续自动分段整理</button>
      </div>
      <section v-if="drafts.length" aria-label="分段经历"><h3>分段经历 · {{ drafts.length }} 份</h3>
        <button v-if="drafts.length" :disabled="busy||dirty||reviewBusy" @click="emit('review',drafts.map(item=>item.entryId))">审核本批 {{drafts.length}} 段经历</button>
        <button v-if="drafts.length" class="c-primary" :disabled="busy||dirty||reviewBusy" @click="emit('review',drafts.map(item=>item.entryId),true)">一键全选本批待审经历</button>
        <details v-for="draft in drafts" :key="draft.entryId" class="import-file" @toggle="($event.currentTarget as HTMLDetailsElement).open && opened.add(draft.entryId)"><summary>{{ draft.titleSuggestion }} · 阅读</summary><ChronicleEditor ref="editors" @removed="removedEntry" @revised="entryRevised" @saved="emit('changed')" v-if="opened.has(draft.entryId)" :character-id="characterId" :entry-id="draft.entryId" /></details>
      </section>
      <div v-for="file in batch.files" :key="file.id" class="import-file"><h3>{{ file.name }} <small>{{ file.status }} · {{ file.cursor }}/{{ file.total }} · {{ file.conversion_note }}</small></h3><button :disabled="busy||!file.cursor" @click="loadRaw(file.id)">查看待整理 RAW</button><template v-if="rawFile===file.id"><div v-for="item in raw" :key="item.messageId" class="import-raw"><small>{{ item.ordinal }} · {{ item.role }} · {{ item.originalTime||item.occurredDate||'发生时间未知' }} · {{ item.timePrecision }}</small><p>{{ item.content }}</p></div><button v-if="rawNext!==null" :disabled="busy" @click="loadRaw(file.id,true)">继续读取 RAW</button></template></div>
      <button v-if="batch.files.some(f=>f.reading_ranges_pending) && draftConfirmation!==batch.batchId" :disabled="busy" @click="draftConfirmation=batch.batchId">可选：确认本批分段范围</button>
      <div v-if="draftConfirmation === batch.batchId" class="import-actions" role="group" aria-label="确认来源范围">
        <p>阅读已有草稿无需确认。若已核对本批分段，可一次记录来源范围确认。</p>
        <button :disabled="busy" @click="makeDrafts">确认这些来源范围</button>
        <button :disabled="busy" @click="draftConfirmation=null">取消</button>
      </div>
      
    </template>
  </section>
</template>

<style scoped>
.import-workbench .import-note,.import-file p,.import-info,.import-warning,.import-error,.import-file li {
  font-size:var(--ui-reading-size,17px);line-height:1.75;max-width:78ch;overflow-wrap:anywhere;
}
.import-note {color:var(--ui-ink)}
.import-file h3 {font-size:18px}
.import-workbench details summary {font-size:15px}
.import-workbench .import-error {color:var(--ui-danger)}
@media(max-width:600px){
  .import-workbench {padding:14px;min-width:0}
  .import-workbench button,.import-workbench summary {min-height:44px}
  .import-actions input[type=file] {width:100%;max-width:100%}
}
</style>
