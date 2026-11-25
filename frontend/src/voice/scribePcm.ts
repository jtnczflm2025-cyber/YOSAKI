// Mono, signed 16-bit little-endian PCM for the isolated Scribe draft relay.
export function pcm16At16k(input: Float32Array, sourceRate: number): Uint8Array {
  if (!Number.isFinite(sourceRate) || sourceRate < 16000 || sourceRate > 192000)
    throw new Error('麦克风采样率不受支持')
  const count = Math.floor(input.length * 16000 / sourceRate)
  const output = new Uint8Array(count * 2)
  for (let i = 0; i < count; i++) {
    const begin = Math.floor(i * sourceRate / 16000)
    const end = Math.min(input.length, Math.max(begin + 1, Math.floor((i + 1) * sourceRate / 16000)))
    let sample = 0
    for (let j = begin; j < end; j++) sample += input[j]
    sample = Math.max(-1, Math.min(1, sample / (end - begin)))
    const value = Math.round(sample < 0 ? sample * 32768 : sample * 32767)
    output[i * 2] = value & 255
    output[i * 2 + 1] = (value >> 8) & 255
  }
  return output
}

export function pcmBase64(bytes: Uint8Array): string {
  let binary = ''
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i])
  return btoa(binary)
}
