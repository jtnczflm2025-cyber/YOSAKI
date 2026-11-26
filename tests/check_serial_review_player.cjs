// Evaluate the ACTUAL cached review HTML with synthetic DOM/Audio; no network.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict')
const html=fs.readFileSync(process.argv[2],'utf8'),count=Number(process.argv[3])
const nodes=Object.fromEntries(['play','pause','resume','stop','state'].map(k=>['#'+k,{textContent:''}]))
const played=[]
class Audio{
 constructor(url){this.url=url;played.push(this)}
 play(){this.paused=false;return Promise.resolve()}
 pause(){this.paused=true}
 removeAttribute(){}load(){}
}
const context={Audio,document:{querySelector:s=>nodes[s]},window:{addEventListener(){}}}
vm.runInNewContext(html.match(/<script>([\s\S]*?)<\/script>/)[1],context)
nodes['#play'].onclick()
for(let i=0;i<count;i++){assert.equal(played[i].url,'/audio/'+i);played[i].onended()}
assert.equal(nodes['#state'].textContent,'已播完')
nodes['#play'].onclick();const old=played.at(-1).onended,before=played.length
nodes['#stop'].onclick();old();assert.equal(played.length,before);assert.equal(nodes['#state'].textContent,'已停止')
nodes['#play'].onclick();assert.equal(played.at(-1).url,'/audio/0')
console.log(JSON.stringify({ordered_segments:count,complete:true,stop_and_late_end_isolated:true,replay:true}))
