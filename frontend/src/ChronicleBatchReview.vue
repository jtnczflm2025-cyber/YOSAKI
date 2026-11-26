<script setup lang="ts">
import {computed,onBeforeUnmount,ref,watch} from 'vue'
import type {ReviewChange} from './chronicleReviewChange'
type Card={entryId:string; version?:string; token?:string; title?:string; synopsis?:string; dates?:string[]; dateBases?:string[]; narrativeReady?:boolean; narrativeId?:string; published?:boolean; reasons:string[];labels?:Record<string,unknown[]>}
const props=defineProps<{cid:number;dirty:boolean;revision:number;changes:Record<string,ReviewChange>}>(), emit=defineEmits<{published:[]}>()
const open=ref(false),busy=ref(false),query=ref(''),pendingOnly=ref(true),error=ref('')
const cards=ref<Card[]>([]),chosen=ref<string[]>([]),bodies=ref<Record<string,string>>({})
const termNames=ref<Record<string,string>>({})
const labelTitles={categories:'分类',tags:'标签',keywords:'原文关键词'} as const
function names(c:Card,field:keyof typeof labelTitles){
  return (c.labels?.[field]||[]).map((v:any)=>typeof v==='string'?v:v.text||termNames.value[v.id]||'词项已变化')
}
const coverage=computed(()=>Object.entries(labelTitles).map(([key,label])=>
  label+' '+visible.value.filter(c=>c.labels?.[key]?.length).length+'/'+visible.value.length).join(' · '))
const key='chronicle.batchReview.results.'+props.cid
const results=ref<Record<string,string>>({})
try {results.value=JSON.parse(sessionStorage.getItem(key)||'{}')} catch {}
const scope=ref<Set<string>|null>(null)
const visible=computed(()=>cards.value.filter(c=>(!scope.value||scope.value.has(c.entryId))&&(!pendingOnly.value||!c.published)&&(!query.value||c.title?.includes(query.value))))
const exceptionsOnly=ref(false)
const displayed=computed(()=>exceptionsOnly.value?visible.value.filter(c=>c.reasons.length):visible.value)
const ready=computed(()=>visible.value.filter(c=>!c.published&&!c.reasons.length))
const selected=computed(()=>displayed.value.filter(c=>chosen.value.includes(c.entryId)))
watch([query,pendingOnly,exceptionsOnly],()=>{chosen.value=[];bodies.value={}})
let refreshPending=false
watch(()=>props.revision,()=>{
  if(!open.value)return
  if(busy.value)refreshPending=true
  else void load()
})
watch(busy,value=>{if(!value && refreshPending){refreshPending=false;void load()}})
const controller=new AbortController()
onBeforeUnmount(()=>controller.abort())
async function request(path:string,options:RequestInit={}) {
  const r=await fetch('/api/memory-v2/workspace/'+path,{...options,signal:controller.signal,cache:'no-store'})
  if(!r.ok){if(r.status===401||r.status===403){results.value={};sessionStorage.removeItem(key);cards.value=[];chosen.value=[];bodies.value={}}
    const message=(await r.json().catch(()=>({}))).detail||'请求失败 '+r.status
    throw Object.assign(new Error(message),{status:r.status})}
  return r.json()
}
async function load(){
  busy.value=true; error.value='';chosen.value=[];cards.value=[];bodies.value={}
  try {const catalog=await request('vocabulary?characterId='+props.cid)
    termNames.value=Object.fromEntries(catalog.items.map((item:{vocabulary_id:string;name:string})=>[item.vocabulary_id,item.name]))
    let after=0;const all:Card[]=[]
    while(true){const page=await request('review-selection?characterId='+props.cid+'&after='+after)
      all.push(...page.items);if(page.nextAfter===null)break;after=page.nextAfter}
    cards.value=all
  }catch(e){error.value=String(e)}finally{busy.value=false}
}
function changedFields(c:Card){
  const change=props.changes[c.entryId]
  return change && !c.reasons.length && change.characterId===props.cid && change.version===c.version &&
    change.narrativeId===(c.narrativeId??null)?change.fields:[]
}
async function reviewBatch(ids:string[],selectAll=false){
  if(busy.value)return
  scope.value=new Set(ids);query.value='';pendingOnly.value=true;exceptionsOnly.value=false;open.value=true
  await load()
  if(selectAll)chosen.value=ready.value.map(c=>c.entryId)
}
defineExpose({reviewBatch,busy})
async function showBodies(items:Card[]){
  busy.value=true;error.value=''
  try{
    for(const c of items){
      if(c.entryId in bodies.value || !c.narrativeReady)continue
      try{
        const r=await request('entries/'+c.entryId+'?characterId='+props.cid)
        if(r.entry.versionId!==c.version||r.narrative?.id!==c.narrativeId)throw Error('版本已变化，请刷新清单')
        bodies.value[c.entryId]=r.narrative.body
      }catch(e){
        if(controller.signal.aborted)break
        error.value+=(error.value?'；':'')+(c.title||'经历')+'：'+String(e)
        if(!cards.value.length)break
      }
    }
  }finally{busy.value=false}
}
function toggleBody(c:Card){
  if(c.entryId in bodies.value)delete bodies.value[c.entryId]
  else void showBodies([c])
}
const publishing=ref(false),stopAfterCurrent=ref(false),completedItems=ref(0),totalItems=ref(0)
async function publish(){
  const items=[...selected.value]
  if(!items.length||props.dirty||busy.value||!window.confirm('确认并发布所选 '+items.length+' 条中的就绪内容？有异常的条目将跳过。'))return
  busy.value=true;error.value=''
  publishing.value=true;stopAfterCurrent.value=false;completedItems.value=0;totalItems.value=items.length
  let changed=false
  let reconcile=false
  try{for(const c of items){
    if(controller.signal.aborted||stopAfterCurrent.value)break
    let outcome=''
    if(c.reasons.length||c.published)outcome='跳过 · '+(c.published?'已发布':c.reasons.join('、'))
    else try{const r=await request('review-selection/'+c.entryId+'/publish',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({characterId:props.cid,expectedVersion:c.version,previewToken:c.token})})
      outcome=r.status==='published'?'成功 · 已发布':'跳过 · '+r.reason
      if(r.status==='published'){c.published=true;changed=true}
    }catch(e){
      const reason=e instanceof Error?e.message:String(e)
      reconcile=true
      if(e instanceof Error && 'status' in e && [401,403].includes(Number(e.status))){
        error.value='访问权限已变化，已停止后续发布。'
        break
      }
      outcome=e instanceof Error && 'status' in e && Number(e.status)<500?'失败 · '+reason:'未获成功回执 · 请以服务端清单为准。'+reason
    }
    if(controller.signal.aborted)break
    completedItems.value++
    results.value[c.entryId]=outcome
    try{sessionStorage.setItem(key,JSON.stringify(results.value))}catch{error.value='浏览器未能保存本次结果，请在关闭页面前核对'}
  }}finally{publishing.value=false;busy.value=false;chosen.value=[];if(changed)emit('published');else if(reconcile&&!controller.signal.aborted)await load()}
}
</script>
<template>
<section class="period-card">
<button :disabled="busy" @click="open=!open;if(open)load()">{{open?'收起批量审核':'批量审核与发布'}}</button>
<template v-if="open">
<h2>批量审核与发布</h2><p v-if="scope" role="status">仅显示这批导入的经历。<button :disabled="busy" @click="scope=null;chosen=[];bodies={}">查看全部经历</button></p>
<label>筛选标题 <input v-model.trim="query" :disabled="busy"></label>
<label><input type="checkbox" v-model="pendingOnly" :disabled="busy">只看待发布</label>
<button :disabled="busy" @click="load">刷新清单</button>
<p v-if="error" role="alert">{{error}}</p>
<p v-if="visible.length">{{coverage}}</p>
<p>可发布 {{ready.length}} 条 · 需处理 {{visible.filter(c=>c.reasons.length).length}} 条。</p>
<label><input type="checkbox" v-model="exceptionsOnly" :disabled="busy">只看需处理的条目</label>
<button class="c-primary" v-if="!exceptionsOnly" :disabled="busy||!ready.length" @click="chosen=ready.map(c=>c.entryId)">一键全选{{scope?'本批':'当前筛选'}}可发布条目（{{ready.length}}）</button>
<button :disabled="busy||!displayed.length" @click="chosen=displayed.map(c=>c.entryId)">全选当前列表供阅读</button>
<button :disabled="busy" @click="chosen=[]">清除选择</button>
<p>已选 {{selected.length}} 条。确认范围包括标题、梗概、完整精修及已填写的分类、标签和原文关键词。</p>
<button :disabled="busy||!selected.some(c=>c.narrativeReady)" @click="showBodies(selected)">连续阅读所选精修</button>
<button v-if="Object.keys(bodies).length" :disabled="busy" @click="bodies={}">收起全部正文</button>
<article v-for="c in displayed" :key="c.entryId">
<label><input type="checkbox" v-model="chosen" :value="c.entryId" :disabled="busy||c.published||!!c.reasons.length"> {{c.title||'不可读取的条目'}}</label>
<p>{{c.dates?.length?c.dates.join('、'):'日期未核实'}} · {{c.dateBases?.includes('owner_entry_date')?'人工修订':c.dateBases?.includes('source_local_day_unverified_zone')?'消息日期（时区未核实）':'来源日期'}} · 精修{{c.narrativeReady?'已就绪':'未就绪'}}</p>
<p class="prose">{{c.synopsis||'梗概尚未就绪'}}</p>
<details v-if="changedFields(c).length"><summary>对照本页最近保存的修改</summary>
<section v-for="field in changedFields(c)" :key="field.label"><h3>{{field.label}}</h3><small>修改前</small><p class="prose">{{field.before||'空白'}}</p><small>本次保存</small><p class="prose">{{field.after||'空白'}}</p></section></details>
<div v-if="c.labels"><p v-for="(label,key) in labelTitles" :key="key">{{label}}：{{names(c,key).join('、')||'暂无候选'}}</p></div>
<p v-if="c.reasons.length">跳过原因 · {{c.reasons.join('、')}}</p>
<button v-if="c.narrativeReady" :disabled="busy" @click="toggleBody(c)">{{c.entryId in bodies?'收起正文':'展开完整精修'}}</button>
<p class="prose" v-if="c.entryId in bodies">{{bodies[c.entryId]}}</p>
</article>
<p v-if="publishing || (stopAfterCurrent && completedItems<totalItems)" role="status">{{publishing?'正在处理':'已停止后续发布'}} · {{completedItems}} / {{totalItems}} 条</p>
<button v-if="publishing" :disabled="stopAfterCurrent" @click="stopAfterCurrent=true">处理完当前一条后停止</button>
<p v-if="dirty">请先保存或取消编辑器中的修改。</p>
<button class="c-primary" :disabled="busy||dirty||!selected.some(c=>!c.published&&!c.reasons.length)" @click="publish">{{busy?'处理中…':'确认并发布所选条目'}}</button>
<section v-if="Object.keys(results).length" aria-live="polite"><h3>操作结果</h3><p v-for="(v,id) in results" :key="id">{{cards.find(c=>c.entryId===id)?.title||id.slice(0,8)}} · {{v}}</p></section>
</template></section>
</template>
<style scoped>
article{border-top:1px solid var(--ui-line);padding:16px 0}label,button{margin:6px;padding:6px}article p,.prose{white-space:pre-wrap;overflow-wrap:anywhere;max-width:min(78ch,860px);line-height:1.75}.prose{font-size:17px}
</style>
