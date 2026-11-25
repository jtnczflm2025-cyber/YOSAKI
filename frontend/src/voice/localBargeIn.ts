import { acquireScribeMicrophone } from './scribeCapture'
import { LocalBargeDetector } from './localBargeDetector'

/** Single-use bounded audio captured during an intentional headset call. */
export interface BargeCapturePort {
  readonly sampleRate:number
  append(frame:Float32Array):void
  subscribe(fn:(frame:Float32Array,rate:number)=>void):boolean
  close():void
  readonly active:boolean
}
export class BargeCapture implements BargeCapturePort {
  private frames: Float32Array[] = []
  private samples = 0
  private consumer: ((frame:Float32Array, rate:number)=>void)|null = null
  private closed = false
  private claimed = false
  constructor(readonly sampleRate:number, private release:()=>void) {}
  append(frame:Float32Array) {
    if (this.closed) return
    const copy=frame.slice()
    if (this.consumer) { this.consumer(copy,this.sampleRate); return }
    this.frames.push(copy);this.samples+=copy.length
    if(this.samples>this.sampleRate*5) this.close()
  }
  subscribe(fn:(frame:Float32Array,rate:number)=>void):boolean {
    if(this.closed||this.claimed) return false
    this.claimed=true;this.consumer=fn
    for(const frame of this.frames) {if(this.closed)break;fn(frame,this.sampleRate)}
    this.frames=[];this.samples=0
    return !this.closed
  }
  close() {if(this.closed)return;this.closed=true;this.frames=[];this.samples=0;this.consumer=null;this.release()}
  get active() {return !this.closed}
}

export class LocalBargeMonitor {
  private epoch = 0
  private stream: MediaStream|null = null
  private context: AudioContext|null = null
  private source: MediaStreamAudioSourceNode|null = null
  private processor: ScriptProcessorNode|null = null
  private handoff: BargeCapture|null = null
  private syntheticTimer: ReturnType<typeof setInterval>|null = null

  async start(onSpeech: (capture:BargeCapture) => void, onUnavailable: () => void,
              deviceId = '', onReady: () => void = () => {}): Promise<void> {
    this.stop()
    const token = this.epoch
    if (import.meta.env.VITE_YOSAKI_BARGE_FAKE_TEST === '1') {
      // Build-time isolated browser fixture: no microphone or supplier access.
      const testWindow = window as Window & {__yosakiBargeFakeTriggered?:boolean;
        __yosakiBargeFakeSilent?:boolean}
      if (testWindow.__yosakiBargeFakeTriggered) return
      testWindow.__yosakiBargeFakeTriggered = true
      const sampleRate = 16000, detector = new LocalBargeDetector()
      let frameIndex = 0, activeCapture:BargeCapture|null = null
      let timer:ReturnType<typeof setInterval>|null=null
      const release = () => {
        if(timer)clearInterval(timer)
        if(this.syntheticTimer===timer)this.syntheticTimer=null
        timer=null
      }
      timer = setInterval(() => {
        if (token !== this.epoch && !activeCapture) { release(); return }
        if (frameIndex === 0) onReady()
        const voiced = !testWindow.__yosakiBargeFakeSilent && frameIndex >= 12 && frameIndex < 30
        const frame = new Float32Array(1600).fill(voiced ? 0.1 : 0)
        frameIndex++
        if (activeCapture) activeCapture.append(frame)
        else if (detector.observe(frame, sampleRate)) {
          activeCapture = new BargeCapture(sampleRate, release)
          this.handoff = activeCapture
          activeCapture.append(frame)
          onSpeech(activeCapture)
        }
        if (frameIndex >= 45) { activeCapture?.close(); release() }
      }, 100)
      this.syntheticTimer=timer
      return
    }
    try {
      const stream = await acquireScribeMicrophone(
        constraints => navigator.mediaDevices.getUserMedia(constraints),
        () => token === this.epoch, deviceId)
      if (!stream) return
      this.stream = stream
      const context = new AudioContext()
      this.context = context
      await context.resume()
      if (token !== this.epoch) return
      if (context.state !== 'running') { this.stop(); onUnavailable(); return }
      const detector = new LocalBargeDetector()
      const preroll:Float32Array[]=[]
      let prerollSamples=0
      this.source = context.createMediaStreamSource(stream)
      this.processor = context.createScriptProcessor(2048, 1, 1)
      const processor=this.processor
      const source=this.source
      let activeCapture:BargeCapture|null=null
      let ready = false
      const release=()=>{
        processor.onaudioprocess=null;processor.disconnect();source.disconnect()
        stream.getTracks().forEach(track=>track.stop())
        void context.close().catch(()=>{})
      }
      this.processor.onaudioprocess = event => {
        event.outputBuffer.getChannelData(0).fill(0)
        const frame=event.inputBuffer.getChannelData(0)
        if(activeCapture){activeCapture.append(frame);return}
        if (token !== this.epoch) return
        if (!ready) { ready = true; onReady() }
        preroll.push(frame.slice());prerollSamples+=frame.length
        while(prerollSamples>context.sampleRate*0.6&&preroll.length>1)
          prerollSamples-=preroll.shift()!.length
        if (detector.observe(frame, context.sampleRate)) {
          const capture=new BargeCapture(context.sampleRate,release)
          activeCapture=capture
          this.handoff=capture
          for(const saved of preroll)capture.append(saved)
          onSpeech(capture)
        }
      }
      this.source.connect(this.processor)
      this.processor.connect(context.destination)
      stream.getTracks().forEach(track => track.addEventListener('ended', () => {
        if (token === this.epoch) { this.stop(); onUnavailable() }
      }, { once:true }))
    } catch {
      if (token === this.epoch) { this.stop(); onUnavailable() }
    }
  }

  take(capture:BargeCapture):BargeCapture|null {
    if(this.handoff!==capture||!capture.active)return null
    this.handoff=null
    // The capture owns its timer or microphone until Scribe releases it.
    this.syntheticTimer=null
    this.stream=null;this.context=null;this.source=null;this.processor=null
    return capture
  }

  stop(): void {
    this.epoch++
    if(this.syntheticTimer)clearInterval(this.syntheticTimer)
    this.syntheticTimer=null
    if(this.handoff){
      const handoff=this.handoff;this.handoff=null
      this.processor=null;this.source=null;this.stream=null;this.context=null
      handoff.close()
      return
    }
    if (this.processor) { this.processor.onaudioprocess = null; this.processor.disconnect() }
    this.processor = null
    this.source?.disconnect(); this.source = null
    this.stream?.getTracks().forEach(track => track.stop()); this.stream = null
    if (this.context) void this.context.close().catch(() => {})
    this.context = null
  }
}
