<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

type Model = { id: number; name: string; enabled: boolean }
type Tendency = { driveId: string; name: string; activation: number; satisfaction: number; trend: string; evidenceEventIds: string[] }
type Advisory = { id: string; createdAt: string; tendencies: Tendency[]; conflicts: { description: string; kind: string }[]; uncertainty: string[] }
type Intention = { id: string; title: string; description: string; stage: 'candidate' | 'pending' | 'committed'; status: string; source: string; reason_summary: string; self_note: string; version: number; relatedDriveIds: string[] }
type Observation = { id: string; mode: string; created_at: string; result: { selfDescription: string; metaDesires: { targetDriveId: string; stance: string; explanation: string }[]; unresolvedQuestions: string[] } }
type Decision = { id: string; created_at: string; result: { type: string; reasonSummary: string; selfNote: string } }

const props = defineProps<{ characterId: number; models: Model[]; refreshKey: number }>()
const advisory = ref<Advisory | null>(null)
const intentions = ref<Intention[]>([])
const observations = ref<Observation[]>([])
const decisions = ref<Decision[]>([])
const modelId = ref<number | null>(null)
const mode = ref<'passive' | 'reflective' | 'goal_focused'>('passive')
const targetItemId = ref('')
const includeMemory = ref(false)
const environment = ref('')
const busy = ref(false)
const notice = ref('')
const active = computed(() => intentions.value.filter(item => item.status === 'active'))
const grouped = computed(() => ({
  candidate: active.value.filter(item => item.stage === 'candidate'),
  pending: active.value.filter(item => item.stage === 'pending'),
  committed: active.value.filter(item => item.stage === 'committed'),
}))

function base() { return `/api/desires/${props.characterId}` }
async function request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(path, { method, credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || `请求失败（${response.status}）`)
  return response.json()
}
async function refresh() {
  const [a, i, o, d] = await Promise.all([
    request<Advisory>(`${base()}/advisory`), request<Intention[]>(`${base()}/intentions`),
    request<Observation[]>(`${base()}/observations`), request<Decision[]>(`${base()}/decisions`),
  ])
  advisory.value = a; intentions.value = i; observations.value = o; decisions.value = d
  if (!modelId.value || !props.models.some(model => model.id === modelId.value)) modelId.value = props.models[0]?.id ?? null
}
async function run(action: () => Promise<unknown>, message: string) {
  busy.value = true; notice.value = ''
  try { await action(); await refresh(); notice.value = message }
  catch (error) { notice.value = error instanceof Error ? error.message : '请求失败' }
  finally { busy.value = false }
}
async function observe() {
  if (!modelId.value) return
  await run(() => request(`${base()}/observations`, 'POST', {
    schemaVersion: 2, requestId: crypto.randomUUID(), modelProfileId: modelId.value,
    mode: mode.value, targetItemId: mode.value === 'goal_focused' ? targetItemId.value || null : null,
    includeMemoryContext: includeMemory.value,
  }), '自我观察已记录')
}
async function decide() {
  if (!modelId.value) return
  await run(() => request(`${base()}/decisions`, 'POST', {
    schemaVersion: 2, requestId: crypto.randomUUID(), modelProfileId: modelId.value,
    targetItemId: targetItemId.value || null, observationId: observations.value[0]?.id ?? null,
    includeMemoryContext: includeMemory.value, environmentSummary: environment.value,
  }), '意愿判断已记录')
}
async function transition(item: Intention, action: 'archive' | 'abandon' | 'reconsider' | 'complete') {
  await run(() => request(`${base()}/intentions/${item.id}/transition`, 'POST', {
    schemaVersion: 2, requestId: crypto.randomUUID(), expectedVersion: item.version, action,
  }), '意愿状态已更新')
}
const labels: Record<string, string> = { candidate: '候选兴趣', pending: '未决意愿', committed: '长期目标' }
const decisionLabels: Record<string, string> = { create_goal: '形成新意愿', choose_goal: '选择目标', maintain_goal: '维持目标', defer_goal: '暂时推迟', reject_goal: '放弃目标', revise_goal: '修改目标', no_action: '决定暂不行动', undecided: '尚未决定' }
onMounted(() => { void refresh().catch(error => { notice.value = error instanceof Error ? error.message : '读取失败' }) })
watch(() => [props.characterId, props.refreshKey], (next, previous) => {
  if (next[0] !== previous?.[0]) { advisory.value = null; intentions.value = []; observations.value = []; decisions.value = [] }
  void refresh().catch(error => { notice.value = error instanceof Error ? error.message : '读取失败' })
})
</script>

<template>
  <div class="volition-panel">
    <section class="desire-section">
      <h3>状态参谋</h3>
      <p>依据当前模拟驱力与事件生成；查看此处不调用模型。</p>
      <p v-if="!advisory">正在读取状态…</p>
      <template v-else>
        <div class="volition-tendencies">
          <article v-for="tendency in advisory.tendencies.filter(x => x.activation >= .25).slice(0, 6)" :key="tendency.driveId" class="desire-log">
            <strong>{{ tendency.name }} · {{ tendency.activation.toFixed(2) }}</strong>
            <span>满足 {{ tendency.satisfaction.toFixed(2) }} · 趋势 {{ tendency.trend === 'rising' ? '增强' : tendency.trend === 'falling' ? '减弱' : tendency.trend === 'stable' ? '稳定' : '待观察' }}</span>
            <small v-if="tendency.evidenceEventIds.length">事件证据：{{ tendency.evidenceEventIds.join('、') }}</small>
          </article>
        </div>
        <p v-for="conflict in advisory.conflicts" :key="conflict.description">潜在张力：{{ conflict.description }}</p>
        <p v-for="uncertain in advisory.uncertainty" :key="uncertain">{{ uncertain }}</p>
      </template>
    </section>

    <section class="desire-section">
      <h3>自我观察与意愿判断</h3>
      <p>两项操作均只保存模拟的公开描述与判断，不执行活动或调用工具。模型可以选择犹豫、休息或什么都不做。</p>
      <div class="desire-controls">
        <label>模型<select v-model="modelId"><option :value="null">选择模型</option><option v-for="profile in models" :key="profile.id" :value="profile.id">{{ profile.name }}</option></select></label>
        <label>观察模式<select v-model="mode"><option value="passive">被动观察</option><option value="reflective">反思观察</option><option value="goal_focused">围绕意愿</option></select></label>
        <label>关注的意愿<select v-model="targetItemId"><option value="">不指定</option><option v-for="item in active" :key="item.id" :value="item.id">{{ item.title }}</option></select></label>
        <label class="volition-check"><input v-model="includeMemory" type="checkbox"/>参考长期记忆</label>
      </div>
      <label class="volition-environment">当前情境（可选）<textarea v-model="environment" maxlength="1000" rows="2" placeholder="例如：今天只有十分钟，暂时不适合查资料。"/></label>
      <div class="desire-actions"><button class="secondary-btn" :disabled="busy || !modelId || (mode === 'goal_focused' && !targetItemId)" @click="observe">请求自我观察</button><button class="primary-btn" :disabled="busy || !modelId" @click="decide">进行纯意愿判断</button></div>
      <p role="status" class="desire-notice">{{ notice }}</p>
      <article v-if="observations[0]" class="desire-log"><strong>最近观察 · {{ observations[0].mode }}</strong><p>{{ observations[0].result.selfDescription || '这次没有形成文字描述。' }}</p><p v-for="meta in observations[0].result.metaDesires" :key="meta.targetDriveId">{{ meta.targetDriveId }}：{{ meta.stance }} · {{ meta.explanation }}</p><small v-for="question in observations[0].result.unresolvedQuestions" :key="question">未解决：{{ question }}</small></article>
      <article v-if="decisions[0]" class="desire-log"><strong>最近决定 · {{ decisionLabels[decisions[0].result.type] || decisions[0].result.type }}</strong><p>{{ decisions[0].result.reasonSummary }}</p><small>{{ decisions[0].result.selfNote }}</small></article>
      <details v-if="observations.length > 1 || decisions.length > 1"><summary>观察与判断历史</summary><p v-for="entry in observations.slice(1)" :key="entry.id">{{ entry.created_at }} · {{ entry.result.selfDescription || '无文字描述' }}</p><p v-for="entry in decisions.slice(1)" :key="entry.id">{{ entry.created_at }} · {{ decisionLabels[entry.result.type] || entry.result.type }} · {{ entry.result.reasonSummary }}</p></details>
    </section>

    <section class="desire-section">
      <h3>未决意愿池与长期目标</h3>
      <p>未决意愿只是“已经决定、暂时推迟”。它不会随时间自动提高紧迫度，也没有完成率。</p>
      <div v-for="stage in (['candidate', 'pending', 'committed'] as const)" :key="stage" class="volition-group">
        <h4>{{ labels[stage] }}</h4><p v-if="!grouped[stage].length">暂无</p>
        <article v-for="item in grouped[stage]" :key="item.id" class="desire-log">
          <strong>{{ item.title }}</strong><p v-if="item.description">{{ item.description }}</p>
          <span>来源：{{ item.source }} · {{ item.reason_summary }}</span><small v-if="item.self_note">模拟自我备注：「{{ item.self_note }}」</small>
          <div class="desire-actions"><button class="text-btn" :disabled="busy" @click="transition(item, 'archive')">归档</button><button class="text-btn" :disabled="busy" @click="transition(item, 'abandon')">放弃</button><button v-if="stage === 'committed'" class="text-btn" :disabled="busy" @click="transition(item, 'complete')">标记完成</button></div>
        </article>
      </div>
      <details v-if="intentions.some(item => item.status !== 'active')"><summary>已归档、放弃或完成</summary><article v-for="item in intentions.filter(x => x.status !== 'active')" :key="item.id" class="desire-log"><strong>{{ item.title }} · {{ item.status }}</strong><button v-if="item.status === 'archived' || item.status === 'rejected'" class="text-btn" :disabled="busy" @click="transition(item, 'reconsider')">重新考虑</button></article></details>
    </section>
  </div>
</template>

<style scoped>
.volition-panel{display:grid;gap:14px;min-width:0}
.desire-section{padding:17px;border:1px solid var(--ui-line-soft);border-radius:14px;background:var(--ui-raised);display:grid;gap:10px;min-width:0}
.desire-section h3,.volition-group h4{margin:0}.desire-section p{margin:0;line-height:1.5}
.desire-controls{display:flex;flex-wrap:wrap;gap:12px}.desire-controls label{display:grid;gap:5px;min-width:min(140px,100%);flex:1;color:var(--ui-muted);font-size:13px}
.desire-controls select,.volition-environment textarea{box-sizing:border-box;width:100%;color:var(--ui-ink);background:var(--ui-surface);border:1px solid var(--ui-line-soft);border-radius:7px;padding:8px;font:inherit}
.desire-controls select{appearance:auto;color-scheme:dark}
.desire-actions{display:flex;flex-wrap:wrap;gap:8px}.desire-log{display:grid;gap:5px;min-width:0;padding:8px 0;border-top:1px solid var(--ui-line-soft)}
.desire-log p{font-size:13px;overflow-wrap:anywhere}.desire-log span,.desire-log small{font-size:13px;color:var(--ui-accent);overflow-wrap:anywhere}
.desire-notice{min-height:1.2em;color:var(--ui-accent)}.volition-check{display:flex!important;align-items:center;gap:7px!important}.volition-check input{width:auto!important}
.volition-environment{display:grid;gap:7px;margin:0;color:var(--ui-muted);font-size:13px}.volition-tendencies{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:7px}
.volition-group{display:grid;gap:7px;margin-top:6px}.volition-group h4{font-size:13px;color:var(--ui-ink)}
</style>
