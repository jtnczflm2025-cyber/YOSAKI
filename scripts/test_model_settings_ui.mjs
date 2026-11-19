// Synthetic UI regression. The supported browser CDP capability can be supplied
// through runModelSettingsUI; direct CLI mode uses a dedicated test-only CDP port.
import fs from 'node:fs/promises'
import assert from 'node:assert/strict'
import path from 'node:path'
import {fileURLToPath} from 'node:url'
export async function runModelSettingsUI(cdp,{BASE='http://yosaki-settings.localhost:28187',OUT='evidence/MODEL_SETTINGS_REGRESSION.json'}={}){
assert.equal(BASE,'http://yosaki-settings.localhost:28187','Use the dedicated synthetic hostname; cookies are not isolated by port')
const checks=[]
async function ev(expression){const r=await cdp('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true,userGesture:true});if(r.exceptionDetails)throw Error(r.exceptionDetails.exception?.description);return r.result.value===undefined?undefined:JSON.parse(JSON.stringify(r.result.value))}
async function until(expression){for(let n=0;n<70;n++){try{if(await ev(expression))return}catch(e){if(!String(e).includes('TypeError'))throw e}await new Promise(r=>setTimeout(r,100))}throw Error('Timed out: '+expression)}
async function go(path){await ev(`window.__navigationOld=true`);await cdp('Page.navigate',{url:BASE+path});await until(`window.__navigationOld!==true&&location.pathname===${JSON.stringify(path)}&&document.readyState==='complete'&&Boolean(document.querySelector('#app'))`);await ev(`(async()=>{const s=await fetch('/api/auth/status').then(r=>r.json());if(!s.authenticated){await fetch(s.setup_required?'/api/auth/setup':'/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'synthetic-owner',password:'Synthetic-Browser-20261118'})});location.reload()}})()`);await until(`document.querySelector('#app')?._vnode?.component?.setupState?.authenticated===true`);await until(`document.querySelector('#app')._vnode.component.setupState.modelProfiles.length===2`);const rows=await ev(`fetch('/api/conversations?character_id=1').then(r=>r.json())`);await until(`${vm}.conversations.length===${rows.length}&&${vm}.activeConversation===${rows[0]?.id??'null'}`);await until(`${vm}.nextSettingsReady===true`);await new Promise(r=>setTimeout(r,100))}
const vm=`document.querySelector('#app')._vnode.component.setupState`
const effort=`[...document.querySelectorAll('label')].find(e=>e.innerText.startsWith('推理强度')).querySelector('select')`
const click=t=>`[...document.querySelectorAll('button')].find(e=>e.innerText===${JSON.stringify(t)}).click()`
async function profile(id){await ev(`document.querySelectorAll('.model-list-item')[${id===1?0:1}].click()`)}
async function setEffort(value){await ev(`{const e=${effort};e.value=${JSON.stringify(value)};e.dispatchEvent(new Event('change',{bubbles:true}))}`)}
async function saved(){await until(`${vm}.modelSaving===false`);assert.match(await ev(`${vm}.modelTestStatus`),/^档案已保存/)}
async function check(name,fn){await fn();checks.push({name,passed:true});await fs.writeFile(OUT,JSON.stringify({status:'running',checks},null,2))}
try{
 await cdp('Network.enable');await cdp('Network.setCacheDisabled',{cacheDisabled:true})
 await go('/models')
 const profiles=await ev(`fetch('/api/models').then(r=>r.json())`);assert(profiles.every(p=>p.model_name==='synthetic-only'&&p.base_url.startsWith('http://127.0.0.1:28186/')))
 await check('model PATCH payload, response, GET and form agree',async()=>{
  await ev(`${vm}.editModel(${vm}.modelProfiles.find(p=>p.id===1))`);await setEffort('high')
  await ev(`window.trace=[];window.realFetch=window.fetch;window.fetch=async(...args)=>{const r=await realFetch(...args);if(String(args[0]).startsWith('/api/models')&&args[1]?.method==='PATCH')trace.push({payload:JSON.parse(args[1].body),status:r.status,response:await r.clone().json()});return r};${click('保存配置')}`)
  await saved();const trace=await ev('trace.at(-1)');assert.equal(trace.status,200);assert.equal(trace.payload.reasoning_effort,'high');assert.equal(trace.response.reasoning_effort,'high');assert.equal(await ev(`fetch('/api/models').then(r=>r.json()).then(a=>a.find(p=>p.id===1).reasoning_effort)`),'high')
 })
 await check('reopen model page and hard refresh hydrate saved profile',async()=>{
  await go('/chat');await go('/models');assert.equal(await ev(`${effort}.value`),'high');assert.equal(await ev(`${vm}.editingModelId`),1)
  await go('/models');await until(`${vm}.modelEditorReady===true`);assert.equal(await ev(`${effort}.value`),'high')
 })
 await check('switch profile and return retains saved effort',async()=>{
  await ev(`${vm}.editModel(${vm}.modelProfiles.find(p=>p.id===2))`);assert.equal(await ev(`${effort}.value`),'medium');await ev(`${vm}.editModel(${vm}.modelProfiles.find(p=>p.id===1))`);assert.equal(await ev(`${effort}.value`),'high')
 })
 await check('unsupported effort 422 does not claim saved or change database',async()=>{
  await ev(`{const e=${effort};e.add(new Option('unsupported','ultra'));e.value='ultra';e.dispatchEvent(new Event('change',{bubbles:true}))};${click('保存配置')}`)
  await until(`${vm}.modelSaving===false`);assert.match(await ev(`${vm}.modelTestStatus`),/^保存失败/);assert.equal(await ev(`fetch('/api/models').then(r=>r.json()).then(a=>a.find(p=>p.id===1).reasoning_effort)`),'high')
  await ev(`${vm}.editModel(${vm}.modelProfiles.find(p=>p.id===1))`)
 })
 await check('HTTP failure keeps draft and displays backend error',async()=>{
  await ev(`window.originalFetch=window.fetch;window.fetch=(...a)=>String(a[0])==='/api/models/1'&&a[1]?.method==='PATCH'?Promise.resolve(new Response(JSON.stringify({detail:'合成保存失败'}),{status:500,headers:{'Content-Type':'application/json'}})):originalFetch(...a)`)
  await setEffort('low');await ev(click('保存配置'));await until(`${vm}.modelSaving===false`);assert.match(await ev(`${vm}.modelTestStatus`),/保存失败.*合成保存失败/);assert.equal(await ev(`${effort}.value`),'low');await ev('window.fetch=originalFetch')
  await ev(`${vm}.editModel(${vm}.modelProfiles.find(p=>p.id===1))`)
 })
 await check('late pre-save GET cannot overwrite saved profile or draft',async()=>{
  await ev(`window.originalFetch=window.fetch;window.releaseOld=null;window.fetch=async(...a)=>{if(String(a[0])==='/api/models'&&(!a[1]?.method||a[1].method==='GET')){window.fetch=originalFetch;const r=await originalFetch(...a),body=await r.json();body.find(p=>p.id===1).reasoning_effort='low';await new Promise(resolve=>releaseOld=resolve);return new Response(JSON.stringify(body),{headers:{'Content-Type':'application/json'}})}return originalFetch(...a)};void ${vm}.loadModels()`)
  await until('typeof releaseOld===\'function\'');await setEffort('high');await ev(click('保存配置'));await saved();await ev('releaseOld()');await new Promise(r=>setTimeout(r,120));assert.equal(await ev(`${vm}.modelProfiles.find(p=>p.id===1).reasoning_effort`),'high');assert.equal(await ev(`${effort}.value`),'high')
 })
 await go('/chat');await ev(click('请求／分支／上下文'));await until('Boolean(document.querySelector(\'.chat-enhancement-panel\'))')
 await check('one-shot validates via preview and does not change model profile',async()=>{
  await setEffort('low');await ev(click('应用于下一次'));await until(`${vm}.nextOverrides.reasoning_effort==='low'`);assert.match(await ev(`document.querySelector('.chat-enhancement-panel').innerText`),/来源：单次设置/)
  const p=await ev(`fetch('/api/chat/context-preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({character_id:1,model_profile_id:1,message:'合成预览',overrides:${vm}.nextOverrides})}).then(r=>r.json())`);assert.equal(p.serialized.reasoning.effort,'low');assert.equal(await ev(`${vm}.modelProfiles.find(p=>p.id===1).reasoning_effort`),'high')
 })
 await check('unsupported one-shot reports validation failure and preserves previous setting',async()=>{
  await ev(`{const e=${effort};e.add(new Option('unsupported','ultra'));e.value='ultra';e.dispatchEvent(new Event('change',{bubbles:true}))};${click('应用于下一次')}`)
  await until(`document.querySelector('.chat-enhancement-panel').innerText.includes('此推理强度尚未接入')`);assert.equal(await ev(`${vm}.nextOverrides.reasoning_effort`),'low');await setEffort('low')
 })
 await check('drawer remount preserves pending override and source',async()=>{
  await ev(`document.querySelector('[aria-label=关闭面板]').click()`);await ev(click('请求／分支／上下文'));await until('Boolean(document.querySelector(\'.chat-enhancement-panel\'))');assert.equal(await ev(`${effort}.value`),'low');assert.match(await ev(`document.querySelector('.chat-enhancement-panel').innerText`),/刷新或重开抽屉均保留/)
 })
 await check('hard reload preserves pending one-shot; explicit clear restores model defaults',async()=>{
  await go('/chat');await until(`${vm}.modelProfiles.length===2`);await ev(click('请求／分支／上下文'));await until('Boolean(document.querySelector(\'.chat-enhancement-panel\'))');assert.equal(await ev(`${effort}.value`),'low');assert.equal(await ev(`${vm}.nextOverrides.reasoning_effort`),'low');await ev(click('清除单次设置'));await until(`Object.keys(${vm}.nextOverrides).length===0`);const p=await ev(`fetch('/api/chat/context-preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({character_id:1,model_profile_id:1,message:'合成预览'})}).then(r=>r.json())`);assert.equal(p.serialized.reasoning.effort,'high')
 })
 await check('HTTP rejection retains one-shot; accepted mock send consumes it',async()=>{
  await setEffort('low');await ev(click('应用于下一次'));await until(`${vm}.nextOverrides.reasoning_effort==='low'`);await ev(`document.querySelector('[aria-label=关闭面板]').click();window.realSendFetch=window.fetch;window.fetch=(...a)=>String(a[0])==='/api/chat/stream'?Promise.resolve(new Response(JSON.stringify({detail:'合成拒绝'}),{status:422,headers:{'Content-Type':'application/json'}})):realSendFetch(...a);${vm}.draft='合成发送，无真实模型';void ${vm}.send()`);await until(`${vm}.isStreaming===false`);assert.equal(await ev(`${vm}.nextOverrides.reasoning_effort`),'low')
  await ev(`window.fetch=realSendFetch;void ${vm}.send()`);await until(`${vm}.isStreaming===false&&${vm}.messages.some(m=>m.role==='assistant')`);assert.deepEqual(await ev(`${vm}.nextOverrides`),{});assert.equal(await ev(`${vm}.modelProfiles.find(p=>p.id===1).reasoning_effort`),'high')
 })
 await check('accepted mock regenerate consumes one-shot without saving profile',async()=>{
  await ev(click('请求／分支／上下文'));await until('Boolean(document.querySelector(\'.chat-enhancement-panel\'))');await setEffort('low');await ev(click('应用于下一次'));await until(`${vm}.nextOverrides.reasoning_effort==='low'`);await ev(`void ${vm}.regenerateLast()`);await until(`${vm}.isStreaming===false&&Object.keys(${vm}.nextOverrides).length===0`);assert.equal(await ev(`fetch('/api/models').then(r=>r.json()).then(a=>a.find(p=>p.id===1).reasoning_effort)`),'high')
 })
 await fs.writeFile(OUT,JSON.stringify({status:'passed',checks,real_model_calls:0},null,2));console.log(`${checks.length} end-to-end checks passed; only isolated mock sends`);return checks
}catch(e){await fs.writeFile(OUT,JSON.stringify({status:'failed',checks,error:String(e)},null,2));throw e}finally{}


}
if(typeof process!=='undefined'&&process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 const tabs=await fetch('http://127.0.0.1:29230/json/list').then(r=>r.json())
 const target=tabs.find(t=>t.type==='page'&&t.url.startsWith('http://yosaki-settings.localhost:28187/'));assert(target,'Only isolated fixture page allowed')
 const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise(r=>ws.onopen=r)
 let seq=0;const pending=new Map()
 ws.onmessage=({data})=>{const r=JSON.parse(data);if(pending.has(r.id)){pending.get(r.id)(r);pending.delete(r.id)}}
 async function cdp(method,params={}){const r=await new Promise(resolve=>{const id=++seq;pending.set(id,resolve);ws.send(JSON.stringify({id,method,params}))});if(r.error)throw Error(JSON.stringify(r.error));return r.result}
 try{await runModelSettingsUI(cdp)}finally{ws.close()}
}
