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
  padding: 10px 12px;
  border-radius: 8px;
  min-width: 220px;
  box-shadow: 0 4px 14px rgba(var(--v-theme-shadow), 0.28);
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
  font-size: 0.85em;
  margin-bottom: 6px;
  white-space: pre-line;
}

.progress-overlay__bar {
  width: 100%;
  height: 7px;
  background: rgba(var(--v-theme-on-dark-surface), 0.18);
  border-radius: 999px;
  overflow: hidden;
}

.progress-overlay__fill {
  height: 100%;
  background: rgb(var(--v-theme-accent));
  width: 0;
  transition: width 0.25s ease;
}

.progress-overlay__meta {
  margin-top: 6px;
  font-size: 0.8em;
  opacity: 0.85;
}

.progress-overlay__abort {
  margin-top: 10px;
  width: 100%;
  background: rgb(var(--v-theme-error));
  color: rgb(var(--v-theme-on-error));
  border: none;
  border-radius: 6px;
  padding: 6px 10px;
  font-size: 0.85em;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s ease;
}

.progress-overlay__abort:hover {
  background: rgba(var(--v-theme-error), 0.85);
}
</style>
