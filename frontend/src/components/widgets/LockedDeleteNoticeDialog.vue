<script setup>
/**
 * "Some (or all) of what you tried to delete is frozen by a locked set."
 *
 * Presentational only: no API calls, no store, no router. The parent decides
 * when to open it and supplies the already-built message (see
 * `utils/lockedDelete.js`, which owns the wording and the counts).
 *
 * Two situations, one surface:
 *  - the parent refused to send a delete it knew would do nothing (all locked);
 *  - the delete ran and the server reported `skipped_locked` (partial).
 *
 * Why a dialog and not a toast: the app has no mounted notice host yet
 * (`useNoticeStore` is a headless scaffold), and a message that never renders is
 * the exact bug this fixes. The card mirrors `DeleteForeverDialog.vue` so it
 * carries no new visual vocabulary.
 */
const props = defineProps({
  open: { type: Boolean, default: false },
  /** Short headline, e.g. "Nothing was deleted". */
  title: { type: String, default: "" },
  /** What happened, with counts. */
  body: { type: String, default: "" },
  /** How to change the outcome — always names unlocking the set. */
  hint: { type: String, default: "" },
});

const emit = defineEmits(["close", "update:open"]);

function requestClose() {
  emit("close");
  emit("update:open", false);
}

// Vuetify emits update:model-value(false) for Escape and scrim clicks; both mean
// "dismiss". There is nothing destructive here, so dismissing is always safe.
function onModelValue(value) {
  if (!value) requestClose();
}
</script>

<template>
  <v-dialog
    :model-value="open"
    max-width="420"
    @update:model-value="onModelValue"
  >
    <div class="notice" role="alertdialog" :aria-label="props.title">
      <h4>{{ props.title }}</h4>
      <p>{{ props.body }}</p>

      <div v-if="props.hint" class="lock-hint">
        <div class="lock-hint-head">
          <v-icon size="18">mdi-lock-outline</v-icon>
          <span>{{ props.hint }}</span>
        </div>
      </div>

      <div class="row">
        <button
          type="button"
          class="btn btn-quiet"
          autofocus
          @click="requestClose"
        >
          Got it
        </button>
      </div>
    </div>
  </v-dialog>
</template>

<style scoped>
/* Same card as DeleteForeverDialog — standard dialog pattern, tokenized. */
.notice {
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  border-radius: var(--radius-lg);
  box-shadow: var(--elevation-4);
  padding: var(--space-7);
}

.notice h4 {
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-tight);
  margin: 0 0 var(--space-3);
}

.notice p {
  font-size: var(--text-md);
  color: rgba(var(--v-theme-on-surface), 0.8);
  margin: 0 0 var(--space-5);
}

.notice .row {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-3);
}

/* The lever. Mirrors DeleteForeverDialog's `.ref-warn` panel shape, but tinted
   `info` rather than `error`: nothing was destroyed, the user just needs to know
   what to do next. */
.lock-hint {
  display: block;
  margin: 0 0 var(--space-6);
  border: 1px solid rgba(var(--v-theme-info), 0.5);
  background: rgba(var(--v-theme-info), 0.08);
  border-radius: var(--radius-md);
  padding: var(--space-4);
}

.lock-hint-head {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  font-size: var(--text-sm);
  line-height: var(--leading-snug);
  color: rgb(var(--v-theme-on-surface));
}

.btn {
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  padding: var(--space-3) var(--space-5);
  border-radius: var(--radius-sm);
  border: 1px solid transparent;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
}

.btn:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

.btn-quiet {
  background: rgb(var(--v-theme-cancel-button));
  color: rgb(var(--v-theme-cancel-button-text));
}
</style>
