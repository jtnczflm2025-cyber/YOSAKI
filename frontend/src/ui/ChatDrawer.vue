<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { X } from 'lucide-vue-next'
const props = defineProps<{ open: boolean; title: string }>()
const emit = defineEmits<{ close: [] }>()
const panel = ref<HTMLElement>()
let trigger: HTMLElement | null = null
watch(() => props.open, async open => {
  if (open) {
    trigger = document.activeElement as HTMLElement
    await nextTick()
    panel.value?.querySelector<HTMLButtonElement>('.chat-drawer-close')?.focus()
  } else { trigger?.focus() }
})
function keydown(event: KeyboardEvent) {
  if (event.key === 'Escape') { event.preventDefault(); emit('close'); return }
  if (event.key !== 'Tab') return
  const items = Array.from(panel.value?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href],summary,[tabindex="0"]') ?? []).filter(el => el.getClientRects().length)
  const first = items[0], last = items[items.length-1]
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
}
</script>
<template>
  <!-- v-show keeps recording/playback components mounted while the drawer closes. -->
  <div v-show="open" class="chat-drawer-frame">
    <div class="chat-drawer-scrim" @click="emit('close')"></div>
    <section ref="panel" class="chat-drawer" role="dialog" aria-modal="true" :aria-label="title" @keydown="keydown">
      <header><h2>{{title}}</h2><button class="chat-drawer-close" type="button" aria-label="关闭面板" @click="emit('close')"><X :size="18"/></button></header>
      <div class="chat-drawer-scroll"><slot/></div>
      <footer v-if="$slots.activity" class="chat-drawer-activity"><slot name="activity"/></footer>
    </section>
  </div>
</template>
