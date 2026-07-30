<template>
  <!-- The search half of the grid action pill. It is a run of controls, not a
       surface: GridActionPill owns the background, the seam and the anchor. -->
  <div class="search-result-bar">
    <!-- The one live region in the pill. Permanently mounted and never v-if'd:
         a region that mounts with content already in it announces unreliably
         across screen-reader/browser pairs. It carries the full sentence even
         when the visible text is compressed, and it is debounced so a slider
         drag reads once instead of ~40 times. -->
    <span class="visually-hidden" role="status" aria-live="polite" aria-atomic="true">{{
      announcement
    }}</span>

    <span class="search-result-status" :title="statusSentence">
      <v-progress-circular
        v-if="imagesLoading"
        indeterminate
        size="16"
        width="2"
        color="primary"
        class="search-result-glyph"
      ></v-progress-circular>
      <!-- The half's identity glyph. Sits in the same box as the spinner, so a
           load does not change the pill's width (see the note on the loading
           state below). -->
      <v-icon v-else size="18" class="search-result-glyph">mdi-magnify</v-icon>

      <template v-if="imagesLoading">
        <span class="search-result-label">Searching…</span>
      </template>
      <template v-else>
        <!-- Numeral and noun in one shared recipe with the selection half's
             count: two numerals bracketing the pill is the fastest parse of
             "left is what I found, right is what I picked". This is the
             differentiator a second background colour was rejected in favour of
             (merged-grid-action-pill.md §2.1). -->
        <span v-if="statusCount !== null" class="search-result-count">{{
          statusCount
        }}</span>
        <span class="search-result-label">{{ statusLabel }}</span>
      </template>
    </span>

    <!-- Threshold. Filters the already-fetched ranked list client-side, so the
         count updates while dragging instead of per round-trip.

         Two forms, swapped by the container query at the bottom of this file,
         never both operable at once: `display: none` keeps the hidden one out
         of the tab order, so this does not create an invisible tab stop. The
         inline form keeps the sweep-and-watch-the-count gesture wherever there
         is room for real travel; the popover form is what narrow widths and
         every coarse pointer get, because a 40px inline slider under a finger
         is a mis-hit generator. Vertical was rejected: 46 discrete steps in a
         40px band is 0.9px per step (§4). -->
    <template v-if="showThreshold">
      <span class="search-result-rule" aria-hidden="true"></span>

      <!-- Inline: the label IS the readout, one run instead of
           label + gap + track + gap + output. -->
      <div class="search-result-threshold search-result-threshold--inline">
        <label class="search-result-threshold-label" :for="inlineThresholdId"
          >Match ≥</label
        >
        <output
          class="search-result-threshold-value"
          :for="inlineThresholdId"
          aria-live="off"
          >{{ thresholdPercent }}%</output
        >
        <input
          :id="inlineThresholdId"
          class="search-result-threshold-input"
          type="range"
          :min="thresholdMin"
          :max="thresholdMax"
          step="0.01"
          :value="threshold"
          :aria-valuetext="`${thresholdPercent}%`"
          :aria-disabled="imagesLoading ? 'true' : undefined"
          @input="onThresholdInput"
        />
      </div>

      <!-- Compressed: value-carrying trigger + popover. A standing state
           compresses to its value and never disappears (visual-language.md §13). -->
      <div class="search-result-threshold search-result-threshold--compact">
        <v-menu
          v-model="thresholdMenuOpen"
          :close-on-content-click="false"
          location="top"
          origin="bottom center"
          transition="scale-transition"
        >
          <template #activator="{ props: menuProps }">
            <button
              v-bind="menuProps"
              class="stack-btn"
              type="button"
              :aria-label="`Match at least ${thresholdPercent}%`"
              aria-haspopup="dialog"
            >
              <v-icon size="18">mdi-tune-variant</v-icon>
              <span class="search-result-threshold-value">
                {{ thresholdPercent }}%
              </span>
            </button>
          </template>
          <div class="threshold-panel">
            <label class="section-label" :for="popoverThresholdId"
              >Match at least</label
            >
            <div class="threshold-panel-row">
              <button
                class="threshold-step"
                type="button"
                :disabled="threshold <= thresholdMin"
                aria-label="Decrease by 1 percent"
                @click="stepThreshold(-0.01)"
              >
                <v-icon size="18">mdi-minus</v-icon>
              </button>
              <input
                :id="popoverThresholdId"
                class="search-result-threshold-input"
                type="range"
                :min="thresholdMin"
                :max="thresholdMax"
                step="0.01"
                :value="threshold"
                :aria-valuetext="`${thresholdPercent}%`"
                @input="onThresholdInput"
              />
              <button
                class="threshold-step"
                type="button"
                :disabled="threshold >= thresholdMax"
                aria-label="Increase by 1 percent"
                @click="stepThreshold(0.01)"
              >
                <v-icon size="18">mdi-plus</v-icon>
              </button>
            </div>
            <!-- The count repeated inside, so tuning does not require looking
                 back past the popover at the pill it covers. -->
            <p class="threshold-panel-count">{{ statusSentence }}</p>
          </div>
        </v-menu>
      </div>
    </template>

    <div class="search-result-actions">
      <button
        v-if="showSearchAll"
        class="stack-btn"
        type="button"
        title="Search everything, not just this category"
        @click="$emit('search-all')"
      >
        <v-icon size="18">mdi-magnify-expand</v-icon>
        <span class="search-all-label">Search everything</span>
      </button>

      <!-- The one accent-weight action in the pill: it is the only bulk WRITE.
           The count is on the button, never "all" — the blast radius has to be
           visible before the click, and it is what makes the slider legible. -->
      <button
        v-if="assignTarget"
        class="assign-btn"
        type="button"
        :disabled="assignCount === 0 || assignBusy"
        :aria-label="assignAccessibleName"
        :title="assignAccessibleName"
        @click="$emit('assign')"
      >
        <v-icon size="18">mdi-account-check-outline</v-icon>
        <span class="assign-label">{{ assignLabel }}</span>
      </button>

      <button
        class="stack-btn clear-search-btn"
        type="button"
        :title="clearTitle"
        :aria-keyshortcuts="ownsEscape ? 'Escape' : undefined"
        @click="$emit('clear')"
      >
        <v-icon size="18" class="clear-search-glyph">mdi-magnify-close</v-icon>
        <span class="clear-search-label">Clear search</span>
        <!-- aria-hidden: the accessible name stays the verb, and the
             machine-readable copy is aria-keyshortcuts above
             (visual-language.md §13). -->
        <kbd v-if="ownsEscape" class="key-hint" aria-hidden="true">Esc</kbd>
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed, onUnmounted, ref, useId, watch } from "vue";

const props = defineProps({
  imagesLoading: { type: Boolean, default: false },
  /** The numeral in the status sentence. Null renders the sentence alone. */
  statusCount: { type: Number, default: null },
  /** The rest of the sentence, e.g. `matches for "sunset" in Landscapes`. */
  statusLabel: { type: String, default: "results" },
  isAllPicturesActive: { type: Boolean, default: false },
  /** Current likeness cut, 0-1. Null hides the threshold. */
  threshold: { type: Number, default: null },
  /** Fetch floor: dragging below it would need a refetch, so it is the min. */
  thresholdMin: { type: Number, default: 0.5 },
  thresholdMax: { type: Number, default: 0.95 },
  /** Person the results can be assigned to; null hides the assign action. */
  assignTarget: { type: String, default: null },
  /** How many pictures the assign action would write. Stated on the button. */
  assignCount: { type: Number, default: 0 },
  /** True when the assign action is chosen by an explicit grid selection. */
  assignFromSelection: { type: Boolean, default: false },
  assignBusy: { type: Boolean, default: false },
  /**
   * Esc reaches THIS half (nothing is selected). Only the control Esc will
   * actually hit wears the keycap: an aria-keyshortcuts on a button that will
   * not get the key is a 4.1.2 lie.
   */
  ownsEscape: { type: Boolean, default: true },
});

const emit = defineEmits(["clear", "search-all", "update:threshold", "assign"]);

const inlineThresholdId = useId();
const popoverThresholdId = useId();
const thresholdMenuOpen = ref(false);

const showSearchAll = computed(() => !props.isAllPicturesActive);

// Deliberately NOT gated on `imagesLoading`. Hiding the controls while a search
// runs collapsed the pill and snapped it back to full width, moving targets
// under a cursor already travelling toward them; the controls stay mounted and
// aria-disabled instead (merged-grid-action-pill.md §3).
const showThreshold = computed(() => Number.isFinite(props.threshold));

const thresholdPercent = computed(() => Math.round(props.threshold * 100));

const statusSentence = computed(() => {
  if (props.imagesLoading) return "Searching…";
  return props.statusCount === null
    ? props.statusLabel
    : `${props.statusCount} ${props.statusLabel}`;
});

// The count is on the button, not "all": the blast radius of a bulk write has
// to be visible before the click.
const assignLabel = computed(() =>
  props.assignFromSelection
    ? `Assign ${props.assignCount} selected to ${props.assignTarget}`
    : `Assign ${props.assignCount} to ${props.assignTarget}`,
);

// The label compresses down the ladder; the accessible name never does.
const assignAccessibleName = computed(() => {
  const base = assignLabel.value;
  if (!props.assignFromSelection || props.assignCount === props.statusCount) {
    return base;
  }
  return `${base}. Using your ${props.assignCount} selected, not all ${props.statusCount} matches.`;
});

const clearTitle = computed(() =>
  props.ownsEscape
    ? "Clear search (Esc)"
    : "Clear search — press Esc twice, or click",
);

function onThresholdInput(event) {
  emit("update:threshold", Number(event.target.value));
}

function stepThreshold(delta) {
  const next = Math.min(
    props.thresholdMax,
    Math.max(props.thresholdMin, props.threshold + delta),
  );
  // Float arithmetic on a 0.01 step drifts (0.7 + 0.01 = 0.7100000000000001),
  // which would render as 71% but store a value the slider cannot match.
  emit("update:threshold", Math.round(next * 100) / 100);
}

// ── The one live region ─────────────────────────────────────────────────────
// Debounced 300ms trailing, matching the grid's own 200ms recut: dragging the
// slider must produce ONE announcement, not one per pointer sample. The
// threshold is folded into the same sentence rather than spoken separately —
// the <output> is aria-live="off" for exactly that reason (it maps to
// role="status" by default and would double-speak).
const announcement = ref("");
let announceTimer = null;

const announcementSource = computed(() => {
  if (props.imagesLoading) return "Searching…";
  if (!showThreshold.value) return statusSentence.value;
  return `${statusSentence.value} at ${thresholdPercent.value}% or better`;
});

watch(
  announcementSource,
  (text) => {
    if (announceTimer !== null) clearTimeout(announceTimer);
    announceTimer = setTimeout(() => {
      announceTimer = null;
      announcement.value = text;
    }, 300);
  },
  { immediate: true },
);

onUnmounted(() => {
  if (announceTimer !== null) clearTimeout(announceTimer);
});
</script>

<style scoped>
.search-result-bar {
  display: contents;
}

.search-result-status {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}

.search-result-glyph {
  flex: 0 0 auto;
  width: 18px;
  color: rgba(var(--v-theme-on-surface), 0.55);
}

/* Numeral and noun share the selection half's count recipe, so the two read as
   siblings. Hierarchy by weight and colour, never by a new size. */
.search-result-count {
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.search-result-label {
  font-size: var(--text-sm);
  font-weight: var(--weight-regular);
  color: rgba(var(--v-theme-on-surface), 0.65);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}

/* The intra-half rule: status + threshold on one side (dragging the threshold
   changes the count, so they group), actions on the other. Shorter than the
   seam and with only the pill's own 8px gap of air, so the two boundaries are
   told apart by height and air alone. */
.search-result-rule {
  width: 1px;
  height: var(--rule-h);
  background: rgb(var(--v-theme-border));
  align-self: center;
  flex-shrink: 0;
}

.search-result-threshold {
  align-items: center;
  gap: var(--space-2);
}

.search-result-threshold--inline {
  display: inline-flex;
  /* Real travel is the whole argument for keeping this inline. */
  flex: 1 1 160px;
  min-width: 120px;
  max-width: 260px;
}

.search-result-threshold--compact {
  display: none;
}

.search-result-threshold-label {
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), 0.65);
  white-space: nowrap;
}

.search-result-threshold-value {
  font-size: var(--text-base);
  font-weight: var(--weight-medium);
  font-variant-numeric: tabular-nums;
  /* Reserved so the run does not shift as the number changes width. */
  min-width: 4ch;
  text-align: right;
}

.search-result-threshold-input {
  flex: 1;
  min-width: 0;
  /* The one property that behaves identically across engines. Do NOT hand-roll
     ::-webkit-slider-thumb / ::-moz-range-track. The track itself follows
     `color-scheme`, which style.css pins per theme. */
  accent-color: rgb(var(--v-theme-accent));
}

.search-result-threshold-input:focus-visible {
  box-shadow: var(--focus-ring);
  outline: none;
}

.threshold-panel {
  width: 240px;
  padding: var(--space-4);
  background: rgba(var(--v-theme-surface), 0.96);
  color: rgb(var(--v-theme-on-surface));
  border: 1px solid rgba(var(--v-theme-on-surface), 0.14);
  border-radius: var(--radius-lg);
  box-shadow: var(--elevation-3);
}

.threshold-panel-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-top: var(--space-3);
}

.threshold-step {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: rgb(var(--v-theme-on-surface));
  cursor: pointer;
}
.threshold-step:hover:not(:disabled) {
  background: var(--hover-wash);
}
.threshold-step:disabled {
  opacity: 0.35;
  cursor: default;
}

.threshold-panel-count {
  margin: var(--space-3) 0 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.65);
}

.search-result-actions {
  display: inline-flex;
  align-items: center;
  gap: var(--space-3);
}

/* Quiet control recipe. Mirrors `.stack-btn` in the selection half — scoped
   styles cannot share it, and lifting it to a global would put a pill-specific
   recipe in everyone's cascade. Keep the two in step. */
.stack-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: transparent;
  color: rgb(var(--v-theme-on-background));
  border: none;
  padding: 0 10px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: var(--text-base);
  font-family: inherit;
  height: 40px;
  white-space: nowrap;
}
.stack-btn:hover:not(:disabled) {
  background: rgba(var(--v-theme-on-background), 0.12);
}
.stack-btn:disabled {
  opacity: 0.35;
  cursor: default;
}

.assign-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 40px;
  padding: 0 var(--space-4);
  border: none;
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-accent));
  color: rgb(var(--v-theme-on-accent));
  font-size: var(--text-base);
  font-family: inherit;
  font-weight: var(--weight-medium);
  white-space: nowrap;
  cursor: pointer;
}
.assign-btn:hover:not(:disabled) {
  filter: brightness(1.1);
}
.assign-btn:disabled {
  opacity: 0.35;
  cursor: default;
}

.assign-label {
  max-width: 22ch;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Its own breathing room before Clear search: a bulk write and the button that
   throws the results away must not read as a pair of equals. */
.clear-search-btn {
  margin-left: var(--space-2);
}

.key-hint {
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  border: 1px solid currentColor;
  border-radius: var(--radius-sm);
  padding: 0 4px;
  opacity: 0.55;
}

/* ── Responsive ladder (container `selbar`, declared on .grid-content-area) ──
   Each step gives up the least information still available. The full string
   survives in `title` and in the live region at every width. */
@container selbar (max-width: 1100px) {
  /* A bulk write states its blast radius: the count stays, the name goes. */
  .assign-label {
    max-width: 10ch;
  }
}

@container selbar (max-width: 900px) {
  .search-all-label {
    display: none;
  }
}

/* The threshold folds into its value + popover. Also unconditional on coarse
   pointers below. */
@container selbar (max-width: 780px) {
  .search-result-threshold--inline {
    display: none;
  }
  .search-result-threshold--compact {
    display: inline-flex;
  }
}

@container selbar (max-width: 680px) {
  .clear-search-label,
  .key-hint {
    display: none;
  }
  .clear-search-btn {
    padding: 0 10px;
  }
}

@container selbar (max-width: 560px) {
  .search-result-label {
    display: none;
  }
}

@media (hover: none) and (pointer: coarse) {
  .search-result-threshold--inline {
    display: none;
  }
  .search-result-threshold--compact {
    display: inline-flex;
  }
  .stack-btn,
  .assign-btn {
    height: var(--bar-height);
  }
}
</style>
