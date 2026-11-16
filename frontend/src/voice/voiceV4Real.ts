import { RealtimeOutputPlayer, type PlaybackDiagnostic, type RealtimeMetric } from './realtimeOutput'

const TEXT = 'これは音声の接続と再生を確かめるための短い文章です。落ち着いた声で、自然な間を保って読み上げてください。'
const RUN_ID = 'voice-v4-stream-20261111-r2'
const ticket = new URLSearchParams(location.hash.slice(1)).get('ticket')
const markers: Record<string, number> = {}
const diagnostics: PlaybackDiagnostic[] = []
const scope = document.getElementById('scope')!
const text = document.getElementById('text')!
const state = document.getElementById('state')!
const metrics = document.getElementById('metrics')!
const start = document.getElementById('start') as HTMLButtonElement

scope.textContent = `${RUN_ID} · eleven_v4_turbo · pcm_24000 · ja · ${Array.from(TEXT).length} 字符`
text.textContent = TEXT
if (!ticket) {
  start.disabled = true
  state.textContent = '缺少本次本地入口凭证'
}
const player = new RealtimeOutputPlayer(value => { state.textContent = value },
  () => new AudioContext(), (record:RealtimeMetric) => {
    if (!(record.name in markers)) markers[record.name] = record.client_ms
    const first = markers.first_playback_started
    const complete = markers.receive_completed
    const streaming = first === undefined || complete === undefined
      ? '待观察' : first < complete ? '软件播放先于接收完成' : '接收完成后才开始播放'
    metrics.textContent = JSON.stringify({markers, streaming, diagnostics}, null, 2)
  }, (diagnostic: PlaybackDiagnostic) => {
    diagnostics.push(diagnostic)
    metrics.textContent = JSON.stringify({markers, diagnostics}, null, 2)
  })

let used = false
start.addEventListener('click', () => {
  if (used) return
  used = true
  start.disabled = true
  void (async () => {
    try {
      await player.connect('ws://127.0.0.1:18769/ws/output')
      await player.start('frozen', ticket ?? '')
    } catch {
      state.textContent = '本地连接失败；未自动重试'
    }
  })()
})
document.getElementById('stop')!.addEventListener('click', () => player.stop())

Object.assign(window, {__v4Real: {player, markers}})
