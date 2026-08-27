<script setup>
/**
 * Confirm shortening the scrapheap auto-empty window.
 *
 * Presentational only: no API calls, no store, no router. The parent fetches the
 * impact, builds the copy (`buildRetentionReductionMessage` in
 * `utils/retention.js`), and owns the save.
 *
 * Why this exists: lowering the window schedules permanent, unrecoverable
 * deletion from a single dropdown pick - Never → 30 can destroy a long-lived
 * scrapheap - while the delete-forever dialog beside it demands a typed word to
 * destroy far less. No type-to-confirm here though: this schedules deletion a
 * grace period out rather than destroying immediately, so a normal confirm is
 * proportionate.
 *
 * The card mirrors `DeleteForeverDialog.vue` so it carries no new visual
 * vocabulary.
 */
const props = defineProps({
  open: { type: Boolean, default: false },
  /** Headline, e.g. "Shorten the auto-empty window?". */
  title: { type: String, default: "" },
  /** What will happen, with the count and when deletion starts. */
  body: { type: String, default: "" },
  /** The irreversibility warning. */
  warning: { type: String, default: "" },
  /** Affirmative button label, e.g. "Change to 30 days" / "Change anyway". */
  confirmLabel: { type: String, default: "Confirm" },
  /**
   * True when the impact could NOT be read from the server. The user is
   * proceeding on an unverified basis, so the panel says so rather than
   * implying a checked number.
   */
  unverified: { type: Boolean, default: false },
  /** Save in flight - disables the actions. */
  busy: { type: Boolean, default: false },
});

const emit = defineEmits(["confirm", "cancel", "update:open"]);

function requestCancel() {
  emit("cancel");
  emit("update:open", false);
}

// Vuetify emits update:model-value(false) for Escape and scrim clicks; both are
// cancel. The parent closes the dialog on confirm, so we don't self-close.
function onModelValue(value) {
  if (!value) requestCancel();
}

function requestConfirm() {
  if (props.busy) return;
  emit("confirm");
}
</script>

<template>
  <v-dialog
    :model-value="open"
    max-width="440"
    @update:model-value="onModelValue"
  >
    <div class="confirm" role="alertdialog" :aria-label="props.title">
      <h4>{{ props.title }}</h4>
      <p>{{ props.body }}</p>

      <div v-if="props.warning" class="purge-warn">
        <v-icon size="18">{{
          props.unverified ? "mdi-help-circle-outline" : "mdi-alert-outline"
        }}</v-icon>
        <span>{{ props.warning }}</span>
      </div>

      <div class="row">
        <button
          type="button"
          class="btn btn-quiet"
          autofocus
          :disabled="busy"
          @click="requestCancel"
        >
          Cancel
        </button>
        <button
          type="button"
          class="btn btn-danger"
          :disabled="busy"
          @click="requestConfirm"
        >
          <v-progress-circular v-if="busy" indeterminate size="16" width="2" />
          <template v-else>{{ props.confirmLabel }}</template>
        </button>
      </div>
    </div>
  </v-dialog>
</template>

<style scoped>
/* Same card as DeleteForeverDialog - standard dialog pattern, tokenized. */
.confirm {
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  border-radius: var(--radius-lg);
  box-shadow: var(--elevation-4);
  padding: var(--space-7);
}

.confirm h4 {
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-tight);
  margin: 0 0 var(--space-3);
}

.confirm p {
  font-size: var(--text-md);
  color: rgba(var(--v-theme-on-surface), 0.8);
  margin: 0 0 var(--space-5);
}

.confirm .row {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-3);
}

/* Irreversibility panel - `error`-tinted, matching `.ref-warn` in
   DeleteForeverDialog: in both cases the user is about to lose files. */
.purge-warn {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  margin: 0 0 var(--space-6);
  border: 1px solid rgba(var(--v-theme-error), 0.5);
  background: rgba(var(--v-theme-error), 0.08);
  border-radius: var(--radius-md);
  padding: var(--space-4);
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-snug);
  color: rgb(var(--v-theme-error));
}

.btn {
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  padding: var(--space-3) var(--space-5);
  border-radius: var(--radius-sm);
  border: 1px solid transparent;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
}

.btn:disabled {
  opacity: 0.38;
  cursor: default;
}

.btn-quiet {
  background: rgb(var(--v-theme-cancel-button));
  color: rgb(var(--v-theme-cancel-button-text));
}

.btn-danger {
  background: rgba(var(--v-theme-error), 0.1);
  border: 1px solid rgba(var(--v-theme-error), 0.55);
  color: rgb(var(--v-theme-error));
}
</style>
