<template>
  <button
    class="reset-chip"
    type="button"
    :aria-label="`Put ${label} back to ${shown}`"
    :title="`Put ${label} back to ${shown}`"
    @click.stop="emit('reset')"
  >
    <v-icon size="11">mdi-restore</v-icon>
    <span class="reset-chip-value">{{ shown }}</span>
  </button>
</template>

<script setup>
/**
 * The ↺ chip an edited field wears IN ITS LABEL ROW (v1.12 F5).
 *
 * It carries the value the field started at and puts it back. It lives in the
 * label and never beside the control, because the Run popup is a four-column
 * grid: a chip that could change a cell's height would knock the whole row of
 * fields out of alignment the moment one of them was edited.
 */
import { computed } from "vue";
import { VIcon } from "vuetify/components";

const props = defineProps({
  /**
   * The original value, shown on the chip so it can be read without acting.
   *
   * Nullable: a card default can carry no value at all, and a required prop
   * would warn and render an empty chip rather than say so.
   */
  value: { type: [String, Number, Boolean, null], default: null },
  /** The field's own label, for the accessible name. */
  label: { type: String, required: true },
});

const emit = defineEmits(["reset"]);

/**
 * What the chip reads, and what its `title` carries in full.
 *
 * The chip is 16px tall and ellipsises, so a prompt's original is a few
 * truncated words on screen; the `title` is what makes the whole of it
 * readable before a click that cannot be undone.
 */
const shown = computed(() =>
  props.value === null || props.value === "" ? "no value" : String(props.value),
);
</script>

<style scoped>
.reset-chip {
  position: relative;
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  max-width: 60%;
  /* The design draws a 16px chip, and it has to stay 16px or the label row
     grows and the four columns stop sharing a baseline. */
  height: var(--space-5);
  padding: 0 var(--space-2);
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-surface), 0.1);
  color: rgb(var(--v-theme-on-surface));
  font-size: var(--text-2xs);
  line-height: 1;
  cursor: pointer;
}

/* …so the POINTER target is grown instead, to the WCAG 2.5.8 24px floor,
   without touching the layout. 4px each way is exactly the label row's gap to
   the control below it, so the two areas meet and never overlap. */
.reset-chip::after {
  content: "";
  position: absolute;
  inset: -4px 0;
}

/* The grown hit area is what a pointer gets; the ring stays on the chip itself
   or it draws clear of the thing it is naming. Tokens, not the values behind
   them - design-tokens.css calls a hand-restated focus ring a defect. */
.reset-chip:focus-visible {
  outline: var(--focus-width) solid var(--focus-stroke);
  outline-offset: var(--focus-offset);
}

.reset-chip:hover {
  background: rgba(var(--v-theme-on-surface), 0.16);
}

.reset-chip-value {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
