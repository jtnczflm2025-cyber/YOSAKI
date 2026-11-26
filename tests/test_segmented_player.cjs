// Existing production player, synthetic Audio; no browser/provider network.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict')
const ts=require('../frontend/node_modules/typescript')
const source=ts.transpileModule(fs.readFileSync('frontend/src/voice/voiceAudioPlayer.ts','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText
const elements=[]
class FakeAudio{
 constructor(url){this.url=url;elements.push(this)}
 play(){return Promise.resolve()}
 pause(){this.paused=true}
 removeAttribute(){}
 load(){}
}
const context={exports:{},Audio:FakeAudio};vm.runInNewContext(source,context)
const player=context.exports.voiceAudioPlayer
;(async()=>{
 const states=[]
 const run=player.play(1,['first','second','third'],s=>states.push(s))
 await Promise.resolve();assert.equal(elements.length,1)
 elements[0].onended();await new Promise(r=>setImmediate(r));assert.equal(elements[1].url,'second')
 elements[1].onended();await new Promise(r=>setImmediate(r));assert.equal(elements[2].url,'third')
 elements[2].onended();await run;assert.equal(states.at(-1),'ended')
 const before=elements.length
 const stopped=player.play(2,['old1','old2'],s=>states.push(s))
 const late=elements.at(-1).onended;player.stop(2);late();await stopped
 assert.equal(elements.length,before+1);assert.equal(states.at(-1),'stopped')
 const now=player.play(3,['new'],s=>states.push(s));late();assert.equal(elements.at(-1).url,'new')
 elements.at(-1).onended();await now
 console.log('3/3 player checks passed: order, stop/late end, old-to-new isolation')
})().catch(e=>{console.error(e);process.exitCode=1})
