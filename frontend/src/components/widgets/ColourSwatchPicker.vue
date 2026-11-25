<!--
  Search by colour (#2151): twelve swatches and the one "Contains at least"
  amount every picked swatch shares. The search field's list and the Filters
  menu's Colour submenu both draw this, over the one filter store, so there is
  one control in two places rather than two controls.

  A click picks a swatch and a second click lets it go. The amount is a
  draft while the thumb moves and lands on release, so a drag refetches the
  grid once, not once per percent.

  The eyedropper samples a colour from the screen (in the desktop app, from
  its own window) and picks the swatch it counts toward; the small well beside
  it shows the sampled colour itself while that swatch stays picked.
-->
<template>
  <div class="csp">
    <div class="csp-head">
      <span class="tbm-label">Colour</span>
      <span class="csp-hint">{{ hint }}</span>
      <span
        v-if="shownSample"
        class="csp-sample"
        :style="{ '--swatch': shownSample.hex }"
        role="img"
        :aria-label="sampleText"
        data-testid="csp-sample"
      >
        <Tooltip :text="sampleText" activator="parent" />
      </span>
      <AppButton
        v-if="canPickFromScreen"
        variant="ghost"
        size="sm"
        icon-only
        icon-left="eyedropper"
        tooltip="Pick a colour from the screen"
        aria-label="Pick a colour from the screen"
        data-testid="csp-eyedropper"
        @click="pickFromScreen"
      />
    </div>
    <div class="csp-row" role="group" aria-label="Colours">
      <button
        v-for="swatch in COLOUR_SWATCHES"
        :key="swatch.id"
        type="button"
        class="csp-swatch"
        :class="{
          'csp-swatch--on': picked.has(swatch.id),
          'csp-swatch--dark-mark': swatch.dark,
        }"
        :style="{ '--swatch': swatch.fill }"
        :aria-label="swatch.label"
        :aria-pressed="picked.has(swatch.id) ? 'true' : 'false'"
        :data-swatch="swatch.id"
        @click="toggle(swatch.id)"
        @mouseenter="pointed = swatch.id"
        @mouseleave="pointed = null"
        @focus="pointed = swatch.id"
        @blur="pointed = null"
      >
        <v-icon v-if="picked.has(swatch.id)" size="18" aria-hidden="true"
          >mdi-check</v-icon
        >
        <Tooltip :text="tipText(swatch)" activator="parent" />
      </button>
    </div>
    <label class="csp-amount">
      <span>Contains at least</span>
      <span class="csp-slider">
        <!-- `.stop`: the search list prevents mousedown to keep the caret in
             its field, which would also stop the thumb from being dragged. -->
        <input
          type="range"
          :min="COLOUR_AMOUNT_MIN"
          :max="COLOUR_AMOUNT_MAX"
          step="1"
          :value="draft"
          aria-label="Contains at least, percent of the picture"
          @mousedown.stop
          @input="draft = Number($event.target.value)"
          @change="commit"
        />
        <span class="csp-tick" aria-hidden="true">{{ COLOUR_AMOUNT_MIN }}%</span>
        <span class="csp-tick csp-tick--max" aria-hidden="true"
          >{{ COLOUR_AMOUNT_MAX }}%</span
        >
      </span>
      <output class="csp-value">{{ draft }}%</output>
    </label>
  </div>
</template>

<script>
import { ref } from "vue";

// The last colour sampled, shared by the search list's picker and the Filters
// menu's, as the swatches themselves are.
const colourSample = ref(null);
</script>

<script setup>
import { computed, watch } from "vue";

import { useFilterStore } from "../../stores/useFilterStore";
import {
  COLOUR_AMOUNT_MAX,
  COLOUR_AMOUNT_MIN,
  COLOUR_SWATCHES,
  nearestSwatch,
} from "../../utils/colourSwatches";
import { formatCount } from "../../utils/filterChips";
import {
  canPickScreenColour,
  pickScreenColour,
} from "../../utils/screenColour";
import AppButton from "./AppButton.vue";
import Tooltip from "./Tooltip.vue";

const props = defineProps({
  // The line beside the label: what a click does, or what several mean.
  hint: { type: String, default: "Click to add · again to remove" },
  // How many pictures of the view hold a swatch at the current amount, or
  // undefined while unknown. Asked only for the swatch pointed at.
  countFor: { type: Function, default: null },
});

const filters = useFilterStore();

const picked = computed(() => new Set(filters.colourFilter || []));
const pointed = ref(null);
const draft = ref(filters.colourAmountFilter);

watch(
  () => filters.colourAmountFilter,
  (amount) => {
    draft.value = amount;
  },
);

function toggle(id) {
  const list = filters.colourFilter || [];
  filters.colourFilter = list.includes(id)
    ? list.filter((v) => v !== id)
    : [...list, id];
}

function commit() {
  filters.colourAmountFilter = draft.value;
}

const canPickFromScreen = canPickScreenColour();

// Shown only while the swatch it counts toward is still picked, so removing
// that swatch, or Clear all, takes the well away with it.
const shownSample = computed(() =>
  colourSample.value && picked.value.has(colourSample.value.swatch.id)
    ? colourSample.value
    : null,
);
const sampleText = computed(() =>
  shownSample.value
    ? `Picked ${shownSample.value.hex} · searches as ${shownSample.value.swatch.label}`
    : "",
);

/** Sample a colour from the screen and pick the swatch it counts toward. */
async function pickFromScreen() {
  const hex = await pickScreenColour();
  const swatch = hex ? nearestSwatch(hex) : null;
  if (!swatch) return;
  colourSample.value = { hex, swatch };
  const list = filters.colourFilter || [];
  if (!list.includes(swatch.id)) {
    filters.colourFilter = [...list, swatch.id];
  }
}

/** The swatch's name, and for the one pointed at what picking it would show. */
function tipText(swatch) {
  if (pointed.value !== swatch.id || !props.countFor) return swatch.label;
  const n = props.countFor(swatch.id);
  if (n == null) return swatch.label;
  return `${swatch.label} · ${formatCount(n)} at ≥${filters.colourAmountFilter}%`;
}
</script>

<style scoped>
.csp {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.csp-head {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}
.csp-hint {
  margin-left: auto;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}
/* Spread across the list's width, so the row reads as a ruler. */
.csp-row {
  display: flex;
  justify-content: space-between;
  gap: var(--space-3);
}
/* The sampled colour itself: its own small well, a swatch's hairline. */
.csp-sample {
  flex-shrink: 0;
  width: var(--space-5);
  height: var(--space-5);
  border-radius: var(--radius-sm);
  background: var(--swatch);
  box-shadow: inset 0 0 0 1px rgba(var(--v-theme-on-panel), 0.28);
}
.csp-swatch {
  position: relative;
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  width: var(--control-h);
  height: var(--control-h);
  padding: 0;
  color: rgb(var(--v-theme-on-primary));
  background: var(--swatch);
  border: 0;
  border-radius: var(--radius-sm);
  box-shadow: inset 0 0 0 1px rgba(var(--v-theme-on-panel), 0.22);
  cursor: pointer;
}
.csp-swatch--dark-mark {
  color: rgb(var(--v-theme-dark-surface));
}
.csp-swatch:hover::after {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: var(--scrim-drawer);
}
/* Picked is the system's selected mark: the olive ring, clear of the fill. */
.csp-swatch--on {
  box-shadow:
    0 0 0 2px rgb(var(--v-theme-panel)),
    0 0 0 4px var(--active-bar);
}
.csp-amount {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  margin-top: var(--space-2);
  padding-bottom: var(--space-4);
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-panel));
}
.csp-slider {
  position: relative;
  flex: 1;
  display: flex;
}
.csp-slider input {
  width: 100%;
  margin: 0;
  accent-color: rgb(var(--v-theme-primary));
  cursor: pointer;
}
.csp-tick {
  position: absolute;
  top: 100%;
  left: 0;
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}
.csp-tick--max {
  left: auto;
  right: 0;
}
.csp-value {
  min-width: var(--space-7);
  text-align: right;
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}
</style>
