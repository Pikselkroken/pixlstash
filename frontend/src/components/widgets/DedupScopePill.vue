<script setup>
/**
 * The dismissible pill that says the duplicate queue is showing one scope: a
 * project, a set, a character, or a folder.
 *
 * A filtered list that does not say it is filtered is how a user concludes they
 * have no duplicates left when they are looking at one folder's worth. The pill
 * states the scope, and dismissing it is the way back to the whole library.
 *
 * The dismiss control's label says what dismissing DOES, not what the glyph
 * looks like: "Show duplicates in the whole library" is actionable out of
 * context, "Close" is not.
 */
import { computed } from "vue";

const props = defineProps({
  /** The scope's name, as the user knows it. */
  label: { type: String, required: true },
  /** MDI glyph for the scope's kind. */
  icon: { type: String, default: "mdi-folder-multiple-image" },
  /** Pictures in scope. `null` when the count is not known. */
  count: { type: Number, default: null },
});

defineEmits(["dismiss"]);

/** Rendered only when the caller actually has a number, never as a "0". */
const countText = computed(() =>
  Number.isFinite(props.count) ? props.count.toLocaleString() : "",
);
</script>

<template>
  <span class="scope-pill" :title="label">
    <v-icon class="scope-pill__ico" size="14">{{ icon }}</v-icon>
    <span class="scope-pill__label">{{ label }}</span>
    <span v-if="countText" class="scope-pill__count">{{ countText }}</span>
    <button
      type="button"
      class="scope-pill__dismiss"
      aria-label="Show duplicates in the whole library"
      @click="$emit('dismiss')"
    >
      <v-icon size="14">mdi-close</v-icon>
    </button>
  </span>
</template>

<style scoped>
/* An accent tint rather than a solid accent fill: the pill is a standing state,
   not a call to action, and the label has to stay `on-surface` body text. */
.scope-pill {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-2) var(--space-2) var(--space-3);
  border-radius: var(--radius-pill);
  background: rgba(var(--v-theme-accent), 0.14);
  border: 1px solid rgba(var(--v-theme-accent), 0.4);
  font-size: var(--text-sm);
  line-height: var(--leading-snug);
  color: rgb(var(--v-theme-on-surface));
  max-width: 100%;
  /* The containing block for the clipped label at the narrow end (below),
     which would otherwise anchor to whatever positioned ancestor it found. */
  position: relative;
}

.scope-pill__ico {
  color: rgb(var(--v-theme-on-surface));
  flex-shrink: 0;
}

.scope-pill__label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.scope-pill__count {
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface), 0.7);
  flex-shrink: 0;
}

.scope-pill__dismiss {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  padding: var(--space-1);
  border-radius: var(--radius-pill);
  color: rgb(var(--v-theme-on-surface));
}
.scope-pill__dismiss:hover {
  background: var(--hover-wash);
}

/* ── Shared toolbar collapse (docs/design/toolbar-responsive-decisions.md).
   The pill never folds while a scope is active - a filtered list that does
   not say it is filtered is the bug this pill exists to prevent - so on a
   narrow bar it compresses to the kind icon + dismiss, the full label
   surviving as the pill's tooltip.

   CLIPPED, not `display: none` (amendment #4): a tooltip reaches neither a
   screen reader nor a touch user, and the scope's name is the whole point of
   the pill. Clipping keeps the name in the accessibility tree at every
   width. ──────────────────────────────────────────────────────────────── */
@container toolbar (max-width: 820px) {
  .scope-pill__label,
  .scope-pill__count {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    clip-path: inset(50%);
    white-space: nowrap;
  }
}
</style>
