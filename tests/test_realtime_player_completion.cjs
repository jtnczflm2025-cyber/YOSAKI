const assert = require('node:assert/strict')
const fs = require('node:fs')
const ts = require('../frontend/node_modules/typescript')

require.extensions['.ts'] = (module, filename) => {
  const source = fs.readFileSync(filename, 'utf8')
  module._compile(ts.transpileModule(source, {compilerOptions:{
    module:ts.ModuleKind.CommonJS, target:ts.ScriptTarget.ES2020,
  }}).outputText, filename)
}

const { RealtimeOutputPlayer } = require('../frontend/src/voice/realtimeOutput.ts')
const states = []
const player = new RealtimeOutputPlayer(value => states.push(value))
player.finishPlayback()
player.finishPlayback() // final source and the WS close handler race to drain
assert.deepEqual(states, ['completed'])
console.log('realtime playback completion is reported once: pass')
