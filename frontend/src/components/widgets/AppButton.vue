<template>
  <button
    ref="rootEl"
    type="button"
    :class="[
      'app-btn',
      `app-btn--${variant}`,
      `app-btn--${size}`,
      {
        'app-btn--icon-only': iconOnly,
        'app-btn--loading': loading,
        'app-btn--block': block,
      },
    ]"
    :disabled="disabled || loading"
    :aria-busy="loading ? 'true' : undefined"
    :title="title"
    :aria-keyshortcuts="keyShortcut"
  >
    <!-- `icon` overrides `iconLeft` for the rare glyph that is not in mdi (the
         ai-toolkit mark). Loading still wins over both: a spinner replacing the
         icon is how this button says it is busy, whichever glyph it wears. -->
    <span
      v-if="!loading && $slots.icon"
      :class="['app-btn__icon', 'app-btn__icon--custom']"
    >
      <slot name="icon" :size="size === 'sm' ? 16 : 18" />
    </span>
    <v-icon
      v-else-if="loading || iconLeft"
      :size="size === 'sm' ? 16 : 18"
      :class="['app-btn__icon', { 'mdi-spin': loading }]"
    >
      {{ loading ? "mdi-loading" : `mdi-${iconLeft}` }}
    </v-icon>
    <span v-if="!iconOnly" class="app-btn__label"><slot /></span>
    <kbd v-if="keyHint" class="app-btn__key" aria-hidden="true">{{
      keyLabel
    }}</kbd>
  </button>
</template>

<script setup>
import { computed, nextTick, ref, watch } from "vue";
import { VIcon } from "vuetify/components";

const props = defineProps({
  // primary (amber accent, the key action) | secondary (neutral) |
  // outline (bordered, no fill) | danger (error) | ghost (transparent).
  // Amber acts, olive selects: there is no olive button.
  variant: { type: String, default: "secondary" },
  size: { type: String, default: "md" }, // md | sm
  iconLeft: { type: String, default: "" },
  iconOnly: { type: Boolean, default: false },
  // Full width of its container, for a panel-width key action.
  block: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  // Pending / in-flight. NOT the same thing as `disabled`: "working", not "not
  // allowed" (visual-language.md §11). Forces the button disabled so a second
  // click cannot fire (issue #647), swaps the leading icon for the shared
  // spinner, and leaves the label alone. There is deliberately no loading-text
  // prop: the label is the accessible name and must not change mid-flight.
  loading: { type: Boolean, default: false },
  title: { type: String, default: "" },
  // The visible shortcut affordance from the dialog keyboard contract:
  // "enter" wears ↵ and "esc" wears Esc; any other single key (e.g. "s" on
  // the dedup Keep separate) wears its own uppercase label. A shortcut shown
  // next to the action it triggers is the only kind anyone discovers.
  keyHint: { type: String, default: "" },
});

const keyLabel = computed(() =>
  props.keyHint === "enter"
    ? "↵"
    : props.keyHint === "esc"
      ? "Esc"
      : props.keyHint.toUpperCase(),
);

const keyShortcut = computed(() =>
  props.keyHint === "enter"
    ? "Enter"
    : props.keyHint === "esc"
      ? "Escape"
      : props.keyHint
        ? props.keyHint.toUpperCase()
        : undefined,
);

const rootEl = ref(null);
let refocusWhenDone = false;

// A natively-disabled button cannot hold focus, so the browser drops focus to
// <body>, stranding a keyboard user who would have to tab all the way back to
// where they were once the request settles.
watch(
  () => props.loading,
  (isLoading) => {
    if (isLoading) {
      refocusWhenDone = rootEl.value === document.activeElement;
      return;
    }
    if (!refocusWhenDone) return;
    refocusWhenDone = false;
    nextTick(() => rootEl.value?.focus());
  },
);

/**
 * Put the keyboard on this button.
 *
 * Exposed for the dialogs that have to place initial focus deliberately rather
 * than let the browser pick. `KeepCoverOnlyDialog` focuses its Cancel on open,
 * because the user arrives from the duplicate queue with Enter under their
 * finger. Reaching through `$el` would work, but it hides that intent.
 */
function focus() {
  rootEl.value?.focus();
}

defineExpose({ focus });
</script>

<style scoped>
.app-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  font-family: var(--font-ui);
  font-weight: var(--weight-medium);
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  white-space: nowrap;
  transition:
    background var(--dur-1) var(--ease-standard),
    border-color var(--dur-1) var(--ease-standard),
    color var(--dur-1) var(--ease-standard),
    background-image var(--dur-1) var(--ease-standard);
}

.app-btn--md {
  height: var(--control-h);
  padding: 0 var(--space-5);
  font-size: var(--text-base);
}

.app-btn--sm {
  height: var(--control-h-sm);
  padding: 0 var(--space-4);
  font-size: var(--text-sm);
}

.app-btn--icon-only.app-btn--md {
  width: var(--control-h);
  padding: 0;
}
.app-btn--icon-only.app-btn--sm {
  width: var(--control-h-sm);
  padding: 0;
}

.app-btn--block {
  display: flex;
  width: 100%;
}

/* Both spellings of "not allowed" fade the same way. `aria-disabled`, not the
   attribute, is how this app marks a control that is blocked FOR A REASON
   (UndoControl, ActionReceipt, ReviewDecisionBar): the button keeps its place
   in the tab order, so the `aria-describedby` reason it points at stays
   reachable by keyboard. It still has to LOOK disabled, which is why it shares
   this fade and why every variant's hover below excludes it: a control that
   lights up under the pointer promises a press that will not land. */
.app-btn:disabled,
.app-btn[aria-disabled="true"] {
  opacity: var(--opacity-disabled);
  cursor: not-allowed;
}

/* PENDING is not DISABLED. A disabled control is "not allowed" and may legally
   fade (WCAG 1.4.3 exempts it); a pending one is the only thing telling the user
   their click landed, so its label has to stay legible. Group opacity cannot do
   that on a filled variant: the whole button composites toward the surface, so
   white on the light `accent` fill measures 4.75:1 at full opacity, 3.97:1 at
   0.9, 2.70:1 at 0.7 and 1.67:1 at the disabled 0.38. The white-label rule
   (visual-language.md §4) only survives above ~0.97, i.e. there is no dim that is
   both perceptible and legal. So pending does not dim. `progress`, not
   `not-allowed`: the button is busy, the rest of the surface is still live. */
.app-btn--loading:disabled {
  opacity: 1;
  cursor: progress;
}

/* Primary - amber accent, the key action. A filled control's hover darkens
   the fill 20% (`--hover-shade`, a background-image), which leaves the label
   alone and lifts white on amber from 3.41 to 5.03:1. */
.app-btn--primary {
  background-color: rgb(var(--v-theme-accent));
  color: rgb(var(--v-theme-on-accent));
}
.app-btn--primary:not(:disabled):not([aria-disabled="true"]):hover,
.app-btn--danger:not(:disabled):not([aria-disabled="true"]):hover {
  background-image: var(--hover-shade);
}

/* Secondary - neutral. The Cancel partner. Its hover lightens in dark and
   darkens in light (`--hover-neutral`): a darkened grey on a dark panel vanishes. */
.app-btn--secondary {
  background-color: rgb(var(--v-theme-cancel-button));
  color: rgb(var(--v-theme-cancel-button-text));
}
.app-btn--secondary:not(:disabled):not([aria-disabled="true"]):hover {
  background-image: var(--hover-neutral);
}

/* Outline - the neutral without a fill, for the former outlined `v-btn` sites.
   Whether these fold into the filled neutral is still open (buttons.md). */
.app-btn--outline {
  background: transparent;
  color: rgb(var(--v-theme-on-surface));
  border-color: rgb(var(--v-theme-border));
}
.app-btn--outline:not(:disabled):not([aria-disabled="true"]):hover {
  background: var(--hover-wash);
}

/* Danger: destructive. `on-error`, not a hardcoded #fff. Both themes author
   `error: #b54538` with `on-error: #f7f1ea`, the warm near-white, at 4.83:1;
   main.js says "(same value in both themes)" on that very line. The comment
   here previously claimed the pair flipped to the warm near-black in dark,
   which was never true of `error` (it is true of `warning`, whose fill IS
   brighter in dark). Recorded because a stale contrast note is the kind of
   thing that gets "corrected" by changing the value instead of the note. */
.app-btn--danger {
  background-color: rgb(var(--v-theme-error));
  color: rgb(var(--v-theme-on-error));
}

/* Ghost - transparent, recedes until hovered. */
.app-btn--ghost {
  background: transparent;
  color: rgba(var(--v-theme-on-surface), 0.7);
}
.app-btn--ghost:not(:disabled):not([aria-disabled="true"]):hover {
  background: var(--hover-wash);
  color: rgb(var(--v-theme-on-surface));
}

.app-btn__icon {
  flex-shrink: 0;
}

/* A slotted glyph is a bare <svg>, which is inline and would sit on the text
   baseline rather than centred against the label. `<v-icon>` handles this for
   itself; this box does it for anything else. */
.app-btn__icon--custom {
  display: inline-flex;
  align-items: center;
}

/* The key-hint badge. currentColor keeps it legible on every variant fill;
   the reduced opacity keeps it a hint rather than a second label. */
.app-btn__key {
  flex-shrink: 0;
  padding: 0 var(--space-1);
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  line-height: 1.5;
  border: 1px solid currentColor;
  border-radius: var(--radius-sm);
  opacity: 0.55;
}

/* The spinner keeps spinning under reduced motion. It is a status readout, not
   decoration - the same exception ActionReceipt's countdown hairline takes
   (visual-language.md §10). The global reset in design-tokens.css zeroes
   animation-duration and iteration-count on `*` and `*::before`, which freezes
   this into a static broken ring that reads as a rendering fault. @mdi/font puts
   the animation on `::before`, so that is what is restored; specificity beats the
   universal reset even though both are !important. A 16px rotating glyph is not
   a vestibular trigger. */
@media (prefers-reduced-motion: reduce) {
  .app-btn--loading .app-btn__icon.mdi-spin::before {
    animation-duration: 2s !important;
    animation-iteration-count: infinite !important;
  }
}
</style>
