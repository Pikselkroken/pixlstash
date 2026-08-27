<script setup>
/**
 * ThumbnailUpgradeBanner - non-blocking progress banner for the one-off
 * thumbnail regeneration that runs on first launch after the v1.8.0 thumbnail
 * format change.
 *
 * The app is fully usable while this runs; the grid just shows old/degraded
 * tiles that sharpen as they rebuild. This banner is a slim, prominent,
 * dismissible in-app status bar - deliberately NOT the one-sentence notice
 * surface (useNoticeStore/NoticeHost), because it carries a determinate
 * progress bar.
 *
 * Data source: useTasksStore polls GET /workers/progress into per-worker
 * snapshots. The thumbnail-regeneration worker is keyed by its worker-type
 * value "ThumbnailGenerationTask" with shape
 *   { label, current, total, remaining, status, running, active }.
 * Regeneration is ACTIVE while `remaining > 0`; complete when it hits 0.
 *
 * Visibility: shown only while active. When it completes, a brief
 * "Thumbnails updated" beat plays, then the banner hides. Dismissal is
 * in-memory (this session only) - the Tasks tab still shows live progress.
 */
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useTasksStore } from "../../stores/useTasksStore";

// The worker-type value the backend keys the thumbnail-regen snapshot under.
const THUMBNAIL_WORKER_KEY = "ThumbnailGenerationTask";
// Outstanding thumbnails below which this is not an upgrade worth a banner.
//
// The worker is shared, and `remaining` is a library-wide count of
// `thumbnail_width IS NULL` - so an in-place rotate, which NULLs exactly the
// pictures it turned to re-queue their bitmaps, lights up the same row as the
// one-off v1.8.0 regeneration did. A determinate progress bar reading "12,070 /
// 12,073" because someone turned three photos is noise, and the rotate already
// shows itself on the tiles it is turning.
const BULK_BACKLOG_THRESHOLD = 5;
// How long the "Thumbnails updated" success beat lingers before the banner hides.
const SUCCESS_BEAT_MS = 2600;

const emit = defineEmits(["view-progress"]);

const tasksStore = useTasksStore();

// The live snapshot for the thumbnail-regen worker (or null when absent - the
// steady state, once the one-off upgrade has finished and the row is gone).
const snapshot = computed(
  () => tasksStore.workerSnapshots?.[THUMBNAIL_WORKER_KEY] || null,
);

const total = computed(() => {
  const value = Number(snapshot.value?.total);
  return Number.isFinite(value) && value > 0 ? value : 0;
});

const current = computed(() => {
  const value = Number(snapshot.value?.current);
  return Number.isFinite(value) && value > 0 ? value : 0;
});

// Prefer the backend's own `remaining`; fall back to total - current so the
// banner still reasons correctly if that field is ever absent.
const remaining = computed(() => {
  const value = Number(snapshot.value?.remaining);
  if (Number.isFinite(value)) return Math.max(0, value);
  return Math.max(0, total.value - current.value);
});

const percent = computed(() =>
  total.value > 0 ? Math.round((current.value / total.value) * 100) : 0,
);

// Engaged when the backlog is genuinely bulk, released only when it empties.
//
// A LATCH rather than a plain threshold, because the two ends of a job need
// different answers: a handful outstanding at the START is a rotate and must
// never raise the banner, while a handful outstanding at the END is the tail of
// a real upgrade and must not make it vanish at 99.9% - or, worse, declare
// "Thumbnails updated" with work still running.
const bulkEngaged = ref(false);
watch(
  remaining,
  (value) => {
    if (value > BULK_BACKLOG_THRESHOLD) bulkEngaged.value = true;
    else if (value === 0) bulkEngaged.value = false;
  },
  { immediate: true },
);

// Regeneration is running while there is anything left to rebuild - and worth
// showing only once it was ever more than a handful.
const isActive = computed(() => remaining.value > 0 && bulkEngaged.value);

// ── Visibility state machine ──────────────────────────────────────────────
// dismissed: in-memory only (this session). This component stays mounted for
// the app's lifetime, so the ref persists without touching localStorage.
const dismissed = ref(false);
// True only during the brief success beat after a real regeneration finishes.
const showSuccess = ref(false);
// Guards the success beat: we only celebrate completion if we actually saw the
// worker active first, so a cold start in steady state never flashes "updated".
const sawActive = ref(false);
let successTimer = null;

function clearSuccessTimer() {
  if (successTimer) {
    clearTimeout(successTimer);
    successTimer = null;
  }
}

watch(
  isActive,
  (active, wasActive) => {
    if (active) {
      sawActive.value = true;
      showSuccess.value = false;
      clearSuccessTimer();
    } else if (wasActive && sawActive.value) {
      // Just crossed from active → done: play the success beat, then hide.
      showSuccess.value = true;
      clearSuccessTimer();
      successTimer = setTimeout(() => {
        showSuccess.value = false;
      }, SUCCESS_BEAT_MS);
    }
  },
  { immediate: true },
);

onBeforeUnmount(clearSuccessTimer);

const visible = computed(() => {
  if (dismissed.value) return false;
  if (isActive.value) return true;
  return showSuccess.value;
});

// Display mode: 'done' locks the readout to a completed 100% state.
const isDone = computed(() => !isActive.value && showSuccess.value);
const displayPercent = computed(() => (isDone.value ? 100 : percent.value));

const numberFormatter = new Intl.NumberFormat();
const currentLabel = computed(() =>
  numberFormatter.format(isDone.value ? total.value : current.value),
);
const totalLabel = computed(() => numberFormatter.format(total.value));
const showCounts = computed(() => total.value > 0);

function dismiss() {
  dismissed.value = true;
  showSuccess.value = false;
  clearSuccessTimer();
}

function viewProgress() {
  emit("view-progress");
}
</script>

<template>
  <Transition name="tub">
    <div
      v-if="visible"
      class="tub-banner"
      :class="{ 'tub-banner--done': isDone }"
      role="status"
      aria-live="polite"
    >
      <v-icon
        v-if="isDone"
        class="tub-icon tub-icon--done"
        size="18"
        aria-hidden="true"
        >mdi-check-circle-outline</v-icon
      >
      <v-icon v-else class="tub-icon" size="18" aria-hidden="true"
        >mdi-image-sync-outline</v-icon
      >

      <span class="tub-label">{{
        isDone ? "Thumbnails updated" : "Upgrading thumbnails"
      }}</span>

      <div
        class="tub-track"
        role="progressbar"
        aria-label="Thumbnail upgrade progress"
        :aria-valuenow="displayPercent"
        aria-valuemin="0"
        aria-valuemax="100"
      >
        <div class="tub-fill" :style="{ width: `${displayPercent}%` }"></div>
      </div>

      <span class="tub-percent">{{ displayPercent }}%</span>

      <span v-if="showCounts" class="tub-counts"
        >{{ currentLabel }} / {{ totalLabel }}</span
      >

      <button
        v-if="!isDone"
        type="button"
        class="tub-view"
        @click="viewProgress"
      >
        View progress
      </button>

      <button
        type="button"
        class="tub-dismiss"
        aria-label="Dismiss thumbnail upgrade banner"
        @click="dismiss"
      >
        <v-icon size="16" aria-hidden="true">mdi-close</v-icon>
      </button>
    </div>
  </Transition>
</template>

<style scoped>
.tub-banner {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-3) var(--space-5);
  background: rgb(var(--v-theme-panel));
  color: rgb(var(--v-theme-on-panel));
  border-bottom: 1px solid rgb(var(--v-theme-border));
  box-shadow: var(--elevation-1);
  font-size: var(--text-sm);
  line-height: var(--leading-snug);
  /* Header-like bar at the top of the content column: sits over the grid that
     scrolls beneath it, well below notices (5000) and the title bar (4500). */
  position: relative;
  z-index: var(--z-sticky);
}

.tub-icon {
  flex: none;
  color: rgb(var(--v-theme-primary));
}

.tub-icon--done {
  color: rgb(var(--v-theme-success));
}

.tub-label {
  flex: none;
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-on-panel));
}

.tub-track {
  flex: 1 1 auto;
  min-width: 80px;
  max-width: 320px;
  height: 6px;
  border-radius: var(--radius-pill);
  background: rgba(var(--v-theme-on-panel), 0.14);
  overflow: hidden;
}

.tub-fill {
  height: 100%;
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-primary));
  transition: width var(--dur-2) var(--ease-standard);
}

.tub-banner--done .tub-fill {
  background: rgb(var(--v-theme-success));
}

.tub-percent {
  flex: none;
  min-width: 3ch;
  text-align: right;
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
}

.tub-counts {
  flex: none;
  color: rgba(var(--v-theme-on-panel), 0.6);
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
}

.tub-view {
  flex: none;
  margin-left: auto;
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  color: rgb(var(--v-theme-primary));
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  font-family: inherit;
}

.tub-view:hover {
  background: rgba(var(--v-theme-primary), 0.1);
}

/* When there is no "View progress" button (the done beat), the dismiss button
   still needs to push to the right edge. */
.tub-banner--done .tub-dismiss {
  margin-left: auto;
}

.tub-dismiss {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border-radius: var(--radius-sm);
  color: rgba(var(--v-theme-on-panel), 0.6);
}

.tub-dismiss:hover {
  background: rgba(var(--v-theme-on-panel), 0.08);
  color: rgb(var(--v-theme-on-panel));
}

/* Enter/leave: a calm slide-and-fade. Reduced motion is honoured globally by
   design-tokens.css, which forces near-zero durations. */
.tub-enter-active {
  transition:
    opacity var(--dur-3) var(--ease-decelerate),
    transform var(--dur-3) var(--ease-decelerate);
}

.tub-leave-active {
  transition:
    opacity var(--dur-2) var(--ease-accelerate),
    transform var(--dur-2) var(--ease-accelerate);
}

.tub-enter-from,
.tub-leave-to {
  opacity: 0;
  transform: translateY(-6px);
}

/* Hide the exact-count readout when the bar gets tight, so the label, bar and
   percentage stay legible. */
@media (max-width: 640px) {
  .tub-counts {
    display: none;
  }
}
</style>
