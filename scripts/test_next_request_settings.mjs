import assert from 'node:assert/strict'
import fs from 'node:fs'
import {pendingSettings,modelRevision,validOverrides} from '../frontend/src/chat/nextRequestSettings.ts'
const checks=[]
function check(name,fn){fn();checks.push({name,passed:true})}
const scope={user:'synthetic-owner',character:1,conversation:2,model:1,revision:'synthetic-revision'}
function fixture(){const map=new Map();const storage={getItem:k=>map.get(k)??null,setItem:(k,v)=>map.set(k,v),removeItem:k=>map.delete(k)};return {store:pendingSettings(storage),storage,map}}
check('high/medium/low/null and max output survive a new store instance',()=>{
 for(const effort of ['high','medium','low',null]){const {store,storage}=fixture();store.apply(scope,{reasoning_effort:effort,max_tokens:333});assert.deepEqual(pendingSettings(storage).read().value,{reasoning_effort:effort,max_tokens:333})}
})
check('reservation is persisted before transport and cannot be reserved twice',()=>{const {store,storage}=fixture();store.apply(scope,{reasoning_effort:'high'});store.reserve(scope);assert.equal(pendingSettings(storage).read().phase,'sending');assert.throws(()=>store.reserve(scope))})
check('HTTP rejection restores ready state for the same scope',()=>{const {store}=fixture();store.apply(scope,{reasoning_effort:'high'});const ticket=store.reserve(scope);store.reject(ticket,scope);assert.equal(store.read().phase,'ready');assert.equal(store.read().value.reasoning_effort,'high')})
check('accepted request removes the persisted record and cannot revive on refresh',()=>{const {store,storage}=fixture();store.apply(scope,{reasoning_effort:'high'});store.consume(store.reserve(scope));assert.equal(pendingSettings(storage).read(),null)})
for(const [key,value] of [['user','another-owner'],['character',2],['conversation',3],['model',2],['revision','another-revision']]){
 check(key+' identity mismatch blocks reserve and rejection restoration',()=>{const {store}=fixture();store.apply(scope,{reasoning_effort:'medium'});assert.throws(()=>store.reserve({...scope,[key]:value}));const ticket=store.reserve(scope);assert.equal(store.reject(ticket,{...scope,[key]:value}),null);assert.equal(store.read().phase,'sending')})
}
check('old acceptance cannot delete a newly applied setting',()=>{const {store}=fixture();store.apply(scope,{reasoning_effort:'high'});const old=store.reserve(scope);store.apply(scope,{reasoning_effort:'low'});store.consume(old);assert.equal(store.read().value.reasoning_effort,'low')})
check('old rejection cannot overwrite a newly applied setting',()=>{const {store}=fixture();store.apply(scope,{reasoning_effort:'high'});const old=store.reserve(scope);store.apply(scope,{reasoning_effort:'medium'});assert.equal(store.reject(old,scope),null);assert.equal(store.read().value.reasoning_effort,'medium')})
check('explicit clear removes record across store instances',()=>{const {store,storage}=fixture();store.apply(scope,{reasoning_effort:'high'});store.clear();assert.equal(pendingSettings(storage).read(),null)})
check('new conversation null identity is distinct from a saved conversation',()=>{const {store}=fixture();store.apply({...scope,conversation:null},{reasoning_effort:'low'});assert.throws(()=>store.reserve(scope));assert.equal(store.reserve({...scope,conversation:null}).phase,'sending')})
check('invalid, empty and unknown parameters are rejected',()=>{for(const v of [{},[],null,{reasoning_effort:'ultra'},{max_tokens:0},{max_tokens:128001},{max_tokens:'333'},{max_tokens:2.5},{api_key:'do-not-store'}])assert.equal(validOverrides(v),false)})
check('corrupt serialized state fails closed',()=>{const {store,map}=fixture();store.apply(scope,{reasoning_effort:'low'});const key=[...map.keys()][0];for(const raw of ['{broken',JSON.stringify({version:2}),JSON.stringify({...store.read(),scope:{...scope,user:''}})]){map.set(key,raw);assert.throws(()=>store.read())}})
check('failed browser write does not replace the old applied value',()=>{const {store,storage}=fixture();store.apply(scope,{reasoning_effort:'low'});const broken=pendingSettings({...storage,setItem(){throw Error('quota')}});assert.throws(()=>broken.apply(scope,{reasoning_effort:'high'}));assert.equal(store.read().value.reasoning_effort,'low')})
check('model identity includes updated_at and effective configuration fields',()=>{const base={id:1,updated_at:'a',protocol:'openai_responses',reasoning_effort:'low',max_tokens:2048};assert.notEqual(modelRevision(base),modelRevision({...base,updated_at:'b'}));assert.notEqual(modelRevision(base),modelRevision({...base,max_tokens:333}));assert.equal(modelRevision(base),modelRevision({...base,api_key:'never-used'}))})
fs.mkdirSync('evidence',{recursive:true});fs.writeFileSync('evidence/NEXT_SETTINGS_UNIT.json',JSON.stringify({status:'passed',checks},null,2));console.log(`${checks.length} state-store regression cases passed`)
