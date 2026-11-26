<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
const plan=ref({enabled:false,frequency:'daily',hour:3,minute:0,weekday:0,keep:7,local_dir:'',github_repo:''})
const runs=ref<any[]>([])
const remote=ref<any[]>([])
const passwordConfigured=ref(false)
const tokenConfigured=ref(false)
const status=ref('')
const busy=ref(false)
async function json(url:string,method='GET',body?:unknown){const r=await fetch(url,{method,headers:body?{'Content-Type':'application/json'}:undefined,body:body?JSON.stringify(body):undefined});const value=await r.json();if(!r.ok)throw new Error(value.detail||`HTTP ${r.status}`);return value}
function statusName(value:string,target:'local'|'github'){return ({success:'成功',disabled:'未启用',failed:'失败',retention_failed:target==='local'?'新备份成功，清理旧份失败':'上传成功，清理旧份失败',missing_token:'缺少 token',unsupported_large_backup:'备份太大，不支持 GitHub Contents 上传',local_failed:'本地备份失败，未上传'} as Record<string,string>)[value]||value}
const saved=ref(''), loaded=ref(false), due=ref(''), readError=ref('')
const dirty=computed(()=>loaded.value && JSON.stringify(plan.value)!==saved.value)
let readVersion=0
function scheduleTime(value:string){
  const stamp=Date.parse(value)
  return Number.isFinite(stamp)?new Date(stamp).toLocaleString('zh-HK',{timeZone:'Asia/Hong_Kong'}):'时间待核对'
}
const schedule=computed(()=>{
  if(!loaded.value)return readError.value?'计划暂不可读':'正在读取备份计划'
  const current=JSON.parse(saved.value)
  return current.enabled?'计划已开启 · 下次计划时间 '+(due.value?scheduleTime(due.value)+'（香港时间）':'待核对'):'定时备份已关闭'
})
async function load(resetDraft=false){
  const version=++readVersion
  try{
    const data=await json('/api/system/backup-plan')
    if(version!==readVersion)return
    const next={...plan.value}
    for(const key of Object.keys(next) as (keyof typeof next)[])Object.assign(next,{[key]:data.plan[key]})
    next.enabled=Boolean(next.enabled)
    const changedElsewhere=loaded.value && saved.value!==JSON.stringify(next)
    if(resetDraft || !dirty.value)plan.value={...next}
    else if(changedElsewhere)status.value='其他页面已更新计划，已保留本页草稿；请先核对再保存。'
    saved.value=JSON.stringify(next);loaded.value=true
    due.value=data.plan.next_run_at||'';runs.value=data.runs
    passwordConfigured.value=data.password_configured;tokenConfigured.value=data.github_token_configured
    readError.value=''
  }catch{if(version===readVersion)readError.value='暂时无法读取最新计划。恢复连接后会自动查看，也可以重新读取。'}
}
async function save(){
  if(busy.value||!dirty.value)return
  busy.value=true;readVersion++
  try{await json('/api/system/backup-plan','PUT',plan.value);status.value='备份计划已保存';await load(true)}
  catch(e){status.value=String(e)}finally{busy.value=false}
}
async function run(){
  if(busy.value||dirty.value||!loaded.value||readError.value)return
  busy.value=true;readVersion++
  try{const result=await json('/api/system/backup-plan/run','POST');status.value=`本地：${statusName(result.local,'local')} · GitHub：${statusName(result.github,'github')}`;await load()}
  catch(e){status.value='本次操作未取得成功回执。请核对运行记录。'+String(e)}finally{busy.value=false}
}
function reconcile(){if(!document.hidden&&!busy.value)void load()}
async function list(){try{remote.value=await json('/api/system/backup-plan/github')}catch(e){status.value=String(e)}}
function download(name:string){const a=document.createElement('a');a.href=`/api/system/backup-plan/github/${encodeURIComponent(name)}`;a.download=name;document.body.append(a);a.click();a.remove();status.value='下载已由浏览器开始；请用上方隔离验证入口检查文件。'}
onMounted(()=>{void load();window.addEventListener('focus',reconcile);window.addEventListener('online',reconcile)})
onBeforeUnmount(()=>{readVersion++;window.removeEventListener('focus',reconcile);window.removeEventListener('online',reconcile)})
</script>
<template><section class="v2-backup" aria-label="备份计划"><h3>定时备份</h3><p role="status">{{schedule}}</p><p v-if="runs.length">最近一次 · {{scheduleTime(runs[0].started_at)}} · 本地 {{statusName(runs[0].local_status,'local')}} · GitHub {{statusName(runs[0].github_status,'github')}}</p><p v-else-if="loaded">尚无执行记录。</p><p v-if="readError" role="alert">{{readError}} <button @click="load()">重新读取</button></p><details><summary>设置备份计划与查看记录</summary><p>到点执行依赖服务的后台任务。密码与 GitHub token 仅从后端环境变量读取；当前状态：密码{{passwordConfigured?'已配置':'未配置'}}，token {{tokenConfigured?'已配置':'未配置'}}。</p><p>本地目录属于后端机器。GitHub 请使用与源码分开的私有仓库；删除旧文件不会清除 Git 历史容量。</p><fieldset :disabled="busy||!loaded"><label><input v-model="plan.enabled" type="checkbox"/>启用定时备份</label><div class="v2-grid"><label>频率<select v-model="plan.frequency"><option value="daily">每天</option><option value="weekly">每周</option></select></label><label>小时<input v-model.number="plan.hour" type="number" min="0" max="23"/></label><label>分钟<input v-model.number="plan.minute" type="number" min="0" max="59"/></label><label v-if="plan.frequency==='weekly'">星期<select v-model.number="plan.weekday"><option v-for="day in 7" :key="day" :value="day-1">{{['一','二','三','四','五','六','日'][day-1]}}</option></select></label><label>保留份数<input v-model.number="plan.keep" type="number" min="1" max="100"/></label><label>本地同步目录<input v-model="plan.local_dir" placeholder="D:\Backups\YOSAKI"/></label><label>私有 GitHub 仓库<input v-model="plan.github_repo" placeholder="owner/backup-repo"/></label></div><p v-if="dirty" role="status">计划有未保存的修改。</p><button :disabled="busy||!dirty" @click="save">保存计划</button><button v-if="dirty" @click="plan=JSON.parse(saved)">撤销本页修改</button></fieldset><button :disabled="busy||dirty||!loaded||!!readError" @click="run">立即备份</button><button @click="list">查看云端备份</button><div v-for="item in remote" :key="item.name">{{item.name}} <button @click="download(item.name)">下载以恢复</button></div><div v-for="item in runs" :key="item.id">{{item.started_at}} · 本地 {{statusName(item.local_status,'local')}} · GitHub {{statusName(item.github_status,'github')}}</div><p role="status">{{status}}</p></details></section></template>
<style scoped>.v2-backup{padding:12px;border:1px solid var(--ui-line-soft);border-radius:12px;margin:8px}.v2-backup summary{cursor:pointer}.v2-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin:10px 0}.v2-grid label{display:flex;flex-direction:column;gap:4px}.v2-grid input,.v2-grid select{width:100%;background:var(--ui-surface);color:var(--ui-ink);border:1px solid var(--ui-line-soft);border-radius:6px;padding:6px}.v2-backup button{background:var(--ui-raised);color:var(--ui-ink);border:1px solid var(--ui-line-soft);border-radius:7px;padding:7px 10px;margin:4px 8px 4px 0}.v2-backup button:disabled{opacity:1;cursor:not-allowed}.v2-backup p{font-size:var(--ui-reading-size);line-height:var(--ui-reading-leading);max-width:78ch}.v2-backup fieldset{border:0;padding:0;min-width:0}.v2-backup summary{padding:8px 0}@media(max-width:600px){.v2-backup button,.v2-backup summary{min-height:44px}}</style>
