const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('../frontend/node_modules/typescript')
const entry = path.resolve(__dirname, '../frontend/src/voice/localBargeIn.ts')
const modules = new Map()
function loadTs(filename) {
  if (modules.has(filename)) return modules.get(filename).exports
  const local = {exports:{}}
  modules.set(filename,local)
  const source = fs.readFileSync(filename,'utf8')
  const output = ts.transpileModule(source,{compilerOptions:{
    module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020,
  }}).outputText.replaceAll('import.meta.env.VITE_YOSAKI_BARGE_FAKE_TEST', '"0"')
  const localRequire = specifier => specifier.startsWith('.')
    ? loadTs(path.resolve(path.dirname(filename),specifier+'.ts')) : require(specifier)
  new Function('require','module','exports',output)(localRequire,local,local.exports)
  return local.exports
}
const { LocalBargeMonitor } = loadTs(entry)

class Track {
  stopped = 0
  stop() { this.stopped++ }
  addEventListener() {}
}
class Stream {
  track = new Track()
  getTracks() { return [this.track] }
}
class Node {
  disconnected = false
  onaudioprocess = null
  connect() {}
  disconnect() { this.disconnected = true }
}
class Context {
  static instances = []
  state = 'running'
  sampleRate = 16000
  destination = {}
  closed = false
  processor = null
  constructor() { Context.instances.push(this) }
  async resume() {}
  async close() { this.closed = true }
  createMediaStreamSource() { return new Node() }
  createScriptProcessor() { this.processor = new Node(); return this.processor }
}
globalThis.AudioContext = Context

async function run() {
  let resolveMedia
  Object.defineProperty(globalThis, 'navigator', {configurable:true,
    value:{mediaDevices:{getUserMedia:()=>new Promise(resolve => {
      resolveMedia=resolve
    })}}})
  const monitor = new LocalBargeMonitor()
  let triggered=0, unavailable=0, ready=0, handoff=null
  const pending=monitor.start(capture=>{triggered++;handoff=capture},()=>unavailable++,'',()=>ready++)
  monitor.stop()
  const late=new Stream()
  resolveMedia(late)
  await pending
  assert.equal(late.track.stopped,1, 'late permission releases microphone')
  assert.equal(Context.instances.length,0)
  assert.equal(ready,0)

  const live=new Stream()
  globalThis.navigator.mediaDevices.getUserMedia=async()=>live
  await monitor.start(capture=>{triggered++;handoff=capture},()=>unavailable++,'',()=>ready++)
  const context=Context.instances.at(-1)
  const processor=context.processor
  assert.ok(processor?.onaudioprocess)
  assert.equal(ready,0, 'permission and a running AudioContext do not establish frame delivery')
  const samples=new Float32Array(1600).fill(0.1)
  const frame={inputBuffer:{getChannelData:()=>samples},
    outputBuffer:{getChannelData:()=>new Float32Array(1600)}}
  for(let i=0;i<4;i++) processor.onaudioprocess(frame)
  assert.equal(ready,1)
  assert.equal(triggered,0)
  processor.onaudioprocess(frame)
  assert.equal(triggered,1)
  assert.equal(live.track.stopped,0, 'the utterance must survive the trigger')
  assert.equal(monitor.take(handoff),handoff)
  monitor.stop()
  assert.equal(live.track.stopped,0, 'phase change must not discard handed-off speech')
  let samplesReceived=0
  assert.equal(handoff.subscribe(chunk=>{samplesReceived+=chunk.length}),true)
  processor.onaudioprocess(frame)
  assert.equal(samplesReceived,9600,
    'the same five-frame preroll and next frame transfer once without a second mic')
  assert.equal(handoff.subscribe(()=>{}),false, 'the same utterance cannot be claimed twice')
  handoff.close()
  assert.equal(live.track.stopped,1)
  assert.equal(processor.onaudioprocess,null)
  assert.equal(context.closed,true)
  assert.equal(unavailable,0)
  monitor.stop()
  assert.equal(triggered,1)
  assert.equal(handoff.subscribe(()=>{}),false, 'released speech cannot be replayed')
  const second=new Stream()
  globalThis.navigator.mediaDevices.getUserMedia=async()=>second
  await monitor.start(capture=>{handoff=capture},()=>unavailable++)
  const secondProcessor=Context.instances.at(-1).processor
  for(let i=0;i<5;i++)secondProcessor.onaudioprocess(frame)
  monitor.stop()
  assert.equal(second.track.stopped,1, 'cancel releases unclaimed speech')
  globalThis.navigator.mediaDevices.getUserMedia=async()=>{throw new Error('synthetic_denial')}
  await monitor.start(capture=>{triggered++;handoff=capture},()=>unavailable++)
  assert.equal(unavailable,1)
  assert.equal(triggered,1)
  console.log('local barge monitor release and late permission: pass')
}
run().catch(error=>{console.error(error);process.exitCode=1})
