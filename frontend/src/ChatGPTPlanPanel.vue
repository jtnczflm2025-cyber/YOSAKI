<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'

const emit = defineEmits<{ 'model-saved': [] }>()
type Status = { enabled:boolean; connected:boolean; plan_allowed:boolean; email:string|null; pending:boolean; error:string }
type CatalogModel = { slug:string; display_name:string }
const status = ref<Status|null>(null)
const models = ref<CatalogModel[]>([])
const authorizationUrl = ref('')
const feedback = ref('')
const busy = ref(false)
let poller:ReturnType<typeof setInterval>|undefined

async function request<T>(url:string,method='GET',body?:unknown):Promise<T>{
  const response=await fetch(url,{method,headers:body?{'Content-Type':'application/json'}:undefined,body:body?JSON.stringify(body):undefined,credentials:'same-origin'})
  const result=await response.json()
  if(!response.ok)throw new Error(result.detail||`HTTP ${response.status}`)
  return result as T
}
async function refresh(){
  try{
    status.value=await request<Status>('/api/chatgpt/status')
    if(status.value.connected && status.value.plan_allowed){
      authorizationUrl.value=''
      models.value=await request<CatalogModel[]>('/api/chatgpt/models')
    }else models.value=[]
  }catch(error){feedback.value=error instanceof Error?error.message:'账户状态读取失败'}
}
async function begin(){
  busy.value=true;feedback.value=''
  try{
    const result=await request<{authorization_url:string}>('/api/chatgpt/start','POST')
    authorizationUrl.value=result.authorization_url
    feedback.value='请点击下方链接，在浏览器中完成授权；完成后会自动刷新状态。'
    await refresh()
  }catch(error){feedback.value=error instanceof Error?error.message:'无法启动授权'}
  finally{busy.value=false}
}
async function addModel(model:CatalogModel){
  busy.value=true;feedback.value=''
  try{
    await request('/api/models','POST',{name:`ChatGPT · ${model.display_name}`,provider_name:'ChatGPT Plan',base_url:'https://api.openai.com/v1',api_key:null,model_name:model.slug,protocol:'chatgpt_plan',temperature:0.8,top_p:1,max_tokens:2048,context_limit:32768,supports_streaming:true,enabled:true,reasoning_effort:null})
    feedback.value=`已添加 ${model.display_name}；可在聊天窗口选择它。`
    emit('model-saved')
  }catch(error){feedback.value=error instanceof Error?error.message:'模型添加失败'}
  finally{busy.value=false}
}
async function disconnect(){
  if(!window.confirm('断开 ChatGPT 账户并尝试撤销远端授权？现有聊天和模型配置仍保留。'))return
  busy.value=true
  try{
    const result=await request<{remote_revoked:boolean}>('/api/chatgpt/disconnect','POST')
    authorizationUrl.value=''
    feedback.value=result.remote_revoked?'已断开，远端授权已撤销。':'本机令牌已清除；请在 ChatGPT 设置中核对远端应用授权。'
    await refresh()
  }catch(error){feedback.value=error instanceof Error?error.message:'断开失败'}
  finally{busy.value=false}
}
onMounted(()=>{void refresh();poller=setInterval(()=>{if(status.value?.pending)void refresh()},2500)})
onUnmounted(()=>{if(poller)clearInterval(poller)})
</script>

<template>
  <details class="chatgpt-plan"><summary>ChatGPT 套餐接入（本机试验）</summary>
    <p>使用官方授权连接符合条件的 ChatGPT 账户。模型请求走独立的 Responses 通道；用量和账户权限由 ChatGPT 判定，不会自动切换到付费 API。</p>
    <p v-if="status && !status.enabled">后端尚未启用本机授权。先在后端环境设置 <code>YOSAKI_CHATGPT_PLAN_ENABLED=1</code> 并重启。</p>
    <template v-else-if="status">
      <p>账户：{{status.connected ? (status.email||'已连接') : '未连接'}} · 套餐权限：{{status.plan_allowed?'已授予':'未验证'}} <span v-if="status.pending">· 等待浏览器授权</span></p>
      <button v-if="!status.connected || !status.plan_allowed" :disabled="busy||status.pending" @click="begin">Continue with ChatGPT</button>
      <button v-if="status.connected" :disabled="busy" @click="disconnect">断开并撤销授权</button>
      <button @click="refresh">刷新状态</button>
      <p v-if="authorizationUrl"><a :href="authorizationUrl" target="_blank" rel="noopener noreferrer">打开 ChatGPT 授权页面</a></p>
      <p v-if="status.error">{{status.error}}</p>
      <div v-if="status.plan_allowed && models.length" class="plan-model-list"><strong>此账户可选模型</strong><div v-for="item in models" :key="item.slug"><span>{{item.display_name}}</span><button :disabled="busy" @click="addModel(item)">加入模型列表</button></div></div>
    </template>
    <p role="status">{{feedback}}</p>
  </details>
</template>

<style scoped>
.chatgpt-plan{padding:12px;border:1px solid #716f83;border-radius:12px;margin:8px 0;color:#f2eefb}
.chatgpt-plan summary{cursor:pointer;font-weight:600}
.chatgpt-plan p{line-height:1.5;color:#d7d1e5}
.chatgpt-plan button{background:#373047;color:#f2ecff;border:1px solid #645a80;border-radius:7px;padding:7px 10px;margin:4px 8px 4px 0}
.chatgpt-plan button:disabled{opacity:.5;cursor:not-allowed}
.chatgpt-plan a{color:#bfb0ff}
.plan-model-list>div{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:4px 0}
</style>
