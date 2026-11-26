// Isolated visual harness. Builds instrumented UI into a temporary directory only.
// No backend, database, microphone, WebSocket, or provider requests are used.
import { build } from 'vite';
import vue from '@vitejs/plugin-vue';
import { mkdtempSync, readFileSync, existsSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import http from 'node:http';
const root=path.resolve('frontend');
const out=mkdtempSync(path.join(tmpdir(),'yosaki-chat-ui-'));
const fixture=String.raw`
async function refreshAuth(){
  authenticated.value=true; authReady.value=true; apiOnline.value=true;
  modelProfiles.value=[{id:9001,name:'合成验收模型',model_name:'gpt-6.1-sol',base_url:'https://api.openai.com/v1',api_key_configured:true,protocol:'openai_responses',enabled:true} as ModelProfile];
  modelProfileId.value=9001; model.value='合成验收模型';
  conversations.value=[{id:9001,character_id:1,title:'雨后的散步 · 合成中日文长对话',created_at:'2026-11-17',updated_at:'2026-11-17',model_profile_id:9001}];
  activeConversation.value=9001;
  messages.value=[{role:'user',content:'今天想慢慢走一段路。你愿意陪我聊聊吗？',time:'21:00'},
    {role:'assistant',content:Array.from({length:30},(_,i)=>'第 '+(i+1)+' 段。我们可以先把今天放慢一点，不急着替每件事情找一个结论。\n\n「急がなくていい。今日は、少し遠回りをして帰ろう。話したいことから、ゆっくり聞かせてくれ。」').join('\n\n').replaceAll('\\n','\n'),time:'21:01'}];
  openFeaturePage('chat');
  const sample=new URLSearchParams(location.search).get('ui_case') || sessionStorage.getItem('ui-case') || 'ready';
  await nextTick(); await refreshAutoBudget();
  callAvailability.value={conversation_id:9001,conversation_title:'合成阅读样本',available_rounds:2,active_call:false,conversation_incomplete:false,model_path_ready:true,scribe_remaining:{attempts:10,connections:10,audio_seconds:600},tts_remaining:{jobs:10,requests:10,characters:1000}};
  chronicleChatCap.value=37000;
  if(sample==='recording'){callId.value='ui-only';callPhase.value='listening';callStatus.value='模拟录音中，未连接麦克风';}
  if(sample==='playing'){callId.value='ui-only';callPhase.value='playing';callPlaybackStarted.value=true;callStatus.value='模拟播放中，未合成音频';}
  if(sample==='error'){callStatus.value='今日录音额度不足；文字聊天仍可继续。';callAvailability.value.available_rounds=0;callAvailability.value.scribe_remaining.attempts=0;errorMessage.value='语音连接失败，未发送音频。';}
  if(sample==='tool'){messages.value.unshift({role:'assistant',content:'请先确认这次工具调用。',time:'21:00',toolRuns:[{run_id:9001,tool_name:'合成工具',server_name:'本地验收',description:'仅模拟等待确认，不执行工具。',arguments:{query:'散步'},status:'pending_confirmation'}]});}
  if(sample==='float'){openWindow('chat');}
}
`;
const bootstrap=String.raw`
const caseName=new URLSearchParams(location.search).get('ui_case')||'ready'; sessionStorage.setItem('ui-case',caseName);
const nativeFetch=window.fetch.bind(window);
window.fetch=async(input,init)=>{
 const url=String(input); if(!url.startsWith('/api/')){if(url.startsWith('/assets/'))return nativeFetch(input,init);throw Error('Fixture blocks network');}
 const method=(init?.method||'GET').toUpperCase();
 if(method!=='GET'){const body=JSON.parse(init?.body||'{}');console.info('UI_FIXTURE_BLOCKED_WRITE',JSON.stringify({path:url,method,chronicleCap:body.chronicle_supplement_max_microusd??null}));return new Response(JSON.stringify({detail:'合成验收禁止实际操作'}),{status:403});}
 const counts={jobs:10,requests:10,characters:1000};
 let value={};
 if(url.includes('chronicle-supplement-status'))value={enabled:true,maxMicrousd:37000};
 else if(url.includes('auto-budget'))value={daily:{limits:counts,used:{jobs:0,requests:0,characters:0},remaining:counts}};
 else if(url.includes('scribe-budget'))value={enabled:true,service_enabled:true,limits:{attempts:10,connections:10,audio_seconds:600},remaining:{attempts:10,connections:10,audio_seconds:600},available_attempts:10,per_attempt_audio_seconds:60};
 else if(url.includes('calls/settings'))value={limits:{rounds:2,seconds:180,tts_requests:2,tts_characters:160},maximum:{rounds:10,seconds:600,tts_requests:10,tts_characters:1000},configurable:true,revision:1};
 else if(url.includes('availability'))value={available_rounds:caseName==='error'?0:2,scribe_remaining:{attempts:10,connections:10,audio_seconds:600},tts_remaining:counts,model_path_ready:true};
 else return new Response(JSON.stringify({detail:'合成验收未提供此接口'}),{status:404});
 return new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json'}});
};
if(navigator.mediaDevices)navigator.mediaDevices.getUserMedia=async()=>{throw Error('Fixture blocks microphone')};
window.WebSocket=class {constructor(){throw Error('Fixture blocks sockets')}};
window.speechSynthesis?.cancel();
if(window.speechSynthesis)window.speechSynthesis.speak=()=>{throw Error('Fixture blocks speech playback')};
window.SpeechRecognition=window.webkitSpeechRecognition=class {start(){throw Error('Fixture blocks speech recognition')} stop(){} abort(){}};
`;
await build({root,configFile:false,plugins:[{
 name:'ui-only-fixture',enforce:'pre',transform(code,id){
  if(id.endsWith('/main.ts')) return bootstrap+'\n'+code;
  if(id.endsWith('/App.vue'))return code.replace(/async function refreshAuth\(\)\{[^\n]+\}/,fixture);
  if(id.endsWith('/ScribeDraftInput.vue'))return code.replace('if (props.continuousMode && props.startSignal) startContinuousCapture()','/* no capture in visual fixture */').replace('</script>',`onMounted(()=>{if(sessionStorage.getItem('ui-case')==='scribe' && !props.continuousMode){running.value=true;captureReady.value=true;status.value='模拟录音中；未连接麦克风';emit('running-change',true)}});\n</script>`);
 }},vue()],define:Object.fromEntries(['CHRONICLE_A0','VOICE_AUTO_READ_ISOLATED','SCRIBE_DRAFT_ISOLATED','SCRIBE_DAILY','VOICE_CONTINUOUS_DAILY','VOICE_CALL_SETTINGS'].map(k=>['import.meta.env.VITE_YOSAKI_'+k,JSON.stringify('1')])),build:{outDir:out,emptyOutDir:false}});
const port=Number(process.argv[2]||65118);
http.createServer((req,res)=>{
 const url=new URL(req.url,'http://localhost');
 if(url.pathname.startsWith('/api/')){res.writeHead(403);res.end('No backend in UI fixture');return;}
 const file=url.pathname.startsWith('/assets/')?path.join(out,url.pathname):path.join(out,'index.html');
 if(!file.startsWith(out)||!existsSync(file)||!statSync(file).isFile()){res.writeHead(404);res.end();return;}
 const type={'.js':'text/javascript','.css':'text/css','.json':'application/json','.png':'image/png','.html':'text/html'}[path.extname(file)]||'application/octet-stream';
 res.writeHead(200,{'Content-Type':type});res.end(readFileSync(file));
}).listen(port,'127.0.0.1',()=>console.log(JSON.stringify({url:'http://127.0.0.1:'+port+'/chat?ui_case=ready',out})));
