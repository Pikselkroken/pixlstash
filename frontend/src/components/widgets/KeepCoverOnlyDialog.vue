<script setup>
/**
 * The one consent for collapsing stacks to their covers.
 *
 * Usage:
 *   <KeepCoverOnlyDialog
 *     :open="keepCoverOpen"
 *     :preview="keepCoverPreview"
 *     :loading="keepCoverLoading"
 *     :preview-failed="keepCoverPreviewFailed"
 *     :busy="keepCoverBusy"
 *     @close="keepCoverOpen = false"
 *     @confirm="runKeepCoverOnly"
 *   />
 *
 * Presentational: the parent owns the preview, the real run and the API.
 *
 * Three properties of this dialog are load-bearing and are spelled out where
 * they are implemented (`docs/design/keep-cover-only.md`):
 *
 *   * **The title says what you keep; the button says what you lose.** The
 *     headline figure and the button label render from ONE computed value
 *     (`picturesMoving`), not merely from one endpoint, so they can never
 *     disagree. That is the failure the neighbouring auto-stack dialog shipped
 *     when it reported "62 stacks to create" for work that would create 3.
 *   * **Nothing is freed.** The originals-deleted-from-disk zero is stated out
 *     loud, and the byte figure is a sentence rather than a figure block,
 *     because a soft delete frees nothing until the Scrapheap is emptied, and
 *     with the default retention it never empties on its own.
 *   * **No type-to-confirm.** That gate belongs to `DeleteForeverDialog`, where
 *     an on-disk original dies. This is a recoverable soft delete, one op-log
 *     batch and one Ctrl+Z; borrowing the heavier ceremony would flatten the
 *     distinction between "recoverable" and "gone".
 */
import { computed, nextTick, ref, watch } from "vue";
import AppDialog from "./AppDialog.vue";
import AppButton from "./AppButton.vue";
import {
  KEEP_COVER_ONLY_ICON_NAME,
  UNKNOWN_FIGURE,
  keepCoverOnlyBytesSentence,
  keepCoverOnlyConfirmLabel,
  keepCoverOnlyRetentionSentence,
  keepCoverOnlySkipReasons,
  keepCoverOnlySkippedCount,
  keepCoverOnlyTitle,
} from "../../utils/keepCoverOnly";

const props = defineProps({
  open: { type: Boolean, default: false },
  /**
   * The dry run from `POST /stacks/keep-cover-only/preview`.
   *
   * Every figure this dialog renders comes out of this one body, because the
   * server derives the whole report from one read over the same selection
   * through the same planner the mutation uses. Do not add a prop fed by a
   * second request, and do not derive a bucket by subtracting one of these
   * figures from another: the stack buckets are disjoint and already sum to
   * `stacks_selected`.
   */
  preview: { type: Object, default: null },
  /** True while the dry run is in flight. */
  loading: { type: Boolean, default: false },
  /**
   * True when the dry run could not be read. Without this a failed preview and
   * a genuinely empty one are the same screen: a zero over a live confirm
   * button, which says "there is nothing to collapse" when the truth is
   * "nobody was able to ask".
   */
  previewFailed: { type: Boolean, default: false },
  /** True while the real run is in flight. */
  busy: { type: Boolean, default: false },
});

const emit = defineEmits(["close", "confirm"]);

/**
 * True while no figure from the server may be shown.
 *
 * One flag for both states on purpose: a stale number from the previous
 * selection is exactly as wrong as an invented one.
 */
const figuresUnknown = computed(
  () => props.loading || props.previewFailed || !props.preview,
);

/**
 * THE number: pictures that would move to the Scrapheap.
 *
 * `null` means "not known yet", which is a different thing from zero and has to
 * render differently. Both the headline block and the confirm button are
 * derived from this single ref below, which is the whole point: two readings of
 * the same endpoint can still drift; two readings of the same computed cannot.
 */
const picturesMoving = computed(() => {
  if (figuresUnknown.value) return null;
  return Number(props.preview.pictures_moving) || 0;
});

/** Stacks that would actually collapse: the figure the title names. */
const stacksEligible = computed(() => {
  if (figuresUnknown.value) return null;
  return Number(props.preview.stacks_eligible) || 0;
});

/** The headline numeral, or the en dash placeholder at the same size. */
const headlineFigure = computed(() =>
  picturesMoving.value === null
    ? UNKNOWN_FIGURE
    : picturesMoving.value.toLocaleString(),
);

const title = computed(() => keepCoverOnlyTitle(stacksEligible.value));
const confirmLabel = computed(() =>
  keepCoverOnlyConfirmLabel(picturesMoving.value),
);

// The rows are declared once so their order is fixed and matches the design's,
// and so neither the metadata row nor the disk row can be dropped by a later
// edit: the first is the whole reason collapsing is safe, and stating the
// second's zero out loud is the point of showing it.
const ROWS = [
  { key: "stacksCollapsed", icon: "mdi-layers-minus", label: "Stacks collapsed" },
  { key: "coversKept", icon: "mdi-image-outline", label: "Covers kept" },
  {
    key: "coversGainingMetadata",
    icon: "mdi-tag-multiple-outline",
    label: "Covers gaining metadata from copies",
  },
  { key: "stacksSkipped", icon: "mdi-cancel", label: "Stacks skipped" },
  {
    key: "originalsDeleted",
    icon: "mdi-delete-off-outline",
    label: "Originals deleted from disk",
  },
];

/**
 * One row's value.
 *
 * While the dry run is in flight every server-derived row shows an en dash
 * rather than a spinner, so the dialog keeps its height and the confirm button
 * does not move out from under the pointer when the counts land.
 */
function rowValue(key) {
  // Not a number the server has to be asked for: this action has no path to
  // disk at all. Stating the zero out loud, in every state, is the row's job,
  // exactly as the auto-stack dialog states its own "Files deleted: 0".
  if (key === "originalsDeleted") return "0";
  if (figuresUnknown.value) return UNKNOWN_FIGURE;
  const p = props.preview;
  if (key === "stacksCollapsed") return (stacksEligible.value ?? 0).toLocaleString();
  if (key === "coversKept") return (Number(p.covers_kept) || 0).toLocaleString();
  if (key === "coversGainingMetadata") {
    return (Number(p.covers_gaining_metadata) || 0).toLocaleString();
  }
  if (key === "stacksSkipped") {
    return keepCoverOnlySkippedCount(p).toLocaleString();
  }
  return UNKNOWN_FIGURE;
}

const skipReasons = computed(() =>
  figuresUnknown.value ? [] : keepCoverOnlySkipReasons(props.preview),
);

/**
 * The recovery window, read from the response.
 *
 * `scrapheap_retention_days` is `null` on a default install, which means the
 * Scrapheap never empties on its own. Hardcoding a window here would be the
 * same class of error the whole dialog exists to avoid, so this branches on the
 * live value and renders nothing until it has one.
 */
const retentionSentence = computed(() =>
  figuresUnknown.value
    ? ""
    : keepCoverOnlyRetentionSentence(props.preview.scrapheap_retention_days),
);

/**
 * What the copies hold on disk. A sentence, never a figure block: a figure is
 * for what changes now, and nothing is reclaimed until the Scrapheap is emptied.
 */
const bytesSentence = computed(() =>
  figuresUnknown.value
    ? ""
    : keepCoverOnlyBytesSentence(props.preview.bytes_held_by_copies),
);

const referenceFolderCount = computed(() =>
  figuresUnknown.value
    ? 0
    : Number(props.preview.reference_folder_pictures_moving) || 0,
);

const canConfirm = computed(
  () => !props.busy && !figuresUnknown.value && picturesMoving.value > 0,
);

// ── The keyboard, deliberately inverted ─────────────────────────────────────
// `AppDialog` implements the app's dialog contract: Escape dismisses, plain
// Enter accepts. This dialog opts OUT of the second half, and it must stay
// opted out.
//
// It does not listen for `accept`, so a bare Enter reaches nothing; and Cancel
// takes focus on open, which puts the key on a native button, where AppDialog's
// own ENTER_EXEMPT rule hands it to the button's default activation. So Enter
// cancels. That is the point: users arrive here straight from the duplicate
// queue with Enter under their finger from the verdict keys, and the very next
// press must not be consent for hundreds of deletions.
//
// If a later change "fixes" this by adding an @accept handler or by focusing
// the confirm button, it reintroduces exactly that hazard. Do not.
const cancelRef = ref(null);

watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) return;
    // After the dialog's own enter transition has mounted the footer.
    nextTick(() => {
      cancelRef.value?.focus?.();
    });
  },
);
</script>

<template>
  <AppDialog :open="open" :title="title" :width="520" @close="emit('close')">
    <p class="kco-lede">
      Each stack keeps its cover. Every other picture in it moves to the
      Scrapheap, where you can restore it. A stack collapses whole, even if you
      only picked some of its pictures, and loose pictures are left alone.
    </p>

    <p v-if="previewFailed" class="kco-failed" role="status">
      <v-icon size="16" class="kco-icon">mdi-alert-outline</v-icon>
      The preview could not be read, so these counts are unknown. Close this and
      try again.
    </p>

    <!-- The headline. One instance of the figure, and the confirm button below
         renders the same computed value. The numeral is `on-surface`, never
         `error`: a 22px red number would be the loudest object in the app. The
         hue rides the leading rail instead. -->
    <div class="kco-headline">
      <p class="kco-figure" data-testid="keep-cover-figure">
        {{ headlineFigure }}
      </p>
      <p class="kco-figure-label">pictures move to the Scrapheap</p>
    </div>

    <dl class="kco-rows">
      <div v-for="row in ROWS" :key="row.key" class="kco-row">
        <dt class="kco-term">
          <v-icon size="16" class="kco-icon">{{ row.icon }}</v-icon>
          {{ row.label }}
        </dt>
        <dd class="kco-value">{{ rowValue(row.key) }}</dd>
      </div>
    </dl>

    <ul v-if="skipReasons.length" class="kco-skips">
      <li v-for="reason in skipReasons" :key="reason.key">{{ reason.text }}</li>
    </ul>

    <!-- Info-tinted, not error-tinted: recovery is the reassuring half, and
         DeleteForeverDialog's lock note already made this call for the same
         reason. -->
    <div class="kco-recovery">
      <v-icon size="18" class="kco-icon">mdi-backup-restore</v-icon>
      <div class="kco-recovery-body">
        <p v-if="retentionSentence" class="kco-sentence">
          {{ retentionSentence }}
        </p>
        <p v-if="bytesSentence" class="kco-sentence">{{ bytesSentence }}</p>
        <p v-if="referenceFolderCount > 0" class="kco-sentence">
          {{ referenceFolderCount.toLocaleString() }} of them live in a
          reference folder; those rows move but the files stay exactly where
          they are.
        </p>
      </div>
    </div>

    <p class="kco-reversible">
      <v-icon size="16" class="kco-icon">mdi-information-outline</v-icon>
      Reversible as one step with <kbd>Ctrl</kbd>+<kbd>Z</kbd>.
    </p>

    <template #footer>
      <AppButton ref="cancelRef" variant="ghost" @click="emit('close')">
        Cancel
      </AppButton>
      <AppButton
        variant="danger"
        :icon-left="KEEP_COVER_ONLY_ICON_NAME"
        :disabled="!canConfirm"
        :loading="busy"
        @click="emit('confirm')"
      >
        {{ confirmLabel }}
      </AppButton>
    </template>
  </AppDialog>
</template>

<style scoped>
.kco-lede {
  margin: 0;
  font-size: var(--text-base);
  line-height: var(--leading-body);
  color: rgba(var(--v-theme-on-surface), 0.8);
}

/* Same construction as the auto-stack dialog's in-place warning, so a failure
   reads as one family across the two sibling confirms. */
.kco-failed {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  margin: var(--space-5) 0 0;
  padding: var(--space-3);
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-warning), 0.12);
  border: 1px solid rgba(var(--v-theme-warning), 0.35);
  font-size: var(--text-xs);
  line-height: var(--leading-body);
  color: rgb(var(--v-theme-on-surface));
}

/* The leading status rail carries the consequence's hue so the numeral does
   not have to. `--rail-w`, not a raw 3px. */
.kco-headline {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  margin: var(--space-6) 0 0;
  padding-left: var(--space-4);
  border-left: var(--rail-w) solid rgb(var(--v-theme-error));
}

.kco-figure {
  margin: 0;
  font-size: var(--text-xl);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-tight);
  font-variant-numeric: tabular-nums;
  color: rgb(var(--v-theme-on-surface));
}

.kco-figure-label {
  margin: 0;
  font-size: var(--text-sm);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-surface), 0.8);
}

.kco-rows {
  margin: var(--space-5) 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
}

.kco-row {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: baseline;
  gap: var(--space-4);
  padding: var(--space-3) 0;
  border-bottom: 1px solid rgb(var(--v-theme-divider));
}

.kco-row:last-child {
  border-bottom: none;
}

.kco-term {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), 0.8);
}

.kco-icon {
  color: rgba(var(--v-theme-on-surface), 0.6);
  flex-shrink: 0;
}

.kco-value {
  margin: 0;
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
  color: rgb(var(--v-theme-on-surface));
}

.kco-skips {
  margin: var(--space-4) 0 0;
  padding-left: var(--space-5);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  font-size: var(--text-xs);
  line-height: var(--leading-body);
  color: rgba(var(--v-theme-on-surface), 0.8);
}

.kco-recovery {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  margin: var(--space-5) 0 0;
  padding: var(--space-4);
  border: 1px solid rgba(var(--v-theme-info), 0.5);
  background: rgba(var(--v-theme-info), 0.08);
  border-radius: var(--radius-md);
  color: rgb(var(--v-theme-on-surface));
}

.kco-recovery-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.kco-sentence {
  margin: 0;
  font-size: var(--text-sm);
  line-height: var(--leading-snug);
}

.kco-reversible {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin: var(--space-5) 0 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.kco-reversible kbd {
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  padding: var(--space-1) var(--space-2);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.3);
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-surface), 0.08);
}
</style>
