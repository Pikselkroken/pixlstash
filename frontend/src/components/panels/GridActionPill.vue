<template>
  <transition name="selbar-pop">
    <div v-if="visible" ref="barEl" class="grid-action-pill">
      <!-- Two real groups, not styled runs. A screen reader navigates the GROUP
           boundary; the seam is decoration and is hidden from it. Each group's
           first child carries its own count, so entering either one announces
           what it holds without a live region of its own. -->
      <div
        v-if="searchActive"
        ref="searchEl"
        class="pill-segment"
        role="group"
        aria-label="Search results"
      >
        <slot name="search"></slot>
      </div>

      <transition name="pill-seam">
        <div
          v-if="searchActive && selectionActive"
          class="pill-seam"
          aria-hidden="true"
        ></div>
      </transition>

      <transition name="pill-segment">
        <div
          v-if="selectionActive"
          ref="selectionEl"
          class="pill-segment"
          role="group"
          aria-label="Selection actions"
        >
          <slot name="selection"></slot>
        </div>
      </transition>
    </div>
  </transition>
</template>

<script setup>
// The grid's single bottom-edge surface (merged-grid-action-pill.md).
//
// Before this component the search bar and the selection pill were independent
// mounts that could both be up at once, and only one of them registered a
// bottom anchor - so notice cards landed on top of the other. One owner of the
// bottom edge is the point of the merge; the two halves are slots so their
// wiring stays in ImageGrid rather than being drilled through a shell.
//
// This component owns the pill surface, the seam, the motion and the anchor
// registration. It owns no actions.

import { computed, nextTick, ref, watch } from "vue";
import { useBottomAnchor } from "../../composables/useBottomAnchor";

const props = defineProps({
  /** The search half has something to say (query, reverse-image, face search). */
  searchActive: { type: Boolean, default: false },
  /** Pictures or faces are selected. */
  selectionActive: { type: Boolean, default: false },
});

// Raised when the whole pill is about to unmount while it holds focus. The pill
// does not know where the grid's cursor is, so the host decides where focus
// lands; letting it fall to <body> would drop a keyboard user out of the tab
// order entirely (WCAG 2.4.3).
const emit = defineEmits(["focus-escaped"]);

const barEl = ref(null);
const searchEl = ref(null);
const selectionEl = ref(null);

const visible = computed(() => props.searchActive || props.selectionActive);

// Registered under the SAME name the selection pill used, deliberately:
// ActionReceipt lifts itself by `useAnchorHeight("selection-bar")`, and the
// height it needs is still this element's. Height is MEASURED, never assumed -
// the pill grows on coarse pointers and wraps at the narrow floor.
useBottomAnchor("selection-bar", barEl);

// ── Focus rescue ────────────────────────────────────────────────────────────
// Esc collapses the selection half one layer at a time (useGridKeyboardNav).
// When the segment holding focus unmounts, focus is dumped to <body>. Capture
// the answer BEFORE the DOM updates (`flush: "pre"`), act after it has.
watch(
  () => props.selectionActive,
  (active, wasActive) => {
    if (active || !wasActive) return;
    const el = selectionEl.value;
    if (!el || !el.contains(document.activeElement)) return;
    nextTick(() => {
      // The search half survives a selection clear; if it does not, the whole
      // pill is going and only the host knows where the grid cursor is.
      if (!props.searchActive || !focusFirstIn(searchEl.value)) {
        emit("focus-escaped");
      }
    });
  },
  { flush: "pre" },
);

watch(
  () => props.searchActive,
  (active, wasActive) => {
    if (active || !wasActive) return;
    const el = searchEl.value;
    if (!el || !el.contains(document.activeElement)) return;
    nextTick(() => {
      if (!props.selectionActive || !focusFirstIn(selectionEl.value)) {
        emit("focus-escaped");
      }
    });
  },
  { flush: "pre" },
);

const FOCUSABLE =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Move focus to the first interactive element inside `el`. True if it landed. */
function focusFirstIn(el) {
  if (!el) return false;
  for (const candidate of el.querySelectorAll(FOCUSABLE)) {
    candidate.focus();
    // A control the responsive ladder has hidden (`display: none`, e.g. the
    // threshold form that is not the active one) refuses focus. Asking the
    // browser whether it landed beats any size or visibility heuristic - and
    // the zero-size menu activators are <div>s without tabindex, so they never
    // match the selector in the first place.
    if (document.activeElement === candidate) return true;
  }
  return false;
}

defineExpose({ visible });
</script>

<style scoped>
.grid-action-pill {
  position: absolute;
  /* --space-5, not the old off-grid 18px. This is the prerequisite for the
     notice stack's arithmetic landing on tokens (notice-surface.md §2.2): the
     stack rests at `--space-5 + measured pill height + --space-3`, which only
     leaves an exact `--space-3` gap if the pill's own inset is also --space-5.
     Only the pill's HEIGHT is measured (useBottomAnchor observes the border
     box), never its inset, so moving it here does not disturb that measurement. */
  bottom: var(--space-5);
  left: 50%;
  /* width: max-content so the pill hugs its contents. NOTE: do NOT add
     container-type here - inline-size containment makes the width ignore the
     contents, collapsing the pill to ~0 and leaving the controls floating with
     no visible background. The `selbar` container for the @container queries in
     the segments is declared on `.grid-content-area` (ImageGrid.vue), the
     pill's positioned ancestor. */
  width: max-content;
  transform: translateX(-50%);
  z-index: 200;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  /* LOAD-BEARING, not a style preference. The merged content is roughly double
     what the selection pill alone held. If this ever wraps, the pill's height
     jumps ~40px, the ResizeObserver in useBottomAnchor fires, and both the
     notice stack and the action receipt move mid-interaction. The responsive
     ladder in the segments exists to make wrapping impossible above the narrow
     floor (merged-grid-action-pill.md §5). Asserted by GridActionPill.test.js. */
  flex-wrap: nowrap;
  max-width: calc(100% - var(--space-6));
  /* Block padding deliberately left at 6px: it sets the pill's occupied height,
     and that dimension belongs to the UI/UX-gated action-bar reconciliation
     (visual-language.md §5/§13, the 34/40/48/56px drift), not to a token swap. */
  padding: 6px var(--space-4);
  border-radius: var(--radius-pill);
  background: rgba(var(--v-theme-surface), 0.86);
  /* --elevation-3, not -4: -4 is reserved for dialogs and lightbox chrome. The
     pill is persistent floating chrome, and it sits --space-3 from the notice
     cards, which are also -3. Peers on the bottom edge read as one layer. */
  box-shadow: var(--elevation-3);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.14);
  backdrop-filter: blur(12px);
}

.pill-segment {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
}

/* The seam between the two contexts. `border`, not `divider`: §4 splits those
   into "visible" and "subtle", and under backdrop-filter: blur(12px) the subtle
   one is a whisper (~1.15:1 on light `surface`) that does not survive a bright
   photo bleeding through the 14% of transparency.

   16px of air each side - 8px here plus the pill's own 8px gap - so the gutter
   is 32px across against an 8px internal rhythm. THAT 4x step is what tells the
   two halves apart at a glance; a two-tone fill was rejected for it
   (merged-grid-action-pill.md §2.1 and §11.1). Do not add a second colour. */
.pill-seam {
  width: 1px;
  height: var(--rule-h-seam);
  background: rgb(var(--v-theme-border));
  align-self: center;
  flex-shrink: 0;
  margin: 0 var(--space-3);
}

/* Entering the screen decelerates, leaving it accelerates and is quicker
   (visual-language.md §10) - the same pairing the notice stack beside it uses. */
.selbar-pop-enter-active {
  transition:
    transform var(--dur-2) var(--ease-decelerate),
    opacity var(--dur-2) var(--ease-decelerate);
}
.selbar-pop-leave-active {
  transition:
    transform var(--dur-1) var(--ease-accelerate),
    opacity var(--dur-1) var(--ease-accelerate);
}
.selbar-pop-enter-from,
.selbar-pop-leave-to {
  transform: translateX(-50%) translateY(120%);
  opacity: 0;
}

/* The expand is GEOMETRY-STABLE: the pill reflows once and the cue rides
   entirely on compositor properties. Width is deliberately not transitioned -
   `max-content` is not interpolable, and because the pill is centred with
   translateX(-50%) an animated width moves its LEFT edge too, dragging the
   search half's controls sideways under a live pointer. Height must never
   animate either: it feeds --floating-bottom-h through a ResizeObserver, so it
   would re-target the notice stack and the receipt's lift every frame. */
.pill-seam-enter-active {
  transition: transform var(--dur-1) var(--ease-standard);
}
.pill-seam-leave-active {
  transition: transform var(--dur-1) var(--ease-accelerate);
}
.pill-seam-enter-from,
.pill-seam-leave-to {
  transform: scaleY(0);
}

/* The segment enters from the pill's new outer edge. Same property pair and the
   same in/out asymmetry as selbar-pop above - one motion vocabulary, three
   parameters (8px instead of 120%, X instead of Y). */
.pill-segment-enter-active {
  transition:
    transform var(--dur-2) var(--ease-decelerate),
    opacity var(--dur-2) var(--ease-decelerate);
}
.pill-segment-leave-active {
  transition:
    transform var(--dur-1) var(--ease-accelerate),
    opacity var(--dur-1) var(--ease-accelerate);
}
.pill-segment-enter-from,
.pill-segment-leave-to {
  transform: translateX(8px);
  opacity: 0;
}

/* When the pill itself is arriving, selbar-pop owns the entrance: a nested
   slide inside a surface that is already flying up from below reads as two
   events. Vue applies the outer transition's `-enter-active` class to the pill
   for exactly that window, so scoping the suppression to it needs no state. */
.selbar-pop-enter-active .pill-seam-enter-active,
.selbar-pop-enter-active .pill-segment-enter-active {
  transition: none;
}
</style>
