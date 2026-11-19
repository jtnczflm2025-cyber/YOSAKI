<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { usageLabel,totalLabel,subtotalLabels } from './usagePresentation'
import {modelRevision,type Overrides} from './nextRequestSettings'
const props=defineProps<{conversationId:number|null;characterId:number;message:any;head:number;modelId:number|null;models:any[];disabled:boolean;overrides:Record<string,any>;draft?:string;attachmentIds?:string[];quoteDraft?:any;applyOverrides:(value:Overrides)=>Promise<void>;settingsNotice:string;settingsReady:boolean}>()
const emit=defineEmits<{reload:[number];regenerate:[number];quote:[{message_id:number;excerpt:string}];busy:[boolean]}>()
const preview=ref<any>(null),lastInput=ref<number|null>(null),previewNote=ref('');let previewEpoch=0
const state=ref<any>(null),detail=ref<any>(null),error=ref(''),notice=ref(''),busy=ref(false)
const editing=ref(false),body=ref(''),language=ref('中文'),translation=ref(''),auxModel=ref<number|null>(null)
const effort=ref('inherit'),output=ref<number|null>(null),cap=ref<any>(null),vision=ref(false),evidence=ref('')
const enabled=ref(false),automatic=ref(false);let controller:AbortController|null=null
const labels:Record<string,string>={serialized:'已序列化（服务商接受情况未核验）',included:'已带入',not_connected:'未接入',disabled:'已关闭',not_selected:'未选中',eligibility_rejected:'资格拒绝',budget_rejected:'预算拒绝',error:'错误'}
const channels:Record<string,string>={structured_volition:'结构化意愿',psychology:'心理参考',learning_strategy:'学习策略',manual_memory:'人工记忆',approved_learning_memory:'已批准学习记忆',drive_advisory:'已有驱力参考',conversation_summary:'会话摘要',chronicle:'Chronicle',chronicle_entry:'Chronicle 条目'}
const dirty=computed(()=>editing.value&&body.value!==props.message?.content)
const attempt=computed(()=>detail.value?.attempt??detail.value)
const tokenLabels:Record<string,string>={input:'累计输入',output:'累计输出',cache_read:'缓存读取（输入子项）',cache_write:'缓存写入（输入子项）',reasoning:'推理（输出子项）'}
const tokenSummary=computed(()=>{const t=state.value?.statistics?.tokens;if(!t)return null;return {total:t.input.known+t.output.known,complete:t.input.unknown===0&&t.output.unknown===0,cache:t.input.unknown===0&&t.cache_read.unknown===0&&t.input.known>0?(100*t.cache_read.known/t.input.known).toFixed(1)+'%':'未知（回执不足或输入为零）'}})
const promptOrder=computed(()=>{const s=attempt.value?.snapshot;if(!s)return [];const rows:any[]=[];if(s.instructions)rows.push({role:'instructions',content:s.instructions});for(const item of s.messages??s.input??[])rows.push(item);return rows})
function promptText(item:any){return typeof item.content==='string'?item.content:JSON.stringify(item.content??item,null,2)}
const outputDuration=computed(()=>typeof attempt.value?.elapsed_ms==='number'&&typeof attempt.value?.first_text_ms==='number'?Math.max(0,attempt.value.elapsed_ms-attempt.value.first_text_ms):null)
async function api(url:string,options:RequestInit={}){const r=await fetch(url,{...options,headers:options.body?{'Content-Type':'application/json'}:undefined,signal:controller?.signal});const data=await r.json();if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:Array.isArray(data.detail)?data.detail.map((x:any)=>x.msg).join('；'):`操作失败（${r.status}）`);return data}
let loadEpoch=0,capEpoch=0
async function load(){
  const epoch=++loadEpoch,cid=props.conversationId,mid=props.message?.id
  if(!cid){state.value=null;detail.value=null;return}
  try{
    const [nextState,nextDetail]=await Promise.all([
      api(`/api/chat/conversations/${cid}/state`),
      mid?api(`/api/chat/messages/${mid}/details?conversation_id=${cid}`):Promise.resolve(null),
    ])
    if(epoch!==loadEpoch||cid!==props.conversationId||mid!==props.message?.id)return
    state.value=nextState;detail.value=nextDetail
    const last=nextState.attempts[0]
    lastInput.value=null
    if(last){const latest=await api('/api/chat/attempts/'+last.id);if(epoch===loadEpoch)lastInput.value=latest.normalized?.input??null}
    enabled.value=Boolean(nextState.policy.enabled);automatic.value=Boolean(nextState.policy.auto_summary)
    if(nextState.policy.summary_model_id)auxModel.value=nextState.policy.summary_model_id
  }catch(e){if(epoch===loadEpoch)error.value=String(e)}
}
watch(()=>[props.conversationId,props.message?.id,props.head],()=>{editing.value=false;translation.value='';void load()},{immediate:true})
watch(()=>props.models.find(model=>model.id===props.modelId)??props.modelId,async()=>{
  const id=props.modelId,epoch=++capEpoch
  auxModel.value=id;cap.value=null;vision.value=false;evidence.value=''
  if(!id)return
  try{
    const next=await api(`/api/chat/models/${id}/capabilities`)
    if(epoch!==capEpoch||id!==props.modelId)return
    cap.value=next;vision.value=Boolean(next.declared_vision?.vision);evidence.value=next.declared_vision?.evidence??''
  }catch(e){if(epoch===capEpoch)error.value=String(e)}
},{immediate:true})
watch(()=>[props.conversationId,props.head,props.modelId,props.draft,props.overrides,props.attachmentIds,props.quoteDraft,props.models],async()=>{
 const epoch=++previewEpoch;preview.value=null;previewNote.value=''
 if(!props.modelId)return
 try{const value=await api('/api/chat/context-preview',{method:'POST',body:JSON.stringify({character_id:props.characterId,conversation_id:props.conversationId,model_profile_id:props.modelId,message:props.draft??'',overrides:props.overrides,attachment_ids:props.attachmentIds??[],quote:props.quoteDraft??null})});if(epoch===previewEpoch)preview.value=value}
 catch(e){if(epoch===previewEpoch)previewNote.value=String(e)}
},{immediate:true,deep:true})
function milliseconds(value:unknown){return typeof value==='number'?value.toFixed(1):'未记录'}
function payload(extra:Record<string,any>={}){return {conversation_id:props.conversationId,expected_head:props.head,...extra}}
async function run(fn:()=>Promise<void>){if(busy.value||props.disabled)return;busy.value=true;emit('busy',true);error.value='';notice.value='';controller=new AbortController();try{await fn()}catch(e){error.value=(e as Error).name==='AbortError'?'已停止等待；已派发请求的用量仍会保留':e instanceof Error?e.message:'操作失败'}finally{busy.value=false;emit('busy',false);controller=null}}
function action(name:string,mid=props.message?.id,extra:Record<string,any>={}){void run(async()=>{const result=await api(`/api/chat/messages/${mid}/${name}`,{method:'POST',body:JSON.stringify(payload(extra))});editing.value=false;emit('reload',result.conversation_id??props.conversationId!);await load();notice.value='已保存'})}
function remove(){if(window.confirm('本条及依赖后续将退出当前路径，原文与用量保留，可以撤销。继续？'))action('delete')}
function quote(){const selected=window.getSelection()?.toString()??'';const excerpt=selected&&props.message.content.includes(selected)?selected:props.message.content;emit('quote',{message_id:props.message.id,excerpt})}
async function copy(){try{await navigator.clipboard.writeText(props.message.content);notice.value='已复制原正文'}catch{error.value='复制失败，请选择正文复制'}}
function canClose(){return !dirty.value||window.confirm('正文修改尚未保存，放弃这份草稿？')}
async function showAttempt(id:string){await run(async()=>{detail.value=await api('/api/chat/attempts/'+id)})}
function saveOverrides(){void run(async()=>{
  const scope=[props.characterId,props.conversationId,props.modelId,props.head,modelRevision(props.models.find(m=>m.id===props.modelId)??{})]
  const value:Overrides={}
  if(effort.value!=='inherit')value.reasoning_effort=effort.value==='none'?null:effort.value as Overrides['reasoning_effort']
  if(typeof output.value==='number'&&Number.isFinite(output.value))value.max_tokens=output.value
  const checked=await api('/api/chat/context-preview',{method:'POST',body:JSON.stringify({character_id:props.characterId,conversation_id:props.conversationId,model_profile_id:props.modelId,message:props.draft??'',overrides:value,attachment_ids:props.attachmentIds??[],quote:props.quoteDraft??null})})
  if(scope.some((v,i)=>v!==[props.characterId,props.conversationId,props.modelId,props.head,modelRevision(props.models.find(m=>m.id===props.modelId)??{})][i]))throw new Error('当前角色、对话或模型已变化，请在新位置重新应用单次设置')
  await props.applyOverrides(value);preview.value=checked;notice.value=''
})}
watch(()=>props.overrides,value=>{effort.value=Object.prototype.hasOwnProperty.call(value,'reasoning_effort')?(value.reasoning_effort===null?'none':value.reasoning_effort):'inherit';output.value=typeof value.max_tokens==='number'?value.max_tokens:null},{immediate:true,deep:true})
const previewEffortLabel=computed(()=>!preview.value?'未知（尚无有效预览）':preview.value.parameters?.reasoning_effort?.state==='not_selected'?'未发送推理参数':preview.value.parameters?.reasoning_effort?.serialized??'未知（参数未接入）')
const defaultModel=computed(()=>props.models.find(m=>m.id===props.modelId))
function effortText(value:unknown){return ({low:'低',medium:'中',high:'高'} as Record<string,string>)[String(value)]??'未指定参数（服务商默认）'}
const effectiveEffort=computed(()=>{const overridden=Object.prototype.hasOwnProperty.call(props.overrides,'reasoning_effort');return {value:effortText(overridden?props.overrides.reasoning_effort:defaultModel.value?.reasoning_effort),source:overridden?'单次设置':'模型默认设置'}})
const overridesDirty=computed(()=>effort.value!==(Object.prototype.hasOwnProperty.call(props.overrides,'reasoning_effort')?(props.overrides.reasoning_effort??'none'):'inherit')||output.value!==(props.overrides.max_tokens??null))
function clearOverrides(){void run(async()=>{await props.applyOverrides({});notice.value=''})}
function translate(){void run(async()=>{const result=await api(`/api/chat/messages/${props.message.id}/translate`,{method:'POST',body:JSON.stringify(payload({model_id:auxModel.value,language:language.value}))});translation.value=result.text;await load()})}
function summarize(){if(window.confirm('将当前分支中最后一轮之前的内容发送给选定辅助模型，生成连续性摘要？原消息保留，用量单独记录。'))void run(async()=>{await api(`/api/chat/conversations/${props.conversationId}/summary`,{method:'POST',body:JSON.stringify(payload({model_id:auxModel.value}))});await load();notice.value='摘要已保存，来源变化后自动失效'})}
defineExpose({canClose,load})
</script>
<template>
  <div class="chat-enhancement-panel">
    <p v-if="error" class="chat-action-error" role="alert">{{error}}</p><p v-if="notice" role="status">{{notice}}</p>
    <p v-if="busy" role="status">正在处理… <button @click="controller?.abort()">停止等待</button></p>
    <section v-if="message?.id"><h3>这条消息</h3><div class="chat-action-grid">
      <button :disabled="busy||disabled" @click="copy">复制正文</button>
      <button :disabled="busy||disabled" @click="editing=true;body=message.content">编辑</button><button :disabled="busy||disabled" @click="remove">删除／保留记录</button>
      <button :disabled="busy||disabled" @click="quote">引用正文或选中片段</button><button :disabled="busy||disabled" @click="action('fork')">从这里建立分支</button>
      <button v-if="message.role==='assistant'" :disabled="busy||disabled" @click="emit('regenerate',message.id)">重新生成</button>
    </div>
    <div v-if="editing"><label>修改正文 <span v-if="dirty">· 未保存</span><textarea v-model="body" rows="8"/></label><p>保存为人工修订并建立新分支，旧版本及后续仍可查看。</p><button :disabled="busy||disabled||!dirty" @click="action('edit',message.id,{content:body})">保存修订</button><button @click="canClose()&&(editing=false)">取消</button></div>
    <details><summary>翻译（独立辅助请求）</summary><label>模型<select v-model="auxModel"><option v-for="model in models" :key="model.id" :value="model.id">{{model.name}}</option></select></label><label>目标语言<input v-model="language" maxlength="40"/></label><button :disabled="busy||disabled||!auxModel" @click="translate">翻译并记录用量</button><pre v-if="translation" class="chat-reading-text">{{translation}}</pre></details>
    <details v-if="detail?.versions?.length"><summary>原文与候选版本</summary><div v-for="version in detail.versions" :key="version.id"><small>{{version.author==='human'?'人工修订':'模型／历史版本'}} · {{version.generation_status||'历史未记录'}}</small><pre class="chat-reading-text">{{version.content}}</pre><button v-if="version.hidden" :disabled="busy||disabled" @click="action('select-version',version.id)">选用此候选</button></div></details>
    </section>
    <section><h3>下一次模型设置</h3>
      <p v-if="settingsNotice" role="status">{{settingsNotice}}</p><p v-if="!settingsReady" role="status">正在核对当前身份和单次设置…</p>
      <p>模型默认推理强度：{{defaultModel?effortText(defaultModel.reasoning_effort):'未知（未选择模型）'}}。</p>
      <p role="status">下一次实际生效推理强度：{{effectiveEffort.value}} · 来源：{{effectiveEffort.source}}；预览实际参数：{{previewEffortLabel}}。</p>
      <p>模型默认输出上限：{{defaultModel?.max_tokens??'未知'}}；下一次设置值：{{overrides.max_tokens??defaultModel?.max_tokens??'未知'}} · 来源：{{overrides.max_tokens!==undefined?'单次设置':'模型默认设置'}}。实际是否发送及支持情况以通道预览为准。</p>
      <p v-if="overridesDirty" role="status">编辑尚未应用；下一次仍使用上方已生效设置。</p>
      <p>点击应用后，在当前标签页刷新或重开抽屉均保留；未应用的编辑不生效。切换用户、角色、会话或模型配置会清除，不修改模型默认值。手动发送／重生成获后端接受后消耗，HTTP 拒绝保留。发送中刷新且接受状态不明时不自动恢复；不用于工具续答或自动语音回合。</p><p v-if="cap?.protocol==='chatgpt_plan'">套餐输出硬上限尚无能力证据，档案值未发送；停止按钮不代表服务商停止计费。</p>
      <label>推理强度<select v-model="effort"><option value="inherit">使用模型默认设置</option><option value="none">不指定参数（不代表关闭推理）</option><option value="low">低</option><option value="medium">中</option><option value="high">高</option></select></label>
      <label>输出 token 上限<input v-model.number="output" type="number" min="1" max="128000" :disabled="cap?.output_limit?.supported!==true" placeholder="使用模型默认设置"/></label><button :disabled="busy||disabled||!modelId||!settingsReady" @click="saveOverrides">应用于下一次</button><button :disabled="busy||disabled||!settingsReady||!Object.keys(overrides).length" @click="clearOverrides">清除单次设置</button>
      <details><summary>图片能力声明</summary><p>仅为这个模型通道记录已有能力依据，不会自动发送测试请求。套餐视觉暂不开放。</p><label><input v-model="vision" type="checkbox" :disabled="cap?.protocol==='chatgpt_plan'"/>此通道已确认支持图片</label><label>依据<input v-model="evidence" placeholder="官方文档或本机验证记录" maxlength="1000"/></label><button :disabled="busy||disabled||!modelId||evidence.length<8" @click="run(async()=>{await api(`/api/chat/models/${modelId}/capabilities`,{method:'PUT',body:JSON.stringify({vision,evidence})});notice='已保存为用户声明，尚非本批真实验收'})">保存能力声明</button></details>
    </section>
    <section v-if="state"><h3>分支与上下文</h3><label>当前路径<select :value="conversationId" :disabled="busy||disabled" @change="emit('reload',Number(($event.target as HTMLSelectElement).value))"><option v-for="branch in state.branches" :key="branch.id" :value="branch.id">{{branch.title}} · {{branch.id}}</option></select></label>
      <button v-for="item in state.deleted.filter((x:any,i:number,a:any[])=>a.findIndex(y=>y.group_id===x.group_id)===i)" :key="item.group_id" :disabled="busy||disabled" @click="action('restore',item.group_id)">撤销删除 {{item.group_id}}</button>
      <details v-if="state.legacy_summary"><summary>原手填摘要（新分支未自动继承）</summary><pre class="chat-reading-text">{{state.legacy_summary.content}}</pre><button :disabled="busy||disabled" @click="run(async()=>{await api(`/api/chat/conversations/${conversationId}/choose-legacy-summary`,{method:'POST',body:JSON.stringify(payload({content:state.legacy_summary.content}))});notice='已明确选择为本分支人工参考，未补造覆盖范围'})">明确选为本分支参考</button></details>
      <label><input v-model="enabled" type="checkbox"/>使用完整回合预算策略</label><label><input v-model="automatic" type="checkbox"/>发送前必要时生成连续性摘要（额外模型请求）</label>
      <p>容量来自模型档案，输入采用保守字节上界估算，预留总输出。旧策略仍保留；原始消息不删除。自动摘要使用下方选定模型及其档案输出预留；套餐硬上限未知。</p>
      <button :disabled="busy||disabled" @click="run(async()=>{await api(`/api/chat/characters/${characterId}/context-policy`,{method:'PUT',body:JSON.stringify({enabled,auto_summary:automatic,summary_model_id:auxModel})});notice='已保存上下文策略'})">保存策略</button>
      <label>摘要模型<select v-model="auxModel"><option v-for="model in models" :key="model.id" :value="model.id">{{model.name}}</option></select></label><button :disabled="busy||disabled||!auxModel" @click="summarize">生成一次连续性摘要</button><pre v-if="state.summary" class="chat-reading-text">{{state.summary.content}}</pre>
    </section>
    <section v-if="state"><h3>本会话及分支消耗</h3><p>下一请求输入估算：{{preview?.estimate?.total??'未知'}}{{preview?' token（字符启发式，非账单）':''}}；上次请求实际输入：{{lastInput??'未知'}} token。</p><p v-if="previewNote">估算不可用：{{previewNote}}</p><p>会话累计输入：{{usageLabel(state.statistics.tokens.input)}}；累计输出：{{usageLabel(state.statistics.tokens.output)}}。</p><p>{{state.statistics.attempts}} 次请求尝试，其中 {{state.statistics.dispatched_attempts}} 次进入本地派发流程（不等于服务商收到）；包含被替换／删除的候选，公共祖先不重复计数。实际金额未提供。</p><p v-if="tokenSummary">总 token：{{totalLabel(state.statistics.tokens.input,state.statistics.tokens.output)}}；缓存命中率：{{tokenSummary.cache}}</p><dl><template v-for="(v,k) in state.statistics.tokens" :key="k"><dt>{{tokenLabels[k]||k}}</dt><dd>{{usageLabel(v)}}</dd></template></dl><details><summary>当前分支与辅助任务分别统计</summary><pre>{{JSON.stringify({current_branch:subtotalLabels(state.statistics.current_branch),by_purpose:Object.fromEntries(Object.entries(state.statistics.by_purpose).map(([k,v])=>[k,subtotalLabels(v)]))},null,2)}}</pre><p>当前分支只统计在本分支派发的请求；共有祖先只计入会话总量一次。</p></details><details><summary>请求记录（含失败与辅助任务）</summary><button v-for="a in state.attempts" :key="a.id" :disabled="busy" @click="showAttempt(a.id)">{{a.purpose}} · {{a.status}} · {{a.reliability}}</button></details></section>
    <section v-if="attempt?.id"><h3>实际请求详情</h3><p>{{attempt.requested_model}} → {{attempt.returned_model||'返回模型未提供'}} · {{attempt.status}}</p><p>首个供应商正文 {{milliseconds(attempt.first_text_ms)}} ms；首个公开正文 {{milliseconds(attempt.first_public_ms)}} ms；总耗时 {{milliseconds(attempt.elapsed_ms)}} ms</p>
      <p>等待正文：{{milliseconds(attempt.first_text_ms)}} ms；正文开始至结束：{{milliseconds(outputDuration)}} ms（客户端观察，含传输与收尾）。</p><p>本次上下文字节估算：{{attempt.sources?.budget?.serialized_bytes??'未知'}}；输入预算：{{attempt.sources?.budget?.input_budget??'未知'}}；输出预留：{{attempt.sources?.budget?.output_reserve??'未知'}}。字节估算不是计费用量。</p><pre>{{JSON.stringify(attempt.normalized,null,2)}}</pre><details v-if="attempt.usage_receipts?.length"><summary>供应商原始 usage 回执</summary><pre>{{JSON.stringify(attempt.usage_receipts,null,2)}}</pre></details><p>缓存是输入子项，推理是输出子项；未提供的值不按零计。</p>
      <div v-for="(value,key) in attempt.parameters" :key="key"><strong>{{key}}</strong> · 配置 {{value.requested??'未指定'}} · 实际发送 {{value.serialized??'未发送'}} · {{labels[value.state]||value.state}}</div>
      <ul><li v-for="(item,i) in attempt.sources?.channels||[]" :key="i">{{channels[item.channel]||item.channel}}：{{labels[item.state]||item.state}} {{item.reason}}</li></ul>
      <p v-if="attempt.sources?.channels?.some((item:any)=>item.channel==='drive_advisory'&&item.state==='included')">驱力来源：本角色的模拟驱力状态，按本条问题选择参考；实际带入值以本次提示词快照为准。</p>
      <p v-if="attempt.sources?.memory_refs?.length">记忆来源编号：{{attempt.sources.memory_refs.map((item:any)=>item.id).join('、')}}；来源内容校验值见参考来源记录，实际正文见提示词顺序。</p>
      <details><summary>实际发送的提示词顺序</summary><ol><li v-for="(item,i) in promptOrder" :key="i"><strong>{{item.role||item.type||'协议项'}}</strong><pre class="chat-reading-text">{{promptText(item)}}</pre></li></ol><p>顺序取自本次协议快照；工具声明见完整快照，附件内容仅显示哈希。</p></details><details><summary>参考来源记录</summary><pre>{{JSON.stringify(attempt.sources,null,2)}}</pre></details><details v-if="attempt.sources?.prefix_comparison"><summary>与前次请求的前缀差异</summary><pre>{{JSON.stringify(attempt.sources.prefix_comparison,null,2)}}</pre><p>相同前缀不保证服务商缓存命中。</p></details><details><summary>实际序列化快照 · {{attempt.snapshot_state}}</summary><p>不透明状态和附件字节只展示哈希。凭据不保存于快照。</p><pre>{{JSON.stringify(attempt.snapshot,null,2)}}</pre></details><button :disabled="busy" @click="run(async()=>{await api(`/api/chat/attempts/${attempt.id}/snapshot`,{method:'DELETE'});detail=await api(`/api/chat/attempts/${attempt.id}`)})">清除这份快照正文，保留用量</button>
    </section><p v-else-if="detail?.note">{{detail.note}}</p>
  </div>
</template>
