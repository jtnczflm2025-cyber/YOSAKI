// Simulate 48 kHz mono ScriptProcessor blocks using the fixed local WAV.
// This imports the exact converter used by ScribeDraftInput; no provider calls.
import fs from 'node:fs'
import { pcm16At16k } from '../frontend/src/voice/scribePcm.ts'

const [source, target] = process.argv.slice(2)
if (!source || !target) throw new Error('source and target required')
const wav = fs.readFileSync(source)
if (wav.toString('ascii', 0, 4) !== 'RIFF' || wav.toString('ascii', 8, 12) !== 'WAVE')
  throw new Error('fixture WAV invalid')
let fmt, offset = 12, data
while (offset + 8 <= wav.length) {
  const kind = wav.toString('ascii', offset, offset + 4)
  const size = wav.readUInt32LE(offset + 4)
  if (kind === 'fmt ') fmt = {
    encoding: wav.readUInt16LE(offset + 8), channels: wav.readUInt16LE(offset + 10),
    rate: wav.readUInt32LE(offset + 12), bits: wav.readUInt16LE(offset + 22),
  }
  if (kind === 'data') data = wav.subarray(offset + 8, offset + 8 + size)
  offset += 8 + size + (size % 2)
}
if (!fmt || !data || fmt.encoding !== 1 || fmt.channels !== 1 ||
    fmt.rate !== 16000 || fmt.bits !== 16 || data.length !== 185760)
  throw new Error('fixture format changed')
const capture = new Float32Array(data.length / 2 * 3)
for (let i = 0; i < data.length / 2; i++) {
  const sample = data.readInt16LE(i * 2) / 32768
  capture[i * 3] = sample; capture[i * 3 + 1] = sample; capture[i * 3 + 2] = sample
}
const frames = []
for (let offset = 0; offset < capture.length; offset += 8192)
  frames.push(Buffer.from(pcm16At16k(capture.subarray(offset, offset + 8192), 48000)))
fs.writeFileSync(target, Buffer.concat(frames))
fs.writeFileSync(`${target}.json`, JSON.stringify({source_rate:48000, channels:1,
  frames:frames.length, frame_bytes:frames.map(f => f.length), pcm_bytes:frames.reduce((n,f) => n+f.length,0),
  duration_seconds:frames.reduce((n,f) => n+f.length,0)/32000}))
