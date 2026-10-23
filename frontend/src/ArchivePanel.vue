<script setup lang="ts">
import { computed, ref } from 'vue'
const props=defineProps<{characterId:number; conversations:{id:number;title:string}[]}>()
const emit=defineEmits<{imported:[]}>()
const selected=ref<number[]>([])
const format=ref('json')
const after=ref('')
const before=ref('')
const firstTurn=ref<number|undefined>()
const lastTurn=ref<number|undefined>()
const includeCode=ref(true)
const includeTools=ref(false)
const includeReasoning=ref(false)
const includeMonologue=ref(false)
const includeTime=ref(true)
const paragraphMode=ref('keep')
const userName=ref('用户')
const assistantName=ref('助手')
const replacements=ref<{from:string;to:string}[]>([{from:'',to:''}])
const files=ref<File[]>([])
const previews=ref<any[]>([])
const status=ref('')
const busy=ref(false)
const exportPreview=ref<{original:string;selected:string;truncated:boolean;tool_messages_added:number}|null>(null)
const options=computed(()=>({conversation_ids:selected.value,format:format.value,after:after.value||null,before:before.value||null,first_turn:firstTurn.value||null,last_turn:lastTurn.value||null,include_code:includeCode.value,include_tools:includeTools.value,include_reasoning:includeReasoning.value,include_monologue:includeMonologue.value,include_time:includeTime.value,paragraph_mode:paragraphMode.value,user_name:userName.value,assistant_name:assistantName.value,replacements:replacements.value.filter(x=>x.from).map(x=>[x.from,x.to])}))
async function request(url:string,body:BodyInit,headers?:HeadersInit){const r=await fetch(url,{method:'POST',body,headers,credentials:'same-origin'});if(!r.ok)throw new Error((await r.json().catch(()=>({}))).detail||`请求失败 ${r.status}`);return r}
async function exportSelected(){if(!selected.value.length){status.value='请先选择对话';return}busy.value=true;try{const r=await request('/api/archives/export',JSON.stringify(options.value),{'Content-Type':'application/json'});const blob=await r.blob();const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='yosaki-chats.zip';a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);status.value='聊天档案已导出'}catch(e){status.value=String(e)}finally{busy.value=false}}
async function previewExport(){if(!selected.value.length){status.value='请先选择对话';return}try{exportPreview.value=await (await request('/api/archives/export-preview',JSON.stringify(options.value),{'Content-Type':'application/json'})).json()}catch(e){status.value=String(e)}}
function saveTemplate(){localStorage.setItem('YOSAKI.archive-template.v1',JSON.stringify({...options.value,conversation_ids:[]}));status.value='清理模板已保存到当前浏览器'}
function loadTemplate(){try{const saved=JSON.parse(localStorage.getItem('YOSAKI.archive-template.v1')||'null');if(!saved)return;format.value=saved.format;after.value=saved.after||'';before.value=saved.before||'';firstTurn.value=saved.first_turn||undefined;lastTurn.value=saved.last_turn||undefined;includeCode.value=saved.include_code;includeTools.value=saved.include_tools;includeReasoning.value=saved.include_reasoning;includeMonologue.value=saved.include_monologue;includeTime.value=saved.include_time!==false;paragraphMode.value=saved.paragraph_mode==='compact'?'compact':'keep';userName.value=saved.user_name;assistantName.value=saved.assistant_name;replacements.value=saved.replacements?.length?saved.replacements.map((x:string[])=>({from:x[0],to:x[1]})):[{from:'',to:''}];status.value='已加载清理模板'}catch{status.value='模板格式错误'}}
async function chooseFiles(event:Event){files.value=Array.from((event.target as HTMLInputElement).files||[]);previews.value=[];if(!files.value.length)return;busy.value=true;try{const form=new FormData();files.value.forEach(f=>form.append('files',f));previews.value=await (await request('/api/archives/preview',form)).json()}catch(e){status.value=String(e)}finally{busy.value=false}}
async function importFiles(){if(!files.value.length)return;busy.value=true;try{const form=new FormData();files.value.forEach(f=>form.append('files',f));const result=await (await request(`/api/archives/import?character_id=${props.characterId}`,form)).json();const names:{[key:string]:string}={imported:'已导入',skipped:'重复跳过',failed:'失败'};status.value=result.map((x:any)=>`${x.name}：${names[x.status]||x.status}${x.error?' · '+x.error:''}`).join('；');emit('imported')}catch(e){status.value=String(e)}finally{busy.value=false}}
</script>
<template>
  <details class="v2-panel"><summary>批量聊天档案</summary>
    <p>JSON 可完整迁移；Markdown / TXT 用于阅读与约定格式导入。清理选项只影响导出副本。</p>
    <div class="v2-scroll"><label v-for="conversation in conversations" :key="conversation.id"><input v-model="selected" type="checkbox" :value="conversation.id"/>{{conversation.title}}</label></div>
    <div class="v2-grid"><label>格式<select v-model="format"><option value="json">JSON 完整档案</option><option value="markdown">Markdown</option><option value="txt">TXT</option></select></label><label>起始日期<input v-model="after" type="date"/></label><label>结束日期<input v-model="before" type="date"/></label><label>起始轮次<input v-model.number="firstTurn" type="number" min="1"/></label><label>结束轮次<input v-model.number="lastTurn" type="number" min="1"/></label><label>用户称呼<input v-model="userName"/></label><label>角色称呼<input v-model="assistantName"/></label><label>段落格式<select v-model="paragraphMode"><option value="keep">保留原段落</option><option value="compact">合并空行</option></select></label></div>
    <div v-for="(rule,index) in replacements" :key="index" class="v2-grid"><label>替换文字<input v-model="rule.from" placeholder="原文字"/></label><label>替换为<input v-model="rule.to" placeholder="新文字"/></label><button @click="replacements.splice(index,1)">移除规则</button></div><button v-if="replacements.length<20" @click="replacements.push({from:'',to:''})">添加替换规则</button>
    <div class="v2-checks"><label><input v-model="includeCode" type="checkbox"/>保留代码</label><label><input v-model="includeTools" type="checkbox"/>保留工具详情</label><label><input v-model="includeReasoning" type="checkbox"/>公开推理</label><label><input v-model="includeMonologue" type="checkbox"/>内心独白</label><label><input v-model="includeTime" type="checkbox"/>保留时间</label></div>
    <button :disabled="busy" @click="previewExport">预览清理结果</button><button @click="saveTemplate">保存模板</button><button @click="loadTemplate">加载模板</button><button :disabled="busy" @click="exportSelected">批量导出 ZIP</button>
    <div v-if="exportPreview" class="v2-grid"><div><strong>原始预览</strong><pre>{{exportPreview.original}}</pre></div><div><strong>导出预览</strong><pre>{{exportPreview.selected}}</pre></div><small v-if="exportPreview.tool_messages_added">完整 JSON 已补齐 {{exportPreview.tool_messages_added}} 条关联工具消息。</small><small v-if="exportPreview.truncated">仅显示前 5000 字</small></div>
    <h4>导入聊天</h4><input type="file" multiple accept=".json,.md,.txt" @change="chooseFiles"/>
    <div v-for="preview in previews" :key="preview.name">{{preview.name}}：{{preview.error||`${preview.messages} 条消息${preview.duplicate?' · 已导入过':''}`}}</div>
    <button :disabled="busy||!previews.some(x=>!x.error&&!x.duplicate)" @click="importFiles">追加到当前角色</button>
    <p role="status">{{status}}</p>
  </details>
</template>
<style scoped>.v2-panel{padding:12px;border:1px solid #716f83;border-radius:12px;margin:8px}.v2-panel summary{cursor:pointer}.v2-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(135px,1fr));gap:8px;margin:10px 0}.v2-grid label,.v2-scroll label{display:flex;flex-direction:column;gap:4px}.v2-grid input,.v2-grid select{width:100%}.v2-scroll{max-height:110px;overflow:auto;display:flex;flex-direction:column}.v2-scroll label,.v2-checks label{flex-direction:row}.v2-checks{display:flex;gap:12px;flex-wrap:wrap;margin:8px 0}.v2-panel input:not([type=checkbox]),.v2-panel select{background:#242230;color:#f2eefb;border:1px solid #625a78;border-radius:6px;padding:6px;min-width:0}.v2-panel button,.v2-panel input[type=file]::file-selector-button{background:#373047;color:#f2ecff;border:1px solid #645a80;border-radius:7px;padding:7px 10px;margin:4px 6px 4px 0}.v2-panel button:disabled{opacity:.5;cursor:not-allowed}.v2-panel pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:280px;overflow:auto;background:#1d1b28;border:1px solid #3b364c;padding:10px;border-radius:8px;color:#ded7f0}</style>
