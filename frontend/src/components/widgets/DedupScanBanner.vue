<script setup>
/**
 * The streaming "still scanning" banner above the duplicate queue.
 *
 * It exists so the queue never blocks on a full pass. Groups stream in as they
 * are found, and this banner is what makes a partial list honest: it says how
 * far the comparison has got, so a short list reads as "not finished yet"
 * rather than as "you have almost no duplicates".
 *
 * It removes itself the moment it stops being true, at 100 percent or as soon
 * as the scan leaves a running state. A banner that lingers at 99 percent after
 * a cancelled scan would be worse than no banner at all.
 */
import { computed } from "vue";

/** Statuses in which a scan is genuinely still comparing pictures. */
const RUNNING_STATES = new Set(["pending", "running"]);

const props = defineProps({
  /**
   * Normalised scan progress from `useDedupStore`:
   * `{ status, scanned, total, percent, buckets, totalBuckets, error }`.
   *
   * The server reports pictures and candidate buckets but no percentage and no
   * time estimate, so `percent` is derived in the store and there is
   * deliberately no "N min left": inventing one from a bucket rate would be a
   * guess presented as a fact, and a wrong estimate is worse than none.
   */
  scan: { type: Object, required: true },
});

/** Progress as a whole number in 0 to 100. Unknown progress reads as 0. */
const percent = computed(() => {
  const value = Number(props.scan?.percent);
  if (!Number.isFinite(value)) return 0;
  return Math.min(100, Math.max(0, Math.round(value)));
});

const visible = computed(
  () => RUNNING_STATES.has(props.scan?.status) && percent.value < 100,
);

/**
 * Tier 2 streams its groups in as each candidate bucket finishes, so a scope
 * whose picture total is not known yet still has honest progress to report.
 */
const countsPictures = computed(() => Number(props.scan?.total) > 0);
const bucketsText = computed(() =>
  Number(props.scan?.buckets ?? 0).toLocaleString(),
);
const totalBucketsText = computed(() =>
  Number(props.scan?.totalBuckets ?? 0).toLocaleString(),
);

const scannedText = computed(() =>
  Number(props.scan?.scanned ?? 0).toLocaleString(),
);
const totalText = computed(() =>
  Number(props.scan?.total ?? 0).toLocaleString(),
);
</script>

<template>
  <div v-if="visible" class="scan-banner">
    <v-icon class="scan-banner__ico" size="18">mdi-radar</v-icon>
    <p v-if="countsPictures" class="scan-banner__line">
      Still scanning. <b>{{ scannedText }}</b> of
      <b>{{ totalText }}</b> pictures compared. Groups appear here as they are
      found.
    </p>
    <p v-else class="scan-banner__line">
      Still scanning. <b>{{ bucketsText }}</b> of
      <b>{{ totalBucketsText }}</b> candidate batches compared. Groups appear
      here as they are found.
    </p>
    <span class="scan-banner__meta">{{ percent }}%</span>
    <span
      class="scan-banner__track"
      role="progressbar"
      aria-label="Duplicate scan progress"
      :aria-valuenow="percent"
      aria-valuemin="0"
      aria-valuemax="100"
    >
      <span class="scan-banner__fill" :style="{ width: `${percent}%` }"></span>
    </span>
  </div>
</template>

<style scoped>
/* A panel surface, not a notice: this is ambient status about the list below
   it, it is never dismissed, and it must not compete with the notice stack.
   `overflow: hidden` is safe here because the banner holds no focusable child
   whose ring could be clipped; it is what lets the track meet the bottom cap. */
.scan-banner {
  position: relative;
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: var(--space-3);
  margin-inline: var(--space-5);
  padding: var(--space-3);
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-panel));
  color: rgb(var(--v-theme-on-panel));
  box-shadow: var(--elevation-1);
  overflow: hidden;
}

.scan-banner__ico {
  color: rgb(var(--v-theme-on-panel));
  flex-shrink: 0;
}

.scan-banner__line {
  margin: 0;
  min-width: 0;
  font-size: var(--text-sm);
  line-height: var(--leading-snug);
}

/* The counts are the part the eye returns to, so they carry the weight, and
   tabular figures stop the sentence reflowing as the numbers tick up. */
.scan-banner__line b {
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
}

.scan-banner__meta {
  justify-self: end;
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-panel), 0.7);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

/* Pinned to the bottom edge and full width, so the banner itself is the meter.
   A separate inset bar would read as a second component. */
.scan-banner__track {
  position: absolute;
  inset-inline: 0;
  bottom: 0;
  height: var(--countdown-h);
  background: rgba(var(--v-theme-on-panel), 0.14);
}

.scan-banner__fill {
  display: block;
  height: 100%;
  background: rgb(var(--v-theme-accent));
  /* Progress arrives in polls, so the fill eases between them instead of
     snapping. The global reduced-motion rule collapses this. */
  transition: width var(--dur-2) var(--ease-standard);
}
</style>
