<template>
  <button
    v-if="visible"
    type="button"
    class="sbadge"
    :class="{
      'sbadge--unresolved': unresolved,
      'sbadge--tinted': tinted,
    }"
    :title="title"
    :aria-label="title"
    :aria-expanded="expanded === null ? undefined : String(expanded)"
    data-testid="stack-badge"
    @click.stop="emit('activate')"
  >
    <v-icon class="sbico" size="14" :style="glyphStyle">{{ glyph }}</v-icon>
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
// that means "expand the stack" or "jump to this group in the queue". A parent
// whose press opens a disclosure (the duplicate queue row's expansion band)
// says so with `expanded` + `actionTitle`, so the badge announces itself as
// the disclosure it is on that surface and as a plain count everywhere else.

import { computed } from "vue";

const props = defineProps({
  /** How many pictures this tile stands for. Below 2 there is nothing to say. */
  count: { type: Number, default: 0 },
  /** True while the group is still a queue suggestion rather than a stack. */
  unresolved: { type: Boolean, default: false },
  /**
   * This stack's colour, already renormalised for glyph use by
   * `applyStackBadgeTint`. Null when the tile has no stack colour to show.
   */
  tint: { type: String, default: null },
  /**
   * When the press opens a DISCLOSURE, whether that disclosure is currently
   * open. Null (the default) publishes no `aria-expanded` at all, which is
   * the honest answer on the surfaces where the press navigates or selects
   * instead: a control that claims to be a disclosure while opening nothing
   * is a lie to a screen reader (WCAG 4.1.2).
   */
  expanded: { type: Boolean, default: null },
  /**
   * Overrides the badge's own name where the press does something other than
   * state the count: the queue row's expansion trigger, where the name has
   * to say what opens. Empty keeps the count sentence below.
   */
  actionTitle: { type: String, default: "" },
});

const emit = defineEmits(["activate"]);

const visible = computed(() => props.count >= 2);

// An unresolved group has no stack, so it has no stack colour, and tinting it
// would hand a suggestion the one signal that says "this stack exists".
const tinted = computed(() => !!props.tint && !props.unresolved);

// The glyph is the only thing the hue touches. The count is small text and
// needs 4.5:1 (WCAG 1.4.3); no colour in the stack palette reaches that on any
// usable chip opacity, so the number stays `on-dark-surface` (visual-language.md
// §7, "Scrims").
const glyphStyle = computed(() =>
  tinted.value ? { color: props.tint } : null,
);

const glyph = computed(() =>
  props.unresolved ? "mdi-content-duplicate" : "mdi-image-multiple",
);

// The question mark is the point: it is what stops an unresolved group reading
// as a stack that already exists.
const label = computed(() =>
  props.unresolved ? `${props.count}?` : `${props.count}`,
);

const title = computed(() => {
  // The caller's sentence wins whenever it has one: a badge that expands must
  // name the expansion, not repeat the count the numeral already carries.
  if (props.actionTitle) return props.actionTitle;
  return props.unresolved
    ? `${props.count} possible duplicates, not stacked yet. Open Duplicates to decide.`
    : `Stack of ${props.count} pictures`;
});
</script>

<style scoped>
/* Sits on top of an arbitrary photo, so the backing is the photo scrim and the
   glyph is `on-dark-surface`: the pair that stays legible over unknown content
   in both themes (visual-language.md §7, "Scrims"). Deliberately NOT
   self-positioned: it flows inside the tile's top-right badge column
   (`.thumbnail-top-right-badges`, the shared home for corner indicators: this
   first, the hover-only stars below it), so it can never land on top of a
   sibling badge. It leads the column because it is the column's only PERMANENT
   member: put it second and its rest position is set by the height of an
   invisible star strip, which is how it ended up floating off the corner.
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

/* A coloured glyph cannot reach `on-dark-surface`'s luminance, so the chip it
   sits on carries the difference: on the 0.55 scrim the darkest hue in the
   palette measures 1.62:1 over a bright photo, and on this one 3.98:1
   (visual-language.md §7). The pair is the spec; tinting the glyph without
   deepening the chip is the invisible-indicator bug this replaced. */
.sbadge--tinted {
  background-color: var(--scrim-photo-strong);
}

/* Quieter than a real stack, deliberately. Same scrim so it stays legible over
   any photo, muted foreground so it never competes with a resolved count. The
   lighter chip is the second axis: a suggestion and a fact differ in both
   chroma and weight, which is what keeps them apart at grid scale. */
.sbadge--unresolved {
  color: rgba(var(--v-theme-on-dark-surface), 0.75);
}

.sbico {
  flex-shrink: 0;
}
</style>
