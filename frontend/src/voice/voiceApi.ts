export type VoiceConfiguration = { schema_version: 1; model_standard: string; model_realtime: string; model_stt: string; language: 'auto'|'zh'|'ja'|'en'; personality: string; audio_format: 'mp3_44100_128'|'pcm_16000'|'pcm_24000' }
export type VoiceProfile = { character_id: number; voice_id: string; active: boolean; revision: number; configuration: VoiceConfiguration }
export type VoiceStatus = { enabled: boolean; expression_enabled?:boolean; auto_daily_budget_enabled?:boolean; scribe_daily_enabled?:boolean; api_key_configured: boolean; real_api_verified: boolean; planner_version?:string; models: {voice_id:string;model_standard:string;model_realtime:string;model_stt:string} }
export type Privacy = { notice_version:string; accepted:boolean; accepted_at:string|null }

export async function voiceApi<T>(path: string, method='GET', body?: object): Promise<T> {
  const response = await fetch(`/api/voice${path}`, { method, credentials:'same-origin',
    headers: body ? {'Content-Type':'application/json'} : undefined,
    body: body ? JSON.stringify(body) : undefined })
  if (!response.ok) {
    const result = await response.json().catch(() => null)
    const detail = result?.detail
    throw new Error(typeof detail === 'string' ? detail : response.status === 422
      ? '设置值无效或超出允许范围，请检查输入框标注的上限。'
      : `语音请求失败（${response.status}）`)
  }
  return response.status === 204 ? undefined as T : response.json()
}
