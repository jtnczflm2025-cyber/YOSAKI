<script setup lang="ts">
import { computed,onMounted,onBeforeUnmount,ref,watch } from 'vue'
import { voiceApi } from './voiceApi'
import { voiceAudioPlayer,type PlaybackState } from './voiceAudioPlayer'
type Annotation={fragment_id:string;intent:string}
type Fragment={fragment_id:string;text:string;kind:string;start:number;end:number}
type Result={cache_read_only?:boolean;id:string;revision:number;state:string;bridge_state:string;error:string|null;reason:string|null;preview:{request_text:string;request_characters:number;effective_mode:string;fallback_reason:string|null;parameters:Record<string,string>}|null;audio:{url:string}[];versions:{revision:number;annotations:Annotation[]}[];fragments:Fragment[];intent_tags:Record<string,string>;performance_preferences:{version:string;delivery_target:string}|null;cached_versions:{revision:number;url:string}[]}
const props=defineProps<{messageId:number;autoStart?:boolean}>()
const emit=defineEmits<{play:[];'playback-state':[value:PlaybackState];'budget-changed':[]}>()
const plannerName=ref('')
const eligible=ref(false),loading=ref(true),localStopped=ref(false),busy=ref(false),error=ref(''),result=ref<Result|null>(null),draft=ref<Annotation[]>([]),show=ref(false),playback=ref('idle')
let epoch=0,disposed=false,started=false
const labels:Record<string,string>={planning:'正在规划完整回复，可取消',synthesizing:'正在合成',completed:'AI 音频已保存',failed_unknown:'规划结果未知，不会重试',unknown:'合成结果未知，不会重试',failed:'本轮未派发或生成失败',cancelled:'已取消',not_generated:'当前版本尚未合成'}
const intents=['inherit','thoughtful','restrained','resigned','lazy','dry_humor','whisper','chuckle']
const newFragment=ref(''),newIntent=ref('thoughtful')
const savedAnnotations=computed(()=>result.value?.versions.find(v=>v.revision===result.value?.revision)?.annotations || [])
const dirty=computed(()=>JSON.stringify(draft.value)!==JSON.stringify(savedAnnotations.value))
const availableFragments=computed(()=>result.value?.fragments.filter(f=>!draft.value.some(a=>{
 const old=result.value?.fragments.find(v=>v.fragment_id===a.fragment_id)
 return old && old.start<f.end && f.start<old.end
})) || [])
const rows=computed(()=>result.value?.fragments.filter(f=>f.kind==='sentence').map(f=>{
 const mark=savedAnnotations.value.find(a=>result.value?.fragments.find(p=>p.fragment_id===a.fragment_id)?.start===f.start)
 return { ...f,intent:mark?.intent || '留白',tag:mark ? result.value?.intent_tags[mark.intent] || '无新增标签' : '无新增标签' }
}) || [])
function adopt(value:Result){result.value=value;draft.value=(value.versions.find(v=>v.revision===value.revision)?.annotations || []).map(a=>({...a}))}
function addAnnotation(){if(busy.value)return;const f=availableFragments.value.find(v=>v.fragment_id===newFragment.value);if(!f){error.value='请选择不与已有标注重叠的句子或段落';return}draft.value.push({fragment_id:f.fragment_id,intent:newIntent.value});newFragment.value=''}
async function load(allowAuto=true){const token=epoch;loading.value=true;error.value='';try{const settings=await voiceApi<{model_profile_id:number|null;profiles:{id:number;name:string;model_name:string}[]}>('/ai-http/settings');if(token!==epoch||disposed)return;const chosen=settings.profiles.find(p=>p.id===settings.model_profile_id);plannerName.value=chosen?`${chosen.name} · ${chosen.model_name}`:'未选择';const data=await voiceApi<{eligible:boolean;reason?:string;plan:Result|null}>(`/ai-http/messages/${props.messageId}/state`);if(token!==epoch||disposed)return;eligible.value=data.eligible;if(data.plan)adopt(data.plan);else{result.value=null;draft.value=[];newFragment.value=''}if(!data.eligible && data.reason==='ai_planning_disabled'){const saved=await voiceApi<{plan:Result|null}>(`/ai-http/messages/${props.messageId}/saved-audio`);if(token!==epoch||disposed)return;if(saved.plan){adopt(saved.plan);error.value='AI 规划已关闭；这里只播放已保存版本，不规划、不合成。'}}if(!data.eligible && !result.value?.cache_read_only)error.value=data.reason==='complete_display_binding_required'?'此消息没有有效的后端完成／展示回执，AI 模式不可用；请选择无标签或手选 thoughtful。':`AI 模式不可用：${data.reason}`;
loading.value=false;if(allowAuto&&props.autoStart&&!started&&data.eligible){started=true;if(data.plan?.bridge_state==='completed')play(data.plan.audio[0].url);else await run()}}
catch(e){if(token===epoch&&!disposed){eligible.value=false;error.value=e instanceof Error?e.message:'AI 模式不可用'}}finally{if(token===epoch&&!disposed)loading.value=false}}
function play(url:string){const token=epoch;localStopped.value=false;emit('play');void voiceAudioPlayer.play(props.messageId,[url],s=>{if(!disposed&&token===epoch){playback.value=s;emit('playback-state',s)}})}
async function run(){if(busy.value||dirty.value||!eligible.value)return;const token=++epoch;localStopped.value=false;busy.value=true;error.value='';try{let value=await voiceApi<Result>(`/ai-http/messages/${props.messageId}/read`,'POST',{});if(token!==epoch||disposed){await voiceApi(`/ai-http/plans/${value.id}`,'DELETE').catch(()=>{});return}
while(token===epoch&&!disposed){adopt(value);if(value.bridge_state==='completed'){play(value.audio[0].url);break}if(!['planning','synthesizing'].includes(value.bridge_state)){emit('playback-state','failed');break}await new Promise(r=>setTimeout(r,120));if(token!==epoch||disposed)return;value=await voiceApi<Result>(`/ai-http/plans/${value.id}`)}}catch(e){if(token===epoch){error.value=e instanceof Error?e.message:'本轮未完成，不会自动重试';await cancel()}}finally{if(token===epoch)busy.value=false;if(!disposed)emit('budget-changed')}}
async function cancel(){epoch++;localStopped.value=true;voiceAudioPlayer.stop(props.messageId);busy.value=false;loading.value=false;playback.value='stopped';if(!disposed)emit('playback-state','stopped');if(result.value&&['planning','synthesizing'].includes(result.value.bridge_state)){const id=result.value.id;result.value={...result.value,bridge_state:'cancelled',state:'cancelled',error:null,reason:null};await voiceApi(`/ai-http/plans/${id}`,'DELETE').catch(()=>{})}}
async function edit(){if(!result.value||busy.value)return;await cancel();busy.value=true;error.value='';try{await voiceApi(`/ai-http/plans/${result.value.id}`,'PATCH',{expected_revision:result.value.revision,annotations:draft.value});await load(false);localStopped.value=false}catch(e){error.value=e instanceof Error?e.message:'标注未保存'}finally{busy.value=false}}
function refresh(){void cancel().then(()=>load())}
onMounted(()=>{void load();window.addEventListener('yosaki-voice-config-changed',refresh)})
watch(()=>props.messageId,()=>{void cancel().then(()=>{localStopped.value=false;started=false;void load()})})
watch(()=>props.autoStart,value=>{if(value&&!started&&!disposed)void load();else if(!value&&started&&(busy.value||playback.value==='playing'||playback.value==='paused'))void cancel()})
onBeforeUnmount(()=>{disposed=true;window.removeEventListener('yosaki-voice-config-changed',refresh);void cancel()})
</script>
<template>
 <section aria-label="AI 表达朗读" class="ai-message-audio">
  <p>所选规划档案：{{plannerName}}</p>
  <p role="status">{{localStopped ? '已在本地停止；迟到结果不会播放' : loading ? '正在核对 AI 资格…' : result ? labels[result.bridge_state] || result.bridge_state : eligible ? '可规划朗读' : 'AI 模式不可用'}} · {{playback}}</p>
  <p v-if="error || result?.error || result?.reason" role="alert">{{error || result?.error || result?.reason}}</p>
  <button v-if="eligible && !busy" type="button" :disabled="dirty" @click="run">{{result?.bridge_state==='completed'?'播放 AI 缓存':'规划并朗读当前版本'}}</button>
  <button v-if="busy" type="button" @click="cancel">取消 AI 规划／合成</button>
  <button v-if="playback==='playing'" type="button" @click="voiceAudioPlayer.pause(messageId)">暂停 AI 音频</button>
  <button v-if="playback==='paused'" type="button" @click="voiceAudioPlayer.resume(messageId)">继续 AI 音频</button>
  <button type="button" @click="cancel">停止 AI 音频</button>
  <template v-if="result?.preview">
   <p v-if="result.performance_preferences">声音表演偏好：{{result.performance_preferences.delivery_target}}（{{result.performance_preferences.version}}；不改变聊天人格）</p>
   <details><summary>实际送读预览：{{result.preview.request_characters}} 字符 · 1次 HTTP</summary><pre>{{result.preview.request_text}}</pre><p>有效模式：{{result.preview.effective_mode}}；{{result.preview.fallback_reason}}</p><pre>{{JSON.stringify(result.preview.parameters,null,2)}}</pre></details>
   <label><input v-model="show" type="checkbox">显示并编辑语气标注（默认隐藏）</label>
   <div v-if="show" class="annotation-editor">
    <p>以下对应已保存版本 {{result.revision}}。只影响送读；没有新增标签不等于声学重置，标签作用范围仍限已听评样本。</p>
    <div class="mapping-scroll"><table aria-label="逐句表达对应"><thead><tr><th>原句</th><th>当前版本意图／留白</th><th>此位置新增标签</th></tr></thead><tbody><tr v-for="row in rows" :key="row.fragment_id"><td>{{row.text}}</td><td>{{row.intent}}</td><td>{{row.tag}}</td></tr></tbody></table></div>
    <p>段落标注仅在段首插入标签；不声称后续句子已恢复无表演状态。</p>
    <template v-if="!result.cache_read_only">
    <div v-for="(a,i) in draft" :key="a.fragment_id"><span>{{result.fragments.find(f=>f.fragment_id===a.fragment_id)?.text}}</span><select v-model="a.intent" aria-label="表达意图" :disabled="busy"><option v-for="intent in intents" :key="intent" :value="intent">{{intent}}</option></select><button type="button" :disabled="busy" @click="draft.splice(i,1)">移除此标注</button></div>
    <label>新增标注位置<select v-model="newFragment" :disabled="busy"><option value="">请选择句子或段落</option><option v-for="f in availableFragments" :key="f.fragment_id" :value="f.fragment_id">{{f.kind==='sentence'?'句子':'段落'}}：{{f.text}}</option></select></label>
    <label>新增表达意图<select v-model="newIntent" :disabled="busy"><option v-for="intent in intents" :key="intent" :value="intent">{{intent}}</option></select></label>
    <button type="button" :disabled="busy || !newFragment" @click="addAnnotation">添加到标注草稿</button>
    <p v-if="dirty" role="status">标注修改尚未保存；不会重新规划或合成。保存后预览更新，再由你明确朗读。</p>
    <button type="button" :disabled="busy || !dirty" @click="edit">保存标注（不合成）</button>
    <button type="button" :disabled="busy || !dirty" @click="draft=savedAnnotations.map(a=>({...a}))">放弃标注修改</button>
    </template>
   </div>
  </template>
  <button v-for="v in result?.cached_versions || []" :key="v.revision" type="button" @click="play(v.url)">播放表达版本 {{v.revision}}</button>
 </section>
</template>
<style scoped>pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:17px;line-height:1.75}button{margin:4px}select{max-width:100%}.annotation-editor p,.annotation-editor td{font-size:17px;line-height:1.75}.mapping-scroll{overflow-x:auto}th,td{text-align:left;padding:8px;border-bottom:1px solid var(--ui-line);vertical-align:top}.annotation-editor label{display:block;margin-block:8px}</style>
