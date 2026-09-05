<template>
  <!-- The search half of the grid action pill. It is a run of controls, not a
       surface: GridActionPill owns the background, the seam and the anchor. -->
  <div class="search-result-bar">
    <!-- The one live region in the pill. Permanently mounted and never v-if'd:
         a region that mounts with content already in it announces unreliably
         across screen-reader/browser pairs. It carries the full sentence even
         when the visible text is compressed, and it is debounced so a slider
         drag reads once instead of ~40 times. -->
    <span
      class="visually-hidden"
      role="status"
      aria-live="polite"
      aria-atomic="true"
      >{{ announcement }}</span
    >

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

    <!-- Tuning. Both knobs filter the already-fetched ranked list client-side,
         so the count updates while dragging instead of per round-trip.

         ONE form at every width: a value-carrying trigger and a popover. The
         inline slider this replaces is gone, not hidden. See the note on
         `Match ≥` in merged-grid-action-pill.md §12.1. Two knobs cannot both
         live in a 40px band without taking half the pill, and a pair of sliders
         is a thing to compare against each other and against the count, which is
         a panel's job rather than a strip's. A standing state still compresses
         to its value and never disappears (visual-language.md §13), and that
         is what the trigger label carries. Vertical sliders remain rejected: 46
         discrete steps in a 40px band is 0.9px per step (§4). -->
    <template v-if="showThreshold">
      <span class="search-result-rule" aria-hidden="true"></span>

      <div class="search-result-threshold">
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
              class="stack-btn search-result-tune-btn"
              type="button"
              :aria-label="tuneAccessibleName"
              :title="tuneAccessibleName"
              :aria-disabled="imagesLoading ? 'true' : undefined"
              aria-haspopup="dialog"
            >
              <v-icon size="18">mdi-tune-variant</v-icon>
              <span class="search-result-threshold-value">
                {{ thresholdPercent }}%
              </span>
              <!-- Only once the knob is off its default. At 1-of-N it filters
                   nothing, and a permanent "1/7" would read as a live
                   constraint the user did not set. -->
              <span v-if="refsEngaged" class="search-result-threshold-refs">
                · {{ minRefs }}/{{ referenceCount }}
              </span>
            </button>
          </template>
          <div class="threshold-panel">
            <div class="threshold-group">
              <div class="threshold-group-head">
                <label class="section-label" :for="strengthId"
                  >Match strength</label
                >
                <output
                  class="threshold-readout"
                  :for="strengthId"
                  aria-live="off"
                  >{{ thresholdPercent }}%</output
                >
              </div>
              <div class="threshold-panel-row">
                <button
                  class="threshold-step"
                  type="button"
                  :disabled="threshold <= thresholdMin"
                  aria-label="Decrease match strength by 1 percent"
                  @click="stepThreshold(-0.01)"
                >
                  <v-icon size="18">mdi-minus</v-icon>
                </button>
                <input
                  :id="strengthId"
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
                  aria-label="Increase match strength by 1 percent"
                  @click="stepThreshold(0.01)"
                >
                  <v-icon size="18">mdi-plus</v-icon>
                </button>
              </div>
              <p class="threshold-group-hint">
                How closely a face has to resemble a reference.
              </p>
            </div>

            <!-- Dropped entirely below two references: a slider whose only legal
                 position is its minimum is chrome, not a control. -->
            <div v-if="showRefs" class="threshold-group">
              <div class="threshold-group-head">
                <label class="section-label" :for="refsId"
                  >Reference faces</label
                >
                <output class="threshold-readout" :for="refsId" aria-live="off"
                  >{{ minRefs }} of {{ referenceCount }}</output
                >
              </div>
              <div class="threshold-panel-row">
                <button
                  class="threshold-step"
                  type="button"
                  :disabled="minRefs <= 1"
                  aria-label="Require one fewer reference face"
                  @click="stepMinRefs(-1)"
                >
                  <v-icon size="18">mdi-minus</v-icon>
                </button>
                <input
                  :id="refsId"
                  class="search-result-threshold-input"
                  type="range"
                  min="1"
                  :max="referenceCount"
                  step="1"
                  :value="minRefs"
                  :aria-valuetext="`${minRefs} of ${referenceCount}`"
                  @input="onMinRefsInput"
                />
                <button
                  class="threshold-step"
                  type="button"
                  :disabled="minRefs >= referenceCount"
                  aria-label="Require one more reference face"
                  @click="stepMinRefs(1)"
                >
                  <v-icon size="18">mdi-plus</v-icon>
                </button>
              </div>
              <p class="threshold-group-hint">
                How many of them have to agree at that strength.
              </p>
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
           The count is on the button, never "all" - the blast radius has to be
           visible before the click, and it is what makes the sliders legible.

           The name is its own span so the ladder can DROP it and leave
           `Assign 14`, which was §7's intent. Ellipsising the whole label
           instead produced `Assign 2 t…`, a truncation mid-preposition that
           reads as a bug and loses the count's neighbour anyway. -->
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
        <span class="assign-label"
          >Assign {{ assignCount
          }}<span v-if="assignFromSelection"> selected</span
          ><span class="assign-target">to {{ assignTarget }}</span></span
        >
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
  /** Current likeness cut, 0-1. Null hides the whole tuning control. */
  threshold: { type: Number, default: null },
  /** Fetch floor: dragging below it would need a refetch, so it is the min. */
  thresholdMin: { type: Number, default: 0.5 },
  thresholdMax: { type: Number, default: 0.95 },
  /** Reference faces that must clear the cut, 1..referenceCount. */
  minRefs: { type: Number, default: 1 },
  /** How many reference faces the query carried. Under 2 hides that slider. */
  referenceCount: { type: Number, default: 0 },
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

const emit = defineEmits([
  "clear",
  "search-all",
  "update:threshold",
  "update:min-refs",
  "assign",
]);

const strengthId = useId();
const refsId = useId();
const thresholdMenuOpen = ref(false);

const showSearchAll = computed(() => !props.isAllPicturesActive);

// Deliberately NOT gated on `imagesLoading`. Hiding the controls while a search
// runs collapsed the pill and snapped it back to full width, moving targets
// under a cursor already travelling toward them; the controls stay mounted and
// aria-disabled instead (merged-grid-action-pill.md §3).
const showThreshold = computed(() => Number.isFinite(props.threshold));

const thresholdPercent = computed(() => Math.round(props.threshold * 100));

const showRefs = computed(() => props.referenceCount > 1);

// The agreement knob is "on" only above its floor. See the trigger markup.
const refsEngaged = computed(() => showRefs.value && props.minRefs > 1);

// The trigger's accessible name spells both knobs out. Its visible label is two
// numbers and a separator, which is legible next to the count it shapes but is
// not a sentence, and a control's name has to survive without the pill around
// it (visual-language.md §13).
const tuneAccessibleName = computed(() => {
  const base = `Tune suggestions. Match strength ${thresholdPercent.value}%`;
  if (!showRefs.value) return `${base}.`;
  return props.minRefs > 1
    ? `${base}, on at least ${props.minRefs} of ${props.referenceCount} reference faces.`
    : `${base}, on any of ${props.referenceCount} reference faces.`;
});

const statusSentence = computed(() => {
  if (props.imagesLoading) return "Searching…";
  return props.statusCount === null
    ? props.statusLabel
    : `${props.statusCount} ${props.statusLabel}`;
});

// The label compresses down the ladder; the accessible name never does. The
// count is in both, and never "all": the blast radius of a bulk write has to be
// visible before the click.
const assignAccessibleName = computed(() => {
  const base = props.assignFromSelection
    ? `Assign ${props.assignCount} selected to ${props.assignTarget}`
    : `Assign ${props.assignCount} to ${props.assignTarget}`;
  if (!props.assignFromSelection || props.assignCount === props.statusCount) {
    return base;
  }
  return `${base}. Using your ${props.assignCount} selected, not all ${props.statusCount} matches.`;
});

const clearTitle = computed(() =>
  props.ownsEscape
    ? "Clear search (Esc)"
    : "Clear search - press Esc twice, or click",
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

function onMinRefsInput(event) {
  emit("update:min-refs", Number(event.target.value));
}

function stepMinRefs(delta) {
  const next = Math.min(
    props.referenceCount,
    Math.max(1, props.minRefs + delta),
  );
  emit("update:min-refs", next);
}

// ── The one live region ─────────────────────────────────────────────────────
// Debounced 300ms trailing, matching the grid's own 200ms recut: dragging the
// slider must produce ONE announcement, not one per pointer sample. The
// threshold is folded into the same sentence rather than spoken separately -
// the <output> is aria-live="off" for exactly that reason (it maps to
// role="status" by default and would double-speak).
const announcement = ref("");
let announceTimer = null;

const announcementSource = computed(() => {
  if (props.imagesLoading) return "Searching…";
  if (!showThreshold.value) return statusSentence.value;
  const cut = `${statusSentence.value} at ${thresholdPercent.value}% or better`;
  // Both knobs in the one sentence, for the same reason the percentage is: two
  // regions racing over one drag is the defect §6.4 was written about.
  return refsEngaged.value
    ? `${cut}, on at least ${props.minRefs} of ${props.referenceCount} reference faces`
    : cut;
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
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  /* Never a flex line of its own: the trigger is two short numbers and must not
     take width from the status sentence beside it. */
  flex: 0 0 auto;
}

.search-result-threshold-value {
  font-size: var(--text-base);
  font-weight: var(--weight-medium);
  font-variant-numeric: tabular-nums;
  /* Reserved so the run does not shift as the number changes width. */
  min-width: 4ch;
  text-align: right;
}

/* The agreement half of the trigger. Quieter than the percentage: it is the
   secondary knob and it is absent at its default, so it must not read as the
   headline number when it does appear. */
.search-result-threshold-refs {
  font-size: var(--text-sm);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface), 0.65);
  white-space: nowrap;
}

.search-result-threshold-input {
  flex: 1;
  min-width: 0;
  /* The one property that behaves identically across engines. Do NOT hand-roll
     ::-webkit-slider-thumb / ::-moz-range-track. The track itself follows
     `color-scheme`, which style.css pins per theme. */
  accent-color: rgb(var(--v-theme-accent));
}

.threshold-panel {
  /* 300px, not the 240px this panel had as the narrow-width fallback: it is now
     the ONLY form of the strength slider, so its travel is the travel. 300 −
     2×16 padding − 2×32 steppers − 2×8 gaps = 188px for 46 steps ≈ 4.1px/step,
     against the ~2.8px/step the 240px panel gave (§4's arithmetic). */
  width: 300px;
  padding: var(--space-4);
  background: rgba(var(--v-theme-surface), 0.96);
  color: rgb(var(--v-theme-on-surface));
  border: 1px solid rgba(var(--v-theme-on-surface), 0.14);
  border-radius: var(--radius-lg);
  box-shadow: var(--elevation-3);
}

/* Two knobs, so each needs a boundary of its own. Air, not a rule: a hairline
   between two 3-row groups in a 300px panel is more furniture than structure. */
.threshold-group + .threshold-group {
  margin-top: var(--space-5);
}

.threshold-group-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
}

/* The value sits on the label's line rather than under the thumb: two sliders
   with travelling readouts is two moving numbers to track. */
.threshold-readout {
  font-size: var(--text-base);
  font-weight: var(--weight-medium);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.threshold-group-hint {
  margin: var(--space-2) 0 0;
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-surface), 0.65);
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
  border-radius: var(--radius-sm);
  color: rgb(var(--v-theme-on-surface));
}
.threshold-step:hover:not(:disabled) {
  background: var(--hover-wash);
}
.threshold-step:disabled {
  opacity: 0.35;
  cursor: default;
}

.threshold-panel-count {
  margin: var(--space-4) 0 0;
  padding-top: var(--space-3);
  border-top: 1px solid rgb(var(--v-theme-border));
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.65);
}

.search-result-actions {
  display: inline-flex;
  align-items: center;
  gap: var(--space-3);
}

/* Quiet control recipe. Mirrors `.stack-btn` in the selection half - scoped
   styles cannot share it, and lifting it to a global would put a pill-specific
   recipe in everyone's cascade. Keep the two in step. */
.stack-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: rgb(var(--v-theme-on-background));
  padding: 0 10px;
  border-radius: var(--radius-sm);
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
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-accent));
  color: rgb(var(--v-theme-on-accent));
  font-size: var(--text-base);
  font-family: inherit;
  font-weight: var(--weight-medium);
  white-space: nowrap;
}
.assign-btn:hover:not(:disabled) {
  filter: brightness(1.1);
}
.assign-btn:disabled {
  opacity: 0.35;
  cursor: default;
}

.assign-label {
  white-space: nowrap;
}

/* Dropped whole by the ladder, never ellipsised. See the markup.

   The word space before it is this margin, NOT a leading space in the text.
   `inline-block` is required for `text-overflow: ellipsis` to have a box to
   clip, and CSS strips leading whitespace at the start of an inline-block's
   line box, so a text space here renders as "Assign 0to Walter". --space-2 is
   4px against a ~3.9px word space at --text-base, so it matches the type.
   Do not move the space back into the markup. */
.assign-target {
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 16ch;
  display: inline-block;
  vertical-align: bottom;
  margin-left: var(--space-2);
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
   survives in `title` and in the live region at every width.

   The old ≤780px step is gone with the inline slider: the tuning control is a
   value-carrying trigger at every width, so there is nothing left to fold. That
   also moves the assign step down the ladder: dropping the popover reclaimed
   the 160–260px the inline slider used to take from exactly this run. */
@container selbar (max-width: 900px) {
  /* A bulk write states its blast radius: the count stays, the name goes. */
  .assign-target {
    display: none;
  }
  .search-all-label {
    display: none;
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
  .stack-btn,
  .assign-btn {
    height: var(--bar-height);
  }
  /* The one thing touch still needs from the old §7 rule: the panel's own
     controls at the touch-target floor. The trigger is already a button. */
  .threshold-step {
    width: var(--bar-height);
    height: var(--bar-height);
  }
}
</style>
