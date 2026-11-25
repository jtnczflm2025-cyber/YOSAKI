<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, useId } from 'vue'
import { buildFilterSvg, generateDisplacementBlobUrl } from './glassRefraction'

const props = withDefaults(defineProps<{ level?: 'hero' | 'navigation' | 'reading'; refract?: boolean }>(), { level: 'navigation', refract: false })
const root = ref<HTMLElement>()
const filter = ref('')
const filterId = ref('')
const id = useId().replace(/[^a-zA-Z0-9_-]/g, '')
const style = computed(() => filterId.value ? { backdropFilter: `url(#${filterId.value}) blur(1px)` } : {})
let observer: ResizeObserver | undefined
let timer: ReturnType<typeof setTimeout> | undefined
let blob: string | undefined
let revision = 0
let media: MediaQueryList | undefined
function clearFilter() {
  revision++
  filter.value = ''; filterId.value = ''
  if (blob) URL.revokeObjectURL(blob)
  blob = undefined
}
function schedule() {
  clearTimeout(timer)
  if (media?.matches || !props.refract || props.level === 'reading') { clearFilter(); return }
  timer = setTimeout(async () => {
    const el = root.value
    if (!el) return
    const box = el.getBoundingClientRect()
    if (box.width < 2 || box.height < 2) return
    const ticket = ++revision
    let next: string | undefined
    try {
      const width = Math.round(box.width), height = Math.round(box.height)
      next = await generateDisplacementBlobUrl({ width, height, radius: 20, bevelDepth: 12, dpr: 1 })
      if (ticket !== revision) { URL.revokeObjectURL(next); return }
      const nextId = `glass-${id}-${ticket}`
      filter.value = buildFilterSvg({ id: nextId, mapUrl: next, width, height, scale: 16, aberration: 1.2, frost: .5 })
      filterId.value = nextId
      if (blob) URL.revokeObjectURL(blob)
      blob = next
    } catch {
      if (next) URL.revokeObjectURL(next)
      if (ticket === revision) clearFilter()
    }
  }, 160)
}
onMounted(() => {
  media = matchMedia('(prefers-reduced-transparency: reduce)')
  media.addEventListener('change', schedule)
  // CSS.supports alone accepts the syntax on engines that cannot render the filter.
  if (!/Chrome|Chromium|Edg/.test(navigator.userAgent) || !CSS.supports('backdrop-filter', 'url(#glass)')) return
  observer = new ResizeObserver(schedule)
  if (root.value) observer.observe(root.value)
})
onBeforeUnmount(() => {
  observer?.disconnect(); clearTimeout(timer); media?.removeEventListener('change', schedule); clearFilter()
})
</script>
<template>
  <div ref="root" class="glass-surface" :class="`glass-${level}`">
    <span v-if="filter" class="glass-filter-def" aria-hidden="true" v-html="filter"></span>
    <span class="glass-optics" aria-hidden="true" :style="style"></span>
    <div class="glass-content"><slot/></div>
  </div>
</template>
