<script setup lang="ts">
import type {ReviewChange} from './chronicleReviewChange'
import ChronicleEditor from './ChronicleEditor.vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'

const emit=defineEmits<{changed:[]; review:[ids:string[]]; revised:[change:ReviewChange]}>()
const editors=ref<{dirty:boolean}[]>([])
const dirty=computed(()=>editors.value.some(editor=>editor.dirty))
defineExpose({dirty})
type Message = {ordinal:number; role:string; contentPreview:string|null; externalMessageId:string|null; time:{original:string|null; precision:string; occurredAt:string|null; occurredDate:string|null}}
type Preview = {name:string; format:string; title:string; messageCount:number; fileSha256:string; legacyDuplicate:boolean; chronicleDuplicate:boolean; externalConversationId:string|null;externalIdentityConflicts:string[]; warnings:string[]; messages:Message[]}
type Segment = {id:string; file_id:string; ordinal:number; start_ordinal:number; end_ordinal:number; title:string; entry_id:string|null}
type FileState = {id:string; name:string; cursor:number; total:number; status:string; conversation_id:number|null; conversion_note:string; reading_ranges_pending:number}
type Processing = {status:string;reason:string|null;entries:number;synopses:number;unsent?:number;exceptions?:{ordinal:number;state:string;reason:string|null}[]}
type Batch = {processing?:Processing|null;batchId:string; status:string; processed:number; total:number; planVersion:number; previewFingerprint:string|null; files:FileState[]; segments:Segment[]; errorCode:string|null}
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
  return known[reason]||reason
}
const props = defineProps<{characterId:number;reviewBusy?:boolean}>()
const files = ref<File[]>([])
const confirmTimezone = ref(false)
const timezoneOffset = ref(480)
const previews = ref<Preview[]>([])
const previewFingerprint = ref('')
const batch = ref<Batch|null>(null)
const previous = ref<{batchId:string;status:string;processed:number;total:number}[]>([])
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
let refreshTimer:ReturnType<typeof setTimeout> | undefined
onBeforeUnmount(()=>{clearTimeout(refreshTimer);mounted=false;running.value=false})
const draftConfirmation = ref<string | null>(null)
const raw = ref<RawItem[]>([])
const rawFile = ref('')
const rawNext = ref<number|null>(null)
const rawSnapshot = ref<string|null>(null)
const indexJob = ref<string|null>(null)
const indexState = ref('')
const busy = ref(false)
const running = ref(false)
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
function choose(event:Event){omitted.value=[];files.value=Array.from((event.target as HTMLInputElement).files||[]);previews.value=[];previewFingerprint.value=''}
function synthetic(){omitted.value=[];const data={format:'yosaki-conversation',version:2,title:'合成旧聊天',conversation:{id:'synthetic-archive-1'},messages:[
  {id:'s1',role:'user',content:'如果明天下雨，就先去图书馆。',created_at:'2024-05-06T10:00:00+08:00'},
  {id:'s2',role:'assistant',content:'うん、先に図書館へ行こう。',created_at:'2024-05-06T10:03:00+08:00'},
  {id:'s3',role:'user',content:'不是明天，是今天。',created_at:'2024-05-06T10:35:00+08:00'}]}
  files.value=[new File([JSON.stringify(data)],'synthetic-history.json',{type:'application/json'})];previews.value=[];previewFingerprint.value='';message.value='已载入合成聊天，尚未写入 RAW。'
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
  batch.value=saved;boundaryDirty.value=false;progressError.value=''
  clearTimeout(refreshTimer)
  if(saved.processing && ['queued','running'].includes(saved.processing.status))
    scheduleProgress(id)
  const locationUrl=new URL(location.href)
  locationUrl.searchParams.set('batch',saved.batchId)
  history.replaceState(history.state,'',locationUrl)
  drafts.value=saved.files.flatMap(file=>saved.segments.filter(s=>s.file_id===file.id && s.entry_id).map(s=>({
    entryId:s.entry_id!, segmentId:s.id, titleSuggestion:s.title, sourceRefs:[], status:'saved'
  })))
  if(finished){emit('changed');void loadRawFiles();void loadPrevious()}
  if(!progressOnly){raw.value=[];rawFile.value='';rawNext.value=null;rawSnapshot.value=null}
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
  },3500*(failures+1))
}
async function organize(id:string){
  await load(id)
  if(batch.value?.status==='preview'){
    const fingerprint=encodeURIComponent(batch.value.previewFingerprint||'')
    batch.value=await request<Batch>(path(`/batches/${id}/confirm?${params(`&expectedVersion=${batch.value.planVersion}&expectedPreviewFingerprint=${fingerprint}`)}`),'POST')
  }
  if(batch.value?.status==='paused')batch.value=await request<Batch>(path(`/batches/${id}/resume?${params()}`),'POST')
  while(batch.value && ['confirmed','running'].includes(batch.value.status)){
    message.value=`正在归档：${batch.value.processed} / ${batch.value.total} 条消息`
    await step()
  }
  if(batch.value?.status!=='raw_complete')throw new Error('归档尚未完成，请查看批次状态。')
  const pending=batch.value.files.filter(f=>!batch.value!.segments.some(s=>s.file_id===f.id && s.entry_id))
  if(pending.length){
    message.value='正在建立本地来源索引…'
    let job=await request<{job_id:string;status:string}>(path(`/index/start?${params('&reuseRunning=true')}`),'POST')
    while(job.status==='running')job=await request(path(`/index/${job.job_id}/step?${params()}`),'POST')
    if(job.status!=='completed')throw new Error('本地索引未完成，已保留归档和进度。')
  }
  const failures:string[]=[]
  for(const file of pending){
    message.value=`正在分段整理《${file.name}》…`
    try{await request(`/api/memory-v2/workspace/imports/${id}/${file.id}/reading-drafts?${params()}`,'POST')}
    catch(e){failures.push(`${file.name}：${e instanceof Error?e.message:'整理失败'}`)}
  }
  await load(id);await loadPrevious();await loadRawFiles()
  message.value=`已保存 ${drafts.value.length} 份分段经历，可在下方展开阅读。`
  emit('changed')
  if(failures.length)throw new Error(failures.join('；'))
}
const omitted=ref<string[]>([])
async function autoImport(withPlan:boolean){await action(async()=>{
  let selected=[...files.value]
  omitted.value=[]
  type FilePreview={previewFingerprint:string;files:Preview[]}
  const readPreview=(material:File[])=>request<FilePreview>(path('/preview'),'POST',form(material))
  let preview:FilePreview
  try{preview=await readPreview(selected)}catch(e){
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
  if(withPlan)body.append('withPlanSynopsis','true')
  const staged=await request<{batchId:string}>(path('/batches'),'POST',body)
  files.value=[];previews.value=[];previewFingerprint.value=''
  if(withPlan){
    await load(staged.batchId);await loadPrevious();message.value=batch.value?.processing?'已读取后台整理状态，可关闭页面，稍后回来审核。':'已暂存本批，尚未取得后台整理回执。'
  }else{
    await organize(staged.batchId)
  }
})}
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
  if(target)void action(()=>load(target))
});void loadRawFiles()},{immediate:true})
watch([confirmTimezone,timezoneOffset],()=>{previews.value=[];previewFingerprint.value=''})
async function saveBoundaries(file:FileState){await action(async()=>{if(!batch.value)return;const segments=batch.value.segments.filter(item=>item.file_id===file.id).map(item=>({start:item.start_ordinal,end:item.end_ordinal,title:item.title}));batch.value=await request<Batch>(path(`/batches/${batch.value.batchId}/boundaries`),'PUT',JSON.stringify({characterId:props.characterId,fileId:file.id,expectedVersion:batch.value.planVersion,segments}));boundaryDirty.value=false;message.value='人工边界已保存，仍未导入 RAW。'})}
function split(segment:Segment){if(!batch.value||segment.start_ordinal>=segment.end_ordinal)return;const list=batch.value.segments;const at=list.indexOf(segment);const oldEnd=segment.end_ordinal;segment.end_ordinal=segment.start_ordinal;list.splice(at+1,0,{...segment,id:`new-${Date.now()}`,ordinal:segment.ordinal+1,start_ordinal:segment.start_ordinal+1,end_ordinal:oldEnd,title:'待整理片段'});boundaryDirty.value=true}
function merge(segment:Segment){if(!batch.value)return;const list=batch.value.segments;const at=list.indexOf(segment);const next=list[at+1];if(next&&next.file_id===segment.file_id){segment.end_ordinal=next.end_ordinal;list.splice(at+1,1);boundaryDirty.value=true}}
async function confirm(){await action(async()=>{if(!batch.value)return;const fingerprint=batch.value.previewFingerprint?`&expectedPreviewFingerprint=${encodeURIComponent(batch.value.previewFingerprint)}`:'';batch.value=await request<Batch>(path(`/batches/${batch.value.batchId}/confirm?${params(`&expectedVersion=${batch.value.planVersion}${fingerprint}`)}`),'POST');message.value='已确认 RAW 导入；请逐批处理。'})}
async function step(){if(!batch.value)return;batch.value=await request<Batch>(path(`/batches/${batch.value.batchId}/step?${params()}`),'POST')}
async function run(){await action(async()=>{running.value=true;try{while(running.value&&batch.value&&['confirmed','running'].includes(batch.value.status)){await step()}}finally{running.value=false}await loadRawFiles();message.value=batch.value?.status==='raw_complete'?'RAW 已完整导入，可独立查看并显式建立索引。':'已停止连续处理。'})}
async function pause(){running.value=false;await action(async()=>{if(batch.value)batch.value=await request<Batch>(path(`/batches/${batch.value.batchId}/pause?${params()}`),'POST')})}
async function resume(){await action(async()=>{if(batch.value)batch.value=await request<Batch>(path(`/batches/${batch.value.batchId}/resume?${params()}`),'POST')})}
async function discard(){await action(async()=>{if(!batch.value)return;await request(path(`/batches/${batch.value.batchId}?${params()}`),'DELETE');batch.value=null;await loadPrevious();message.value='只清理了未确认的服务端暂存；你保留的原始文件未被删除。'})}
async function loadRaw(fileId:string,more=false,startAfter=0){await action(async()=>{if(!batch.value)return;const previous=more?[...raw.value]:[];const after=more?rawNext.value||0:startAfter;const snapshot=rawSnapshot.value;raw.value=[];rawNext.value=null;rawFile.value='';const link=path(`/batches/${batch.value.batchId}/raw/${encodeURIComponent(fileId)}?${params(`&after=${after}&limit=30${more&&snapshot?`&snapshot=${snapshot}`:''}`)}`);const result=await request<{items:RawItem[];nextAfter:number|null;snapshot:string}>(link);raw.value=more?[...previous,...result.items]:result.items;rawNext.value=result.nextAfter;rawSnapshot.value=result.snapshot;rawFile.value=fileId})}
async function startIndex(){await action(async()=>{const result=await request<{job_id:string;status:string}>(path(`/index/start?${params()}`),'POST');indexJob.value=result.job_id;indexState.value=result.status;message.value='索引任务已建立；不会自动生成条目或模型文本。'})}
async function indexStep(){await action(async()=>{if(!indexJob.value)return;const result=await request<{status:string;processed:number}>(path(`/index/${encodeURIComponent(indexJob.value)}/step?${params()}`),'POST');indexState.value=`${result.status} · ${result.processed} 条`;message.value=result.status==='completed'?'M1 索引完成，可建立草稿条目。':'已处理一批索引。'})}
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
async function activate(draft:Draft){await action(async()=>{const item=metadata.value[draft.entryId];if(!item.versionId)throw new Error('尚无版本');await request(`/api/memory-v2/entries/${draft.entryId}/versions/${item.versionId}/activate?${params()}`,'POST');item.status='已激活';message.value='条目已激活；可刷新年历查看。'})}
</script>

<template>
  <section class="import-workbench" aria-label="历史聊天导入">
    <div class="import-heading"><h2>历史聊天导入</h2></div>
    <p class="import-note">分段、精修、日期归类和原文关键词在本机整理。点「导入并自动整理」即允许将本批对话和说话人、来源编号发给 OpenAI，通过已连接的 ChatGPT 套餐逐段写梗概。选择「仅在本机导入并分段」会保存原文、建立索引和分段草稿，请保持本页打开直到整理完成。两种方式都由你审核后发布。</p>
    <div class="import-actions"><input :disabled="busy" type="file" multiple accept=".json,.md,.txt,.zip" @change="choose" aria-label="选择聊天档案"/><button class="primary" :disabled="busy||!files.length" @click="autoImport(true)">导入并自动整理</button><button :disabled="busy||!files.length" @click="autoImport(false)">仅在本机导入并分段</button><button :disabled="busy" @click="synthetic">载入合成示例</button><button :disabled="busy||!files.length" @click="preview">只读预览</button><button :disabled="busy||!previews.length||previews.some(x=>x.legacyDuplicate||x.chronicleDuplicate||x.externalIdentityConflicts.length)" @click="prepare">暂存预览批次</button></div>
    <div class="import-actions"><label><input v-model="confirmTimezone" :disabled="busy" type="checkbox"/> 我确认无时区时间使用此固定 UTC 偏移</label><label v-if="confirmTimezone">偏移分钟 <input v-model.number="timezoneOffset" :disabled="busy" type="number" min="-840" max="840"/></label><small>未确认时保留时间原文，不换算精确发生时刻。</small></div>
    <details v-if="omitted.length"><summary>已跳过 {{omitted.length}} 个档案</summary><ul><li v-for="(item,i) in omitted" :key="i">{{item}}</li></ul></details><p v-if="error" class="import-error" role="alert">{{ error }}</p><p v-if="message" class="import-info" role="status">{{ message }}</p>
    <div v-for="file in previews" :key="file.fileSha256" class="import-file"><h3>{{ file.title }} <small>{{ file.name }} · {{ file.messageCount }} 条 · {{ file.format.toUpperCase() }}</small></h3><p v-if="file.legacyDuplicate||file.chronicleDuplicate" class="import-error">与已导入或待处理档案重复，不能直接再次写入。</p><p v-if="file.externalIdentityConflicts.length" class="import-error">外部消息身份冲突：{{ file.externalIdentityConflicts.slice(0,3).join('、') }}；请先检查重叠来源。</p><p v-for="warning in file.warnings" :key="warning" class="import-warning">{{ warning }}</p><details><summary>查看原始顺序、说话人与时间依据</summary><p v-for="item in file.messages.slice(0,20)" :key="item.ordinal">{{ item.ordinal }} · {{ item.role }} · {{ item.time.original || '时间未知' }} · {{ item.time.precision }}<br/>{{ item.contentPreview }}</p><small v-if="file.messages.length>20">仅显示前 20 条正文预览；全部 {{ file.messages.length }} 条已校验。</small></details></div>
    <details class="import-recent"><summary>阅读已导入的批次</summary><button v-for="item in previous" :key="item.batchId" :disabled="busy" @click="action(()=>load(item.batchId))">{{ item.status==='raw_complete'?'已归档':item.status }} · {{ item.processed }}/{{ item.total }} · {{ item.batchId.slice(0,8) }}</button><p v-if="!previous.length">当前角色暂无批次。</p></details>
    <details class="import-recent" open><summary>按会话和日期查找原始聊天</summary><div class="import-actions"><label>起始日期<input v-model="rawFrom" type="date"/></label><label>结束日期<input v-model="rawTo" type="date"/></label><button v-if="rawListError" @click="loadRawFiles">重新读取会话</button></div><button v-for="item in rawFiles" :key="item.id" :disabled="item.sourceStatus!=='valid'" @click="openRawHit({messageId:0,ordinal:1,fileId:item.id,batchId:item.batch_id,conversationTitle:item.title||'',role:'',text:'',occurredDate:item.first_date})">{{ item.sourceStatus==='valid'?item.title:'来源失效，请刷新或检查' }} · {{ item.first_date||'时间未知' }}—{{ item.last_date||'时间未知' }}<span v-if="item.unverified_local_count"> · 含未核实时区日期</span> · {{ item.cursor }}/{{ item.total }}</button><p v-if="rawListError" role="alert">{{rawListError}}</p><p v-else-if="!rawFiles.length">当前日期范围内没有已归档的聊天。</p><div class="import-actions"><input v-model="rawQuery" placeholder="搜索原始聊天正文" @keyup.enter="searchRaw()"/><button @click="searchRaw()">检索</button></div><p v-for="hit in rawHits" :key="hit.messageId"><button @click="openRawHit(hit)">{{ hit.conversationTitle||'来源标题失效' }} · 第 {{ hit.ordinal }} 条 · {{ hit.occurredDate||'时间未知' }}<span v-if="hit.timePrecision==='local_datetime_unverified'">（时区未核实）</span><br/>{{ hit.text }}</button></p><button v-if="rawSearchNext!==null" @click="searchRaw(true)">加载更多命中</button></details>
    <template v-if="batch"><p v-if="progressError" role="status">{{progressError}} <button :disabled="busy" @click="action(()=>load(batch!.batchId))">查看进度</button></p><div class="import-progress"><strong>原文归档 · {{({preview:'待确认',confirmed:'已确认',running:'进行中',paused:'已暂停',raw_complete:'已完成',failed:'需要处理'} as Record<string,string>)[batch.status]||batch.status}}</strong><span>{{ batch.processed }}/{{ batch.total }} 条消息</span><details><summary>批次详情</summary><small>{{batch.batchId}} · {{batch.status}} · {{batch.processing?.reason}}</small></details></div>
      <button v-if="!batch.processing && batch.status==='raw_complete'" :disabled="busy" @click="action(async()=>{const id=batch!.batchId;await request(path('/batches/'+id+'/prepare-review?'+params()),'POST');await load(id)})">用 ChatGPT 套餐自动整理本批</button>
      <button v-if="batch.processing?.status==='paused' && batch.processing.unsent" :disabled="busy" @click="action(async()=>{const id=batch!.batchId;await request(path('/batches/'+id+'/prepare-review?'+params('&onlyUnsent=true')),'POST');await load(id)})">继续整理尚未发送的 {{batch.processing.unsent}} 段</button>
      <p v-if="batch.processing" role="status">ChatGPT 套餐 · {{({queued:'等待整理',running:'正在自动整理',completed:'整理完成，可进行最终审核',failed:'部分内容待处理',paused:'整理已暂停'} as Record<string,string>)[batch.processing.status]}} · 已保存梗概 {{batch.processing.synopses}} / {{batch.processing.entries}} 份 <span v-if="batch.processing.reason"> · {{processingReason(batch.processing.reason)}}</span></p>
      <details v-if="batch.processing?.exceptions?.length"><summary>查看需要处理的段落（{{batch.processing.exceptions.length}}）</summary>
        <p v-for="(issue,i) in batch.processing.exceptions" :key="i">第 {{issue.ordinal}} 段 · {{issue.state==='unknown'?'请求结果未确认，已暂停。':processingReason(issue.reason||'梗概未通过校验。')}}</p>
      </details>
      <template v-if="batch.status==='preview' && !batch.processing"><p class="import-note">以下是时间间隔建议，不代表已确认的语义话题。可手动调整后逐文件保存。</p><div v-for="file in batch.files" :key="file.id" class="import-file"><h3>{{ file.name }}</h3><div v-for="segment in batch.segments.filter(x=>x.file_id===file.id)" :key="segment.id" class="import-boundary"><label>起始序号<input v-model.number="segment.start_ordinal" type="number" min="1" @input="boundaryDirty=true"/></label><label>结束序号<input v-model.number="segment.end_ordinal" type="number" min="1" @input="boundaryDirty=true"/></label><label>建议标题<input v-model="segment.title" maxlength="200" @input="boundaryDirty=true"/></label><button @click="split(segment)">拆分</button><button @click="merge(segment)">并入下一段</button></div><button :disabled="busy" @click="saveBoundaries(file)">保存此文件边界</button></div><div class="import-actions"><button :disabled="busy||boundaryDirty" @click="action(()=>organize(batch!.batchId))">导入并整理为分段经历</button><button :disabled="busy||boundaryDirty" @click="confirm">仅写入原文归档</button><span v-if="boundaryDirty">先保存边界修改</span><button :disabled="busy" @click="discard">清理暂存批次</button></div></template>
      <div v-else class="import-actions"><button v-if="['confirmed','running'].includes(batch.status)" :disabled="busy" @click="run">连续处理 RAW</button><button v-if="batch.status==='running'" :disabled="busy" @click="pause">暂停</button><button v-if="batch.status==='paused'" :disabled="busy" @click="resume">恢复</button><button v-if="batch.status==='raw_complete'" :disabled="busy" @click="startIndex">显式建立 M1 索引</button><button v-if="indexJob" :disabled="busy" @click="indexStep">处理下一批索引 · {{ indexState }}</button><button v-if="batch.status!=='failed' && batch.files.some(f=>!batch!.segments.some(s=>s.file_id===f.id && s.entry_id))" :disabled="busy" @click="action(()=>organize(batch!.batchId))">继续自动分段整理</button></div>
      <section v-if="drafts.length" aria-label="分段经历"><h3>分段经历 · {{ drafts.length }} 份</h3>
        <button v-if="drafts.length" :disabled="busy||dirty||reviewBusy" @click="emit('review',drafts.map(item=>item.entryId))">审核本批 {{drafts.length}} 段经历</button>
        <details v-for="draft in drafts" :key="draft.entryId" class="import-file" @toggle="($event.currentTarget as HTMLDetailsElement).open && opened.add(draft.entryId)"><summary>{{ draft.titleSuggestion }} · 阅读</summary><ChronicleEditor ref="editors" @revised="emit('revised',$event)" @saved="emit('changed')" v-if="opened.has(draft.entryId)" :character-id="characterId" :entry-id="draft.entryId" /></details>
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
