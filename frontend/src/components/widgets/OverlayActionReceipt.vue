<script setup>
/**
 * The lightbox's own narration of an undoable action.
 *
 * The owner ruled that undo must work inside the lightbox, and that the
 * affordance may be fitted differently there because the lightbox has its own
 * GUI. So this is not the grid pill promoted above the modal layer - it is the
 * same receipt, rendered in the overlay's own material.
 *
 * What is identical (all of it from `useActionReceipt`, so the two surfaces
 * cannot drift): the wording, the glyphs, the keycaps, the dwell window and its
 * hairline drain, the hover/focus/hidden-tab pause, the in-place flip to
 * "Undone … Redo", the "Can't be undone" limit instead of a dead button, and the
 * store's one-receipt-at-a-time rule.
 *
 * What is different, and why:
 *
 *   • Material. `dark-surface` / `on-dark-surface` at 0.9, the exact fill
 *     `.overlay-topbar` and `.overlay-rail` already carry, so it reads as this
 *     surface's chrome rather than as an imported card. `--elevation-4` is the
 *     rung visual-language.md §7 names for lightbox chrome; the grid pill takes
 *     -3 precisely because -4 was reserved for here.
 *   • NO live region. The grid's `ActionReceipt` stays mounted underneath and
 *     the lightbox does not `inert` or `aria-hidden` it, so its persistent
 *     `role="status"` region still speaks. A second region here would be
 *     guaranteed double-speak, not a risk of it.
 *   • A scope clause. In the grid you made the selection and can watch the
 *     tiles change; here you can see exactly one picture while Ctrl+Z reverts
 *     an action across thousands. Above one target the pill says how wide the
 *     step was, derived from the count alone so it stays true no matter which
 *     picture is on screen. Nothing here ever says "this picture" - that would
 *     become a lie the instant the user presses the right arrow.
 *   • No History popover. Choosing a step is a browsing task whose preview has
 *     no visible referent on a surface showing one picture. Repeated Ctrl+Z
 *     still walks the stack, and the toolbar control is one Escape away.
 */
import { computed, ref } from "vue";

import { useActionReceipt } from "../../composables/useActionReceipt";

defineProps({
  /**
   * The overlay's chrome-hidden state. The rail and the sidebar are only
   * opacity-hidden and keep their width, so the band has to collapse to the
   * whole canvas or the pill sits off-centre against a rail nobody can see.
   */
  chromeHidden: { type: Boolean, default: false },
});

// `announce: false`: the grid's receipt owns the single app-wide live region.
const {
  store,
  receipt,
  undone,
  blocked,
  glyph,
  actionLabel,
  actionGlyph,
  keyHint,
  text,
  actionKeyShortcut,
  drainStyle,
  pillKey,
  pause,
  resume,
  takeAction,
} = useActionReceipt({ announce: false });

const rootEl = ref(null);

/**
 * How wide the step was, when it was wider than the picture on screen.
 *
 * Derived from `targetCount` only, never from the displayed picture, so
 * navigating away cannot falsify it. Absent at one target: "Just this picture"
 * would be noise on the common case and false the moment the user moves on.
 */
const scopeNote = computed(() => {
  const count = Number(receipt.value?.targetCount);
  if (!Number.isFinite(count) || count <= 1) return "";
  const pictures = `${count.toLocaleString()} pictures`;
  return undone.value
    ? `Reverted across ${pictures}, not just this one`
    : `Across ${pictures}, not just this one`;
});

/** The button's accessible name: the verb alone does not say what it undoes. */
const actionAccessibleName = computed(
  () => `${actionLabel.value}: ${text.value}`,
);

function onAction(event) {
  return takeAction(event, () =>
    rootEl.value?.querySelector?.(".r-btn")?.focus?.(),
  );
}

/** Does the keyboard currently sit inside the pill? Drives the Escape guard. */
function containsFocus() {
  if (typeof document === "undefined" || !rootEl.value) return false;
  return rootEl.value.contains(document.activeElement);
}

defineExpose({
  containsFocus,
  /** Escape pressed with focus in the pill: retire it, keep the lightbox. */
  dismiss() {
    store.dismissReceipt();
  },
});
</script>

<template>
  <!-- Pointer-transparent band; only the pill takes pointer events, so a click
       aimed at the image behind it is never eaten. `@click.stop` on the pill
       keeps the overlay's "any click reveals the chrome" handler from dragging
       the whole chrome back when the user just wanted Undo. -->
  <div
    ref="rootEl"
    class="overlay-receipt-slot"
    :class="{ 'chrome-hidden': chromeHidden }"
    data-testid="overlay-action-receipt-slot"
  >
    <transition name="receipt">
      <div
        v-if="receipt"
        :key="pillKey"
        class="overlay-receipt"
        :class="{
          'overlay-receipt--undone': undone,
          'overlay-receipt--blocked': blocked,
        }"
        :style="drainStyle"
        data-testid="overlay-action-receipt"
        @click.stop
        @mouseenter="pause"
        @mouseleave="resume"
        @focusin="pause"
        @focusout="resume"
      >
        <v-icon class="r-ico" size="18">{{ glyph }}</v-icon>
        <span class="r-body">
          <span class="r-text">{{ text }}</span>
          <span v-if="scopeNote" class="r-scope">{{ scopeNote }}</span>
        </span>
        <span v-if="receipt.mergedCount > 0" class="r-more"
          >+{{ receipt.mergedCount }}</span
        >
        <span v-if="blocked" class="r-limit">Can't be undone</span>
        <template v-else>
          <button
            type="button"
            class="r-btn"
            :aria-disabled="store.busy"
            :aria-label="actionAccessibleName"
            :aria-keyshortcuts="actionKeyShortcut"
            @click="onAction"
          >
            <v-icon size="16">{{ actionGlyph }}</v-icon>
            <span>{{ actionLabel }}</span>
          </button>
          <span class="kbdhint" aria-hidden="true">
            <kbd v-for="key in keyHint" :key="key">{{ key }}</kbd>
          </span>
        </template>
        <span class="r-progress run" aria-hidden="true"></span>
      </div>
    </transition>
  </div>
</template>

<style scoped>
/* The lightbox's transient-status lane. Centred on the VISIBLE image, not on
   the viewport, so the pill never drifts under the filmstrip rail or the
   sidebar. `--filmstrip-rail-width` is declared on `.overlay-main`
   (filmstripStyleVars), `--sidebar-width` on `.overlay-shell`.

   64px: the line `.overlay-progress--comfyui` already establishes, and one
   --space-8 of clearance above the 16px hint lane whose tallest occupant is
   ~30px. The 16px lane was originally rejected because the old `.zoom-hud`
   lived there persistently; the hud has since retired into the toolbar
   button's readout, but the lane still hosts the swipe/chrome hints, so the
   placement stands.

   z-index 6 is the overlay's own trapped local scale (visual-language.md §14
   sanctions it): the same rung as `.overlay-progress`, which is the same class
   of object. */
.overlay-receipt-slot {
  position: absolute;
  bottom: calc(var(--space-5) + var(--space-8));
  left: var(--filmstrip-rail-width, 0px);
  right: var(--sidebar-width, 0px);
  z-index: 6;
  display: flex;
  justify-content: center;
  pointer-events: none;
  /* `--sidebar-width` flips instantly (0 to 320px) while `.overlay-sidebar`
     slides its width over 200ms. Without this the pill jumps mid-glide. */
  transition:
    left var(--dur-2) var(--ease-standard),
    right var(--dur-2) var(--ease-standard);
}

/* Chrome hidden: the rail and the sidebar are opacity-hidden but keep their
   width, so the band has to collapse to the whole canvas or the pill sits
   visibly off-centre against a rail nobody can see. */
.overlay-receipt-slot.chrome-hidden {
  left: 0;
  right: 0;
}

/* The grid pill's recipe in this surface's material. Capped, unlike the grid
   pill: the cap is what keeps it clear of the progress cards on a narrow
   window. Deliberately NO overflow: hidden - it would clip both the drain's
   tail inside the cap radius and the focus ring on the Undo button. */
.overlay-receipt {
  position: relative;
  width: max-content;
  max-width: min(var(--notice-max-w), calc(100% - var(--space-6)));
  display: flex;
  align-items: center;
  gap: var(--space-3);
  /* 6px block padding: the carried off-grid value the grid pill documents, and
     independently `.overlay-swipe-hint`'s own padding on this surface. */
  padding: 6px var(--space-4);
  border-radius: var(--radius-pill);
  background: rgba(var(--v-theme-dark-surface), 0.9);
  /* 0.2, not the grid pill's 0.14: `.overlay-nav` already solves this exact
     problem (a bordered floating control over an arbitrary photo) at 0.2, and
     a 0.14 hairline over a bright photo is not an edge. */
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.2);
  /* -4, not the grid pill's -3. §7 assigns -4 to lightbox chrome, and the grid
     pill takes -3 precisely because -4 was reserved for here. */
  box-shadow: var(--elevation-4);
  backdrop-filter: blur(12px);
  pointer-events: auto;
}

.r-ico {
  color: rgb(var(--v-theme-on-dark-surface));
  flex-shrink: 0;
}

.r-body {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.r-text {
  font-size: var(--text-base);
  line-height: var(--leading-snug);
  color: rgb(var(--v-theme-on-dark-surface));
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* How wide the step was. Secondary weight, at the same treatment as the limit
   note, because it qualifies the sentence above rather than competing with it. */
.r-scope {
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.r-more {
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-dark-surface), 0.7);
  flex-shrink: 0;
}

.r-limit {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
  padding-inline: var(--space-3);
  white-space: nowrap;
  flex-shrink: 0;
}

.r-btn {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  color: rgb(var(--v-theme-on-dark-surface));
  font-family: inherit;
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  text-decoration: underline;
  text-underline-offset: 2px;
}
/* `--hover-wash` is `rgba(accent, 0.14)` in BOTH themes, so it composites to a
   visible warm lift on this dark fill without a variant. */
.r-btn:hover:not([aria-disabled="true"]) {
  background: var(--hover-wash);
}
/* `aria-disabled`, never the attribute: disabling a control the keyboard is on
   moves focus to <body>, and this button flips to Redo when the trip lands. */
.r-btn[aria-disabled="true"] {
  opacity: 0.35;
  cursor: default;
}

.kbdhint {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  flex-shrink: 0;
}

/* The global App.css `kbd` rule with `on-background` swapped for
   `on-dark-surface`. The global rule is right in geometry and wrong in colour
   here: light-theme `on-background` is near-black, i.e. a dark fill and a dark
   border on a dark pill. */
.overlay-receipt kbd {
  display: inline-block;
  padding: var(--space-1) var(--space-2);
  font-size: var(--text-2xs);
  font-family: var(--font-mono);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.2);
  border-radius: var(--radius-sm);
  line-height: 1.5;
}

/* ── Countdown drain ──────────────────────────────────────────────────────── */
.r-progress {
  position: absolute;
  inset-inline: var(--space-7);
  bottom: var(--space-1);
  height: var(--countdown-h);
  border-radius: var(--radius-pill);
  background: rgba(var(--v-theme-on-dark-surface), 0.12);
  overflow: hidden;
  pointer-events: none;
}

/* scaleX, never an animated `width`: width relayouts the pill every frame. */
.r-progress::after {
  content: "";
  display: block;
  height: 100%;
  border-radius: inherit;
  background: rgba(var(--v-theme-on-dark-surface), 0.45);
  transform: scaleX(1);
  transform-origin: left center;
  will-change: transform;
}

.r-progress.run::after {
  animation: r-drain var(--r-drain-dur, 5000ms) linear forwards;
}

@keyframes r-drain {
  from {
    transform: scaleX(1);
  }
  to {
    transform: scaleX(0);
  }
}

/* WCAG 2.2.1. The store's dismissal timer pauses on the same two conditions. */
.overlay-receipt:hover .r-progress.run::after,
.overlay-receipt:focus-within .r-progress.run::after {
  animation-play-state: paused;
}

/* The grid pill's timing, deliberately identical: the receipt's arrival should
   read the same on both surfaces. The 120% rise starts inside the canvas and
   nothing clips it, which is one reason this mounts outside `.overlay-canvas`
   and its `overflow: hidden`. */
.receipt-enter-active {
  transition:
    transform var(--dur-3) var(--ease-decelerate),
    opacity var(--dur-3) var(--ease-decelerate);
}
.receipt-leave-active {
  transition:
    transform var(--dur-2) var(--ease-accelerate),
    opacity var(--dur-2) var(--ease-accelerate);
}
.receipt-enter-from,
.receipt-leave-to {
  transform: translateY(120%);
  opacity: 0;
}

@media (prefers-reduced-motion: reduce) {
  /* The travel goes; a zero-duration transition still snaps a translate into
     place, so it has to be removed rather than shortened. */
  .receipt-enter-from,
  .receipt-leave-to {
    transform: none;
  }
  /* The drain STAYS. It is essential animation (WCAG 2.3.3): the hairline is
     the time-remaining readout, and the global reduced-motion collapse would
     run it to completion in 0.001ms and leave it frozen at scaleX(0),
     asserting "expired" about a live receipt.

     The grid pill justifies this by pointing at the toolbar control surviving
     the receipt. That fallback is FALSE here - `UndoControl` is occluded by the
     lightbox. What survives instead is the Ctrl+Z binding this overlay's own
     key handler keeps live, plus the hover/focus pause. Same conclusion, and a
     2px linear hairline is at the bottom of the vestibular-risk scale, but the
     reasoning had to be re-made rather than copied. */
  .r-progress.run::after {
    animation-duration: var(--r-drain-dur, 5000ms) !important;
  }
}
</style>
