import assert from 'node:assert/strict'
import { LocalBargeDetector } from '../frontend/src/voice/localBargeDetector.ts'

const frame = (amplitude) => new Float32Array(1600).fill(amplitude)
const detector = new LocalBargeDetector()
for (let i=0; i<8; i++) assert.equal(detector.observe(frame(0.02),16000),false)
for (let i=0; i<4; i++) assert.equal(detector.observe(frame(0.10),16000),false)
assert.equal(detector.observe(frame(0),16000),false) // interrupted speech does not accumulate
for (let i=0; i<4; i++) assert.equal(detector.observe(frame(0.10),16000),false)
assert.equal(detector.observe(frame(0.10),16000),true)
assert.equal(detector.observe(frame(0.10),16000),false) // one trigger per playback interval
assert.equal(new LocalBargeDetector().observe(frame(0.10),0),false)
console.log('local sound-triggered interrupt detector: pass')
