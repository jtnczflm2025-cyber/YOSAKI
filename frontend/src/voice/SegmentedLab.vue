<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount } from 'vue'
import GlassSurface from '../ui/GlassSurface.vue'
import ThemeToggle from '../ui/ThemeToggle.vue'
import { voiceAudioPlayer } from './voiceAudioPlayer'
type Item={sequence:number;status:string;audio_file?:string}
type State={preview:{source_text:string;characters:number;requests:number;preview_sha256:string;segments:{text:string;characters:number}[]};result:{status:string;error_code:string|null;segments:Item[]};fake_http:number;real_requests:number}
const scenario=ref('normal'),state=ref<State|null>(null),playback=ref('未播放'),error=ref('')
const events=ref<{event:string;sequence?:number;at_ms:number}[]>([])
let timer:ReturnType<typeof setInterval>|undefined,epoch=0
const base=()=>'/api/voice-segmented-lab/'+scenario.value
async function refresh(){const name=scenario.value;const response=await fetch('/api/voice-segmented-lab/'+name);if(!response.ok)throw Error('读取失败');const value=await response.json();if(name===scenario.value)state.value=value}
async function select(){stop();state.value=null;await refresh()}
async function start(){try{const response=await fetch(base()+'/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({preview_sha256:state.value?.preview.preview_sha256})});if(!response.ok)throw Error('预览已失效或运行已占用');await refresh()}catch(e){error.value=String(e)}}
function stop(){epoch++;voiceAudioPlayer.stop();playback.value='已停止；不会自动恢复'}
async function cancel(){stop();await fetch(base()+'/cancel',{method:'POST'});await refresh()}
async function play(){
 stop();const token=epoch,items=state.value?.result.segments.filter(s=>s.status==='completed')??[]
 if(!items.length)return
 for(const item of items){
  if(token!==epoch)return
  events.value.push({event:'segment_start',sequence:item.sequence,at_ms:performance.now()})
  let failed=false
  await voiceAudioPlayer.play(700,[base()+'/audio/'+item.sequence],s=>{
   if(token!==epoch)return
   playback.value=s==='playing'?'正在播放第'+(item.sequence+1)+'段':s
   events.value.push({event:s,sequence:item.sequence,at_ms:performance.now()})
   if(s==='failed')failed=true
  })
  if(token!==epoch||failed)return
 }
 if(token===epoch)playback.value='全部完整片段已播完'
}
onMounted(async()=>{await refresh();timer=setInterval(()=>void refresh().catch(()=>error.value='隔离实例已断开'),300)})
onBeforeUnmount(()=>{clearInterval(timer);stop();void fetch(base()+'/cancel',{method:'POST',keepalive:true})})
</script>
<template><main class="lab"><GlassSurface level="reading">
 <header><h1>分段朗读 · 隔离验证</h1><ThemeToggle/></header>
 <p>仅使用假供应商和合成静音音频，真实外呼为零。完整片段可单独播放；不完整音频不提供播放。</p>
 <label>模拟场景 <select v-model="scenario" @change="select"><option value="normal">两段正常</option><option value="first_rejected">首段拒绝</option><option value="first_unknown">首段未知</option><option value="partial_unknown">接收中断</option><option value="later_failed">第二段失败</option><option value="cancel">段间取消</option></select></label>
 <div v-if="state"><p aria-live="polite">{{state.result.status}} · {{state.result.error_code}} · 假 HTTP {{state.fake_http}} · 真实 {{state.real_requests}}</p>
 <p>{{state.preview.characters}} 个请求字符／{{state.preview.requests}} 段，eleven_v4，mp3_44100_128，zh，无标签。</p>
 <div class="actions"><button @click="start" :disabled="state.result.status!=='not_started'">执行本次模拟</button><button @click="cancel">取消生成并停播</button><button @click="play">播放已完整保存的片段</button><button @click="voiceAudioPlayer.pause(700)">暂停</button><button @click="voiceAudioPlayer.resume(700)">继续</button><button @click="stop">仅停止播放</button></div>
 <p role="status">{{playback}}</p><p role="alert">{{error}}</p>
 <ol><li v-for="item in state.result.segments" :key="item.sequence">第{{item.sequence+1}}段：{{item.status}} <a v-if="item.status==='completed'" :href="base()+'/audio/'+item.sequence" target="_blank" rel="noopener">完整音频</a></li></ol>
 <details><summary>完整正文与逐段送读预览</summary><pre>{{state.preview.source_text}}</pre><section v-for="(segment,i) in state.preview.segments" :key="i"><h2>第{{i+1}}段 · {{segment.characters}}字</h2><pre>{{segment.text}}</pre></section></details>
 <details><summary>本地播放时点</summary><pre>{{JSON.stringify(events,null,2)}}</pre></details>
 </div></GlassSurface></main></template>
<style scoped>
.lab{max-width:900px;margin:24px auto;padding:16px;color:var(--ui-ink);font:17px/1.75 var(--ui-font)}
.lab :deep(.glass-surface){padding:24px}
header{display:flex;align-items:center;justify-content:space-between}h1{font-size:25px}.actions{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0}button,select{min-height:44px;background:var(--ui-raised);color:var(--ui-ink);border:1px solid var(--ui-line);border-radius:12px;padding:8px 12px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}h2{font-size:20px}section{border-top:1px solid var(--ui-line);margin-top:16px}
</style>
