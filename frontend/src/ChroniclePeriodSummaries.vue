<script setup lang="ts">
import { computed,onBeforeUnmount,ref,watch } from 'vue'
type Period={period:string;level:string;count:number;ready:number;status:string;body:string|null;reason:string|null;members?:{id:string;title:string}[]}
const emit=defineEmits<{(event:'open-entry',id:string):void}>()
const props=defineProps<{cid:number;period?:string|null;revision:string}>()
const rows=ref<Period[]>([]),key=ref(''),enabled=ref(false),busy=ref(false),error=ref(''),configured=ref(false)
const bounded=ref(false), recall=ref(false), scopeFuture=ref(false), allowRecall=ref(false)
const check=ref<{lastCheck?:string;nextCheck?:string}>({})
const budget=ref<{perTask?:number;perDay?:number;total?:number;spent?:number;reserved?:number;today?:number;endsAt?:string}>({})
const days=ref(30), perTask=ref(0.15), perDay=ref(1), total=ref(10)
const pending=computed(()=>rows.value.filter(r=>!r.body).length)
const dateText=(s?:string)=>s?new Date(s).toLocaleString('zh-HK',{timeZone:'Asia/Hong_Kong'}):'尚无记录'
const usd=(v?:number)=>v==null?'未设置':'USD '+(v/1000000).toFixed(6)
const reasons:Record<string,string>={output_invalid_or_no_longer_eligible:'生成结果未通过校验，或提交时来源资格已改变',daily_budget:'今日可用预算不足',total_budget:'本期总预算不足',task_budget:'超过每篇费用上限',input_changed:'来源或授权已改变',input_capacity_or_count_failed:'输入计数失败或超过单次容量',cancelled:'已取消',semantic_scope_unknown:'同周期有待对账请求',legacy_unknown_scope:'历史请求需对账',period_official_rates_changed:'供应商费率发生变化'}
const current=computed(()=>rows.value.find(row=>row.period===key.value))
const statuses:Record<string,string>={waiting:'等待生成',queued:'正在核对输入',running:'正在生成',unknown:'请求已派发，等待可靠结果',failed:'本次生成未完成',member_missing:'有经历尚缺已确认的梗概',budget_blocked:'预算不足',outside_initial_scope:'本周期含授权范围外的经历',off:'自动更新已关闭',cancelled:'已取消',paused:'已暂停'}
let timer:ReturnType<typeof setTimeout>|undefined
const received=ref(false)
let seq=0
async function load(reset=false){
 const n=++seq;clearTimeout(timer)
 if(reset){received.value=false;rows.value=[];key.value='';configured.value=false}
 try{
  const r=await fetch('/api/memory-v2/workspace/calendar-summaries?characterId='+props.cid,{cache:'no-store'})
  if(!r.ok)throw Error('读取周期摘要失败（'+r.status+'）')
  const data=await r.json();if(n!==seq)return
  received.value=true;bounded.value=data.bounded;recall.value=data.recall;check.value=data.check;budget.value=data.budget
  rows.value=data.periods;enabled.value=data.enabled;configured.value=data.configured
  error.value=data.error?'本次检查遇到问题：'+(reasons[data.error]||data.error):''
  if(!rows.value.some(x=>x.period===key.value)){
   key.value=rows.value.find(x=>x.period===props.period)?.period||rows.value.find(x=>x.status!=='outside_initial_scope')?.period||rows.value[0]?.period||''
  }
  const deadlines=[check.value?.nextCheck,budget.value?.endsAt]
    .map(value=>value?Date.parse(value)-Date.now():NaN).filter(ms=>Number.isFinite(ms)&&ms>0)
  const inProgress=enabled.value&&!error.value&&rows.value.some(x=>['waiting','queued','running','unknown'].includes(x.status))
  const delay=Math.min(inProgress?10000:300000,...deadlines)
  timer=setTimeout(()=>{if(!document.hidden)void load()},Math.max(delay+100,1000))
 }catch(e){if(n===seq){received.value=false;rows.value=[];error.value=(e instanceof Error?e.message:'读取周期摘要失败')+'。恢复连接后可重新读取。'}}
}
async function change(kind:'authorize'|'revoke'){
 if(busy.value)return
 const msg=kind==='revoke'?'撤销本角色的持续加工及自动摘要召回授权？':'批准在所列期限和预算内将所选范围的已确认标题、完整梗概和日期发送到 OpenAI？'
 if(!window.confirm(msg))return
 busy.value=true
 try{
  const r=await fetch('/api/memory-v2/workspace/calendar-summaries/'+kind+(kind==='revoke'?'?characterId='+props.cid:''),{method:'POST',headers:{'Content-Type':'application/json'},
   body:kind==='authorize'?JSON.stringify({characterId:props.cid,days:days.value,perTask:Math.round(perTask.value*1e6),perDay:Math.round(perDay.value*1e6),total:Math.round(total.value*1e6),recall:allowRecall.value,futurePublished:scopeFuture.value}):undefined})
  if(!r.ok)throw Error('设置未保存，请核对期限及每篇 ≤ 每日 ≤ 总预算。')
  await load()
 }catch(e){error.value=String(e)}finally{busy.value=false}
}
async function toggle(){
 if(busy.value)return
 if(!enabled.value&&!window.confirm('恢复本角色的周期自动统稿？已发布且已确认的标题、日期和梗概将发送到 OpenAI，按用量持续计费。'))return
 busy.value=true
 try{const r=await fetch('/api/memory-v2/workspace/calendar-summaries/consent?characterId='+props.cid+'&enabled='+!enabled.value,{method:'POST'})
 if(!r.ok)throw Error('未能更改授权');await load()}catch(e){error.value=String(e)}finally{busy.value=false}
}
function refreshVisible(){if(!document.hidden && !busy.value)void load()}
document.addEventListener('visibilitychange',refreshVisible)
window.addEventListener('online',refreshVisible)
watch(()=>[props.cid,props.revision],()=>load(true),{immediate:true})
watch(()=>props.period,p=>{if(p&&rows.value.some(x=>x.period===p))key.value=p})
onBeforeUnmount(()=>{seq++;clearTimeout(timer);document.removeEventListener('visibilitychange',refreshVisible);window.removeEventListener('online',refreshVisible)})
</script>
<template>
<section class="period-card" aria-label="周期摘要">
<h2>周期摘要</h2>
<label v-if="rows.length">查看周期 <select v-model="key"><option v-for="row in rows" :key="row.period" :value="row.period">{{row.period}} · {{({L1:'周',L2:'月',L3:'季度',L4:'半年',L5:'年度'} as Record<string,string>)[row.level]}}</option></select></label>
<template v-if="current"><p>{{current.count}} 条已发布经历，其中 {{current.ready}} 条有已确认的单条梗概。</p>
<p v-if="current.body" class="reading-text">{{current.body}}</p><p v-else role="status">{{statuses[current.status]||current.status}}<span v-if="current.reason"> · {{reasons[current.reason]||current.reason}}</span></p>
<small v-if="current.body">模型自动统稿 · 根据本周期当前已确认梗概生成</small><details v-if="current.members?.length"><summary>查看本周期经历与完整精修</summary><button v-for="member in current.members" :key="member.id" @click="emit('open-entry',member.id)">{{member.title}}</button></details></template>
<p v-else-if="received && !error">本角色暂无可按日期归入周期的已发布经历。</p>
<p v-if="error" role="alert">{{error}}</p>
<p v-if="received" role="status">本地维护 · {{enabled?'自动付费已开启':bounded?'自动付费已暂停':'自动付费未授权'}} · {{pending}} 个周期尚无可用正文</p>
<details v-if="received"><summary>自动更新设置</summary><p>上次检查 {{dateText(check.lastCheck)}}，下次 {{dateText(check.nextCheck)}}（香港时间）。</p>

<p>服务启动、发布变动及每日零点检查。周、月、季度随发布整理；达到半年档或年度档后补齐对应回顾。只有当前已发布、日期明确且已确认的标题和梗概参与。</p>
<p>自动摘要用于召回：{{recall?'已授权，按实际命中范围核验':'未授权'}}。展开仍只读取已确认的完整精修。</p>
<template v-if="budget.perTask"><p>每篇 {{usd(budget.perTask)}}，每日 {{usd(budget.perDay)}}，总额 {{usd(budget.total)}}，到期 {{dateText(budget.endsAt)}}。</p><p>已估算 {{usd(budget.spent)}} · 预留 {{usd(budget.reserved)}} · 今日占用 {{usd(budget.today)}}（每日预算按 UTC）。</p></template>
<form v-if="configured&&!bounded" @submit.prevent="change('authorize')">
<p>授权范围为本角色当前符合条件的经历。供应商为 OpenAI Standard，模型 gpt-6.1-sol。填写金额的单位为 USD。</p>
<label>有效天数 <input type="number" min="1" max="365" required v-model.number="days"></label>
<label>每篇上限 <input type="number" min="0.000001" step="0.000001" required v-model.number="perTask"></label>
<label>每日上限 <input type="number" min="0.000001" step="0.000001" required v-model.number="perDay"></label>
<label>总预算 <input type="number" min="0.000001" step="0.000001" required v-model.number="total"></label>
<label><input type="checkbox" v-model="scopeFuture"> 同时允许今后新发布的合资格经历外发加工</label>
<label><input type="checkbox" v-model="allowRecall"> 允许当前有效的模型周期摘要用于召回；仅当本次命中覆盖摘要全部成员时带入</label>
<button type="submit" :disabled="busy">确认持续加工范围与预算</button></form>
<button v-if="bounded" :disabled="busy" @click="change('revoke')">撤销加工与自动摘要召回授权</button>
<button v-if="bounded" :disabled="busy" @click="toggle">{{enabled?'暂停自动统稿':'恢复自动统稿'}}</button></details>
<button @click="load()">刷新周期摘要</button>
</section>
</template>

<style scoped>form{display:grid;gap:.75rem;max-width:40rem}form label{display:flex;align-items:center;gap:.6rem;flex-wrap:wrap}input[type=number]{max-width:9rem}button{margin:.4rem .5rem .4rem 0}</style>
