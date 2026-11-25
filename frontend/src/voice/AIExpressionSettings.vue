<script setup lang="ts">
import { onMounted,ref } from 'vue'
import { voiceApi } from './voiceApi'
type Settings={available:boolean;enabled:boolean;model_profile_id:number|null;revision:number;profiles:{id:number;name:string;model_name:string}[];reason?:string;performance_preferences?:{version:string;delivery_target:string}}
const value=ref<Settings|null>(null), enabled=ref(false), selected=ref<number|null>(null), confirm=ref(false), error=ref(''), busy=ref(false)
async function load(){try{value.value=await voiceApi<Settings>('/ai-http/settings');enabled.value=value.value.enabled;selected.value=value.value.model_profile_id}catch{value.value=null}}
onMounted(load)
async function save(){if(!value.value)return;busy.value=true;error.value='';try{
 value.value=await voiceApi<Settings>('/ai-http/settings','PUT',{enabled:enabled.value,model_profile_id:selected.value,expected_revision:value.value.revision,confirm_planning:confirm.value});confirm.value=false;error.value='AI 表达设置已保存。';window.dispatchEvent(new Event('yosaki-voice-config-changed'))
}catch(e){error.value=e instanceof Error?e.message:'保存失败'}finally{busy.value=false}}
</script>
<template>
 <section class="voice-section" aria-label="AI 表达规划设置">
  <h3>AI 表达规划（普通聊天 · 短日语 HTTP）</h3>
  <p v-if="!value?.available">此候选尚未启用；原有普通朗读和手选 thoughtful 仍可使用。</p>
  <template v-else>
   <label><input v-model="enabled" type="checkbox" :disabled="busy">启用 AI 表达规划（默认关闭）</label>
   <label>规划模型档案<select v-model="selected" :disabled="busy"><option :value="null">请选择</option><option v-for="p in value.profiles" :key="p.id" :value="p.id">{{p.name}} · {{p.model_name}}</option></select></label>
   <p>已选档案：{{value.profiles.find(p=>p.id===selected)?.name ?? '未选择'}}。完整回复通过展示核验后，每条最多新增一次规划（512 输出 token／20秒，不重试）。只改变送读表达，正文不变；不适用于连续通话、WS、长文和其他语言。</p>
   <p v-if="value.performance_preferences">声音表演偏好：{{value.performance_preferences.delivery_target}} 根据整段语义稀疏安排，可以全部留白；不强制每句加标签，不修改声音档案或聊天人格。旧音频保留原表达版本。</p>
   <label v-if="enabled"><input v-model="confirm" type="checkbox" :disabled="busy">我确认所选规划档案及新增模型调用；语音合成仍受原日额限制</label>
   <button type="button" :disabled="busy || (enabled && (!selected || !confirm))" @click="save">保存 AI 表达设置</button>
  </template>
  <p role="status">{{error}}</p>
 </section>
</template>
