<script setup>
/**
 * The one consent for bulk-stacking the exact-match tier.
 *
 * Usage:
 *   <DedupAutoStackDialog
 *     :open="autoStackOpen"
 *     :preview="dryRun"
 *     :loading="dryRunLoading"
 *     :busy="autoStackRunning"
 *     @close="autoStackOpen = false"
 *     @confirm="runAutoStack"
 *   />
 *
 * Byte-identical files need no judgment, so this dialog replaces per-group
 * adjudication for the exact tier: one dry run, one confirmation, one undo
 * step. The component is presentational: the parent owns the dry run, the real
 * run and the API.
 */
import { computed } from "vue";
import AppDialog from "./AppDialog.vue";
import AppButton from "./AppButton.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  /**
   * The dry-run report from `POST /dedup/auto-stack`:
   * `{ dry_run, groups, pictures, scope, dry_run_summary, results, failures }`.
   * `results` is empty for a dry run, by design.
   *
   * Every number this dialog renders about the run comes out of
   * `dry_run_summary` - `{ groups, groups_by_tier, pictures,
   * covers_gaining_tags, covers_gaining_score, covers_gaining_metadata }` -
   * because the server derives the whole summary from one read of one group
   * list, so its rows cannot disagree with each other the way two separate
   * counts across a running scan can. The top-level `groups` / `pictures` are
   * only used as a fallback for a server that predates the summary.
   */
  preview: { type: Object, default: null },
  /**
   * Unresolved groups in every tier this bulk action does NOT touch, from the
   * counts endpoint's per-tier split.
   *
   * This one deliberately does not come from `dry_run_summary`. That summary's
   * `groups_by_tier` counts the groups **this run would act on** (exact-only,
   * zero-filled for the rest), so reading the queue's remainder out of it would
   * render a confident zero. The remainder is a property of the queue, not of
   * the run, and only `POST /dedup/counts` knows it.
   */
  queueRemaining: { type: Number, default: 0 },
  /** True while the dry run is in flight. */
  loading: { type: Boolean, default: false },
  /**
   * True when the dry run could not be read. Without this a failed preview and
   * a genuinely empty one are the same screen: a column of zeroes and a
   * disabled button, which says "there is nothing to stack" when the truth is
   * "nobody was able to ask".
   */
  previewFailed: { type: Boolean, default: false },
  /** True while the real run is in flight. */
  busy: { type: Boolean, default: false },
});

const emit = defineEmits(["close", "confirm"]);

// The rows are declared once so the order is fixed and matches the design's,
// and so neither the "Covers gaining metadata" row nor the "Files deleted" row
// can be dropped by a later edit: the first is the whole reason the union is
// safe, and stating the second's zero out loud is the point of showing it.
const ROWS = [
  {
    key: "groups",
    icon: "mdi-layers-plus",
    label: "Stacks to create",
  },
  {
    key: "pictures",
    icon: "mdi-image-multiple-outline",
    label: "Pictures collapsed behind a cover",
  },
  {
    key: "coversGainingMetadata",
    icon: "mdi-tag-multiple-outline",
    label: "Covers gaining metadata from copies",
  },
  {
    key: "queueRemaining",
    icon: "mdi-blur",
    label: "Groups left in the queue to review",
  },
  {
    key: "filesDeleted",
    icon: "mdi-delete-off-outline",
    label: "Files deleted",
  },
];

/**
 * The dry run's own aggregates, or null for a server that predates them.
 *
 * One read of one group list on the server, so the rows below are internally
 * consistent even if a scan lands between this request and the next.
 */
const summary = computed(() => props.preview?.dry_run_summary ?? null);

/**
 * Groups this run would stack.
 *
 * Read from the summary's per-tier split, which is the same number the
 * summary's other rows were derived alongside. Auto-stack is exact-only, so the
 * split is the exact tier plus zeroes; summing it rather than naming `exact`
 * keeps the dialog correct if the run ever widens.
 */
const stacksToCreate = computed(() => {
  const byTier = summary.value?.groups_by_tier;
  if (byTier && typeof byTier === "object") {
    return Object.values(byTier).reduce(
      (sum, count) => sum + (Number(count) || 0),
      0,
    );
  }
  return Number(summary.value?.groups ?? props.preview?.groups) || 0;
});

const picturesCollapsed = computed(
  () => Number(summary.value?.pictures ?? props.preview?.pictures) || 0,
);

/**
 * Covers that would gain a tag or a better score from the union.
 *
 * The design's "covers gaining metadata from copies" row, and the answer to the
 * only real question a bulk stack raises: whether collapsing copies loses
 * anything. It cannot, because the union runs in the other direction, and this
 * number says how often it actually will.
 */
const coversGainingMetadata = computed(
  () => Number(summary.value?.covers_gaining_metadata) || 0,
);

const canConfirm = computed(
  () =>
    !props.loading &&
    !props.busy &&
    !props.previewFailed &&
    stacksToCreate.value > 0,
);

/**
 * The number a row shows.
 *
 * While the dry run is in flight every row shows an en dash rather than a
 * spinner, so the dialog keeps its height and nothing moves under the pointer
 * when the counts land.
 */
function rowValue(key) {
  // Deletion is a property of the feature, not a number the server reports:
  // there is no destructive route on this surface at all in 1.9. Stating the
  // zero out loud is the point of the row.
  if (key === "filesDeleted") return "0";
  // The queue's remainder comes from the counts endpoint, not the dry run, so
  // it stays readable while the dry run is still in flight.
  if (key === "queueRemaining") {
    return (Number(props.queueRemaining) || 0).toLocaleString();
  }
  if (props.loading || props.previewFailed) return "–";
  if (key === "groups") return stacksToCreate.value.toLocaleString();
  if (key === "pictures") return picturesCollapsed.value.toLocaleString();
  if (key === "coversGainingMetadata") {
    return coversGainingMetadata.value.toLocaleString();
  }
  return "0";
}
</script>

<template>
  <AppDialog
    :open="open"
    title="Auto-stack exact matches"
    :width="520"
    @close="emit('close')"
  >
    <p class="as-lede">
      Byte-identical files need no judgment. This groups every exact-match set
      behind one cover, the largest and best-tagged copy of each. Every other
      copy stays in your library, and every tag, character and set membership
      moves onto the stack. Near-duplicates stay in the queue for you to review.
    </p>

    <p v-if="previewFailed" class="as-failed" role="status">
      <v-icon size="16" class="as-icon">mdi-alert-outline</v-icon>
      The preview could not be read, so these counts are unknown. Close this and
      try again.
    </p>

    <dl class="as-rows">
      <div v-for="row in ROWS" :key="row.key" class="as-row">
        <dt class="as-term">
          <v-icon size="16" class="as-icon">{{ row.icon }}</v-icon>
          {{ row.label }}
        </dt>
        <dd class="as-value">{{ rowValue(row.key) }}</dd>
      </div>
    </dl>

    <p class="as-reversible">
      <v-icon size="16" class="as-icon">mdi-information-outline</v-icon>
      Reversible as one step with <kbd>Ctrl</kbd>+<kbd>Z</kbd>.
    </p>

    <template #footer>
      <AppButton variant="ghost" @click="emit('close')">Cancel</AppButton>
      <AppButton
        variant="primary"
        icon-left="layers-plus"
        :disabled="!canConfirm"
        @click="emit('confirm')"
      >
        Create {{ stacksToCreate.toLocaleString() }} stacks
      </AppButton>
    </template>
  </AppDialog>
</template>

<style scoped>
.as-lede {
  margin: 0;
  font-size: var(--text-base);
  line-height: var(--leading-body);
  color: rgba(var(--v-theme-on-surface), 0.8);
}

/* Same construction as the tier menu's in-place warning, so a failure and a
   caution read as one family rather than two components. */
.as-failed {
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

.as-rows {
  margin: var(--space-5) 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
}

.as-row {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: baseline;
  gap: var(--space-4);
  padding: var(--space-3) 0;
  border-bottom: 1px solid rgb(var(--v-theme-divider));
}

.as-row:last-child {
  border-bottom: none;
}

.as-term {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), 0.8);
}

.as-icon {
  color: rgba(var(--v-theme-on-surface), 0.6);
  flex-shrink: 0;
}

.as-value {
  margin: 0;
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
  color: rgb(var(--v-theme-on-surface));
}

.as-reversible {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin: var(--space-5) 0 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.as-reversible kbd {
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  padding: var(--space-1) var(--space-2);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.3);
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-surface), 0.08);
}
</style>
