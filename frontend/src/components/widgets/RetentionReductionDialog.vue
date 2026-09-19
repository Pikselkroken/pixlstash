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
 * Same `AppDialog` chrome as `DeleteForeverDialog.vue`, so it carries no new
 * visual vocabulary.
 */
import AppButton from "./AppButton.vue";
import AppDialog from "./AppDialog.vue";

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

// Escape, the scrim and the close button all route here. While the save is in
// flight Cancel is disabled, so the other ways out are too.
function onClose() {
  if (props.busy) return;
  requestCancel();
}

function requestConfirm() {
  if (props.busy) return;
  emit("confirm");
}
</script>

<template>
  <!-- Destructive: no @accept, Enter never schedules the deletion. -->
  <AppDialog
    :open="open"
    role="alertdialog"
    :title="props.title"
    size="sm"
    :persistent="busy"
    @close="onClose"
  >
    <p class="retention-body">{{ props.body }}</p>

    <div v-if="props.warning" class="purge-warn">
      <v-icon size="18">{{
        props.unverified ? "mdi-help-circle-outline" : "mdi-alert-outline"
      }}</v-icon>
      <span>{{ props.warning }}</span>
    </div>

    <template #footer>
      <AppButton
        variant="secondary"
        autofocus
        :disabled="busy"
        @click="requestCancel"
      >
        Cancel
      </AppButton>
      <AppButton variant="danger" :loading="busy" @click="requestConfirm">
        {{ props.confirmLabel }}
      </AppButton>
    </template>
  </AppDialog>
</template>

<style scoped>
.retention-body {
  font-size: var(--text-md);
  color: rgba(var(--v-theme-on-surface), 0.8);
  margin: 0;
}

/* Irreversibility panel - `error`-tinted, matching `.ref-warn` in
   DeleteForeverDialog: in both cases the user is about to lose files. */
.purge-warn {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  border: 1px solid rgba(var(--v-theme-surface-error), 0.5);
  background: rgba(var(--v-theme-error), 0.08);
  border-radius: var(--radius-md);
  padding: var(--space-4);
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-snug);
  color: rgb(var(--v-theme-surface-error));
}
</style>
