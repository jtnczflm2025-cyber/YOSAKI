<script setup lang="ts">
import { onMounted, ref } from 'vue'

type Limits = { rounds:number; seconds:number; tts_requests:number; tts_characters:number }
type Settings = { limits:Limits; maximum:Limits; revision:number; configurable:boolean }
const emit = defineEmits<{ saved:[] }>()
const current = ref<Settings|null>(null)
const form = ref<Limits>({ rounds:2, seconds:180, tts_requests:2, tts_characters:160 })
const confirmIncrease = ref(false)
const busy = ref(false)
const notice = ref('')

async function load() {
  const response = await fetch('/api/voice/calls/settings')
  if (!response.ok) throw new Error('通话限制设置暂不可用')
  const result = await response.json() as Settings
  current.value = result
  form.value = { ...result.limits }
  confirmIncrease.value = false
}

async function save() {
  if (!current.value || !current.value.configurable || busy.value) return
  busy.value = true
  notice.value = ''
  try {
    const response = await fetch('/api/voice/calls/settings', {
      method:'PUT', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({ ...form.value, expected_revision:current.value.revision,
                            confirm_increase:confirmIncrease.value }),
    })
    if (!response.ok) {
      const detail = (await response.json().catch(() => null))?.detail
      throw new Error(detail === 'voice_call_settings_revision_conflict'
        ? '设置已在另一页面更新，请刷新后再保存。'
        : detail === 'voice_call_settings_increase_confirmation_required'
          ? '提高通话限制前，请勾选确认。'
          : '保存失败，请检查输入范围。')
    }
    const result = await response.json() as Settings
    current.value = result
    form.value = { ...result.limits }
    confirmIncrease.value = false
    notice.value = '通话限制已保存；从下一通开始生效。每日额度仍同时限制实际调用。'
    emit('saved')
  } catch (error) {
    notice.value = error instanceof Error ? error.message : '保存失败。'
  } finally { busy.value = false }
}

onMounted(() => { void load().catch(() => { notice.value = '通话限制设置暂不可用。' }) })
</script>

<template>
  <details class="call-settings">
    <summary>设置每通话限制</summary>
    <p>默认两轮、3 分钟。合成请求数与送读字符数都不能小于轮数。每通话限制与每日录音、模型和朗读额度同时生效；重新开始不会恢复已用的每日额度。</p>
    <div v-if="current?.configurable" class="call-settings-grid">
      <label>最多轮数（1–{{current.maximum.rounds}}）<input v-model.number="form.rounds" type="number" min="1" :max="current.maximum.rounds" step="1" /></label>
      <label>最长秒数（60–{{current.maximum.seconds}}）<input v-model.number="form.seconds" type="number" min="60" :max="current.maximum.seconds" step="1" /></label>
      <label>最多合成请求（至少等于轮数，最多 {{current.maximum.tts_requests}}）<input v-model.number="form.tts_requests" type="number" :min="form.rounds" :max="current.maximum.tts_requests" step="1" /></label>
      <label>最多送读字符（至少等于轮数，最多 {{current.maximum.tts_characters}}）<input v-model.number="form.tts_characters" type="number" :min="form.rounds" :max="current.maximum.tts_characters" step="1" /></label>
      <label class="call-settings-confirm"><input v-model="confirmIncrease" type="checkbox" />我确认提高以后每通话的调用上限；实际使用仍受每日额度约束，可能增加费用。</label>
      <button type="button" :disabled="busy" @click="save">保存通话限制</button>
    </div>
    <p v-else>可调限制尚未完成受控迁移，当前继续使用两轮／180 秒。</p>
    <p v-if="notice" role="status">{{ notice }}</p>
  </details>
</template>

<style scoped>
.call-settings { margin:.5rem 0; }
.call-settings-grid { display:grid; gap:.5rem; grid-template-columns:repeat(auto-fit,minmax(9rem,1fr)); }
.call-settings-grid label { display:grid; gap:.25rem; }
.call-settings-grid input[type=number] { min-width:0; width:100%; }
.call-settings-confirm { grid-column:1 / -1; display:flex !important; align-items:center; }
.call-settings-confirm input { width:auto; }
</style>
