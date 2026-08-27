<template>
  <div
    v-if="visible"
    class="progress-overlay"
    :class="[
      `progress-overlay--${anchor}`,
      { 'progress-overlay--error': isFailed },
    ]"
    :aria-busy="!isTerminal"
  >
    <div v-if="isFailed" class="progress-overlay__failed">
      <v-icon size="16">mdi-alert-circle</v-icon>
      <span>Failed</span>
    </div>
    <div class="progress-overlay__title">{{ message }}</div>
    <div
      class="progress-overlay__bar"
      role="progressbar"
      :aria-label="message || 'Progress'"
      :aria-valuenow="indeterminate ? null : Math.round(clampedPercent)"
      aria-valuemin="0"
      aria-valuemax="100"
    >
      <div
        class="progress-overlay__fill"
        :class="{ 'progress-overlay__fill--indeterminate': indeterminate }"
        :style="{ width: `${clampedPercent}%` }"
      ></div>
    </div>
    <div v-if="total != null" class="progress-overlay__meta">
      {{ count }} / {{ total }}
    </div>
    <button
      v-if="abortLabel"
      class="progress-overlay__abort"
      type="button"
      @click="emit('abort')"
    >
      {{ abortLabel }}
    </button>
  </div>
  <!-- Outside the `v-if` on purpose: a live region inserted at the same moment
       as its first text is not reliably announced, so the run's opening line
       would be the one that goes missing. -->
  <p
    class="visually-hidden"
    role="status"
    aria-live="polite"
    aria-atomic="true"
  >
    {{ announcement }}
  </p>
</template>

<script setup>
/**
 * ProgressOverlay
 *
 * A shared progress bar overlay used for export, plugin and smart-score progress.
 *
 * Accessibility: the bar is a real `role="progressbar"` (value omitted while
 * indeterminate, per ARIA), the card carries `aria-busy` until it reaches a
 * terminal status, and a visually-hidden live region announces start, coarse
 * progress, completion and failure. Failure also gets a glyph and a word, not
 * only the red card.
 *
 * Props:
 *   visible    - Whether the overlay is shown.
 *   status     - Current status string (idle, running, completed, failed, cancelled, queued, ...).
 *   message    - Title text.
 *   percent    - Progress percentage (0-100).
 *   count      - Processed/current item count (optional).
 *   total      - Total item count (optional).
 *   abortLabel - Label for the card's one button. No button rendered if falsy.
 *                The label alone gates it, terminal status included, so a card
 *                held up to report a failure can carry its own dismissal. A
 *                caller that wants no button at the end nulls the label, which
 *                the export already does.
 *   anchor     - 'top' | 'bottom'. Controls vertical position.
 *   indeterminate - When true, show animated indeterminate progress.
 *
 * Emits:
 *   abort - When the abort button is clicked.
 */
import { computed } from "vue";

const props = defineProps({
  visible: { type: Boolean, default: false },
  status: { type: String, default: "idle" },
  message: { type: String, default: "" },
  percent: { type: Number, default: 0 },
  count: { type: Number, default: null },
  total: { type: Number, default: null },
  abortLabel: { type: String, default: null },
  anchor: { type: String, default: "bottom" },
  indeterminate: { type: Boolean, default: false },
});

const emit = defineEmits(["abort"]);

const TERMINAL_STATUSES = new Set(["completed", "failed", "cancelled"]);
const isTerminal = computed(() => TERMINAL_STATUSES.has(props.status));
const isFailed = computed(() => props.status === "failed");

/**
 * The percentage both the bar and the live region are allowed to state.
 *
 * ARIA requires `aria-valuenow` to sit inside min/max, and a caller that hands
 * over a NaN (a division by a total that has not arrived yet) must not make the
 * overlay announce "NaN% complete". Every current caller already clamps, so
 * this is the guard for the next one.
 */
const clampedPercent = computed(() => {
  const raw = Number(props.percent);
  if (!Number.isFinite(raw)) return 0;
  return Math.min(100, Math.max(0, raw));
});

/**
 * What the live region says.
 *
 * Deliberately coarse while running: the card's own percent and `count / total`
 * tick once per item, and a live region repeating "41 of 12000" on every tick
 * buries the start, the finish and the failure it exists to announce. Rounding
 * to tens announces roughly ten times over a run instead.
 *
 * A terminal status is read out even once the card is hidden. Callers routinely
 * settle the status and drop `visible` in the same tick (the export's two cancel
 * paths do), so gating the terminal branches on `visible` would end those runs
 * in silence, which is the exact failure this region exists to prevent. The
 * stale text only sits in the DOM; a live region announces a change, not a
 * presence, so nothing is re-read until the next run moves it.
 */
const announcement = computed(() => {
  // Every branch below appends its own sentence, so a message that already ends
  // one produces "…stayed put.: failed." The shelf's held failure card carries
  // a whole receipt as its title, and "Moving model files…" has an ellipsis for
  // the same reason; both read as one sentence once the tail comes off.
  const label = (props.message || "").replace(/[.…\s]+$/u, "") || "Progress";
  if (props.status === "failed") return `${label}: failed.`;
  if (props.status === "cancelled") return `${label}: cancelled.`;
  if (props.status === "completed") return `${label}: complete.`;
  if (!props.visible) return "";
  if (props.indeterminate) return `${label}: working.`;
  return `${label}: ${Math.floor(clampedPercent.value / 10) * 10}% complete.`;
});
</script>

<style scoped>
.progress-overlay {
  position: absolute;
  right: 12px;
  z-index: 120;
  background: rgba(var(--v-theme-dark-surface), 0.85);
  color: rgb(var(--v-theme-on-dark-surface));
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  min-width: 220px;
  box-shadow: var(--elevation-3);
  backdrop-filter: blur(6px);
}

.progress-overlay--top {
  top: 10px;
}

.progress-overlay--bottom {
  bottom: 88px;
}

.progress-overlay--error {
  background: rgba(var(--v-theme-error), 0.95);
}

/* Failure never rides on the red card alone (WCAG 1.4.1), the same rule
   DedupWhyPills states: a glyph and the word carry it in monochrome too. */
.progress-overlay__failed {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-2);
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.progress-overlay__title {
  font-size: var(--text-sm);
  margin-bottom: var(--space-2);
  white-space: pre-line;
}

.progress-overlay__bar {
  width: 100%;
  height: 7px;
  background: rgba(var(--v-theme-on-dark-surface), 0.18);
  border-radius: var(--radius-pill);
  overflow: hidden;
}

.progress-overlay__fill {
  height: 100%;
  background: rgb(var(--v-theme-accent));
  width: 0;
  transition: width var(--dur-3) var(--ease-standard);
}

.progress-overlay__fill--indeterminate {
  width: 38% !important;
  animation: progress-overlay-indeterminate 1.2s ease-in-out infinite;
  transition: none;
}

@keyframes progress-overlay-indeterminate {
  0% {
    transform: translateX(-120%);
  }
  50% {
    transform: translateX(90%);
  }
  100% {
    transform: translateX(220%);
  }
}

/* Not a no-op: the bar still has to read as "running, length unknown", so the
   sliding fill parks at its start offset rather than disappearing. */
@media (prefers-reduced-motion: reduce) {
  .progress-overlay__fill {
    transition: none;
  }

  .progress-overlay__fill--indeterminate {
    animation: none;
    transform: translateX(0);
  }
}

.progress-overlay__meta {
  margin-top: var(--space-2);
  font-size: var(--text-xs);
  opacity: 0.85;
}

.progress-overlay__abort {
  margin-top: var(--space-3);
  width: 100%;
  background: rgb(var(--v-theme-error));
  color: rgb(var(--v-theme-on-error));
  border-radius: var(--radius-sm);
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  transition: background var(--dur-2) var(--ease-standard);
}

.progress-overlay__abort:hover {
  background: rgba(var(--v-theme-error), 0.85);
}

/* On the failed card the button's own error fill sits on an error background
   and all but disappears - which would hide the only way out of a card that is
   held until it is dismissed. A wash of the card's own ink delineates it, and
   the ink itself is the pair the theme already contrast-checks. */
.progress-overlay--error .progress-overlay__abort {
  background: rgba(var(--v-theme-on-error), 0.16);
  color: rgb(var(--v-theme-on-error));
}

.progress-overlay--error .progress-overlay__abort:hover {
  background: rgba(var(--v-theme-on-error), 0.28);
}
</style>
