/** Sound-triggered stop only. Acoustic echo cannot be distinguished from speech. */
export class LocalBargeDetector {
  private voicedSeconds = 0
  private triggered = false

  observe(samples: Float32Array, sampleRate: number): boolean {
    if (this.triggered || !samples.length || sampleRate <= 0) return false
    let energy = 0
    for (const sample of samples) energy += sample * sample
    const rms = Math.sqrt(energy / samples.length)
    this.voicedSeconds = rms >= 0.045
      ? this.voicedSeconds + samples.length / sampleRate : 0
    if (this.voicedSeconds < 0.45) return false
    this.triggered = true
    return true
  }
}
