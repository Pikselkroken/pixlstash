<template>
  <div
    v-if="visible"
    class="progress-overlay"
    :class="[
      `progress-overlay--${anchor}`,
      { 'progress-overlay--error': hasFailed },
    ]"
    :aria-busy="isRunning || undefined"
  >
    <div class="progress-overlay__title">
      <!-- A glyph and a word, so failure is not carried by the red card alone.
           Same rule DedupWhyPills states: colour may reinforce a state, never
           be the only thing encoding it. -->
      <v-icon v-if="hasFailed" class="progress-overlay__state-ico" size="16"
        >mdi-alert-circle</v-icon
      >
      <span>
        <span v-if="stateWord" class="progress-overlay__state-word"
          >{{ stateWord }}. </span
        >{{ message }}
      </span>
    </div>
    <div
      class="progress-overlay__bar"
      role="progressbar"
      :aria-label="message || 'Progress'"
      :aria-valuenow="indeterminate ? null : Math.round(percent)"
      aria-valuemin="0"
      aria-valuemax="100"
    >
      <div
        class="progress-overlay__fill"
        :class="{ 'progress-overlay__fill--indeterminate': indeterminate }"
        :style="{ width: `${percent}%` }"
      ></div>
    </div>
    <!-- aria-hidden: the same numbers reach a screen reader through the
         progressbar's aria-valuenow and the announcement below. Left visible
         to assistive tech they would be read again on every tick. -->
    <div v-if="total != null" class="progress-overlay__meta" aria-hidden="true">
      {{ count }} / {{ total }}
    </div>
    <button
      v-if="abortLabel && !isTerminal"
      class="progress-overlay__abort"
      type="button"
      @click="emit('abort')"
    >
      {{ abortLabel }}
    </button>
    <span
      class="visually-hidden"
      :role="hasFailed ? 'alert' : 'status'"
      aria-live="polite"
      aria-atomic="true"
      >{{ announcement }}</span
    >
  </div>
</template>

<script setup>
/**
 * ProgressOverlay
 *
 * A shared progress bar overlay used for both export and plugin progress.
 *
 * Props:
 *   visible    - Whether the overlay is shown.
 *   status     - Current status string (idle, running, completed, failed, cancelled, queued, ...).
 *   message    - Title text.
 *   percent    - Progress percentage (0-100).
 *   count      - Processed/current item count (optional).
 *   total      - Total item count (optional).
 *   abortLabel - Label for the abort button. No button rendered if falsy.
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
const hasFailed = computed(() => props.status === "failed");
const isRunning = computed(() => props.visible && !isTerminal.value);

/** The word shown beside the glyph for a state colour alone must not carry. */
const STATE_WORDS = {
  failed: "Failed",
  cancelled: "Cancelled",
  completed: "Done",
};
const stateWord = computed(() => STATE_WORDS[props.status] ?? "");

/**
 * What a screen reader hears. Deliberately coarse.
 *
 * The live region is `aria-atomic`, so it re-reads in full every time its text
 * changes. Announcing each percent would make a multi-GB move unusable: the
 * reader would talk continuously and drown out everything else on the page.
 * Quartiles give the "periodic progress" the issue asks for while leaving the
 * exact figure available on demand through the progressbar's `aria-valuenow`.
 */
const announcement = computed(() => {
  if (!props.visible) return "";
  const subject = props.message || "Progress";
  if (isTerminal.value) {
    return `${subject}. ${STATE_WORDS[props.status] ?? props.status}.`;
  }
  if (props.indeterminate) return `${subject}. Working.`;
  const quartile = Math.floor((props.percent || 0) / 25) * 25;
  return `${subject}. ${quartile} percent.`;
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

.progress-overlay__title {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  font-size: var(--text-sm);
  margin-bottom: var(--space-2);
  white-space: pre-line;
}

.progress-overlay__state-ico {
  flex: none;
  margin-top: 1px;
}

.progress-overlay__state-word {
  font-weight: var(--weight-semibold);
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

/* A sliding bar is exactly the kind of continuous motion that triggers
   vestibular symptoms, and this one can run for the whole length of a
   multi-GB move. Hold it still and let it fill the track instead, so "busy"
   is still visible without animating. */
@media (prefers-reduced-motion: reduce) {
  .progress-overlay__fill {
    transition: none;
  }

  .progress-overlay__fill--indeterminate {
    width: 100% !important;
    animation: none;
    opacity: 0.55;
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
</style>
