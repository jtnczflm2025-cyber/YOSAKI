import assert from 'node:assert/strict'
import { ScribeTurnDetector } from '../frontend/src/voice/scribeTurnDetector.ts'

const frame = (amplitude, length = 1600) => new Float32Array(length).fill(amplitude)
const rate = 16000
const normal = new ScribeTurnDetector()
for (let i = 0; i < 4; i++) assert.equal(normal.observe(frame(0.2), rate), null)
for (let i = 0; i < 15; i++) assert.equal(normal.observe(frame(0), rate), null)
assert.equal(normal.observe(frame(0), rate), 'commit')
assert.equal(normal.observe(frame(0), rate), null)
const silent = new ScribeTurnDetector()
for (let i = 0; i < 119; i++) assert.equal(silent.observe(frame(0), rate), null)
assert.equal(silent.observe(frame(0), rate), 'no_speech')
const long = new ScribeTurnDetector()
for (let i = 0; i < 119; i++) assert.equal(long.observe(frame(0.2), rate), null)
assert.equal(long.observe(frame(0.2), rate), 'commit')
console.log('continuous local turn detector: pass')
