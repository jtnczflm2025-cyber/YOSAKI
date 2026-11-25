/** V4 PCM output player. Product mounting remains explicitly gated. */
export type RealtimePlaybackState = 'idle' | 'connecting' | 'waiting' | 'playing' |
  'completed' | 'stopped' | 'failed' | 'unknown'
export type RealtimeMetric = {name:string; client_ms:number; request_id:string}
export type PlaybackDiagnostic = {
  code: string; request_id: string; session_id: string; generation_id: string
  segment_id: string; connection_id: string; connection_epoch: number
  pending_audio_bytes: number; incoming_audio_bytes: number
  scheduled_ahead_seconds: number; scheduled_sources: number
}
export type RelayObservation = {
  type: 'text_dispatch_attempt' | 'audio' | 'completed' | 'failed' |
    'final_turn' | 'final_protocol_event' | 'transport_closed' | 'stopped' | 'input_rejected'
  text_dispatch_to_first_audio_ms?: number
  error_category?: string
  close_origin?: string
  remote_close_code?: number
  request_outcome?: string
  reason?: string
}
export type DynamicChatEvent = {type:string; text?:string; reason?:string;
  message_id?:number; generation_status?:string; model_status?:string;
  display_status?:string; segments?:number;
  message_version_sha256?:string; public_text_sha256?:string;
  connections?:number; characters?:number; audio_bytes?:number;
  tts_segments?:number; tts_connections?:number;
  inter_segment_receive_gap_ms?:number}

type WireEvent = Record<string, unknown> & { type: string }
type Identity = {
  sessionId: string
  generationId: string
  segmentId: string
  connectionId: string
  connectionEpoch: number
}

const FIRST_SAMPLE_WORKLET = `
class YosakiFirstSample extends AudioWorkletProcessor {
  constructor() {
    super(); this.token = -1; this.reported = false;
    this.port.onmessage = event => {
      if (event.data && event.data.type === 'reset') {
        this.token = event.data.token; this.reported = false;
      }
    };
  }
  process(inputs, outputs) {
    const input = inputs[0] && inputs[0][0];
    const output = outputs[0] && outputs[0][0];
    if (input && output) {
      output.set(input);
      if (!this.reported && input.some(value => value !== 0)) {
        this.reported = true;
        this.port.postMessage({type:'first_sample', token:this.token});
      }
    }
    return true;
  }
}
registerProcessor('yosaki-v4-first-sample', YosakiFirstSample);
`
const MAX_AUDIO_BYTES = 8 * 1024 * 1024
const MAX_FRAME_BASE64 = 1024 * 1024
const SCHEDULE_WINDOW_SECONDS = 8
const SCHEDULE_CHUNK_BYTES = 48000

export class RealtimeOutputPlayer {
  private socket: WebSocket | null = null
  private context: AudioContext | null = null
  private meter: AudioWorkletNode | null = null
  private sources = new Set<AudioBufferSourceNode>()
  private pendingPcm: Uint8Array[] = []
  private pendingBytes = 0
  private acceptedAudioBytes = 0
  private drainTimer: ReturnType<typeof setInterval> | null = null
  private requestId: string | null = null
  private identity: Identity | null = null
  private localEpoch = 0
  private nextStart = 0
  private providerCompleted = false
  private multiSegment = false
  private turnId: string | null = null
  private segmentSequence = 0
  private voiceMuted = false
  private lastSequence = 0
  private firstAudioReceived = false
  private firstPlaybackStarted = false
  private playbackEnded = false
  private lastIncomingBytes = 0
  private state: RealtimePlaybackState = 'idle'

  constructor(private readonly onState: (state: RealtimePlaybackState) => void,
              private readonly contextFactory: () => AudioContext = () => new AudioContext(),
              private readonly onMetric?: (metric: RealtimeMetric) => void,
              private readonly onDiagnostic?: (diagnostic: PlaybackDiagnostic) => void,
              private readonly onRelayEvent?: (observation: RelayObservation) => void,
              private readonly onDynamicEvent?: (event: DynamicChatEvent) => void) {}

  getState() { return this.state }
  private setState(value: RealtimePlaybackState) {
    this.state = value
    this.onState(value)
  }
  private metric(name: string): void {
    if (!this.requestId) return
    const record = {name, client_ms:performance.now(), request_id:this.requestId}
    try { this.onMetric?.(record) } catch { /* UI telemetry cannot stop audio. */ }
    if (this.socket?.readyState === WebSocket.OPEN)
      this.socket.send(JSON.stringify({type:'metric', ...record}))
  }

  private failPlayback(rawCode: string): void {
    const code = ['playback_queue_full', 'audio_hard_limit', 'audio_frame_limit',
      'invalid_pcm', 'invalid_audio_event'].includes(rawCode) ? rawCode : 'unknown_playback_error'
    if (this.requestId && this.identity) {
      const diagnostic: PlaybackDiagnostic = {
        code, request_id: this.requestId, session_id: this.identity.sessionId,
        generation_id: this.identity.generationId, segment_id: this.identity.segmentId,
        connection_id: this.identity.connectionId,
        connection_epoch: this.identity.connectionEpoch,
        pending_audio_bytes: this.pendingBytes, incoming_audio_bytes: this.lastIncomingBytes,
        scheduled_ahead_seconds: Math.max(0, this.nextStart - (this.context?.currentTime ?? 0)),
        scheduled_sources: this.sources.size,
      }
      try { this.onDiagnostic?.(diagnostic) } catch { /* Diagnostics cannot stop cleanup. */ }
      if (this.socket?.readyState === WebSocket.OPEN)
        this.socket.send(JSON.stringify({type:'playback_error', ...diagnostic}))
    }
    this.stop()
    this.setState('failed')
  }

  async connect(url: string): Promise<void> {
    if (this.socket) throw new Error('already_connected')
    this.setState('connecting')
    const socket = new WebSocket(url)
    this.socket = socket
    socket.onmessage = event => this.accept(event.data)
    socket.onclose = () => {
      if (this.socket !== socket) return
      this.socket = null
      if (this.providerCompleted) {
        this.identity = null
        if (!this.sources.size && !this.pendingPcm.length) this.finishPlayback()
        return
      }
      const wasActive = this.requestId !== null
      this.clearAudio()
      this.requestId = null
      this.identity = null
      if (this.state !== 'failed' && this.state !== 'stopped')
        this.setState(wasActive ? 'unknown' : 'stopped')
    }
    socket.onerror = () => {
      if (this.socket === socket && this.state === 'connecting') this.setState('failed')
    }
    await new Promise<void>((resolve, reject) => {
      socket.addEventListener('open', () => resolve(), { once: true })
      socket.addEventListener('close', () => reject(new Error('connection_closed')), { once: true })
    })
    this.setState('idle')
  }

  async start(sampleId: string, browserTicket?: string): Promise<void> {
    await this.startPayload({sample_id: sampleId, browser_ticket: browserTicket})
  }

  async startSavedMessage(messageId: number, conversationId: number,
                          characterId: number, callId?:string,
                          turnId?:string): Promise<void> {
    if (![messageId, conversationId, characterId].every(value =>
      Number.isSafeInteger(value) && value > 0)) throw new Error('invalid_message_binding')
    if ((callId || turnId) && (!callId || !turnId ||
        !/^[0-9a-f]{32}$/.test(callId) || !/^[0-9a-f]{32}$/.test(turnId)))
      throw new Error('invalid_call_binding')
    await this.startPayload({message_id: messageId, conversation_id: conversationId,
      character_id: characterId, ...(callId ? {call_id:callId,turn_id:turnId} : {})})
  }

  async startChatTurn(runId: string, conversationId: number,
                      characterId: number): Promise<void> {
    if (!runId || ![conversationId, characterId].every(value =>
      Number.isSafeInteger(value) && value > 0)) throw new Error('invalid_chat_binding')
    await this.startPayload({run_id:runId, conversation_id:conversationId,
      character_id:characterId}, 'start_chat', true)
  }

  private async startPayload(payload: Record<string, unknown>,
                             startType = 'start', multiSegment = false): Promise<void> {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) throw new Error('not_connected')
    if (!this.context) this.context = this.contextFactory()
    await this.context.resume()
    if (this.onMetric && !this.meter && this.context.audioWorklet) {
      const url = URL.createObjectURL(new Blob([FIRST_SAMPLE_WORKLET], {type:'text/javascript'}))
      try {
        await this.context.audioWorklet.addModule(url)
        this.meter = new AudioWorkletNode(this.context, 'yosaki-v4-first-sample')
        this.meter.port.onmessage = event => {
          if (event.data?.type === 'first_sample' &&
              event.data.token === this.localEpoch && !this.firstPlaybackStarted &&
              this.requestId) {
            this.firstPlaybackStarted = true
            this.metric('first_playback_started')
          }
        }
        this.meter.connect(this.context.destination)
      } catch { /* Missing worklet leaves playback timing unverified. */ }
      finally { URL.revokeObjectURL(url) }
    }
    this.clearAudio()
    this.localEpoch++
    this.meter?.port.postMessage({type:'reset', token:this.localEpoch})
    this.requestId = crypto.randomUUID()
    this.identity = null
    this.lastSequence = 0
    this.firstAudioReceived = false
    this.firstPlaybackStarted = false
    this.playbackEnded = false
    this.lastIncomingBytes = 0
    this.providerCompleted = false
    this.multiSegment = multiSegment
    this.turnId = null
    this.segmentSequence = 0
    this.voiceMuted = false
    this.nextStart = this.context.currentTime
    this.setState('waiting')
    this.socket.send(JSON.stringify({ type: startType, ...payload, request_id: this.requestId }))
  }

  stop(): void {
    this.stopWith(this.multiSegment ? 'stop_voice' : 'stop')
  }

  stopGeneration(): void {
    this.stopWith('stop_generation')
  }

  stopVoiceOnly(): void {
    this.silenceDynamic('stop_voice')
  }

  stopChatGeneration(): void {
    this.silenceDynamic('stop_generation')
  }

  private silenceDynamic(controlType?:string): void {
    if (!this.multiSegment || !this.requestId) return
    this.localEpoch++
    this.clearAudio()
    this.identity = null
    this.voiceMuted = true
    if (controlType && this.socket?.readyState === WebSocket.OPEN)
      this.socket.send(JSON.stringify({type:controlType, request_id:this.requestId}))
    this.setState('stopped')
  }

  private stopWith(controlType: string): void {
    const oldRequest = this.requestId
    if (oldRequest) this.metric('stopped')
    this.localEpoch++
    this.clearAudio()
    this.requestId = null
    this.identity = null
    this.providerCompleted = false
    if (oldRequest && this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ type: controlType, request_id: oldRequest }))
    }
    this.setState('stopped')
  }

  async close(): Promise<void> {
    this.stop()
    const socket = this.socket
    this.socket = null
    socket?.close(1000)
    this.meter?.disconnect()
    this.meter = null
    if (this.context) await this.context.close()
    this.context = null
  }

  private clearAudio(): void {
    if (this.drainTimer !== null) clearInterval(this.drainTimer)
    this.drainTimer = null
    this.pendingPcm = []
    this.pendingBytes = 0
    this.acceptedAudioBytes = 0
    for (const source of this.sources) {
      source.onended = null
      try { source.stop() } catch { /* An already-ended source is harmless. */ }
      source.disconnect()
    }
    this.sources.clear()
    this.nextStart = this.context?.currentTime ?? 0
  }

  private accept(raw: unknown): void {
    if (typeof raw !== 'string' || raw.length > 2 * 1024 * 1024) return
    let event: WireEvent
    try { event = JSON.parse(raw) as WireEvent } catch { return }
    if (!event || typeof event !== 'object' || typeof event.type !== 'string') return
    if (event.request_id !== this.requestId || !this.requestId) return
    if (this.multiSegment && ['chat_message_start','chat_text_delta',
        'chat_message_complete','chat_failed','turn_summary'].includes(event.type)) {
      try { this.onDynamicEvent?.(event as DynamicChatEvent) } catch { /* UI only. */ }
      return
    }
    if (this.multiSegment && event.type === 'voice_turn_completed') {
      if (this.voiceMuted) return
      if (event.turn_id !== this.turnId || !this.turnId) return
      this.providerCompleted = true
      this.metric('receive_completed')
      if (!this.sources.size && !this.pendingPcm.length) this.finishPlayback()
      return
    }
    if (this.multiSegment && event.type === 'voice_turn_stopped') {
      if (event.turn_id !== this.turnId && this.turnId) return
      this.silenceDynamic()
      return
    }
    if (this.multiSegment && this.voiceMuted) return
    if (event.type === 'input_rejected' && !this.identity) {
      const reason = typeof event.reason === 'string' ? event.reason : 'unknown'
      try { this.onRelayEvent?.({type:'input_rejected', reason}) } catch { /* UI only. */ }
      this.stop()
      this.setState('failed')
      return
    }
    if (event.type === 'started') {
      if (typeof event.session_id !== 'string' || typeof event.generation_id !== 'string' ||
          typeof event.segment_id !== 'string') return
      if (this.multiSegment) {
        if (typeof event.turn_id !== 'string' ||
            typeof event.segment_sequence !== 'number' ||
            event.segment_sequence !== this.segmentSequence + 1 ||
            (this.turnId !== null && event.turn_id !== this.turnId)) {
          this.failPlayback('invalid_audio_event'); return
        }
        this.turnId = event.turn_id
        this.segmentSequence = event.segment_sequence
        this.lastSequence = 0
      }
      this.identity = { sessionId: event.session_id, generationId: event.generation_id,
        segmentId: event.segment_id, connectionId: '', connectionEpoch: -1 }
      return
    }
    const identity = this.identity
    if (!identity || event.session_id !== identity.sessionId ||
        event.generation_id !== identity.generationId ||
        event.segment_id !== identity.segmentId) return
    if (event.type === 'connection_ready') {
      if (typeof event.connection_id !== 'string' ||
          typeof event.connection_epoch !== 'number' ||
          event.connection_epoch <= identity.connectionEpoch) return
      identity.connectionId = event.connection_id
      identity.connectionEpoch = event.connection_epoch
      this.lastSequence = 0
      return
    }
    if (event.connection_id !== identity.connectionId ||
        event.connection_epoch !== identity.connectionEpoch) return
    if (['text_dispatch_attempt', 'audio', 'completed', 'failed', 'final_turn',
         'final_protocol_event', 'transport_closed', 'stopped'].includes(event.type)) {
      const observation: RelayObservation = {type: event.type as RelayObservation['type']}
      if (event.type === 'audio' && event.sequence === 1 &&
          typeof event.text_dispatch_to_first_audio_ms === 'number' &&
          Number.isFinite(event.text_dispatch_to_first_audio_ms) &&
          event.text_dispatch_to_first_audio_ms >= 0)
        observation.text_dispatch_to_first_audio_ms = event.text_dispatch_to_first_audio_ms
      if (event.type === 'failed' && typeof event.error_category === 'string')
        observation.error_category = event.error_category
      if (['transport_closed','failed'].includes(event.type) && typeof event.close_origin === 'string')
        observation.close_origin = event.close_origin
      if (['transport_closed','failed'].includes(event.type) &&
          typeof event.remote_close_code === 'number' && Number.isInteger(event.remote_close_code))
        observation.remote_close_code = event.remote_close_code
      if (event.type === 'failed' && typeof event.request_outcome === 'string')
        observation.request_outcome = event.request_outcome
      try { this.onRelayEvent?.(observation) } catch { /* UI observer cannot stop audio. */ }
    }
    if (event.type === 'audio') {
      if (this.providerCompleted || event.sample_rate !== 24000 ||
          typeof event.pcm_base64 !== 'string' ||
          typeof event.sequence !== 'number' || event.sequence !== this.lastSequence + 1) {
        this.failPlayback('invalid_audio_event'); return
      }
      this.lastSequence = event.sequence
      if (!this.firstAudioReceived) {
        this.firstAudioReceived = true
        this.metric('first_audio_received')
      }
      try { this.enqueuePcm(event.pcm_base64) }
      catch (error) { this.failPlayback(error instanceof Error ? error.message : 'unknown_playback_error') }
    } else if (event.type === 'completed') {
      if (!this.multiSegment) {
        this.providerCompleted = true
        this.metric('receive_completed')
        if (!this.sources.size && !this.pendingPcm.length) this.finishPlayback()
      }
    } else if (event.type === 'failed') {
      if (this.multiSegment) this.silenceDynamic()
      else this.stop()
      this.setState('failed')
    }
  }

  private finishPlayback(): void {
    // Both the final source and the WS close handler can observe an empty
    // queue. Only one completion may advance a bound daily call cursor.
    if (this.playbackEnded) return
    if (this.drainTimer !== null) clearInterval(this.drainTimer)
    this.drainTimer = null
    this.playbackEnded = true
    this.metric('playback_ended')
    this.setState('completed')
  }

  private enqueuePcm(encoded: string): void {
    if (!this.context || encoded.length > MAX_FRAME_BASE64) throw new Error('audio_frame_limit')
    const bytes = Uint8Array.from(atob(encoded), char => char.charCodeAt(0))
    this.lastIncomingBytes = bytes.length
    if (!bytes.length || bytes.length % 2) throw new Error('invalid_pcm')
    if (this.acceptedAudioBytes + bytes.length > MAX_AUDIO_BYTES)
      throw new Error('audio_hard_limit')
    this.acceptedAudioBytes += bytes.length
    for (let offset = 0; offset < bytes.length; offset += SCHEDULE_CHUNK_BYTES) {
      const chunk = bytes.subarray(offset, Math.min(offset + SCHEDULE_CHUNK_BYTES, bytes.length))
      this.pendingPcm.push(chunk)
      this.pendingBytes += chunk.length
    }
    if (this.drainTimer === null)
      this.drainTimer = setInterval(() => this.drainSafely(), 100)
    this.drainPending()
  }

  private drainSafely(): void {
    try { this.drainPending() }
    catch (error) { this.failPlayback(error instanceof Error ? error.message : 'unknown_playback_error') }
  }

  private drainPending(): void {
    const context = this.context
    if (!context || !this.requestId || this.state === 'failed' || this.state === 'stopped') return
    while (this.pendingPcm.length) {
      const pcm = this.pendingPcm[0]
      const ahead = Math.max(0, this.nextStart - context.currentTime)
      const duration = pcm.length / 48000
      if (ahead + duration > SCHEDULE_WINDOW_SECONDS) break
      const buffer = context.createBuffer(1, pcm.length / 2, 24000)
      const channel = buffer.getChannelData(0)
      const view = new DataView(pcm.buffer, pcm.byteOffset, pcm.byteLength)
      for (let i = 0; i < channel.length; i++) channel[i] = view.getInt16(i * 2, true) / 32768
      const source = context.createBufferSource()
      source.buffer = buffer
      source.connect(this.meter ?? context.destination)
      const epoch = this.localEpoch
      source.onended = () => {
        this.sources.delete(source)
        source.disconnect()
        if (epoch !== this.localEpoch) return
        this.drainSafely()
        if (!this.pendingPcm.length && !this.sources.size && this.providerCompleted)
          this.finishPlayback()
      }
      const when = Math.max(this.nextStart, context.currentTime)
      source.start(when)
      this.sources.add(source)
      this.nextStart = when + duration
      this.pendingPcm.shift()
      this.pendingBytes -= pcm.length
      this.setState('playing')
    }
  }
}
