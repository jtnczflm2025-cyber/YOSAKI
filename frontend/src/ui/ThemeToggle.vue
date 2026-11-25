<script setup lang="ts">
import { Sun, Moon } from 'lucide-vue-next'
import { theme, toggleTheme } from './theme'
</script>
<template>
  <button class="icon-btn theme-toggle" type="button" :aria-label="theme === 'light' ? '切换深色主题' : '切换浅色主题'" :title="theme === 'light' ? '深色主题' : '浅色主题'" @click="toggleTheme">
    <Moon v-if="theme === 'light'" :size="18"/><Sun v-else :size="18"/>
  </button>
</template>
