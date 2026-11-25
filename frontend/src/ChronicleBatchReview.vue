<script setup lang="ts">
import {computed,onBeforeUnmount,ref,watch} from 'vue'
type Card={entryId:string; version?:string; token?:string; title?:string; synopsis?:string; dates?:string[]; dateBases?:string[]; narrativeReady?:boolean; narrativeId?:string; published?:boolean; reasons:string[];labels?:Record<string,unknown[]>}
const props=defineProps<{cid:number;dirty:boolean}>(), emit=defineEmits<{published:[]}>()
const open=ref(false),busy=ref(false),query=ref(''),pendingOnly=ref(true),error=ref('')
const cards=ref<Card[]>([]),chosen=ref<string[]>([]),body=ref(''),expanded=ref('')
const key='chronicle.batchReview.results.'+props.cid
const results=ref<Record<string,string>>({})
try {results.value=JSON.parse(sessionStorage.getItem(key)||'{}')} catch {}
const visible=computed(()=>cards.value.filter(c=>(!pendingOnly.value||!c.published)&&(!query.value||c.title?.includes(query.value))))
const selected=computed(()=>visible.value.filter(c=>chosen.value.includes(c.entryId)))
watch([query,pendingOnly],()=>{chosen.value=[];body.value='';expanded.value=''})
const controller=new AbortController()
onBeforeUnmount(()=>controller.abort())
async function request(path:string,options:RequestInit={}) {
  const r=await fetch('/api/memory-v2/workspace/'+path,{...options,signal:controller.signal,cache:'no-store'})
  if(!r.ok){if(r.status===401||r.status===403){results.value={};sessionStorage.removeItem(key);cards.value=[];chosen.value=[];body.value='';expanded.value=''}
    const message=(await r.json().catch(()=>({}))).detail||'请求失败 '+r.status
    throw Object.assign(new Error(message),{status:r.status})}
  return r.json()
}
async function load(){
  busy.value=true; error.value='';chosen.value=[];cards.value=[];expanded.value='';body.value=''
  try {let after=0;const all:Card[]=[]
    while(true){const page=await request('review-selection?characterId='+props.cid+'&after='+after)
      all.push(...page.items);if(page.nextAfter===null)break;after=page.nextAfter}
    cards.value=all
  }catch(e){error.value=String(e)}finally{busy.value=false}
}
async function show(c:Card){
  if(expanded.value===c.entryId){expanded.value='';body.value='';return}
  expanded.value=c.entryId;body.value='';busy.value=true
  try{const r=await request('entries/'+c.entryId+'?characterId='+props.cid)
    if(r.entry.versionId!==c.version||r.narrative?.id!==c.narrativeId)throw Error('版本已变化，请刷新清单')
    body.value=r.narrative.body
  }catch(e){error.value=String(e)}finally{busy.value=false}
}
async function publish(){
  const items=[...selected.value]
  if(!items.length||props.dirty||busy.value||!window.confirm('确认并发布所选 '+items.length+' 条中的就绪内容？有异常的条目将跳过。'))return
  busy.value=true;error.value=''
  let changed=false
  try{for(const c of items){
    if(controller.signal.aborted)break
    let outcome=''
    if(c.reasons.length||c.published)outcome='跳过 · '+(c.published?'已发布':c.reasons.join('、'))
    else try{const r=await request('review-selection/'+c.entryId+'/publish',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({characterId:props.cid,expectedVersion:c.version,previewToken:c.token})})
      outcome=r.status==='published'?'成功 · 已发布':'跳过 · '+r.reason
      if(r.status==='published'){c.published=true;changed=true}
    }catch(e){
      const reason=e instanceof Error?e.message:String(e)
      outcome=e instanceof Error && 'status' in e && Number(e.status)<500?'失败 · '+reason:'未获成功回执 · 请刷新核对。'+reason
    }
    results.value[c.entryId]=outcome
    try{sessionStorage.setItem(key,JSON.stringify(results.value))}catch{error.value='浏览器未能保存本次结果，请在关闭页面前核对'}
  }}finally{busy.value=false;chosen.value=[];if(changed)emit('published')}
}
</script>
<template>
<section class="period-card">
<button :disabled="busy" @click="open=!open;if(open)load()">{{open?'收起批量审核':'批量审核与发布'}}</button>
<template v-if="open">
<h2>批量审核与发布</h2>
<label>筛选标题 <input v-model.trim="query" :disabled="busy"></label>
<label><input type="checkbox" v-model="pendingOnly" :disabled="busy">只看待发布</label>
<button :disabled="busy" @click="load">刷新清单</button>
<p v-if="error" role="alert">{{error}}</p>
<button :disabled="busy" @click="chosen=visible.map(c=>c.entryId)">全选当前筛选结果（{{visible.length}}）</button>
<button :disabled="busy" @click="chosen=[]">清除选择</button>
<p>已选 {{selected.length}} 条。确认范围包括标题、梗概、完整精修及已填写的分类、标签和原文关键词。</p>
<article v-for="c in visible" :key="c.entryId">
<label><input type="checkbox" v-model="chosen" :value="c.entryId" :disabled="busy"> {{c.title||'不可读取的条目'}}</label>
<p>{{c.dates?.length?c.dates.join('、'):'日期未核实'}} · {{c.dateBases?.includes('owner_entry_date')?'人工修订':c.dateBases?.includes('source_local_day_unverified_zone')?'消息日期（时区未核实）':'来源日期'}} · 精修{{c.narrativeReady?'已就绪':'未就绪'}}</p>
<p>{{c.synopsis||'梗概尚未就绪'}}</p>
<details v-if="c.labels"><summary>查看分类与检索词</summary><p v-for="(vals,name) in c.labels" :key="name">{{({categories:'分类',tags:'标签',keywords:'关键词'} as Record<string,string>)[name]}}：{{vals?.map((v:any)=>typeof v==='string'?v:v.text||v.name||v.id).join('、')||'未设置'}}</p></details>
<p v-if="c.reasons.length">跳过原因 · {{c.reasons.join('、')}}</p>
<button v-if="c.narrativeReady" :disabled="busy" @click="show(c)">{{expanded===c.entryId?'收起正文':'展开完整精修'}}</button>
<p class="prose" v-if="expanded===c.entryId">{{body}}</p>
</article>
<p v-if="dirty">请先保存或取消编辑器中的修改。</p>
<button class="c-primary" :disabled="busy||dirty||!selected.length" @click="publish">{{busy?'处理中…':'确认并发布所选条目'}}</button>
<section v-if="Object.keys(results).length" aria-live="polite"><h3>操作结果</h3><p v-for="(v,id) in results" :key="id">{{cards.find(c=>c.entryId===id)?.title||id.slice(0,8)}} · {{v}}</p></section>
</template></section>
</template>
<style scoped>
article{border-top:1px solid var(--ui-line);padding:16px 0}label,button{margin:6px;padding:6px}article p,.prose{white-space:pre-wrap;overflow-wrap:anywhere;max-width:78ch;line-height:1.75}.prose{font-size:17px}
</style>
