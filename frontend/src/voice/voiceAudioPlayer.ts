export type PlaybackState = 'playing'|'paused'|'ended'|'stopped'|'failed'

class VoiceAudioPlayer {
  private audio: HTMLAudioElement | null = null
  private epoch = 0
  private owner: number | null = null
  private notify: ((state: PlaybackState) => void) | null = null
  private settle: (() => void) | null = null

  isOwner(messageId: number) { return this.owner === messageId }

  stop(messageId?: number) {
    if (messageId !== undefined && this.owner !== messageId) return
    this.epoch++
    this.audio?.pause()
    this.settle?.()
    this.settle = null
    if (this.audio) {
      this.audio.onended = null
      this.audio.onerror = null
      this.audio.removeAttribute('src')
      this.audio.load()
    }
    this.audio = null
    this.notify?.('stopped')
    this.notify = null
    this.owner = null
  }

  pause(messageId: number) {
    if (this.owner === messageId && this.audio) { this.audio.pause(); this.notify?.('paused') }
  }

  resume(messageId: number) {
    if (this.owner === messageId && this.audio) {
      void this.audio.play().then(() => this.notify?.('playing')).catch(() => this.notify?.('failed'))
    }
  }

  async play(messageId: number, urls: string[], notify: (state: PlaybackState) => void) {
    this.stop()
    const token = this.epoch
    this.owner = messageId
    this.notify = notify
    try {
      for (const url of urls) {
        if (token !== this.epoch) return
        const audio = new Audio(url)
        this.audio = audio
        await new Promise<void>((resolve, reject) => {
          this.settle = resolve
          audio.onended = () => {
            if (token !== this.epoch || this.audio !== audio) return
            this.settle = null
            resolve()
          }
          audio.onerror = () => {
            if (token !== this.epoch || this.audio !== audio) return
            this.settle = null
            reject(new Error('音频播放失败'))
          }
          void audio.play().then(() => { if (token === this.epoch) notify('playing') }).catch(reject)
        })
      }
      if (token === this.epoch) { notify('ended'); this.audio = null; this.owner = null; this.notify = null }
    } catch { if (token === this.epoch) { this.stop(); notify('failed') } }
  }
}

export const voiceAudioPlayer = new VoiceAudioPlayer()
