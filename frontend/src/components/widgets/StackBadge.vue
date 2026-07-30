<template>
  <button
    v-if="visible"
    type="button"
    class="sbadge"
    :class="{ 'sbadge--unresolved': unresolved }"
    :title="title"
    :aria-label="title"
    data-testid="stack-badge"
    @click.stop="emit('activate')"
  >
    <v-icon class="sbico" size="14">{{ glyph }}</v-icon>
    <span class="sbcount">{{ label }}</span>
  </button>
</template>

<script setup>
// The corner badge that says "this tile is more than one picture".
//
// It replaces the hover-only layers glyph with a count that is readable without
// touching the mouse, because the number is the thing the user is actually
// deciding on, and a badge that only exists on hover cannot be scanned.
//
// It carries two states, and keeping them visually apart is the whole job:
//
//   * stacked: the stack exists, and the count is a fact.
//   * unresolved: the group is still sitting in the duplicates queue. It is a
//     suggestion, so it is quieter (muted foreground, no accent) and the count
//     wears a question mark. A suggestion that looks like a fact would have the
//     user believing pictures were already merged when nothing has happened.
//
// Purely presentational: it reports the click and the parent decides whether
// that means "expand the stack" or "jump to this group in the queue".

import { computed } from "vue";

const props = defineProps({
  /** How many pictures this tile stands for. Below 2 there is nothing to say. */
  count: { type: Number, default: 0 },
  /** True while the group is still a queue suggestion rather than a stack. */
  unresolved: { type: Boolean, default: false },
});

const emit = defineEmits(["activate"]);

const visible = computed(() => props.count >= 2);

const glyph = computed(() =>
  props.unresolved ? "mdi-content-duplicate" : "mdi-image-multiple",
);

// The question mark is the point: it is what stops an unresolved group reading
// as a stack that already exists.
const label = computed(() =>
  props.unresolved ? `${props.count}?` : `${props.count}`,
);

const title = computed(() =>
  props.unresolved
    ? `${props.count} possible duplicates, not stacked yet. Open Duplicates to decide.`
    : `Stack of ${props.count} pictures`,
);
</script>

<style scoped>
/* Sits on top of an arbitrary photo, so the backing is the photo scrim and the
   glyph is `on-dark-surface`: the pair that stays legible over unknown content
   in both themes (visual-language.md §7, "Scrims"). Deliberately NOT
   self-positioned: it flows inside the tile's top-right badge column
   (`.thumbnail-top-right-badges`, the shared home for corner indicators —
   stars above, this below), so it can never land on top of a sibling badge.
   `pointer-events` is explicit because that container is pointer-inert. */
.sbadge {
  pointer-events: auto;
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  min-height: var(--badge-size);
  padding: 0 var(--space-2);
  border: none;
  border-radius: var(--radius-pill);
  background-color: var(--scrim-photo);
  color: rgb(var(--v-theme-on-dark-surface));
  font-family: var(--font-ui);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-snug);
  font-variant-numeric: tabular-nums;
  cursor: pointer;
}

/* Layered as an image so the scrim underneath is preserved: a `background`
   shorthand on hover would drop the backing and leave the glyph on the photo. */
.sbadge:hover {
  background-image: linear-gradient(var(--hover-wash), var(--hover-wash));
}

.sbadge:focus-visible {
  box-shadow: var(--focus-ring);
  outline: none;
}

/* Quieter than a real stack, deliberately. Same scrim so it stays legible over
   any photo, muted foreground so it never competes with a resolved count. */
.sbadge--unresolved {
  color: rgba(var(--v-theme-on-dark-surface), 0.75);
}

.sbico {
  flex-shrink: 0;
}
</style>
