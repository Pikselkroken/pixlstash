<script setup>
/**
 * The why-pills under a duplicate group: the evidence for and against stacking.
 *
 * The pills are the group's reasoning, shown where the decision is made, so the
 * user never has to trust an unexplained suggestion.
 *
 * Two rules shape this component:
 *
 *   • Counter-evidence reads FIRST (`orderEvidence`). A group carrying red pills
 *     is exactly the one that needs a closer look, and a collapsed row has room
 *     for about two pills, so the warning half has to be the half that survives
 *     the `limit`.
 *   • Meaning never rides on colour alone (WCAG 1.4.1). The check and the cross
 *     glyphs carry the for/against split, and each pill states it in its `title`
 *     as well, so the distinction survives a monochrome or colour-blind read.
 *
 * The label sits in `on-surface`, not in `primary` or `error`: an action-fill
 * token is never small body text on a canvas (visual-language.md §4). The status
 * colour lives in the tinted fill, the border, and the glyph.
 */
import { computed } from "vue";

import { orderEvidence, evidenceLabel } from "../../utils/dedup";

/** The pill's rendered text. The server calls the field `text`. */
const labelOf = evidenceLabel;

const props = defineProps({
  /** Evidence entries, the server's `[{ text, against }]`, in its order. */
  why: { type: Array, default: () => [] },
  /** Maximum pills to render. 0 means show every one. */
  limit: { type: Number, default: 0 },
  /**
   * How the pills are toned.
   *
   * `argument` is the shipped duplicate-group treatment: the group is a
   * PROPOSAL, the pills argue for and against it, and the two sides are tinted
   * because the user is being asked to weigh them.
   *
   * `fact` is the Mixed stacks row: the stack already exists and the pills
   * describe what was measured about it (`2 groups (2 + 1)`,
   * `Weakest match 97%`). Nothing there is arguing for a verdict the user has
   * not given yet, and a row of red chips over an existing stack would read as
   * an accusation. The glyph and the title still carry the for/against split,
   * so the distinction survives without colour (WCAG 1.4.1) exactly as it does
   * in `argument`.
   */
  variant: {
    type: String,
    default: "argument",
    validator: (value) => ["argument", "fact"].includes(value),
  },
});

/**
 * Ordering happens BEFORE truncation, never after: truncating the server order
 * first would be free to drop the counter-evidence and leave a row that looks
 * unanimously safe when it is not.
 */
const pills = computed(() => {
  const ordered = orderEvidence(props.why);
  return props.limit > 0 ? ordered.slice(0, props.limit) : ordered;
});
</script>

<template>
  <ul v-if="pills.length" class="why-pills">
    <li
      v-for="(pill, index) in pills"
      :key="`${index}:${labelOf(pill)}`"
      class="why-pill"
      :class="
        variant === 'fact'
          ? 'why-pill--fact'
          : pill.against
            ? 'why-pill--neg'
            : 'why-pill--pos'
      "
      :title="`${labelOf(pill)}. ${
        pill.against ? 'Argues against stacking.' : 'Supports stacking.'
      }`"
    >
      <v-icon class="why-pill__ico" size="12">{{
        pill.against ? "mdi-close" : "mdi-check"
      }}</v-icon>
      <span class="why-pill__label">{{ labelOf(pill) }}</span>
    </li>
  </ul>
</template>

<style scoped>
/* A list, not a row of spans: this is an enumeration of reasons, and a screen
   reader announcing "list, 3 items" is the correct summary of it. */
.why-pills {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  max-width: 100%;
  margin: 0;
  padding: 0;
  list-style: none;
}

.why-pill {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-pill);
  border: 1px solid transparent;
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  /* Full-strength body text on a tint. The status hue is carried by the fill,
     the border, and the glyph, which is where a 12px label cannot go. */
  color: rgb(var(--v-theme-on-surface));
  white-space: nowrap;
  max-width: 100%;
  min-width: 0;
  box-sizing: border-box;
}

/* Supports stacking. */
.why-pill--pos {
  background: rgba(var(--v-theme-primary), 0.12);
  border-color: rgba(var(--v-theme-primary), 0.35);
}
.why-pill--pos .why-pill__ico {
  color: rgb(var(--v-theme-primary));
}

/* Argues against stacking. Same construction, so the two read as one family
   with one differing signal rather than as two different components. */
.why-pill--neg {
  background: rgba(var(--v-theme-error), 0.12);
  border-color: rgba(var(--v-theme-error), 0.35);
}
.why-pill--neg .why-pill__ico {
  color: rgb(var(--v-theme-error));
}

/* A measurement, not an argument. Same box as the other two so the family reads
   as one component; the fill goes and the border drops to the neutral one, so a
   run of them is a row of facts about an existing stack rather than a verdict
   being urged on the user. */
.why-pill--fact {
  background: transparent;
  border-color: rgb(var(--v-theme-border));
}

.why-pill--fact .why-pill__ico {
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.why-pill__ico {
  flex-shrink: 0;
}

/* The info column is deliberately narrow so the pictures keep the row's room.
   A long server reason must end inside that column instead of painting under
   the first thumbnail. Its full wording remains in the pill's tooltip. */
.why-pill__label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
