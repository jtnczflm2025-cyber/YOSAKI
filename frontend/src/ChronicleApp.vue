<script setup lang="ts">
import { theme, toggleTheme } from './ui/theme'
import { watch, computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { ArrowLeft, ArrowRight, BookOpen, CalendarDays, ChevronDown, ChevronRight, ChevronUp, CircleAlert, Clock3, FileText, Layers3, LoaderCircle, LogIn, Moon, RefreshCw, Search, ShieldCheck, Sun, X } from 'lucide-vue-next'
import './chronicle.css'
import ChronicleImportWizard from './ChronicleImportWizard.vue'
import ChronicleEditor from './ChronicleEditor.vue'
import type {ReviewChange} from './chronicleReviewChange'
import ChronicleBatchReview from './ChronicleBatchReview.vue'
import ChroniclePeriodSummaries from './ChroniclePeriodSummaries.vue'
import { isoWeekBounds, periodLabel as formatPeriod, quarterName } from './chroniclePeriods'

type SourceRef = {kind: string; id: string}
type Node = {nodeId: string; level: string | null; period: string | null; timeBasis: string; title: string; entrySynopsis?: string | null; entryIds: string[]; entryLabels?: {entryId: string; title: string}[]; sourceRefs: SourceRef[]; memberCount: number; childNodeIds: string[]; summaryStatus: string; taskStatus: string | null; digestVersionId: string | null; summaryPreview?: string; summaryPage?: string | null; nextOffset?: number | null; nextMemberCursor?: string | null; children?: Node[]}
type Entry = {entryId: string; versionId: string; title: string; keywords: string[]; tags: string[]; synopsis: string; occurredAt: [string | null, string | null]; recordedAt: [string | null, string | null]; timeBasis: string; sourceRefs: SourceRef[]; views: Record<'RAW' | 'NARRATIVE' | 'CLEANED', {status: string; versionId?: string}>}
type Card = Pick<Entry, 'entryId' | 'title' | 'keywords' | 'tags' | 'synopsis' | 'occurredAt' | 'recordedAt' | 'timeBasis' | 'sourceRefs'> & {level: string | null}
type TextPage = {text: string; nextOffset: number | null; versionId?: string; contentHash?: string; sourceRole?: string; sourceRef?: SourceRef; totalLength: number; sourceRefs?: SourceRef[]}
type Character = {id: number; name: string}
type DigestSource = {ordinal: number; sourceRef: SourceRef; role: string; status: string; evidenceKind: string}
type DigestDisplay = {mode: 'extractive' | 'reviewed_digest' | 'unavailable'; label: string; versionId: string | null; summaryStatus: string; sourceMeta: DigestSource[]; sections: {ordinal: number; text: string; sourceOffset: number; incomplete: boolean}[]; text: string; nextOffset: number | null; totalLength: number}
type ProjectionBlock = {projectionKind: string; viewType: string | null; level: string | null; period: string | null; entryIds: string[]; sourceRefs: SourceRef[]; text: string; summaryStatus: string; estimatedTokens: number; versionRefs: {kind: string; id: string; entryId?: string}[]}
type ProjectionResult = {blocks: ProjectionBlock[]; rejected: {entryId: string; reason: string}[]; selectionDiagnostics: {entryId: string; eligibility: string; level?: string | null; period?: string | null; summaryStatus?: string; entryVersionId?: string; sourceRefs?: SourceRef[]; availableViews?: string[]}[]; selectedCount: number; uniqueExperienceCount: number; estimatedTokens: number; shadowOnly: boolean; formalModelReceived: boolean}
type NavigationMonth = {key: string; label: string; nodes: Node[]}
type NavigationYear = {key: string; label: string; months: NavigationMonth[]}

function localHongKongTime(date: Date) { return new Date(date.getTime() + 480 * 60000).toISOString().slice(0, 16) }

const authReady = ref(false)
const authenticated = ref(false)
const loginBusy = ref(false)
const username = ref('chronicle-demo')
const password = ref('')
// Theme shared with the workbench, including across direct entry and reload.
const characters = ref<Character[]>([])
const characterId = ref<number | null>(null)
const asOf = ref(new Date().toISOString())
const tree = ref<Node[]>([])
const treeNext = ref<string | null>(null)
const treeLoading = ref(false)
const status = ref<{state: string; available: number; excludedStale: number} | null>(null)
const diagnosticsOpen = ref(false)
const availableCount = computed(() => status.value?.available ?? (treeLoading.value ? '—' :
  `${tree.value.reduce((total, node) => total + node.memberCount, 0)}${treeNext.value ? '+' : ''}`))
const activeNode = ref<Node | null>(null)
const nodeTrail = ref<Node[]>([])
const nodeLoading = ref(false)
const digestDisplay = ref<DigestDisplay | null>(null)
const summaryBusy = ref(false)
const memberNext = ref<string | null>(null)
const selectedEntry = ref<Entry | null>(null)
const entryLoading = ref(false)
type ProgressiveDraft = {entryId: string; versionId: string; sourceRefs: unknown[]; fields: Record<string, {value: unknown; completionStatus: string; lifecycleStatus: string}>}
const progressiveDrafts = ref<ProgressiveDraft[]>([])
const draftCharacterId = ref<number | null>(null)
const draftBusy = ref(false)
const draftError = ref('')
const draftNext = ref<number | null>(null)
async function loadProgressiveDrafts(more = false) {
  if (characterId.value === null || draftBusy.value) return
  const role = characterId.value
  draftBusy.value = true; draftError.value = ''
  if (!more) { progressiveDrafts.value = []; draftNext.value = null }
  try {
    const result = await getJson<{items: ProgressiveDraft[]; nextOffset: number | null}>(
      '/api/memory-v2/entry-drafts', {characterId: role, offset: more ? draftNext.value : 0})
    if (characterId.value !== role) return
    draftCharacterId.value = role
    progressiveDrafts.value = more ? [...progressiveDrafts.value, ...result.items] : result.items
    draftNext.value = result.nextOffset
  } catch { progressiveDrafts.value = []; draftError.value = '草稿不可读取，请检查记忆服务、登录或来源状态。' }
  finally { draftBusy.value = false }
}
const editorId = ref<string|null>(null)
const importRef=ref<{dirty:boolean}|null>(null)
const reviewPanel=ref<{reviewBatch:(ids:string[],selectAll?:boolean)=>Promise<void>;busy:boolean}|null>(null)
function openBatchReview(ids:string[],selectAll=false){
  if(!reviewPanel.value || reviewPanel.value.busy)return
  void reviewPanel.value.reviewBatch(ids,selectAll)
  document.getElementById('chronicle-review')?.scrollIntoView({block:'start'})
}
const reviewRevision=ref(0)
const reviewChanges=ref<Record<string,ReviewChange>>({})
function revised(change:ReviewChange){if(change.characterId===characterId.value)reviewChanges.value[change.entryId]=change}
watch(characterId,()=>{reviewChanges.value={}})
function draftsChanged(){reviewRevision.value++;void loadProgressiveDrafts();void refreshAll()}
function toggleImport(){
  if(importRef.value?.dirty && !window.confirm('放弃导入面板中未保存的修改？'))return
  showImport.value=!showImport.value
}
const editorRef = ref<{dirty:boolean}|null>(null)
function editDraft(id:string) {
  if(editorRef.value?.dirty && !window.confirm('有未保存的修改。放弃后打开另一条？')) return
  editorId.value=id
}
const activeView = ref<'RAW' | 'CLEANED' | 'NARRATIVE'>('NARRATIVE')
const viewText = ref('')
const viewNext = ref<number | null>(null)
const viewVersion = ref<string | null>(null)
const viewBusy = ref(false)
const rawOrdinal = ref(0)
const rawText = ref('')
const rawNext = ref<number | null>(null)
const rawHash = ref<string | null>(null)
const rawRole = ref('')
const rawBusy = ref(false)
const query = ref('')
const fromDate = ref('')
const toDate = ref('')
const cards = ref<Card[]>([])
const cardsNext = ref<string | null>(null)
const searchLoading = ref(false)
const notice = ref('')
const error = ref('')
const showSearch = ref(false)
const expandedYears = ref<string[]>([])
const expandedMonths = ref<string[]>([])
const previewSelectedIds = ref<string[]>([])
const previewLocalTime = ref(localHongKongTime(new Date()))
const previewBudget = ref(512)
const previewMaxBlocks = ref(5)
const previewBusy = ref(false)
const previewResult = ref<ProjectionResult | null>(null)
const previewError = ref('')
const showImport = ref(new URLSearchParams(location.search).has('batch'))

const levelNames: Record<string, string> = {L0: '当日纪事', L1: '周记', L2: '月记', L3: '季度', L4: '半年', L5: '年度'}
const summaryNames: Record<string, string> = {available: '已审核摘要', missing: '独立审核版尚未生成', stale_or_unreviewed: '待审核或已失效', child_digest_missing: '独立审核版等待下级摘要', not_applicable: '详细条目'}
const currentCharacter = computed(() => characters.value.find(x => x.id === characterId.value))
const navIds = computed(() => showSearch.value ? cards.value.map(x => x.entryId) : activeNode.value?.entryIds ?? [])
const currentIndex = computed(() => navIds.value.indexOf(selectedEntry.value?.entryId ?? ''))
const canPrevious = computed(() => currentIndex.value > 0)
const canNext = computed(() => currentIndex.value >= 0 && currentIndex.value < navIds.value.length - 1)
const dateLabel = computed(() => {
  const item = selectedEntry.value
  if (!item) return ''
  if (item.timeBasis !== 'occurred') return '发生时间未核实'
  const dates = item.occurredAt
  if (!dates?.[0]) return '时间未核实'
  const start = dates[0].slice(0, 10)
  const end = dates[1]?.slice(0, 10)
  return end && end !== start ? `${start} — ${end}` : start
})

function periodLabel(node: Node) {
  return formatPeriod(node.period)
}

function navigationBucket(node: Node): [string, string] {
  if (!node.level || !node.period) return ['undated', 'undated:unknown']
  if (node.level === 'L0') return [node.period.slice(0, 4), `${node.period.slice(0, 4)}:${node.period.slice(5, 7)}`]
  if (node.level === 'L1') {
    const start = isoWeekBounds(node.period)?.[0]
    if (!start) return ['undated', 'undated:unknown']
    // An ISO week belongs to the year/month containing its Thursday.
    // W01 may start in December of the preceding Gregorian year.
    const thursday = new Date(`${start}T00:00:00Z`)
    thursday.setUTCDate(thursday.getUTCDate() + 3)
    const bucket = thursday.toISOString().slice(0, 7)
    return [bucket.slice(0, 4), `${bucket.slice(0, 4)}:${bucket.slice(5, 7)}`]
  }
  if (node.level === 'L2') return [node.period.slice(0, 4), `${node.period.slice(0, 4)}:${node.period.slice(5, 7)}`]
  return [node.period.slice(0, 4), `${node.period.slice(0, 4)}:wide`]
}

const navigationYears = computed<NavigationYear[]>(() => {
  const years = new Map<string, Map<string, Node[]>>()
  for (const node of tree.value) {
    const [year, month] = navigationBucket(node)
    if (!years.has(year)) years.set(year, new Map())
    const months = years.get(year)!
    if (!months.has(month)) months.set(month, [])
    months.get(month)!.push(node)
  }
  return [...years.entries()].sort(([a], [b]) => b.localeCompare(a)).map(([key, months]) => ({
    key, label: key === 'undated' ? '时间未核实' : `${key} 年`,
    months: [...months.entries()].sort(([a], [b]) => b.localeCompare(a)).map(([month, nodes]) => ({
      key: month, label: month.endsWith(':wide') ? '季度、半年与全年' : month.endsWith(':unknown') ? '日期未核实' : `${Number(month.slice(-2))} 月 · ${quarterName(Number(month.slice(-2)))}`, nodes
    }))
  }))
})

function toggleYear(key: string) { expandedYears.value = expandedYears.value.includes(key) ? expandedYears.value.filter(x => x !== key) : [...expandedYears.value, key] }
function toggleMonth(key: string) { expandedMonths.value = expandedMonths.value.includes(key) ? expandedMonths.value.filter(x => x !== key) : [...expandedMonths.value, key] }
function togglePreview(entryId: string) {
  previewSelectedIds.value = previewSelectedIds.value.includes(entryId)
    ? previewSelectedIds.value.filter(id => id !== entryId)
    : previewSelectedIds.value.length < 50 ? [...previewSelectedIds.value, entryId] : previewSelectedIds.value
  previewResult.value = null; previewError.value = ''
}

function previewTimeIso() {
  const parsed = new Date(`${previewLocalTime.value}:00+08:00`)
  if (!Number.isFinite(parsed.getTime())) throw new Error('模拟观察时间无效。')
  return parsed.toISOString()
}

function sourceRoleLabel(role: string) { return role === 'assistant' ? '阿夜的模型发言' : role === 'user' ? '用户发言' : '活动证据' }
function projectionLabel(kind: string) { return ({calendar_summary:'模型周期摘要',period_digest:'周期 DIGEST', detailed_narrative:'详细 NARRATIVE', entry_synopsis:'单经历梗概', title_index:'标题索引'} as Record<string,string>)[kind] || kind }
function rejectionLabel(reason: string) { return ({entry_missing_or_stale:'来源或权限已失效', duplicate_experience:'同一经历已计入', block_limit:'达到块数上限', budget_exhausted:'Token 预算不足', narrative_stale:'详细纪事已失效'} as Record<string,string>)[reason] || reason }
function entryTitle(id: string) { return cards.value.find(x => x.entryId === id)?.title || activeNode.value?.entryLabels?.find(x => x.entryId === id)?.title || (selectedEntry.value?.entryId === id ? selectedEntry.value.title : `经历 ${id.slice(0, 8)}`) }

function discardLoadedData() {
  tree.value = []; treeNext.value = null
  activeNode.value = null; nodeTrail.value = []
  digestDisplay.value = null; memberNext.value = null
  selectedEntry.value = null; viewText.value = ''; viewNext.value = null; viewVersion.value = null
  rawText.value = ''; rawNext.value = null; rawHash.value = null
  cards.value = []; cardsNext.value = null
  previewResult.value = null; previewSelectedIds.value = []
}

async function getJson<T>(path: string, params: Record<string, string | number | null | undefined> = {}): Promise<T> {
  const url = new URL(path, window.location.origin)
  for (const [key, value] of Object.entries(params)) if (value !== undefined && value !== null && value !== '') url.searchParams.set(key, String(value))
  const response = await fetch(url, {credentials: 'same-origin', cache: 'no-store'})
  if (response.status === 401) { discardLoadedData(); authenticated.value = false; throw new Error('会话已结束，请重新登录。') }
  if (response.status === 409) { discardLoadedData(); throw new Error('来源、版本或分页视角已变化，请刷新当前内容。') }
  if (!response.ok) throw Object.assign(new Error(`读取失败（${response.status}）`), {status:response.status})
  return response.json() as Promise<T>
}

function failed(exc: unknown) {
  error.value = exc instanceof Error ? exc.message : '读取失败，请重试。'
}

async function login() {
  if (loginBusy.value) return
  loginBusy.value = true; error.value = ''
  try {
    const response = await fetch('/api/auth/login', {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({username: username.value, password: password.value})})
    if (!response.ok) throw new Error('登录失败，请检查账户。')
    password.value = ''; authenticated.value = true
    await initialize()
  } catch (exc) { failed(exc) } finally { loginBusy.value = false }
}

async function initialize() {
  error.value = ''
  try {
    characters.value = await getJson<Character[]>('/api/memory-v2/calendar/characters')
    if (!characters.value.length) { notice.value = '没有可访问角色。'; return }
    if (!characters.value.some(x => x.id === characterId.value)) characterId.value = characters.value[0].id
    await refreshAll()
  } catch (exc) { failed(exc) }
}

async function changeRole(event: Event) {
  const select=event.target as HTMLSelectElement
  if((editorRef.value?.dirty || importRef.value?.dirty) && !window.confirm('有未保存的修改。放弃后切换角色？')) { select.value=String(characterId.value ?? '');return }
  characterId.value=Number(select.value);editorId.value=null
  await refreshAll()
}
let entryEpoch=0,viewEpoch=0,rawEpoch=0
async function refreshAll() {
  const ticket=++entryEpoch
  if (characterId.value === null) return
  asOf.value = new Date().toISOString()
  activeNode.value = null; nodeTrail.value = []; selectedEntry.value = null
  tree.value = []; treeNext.value = null; status.value = null; cards.value = []; cardsNext.value = null
  digestDisplay.value = null; previewResult.value = null; previewSelectedIds.value = []
  periodOpen.value=false;periodDraft.value=null
  expandedYears.value = []; expandedMonths.value = []
  error.value = ''; notice.value = ''
  // The tree already counts validated members. Load unused search results and
  // full diagnostics only when their respective panels are requested.
  await loadTree()
  if (showSearch.value && ticket===entryEpoch) await searchEntries()
  if (tree.value[0] && ticket===entryEpoch && !showSearch.value) await openNode(tree.value[0])
  if (diagnosticsOpen.value) await loadStatus()
}

async function loadStatus() {
  if (characterId.value === null) return
  try { status.value = await getJson('/api/memory-v2/calendar/status', {characterId: characterId.value}) }
  catch (exc) { failed(exc) }
}

async function loadTree(more = false) {
  if (characterId.value === null || treeLoading.value) return
  treeLoading.value = true
  try {
    const data = await getJson<{nodes: Node[]; nextCursor: string | null; state: string}>(
      '/api/memory-v2/chronicle/tree', {characterId: characterId.value, asOf: asOf.value,
        limit: 20, cursor: more ? treeNext.value : null})
    tree.value = more ? [...tree.value, ...data.nodes] : data.nodes
    treeNext.value = data.nextCursor
    if (!more && navigationYears.value[0]) {
      expandedYears.value = [navigationYears.value[0].key]
      expandedMonths.value = navigationYears.value[0].months[0] ? [navigationYears.value[0].months[0].key] : []
    }
    if (data.state === 'needs_schema') notice.value = '记忆结构尚未安装，请检查启动状态。'
  } catch (exc) { failed(exc); if (more) treeNext.value = null }
  finally { treeLoading.value = false }
}

async function openNode(item: Node, down = false, preserveTrail = false) {
  if (characterId.value === null || nodeLoading.value) return
  const ticket=++entryEpoch
  nodeLoading.value = true; error.value = ''; selectedEntry.value = null
  try {
    const node = await getJson<Node>(`/api/memory-v2/chronicle/nodes/${encodeURIComponent(item.nodeId)}`,
      {characterId: characterId.value, asOf: asOf.value, memberLimit: 50})
    if(ticket!==entryEpoch)return
    if (down && activeNode.value) nodeTrail.value = [...nodeTrail.value, activeNode.value]
    else if (!preserveTrail) nodeTrail.value = []
    if (activeNode.value?.nodeId !== node.nodeId) {
      periodOpen.value = false
      periodDraft.value = null
      periodTask.value = ''
    }
    activeNode.value = node
    digestDisplay.value = null
    memberNext.value = node.nextMemberCursor ?? null
    if (node.summaryStatus === 'available') await loadDigest(node)
    if (ticket===entryEpoch && !showSearch.value && activeNode.value?.nodeId === node.nodeId && node.entryIds.length === 1 && !node.children?.length) await openEntry(node.entryIds[0])
  } catch (exc) { failed(exc) } finally { nodeLoading.value = false }
}

async function upNode() {
  const previous = nodeTrail.value[nodeTrail.value.length - 1]
  if (!previous) return
  nodeTrail.value = nodeTrail.value.slice(0, -1)
  await openNode(previous, false, true)
}

async function loadDigest(node: Node, more = false) {
  if (characterId.value === null || summaryBusy.value) return
  if (more && (!digestDisplay.value || digestDisplay.value.nextOffset === null)) return
  summaryBusy.value = true
  try {
    const page = await getJson<DigestDisplay>(`/api/memory-v2/calendar/nodes/${encodeURIComponent(node.nodeId)}/digest-display`,
      {characterId: characterId.value, asOf: asOf.value, offset: more ? digestDisplay.value?.nextOffset : 0,
        digestVersionId: more ? digestDisplay.value?.versionId : node.digestVersionId, limit: 4096})
    if (more && digestDisplay.value) {
      const previous = digestDisplay.value
      if (previous.versionId !== page.versionId) throw new Error('摘要版本已变化，请刷新。')
      const sections = previous.sections.map(section => ({...section}))
      for (const incoming of page.sections) {
        const existing = sections.find(section => section.ordinal === incoming.ordinal)
        if (existing) { existing.text += incoming.text; existing.incomplete = incoming.incomplete }
        else sections.push(incoming)
      }
      digestDisplay.value = {...page, text: previous.text + page.text, sections}
    } else digestDisplay.value = page
  } catch (exc) { digestDisplay.value = null; failed(exc) }
  finally { summaryBusy.value = false }
}

async function moreSummary() { if (activeNode.value) await loadDigest(activeNode.value, true) }

async function moreMembers() {
  if (!activeNode.value || !memberNext.value || characterId.value === null || nodeLoading.value) return
  nodeLoading.value = true
  try {
    const next = await getJson<Node>(`/api/memory-v2/chronicle/nodes/${encodeURIComponent(activeNode.value.nodeId)}`,
      {characterId: characterId.value, asOf: asOf.value, memberLimit: 50, memberCursor: memberNext.value})
    activeNode.value = {...activeNode.value, entryIds: [...activeNode.value.entryIds, ...next.entryIds],
      entryLabels: [...(activeNode.value.entryLabels || []), ...(next.entryLabels || [])]}
    memberNext.value = next.nextMemberCursor ?? null
  } catch (exc) { failed(exc); memberNext.value = null }
  finally { nodeLoading.value = false }
}

const semanticMetered=ref(false)
const semanticOpen=ref(false), semanticApproved=ref(false), semanticModel=ref(0), semanticBusy=ref(false)
const semanticModels=ref<{id:number;name:string}[]>([])
const semanticItems=ref<{entryId:string;title:string;synopsis:string}[]>([])
const semanticMessage=ref(''), semanticCap=ref(0), emptySearchKey=ref('')
const searchKey=computed(()=>JSON.stringify([characterId.value,asOf.value,query.value,fromDate.value,toDate.value]))
const canSupplement=computed(()=>showSearch.value&&!searchLoading.value&&!cards.value.length
  &&!!query.value.trim()&&emptySearchKey.value===searchKey.value)
let semanticEpoch=0
let ordinarySearchEpoch=0
async function loadGenerationModels(statusPath='/generation-status') {
  const status=await getJson<{enabled:boolean}>('/api/memory-v2/workspace'+statusPath,{})
  if(!status.enabled)return []
  const all=await getJson<any>('/api/models',{})
  return (Array.isArray(all)?all:all.items||[]).filter((m:any)=>m.enabled&&m.protocol==='openai_responses'&&m.model_name==='gpt-6.1-sol')
}
async function openSemantic() {
  if(!canSupplement.value||semanticBusy.value)return
  semanticOpen.value=!semanticOpen.value;semanticApproved.value=false
  if(!semanticOpen.value)return
  semanticMessage.value='';semanticCap.value=0;semanticModels.value=[]
  const key=searchKey.value, epoch=semanticEpoch
  try {
    const status=await getJson<{enabled:boolean;maxMicrousd:number;metered?:boolean;reason?:string}>('/api/memory-v2/workspace/semantic-supplement/status',{})
    const models=status.enabled?await loadGenerationModels('/semantic-supplement/status'):[]
    if(key!==searchKey.value||epoch!==semanticEpoch)return
    if(!models.length){semanticMessage.value=status.reason==='chronicle_daily_rates_verification_required'?'语义补搜核价已到期，当前不发送请求；普通搜索结果保留。':'语义补搜暂未启用或未配置合资格账户，普通搜索结果保留。';return}
    semanticMetered.value=Boolean(status.metered);semanticModels.value=models;semanticModel.value=models[0]?.id||0;semanticCap.value=status.maxMicrousd
  }catch{if(key===searchKey.value&&epoch===semanticEpoch)semanticMessage.value='无法读取语义补搜设置，普通搜索结果保留。'}
}
async function semanticSearch() {
  if(!canSupplement.value||(!semanticMetered.value&&!semanticApproved.value)||!semanticModel.value||!semanticCap.value||semanticBusy.value)return
  const key=searchKey.value, epoch=semanticEpoch
  const controller=new AbortController()
  const timer=window.setTimeout(()=>controller.abort(),65000)
  const current=()=>key===searchKey.value&&epoch===semanticEpoch
  semanticBusy.value=true;semanticApproved.value=false;semanticItems.value=[]
  semanticMessage.value='正在查找候选并判断是否相关…'
  try {
    const response=await fetch('/api/memory-v2/workspace/semantic-supplement',{method:'POST',credentials:'same-origin',
      signal:controller.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify({
        characterId:characterId.value,modelId:semanticModel.value,query:query.value,requestId:crypto.randomUUID(),
        asOf:asOf.value,fromDate:fromDate.value||null,toDate:toDate.value||null,approveMaxMicrousd:semanticCap.value})})
    const result=await response.json()
    if(!current())return
    if(!response.ok){
      semanticMessage.value=result.detail==='semantic_index_unavailable'?'这个范围内暂无可用的语义索引，普通搜索结果保留。'
        :result.detail==='semantic_unknown'?'本次处理结果尚未确认，费用预留保留。普通搜索结果保留。'
        :'语义补搜未完成，普通搜索结果保留。'
      return
    }
    semanticItems.value=result.items
    semanticMessage.value=result.items.length?'已找到相关记忆。':'没有足够相关的记忆。'
  }catch{
    if(current())semanticMessage.value=controller.signal.aborted
      ?'等待超时，普通搜索结果保留。请求可能仍在结算。':'语义补搜未完成，普通搜索结果保留。'
  }finally{clearTimeout(timer);semanticBusy.value=false}
}

const periodOpen=ref(false),periodConsent=ref(false),periodBusy=ref(false),periodReviewConsent=ref(false),periodActivateConsent=ref(false)
const periodDraft=ref<any>(null)
const periodTask=ref('')
const periodModel=ref(0)
const periodModels=ref<{id:number;name:string}[]>([])
const periodFeedback: Record<string, string> = {
  approval_required: '等待授权与预算。', paid_generation_not_enabled: '周期付费加工未开启，暂无可执行的预算授权。',
  period_budget_insufficient: '批准预算不足，尚未发送请求。',
  period_approval_already_queued: '此周期已有任务，请读取当前任务状态。',
  queued: '已授权，等待维护处理。', counting: '正在核对输入和预算。', running: '正在生成。',
  completed: '周期摘要已生成，可读取并确认。', stale: '来源或成员已变化，原结果已失效。',
  unknown: '供应商结果不明，预留仍有效。', stopped: '任务已停止。', cancelled: '计划已撤销。',
  chronicle_approved_model_required: '缺少已配置的模型账户。',
  generation_price_changed: '费率凭据已变化，请核对预算。',
  chronicle_daily_rates_verification_required: '当前费率尚未核验，任务暂未派发。',
}
async function postWorkspace(path:string,body:unknown) {
  const r=await fetch('/api/memory-v2/workspace'+path,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
  if(!r.ok){const {detail}=await r.json().catch(()=>({detail:''}));throw new Error(periodFeedback[detail] || '操作已停止，请核对来源、预算与当前版本。')}
  return await r.json()
}
async function loadPeriodTask() {
  const node = activeNode.value?.nodeId, role = characterId.value
  if(!node || !role)return
  let state
  try {
    state=await getJson<{status:string;versionId?:string;reason?:string}>('/api/memory-v2/workspace/periods/task',{characterId:role,nodeId:node})
  } catch(exc) {
    if((exc as {status?:number})?.status!==404)throw exc
    if(activeNode.value?.nodeId===node && characterId.value===role){
      periodTask.value='当前服务尚未加载周期维护接口。';periodDraft.value=null
      periodModel.value=0;periodModels.value=[]
    }
    return false
  }
  if (activeNode.value?.nodeId!==node || characterId.value!==role)return
  periodTask.value=periodFeedback[state.reason || ''] || periodFeedback[state.status] || '周期任务当前不可执行。'
  if (periodDraft.value?.id!==state.versionId) {
    periodReviewConsent.value=false;periodActivateConsent.value=false
  }
  periodDraft.value=null
  if(state.versionId){
    const draft=await getJson<any>('/api/memory-v2/workspace/period-versions/'+state.versionId,{characterId:role})
    if(activeNode.value?.nodeId===node && characterId.value===role)periodDraft.value=draft
  }
  return true
}
async function openPeriodProcessing() {
  periodOpen.value=true;periodConsent.value=false;periodDraft.value=null;periodTask.value='正在读取周期任务状态…'
  periodModel.value=0;periodModels.value=[]
  const node=activeNode.value?.nodeId, role=characterId.value
  try {
    if(!await loadPeriodTask())return
    const models=await loadGenerationModels()
    if(activeNode.value?.nodeId!==node || characterId.value!==role)return
    periodModels.value=models;periodModel.value=models[0]?.id||0
    if(!models.length && periodTask.value===periodFeedback.approval_required){
      periodTask.value=periodFeedback.chronicle_approved_model_required
    }
  } catch(exc){failed(exc)}
}
async function generatePeriod() {
  if(!periodConsent.value||!activeNode.value||!periodModel.value||periodBusy.value)return
  periodBusy.value=true;error.value=''
  try{
    const nodeId=activeNode.value.nodeId
    await postWorkspace('/periods/plan',{characterId:characterId.value,modelId:periodModel.value,nodeId,asOf:asOf.value,approveMaxMicrousd:122880,queueForMaintenance:true})
    const result=await postWorkspace('/maintain?characterId='+characterId.value+'&nodeId='+encodeURIComponent(nodeId),{})
    await loadPeriodTask()
    if(result.periodResult?.status==='blocked')periodTask.value=periodFeedback[result.periodResult.reason] || '配置尚未就绪，维护未派发。'
    periodConsent.value=false;periodReviewConsent.value=false;periodActivateConsent.value=false
  }catch(exc){failed(exc)}finally{periodBusy.value=false}
}
async function decidePeriod(action:'review'|'activate'){
  if(!periodDraft.value || periodBusy.value || (action==='review'?!periodReviewConsent.value:!periodActivateConsent.value))return
  periodBusy.value=true
  try{periodDraft.value=await postWorkspace('/period-versions/'+periodDraft.value.id+'/'+action,{characterId:characterId.value,inputHash:periodDraft.value.input_hash,outputHash:periodDraft.value.output_hash});notice.value=action==='review'?'已记录本版本的内容确认，尚未启用。':'本版本已启用；每次读取仍校验来源与成员范围。';if(action==='activate'&&activeNode.value)await openNode(activeNode.value)}catch(exc){failed(exc)}finally{periodBusy.value=false}
}

async function searchEntries(more = false) {
  if (characterId.value === null || (more&&searchLoading.value)) return
  const key=searchKey.value
  const run=++ordinarySearchEpoch
  if(!more){
    ++semanticEpoch;emptySearchKey.value='';semanticOpen.value=false;semanticApproved.value=false
    semanticItems.value=[];semanticMessage.value=''
  }
  searchLoading.value = true; error.value = ''
  try {
    const result = await getJson<{items: Card[]; nextCursor: string | null}>('/api/memory-v2/calendar/entries',
      {characterId: characterId.value, asOf: asOf.value, q: query.value,
        fromDate: fromDate.value, toDate: toDate.value, limit: 20,
        cursor: more ? cardsNext.value : null})
    if(run!==ordinarySearchEpoch||key!==searchKey.value)return
    cards.value = more ? [...cards.value, ...result.items] : result.items
    cardsNext.value = result.nextCursor
    if(!more&&!result.items.length)emptySearchKey.value=key
  } catch (exc) {
    if(run===ordinarySearchEpoch&&key===searchKey.value){failed(exc);if(more)cardsNext.value=null}
  }
  finally { if(run===ordinarySearchEpoch)searchLoading.value=false }
}

let searchTimer:ReturnType<typeof setTimeout>|undefined
function applySearch(){
  clearTimeout(searchTimer)
  showSearch.value=true
  void searchEntries()
}
function scheduleSearch(){
  clearTimeout(searchTimer)
  searchTimer=setTimeout(applySearch,350)
}
function clearSearch(){
  clearTimeout(searchTimer)
  query.value='';fromDate.value='';toDate.value='';showSearch.value=false
  void searchEntries()
}
onBeforeUnmount(()=>{clearTimeout(searchTimer);ordinarySearchEpoch++;semanticEpoch++})

async function openEntry(entryId: string) {
  if (characterId.value === null) return
  const ticket=++entryEpoch
  entryLoading.value = true; error.value = ''
  selectedEntry.value = null; viewText.value = ''; rawText.value = '';viewNext.value=null;rawNext.value=null
  try {
    const entry = await getJson<Entry>(`/api/memory-v2/calendar/entries/${encodeURIComponent(entryId)}`,
      {characterId: characterId.value})
    if(ticket!==entryEpoch)return
    selectedEntry.value=entry
    activeView.value = 'NARRATIVE'
    if(selectedEntry.value.views.NARRATIVE.status === 'available') await loadView()
    else viewText.value='精修后的对话记录尚未完成或尚未确认。梗概不会替代全文。'
  } catch (exc) { if(ticket===entryEpoch)failed(exc) } finally { if(ticket===entryEpoch)entryLoading.value=false }
}

async function chooseView(view: 'RAW' | 'CLEANED' | 'NARRATIVE') {
  if (!selectedEntry.value || selectedEntry.value.views[view].status !== 'available') return
  ++viewEpoch;++rawEpoch;viewBusy.value=false;rawBusy.value=false
  activeView.value = view
  if(view === 'RAW') await loadRaw(0)
  else await loadView()
}

async function loadView(more = false) {
  if (!selectedEntry.value || characterId.value === null || (more && viewBusy.value)) return
  const ticket=++viewEpoch,entryTicket=entryEpoch
  const current=()=>ticket===viewEpoch && entryTicket===entryEpoch
  if (activeView.value === 'RAW') { viewText.value = ''; return }
  viewBusy.value = true
  try {
    const page = await getJson<TextPage>(`/api/memory-v2/calendar/entries/${encodeURIComponent(selectedEntry.value.entryId)}/views/${activeView.value}`,
      {characterId: characterId.value, offset: more ? viewNext.value : 0,
        versionId: more ? viewVersion.value : null, limit: 4096})
    if(!current())return
    viewText.value = more ? viewText.value + page.text : page.text
    viewNext.value = page.nextOffset; viewVersion.value = page.versionId ?? null
  } catch (exc) { if(!current())return;viewText.value = ''; viewNext.value = null; failed(exc) }
  finally { if(ticket===viewEpoch)viewBusy.value = false }
}

async function loadRaw(ordinal: number, more = false) {
  if (!selectedEntry.value || characterId.value === null || (more && rawBusy.value)) return
  const ticket=++rawEpoch,entryTicket=entryEpoch
  const current=()=>ticket===rawEpoch && entryTicket===entryEpoch
  rawBusy.value = true; error.value = ''
  try {
    const page = await getJson<TextPage>(`/api/memory-v2/chronicle/entries/${encodeURIComponent(selectedEntry.value.entryId)}/raw/${ordinal}`,
      {characterId: characterId.value, offset: more ? rawNext.value : 0,
        contentHash: more ? rawHash.value : null, limit: 4096})
    if(!current())return
    rawOrdinal.value = ordinal
    rawText.value = more ? rawText.value + page.text : page.text
    rawNext.value = page.nextOffset; rawHash.value = page.contentHash ?? null
    rawRole.value = page.sourceRole ?? ''
  } catch (exc) { if(!current())return;rawText.value = ''; rawNext.value = null; failed(exc) }
  finally { if(ticket===rawEpoch)rawBusy.value = false }
}

const moreTextButton=ref<HTMLButtonElement|null>(null)
watch(moreTextButton,(element,_,cleanup)=>{
  if(!element || typeof IntersectionObserver==='undefined')return
  const observer=new IntersectionObserver(entries=>{
    if(entries.some(entry=>entry.isIntersecting) && !viewBusy.value && viewNext.value!==null)void loadView(true)
  })
  observer.observe(element);cleanup(()=>observer.disconnect())
},{flush:'post'})

function navigate(delta: number) {
  const next = navIds.value[currentIndex.value + delta]
  if (next) void openEntry(next)
}

async function previewProjection() {
  if (characterId.value === null || !previewSelectedIds.value.length || previewBusy.value) return
  previewBusy.value = true; previewError.value = ''; previewResult.value = null
  try {
    const payload = {characterId: characterId.value, entryIds: previewSelectedIds.value,
      asOf: previewTimeIso(), offsetMinutes: 480, budgetTokens: previewBudget.value,
      maxBlocks: previewMaxBlocks.value}
    const response = await fetch('/api/memory-v2/calendar/projection-preview', {method: 'POST',
      credentials: 'same-origin', cache: 'no-store', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)})
    if (response.status === 401) { discardLoadedData(); authenticated.value = false; throw new Error('会话已结束，请重新登录。') }
    if (response.status === 409) { discardLoadedData(); throw new Error('来源或投影视角已变化，请刷新。') }
    if (!response.ok) throw new Error(`投影预览失败（${response.status}）`)
    const result = await response.json() as ProjectionResult
    if (result.selectionDiagnostics.some(item => item.eligibility !== 'current_validated')) {
      tree.value = []; activeNode.value = null; nodeTrail.value = []; selectedEntry.value = null
      cards.value = []; digestDisplay.value = null; rawText.value = ''; viewText.value = ''
    }
    previewResult.value = result
  } catch (exc) { previewError.value = exc instanceof Error ? exc.message : '投影预览失败。' }
  finally { previewBusy.value = false }
}

onMounted(async () => {
  try {
    const response = await getJson<{authenticated: boolean}>('/api/auth/status')
    authenticated.value = response.authenticated;authReady.value=true
    if (authenticated.value) await initialize()
  } catch (exc) { failed(exc) } finally { authReady.value = true }
})
</script>

<template>
  <div class="chronicle" :class="`chronicle--${theme}`">
    <div v-if="!authReady" class="chronicle-splash"><LoaderCircle class="spin" :size="28" /> 正在打开记忆年历…</div>
    <div v-else-if="!authenticated" class="chronicle-login-wrap">
      <form class="chronicle-login" @submit.prevent="login">
        <div class="chronicle-logo">✦</div><p class="eyebrow">YOSAKI · CHRONICLE</p>
        <h1>溯时流源年历</h1><p>登录后浏览梗概、完整精修记录，按需核对原始对话。</p>
        <label>账号<input v-model="username" autocomplete="username" /></label>
        <label>密码<input v-model="password" type="password" autocomplete="current-password" /></label>
        <p v-if="error" class="chronicle-alert">{{ error }}</p>
        <button class="primary" :disabled="loginBusy"><LogIn :size="16" /> {{ loginBusy ? '正在登录…' : '进入年历' }}</button>
        <a href="/hub">返回工作台</a>
      </form>
    </div>
    <template v-else>
      <header class="chronicle-header">
        <div class="chronicle-brand"><span class="chronicle-logo"><Layers3 :size="24"/></span><div><small>YOSAKI / MEMORY V2</small><strong>溯时流源 <em>Chronicle</em></strong></div></div>
        <div class="chronicle-head-actions">
          <span class="chronicle-isolation"><ShieldCheck :size="14" /> Chronicle 记忆</span>
          <button class="text-button" :aria-label="showImport ? '关闭对话导入' : '打开对话导入'" @click="toggleImport"><FileText :size="17" /> 导入与分段阅读</button>
          <select :value="characterId" aria-label="选择角色" @change="changeRole"><option v-for="person in characters" :key="person.id" :value="person.id">{{ person.name }}</option></select>
          <button class="icon-btn" aria-label="切换明暗模式" @click="toggleTheme"><Sun v-if="theme === 'dark'" :size="17" /><Moon v-else :size="17" /></button>
          <button class="icon-btn" aria-label="刷新年历" @click="refreshAll"><RefreshCw :size="17" /></button>
          <a class="chronicle-exit" href="/hub"><X :size="16" /> 关闭年历</a>
        </div>
      </header>
      <div v-if="error" class="chronicle-message error"><CircleAlert :size="17" /> {{ error }} <button @click="refreshAll">刷新</button></div>
      <div v-else-if="notice" class="chronicle-message"><CircleAlert :size="17" /> {{ notice }}</div>
      <main class="chronicle-layout">
        <aside class="chronicle-rail">
          <div class="rail-heading"><div><p class="eyebrow">TIME ATLAS</p><h2>时间年历</h2></div><span>{{ availableCount }} 条</span></div>
          <div class="rail-search"><Search :size="15" /><input v-model="query" placeholder="搜索标题、关键词、梗概" @input="!($event as InputEvent).isComposing && scheduleSearch()" @compositionend="scheduleSearch" @keyup.enter="!$event.isComposing && applySearch()" aria-label="关键词搜索" /><button aria-label="执行搜索" :aria-busy="searchLoading" @click="applySearch"><Search :size="16" />搜索</button></div>
          <div class="date-filters"><label>起始日期<input v-model="fromDate" @change="scheduleSearch" type="date" /></label><label>结束日期<input v-model="toDate" @change="scheduleSearch" type="date" /></label></div>
          <div class="rail-filter-actions"><span aria-live="polite">{{searchLoading ? '正在检索…' : '关键词和日期自动筛选'}}</span><button @click="clearSearch">清除</button></div>
          <div v-if="showSearch" class="rail-section">
            <div class="rail-section-title"><Search :size="14" /> 筛选结果 <span>{{ cards.length }}</span></div>
            <p v-if="searchLoading" class="rail-muted">正在检索…</p><p v-else-if="!cards.length" class="rail-muted">{{ canSupplement ? '普通搜索没有结果。' : '没有符合条件的可用经历。' }}</p>
            <button v-if="canSupplement" class="text-button" :disabled="semanticBusy" :aria-expanded="semanticOpen" @click="openSemantic">语义补搜</button>
            <section v-if="canSupplement && semanticOpen" :aria-busy="semanticBusy">
              <template v-if="semanticCap">
                <p>向 OpenAI 发送当前查询及最多三条已确认记忆的标题和梗概。不发送原始对话。{{semanticMetered ? "按实际用量计费。" : "单次最多 USD "+(semanticCap/1000000).toFixed(3)+"。"}}</p>
                <select v-model="semanticModel" :disabled="semanticBusy" aria-label="语义补搜账户"><option v-for="m in semanticModels" :key="m.id" :value="m.id">{{m.name}}</option></select>
                <label v-if="!semanticMetered"><input type="checkbox" v-model="semanticApproved" :disabled="semanticBusy" />同意本次发送和费用</label>
                <button :disabled="(!semanticMetered && !semanticApproved) || semanticBusy || !semanticModel" @click="semanticSearch">开始补搜</button>
              </template>
              <p v-if="semanticMessage" role="status">{{ semanticMessage }}</p>
              <div v-for="item in semanticItems" :key="item.entryId" class="search-entry">
                <button class="entry-row" @click="openEntry(item.entryId)"><span>{{item.title}}</span><small>语义补搜</small></button>
                <p>{{item.synopsis}}</p>
              </div>
            </section>
            <div v-for="card in cards" :key="card.entryId" class="search-entry"><button class="entry-row" :class="{selected: selectedEntry?.entryId === card.entryId}" @click="openEntry(card.entryId)"><span>{{ card.title }}</span><small>{{ card.occurredAt?.[0]?.slice(0,10) || '时间未核实' }}</small></button><button class="preview-pick" :aria-label="`${previewSelectedIds.includes(card.entryId) ? '移出' : '选入'}投影预览：${card.title}`" @click="togglePreview(card.entryId)">{{ previewSelectedIds.includes(card.entryId) ? '已选' : '选入' }}</button></div>
            <button v-if="cardsNext" class="text-button" :disabled="searchLoading" @click="searchEntries(true)">加载更多结果 <ChevronDown :size="13" /></button>
          </div>
          <div class="rail-section"><div class="rail-section-title"><CalendarDays :size="15" /> 按时间浏览</div>
            <p class="rail-muted time-guide">近期经历按周、月查看，较早经历按季度、半年或全年汇总。各时段按经历日期归属。</p>
            <p v-if="treeLoading" class="rail-muted">正在读取时段…</p>
            <p v-else-if="!tree.length" class="rail-muted">暂无可浏览的经历。</p>
            <div v-for="year in navigationYears" :key="year.key" class="nav-year"><button class="nav-parent" :aria-expanded="expandedYears.includes(year.key)" @click="toggleYear(year.key)"><ChevronDown v-if="expandedYears.includes(year.key)" :size="14" /><ChevronRight v-else :size="14" /> {{ year.label }} <small>{{ year.months.reduce((count, month) => count + month.nodes.length, 0) }} 时段</small></button>
              <div v-if="expandedYears.includes(year.key)" class="nav-months"><div v-for="month in year.months" :key="month.key"><button class="nav-parent nav-month" :aria-expanded="expandedMonths.includes(month.key)" @click="toggleMonth(month.key)"><ChevronDown v-if="expandedMonths.includes(month.key)" :size="13" /><ChevronRight v-else :size="13" /> {{ month.label }} <small>{{ month.nodes.length }}</small></button>
                <div v-if="expandedMonths.includes(month.key)" class="nav-periods"><button v-for="node in month.nodes" :key="node.nodeId" class="nav-period" :class="{selected: activeNode?.nodeId === node.nodeId}" @click="showSearch = false; openNode(node)"><span><strong>{{ periodLabel(node) }}</strong><small>{{ node.memberCount }} 条经历</small><small v-if="node.memberCount === 1 && node.title !== node.period">{{ node.title }}</small></span></button></div>
              </div></div>
            </div>
            <button v-if="treeNext" class="text-button" :disabled="treeLoading" @click="loadTree(true)">加载更多时段 <ChevronDown :size="13" /></button>
          </div>
          <details class="diagnostics-panel"><summary>开发详情：阅读层级</summary>
            <p>近期更详细，久远按周期概括。阅读层级随经历年龄变化，季度仍按 1—3、4—6、7—9、10—12 月划分。</p>
            <button v-for="node in tree" :key="node.nodeId" class="tree-row" :class="{selected: activeNode?.nodeId === node.nodeId}" @click="showSearch = false; openNode(node)"><span class="level-marker">{{ node.level || '—' }}</span><span class="tree-row-copy"><strong>{{ node.title }}</strong><small>{{ node.level ? levelNames[node.level] : '时间未核实' }} · {{ periodLabel(node) }} · {{ node.memberCount }} 条经历</small></span><ChevronRight :size="15" /></button>
            <p v-if="!treeLoading && !tree.length" class="rail-muted">暂无可浏览的经历。</p>
          </details>
          <details class="diagnostics-panel" @toggle="diagnosticsOpen=($event.currentTarget as HTMLDetailsElement).open;if(diagnosticsOpen)loadStatus()"><summary><CircleAlert :size="14" /> 状态与诊断 <span v-if="status?.excludedStale">{{ status.excludedStale }} 条失效</span></summary><p>当前可用经历：{{ status?.available ?? '—' }}</p><p>已安全过滤的失效来源：{{ status?.excludedStale ?? '—' }}</p><p>索引状态：{{ status?.state || '读取中' }}</p><small>正在查看的内容发生变化时会显著报错并清除旧正文。</small></details>
          <div class="rail-footnote">打开时段后可逐层查看经历，展开到完整精修记录。</div>
        </aside>
        <section class="chronicle-main">
          <div class="chronicle-page-kicker"><CalendarDays :size="16" /> {{ currentCharacter?.name || '角色' }}的记忆年历 <span>·</span> {{ asOf.slice(0,10) }} 视角</div>
          <ChronicleImportWizard ref="importRef" :review-busy="reviewPanel?.busy" @review="openBatchReview" @revised="revised" @changed="draftsChanged" :key="characterId ?? 0" v-if="showImport && characterId !== null" :character-id="characterId" />
          <ChronicleBatchReview id="chronicle-review" ref="reviewPanel" v-if="characterId" :key="characterId" :cid="characterId" :changes="reviewChanges" :revision="reviewRevision" :dirty="!!editorRef?.dirty || !!importRef?.dirty" @published="if(!editorRef?.dirty)editorId=null;refreshAll();loadProgressiveDrafts()" />
          <details :key="characterId ?? 0" class="projection-diagnostics" @toggle="($event.currentTarget as HTMLDetailsElement).open && loadProgressiveDrafts()"><summary>待补齐的经历草稿</summary>
            <p>仅用于整理与查看；待审字段不会进入记忆投影。</p>
            <button class="text-button" :disabled="draftBusy" @click="loadProgressiveDrafts()">{{ draftBusy ? '正在校验来源…' : '刷新草稿' }}</button>
            <p v-if="draftError">{{ draftError }}</p>
            <template v-if="draftCharacterId === characterId">
              <article v-for="draft in progressiveDrafts" :key="draft.entryId">
                <strong>{{ draft.fields.title?.value || '标题不可用' }} · 待整理</strong>
                <p>{{ draft.sourceRefs.length }} 条有序来源 · 梗概：{{ draft.fields.synopsis?.completionStatus === 'missing' ? '待补齐' : draft.fields.synopsis?.lifecycleStatus === 'available' ? '已核验' : '待核验或失效' }}</p>
                <button @click="editDraft(draft.entryId)">打开并整理</button>
              </article>
              <button v-if="draftNext !== null" class="text-button" :disabled="draftBusy" @click="loadProgressiveDrafts(true)">下一页草稿</button>
            </template>
          </details>
          <ChronicleEditor v-if="editorId && characterId" ref="editorRef" :key="editorId" :character-id="characterId" :entry-id="editorId" @removed="editorId=null;draftsChanged()" @saved="draftsChanged" @revised="revised" />
          <ChroniclePeriodSummaries @open-entry="openEntry" v-if="characterId" :key="characterId" :cid="characterId" :period="activeNode?.period" :revision="asOf" />
          <div v-if="activeNode" class="period-card">
            <div class="period-heading"><div><div class="period-crumbs"><button v-if="nodeTrail.length" @click="upNode"><ArrowLeft :size="14" /> 上一级</button><span v-else>正在浏览的经历</span></div><h1>{{ activeNode.period ? periodLabel(activeNode) : activeNode.title }}</h1><p>{{ activeNode.memberCount }} 条经历 · 按{{ activeNode.level ? levelNames[activeNode.level] : '未核实日期' }}查看</p></div></div>
            <div class="period-status"><span :class="`summary-${activeNode.summaryStatus}`"><BookOpen :size="14" /> {{ summaryNames[activeNode.summaryStatus] || activeNode.summaryStatus }}</span><span v-if="activeNode.taskStatus" class="task-status"><Clock3 :size="14" /> 任务：{{ activeNode.taskStatus }}</span><span v-if="activeNode.timeBasis !== 'occurred_at'">时间依据：{{ activeNode.timeBasis === 'occurred_day' ? '来源日历日期' : activeNode.timeBasis === 'recorded_at' ? '记录时间' : '未核实' }}</span></div>
            <div v-if="digestDisplay && digestDisplay.mode !== 'unavailable'" class="period-digest"><div class="section-label">周期 DIGEST <small>{{ digestDisplay.label }} · 已审核派生{{ digestDisplay.mode === 'extractive' ? ' · 非语义概括' : '' }}</small></div>
              <template v-if="digestDisplay.mode === 'extractive'"><div v-for="section in digestDisplay.sections" :key="section.ordinal" class="digest-section"><p>{{ section.text }}</p><details class="source-details"><summary>来源 {{ section.ordinal + 1 }} · {{ sourceRoleLabel(digestDisplay.sourceMeta[section.ordinal]?.role || '') }} <span v-if="section.incomplete">· 分页节选</span></summary><dl><dt>来源 ID</dt><dd>{{ digestDisplay.sourceMeta[section.ordinal]?.sourceRef.kind }}:{{ digestDisplay.sourceMeta[section.ordinal]?.sourceRef.id }}</dd><dt>原始角色</dt><dd>{{ digestDisplay.sourceMeta[section.ordinal]?.role }}</dd><dt>来源状态</dt><dd>{{ digestDisplay.sourceMeta[section.ordinal]?.status }}</dd><dt>证据类型</dt><dd>{{ digestDisplay.sourceMeta[section.ordinal]?.evidenceKind }}</dd></dl></details></div></template>
              <template v-else><p>{{ digestDisplay.text }}</p><details class="source-details"><summary>展开 {{ digestDisplay.sourceMeta.length }} 条来源元数据</summary><dl v-for="source in digestDisplay.sourceMeta" :key="source.ordinal"><dt>来源 {{ source.ordinal + 1 }}</dt><dd>{{ source.sourceRef.kind }}:{{ source.sourceRef.id }} · {{ source.role }} · {{ source.status }}</dd></dl></details></template>
              <button v-if="digestDisplay.nextOffset !== null" class="text-button" :disabled="summaryBusy" @click="moreSummary">继续读取摘要 <ChevronDown :size="13" /></button></div>
            <p v-else-if="summaryBusy" class="period-fallback">正在校验周期摘要…</p>
            <p v-else-if="activeNode.level && ['L1','L2','L3','L4'].includes(activeNode.level)" class="period-fallback">此处显示另行审核的周期版本。自动生成的周、月、季度、半年和年度回顾见上方「周期摘要」。</p>
            <button v-if="activeNode.level && ['L1','L2','L3','L4'].includes(activeNode.level)" class="text-button" @click="openPeriodProcessing">另行生成待审核版本</button>
            <section v-if="periodOpen" class="period-processing" :aria-busy="periodBusy"><p role="status">{{ periodTask }}</p><button class="text-button" :disabled="periodBusy" @click="loadPeriodTask().catch(failed)">刷新周期任务状态</button><p>只使用当前有资格的完整成员及直接下级摘要；缺失下级时停止，不自动降级。一次计数和一次生成，上限 USD 0.12288；超出单次容量明确拒绝。生成后仍需分别确认内容与启用。</p><select v-model="periodModel" aria-label="周期摘要模型"><option v-for="m in periodModels" :key="m.id" :value="m.id">{{m.name}}</option></select><label><input type="checkbox" v-model="periodConsent" />同意本次把上述周期材料发给 OpenAI 并承担限定费用</label><button :disabled="!periodConsent || periodBusy || !periodModel || !!periodDraft" @click="generatePeriod">{{periodBusy ? '正在处理…' : '授权一次维护生成'}}</button><article v-if="periodDraft"><h3>待审周期摘要</h3><div style="white-space:pre-wrap;line-height:1.9;max-width:860px">{{periodDraft.body}}</div><template v-if="periodDraft.review_status!=='human_checked'"><label><input type="checkbox" v-model="periodReviewConsent" />我已对照本周期的完整成员，确认内容</label><button :disabled="!periodReviewConsent || periodBusy" @click="decidePeriod('review')">确认这份周期摘要</button></template><template v-else-if="periodDraft.status!=='active'"><label><input type="checkbox" v-model="periodActivateConsent" />启用这份已确认的摘要</label><button :disabled="!periodActivateConsent || periodBusy" @click="decidePeriod('activate')">启用这个版本</button></template></article></section>
            <div v-if="activeNode.children?.length" class="child-grid"><button v-for="child in activeNode.children" :key="child.nodeId" class="child-card" @click="openNode(child, true)"><strong>{{ periodLabel(child) }}</strong><small>{{ child.memberCount }} 条经历 · {{ summaryNames[child.summaryStatus] || '可展开' }}</small><ArrowRight :size="16" /></button></div>
            <div v-if="activeNode.entryIds.length" class="period-entries"><div class="section-label">本时段经历 <small>{{ activeNode.memberCount }} 条</small></div><div class="period-entry-list"><div v-for="(id, index) in activeNode.entryIds" :key="id" class="period-entry-row"><button class="entry-open" :class="{selected: selectedEntry?.entryId === id}" @click="openEntry(id)"><span>{{ String(index+1).padStart(2,'0') }}</span> {{ entryTitle(id) }} <ArrowRight :size="14" /></button><button class="preview-pick" :aria-label="`${previewSelectedIds.includes(id) ? '移出' : '选入'}投影预览：${entryTitle(id)}`" @click="togglePreview(id)">{{ previewSelectedIds.includes(id) ? '已选' : '选入预览' }}</button></div></div><button v-if="memberNext" class="text-button" @click="moreMembers">加载更多经历 <ChevronDown :size="13" /></button></div>
          </div>
          <div v-else class="period-card empty-period"><BookOpen :size="32" /><h1>选择一段时间</h1><p>从左侧时间树开始，逐层展开到完整精修记忆。</p></div>
          <article v-if="selectedEntry" class="experience-card">
            <div class="experience-top"><div><p class="eyebrow">EXPERIENCE / {{ dateLabel }}</p><h2>{{ selectedEntry.title }}</h2><div class="tag-list"><span v-for="tag in selectedEntry.keywords" :key="`k-${tag}`">#{{ tag }}</span><span v-for="tag in selectedEntry.tags" :key="`t-${tag}`" class="tag-muted">{{ tag }}</span></div></div><div class="entry-navigation"><button :disabled="!canPrevious" title="上一条经历" @click="navigate(-1)"><ArrowLeft :size="17" /></button><button :disabled="!canNext" title="下一条经历" @click="navigate(1)"><ArrowRight :size="17" /></button><button class="preview-pick" @click="togglePreview(selectedEntry.entryId)">{{ previewSelectedIds.includes(selectedEntry.entryId) ? '移出预览' : '选入预览' }}</button></div></div>
            <div class="experience-synopsis"><span class="section-label">单经历梗概</span><p>{{ selectedEntry.synopsis || '梗概尚未完成或当前不可用' }}</p></div>
            <p v-if="navIds.length" class="projection-note">当前第 {{currentIndex >= 0 ? currentIndex+1 : '—'}} / {{navIds.length}} 条<template v-if="!canPrevious"> · 没有上一条</template><template v-if="!canNext"> · 没有下一条</template>。</p>
            <div class="view-header"><div class="view-tabs"><button v-for="view in (['NARRATIVE','CLEANED','RAW'] as const)" :key="view" :class="{active: activeView === view}" :disabled="selectedEntry.views[view].status !== 'available'" @click="chooseView(view)">{{ {NARRATIVE:'精修后的对话记录',RAW:'查看原始对话',CLEANED:'旧清理视图'}[view] }}<small v-if="selectedEntry.views[view].status !== 'available'">{{ selectedEntry.views[view].status === 'needs_review' ? '待审核' : '不可用' }}</small></button></div><span>有序来源 {{ selectedEntry.sourceRefs.length }} 条 · {{ selectedEntry.timeBasis === 'occurred' ? '发生时间' : '时间待核实' }}</span></div>
            <div class="compare-grid" :class="{'raw-only': activeView === 'RAW'}">
              <section v-if="activeView !== 'RAW'" class="compare-pane"><div class="compare-pane-title"><FileText :size="15" /> {{ activeView === 'NARRATIVE' ? '精修后的对话记录' : '保守清理正文' }} <small>已审核派生</small></div><div class="reading-text">{{ viewText }}</div><button ref="moreTextButton" :key="viewNext" v-if="viewNext !== null" class="text-button" :disabled="viewBusy" @click="loadView(true)">{{viewBusy ? '正在读取…' : '继续读取'}} <ChevronDown :size="13" /></button></section>
              <section v-if="activeView === 'RAW'" class="compare-pane raw-pane"><div class="compare-pane-title"><BookOpen :size="15" /> RAW 原始证据 <small>权威来源 · 第 {{ rawOrdinal + 1 }} / {{ selectedEntry.sourceRefs.length }} 条</small></div><div class="source-switch"><button v-for="(source, index) in selectedEntry.sourceRefs" :key="`${source.kind}-${source.id}`" :class="{active: rawOrdinal === index}" @click="loadRaw(index)">{{ index + 1 }} · {{ source.kind === 'message' ? '消息' : '活动操作' }}</button></div><div class="source-meta">{{ rawRole === 'assistant' ? '阿夜的模型发言' : rawRole === 'user' ? '用户发言' : '活动证据' }} · {{ selectedEntry.sourceRefs[rawOrdinal]?.kind }}:{{ selectedEntry.sourceRefs[rawOrdinal]?.id }}</div><div class="reading-text">{{ rawText }}</div><button v-if="rawNext !== null" class="text-button" :disabled="rawBusy" @click="loadRaw(rawOrdinal, true)">{{rawBusy ? '正在读取…' : '继续读取原始对话'}} <ChevronDown :size="13" /></button></section>
            </div>
          </article>
          <div v-else-if="entryLoading" class="entry-placeholder"><LoaderCircle class="spin" :size="20" /> 正在校验条目与来源…</div>
          <div v-else class="entry-placeholder"><ChevronUp :size="18" /> 选择上方的一条经历，查看纪事与原文对照。</div>
          <details class="projection-card" aria-label="记忆投影预览"><summary>开发详情：时间投影检查</summary>
            <div class="projection-heading"><div><p class="eyebrow">SHADOW PROJECTION</p><h2>记忆投影预览</h2></div><span class="projection-badge">只读模拟</span></div>
            <p class="projection-note">只处理你明确选入的经历。这里展示 P2 可能形成的参考文本；当前模型并未读取，也不会写入冷却或触发工具。</p>
            <div class="preview-selection"><span v-if="!previewSelectedIds.length">先从时段或搜索结果中选择经历。</span><button v-for="id in previewSelectedIds" :key="id" @click="togglePreview(id)">{{ entryTitle(id) }} <X :size="12" /></button></div>
            <div class="preview-controls"><label>模拟观察时间 · UTC+8<input v-model="previewLocalTime" type="datetime-local" @input="previewResult = null" /></label><label>Token 估算预算<input v-model.number="previewBudget" type="number" min="0" max="4096" @input="previewResult = null" /></label><label>最多投影块<input v-model.number="previewMaxBlocks" type="number" min="1" max="20" @input="previewResult = null" /></label><button class="projection-run" :disabled="previewBusy || !previewSelectedIds.length" @click="previewProjection"><LoaderCircle v-if="previewBusy" class="spin" :size="15" /><Layers3 v-else :size="15" /> {{ previewBusy ? '正在校验…' : '生成只读预览' }}</button></div>
            <p v-if="previewError" class="preview-error" role="alert">{{ previewError }}</p>
            <template v-if="previewResult"><div class="preview-stats"><span>显式选择 {{ previewResult.selectedCount }} 条</span><span>独立来源经历 {{ previewResult.uniqueExperienceCount }} 组</span><span>实际投影 {{ previewResult.blocks.length }} 块</span><span>估算 {{ previewResult.estimatedTokens }} Token</span></div>
              <p class="projection-note">此旧管理预览不代表实际模型输入。阿夜记忆通道只返回具备资格的精修记录、梗概、周期摘要或标题，不回填原始对话。</p>
              <div v-for="(block, index) in previewResult.blocks" :key="`${block.projectionKind}-${index}`" class="projection-block"><div class="projection-block-head"><strong>{{ projectionLabel(block.projectionKind) }}</strong><span>{{ block.level || '未定时间' }} · {{ block.period || '时间未核实' }} · 约 {{ block.estimatedTokens }} Token</span></div><p class="projection-text">{{ block.text }}</p><div class="projection-detail">资格：当前来源已校验 · 摘要：{{ summaryNames[block.summaryStatus] || block.summaryStatus }} · 正式召回不可用</div><details class="source-details"><summary>展开版本与 {{ block.sourceRefs.length }} 条原始来源映射</summary><p v-for="version in block.versionRefs" :key="version.id">{{ version.kind }} 版本：<code>{{ version.id }}</code></p><p v-for="source in block.sourceRefs" :key="`${source.kind}-${source.id}`">{{ source.kind }}:<code>{{ source.id }}</code></p></details></div>
              <div v-if="previewResult.rejected.length" class="projection-rejected"><strong>未投影的候选</strong><p v-for="item in previewResult.rejected" :key="`${item.entryId}-${item.reason}`">{{ item.entryId.slice(0, 8) }} · {{ rejectionLabel(item.reason) }}</p></div>
              <details class="projection-diagnostics"><summary>查看逐条资格与可用文本视图</summary><div v-for="(item, index) in previewResult.selectionDiagnostics" :key="`${item.entryId}-${index}`"><strong>{{ item.eligibility === 'current_validated' ? entryTitle(item.entryId) : '来源或权限已失效' }}</strong><span>{{ item.eligibility === 'current_validated' ? `${item.level || '未定'} · ${summaryNames[item.summaryStatus || ''] || item.summaryStatus} · ${item.availableViews?.join(' / ')}` : '不返回旧正文' }}</span></div></details>
            </template>
          </details>
          <footer>CHRONICLE · 来源、审核与权限在每次读取时重新核验。保存、确认与发布各有明确操作。</footer>
        </section>
      </main>
    </template>
  </div>
</template>
