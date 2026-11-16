import assert from 'node:assert/strict'
import { acquireScribeMicrophone, mayStartScribe } from '../frontend/src/voice/scribeCapture.ts'

assert.equal(mayStartScribe(false, false, false, false), true)
assert.equal(mayStartScribe(true, false, false, false), false) // double click
assert.equal(mayStartScribe(false, false, true, false), false) // one shot
assert.equal(mayStartScribe(false, true, false, false), false)

let called = 0
const denied = Object.assign(new Error('denied'), {name:'NotAllowedError'})
await assert.rejects(acquireScribeMicrophone(async () => { called++; throw denied },
  () => true), error => error === denied)
assert.equal(called, 1)

let resolve
const pending = new Promise(done => { resolve = done })
let stopped = 0, current = true
const wait = acquireScribeMicrophone(() => pending, () => current)
current = false // cancel or component unmount before permission resolves
resolve({getTracks:() => [{stop:() => { stopped++ }}]})
assert.equal(await wait, null)
assert.equal(stopped, 1)

let kept = 0
const active = await acquireScribeMicrophone(async () => {
  kept++
  return {getTracks:() => [{stop:() => { stopped++ }}]}
}, () => true)
assert.ok(active)
assert.equal(kept, 1)
assert.equal(stopped, 1)
console.log('scribe acquisition, denial, late grant, duplicate gate: pass')
