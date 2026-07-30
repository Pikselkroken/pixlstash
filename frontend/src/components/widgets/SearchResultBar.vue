<template>
  <div class="search-result-bar">
    <span class="search-result-status">
      <template v-if="imagesLoading">
        <v-progress-circular
          indeterminate
          size="16"
          width="2"
          color="primary"
          class="search-result-spinner"
        ></v-progress-circular>
        <span>Searching...</span>
      </template>
      <template v-else>
        <!-- The count moves as the threshold slider does, so it is announced:
             a sighted user watches the number, everyone else needs to hear it. -->
        <span aria-live="polite">{{
          statusText ?? `Search result found ${count} items`
        }}</span>
        <span v-if="showScopeNote" class="search-result-scope">
          Searched {{ categoryLabel }} only
        </span>
      </template>
    </span>

    <!-- Threshold. Filters the already-fetched ranked list client-side, so the
         count updates while dragging instead of per round-trip. -->
    <div v-if="showThreshold" class="search-result-threshold">
      <label class="search-result-threshold-label" :for="thresholdId"
        >Match at least</label
      >
      <input
        :id="thresholdId"
        class="search-result-threshold-input"
        type="range"
        :min="thresholdMin"
        :max="thresholdMax"
        step="0.01"
        :value="threshold"
        @input="$emit('update:threshold', Number($event.target.value))"
      />
      <output class="search-result-threshold-value" :for="thresholdId"
        >{{ thresholdPercent }}%</output
      >
    </div>

    <div class="search-result-actions">
      <v-btn v-if="showSearchAll" variant="tonal" @click="$emit('search-all')">
        Search All Pictures
      </v-btn>
      <!-- Separated from Clear search by its own group so a bulk write is not
           adjacent to the button that throws the results away. -->
      <v-btn
        v-if="assignTarget"
        color="accent"
        class="text-none search-result-assign"
        :disabled="assignCount === 0 || assignBusy"
        :loading="assignBusy"
        @click="$emit('assign')"
      >
        <v-icon size="18" start>mdi-account-check-outline</v-icon>
        {{ assignLabel }}
      </v-btn>
      <v-tooltip text="Clear search (Esc)" location="top">
        <template #activator="{ props: tooltipProps }">
          <v-btn
            color="primary"
            class="text-none"
            v-bind="tooltipProps"
            @click="$emit('clear')"
            >Clear search</v-btn
          >
        </template>
      </v-tooltip>
    </div>
  </div>
</template>

<script setup>
import { computed, useId } from "vue";

const props = defineProps({
  imagesLoading: { type: Boolean, default: false },
  count: { type: Number, default: 0 },
  categoryLabel: { type: String, default: "Category" },
  isAllPicturesActive: { type: Boolean, default: false },
  statusText: { type: String, default: null },
  /** Current likeness cut, 0-1. Null hides the slider. */
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
});

defineEmits(["clear", "search-all", "update:threshold", "assign"]);

const thresholdId = useId();

const showScopeNote = computed(
  () => !props.imagesLoading && !props.isAllPicturesActive,
);

const showSearchAll = computed(
  () => !props.imagesLoading && !props.isAllPicturesActive,
);

const showThreshold = computed(
  () => !props.imagesLoading && Number.isFinite(props.threshold),
);

const thresholdPercent = computed(() => Math.round(props.threshold * 100));

// The count is on the button, not "all": the blast radius of a bulk write has
// to be visible before the click, and it is what makes the slider legible.
const assignLabel = computed(() =>
  props.assignFromSelection
    ? `Assign ${props.assignCount} selected to ${props.assignTarget}`
    : `Assign ${props.assignCount} to ${props.assignTarget}`,
);
</script>

<style scoped>
.search-result-bar {
  position: absolute;
  bottom: 0;
  left: 0;
  width: 100%;
  z-index: 200;
  background-color: rgb(var(--v-theme-panel));
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-3) 72px var(--space-3) var(--space-5);
  box-shadow: 0 -2px 4px rgba(var(--v-theme-shadow), 0.1);
}

.search-result-status {
  display: inline-flex;
  align-items: center;
  gap: var(--space-3);
}

.search-result-spinner {
  flex: 0 0 auto;
}

.search-result-scope {
  color: rgba(var(--v-theme-on-panel), 0.6);
}

.search-result-threshold {
  display: inline-flex;
  align-items: center;
  gap: var(--space-3);
  /* Takes the slack in the bar so the slider has usable travel, but never
     squeezes the status text or the actions off the row. */
  flex: 1 1 180px;
  min-width: 140px;
  max-width: 320px;
}

.search-result-threshold-label {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), 0.7);
  white-space: nowrap;
}

.search-result-threshold-input {
  flex: 1;
  min-width: 0;
  accent-color: rgb(var(--v-theme-accent));
}

.search-result-threshold-input:focus-visible {
  box-shadow: var(--focus-ring);
  outline: none;
}

.search-result-threshold-value {
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-panel), 0.7);
  /* Reserved so the bar does not shift as the number changes width. */
  min-width: 4ch;
  text-align: right;
}

.search-result-actions {
  display: inline-flex;
  align-items: center;
  gap: var(--space-3);
}

.search-result-assign {
  /* Its own breathing room before Clear search: the two must not read as a
     pair of equals when one of them writes to the library. */
  margin-right: var(--space-3);
}
</style>
