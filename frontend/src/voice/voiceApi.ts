export type VoiceConfiguration = { schema_version: 1; model_standard: string; model_realtime: string; model_stt: string; language: 'auto'|'zh'|'ja'|'en'; personality: string; expression_mode?: 'none'|'auto'|'thoughtful'; audio_format: 'mp3_44100_128'|'pcm_16000'|'pcm_24000' }
export type VoiceProfile = { character_id: number; voice_id: string; active: boolean; revision: number; configuration: VoiceConfiguration }
export type VoiceStatus = { enabled: boolean; expression_enabled?:boolean; automatic_expression_ready?:boolean; auto_daily_budget_enabled?:boolean; scribe_daily_enabled?:boolean; api_key_configured: boolean; real_api_verified: boolean; planner_version?:string; models: {voice_id:string;model_standard:string;model_realtime:string;model_stt:string} }
export type Privacy = { notice_version:string; accepted:boolean; accepted_at:string|null }

const wsUnavailability: Record<string,string> = {
  daily_output_disabled: '当前服务未启用 WS 播放。',
  real_provider_disabled: '当前服务未开放真实 WS 合成。',
  api_key_missing: '服务端缺少语音密钥。',
  unsupported_proxy: 'WS 代理配置不受支持。',
  realtime_model_unsupported: '声音档案的实时模型不支持 WS 播放。',
  realtime_text_unsupported: 'WS 暂只支持通过核验的日语单段回复（最多 1000 字）。',
  binding_or_permission_invalid: '回复、声音档案或第三方处理授权不符合 WS 播放条件。',
  voice_call_tts_text_too_long: '回复超出当前通话的剩余送读字符额度。',
  voice_call_tts_request_limit: '当前通话的合成请求额度已用完。',
  auto_daily_budget_exhausted: '今日朗读剩余额度不足。',
  output_busy: '已有实时播放任务。',
  authorization_changed: '语音授权已变化。',
}
export function wsUnavailableText(reason?:string|null) {
  return (reason && wsUnavailability[reason]) || '无法确认 WS 播放可用，请检查服务状态。'
}

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
