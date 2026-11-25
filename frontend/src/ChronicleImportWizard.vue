<script setup lang="ts">
import ChronicleEditor from './ChronicleEditor.vue'
import { ref, watch } from 'vue'

type Message = {ordinal:number; role:string; contentPreview:string|null; externalMessageId:string|null; time:{original:string|null; precision:string; occurredAt:string|null; occurredDate:string|null}}
type Preview = {name:string; format:string; title:string; messageCount:number; fileSha256:string; legacyDuplicate:boolean; chronicleDuplicate:boolean; externalIdentityConflicts:string[]; warnings:string[]; messages:Message[]}
type Segment = {id:string; file_id:string; ordinal:number; start_ordinal:number; end_ordinal:number; title:string; entry_id:string|null}
type FileState = {id:string; name:string; cursor:number; total:number; status:string; conversation_id:number|null; conversion_note:string}
type Batch = {batchId:string; status:string; processed:number; total:number; planVersion:number; previewFingerprint:string|null; files:FileState[]; segments:Segment[]; errorCode:string|null}
type Draft = {entryId:string; segmentId:string; titleSuggestion:string; sourceRefs:{kind:string;id:string}[];status:string}
type RawItem = {messageId:number; ordinal:number; role:string; content:string; originalTime:string|null; occurredDate:string|null;timePrecision:string;conversionNote:string}
type RawFile = {id:string;batch_id:string;name:string;title:string|null;status:string;sourceStatus:string;cursor:number;total:number;first_date:string|null;last_date:string|null;unverified_local_count:number}
type RawHit = {messageId:number;ordinal:number;fileId:string;batchId:string;conversationTitle:string|null;role:string;text:string;occurredDate:string|null;timePrecision?:string}

const props = defineProps<{characterId:number}>()
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
  const requestedCharacter = props.characterId
  const response = await fetch(path,{method,body,credentials:'same-origin',cache:'no-store',
    headers:body instanceof FormData || !body ? undefined : {'Content-Type':'application/json'}})
  if (!response.ok) {
    const detail = await response.json().catch(()=>({detail:`HTTP ${response.status}`})) as {detail?:string}
    throw new Error(typeof detail.detail === 'string' ? detail.detail : `HTTP ${response.status}`)
  }
  const result = await response.json() as T
  if (props.characterId !== requestedCharacter) throw new Error('角色已切换，请刷新当前角色资料')
  return result
}
function path(suffix:string){return `/api/memory-v2/imports${suffix}`}
function params(extra=''){return `characterId=${props.characterId}${extra}`}
async function action(work:()=>Promise<void>){if(busy.value)return;busy.value=true;error.value='';message.value='';try{await work()}catch(exc){error.value=exc instanceof Error?exc.message:'操作失败'}finally{busy.value=false}}
function choose(event:Event){files.value=Array.from((event.target as HTMLInputElement).files||[]);previews.value=[];previewFingerprint.value=''}
function synthetic(){const data={format:'yosaki-conversation',version:2,title:'合成旧聊天',conversation:{id:'synthetic-archive-1'},messages:[
  {id:'s1',role:'user',content:'如果明天下雨，就先去图书馆。',created_at:'2024-05-06T10:00:00+08:00'},
  {id:'s2',role:'assistant',content:'うん、先に図書館へ行こう。',created_at:'2024-05-06T10:03:00+08:00'},
  {id:'s3',role:'user',content:'不是明天，是今天。',created_at:'2024-05-06T10:35:00+08:00'}]}
  files.value=[new File([JSON.stringify(data)],'synthetic-history.json',{type:'application/json'})];previews.value=[];previewFingerprint.value='';message.value='已载入合成聊天，尚未写入 RAW。'
}
function form(){const body=new FormData();body.append('characterId',String(props.characterId));files.value.forEach(file=>body.append('files',file));if(confirmTimezone.value)body.append('confirmedOffsetMinutes',String(timezoneOffset.value));return body}
async function preview(){await action(async()=>{if(!files.value.length)throw new Error('请先选择文件');const result=await request<{files:Preview[];previewFingerprint:string}>(path('/preview'),'POST',form());previews.value=result.files;previewFingerprint.value=result.previewFingerprint;message.value='只读预览完成；尚未写入消息。'})}
async function prepare(){await action(async()=>{const body=form();if(previewFingerprint.value)body.append('expectedPreviewFingerprint',previewFingerprint.value);const result=await request<{batchId:string}>(path('/batches'),'POST',body);await load(result.batchId);previews.value=[];previewFingerprint.value='';files.value=[];message.value='预览批次已暂存。确认前可调整时间边界；尚未写入 RAW。'})}
async function load(id:string){batch.value=await request<Batch>(path(`/batches/${encodeURIComponent(id)}?${params()}`));boundaryDirty.value=false;drafts.value=[];raw.value=[];rawFile.value='';rawNext.value=null;rawSnapshot.value=null}
async function loadPrevious(){try{previous.value=(await request<{items:typeof previous.value}>(path(`/batches?${params()}`))).items}catch{previous.value=[]}}
async function loadRawFiles(){try{rawFiles.value=(await request<{items:RawFile[]}>(path(`/raw-files?${params(`&fromDate=${rawFrom.value}&toDate=${rawTo.value}`)}`))).items}catch{rawFiles.value=[]}}
async function searchRaw(more=false){await action(async()=>{if(rawQuery.value.trim().length<2)throw new Error('至少输入两个有效字符');const previous=more?[...rawHits.value]:[];const after=more?rawSearchNext.value||0:0;const snapshot=more?rawSearchSnapshot.value:null;rawHits.value=[];rawSearchNext.value=null;rawSearchSnapshot.value=null;const result=await request<{items:RawHit[];nextAfter:number|null;snapshot:string|null}>(path(`/raw-search?${params(`&q=${encodeURIComponent(rawQuery.value)}&afterId=${after}${snapshot?`&snapshot=${encodeURIComponent(snapshot)}`:''}`)}`));rawHits.value=more?[...previous,...result.items]:result.items;rawSearchNext.value=result.nextAfter;rawSearchSnapshot.value=result.snapshot})}
async function openRawHit(hit:RawHit){await action(async()=>{await load(hit.batchId)});await loadRaw(hit.fileId,false,hit.ordinal-1)}
watch(()=>props.characterId,()=>{batch.value=null;files.value=[];previews.value=[];previewFingerprint.value='';drafts.value=[];raw.value=[];rawHits.value=[];rawFile.value='';rawNext.value=null;rawSnapshot.value=null;rawSearchNext.value=null;rawSearchSnapshot.value=null;indexJob.value=null;metadata.value={};void loadPrevious();void loadRawFiles()},{immediate:true})
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
  for(const file of batch.value.files){
    const base=`/api/memory-v2/workspace/imports/${batch.value.batchId}/${file.id}/boundaries`
    const plan=await request<{sourceSnapshot:string;segments:{start:number;end:number;title:string}[]}>(`${base}?${params()}`)
    const result=await request<{items:{entryId:string;start:number;end:number;title:string}[]}>(base,'POST',JSON.stringify({characterId:props.characterId,sourceSnapshot:plan.sourceSnapshot,segments:plan.segments}))
    for(const item of result.items) found.push({entryId:item.entryId,segmentId:'',titleSuggestion:item.title,sourceRefs:[],status:'draft_needs_metadata'})
  }
  drafts.value=found;message.value='已保存完整来源范围。打开条目即可整理、保存和确认正文，不需要填写内部版本 ID。'
})}
async function saveMetadata(draft:Draft){await action(async()=>{const item=metadata.value[draft.entryId];if(!item?.synopsis.trim())throw new Error('请填写有来源支持的单经历梗概');const result=await request<{versionId:string;metadataHash:string;sourceHash:string}>(`/api/memory-v2/entries/${draft.entryId}/versions`,'POST',JSON.stringify({characterId:props.characterId,sourceRefs:draft.sourceRefs,title:item.title,keywords:item.keywords.split(/[,，\s]+/).filter(Boolean),tags:item.tags.split(/[,，\s]+/).filter(Boolean),synopsis:item.synopsis,narrativeVersionId:item.narrativeVersionId||null}));Object.assign(item,result,{status:'草稿待审核'});message.value='元数据新版本已保存，尚未审核或激活。'})}
async function review(draft:Draft){await action(async()=>{const item=metadata.value[draft.entryId];if(!item.versionId||!item.metadataHash||!item.sourceHash)throw new Error('请先保存版本');if(!item.narrativeVersionId)throw new Error('P1-A 激活门槛要求已审核 NARRATIVE；当前 RAW 草稿可先保留。');await request(`/api/memory-v2/entries/${draft.entryId}/versions/${item.versionId}/review`,'POST',JSON.stringify({characterId:props.characterId,metadataHash:item.metadataHash,sourceHash:item.sourceHash}));item.status='已人工审核，待激活';message.value='审核只适用于当前版本与来源快照。'})}
async function activate(draft:Draft){await action(async()=>{const item=metadata.value[draft.entryId];if(!item.versionId)throw new Error('尚无版本');await request(`/api/memory-v2/entries/${draft.entryId}/versions/${item.versionId}/activate?${params()}`,'POST');item.status='已激活';message.value='条目已激活；可刷新年历查看。'})}
</script>

<template>
  <section class="import-workbench" aria-label="历史聊天导入">
    <div class="import-heading"><h2>历史聊天导入</h2></div>
    <p class="import-note">预览后需确认导入。原始对话保存在当前工作区的归档中；整理、确认并发布后，符合资格的内容才可被阿夜查阅。</p>
    <div class="import-actions"><input type="file" multiple accept=".json,.md,.txt,.zip" @change="choose" aria-label="选择聊天档案"/><button @click="synthetic">载入合成示例</button><button :disabled="busy||!files.length" @click="preview">只读预览</button><button :disabled="busy||!previews.length||previews.some(x=>x.legacyDuplicate||x.chronicleDuplicate||x.externalIdentityConflicts.length)" @click="prepare">暂存预览批次</button></div>
    <div class="import-actions"><label><input v-model="confirmTimezone" type="checkbox"/> 我确认无时区时间使用此固定 UTC 偏移</label><label v-if="confirmTimezone">偏移分钟 <input v-model.number="timezoneOffset" type="number" min="-840" max="840"/></label><small>未确认时保留时间原文，不换算精确发生时刻。</small></div>
    <p v-if="error" class="import-error" role="alert">{{ error }}</p><p v-if="message" class="import-info" role="status">{{ message }}</p>
    <div v-for="file in previews" :key="file.fileSha256" class="import-file"><h3>{{ file.title }} <small>{{ file.name }} · {{ file.messageCount }} 条 · {{ file.format.toUpperCase() }}</small></h3><p v-if="file.legacyDuplicate||file.chronicleDuplicate" class="import-error">与已导入或待处理档案重复，不能直接再次写入。</p><p v-if="file.externalIdentityConflicts.length" class="import-error">外部消息身份冲突：{{ file.externalIdentityConflicts.slice(0,3).join('、') }}；请先检查重叠来源。</p><p v-for="warning in file.warnings" :key="warning" class="import-warning">{{ warning }}</p><details><summary>查看原始顺序、说话人与时间依据</summary><p v-for="item in file.messages.slice(0,20)" :key="item.ordinal">{{ item.ordinal }} · {{ item.role }} · {{ item.time.original || '时间未知' }} · {{ item.time.precision }}<br/>{{ item.contentPreview }}</p><small v-if="file.messages.length>20">仅显示前 20 条正文预览；全部 {{ file.messages.length }} 条已校验。</small></details></div>
    <details class="import-recent"><summary>恢复已有导入批次</summary><button v-for="item in previous" :key="item.batchId" @click="load(item.batchId)">{{ item.status }} · {{ item.processed }}/{{ item.total }} · {{ item.batchId.slice(0,8) }}</button><p v-if="!previous.length">当前角色暂无批次。</p></details>
    <details class="import-recent" open><summary>待整理 RAW 档案 · 按会话和日期浏览</summary><div class="import-actions"><label>起始日期<input v-model="rawFrom" type="date"/></label><label>结束日期<input v-model="rawTo" type="date"/></label><button @click="loadRawFiles">筛选会话</button></div><button v-for="item in rawFiles" :key="item.id" :disabled="item.sourceStatus!=='valid'" @click="openRawHit({messageId:0,ordinal:1,fileId:item.id,batchId:item.batch_id,conversationTitle:item.title||'',role:'',text:'',occurredDate:item.first_date})">{{ item.sourceStatus==='valid'?item.title:'来源失效，请刷新或检查' }} · {{ item.first_date||'时间未知' }}—{{ item.last_date||'时间未知' }}<span v-if="item.unverified_local_count"> · 含未核实时区日期</span> · {{ item.cursor }}/{{ item.total }}</button><p v-if="!rawFiles.length">尚无已写入的 RAW；预览不会出现在这里。</p><div class="import-actions"><input v-model="rawQuery" placeholder="检索已索引的 RAW 正文" @keyup.enter="searchRaw()"/><button @click="searchRaw()">检索</button></div><p v-for="hit in rawHits" :key="hit.messageId"><button @click="openRawHit(hit)">{{ hit.conversationTitle||'来源标题失效' }} · 第 {{ hit.ordinal }} 条 · {{ hit.occurredDate||'时间未知' }}<span v-if="hit.timePrecision==='local_datetime_unverified'">（时区未核实）</span><br/>{{ hit.text }}</button></p><button v-if="rawSearchNext!==null" @click="searchRaw(true)">加载更多命中</button></details>
    <template v-if="batch"><div class="import-progress"><strong>批次 {{ batch.batchId.slice(0,8) }} · {{ batch.status }}</strong><span>RAW {{ batch.processed }}/{{ batch.total }}</span></div>
      <template v-if="batch.status==='preview'"><p class="import-note">以下是时间间隔建议，不代表已确认的语义话题。可手动调整后逐文件保存。</p><div v-for="file in batch.files" :key="file.id" class="import-file"><h3>{{ file.name }}</h3><div v-for="segment in batch.segments.filter(x=>x.file_id===file.id)" :key="segment.id" class="import-boundary"><label>起始序号<input v-model.number="segment.start_ordinal" type="number" min="1" @input="boundaryDirty=true"/></label><label>结束序号<input v-model.number="segment.end_ordinal" type="number" min="1" @input="boundaryDirty=true"/></label><label>建议标题<input v-model="segment.title" maxlength="200" @input="boundaryDirty=true"/></label><button @click="split(segment)">拆分</button><button @click="merge(segment)">并入下一段</button></div><button :disabled="busy" @click="saveBoundaries(file)">保存此文件边界</button></div><div class="import-actions"><button :disabled="busy||boundaryDirty" @click="confirm">确认写入 RAW</button><span v-if="boundaryDirty">先保存边界修改</span><button :disabled="busy" @click="discard">清理暂存批次</button></div></template>
      <div v-else class="import-actions"><button v-if="['confirmed','running'].includes(batch.status)" :disabled="busy" @click="run">连续处理 RAW</button><button v-if="batch.status==='running'" :disabled="busy" @click="pause">暂停</button><button v-if="batch.status==='paused'" :disabled="busy" @click="resume">恢复</button><button v-if="batch.status==='raw_complete'" :disabled="busy" @click="startIndex">显式建立 M1 索引</button><button v-if="indexJob" :disabled="busy" @click="indexStep">处理下一批索引 · {{ indexState }}</button><button v-if="batch.status==='raw_complete'" :disabled="busy" @click="draftConfirmation=batch.batchId">建立／查看稳定草稿条目</button></div>
      <div v-for="file in batch.files" :key="file.id" class="import-file"><h3>{{ file.name }} <small>{{ file.status }} · {{ file.cursor }}/{{ file.total }} · {{ file.conversion_note }}</small></h3><button :disabled="busy||!file.cursor" @click="loadRaw(file.id)">查看待整理 RAW</button><template v-if="rawFile===file.id"><div v-for="item in raw" :key="item.messageId" class="import-raw"><small>{{ item.ordinal }} · {{ item.role }} · {{ item.originalTime||item.occurredDate||'发生时间未知' }} · {{ item.timePrecision }}</small><p>{{ item.content }}</p></div><button v-if="rawNext!==null" :disabled="busy" @click="loadRaw(file.id,true)">继续读取 RAW</button></template></div>
      <div v-if="draftConfirmation === batch.batchId" class="import-actions" role="group" aria-label="确认来源范围">
        <p>按当前消息范围建立草稿。内容确认和发布需另外操作。</p>
        <button :disabled="busy" @click="makeDrafts">确认范围并建立草稿</button>
        <button :disabled="busy" @click="draftConfirmation=null">取消</button>
      </div>
      <details v-for="draft in drafts" :key="draft.entryId" class="import-file"><summary>{{ draft.titleSuggestion }} · 打开记忆条目</summary><ChronicleEditor :character-id="characterId" :entry-id="draft.entryId" /></details>
    </template>
  </section>
</template>
