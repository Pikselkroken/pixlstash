<template>
  <div class="toolbox" :class="{ collapsed }">
    <div class="toolbox-header" @click="collapsed = !collapsed">
      <v-icon>{{
        collapsed ? "mdi-chevron-left" : "mdi-chevron-right"
      }}</v-icon>
      <span v-if="!collapsed">Analysis Toolbox</span>
      <span v-else class="vertical-header">Analysis Toolbox</span>
    </div>
    <div v-show="!collapsed" class="toolbox-content">
      <slot />
    </div>
  </div>
</template>

<script setup>
import { ref } from "vue";
const collapsed = ref(false);
</script>

<style scoped>
/* Adjust top offset to match toolbar height (e.g. 64px) */
.toolbox {
  position: fixed;
  top: 60px;
  right: 0;
  width: 320px;
  height: calc(100vh - 60px);
  background: #222;
  color: #fff;
  box-shadow: -2px 0 8px rgba(0, 0, 0, 0.15);
  z-index: 100;
  display: flex;
  flex-direction: column;
  transition: width 0.2s;
}
.toolbox.collapsed {
  width: 32px;
}
.toolbox-header {
  display: flex;
  align-items: center;
  padding: 2px;
  font-weight: bold;
  cursor: pointer;
  background: #333;
  border-bottom: 1px solid #444;
}
.toolbox.collapsed .toolbox-header {
  flex-direction: column;
  justify-content: flex-start;
  align-items: center;
  height: 100%;
  padding: 2px 0;
}
.vertical-header {
  writing-mode: vertical-rl;
  transform: rotate(0deg);
  font-size: 0.8em;
  letter-spacing: 0.05em;
  margin-left: 2px;
  margin-right: 2px;
  white-space: nowrap;
}
.toolbox-content {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
}
</style>
