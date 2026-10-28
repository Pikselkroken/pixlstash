<template>
  <!-- The options of the tool in hand, floating above the picture (the
       design's Photoshop-style options strip): a selection tool's combine
       mode and tolerance, or crop's framings. The lightbox draws it only for
       those; the brush's size is in the Edit tab with the tools. -->
  <div
    class="paint-options"
    role="group"
    :aria-label="current ? `${current.label} options` : 'Paint tools'"
  >
    <span v-if="current" class="paint-options-name">
      <v-icon size="18">{{ current.icon }}</v-icon>
      {{ current.label }}
    </span>

    <template v-if="isModeTool">
      <Segmented
        :model-value="paint.mode.value"
        variant="icon-label"
        aria-label="Combine with the selection"
        :options="modes"
        @update:model-value="paint.setMode"
      />
      <label v-if="paint.tool.value === 'wand'" class="paint-options-range">
        Tolerance
        <input
          :value="paint.tolerance.value"
          type="range"
          min="0"
          max="128"
          step="1"
          @input="paint.setTolerance($event.target.value)"
        />
        <output>{{ paint.tolerance.value }}</output>
      </label>
      <span class="paint-options-hint">Shift adds · Alt takes away</span>
    </template>

    <template v-else-if="paint.tool.value === 'crop'">
      <button
        v-for="framing in framings"
        :key="framing.id"
        type="button"
        class="paint-options-chip"
        :class="{ 'paint-options-chip--on': sameBox(framing.box) }"
        :aria-pressed="sameBox(framing.box) ? 'true' : 'false'"
        @click="paint.setCropBox(framing.box)"
      >
        <v-icon size="16">{{ framing.icon }}</v-icon>
        {{ framing.label }}
      </button>
      <span class="paint-options-hint">or drag a frame</span>
      <template v-if="paint.cropBox.value">
        <AppButton size="sm" on-dark @click="paint.setCropBox(null)">
          Cancel
        </AppButton>
        <AppButton
          size="sm"
          variant="primary"
          on-dark
          :loading="cropping"
          @click="emit('apply-crop', paint.cropBox.value)"
        >
          Crop to a new picture
        </AppButton>
      </template>
    </template>

    <span v-if="current" class="paint-options-hint">Esc puts the tool down</span>
  </div>
</template>

<script setup>
import { computed } from "vue";
import {
  MODE_TOOLS,
  PAINT_MODES,
  PAINT_TOOLS,
} from "../../composables/usePaintSelection";
import AppButton from "../widgets/AppButton.vue";
import Segmented from "../widgets/Segmented.vue";

const props = defineProps({
  /** The `usePaintSelection` state. */
  paint: { type: Object, required: true },
  /** The crop framings for this picture (`utils/cropFramings`). */
  framings: { type: Array, default: () => [] },
  /** A crop is being saved. */
  cropping: { type: Boolean, default: false },
});

const emit = defineEmits(["apply-crop"]);

const modes = PAINT_MODES;
// `null` with no tool held.
const current = computed(
  () => PAINT_TOOLS.find((item) => item.id === props.paint.tool.value) || null,
);
const isModeTool = computed(() => MODE_TOOLS.has(props.paint.tool.value));

function sameBox(box) {
  const chosen = props.paint.cropBox.value;
  return !!chosen && chosen.every((value, index) => Math.abs(value - box[index]) < 1e-6);
}
</script>

<style scoped>
/* Dark in both themes, like the rest of the lightbox's chrome: the theme's
   on-surface ink is remapped onto the dark surface's, as AppButton's on-dark
   context does, so the Segmented track and its labels read here. */
.paint-options {
  --v-theme-on-surface: var(--v-theme-on-dark-surface);
  --hover-wash: rgba(var(--v-theme-on-dark-surface), 0.16);
  --track-trough: rgba(var(--v-theme-scrim), 0.34);
  --track-ring: rgba(var(--v-theme-on-dark-surface), 0.4);
  --focus-stroke: rgb(var(--v-theme-on-dark-surface));

  position: absolute;
  top: var(--space-3);
  left: 50%;
  transform: translateX(-50%);
  z-index: var(--z-floating);
  display: flex;
  align-items: center;
  gap: var(--space-2);
  max-width: calc(100% - 2 * var(--space-9));
  padding: var(--space-2);
  background: rgb(var(--v-theme-dark-surface));
  color: rgb(var(--v-theme-on-dark-surface));
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.12);
  border-radius: var(--radius-pill);
  box-shadow: var(--elevation-3);
  white-space: nowrap;
}

.paint-options-name {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  height: var(--control-h-bar);
  padding: 0 var(--space-3);
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}

.paint-options-name .v-icon {
  color: var(--active-bar);
}

.paint-options-hint {
  padding: 0 var(--space-3) 0 var(--space-2);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
}

.paint-options-range {
  display: inline-flex;
  align-items: center;
  gap: var(--space-3);
  padding: 0 var(--space-3);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
}

.paint-options-range input {
  width: 112px;
  accent-color: rgb(var(--v-theme-primary));
}

.paint-options-range output {
  min-width: 5ch;
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  color: rgb(var(--v-theme-on-dark-surface));
}

.paint-options-chip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  height: var(--control-h-sm);
  padding: 0 var(--space-3);
  border-radius: var(--radius-sm);
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-on-dark-surface));
}

.paint-options-chip:hover {
  background: var(--hover-wash);
}

.paint-options-chip--on {
  background: var(--active-wash);
  box-shadow: inset 0 0 0 1px var(--active-bar);
}
</style>
