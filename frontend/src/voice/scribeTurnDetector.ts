/** Local utterance end detection. It makes no claim to solve acoustic echo. */
export class ScribeTurnDetector {
  private elapsed = 0
  private voiced = 0
  private quietAfterVoice = 0
  private committed = false
  lastAudibleMs: number|null = null

  observe(samples: Float32Array, sampleRate: number): 'commit'|'no_speech'|null {
    if (this.committed || !samples.length || sampleRate <= 0) return null
    const seconds = samples.length / sampleRate
    this.elapsed += seconds
    let energy = 0
    for (let i = 0; i < samples.length; i++) energy += samples[i] * samples[i]
    const audible = Math.sqrt(energy / samples.length) >= 0.015
    if (audible) {
      this.lastAudibleMs = performance.now()
      this.voiced += seconds
      this.quietAfterVoice = 0
    } else if (this.voiced > 0) {
      this.quietAfterVoice += seconds
    }
    if (this.voiced >= 0.25 && this.quietAfterVoice >= 0.8 && this.elapsed >= 2) {
      this.committed = true
      return 'commit'
    }
    if (this.elapsed >= 12 - 1e-6) {
      this.committed = true
      return this.voiced >= 0.25 ? 'commit' : 'no_speech'
    }
    return null
  }
}
