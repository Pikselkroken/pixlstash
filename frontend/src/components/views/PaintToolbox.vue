<template>
  <!-- The head of the Edit tab: the mark tools, seven across with each one's
       key on it, then the size while a brush or eraser is in hand. Pressing
       the tool in hand puts it down, as Esc does. Crop is a Quick change
       further down, and undo is the footer's Ctrl+Z. -->
  <div class="paint-toolbox">
    <div class="section-label section-label--on-dark paint-toolbox-head">
      <span>Mark where</span>
      <span v-if="current" class="paint-toolbox-current">{{
        current.label
      }}</span>
    </div>
    <div class="paint-toolbox-grid" role="toolbar" aria-label="Mark tools">
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
          :describe="false"
        />
        <v-icon size="20">{{ item.icon }}</v-icon>
        <span class="paint-tool-key" aria-hidden="true">{{ item.key }}</span>
      </button>
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
          :describe="false"
        />
        <v-icon size="20">mdi-invert-colors</v-icon>
        <span class="paint-tool-key" aria-hidden="true">I</span>
      </button>
    </div>
    <!-- − and + (= counts as +) change the size while a brush or eraser is in
         hand. [ and ] are not used: they rotate the picture. -->
    <div v-if="sized" class="paint-size" role="group" aria-label="Brush size">
      <button
        type="button"
        class="paint-tool paint-size-step"
        aria-label="Smaller brush (−)"
        aria-keyshortcuts="-"
        :disabled="paint.brushSize.value <= BRUSH_MIN"
        @click="paint.stepBrushSize(-1)"
      >
        <Tooltip text="Smaller" shortcut="−" activator="parent" :describe="false" />
        <span class="paint-size-dot paint-size-dot--small" aria-hidden="true"></span>
        <span class="paint-tool-key" aria-hidden="true">−</span>
      </button>
      <input
        class="paint-size-range"
        :value="paint.brushSize.value"
        type="range"
        :min="BRUSH_MIN"
        :max="BRUSH_MAX"
        step="1"
        aria-label="Brush size"
        @input="paint.setBrushSize($event.target.value)"
      />
      <button
        type="button"
        class="paint-tool paint-size-step"
        aria-label="Larger brush (+)"
        aria-keyshortcuts="+"
        :disabled="paint.brushSize.value >= BRUSH_MAX"
        @click="paint.stepBrushSize(1)"
      >
        <Tooltip text="Larger" shortcut="+" activator="parent" :describe="false" />
        <span class="paint-size-dot paint-size-dot--large" aria-hidden="true"></span>
        <span class="paint-tool-key" aria-hidden="true">+</span>
      </button>
      <output class="paint-size-value num">{{ paint.brushSize.value }} px</output>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue";
import {
  BRUSH_MAX,
  BRUSH_MIN,
  PAINT_TOOLS,
  SIZED_TOOLS,
} from "../../composables/usePaintSelection";
import Tooltip from "../widgets/Tooltip.vue";

const props = defineProps({
  /** The `usePaintSelection` state. */
  paint: { type: Object, required: true },
});

// Crop is a Quick change, not a mark tool; its key (C) still picks it.
const tools = PAINT_TOOLS.filter((item) => item.id !== "crop");
const current = computed(
  () => PAINT_TOOLS.find((item) => item.id === props.paint.tool.value) || null,
);
const sized = computed(() => SIZED_TOOLS.has(props.paint.tool.value));
</script>

<style scoped>
/* Dark in both themes, like the rest of the lightbox's chrome. */
.paint-toolbox {
  --hover-wash: rgba(var(--v-theme-on-dark-surface), 0.16);

  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.paint-toolbox-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.paint-toolbox-current {
  font-weight: var(--weight-regular);
  text-transform: none;
  letter-spacing: 0;
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
}

.paint-toolbox-grid {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: var(--space-1);
}

.paint-tool {
  position: relative;
  height: 34px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-dark-surface), 0.06);
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

.paint-size {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.paint-size-step {
  flex: none;
  width: 34px;
}

.paint-size-dot {
  border-radius: var(--radius-pill);
  background: currentColor;
}

.paint-size-dot--small {
  width: 4px;
  height: 4px;
}

.paint-size-dot--large {
  width: 12px;
  height: 12px;
}

.paint-size-range {
  flex: 1;
  min-width: 0;
}

.paint-size-value {
  flex: none;
  min-width: 6ch;
  text-align: right;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
}
</style>
