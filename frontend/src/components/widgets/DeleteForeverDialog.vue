<script setup>
/**
 * Scrapheap "Delete forever" confirmation dialog.
 *
 * Presentational only: no API calls, no store, no router. The parent drives it
 * from an AUTHORITATIVE server preview (POST /pictures/scrapheap/delete-preview)
 * - never from the virtualized grid - so the protected-original file list and
 * counts are complete regardless of virtualization / grid_lite. The parent owns
 * the DELETE request and closes the dialog once it completes.
 *
 * Two shapes, chosen by `protectedCount`:
 *  - protectedCount === 0 → standard single "Delete forever" + Cancel.
 *  - protectedCount  >  0 → the three-way protected flow: the protected on-disk
 *    originals are previewed, a type-to-confirm word gates "Delete all", and the
 *    user may instead delete only the unprotected subset (leaving the originals).
 *
 * Emits `confirm` with `{ includeProtected: boolean }`:
 *  - true  → DELETE with include_protected:true  (destroys the protected originals on disk)
 *  - false → DELETE with include_protected:false (purges only the unprotected subset)
 */
import { computed, ref, watch } from "vue";
import {
  buildLockedPurgeNote,
  deleteForeverDestroyCounts,
} from "../../utils/lockedDelete.js";

const props = defineProps({
  open: { type: Boolean, default: false },
  /** Total pictures the deletion targets (authoritative). */
  totalCount: { type: Number, default: 0 },
  /** Reference-folder originals in the set whose on-disk file would be destroyed. */
  protectedCount: { type: Number, default: 0 },
  /** Non-protected (managed) pictures in the set. */
  unprotectedCount: { type: Number, default: 0 },
  /**
   * Pictures a locked picture set freezes. NEITHER action destroys these, so
   * the dialog states it up front instead of letting the user discover it from
   * a count that does not add up. Defaults to 0, so a server that has not
   * shipped `locked_count` yet renders exactly the previous copy.
   */
  lockedCount: { type: Number, default: 0 },
  /**
   * Absolute on-disk file paths of the protected originals. May be the full list
   * or a capped subset - `protectedCount` is authoritative for the "+N more".
   */
  protectedPaths: { type: Array, default: () => [] },
  /** DELETE request in flight - disables the actions and shows loading. */
  busy: { type: Boolean, default: false },
});

const emit = defineEmits(["confirm", "cancel", "update:open"]);

// The exact word the user must type to unlock destroying protected originals.
const CONFIRM_WORD = "DELETE";
const confirmInput = ref("");

// Reset the type-to-confirm each time the dialog opens.
watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) confirmInput.value = "";
  },
);

const hasProtected = computed(() => props.protectedCount > 0);
const typedOk = computed(() => confirmInput.value.trim() === CONFIRM_WORD);

// ── Locked-set pictures ─────────────────────────────────────────────────────
// A locked set freezes its pictures against every mutation, purge included, so
// they survive both actions below.
const hasLocked = computed(() => props.lockedCount > 0);
const lockedNote = computed(() => buildLockedPurgeNote(props.lockedCount));

// What each action ACTUALLY destroys. The preview's locked / protected /
// unprotected buckets are disjoint and sum to `totalCount`, so these are sums of
// the server's own classification - never `totalCount` minus something. See
// `deleteForeverDestroyCounts`.
const destroyCounts = computed(() =>
  deleteForeverDestroyCounts({
    protectedCount: props.protectedCount,
    unprotectedCount: props.unprotectedCount,
    lockedCount: props.lockedCount,
  }),
);

// ── Copy ────────────────────────────────────────────────────────────────────
const standardBody = computed(() => {
  // No protected originals in this branch, so "delete all" and "delete
  // unprotected only" are the same act on the same pictures.
  const count = destroyCounts.value.deleteAll;
  const many = count !== 1;
  const noun = many ? "pictures" : "picture";
  const file = many ? "their files" : "its file";
  return `This permanently deletes ${count} ${noun}, including ${file} on disk. This cannot be undone.`;
});

const protectedBody = computed(() => {
  const p = props.protectedCount;
  // The headline count is what "Delete all" destroys (protected + unprotected),
  // NOT `totalCount` - locked pictures are counted in the total but survive.
  const count = destroyCounts.value.deleteAll;
  const isAre =
    p === 1 ? "is a reference-folder original" : "are reference-folder originals";
  const itThem = p === 1 ? "it" : "them";
  const fileFiles = p === 1 ? "file" : "files";
  return `This permanently deletes ${count} pictures. ${p} of ${itThem} ${isAre} - deleting ${itThem} also destroys the original ${fileFiles} on disk. This cannot be undone.`;
});

const refWarnTitle = computed(() =>
  props.protectedCount === 1
    ? "This also deletes your original photo on disk:"
    : "This also deletes your original photos on disk:",
);

// Show every path we were given; the scroll container caps the height. When the
// server capped the list, `protectedCount` exceeds it - surface the remainder.
const moreCount = computed(() =>
  Math.max(0, props.protectedCount - props.protectedPaths.length),
);
const moreText = computed(
  () =>
    `+ ${moreCount.value} more protected original ${moreCount.value === 1 ? "file" : "files"}`,
);

const typeHint = computed(
  () =>
    `Type ${CONFIRM_WORD} to permanently destroy the ${props.protectedCount} protected original${props.protectedCount === 1 ? "" : "s"}.`,
);

const deleteAllLabel = computed(
  () => `Delete all - incl. ${props.protectedCount} protected`,
);
// `unprotectedCount` is already the exact destroyable figure: the preview's
// buckets are disjoint (locked-first), so a locked picture is never counted as
// unprotected and no subtraction is needed.
const deleteUnprotectedLabel = computed(
  () =>
    `Delete unprotected only (${destroyCounts.value.deleteUnprotectedOnly})`,
);

// ── Actions ───────────────────────────────────────────────────────────────────
function requestCancel() {
  emit("cancel");
  emit("update:open", false);
}

// Vuetify emits update:model-value(false) for Escape and scrim (outside) clicks;
// both are cancel. The parent closes the dialog on confirm, so we don't self-close.
function onModelValue(value) {
  if (!value) requestCancel();
}

function confirmStandard() {
  // No protected originals - include_protected is moot; false purges everything.
  emit("confirm", { includeProtected: false });
}
function confirmDeleteAll() {
  if (!typedOk.value || props.busy) return;
  emit("confirm", { includeProtected: true });
}
function confirmDeleteUnprotected() {
  if (props.unprotectedCount === 0 || props.busy) return;
  emit("confirm", { includeProtected: false });
}
</script>

<template>
  <v-dialog
    :model-value="open"
    max-width="440"
    @update:model-value="onModelValue"
  >
    <div class="confirm" role="alertdialog" aria-label="Delete forever">
      <h4>Delete forever?</h4>

      <!-- Pictures a locked set freezes: destroyed by neither action below. -->
      <div v-if="hasLocked" class="lock-note">
        <v-icon size="18">mdi-lock-outline</v-icon>
        <span>{{ lockedNote }}</span>
      </div>

      <!-- ── Standard case: no protected originals ─────────────────────────── -->
      <template v-if="!hasProtected">
        <p>{{ standardBody }}</p>
        <div class="row">
          <button type="button" class="btn btn-quiet" @click="requestCancel">
            Cancel
          </button>
          <button
            type="button"
            class="btn btn-danger"
            :disabled="busy"
            @click="confirmStandard"
          >
            <v-progress-circular
              v-if="busy"
              indeterminate
              size="16"
              width="2"
            />
            <template v-else>Delete forever</template>
          </button>
        </div>
      </template>

      <!-- ── Protected case: three-way confirmation ────────────────────────── -->
      <template v-else>
        <p>{{ protectedBody }}</p>

        <div class="ref-warn">
          <div class="ref-warn-head">
            <v-icon size="18">mdi-alert-outline</v-icon>
            <span>{{ refWarnTitle }}</span>
          </div>
          <ul class="ref-warn-paths">
            <li v-for="path in protectedPaths" :key="path">{{ path }}</li>
            <li v-if="moreCount > 0" class="ref-warn-more">{{ moreText }}</li>
          </ul>
        </div>

        <label class="type-confirm">
          <span class="type-confirm-hint">{{ typeHint }}</span>
          <input
            v-model="confirmInput"
            class="type-confirm-input"
            type="text"
            autocomplete="off"
            autocapitalize="off"
            spellcheck="false"
            :placeholder="CONFIRM_WORD"
            :aria-label="`Type ${CONFIRM_WORD} to confirm`"
          />
        </label>

        <div class="row row--wrap">
          <button type="button" class="btn btn-quiet" @click="requestCancel">
            Cancel
          </button>
          <button
            type="button"
            class="btn btn-outline"
            :disabled="unprotectedCount === 0 || busy"
            @click="confirmDeleteUnprotected"
          >
            {{ deleteUnprotectedLabel }}
          </button>
          <button
            type="button"
            class="btn btn-danger"
            :disabled="!typedOk || busy"
            @click="confirmDeleteAll"
          >
            <v-progress-circular
              v-if="busy"
              indeterminate
              size="16"
              width="2"
            />
            <template v-else>{{ deleteAllLabel }}</template>
          </button>
        </div>
      </template>
    </div>
  </v-dialog>
</template>

<style scoped>
/* Confirm dialog (Delete forever) - standard dialog pattern, tokenized. */
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

.confirm .row--wrap {
  flex-wrap: wrap;
}

/* Locked-set note. Deliberately `info`-tinted, NOT `error`: nothing is at risk
   here - these pictures survive. Same panel shape as `.ref-warn` below. */
.lock-note {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  margin: 0 0 var(--space-5);
  border: 1px solid rgba(var(--v-theme-info), 0.5);
  background: rgba(var(--v-theme-info), 0.08);
  border-radius: var(--radius-md);
  padding: var(--space-4);
  font-size: var(--text-sm);
  line-height: var(--leading-snug);
  color: rgb(var(--v-theme-on-surface));
}

/* Reference-folder original warning - strong, specific: this is your photo on
   disk, not just a library entry. error-tinted panel, tokenized. */
.ref-warn {
  display: block;
  margin: 0 0 var(--space-5);
  border: 1px solid rgba(var(--v-theme-error), 0.5);
  background: rgba(var(--v-theme-error), 0.08);
  border-radius: var(--radius-md);
  padding: var(--space-4);
}

.ref-warn-head {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-snug);
  color: rgb(var(--v-theme-error));
}

.ref-warn-paths {
  list-style: none;
  margin: var(--space-3) 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  /* Scrollable so a large protected set never blows out the dialog. */
  max-height: 40vh;
  overflow-y: auto;
}

.ref-warn-paths li {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-on-surface));
  background: rgba(var(--v-theme-on-surface), 0.06);
  border-radius: var(--radius-sm);
  padding: var(--space-1) var(--space-2);
  word-break: break-all;
}

.ref-warn-paths li.ref-warn-more {
  font-family: var(--font-ui);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.65);
  background: none;
  padding: 0;
}

/* Type-to-confirm - the typed guard is required only when protected originals
   are in the set. */
.type-confirm {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: 0 0 var(--space-6);
}

.type-confirm-hint {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.65);
  line-height: var(--leading-snug);
}

.type-confirm-input {
  font: inherit;
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-input-text));
  background: rgb(var(--v-theme-input-background));
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  padding: var(--space-2) var(--space-3);
  letter-spacing: 0.08em;
}

.type-confirm-input:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
  border-color: rgba(var(--v-theme-accent), 0.55);
}

/* Action buttons - quiet cancel, outline unprotected-only, destructive delete-all. */
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
  /* The named token, same value: it is the one state the system lets fade. */
  opacity: var(--opacity-disabled);
  cursor: default;
}

.btn-quiet {
  background: rgb(var(--v-theme-cancel-button));
  color: rgb(var(--v-theme-cancel-button-text));
}

.btn-outline {
  border: 1px solid rgb(var(--v-theme-border));
  color: rgba(var(--v-theme-on-surface), 0.85);
}

.btn-danger {
  background: rgba(var(--v-theme-error), 0.1);
  border: 1px solid rgba(var(--v-theme-error), 0.55);
  color: rgb(var(--v-theme-error));
}
</style>
