<template>
  <div v-if="visible" class="selection-bar-overlay">
    <div class="selection-bar-content">
      <div class="selection-bar-left">
        <button
          class="restore-btn"
          :disabled="restoreDisabled"
          @click="$emit('restore-scrapheap')"
        >
          Restore All
        </button>
        <!-- Active retention policy, stated where the action happens. Hidden
             until the policy is known so it never shows a guessed window. -->
        <p v-if="retentionLabel" class="retention-note">
          <v-icon size="14" class="retention-note__icon"
            >mdi-delete-clock-outline</v-icon
          >
          <span>Auto-empty: {{ retentionLabel }}</span>
          <span class="retention-note__sep" aria-hidden="true">·</span>
          <button
            type="button"
            class="retention-note__change"
            :aria-label="`Change the auto-empty window (currently ${retentionLabel})`"
            @click="$emit('open-retention-settings')"
          >
            change
          </button>
        </p>
      </div>
      <div class="selection-bar-actions">
        <button
          class="delete-btn"
          :disabled="disabled"
          @click="$emit('empty-scrapheap')"
        >
          Empty Scrapheap
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * Scrapheap action bar: restore-all / empty, plus the active auto-empty policy.
 *
 * Presentational only - the parent (ImageGrid) owns the retention value and the
 * navigation to Settings, so this component never touches the store or the API.
 */
import { VIcon } from "vuetify/components";

defineProps({
  visible: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  restoreDisabled: { type: Boolean, default: false },
  /**
   * Human label for the active auto-empty window, e.g. "30 days" / "Never".
   * Empty string hides the policy line (policy not loaded yet).
   */
  retentionLabel: { type: String, default: "" },
});

defineEmits([
  "empty-scrapheap",
  "restore-scrapheap",
  "open-retention-settings",
]);
</script>

<style scoped>
.selection-bar-overlay {
  position: absolute !important;
  left: 0;
  top: var(--selbar-height, 48px);
  width: 100%;
  z-index: 100;
  background: rgba(var(--v-theme-background), 0.95);
  padding: 0 var(--space-3) var(--space-2);
  margin: 0;
  height: 30px;
  box-sizing: border-box;
  display: flex;
  align-items: center;
}
.selection-bar-content {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}
.selection-bar-left {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  min-width: 0;
}
.selection-bar-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-left: auto;
  flex-wrap: nowrap;
}
/* Active-policy line: quiet status text, not a control, with one inline link-
   styled button through to the setting that owns it. */
.retention-note {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin: 0;
  min-width: 0;
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-background), 0.7);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.retention-note__icon {
  flex-shrink: 0;
  opacity: 0.75;
}

.retention-note__sep {
  color: rgba(var(--v-theme-on-background), 0.4);
}

.retention-note__change {
  padding: 0;
  font: inherit;
  color: rgb(var(--v-theme-accent));
  text-decoration: underline;
  border-radius: var(--radius-sm);
}

.retention-note__change:hover {
  filter: brightness(1.15);
}

.restore-btn {
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
  padding: var(--space-1) var(--space-3);
  border-radius: var(--radius-sm);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  line-height: 1.4;
  white-space: nowrap;
}
.restore-btn:hover {
  filter: brightness(1.3);
}
.restore-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.delete-btn {
  background: rgb(var(--v-theme-error));
  color: rgb(var(--v-theme-on-error));
  padding: var(--space-1) var(--space-4);
  border-radius: var(--radius-sm);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  line-height: 1.4;
  white-space: nowrap;
}
.delete-btn:hover {
  filter: brightness(1.3);
}
.delete-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
