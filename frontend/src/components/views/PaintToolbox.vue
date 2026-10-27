<template>
  <!-- The design's one-column toolbox, floating beside the filmstrip while the
       Edit tab is open. Each tool wears its key; pressing the one in hand puts
       it down, as Esc does. -->
  <div class="paint-toolbox" role="toolbar" aria-label="Paint tools" aria-orientation="vertical">
    <button
      v-for="item in tools"
      :key="item.id"
      type="button"
      class="paint-tool"
      :class="{ 'paint-tool--on': paint.tool.value === item.id }"
      :aria-pressed="paint.tool.value === item.id ? 'true' : 'false'"
      :aria-label="`${item.label} (${item.key})`"
      :aria-keyshortcuts="item.key"
      @click="paint.setTool(paint.tool.value === item.id ? null : item.id)"
    >
      <Tooltip
        :text="item.label"
        :shortcut="item.key"
        activator="parent"
        location="end"
        :describe="false"
      />
      <v-icon size="20">{{ item.icon }}</v-icon>
      <span class="paint-tool-key" aria-hidden="true">{{ item.key }}</span>
    </button>
    <span class="paint-toolbox-rule" role="separator"></span>
    <button
      type="button"
      class="paint-tool"
      aria-label="Invert the selection (I)"
      aria-keyshortcuts="I"
      :disabled="!paint.hasSelection.value"
      @click="paint.invert()"
    >
      <Tooltip
        text="Invert the selection"
        shortcut="I"
        activator="parent"
        location="end"
        :describe="false"
      />
      <v-icon size="20">mdi-invert-colors</v-icon>
      <span class="paint-tool-key" aria-hidden="true">I</span>
    </button>
    <button
      type="button"
      class="paint-tool"
      aria-label="Undo the last selection step (Ctrl+Z)"
      aria-keyshortcuts="Control+Z"
      :disabled="!paint.hasSelection.value"
      @click="paint.undo()"
    >
      <Tooltip
        text="Undo the last selection step"
        shortcut="Ctrl+Z"
        activator="parent"
        location="end"
        :describe="false"
      />
      <v-icon size="20">mdi-undo</v-icon>
    </button>
  </div>
</template>

<script setup>
import { PAINT_TOOLS } from "../../composables/usePaintSelection";
import Tooltip from "../widgets/Tooltip.vue";

defineProps({
  /** The `usePaintSelection` state. */
  paint: { type: Object, required: true },
});

const tools = PAINT_TOOLS;
</script>

<style scoped>
/* Dark in both themes, like the rest of the lightbox's chrome. */
.paint-toolbox {
  --hover-wash: rgba(var(--v-theme-on-dark-surface), 0.16);
  --focus-stroke: rgb(var(--v-theme-on-dark-surface));

  position: absolute;
  top: var(--space-3);
  left: var(--space-3);
  z-index: var(--z-floating);
  display: grid;
  grid-template-columns: 34px;
  gap: var(--space-1);
  padding: var(--space-2);
  background: rgb(var(--v-theme-dark-surface));
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.12);
  border-radius: var(--radius-lg);
  box-shadow: var(--elevation-3);
}

.paint-tool {
  position: relative;
  width: 34px;
  height: 34px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  color: rgb(var(--v-theme-on-dark-surface));
}

.paint-tool:hover:not(:disabled) {
  background: var(--hover-wash);
}

.paint-tool:disabled {
  opacity: var(--opacity-disabled);
}

.paint-tool--on {
  background: var(--active-wash);
  box-shadow: inset 0 0 0 1.5px var(--active-bar);
}

.paint-tool--on .v-icon {
  color: var(--active-bar);
}

.paint-tool-key {
  position: absolute;
  right: 2px;
  bottom: 0;
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  line-height: 1.2;
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
}

.paint-toolbox-rule {
  height: 1px;
  margin: var(--space-1) var(--space-2);
  background: rgba(var(--v-theme-on-dark-surface), 0.12);
}
</style>
