<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Pause, Play, RotateCcw } from 'lucide-vue-next'
const props = withDefaults(defineProps<{ active?: boolean }>(), { active: true })
const host = ref<HTMLElement>()
const ready = ref(false), failed = ref(false), paused = ref(false), reduced = ref(false)
let scene: Awaited<ReturnType<typeof import('./globeScene').createGlobe>> | undefined
const abort = new AbortController()
let frame = 0, last = 0, interacted = 0, intersecting = true, movingUntil = 0
let observer: ResizeObserver | undefined, intersection: IntersectionObserver | undefined, preference: MediaQueryList | undefined
let down: { x: number; y: number } | undefined, dragged = false
function interact() { interacted = performance.now(); movingUntil = interacted + 2000; kick() }
function animate(now: number) {
  frame = 0
  if (!props.active || document.hidden || !intersecting || !scene || failed.value) return
  const auto = !paused.value && !reduced.value && now - interacted > 6000
  scene.render(Math.min((now - last) / 1000 || 0, .05), auto, reduced.value); last = now
  if (auto || down || (!reduced.value && (now < movingUntil || !paused.value))) frame = requestAnimationFrame(animate)
}
function kick() { if (!frame && scene && !failed.value) frame = requestAnimationFrame(animate) }
function visibility() { if (document.hidden) { cancelAnimationFrame(frame); frame = 0 } else { last = performance.now(); kick() } }
function mediaChange() { reduced.value = !!preference?.matches; kick() }
function reset() { scene?.controls.reset(); interacted = performance.now(); movingUntil = interacted + 1000; kick() }
function pointerDown(e: PointerEvent) { down = { x: e.clientX, y: e.clientY }; dragged = false; interact() }
function pointerMove(e: PointerEvent) { if (down && Math.hypot(e.clientX-down.x, e.clientY-down.y) > 6) dragged = true; if (down) interact() }
function pointerUp() { if (!down) return; down = undefined; interact() }
function suppressClick(e: MouseEvent) { if (dragged) { e.preventDefault(); e.stopPropagation(); dragged = false } }
function keyRotate(e: KeyboardEvent) {
  if (!scene || !['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)) return
  e.preventDefault()
  const camera = scene.controls.object
  if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') camera.position.applyAxisAngle(camera.up, e.key === 'ArrowLeft' ? -.12 : .12)
  else camera.position.y += e.key === 'ArrowUp' ? 12 : -12
  interact()
}
watch(() => props.active, () => { if (!props.active) { cancelAnimationFrame(frame); frame = 0 } else kick() })
watch(paused, kick)
onMounted(async () => {
  preference = matchMedia('(prefers-reduced-motion: reduce)'); mediaChange(); preference.addEventListener('change', mediaChange)
  document.addEventListener('visibilitychange', visibility)
  window.addEventListener('pointerup', pointerUp)
  try {
    const { createGlobe } = await import('./globeScene')
    if (abort.signal.aborted || !host.value) return
    scene = await createGlobe(host.value, abort.signal, interact, () => { failed.value = true; cancelAnimationFrame(frame); frame = 0 })
    if (abort.signal.aborted) { scene.dispose(); return }
    ready.value = true
    observer = new ResizeObserver(() => { scene?.resize(); kick() }); observer.observe(host.value)
    intersection = new IntersectionObserver(([entry]) => { intersecting = !!entry?.isIntersecting; if (!intersecting) { cancelAnimationFrame(frame); frame = 0 } else kick() })
    intersection.observe(host.value); kick()
  } catch { if (!abort.signal.aborted) failed.value = true }
})
onBeforeUnmount(() => {
  abort.abort(); cancelAnimationFrame(frame); observer?.disconnect(); intersection?.disconnect()
  document.removeEventListener('visibilitychange', visibility); window.removeEventListener('pointerup', pointerUp)
  preference?.removeEventListener('change', mediaChange); scene?.dispose()
})
</script>
<template>
  <div class="globe-scene" :data-renderer="failed ? 'fallback' : ready ? 'webgl' : 'loading'">
    <img v-if="!ready || failed" class="globe-fallback" src="/assets/globe-fallback.png" alt="银灰色地球，象征连接与探索"/>
    <div ref="host" class="globe-canvas" :class="{ 'globe-hidden': failed }" tabindex="0" role="group" aria-label="探索地球。拖动或使用方向键旋转" @keydown="keyRotate" @pointerdown="pointerDown" @pointermove="pointerMove" @pointercancel="pointerUp" @click.capture="suppressClick"></div>
    <div class="globe-controls">
      <button type="button" :disabled="!ready || failed || reduced" :aria-pressed="paused" @click="paused = !paused"><Play v-if="paused || reduced" :size="13"/><Pause v-else :size="13"/>{{reduced ? '静态模式' : paused ? '继续旋转' : '暂停旋转'}}</button>
      <button type="button" :disabled="!ready || failed" @click="reset"><RotateCcw :size="13"/>复位</button>
    </div>
    <span v-if="failed" class="globe-fallback-notice">静态地球 · 导航仍可使用</span>
  </div>
</template>
