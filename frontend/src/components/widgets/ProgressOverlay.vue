<template>
  <div
    v-if="visible"
    class="progress-overlay"
    :class="[
      `progress-overlay--${anchor}`,
      { 'progress-overlay--error': status === 'failed' },
    ]"
  >
    <div class="progress-overlay__title">{{ message }}</div>
    <div class="progress-overlay__bar">
      <div
        class="progress-overlay__fill"
        :class="{ 'progress-overlay__fill--indeterminate': indeterminate }"
        :style="{ width: `${percent}%` }"
      ></div>
    </div>
    <div v-if="total != null" class="progress-overlay__meta">
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
  border: none;
  border-radius: var(--radius-sm);
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  cursor: pointer;
  transition: background var(--dur-2) var(--ease-standard);
}

.progress-overlay__abort:hover {
  background: rgba(var(--v-theme-error), 0.85);
}
</style>
