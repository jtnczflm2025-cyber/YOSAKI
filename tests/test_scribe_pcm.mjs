import assert from 'node:assert/strict'
import { pcm16At16k, pcmBase64 } from '../frontend/src/voice/scribePcm.ts'

const audio = new Float32Array(4800).fill(0.5)
const pcm = pcm16At16k(audio, 48000)
assert.equal(pcm.length, 3200)
assert.equal(pcm[0], 0)
assert.equal(pcm[1], 64)
assert.equal(pcmBase64(pcm).length, 4268)
assert.deepEqual([...pcm16At16k(new Float32Array([-2, 2]), 16000)],
  [0, 128, 255, 127])
assert.throws(() => pcm16At16k(audio, 8000))
console.log('scribe pcm conversion: pass')
