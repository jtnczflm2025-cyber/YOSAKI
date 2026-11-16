import { RealtimeOutputPlayer, type RealtimePlaybackState } from './realtimeOutput'

const events: RealtimePlaybackState[] = []
const stateNode = document.getElementById('state')!
const eventNode = document.getElementById('events')!
const player = new RealtimeOutputPlayer(state => {
  events.push(state)
  stateNode.textContent = state
  eventNode.textContent = events.join(' → ')
})

document.getElementById('connect')!.addEventListener('click', () => {
  void player.connect('ws://127.0.0.1:18767/ws/output').catch(() => { stateNode.textContent = 'connection_failed' })
})
for (const sample of ['normal', 'keepalive', 'late', 'error', 'remote_close', 'disconnect']) {
  document.getElementById(sample)!.addEventListener('click', () => {
    void player.start(sample).catch(() => { stateNode.textContent = 'start_failed' })
  })
}
document.getElementById('stop')!.addEventListener('click', () => player.stop())
document.getElementById('inject_stale')!.addEventListener('click', () => {
  const socket = (player as unknown as {socket: WebSocket | null}).socket
  socket?.send(JSON.stringify({ type: 'inject_stale' }))
})

Object.assign(window, { __v4Harness: { player, events } })
