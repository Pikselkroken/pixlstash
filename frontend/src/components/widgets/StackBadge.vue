<template>
  <button
    v-if="visible"
    type="button"
    class="sbadge"
    :class="{
      'sbadge--unresolved': unresolved,
      'sbadge--tinted': variant === 'tinted',
      'sbadge--flagged': variant === 'flagged',
    }"
    :title="title"
    :aria-label="title"
    :aria-expanded="expanded === null ? undefined : String(expanded)"
    :data-flagged="variant === 'flagged' ? 'true' : undefined"
    data-testid="stack-badge"
    @click.stop="emit('activate')"
  >
    <v-icon v-if="showsGlyph" class="sbico" size="14" :style="glyphStyle">{{
      glyph
    }}</v-icon>
    <span v-if="showsCount" class="sbcount">{{ label }}</span>
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
// On top of that it can be FLAGGED (design D5): this stack's members do not all
// match at the current similarity threshold. That mark takes over the icon slot
// rather than adding a second badge, because the edge ticks behind the tile
// already say "this is a stack" and the corner has no room for anything else.
// It never blocks a press, and it never blocks a verdict on the row it sits in.
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
  /**
   * This stack's members do not all match: the STRONG case only, a member
   * joined to nothing else in the stack (design D5).
   *
   * The soft cases are deliberately never marked. At the measured 12% a mark
   * is one tile in eight and becomes a warning field, and a soft case is often
   * legitimate (a burst where one frame panned off), so marking them trains
   * the user to dismiss the colour before the real one appears.
   *
   * The flag is a standing FACT, not an event: it never animates, and it never
   * blocks or disables anything. A mixed stack is one a user may legitimately
   * want to add to.
   */
  flagged: { type: Boolean, default: false },
  /**
   * The badge is on a tile too small for both a glyph and a numeral.
   *
   * Below that size the rule INVERTS rather than fading: an unflagged deck
   * keeps its numeral and drops the icon (the count is why the badge exists);
   * a flagged deck keeps the icon and drops the numeral (the warning is). The
   * accessible name carries both either way.
   */
  dense: { type: Boolean, default: false },
});

const emit = defineEmits(["activate"]);

const visible = computed(() => props.count >= 2);

/**
 * Which of the badge's paints applies, as one closed ladder:
 * expanded > flagged > per-stack tint > plain.
 *
 * A ladder rather than three independent classes, because two of them set the
 * same backing and the glyph's colour, and "whichever rule came last in the
 * stylesheet" is not a design decision. An OPEN disclosure outranks the flag:
 * the band below the row is already showing the pictures the flag is about, so
 * the corner goes back to being a plain count.
 */
const variant = computed(() => {
  if (props.expanded === true) return "expanded";
  if (props.flagged) return "flagged";
  // An unresolved group has no stack, so it has no stack colour, and tinting it
  // would hand a suggestion the one signal that says "this stack exists".
  if (props.tint && !props.unresolved) return "tinted";
  return "plain";
});

// The glyph is the only thing the per-stack hue touches. The count is small
// text and needs 4.5:1 (WCAG 1.4.3); no colour in the stack palette reaches
// that on any usable chip opacity, so the number stays `on-dark-surface`
// (visual-language.md §7, "Scrims"). The flag's own hue is a theme status
// token and is set in CSS, not here.
const glyphStyle = computed(() =>
  variant.value === "tinted" ? { color: props.tint } : null,
);

const glyph = computed(() => {
  if (variant.value === "flagged") return "mdi-alert-outline";
  return props.unresolved ? "mdi-content-duplicate" : "mdi-image-multiple";
});

// The dense inversion. Both are always in the accessible name below, so
// nothing is lost to a screen reader at any size.
const showsGlyph = computed(() => !props.dense || variant.value === "flagged");
const showsCount = computed(() => !props.dense || variant.value !== "flagged");

// The question mark is the point: it is what stops an unresolved group reading
// as a stack that already exists.
const label = computed(() =>
  props.unresolved ? `${props.count}?` : `${props.count}`,
);

const title = computed(() => {
  // The caller's sentence wins whenever it has one: a badge that expands must
  // name the expansion, not repeat the count the numeral already carries. The
  // flag is appended rather than replacing it, because it is a second fact
  // about the same stack and the press still does what the caller said.
  const base =
    props.actionTitle ||
    (props.unresolved
      ? `${props.count} possible duplicates, not stacked yet. Open Duplicates to decide.`
      : `Stack of ${props.count} pictures`);
  if (variant.value !== "flagged") return base;
  const suffix = `These ${props.count} pictures don't all match. Review it under Mixed stacks.`;
  return /[.!?]$/.test(base) ? `${base} ${suffix}` : `${base}. ${suffix}`;
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

/* A coloured glyph cannot reach `on-dark-surface`'s luminance, so the chip it
   sits on carries the difference: on the 0.55 scrim the darkest hue in the
   palette measures 1.62:1 over a bright photo, and on this one 3.98:1
   (visual-language.md §7). The pair is the spec; tinting the glyph without
   deepening the chip is the invisible-indicator bug this replaced. */
.sbadge--tinted {
  background-color: var(--scrim-photo-strong);
}

/* The mark for a stack whose members do not all match (design D5).
   It reuses the badge's ICON slot, freed because the edge ticks behind the
   tile already say "this is a stack", so nothing new is added to a corner
   that has no room for it.

   Same pair as `--tinted`, and for the same measured reason: a chromatic glyph
   cannot reach `on-dark-surface`'s luminance over an arbitrary photo, so the
   deepened scrim is what carries it past the 3:1 non-text floor
   (visual-language.md §7). The 1px inset ring is the second, non-colour
   channel (the flag must not be carried by hue alone), and it is inset rather
   than an outline so it cannot grow the badge's box on a tile that is already
   short of corner. `warning` here is a foreground/border on the chip, which is
   the 3:1 UI job the token is authored for; `on-warning` would be the wrong
   token, since there is no solid warning fill under it.

   No motion, deliberately: the flag is a standing fact about the stack, not an
   event that just happened. Moving `--v-theme-warning` in this app means a
   refused press. */
.sbadge--flagged {
  background-color: var(--scrim-photo-strong);
  box-shadow: inset 0 0 0 1px rgb(var(--v-theme-warning));
}

.sbadge--flagged .sbico {
  color: rgb(var(--v-theme-warning));
}

/* The focus ring has to win over the inset flag ring, or a keyboard user
   loses the one indicator they cannot do without. */
.sbadge--flagged:focus-visible {
  box-shadow:
    inset 0 0 0 1px rgb(var(--v-theme-warning)),
    var(--focus-ring);
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
