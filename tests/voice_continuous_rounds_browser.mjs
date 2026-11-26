// Run against the loopback-only fake server and Vite on port 5174.
// Requires Node 22+ and a separate Edge CDP session on port 19227.
import assert from 'node:assert/strict'

const expected = Number(process.argv[2])
const mode = process.argv[3] ?? 'ws'
assert.ok(mode === 'single-start' ? expected === 1 : expected === 2 || expected === 3)
assert.ok(['default', 'ws', 'revert', 'failure', 'budget', 'unavailable', 'headset', 'missed',
  'daily-default', 'daily-interrupt', 'daily-silent', 'daily-stop', 'buttons', 'buttons-http', 'disconnect', 'cycles', 'single-start'].includes(mode))
const wsSelected = ['ws', 'failure', 'budget', 'daily-interrupt', 'buttons', 'disconnect'].includes(mode)
const failed = ['failure', 'budget'].includes(mode)
const headset = mode === 'headset' || mode === 'missed'
const dailyBarge = mode.startsWith('daily-')
const pageUrl = 'http://127.0.0.1:5174/hub?window=chat'
const target = await fetch('http://127.0.0.1:19227/json/new?about:blank', {method:'PUT'}).then(r => r.json())
const socket = new WebSocket(target.webSocketDebuggerUrl)
await new Promise((resolve, reject) => {
  socket.onopen = resolve
  socket.onerror = reject
})
let serial = 0
const pending = new Map()
socket.onmessage = ({data}) => {
  const reply = JSON.parse(data)
  if (!pending.has(reply.id)) return
  pending.get(reply.id)(reply)
  pending.delete(reply.id)
}
function cdp(method, params = {}) {
  return new Promise(resolve => {
    const id = ++serial
    pending.set(id, resolve)
    socket.send(JSON.stringify({id, method, params}))
  })
}
async function evaluate(fn, arg) {
  const {result, error} = await cdp('Runtime.evaluate', {
    expression: '(' + fn.toString() + ')(' + JSON.stringify(arg) + ')',
    awaitPromise:true, returnByValue:true, userGesture:true,
  })
  assert.ifError(error)
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description)
  return result.result.value
}
const pause = ms => new Promise(resolve => setTimeout(resolve, ms))
async function until(fn, arg, seconds = 35) {
  for (let i = 0; i < seconds * 5; i++) {
    try { if (await evaluate(fn, arg)) return } catch (error) {
      if (!/context|navigat/i.test(error.message)) throw error
    }
    await pause(200)
  }
  const status = await evaluate(() => document.querySelector('.voice-call-controls')?.innerText)
  throw new Error('Timed out: ' + status)
}

try {
  // Clear the previous fixture's cookie when testing a different server instance.
  await cdp('Network.clearBrowserCookies')
  await cdp('Storage.clearDataForOrigin', {origin:'http://127.0.0.1:5174', storageTypes:'local_storage'})
  await cdp('Page.navigate', {url:pageUrl})
  await until(() => location.pathname === '/hub' && document.readyState === 'complete')
  const loggedIn = await evaluate(async () => {
    const response = await fetch('/api/auth/login', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({username:'synthetic_voice_owner',password:'SyntheticOnly-Voice-2026!'})})
    return response.status
  })
  assert.equal(loggedIn, 200)
  await cdp('Page.navigate', {url:'http://127.0.0.1:5174/hub?window=voice'})
  await until(() => [...document.querySelectorAll('label')]
    .some(el => el.textContent.includes('流式播放（WS）')))
  assert.equal(await evaluate(findPickerState), false)
  if (mode === 'unavailable' || mode === 'single-start') {
    await until(() => document.body.innerText.includes('当前服务未启用 WS 播放'))
    assert.equal(await evaluate(() => [...document.querySelectorAll('label')]
      .find(el => el.textContent.includes('流式播放（WS）')).querySelector('input').disabled), true)
    console.log(JSON.stringify({mode, capabilityUnavailable:true, wsDisabled:true}))
  } else {
    await until(() => [...document.querySelectorAll('label')]
      .some(el => el.textContent.includes('流式播放（WS）') && !el.querySelector('input').disabled))
  }
  if (wsSelected || mode === 'revert') {
    await evaluate(() => [...document.querySelectorAll('label')]
      .find(el => el.textContent.includes('流式播放（WS）')).querySelector('input').click())
    assert.equal(await evaluate(findPickerState), true)
    if (mode === 'revert') {
      await evaluate(() => [...document.querySelectorAll('label')]
        .find(el => el.textContent.includes('流式播放（WS）')).querySelector('input').click())
      assert.equal(await evaluate(findPickerState), false)
    }
  }
  await cdp('Page.navigate', {url:pageUrl})
  await until(() => document.querySelector('.voice-call-controls') &&
    [...document.querySelectorAll('.conversation-chip')]
      .some(el => el.textContent.includes('Bound new empty conversation')))
  await evaluate(() => {
    const conversation = [...document.querySelectorAll('.conversation-chip')]
      .find(el => el.textContent.includes('Bound new empty conversation'))
    if (!conversation) throw new Error('Fixture conversation missing')
    if (!conversation.classList.contains('active')) conversation.click()
  })
  await until(n => document.querySelector('.voice-call-controls').innerText.includes('当前最多可开始 ' + n + ' 轮'),
    mode === 'budget' ? 1 : expected)
  await evaluate(({n, wsSelected, mode, headset, dailyBarge}) => {
    const realFetch = window.fetch.bind(window)
    const realWebSocket = window.WebSocket
    const evidence = window.roundEvidence = {scribe:[], output:[], drains:[], reemitted:0,
      micRequests:0, ready:[], stoppedFirst:false, handedOffAfterStop:false,
      virtualTracks:[], previousListenerClosed:[], fixtureLoads:0}
    if (mode.startsWith('buttons') || mode === 'disconnect') {
      evidence.stops = []
      evidence.starts = 0
      const start = AudioBufferSourceNode.prototype.start
      AudioBufferSourceNode.prototype.start = function (...args) {
        evidence.starts++
        return start.apply(this,args)
      }
      if (mode === 'buttons-http') {
        const pause = HTMLMediaElement.prototype.pause
        HTMLMediaElement.prototype.pause = function (...args) {
          if (!this.paused) evidence.stops.push(performance.now())
          return pause.apply(this,args)
        }
        const play = HTMLMediaElement.prototype.play
        HTMLMediaElement.prototype.play = function (...args) {
          evidence.starts++
          evidence.audioElement = this
          return play.apply(this,args)
        }
      }
      const stop = AudioBufferSourceNode.prototype.stop
      AudioBufferSourceNode.prototype.stop = function (...args) {
        evidence.stops.push(performance.now())
        return stop.apply(this,args)
      }
    }
    if (headset || dailyBarge) {
      window.__yosakiBargeFakeSilent = mode === 'missed'
      let lastStatus = ''
      const controls = document.querySelector('.voice-call-controls')
      new MutationObserver(() => {
        const status = controls.querySelector(':scope > span[role=status]')?.textContent || ''
        if (status === lastStatus) return
        lastStatus = status
        if (status.includes('现在可以插话')) {
          const output = document.querySelector('[data-v4-bound-output]')
          evidence.ready.push({outputCount:evidence.output.length,
            audioReceived:!!output?.querySelector('[data-v4-first-audio]')?.textContent,
            playbackMeasured:output?.textContent.includes('软件耗时）：未测') === false})
        }
      }).observe(controls,{childList:true,characterData:true,subtree:true})
      const stop = AudioBufferSourceNode.prototype.stop
      AudioBufferSourceNode.prototype.stop = function (...args) {
        if (evidence.ready.length && evidence.output.length === 1 && evidence.scribe.length === 1)
          evidence.stoppedFirst = true
        return stop.apply(this,args)
      }
    }
    const media = navigator.mediaDevices
    const originalMic = media.getUserMedia.bind(media)
    media.getUserMedia = async (...args) => {
      evidence.micRequests++
      if (dailyBarge) {
        // Exercise the real playback listener against a browser MediaStream.
        // Initial and ordinary next-round Scribe use the separate WAV fixture.
        const context = new AudioContext()
        const destination = context.createMediaStreamDestination()
        const signal = context.createBufferSource()
        const buffer = context.createBuffer(1,context.sampleRate * 8,context.sampleRate)
        const samples = buffer.getChannelData(0)
        if (mode === 'daily-interrupt' && evidence.micRequests === 1) {
          for (let i = context.sampleRate / 2; i < context.sampleRate * 2.3; i++)
            samples[i] = 0.25 * Math.sin(i * 2 * Math.PI * 440 / context.sampleRate)
        }
        signal.buffer = buffer
        signal.connect(destination)
        const track = destination.stream.getAudioTracks()[0]
        const stop = track.stop.bind(track)
        track.stop = () => {
          stop()
          void context.close().catch(() => {})
        }
        evidence.virtualTracks.push(track)
        await context.resume()
        signal.start()
        return destination.stream
      }
      return originalMic(...args)
    }
    window.WebSocket = class extends realWebSocket {
      constructor(url, ...rest) {
        super(url, ...rest)
        if (new URL(url).pathname === '/api/voice/scribe-draft/ws') {
          if (dailyBarge && evidence.scribe.length)
            evidence.previousListenerClosed.push(evidence.virtualTracks.every(t => t.readyState === 'ended'))
          evidence.scribe.push(this)
          if (evidence.scribe.length === 2) evidence.handedOffAfterStop = evidence.stoppedFirst
        }
        if (new URL(url).pathname === '/api/voice/realtime/output') {
          evidence.output.push(this)
          this.addEventListener('message', event => {
            if (JSON.parse(event.data).type === 'audio') evidence.lateAudio = event.data
          })
        }
      }
    }
    window.fetch = async (url, options) => {
      if (url === '/api/voice/scribe-draft/fixture.wav') evidence.fixtureLoads++
      if (url === '/api/voice/calls' && options?.method === 'POST') {
        const response = await realFetch(url, options)
        evidence.callId = (await response.clone().json()).call_id
        return response
      }
      if (typeof url === 'string' && url.endsWith('/playback-drained')) {
        const entry = {url, body:options.body}
        evidence.drains.push(entry)
        if (wsSelected && evidence.drains.length === n) {
          const output = document.querySelector('[data-v4-bound-output]')
          const player = output?.__vueParentComponent
          if (typeof player?.emit !== 'function') throw new Error('Cannot repeat the player event')
          evidence.reemitted++
          player.emit('playback-state', 'ended')
        }
        const response = await realFetch(url, options)
        entry.result = await response.clone().json()
        return response
      }
      return realFetch(url, options)
    }
    const controls = document.querySelector('.voice-call-controls')
    if (dailyBarge) {
      const toggle = [...controls.querySelectorAll('label')].find(el => el.textContent.includes('耳机插话（仅本次通话）'))
      const input = toggle?.querySelector('input')
      if (!input || input.checked || !controls.innerText.includes('播放期间会开启麦克风'))
        throw new Error('Daily opt-in privacy notice or default missing')
      if (mode !== 'daily-default') input.click()
    }
    [...controls.querySelectorAll('label')].find(el => el.textContent.includes('最终转写会自动发送'))
      .querySelector('input').click()
  }, {n:expected, wsSelected, mode, headset, dailyBarge})
  await evaluate(() => {
    const controls = document.querySelector('.voice-call-controls')
    const start = [...controls.querySelectorAll('button')].find(el => el.textContent === '开始通话')
    if (!start || start.disabled) throw new Error('Call start unavailable')
    start.click()
  })
  if (mode === 'single-start') {
    const statusHas = text => document.querySelector('.voice-call-controls')?.innerText.includes(text)
    await until(statusHas, '正在听取；现在可以说话')
    await until(statusHas, '声音正在播放')
    await evaluate(() => [...document.querySelectorAll('.voice-call-controls button')]
      .find(el => el.textContent === '打断').click())
    await until(statusHas, '已停止播放并收麦')
    const result = await evaluate(async () => {
      const e = window.roundEvidence
      const counts = await fetch('/api/voice/test/round-counts/' + e.callId).then(r => r.json())
      const timeline = await fetch('/api/voice/calls/' + e.callId + '/timeline').then(r => r.json())
      return {counts, interrupt:timeline.events.find(event => event.event === 'button_interrupt_received'),
        closed:e.scribe.length === 1 && e.scribe[0].readyState === WebSocket.CLOSED}
    })
    assert.equal(result.counts.stored_rounds,1)
    assert.equal(result.counts.ended,true)
    assert.deepEqual([result.counts.scribe_opened,result.counts.scribe_closed,
      result.counts.model,result.counts.http,result.counts.ttd_opened,result.counts.saved],[1,1,1,1,0,1])
    assert.equal(result.closed,true)
    assert.equal(result.interrupt.phase,'playing')
    assert.equal(result.interrupt.client_playback_started,true)
    console.log(JSON.stringify({mode,captureReady:true,...result}))
  } else if (mode === 'cycles') {
    for (let i=0;i<4;i++) {
      await until(() => document.querySelector('.voice-call-controls')?.innerText.includes('现在可以说话'))
      await evaluate(() => [...document.querySelectorAll('.voice-call-controls button')]
        .find(el => el.textContent === '结束通话').click())
      await until(async () => {
        const e = window.roundEvidence
        const counts = await fetch('/api/voice/test/round-counts/' + e.callId).then(r => r.json())
        return counts.ended && e.scribe.length && e.scribe.every(ws => ws.readyState === 3)
      })
      if (i < 3) {
        await evaluate(() => [...document.querySelectorAll('.voice-call-controls label')]
          .find(el => el.textContent.includes('最终转写会自动发送')).querySelector('input').click())
        await until(() => [...document.querySelectorAll('.voice-call-controls button')]
          .some(el => el.textContent === '开始通话' && !el.disabled))
        await evaluate(() => [...document.querySelectorAll('.voice-call-controls button')]
          .find(el => el.textContent === '开始通话').click())
      }
    }
    const counts = await evaluate(() => fetch('/api/voice/test/round-counts/' + window.roundEvidence.callId)
      .then(r => r.json()))
    assert.deepEqual([counts.scribe_opened,counts.scribe_closed,counts.model,counts.http,
      counts.ttd_opened,counts.active_output_leases],[4,4,0,0,0,0])
    console.log(JSON.stringify({mode,counts}))
  } else if (mode.startsWith('buttons') || mode === 'disconnect') {
    const statusHas = text => document.querySelector('.voice-call-controls')?.innerText.includes(text)
    const clickInterrupt = () => {
      window.roundEvidence.lastClick = performance.now()
      ;[...document.querySelectorAll('.voice-call-controls button')]
        .find(el => el.textContent === '打断').click()
    }
    if (mode.startsWith('buttons')) {
      await until(async () => {
        const id = window.roundEvidence.callId
        if (!id) return false
        const actual = await fetch('/api/voice/test/round-counts/' + id).then(r => r.json())
        return actual.model === 1
      })
      await until(statusHas, '模型正在生成')
      await evaluate(clickInterrupt)
      await until(statusHas, '现在可以说话')
    }
    await until(statusHas, '声音正在播放')
    const softwareLatency = await evaluate(() =>
      document.querySelector('[data-call-software-latency]')?.textContent)
    assert.match(softwareLatency, /\d+ 毫秒/)
    if (mode.startsWith('buttons')) await evaluate(clickInterrupt)
    else await evaluate(() => {
      const socket = window.roundEvidence.output[0]
      if (socket.readyState !== WebSocket.OPEN) throw new Error('Relay already closed')
      socket.close()
    })
    await until(async () => {
      const e = window.roundEvidence
      const counts = await fetch('/api/voice/test/round-counts/' + e.callId).then(r => r.json())
      return counts.ended && counts.active_output_leases === 0 &&
        [...e.scribe,...e.output].every(ws => ws.readyState === 3)
    })
    const beforeLateAudio = await evaluate(() => {
      const e = window.roundEvidence
      if (e.audioElement) e.audioElement.dispatchEvent(new Event('ended'))
      else {
        if (!e.lateAudio) throw new Error('Missing previously observed audio event')
        for (const ws of e.output)
          ws.dispatchEvent(new MessageEvent('message',{data:e.lateAudio}))
      }
      return e.starts
    })
    await pause(350)
    assert.equal(await evaluate(() => window.roundEvidence.starts),beforeLateAudio)
    const result = await evaluate(async () => {
      const e = window.roundEvidence
      const counts = await fetch('/api/voice/test/round-counts/' + e.callId).then(r => r.json())
      const timeline = await fetch('/api/voice/calls/' + e.callId + '/timeline').then(r => r.json())
      return {counts, events:timeline.events.map(event => event.event),
        phases:timeline.events.filter(e => e.event === 'button_interrupt_received').map(e => e.phase),
        stopMs:e.stops.find(ms => ms >= e.lastClick)-e.lastClick,
        stops:e.stops.length, scribe:e.scribe.length,
        visible:document.querySelector('.chat-scroll').innerText.includes(counts.model===2 ? '静か' : '今夜')}
    })
    assert.equal(result.counts.saved,1)
    assert.equal(result.visible,true)
    assert.equal(result.counts.ttd_opened,mode === 'buttons-http' ? 0 : 1)
    assert.equal(result.counts.ttd_closed,result.counts.ttd_opened)
    if (mode === 'buttons-http') {
      assert.equal(result.counts.http,1)
      assert.equal(result.counts.http_tags,1)
    }
    assert.equal(result.scribe,mode.startsWith('buttons') ? 2 : 1)
    assert.ok(result.stops > 0)
    if (mode.startsWith('buttons')) {
      assert.deepEqual(result.phases,['waiting','playing'])
      assert.ok(result.events.includes('model_stream_cancel_observed'))
      assert.ok(result.events.indexOf('model_stream_cancel_observed') <
        result.events.lastIndexOf('stt_attempt_reserved'))
      assert.ok(result.stopMs >= 0 && result.stopMs < 1000)
      assert.equal(result.visible,true)
    }
    console.log(JSON.stringify({mode,softwareLatency,...result}))
    process.exitCode = 0
  } else {
  if (mode === 'daily-stop') {
    await until(() => window.roundEvidence.ready.length === 1)
    await evaluate(() => {
      const stop = [...document.querySelectorAll('.voice-call-controls button')]
        .find(el => el.textContent === '结束通话')
      if (!stop) throw new Error('Call stop unavailable')
      stop.click()
    })
  }
  await until(text => document.querySelector('.voice-call-controls')?.innerText.includes(text),
    mode === 'daily-stop' ? '通话已结束' : mode === 'missed' ? '本轮未测到插话' :
      failed ? (mode === 'budget' ? '今日朗读剩余额度不足' : 'WS 合成结果未知') : '音频已播完')
  if (dailyBarge) {
    await until(async () => {
      const e = window.roundEvidence
      const counts = await fetch('/api/voice/test/round-counts/' + e.callId).then(r => r.json())
      return counts.ended && counts.active_output_leases === 0 &&
        [...e.scribe,...e.output].every(ws => ws.readyState === 3)
    })
    const result = await evaluate(async () => {
      const e = window.roundEvidence
      const [counts, observation] = await Promise.all([
        fetch('/api/voice/test/round-counts/' + e.callId).then(r => r.json()),
        fetch('/api/voice/test/headset-observations').then(r => r.json())
      ])
      const toggle = [...document.querySelectorAll('.voice-call-controls label')]
        .find(el => el.textContent.includes('耳机插话（仅本次通话）')).querySelector('input')
      return {counts, observation, micRequests:e.micRequests, ready:e.ready, fixtureLoads:e.fixtureLoads,
        tracksEnded:e.virtualTracks.every(t => t.readyState === 'ended'),
        previousListenerClosed:e.previousListenerClosed, handedOffAfterStop:e.handedOffAfterStop,
        canContinue:e.drains.map(d => d.result?.can_continue), toggledOff:!toggle.checked}
    })
    const interrupt = mode === 'daily-interrupt'
    const manualStop = mode === 'daily-stop'
    const n = manualStop ? 1 : 3
    assert.equal(result.micRequests,mode === 'daily-default' ? 0 : n)
    assert.equal(result.ready.length,result.micRequests)
    assert.equal(result.fixtureLoads, n - Number(interrupt))
    assert.equal(result.handedOffAfterStop,interrupt)
    assert.equal(result.tracksEnded,true)
    assert.deepEqual(result.previousListenerClosed,manualStop ? [] : [!interrupt,true])
    assert.equal(result.toggledOff,true)
    assert.deepEqual(result.canContinue,manualStop ? [] : interrupt ? [true,false] : [true,true,false])
    assert.deepEqual(result.observation.limits,[3,360,3,450])
    assert.equal(result.observation.commit_bytes.length,n)
    assert.equal(result.observation.utterances.filter(t => t==='少しゆっくり話してください。').length,Number(!manualStop))
    for (const key of ['scribe_opened','scribe_closed','model','saved'])
      assert.equal(result.counts[key],n)
    assert.equal(result.counts.http,interrupt ? 0 : n)
    assert.equal(result.counts.ttd_opened,interrupt ? 3 : 0)
    assert.equal(result.counts.ttd_closed,interrupt ? 3 : 0)
    console.log(JSON.stringify({mode,...result}))
  } else if (headset) {
    await until(async () => {
      const e = window.roundEvidence
      const counts = await fetch('/api/voice/test/round-counts/' + e.callId).then(r => r.json())
      return counts.ended && counts.active_output_leases === 0 &&
        [...e.scribe,...e.output].every(ws => ws.readyState === 3)
    })
    const evidence = await evaluate(async () => {
      const e = window.roundEvidence
      const [counts, recognition] = await Promise.all([
        fetch('/api/voice/test/round-counts/' + e.callId).then(r => r.json()),
        fetch('/api/voice/test/headset-observations').then(r => r.json()),
      ])
      const replay = await fetch('/api/voice/calls', {method:'POST',
        headers:{'Content-Type':'application/json'}, body:JSON.stringify({
          character_id:1, conversation_id:6, model_profile_id:7, acknowledge_auto_send:true})})
      const manualChat = await fetch('/api/chat/stream', {method:'POST',
        headers:{'Content-Type':'application/json'}, body:JSON.stringify({
          character_id:1, conversation_id:6, model_profile_id:7, message:'fixture outside the call'})})
      return {counts, recognition, ready:e.ready, stoppedFirst:e.stoppedFirst,
        handedOffAfterStop:e.handedOffAfterStop, micRequests:e.micRequests,
        replayStatus:replay.status, manualChatStatus:manualChat.status,
        canContinue:e.drains.map(d => d.result?.can_continue),
        captureRemoved:!document.querySelector('.voice-call-controls .scribe-draft-isolated')}
    })
    const n = mode === 'headset' ? 2 : 1
    assert.deepEqual(evidence.ready,[{outputCount:1,audioReceived:true,playbackMeasured:true}])
    assert.equal(evidence.handedOffAfterStop, n === 2)
    assert.equal(evidence.micRequests,0)
    assert.equal(evidence.captureRemoved,true)
    assert.deepEqual(evidence.canContinue,n === 2 ? [false] : [true])
    assert.equal(evidence.counts.stored_rounds,2)
    assert.equal(evidence.replayStatus,409)
    assert.equal(evidence.manualChatStatus,409)
    assert.deepEqual(evidence.recognition.limits,[2,180,2,160])
    assert.deepEqual(evidence.recognition.token_caps,Array(n).fill(256))
    assert.deepEqual(evidence.recognition.length_instructions,Array(n).fill(true))
    for (const key of ['scribe_opened','scribe_closed','ttd_opened','ttd_closed','model','saved'])
      assert.equal(evidence.counts[key],n)
    assert.equal(evidence.counts.http,0)
    assert.equal(evidence.recognition.commit_bytes.length,n)
    assert.ok(evidence.recognition.commit_bytes.every(bytes => bytes > 32000 && bytes <= 480000))
    assert.deepEqual(evidence.recognition.utterances, ['夜の散歩について、日本語で百字ほど話してください。',
      ...(n === 2 ? ['少しゆっくり話してください。'] : [])])
    console.log(JSON.stringify({mode, ...evidence}))
  } else if (failed) {
    await until(async () => {
      const e = window.roundEvidence
      const result = await fetch('/api/voice/test/round-counts/' + e.callId).then(r => r.json())
      return result.ended && result.active_output_leases === 0 && e.output.every(ws => ws.readyState === 3)
    })
    const result = await evaluate(async () => {
      const e = window.roundEvidence
      return {status:document.querySelector('.voice-call-controls').innerText,
        scribeSockets:e.scribe.length, outputSockets:e.output.length, micRequests:e.micRequests,
        counters:await fetch('/api/voice/test/round-counts/' + e.callId).then(r => r.json())}
    })
    const attempted = mode === 'failure' ? 1 : 0
    assert.match(result.status, /切回 HTTP/)
    assert.match(result.status, /系统不会自动再次合成此回复/)
    assert.deepEqual([result.scribeSockets, result.outputSockets, result.micRequests], [1, attempted, 0])
    assert.deepEqual(result.counters, {stored_rounds:expected, ended:true,
      scribe_opened:1, scribe_closed:1, ttd_opened:attempted, ttd_closed:attempted,
      http:0, model:1, saved:1, usage:attempted ? [1,1,14] : [0,0,0], active_output_leases:0})
    console.log(JSON.stringify({mode, ...result}))
  } else {
  const result = await evaluate(async () => {
    const {scribe, drains, reemitted} = window.roundEvidence
    const last = drains.at(-1)
    const automaticDrains = drains.map(item => item.result?.can_continue)
    const callUrl = last.url.replace('/playback-drained','')
    const callId = callUrl.split('/').at(-1)
    const duplicate = await fetch(last.url, {method:'POST',
      headers:{'Content-Type':'application/json'}, body:last.body})
    const replay = new WebSocket('ws://' + location.host + '/api/voice/scribe-draft/ws')
    const rejection = await new Promise((resolve, reject) => {
      replay.onopen = () => replay.send(JSON.stringify({type:'start', character_id:1,
        language_code:'auto', attempt_id:crypto.randomUUID().replaceAll('-',''),call_id:callId}))
      replay.onmessage = ({data}) => { replay.close(); resolve(JSON.parse(data)) }
      replay.onerror = reject
    })
    const timeline = await fetch(callUrl + '/timeline').then(r => r.json())
    const settings = await fetch('/api/voice/calls/settings').then(r => r.json())
    const counters = await fetch('/api/voice/test/round-counts/' + callId).then(r => r.json())
    return {reEmitted:reemitted, drained:automaticDrains, dailyRounds:settings.limits.rounds,
      micRequests:window.roundEvidence.micRequests,
      autoScribeSockets:scribe.length - 1, allScribeClosed:scribe.every(ws => ws.readyState >= 2),
      microphoneUnmounted:!document.querySelector('.voice-call-controls .scribe-draft-isolated'),
      duplicateStatus:duplicate.status, duplicateResult:await duplicate.json(), rejection,
      ended:timeline.ended, counters, events:timeline.events.map(e => e.event)}
  })
  assert.equal(result.reEmitted, Number(wsSelected))
  assert.equal(result.micRequests, 0)
  assert.equal(result.dailyRounds, 3)
  assert.deepEqual(result.drained, [...Array(expected - 1).fill(true), false])
  assert.equal(result.autoScribeSockets, expected)
  assert.equal(result.allScribeClosed, true)
  assert.equal(result.microphoneUnmounted, true)
  assert.deepEqual([result.duplicateStatus, result.duplicateResult.can_continue], [200, false])
  assert.match(JSON.stringify(result.rejection), /voice_call_invalid/)
  assert.equal(result.ended, true)
  const {usage, ...counts} = result.counters
  assert.deepEqual(usage.slice(0,2), [expected, expected])
  assert.deepEqual(counts, {stored_rounds:expected, ended:true,
    scribe_opened:expected, scribe_closed:expected,
    ttd_opened:wsSelected ? expected : 0, ttd_closed:wsSelected ? expected : 0,
    http:wsSelected ? 0 : expected, model:expected, saved:expected, active_output_leases:0})
  for (const event of ['stt_attempt_reserved','model_completed_saved','tts_dispatch_claimed','playback_drained'])
    assert.equal(result.events.filter(item => item === event).length, expected)
  assert.equal(result.events.filter(item => item === 'call_ended').length, 1)
  console.log(JSON.stringify({expected, mode, ...result}))
  }
  }
} finally {
  await fetch('http://127.0.0.1:19227/json/close/' + target.id)
  socket.close()
}

function findPickerState() {
  return [...document.querySelectorAll('label')]
    .find(el => el.textContent.includes('流式播放（WS）')).querySelector('input').checked
}
