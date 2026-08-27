<script setup>
/**
 * The action receipt - a transient pill that narrates what just happened and
 * offers to take it back. One instance, rendered over the grid in the same slot
 * as the floating selection bar.
 *
 * Built to the owner's "Undo / Redo System" design:
 *
 *   • Icon + human summary + Undo + the keyboard shortcut, so the shortcut is
 *     taught at the moment it is useful.
 *   • A hairline drains over the dwell window (5s, 8s for a destructive action).
 *     Hover or focus pauses it - WCAG 2.2.1, and the pause is on the store's
 *     dismissal timer too, so the pill cannot vanish out from under a cursor
 *     that is on its way to Undo.
 *   • Undoing flips the pill IN PLACE to "Undone - …" with Redo. Two receipts
 *     are never stacked; the store holds exactly one and the newest replaces it.
 *   • A one-way operation still gets a receipt, stating the limit. Never a dead
 *     Undo button.
 *
 * The pill is not a notice (notice-surface.md governs those). It sits on
 * `--z-floating` with the selection pill and the breadcrumb, and it registers
 * with `useBottomAnchor` so the notice stack clears it by construction rather
 * than by a hardcoded guess.
 *
 * Stacking: when the selection bar is up, the receipt sits above it. The lift is
 * carried as padding on the (pointer-transparent) wrapper, so the wrapper's
 * measured border box is the FULL height this component occupies on the bottom
 * edge - which is exactly what the anchor registry needs to report.
 */
import { computed, ref } from "vue";

import { useActionReceipt } from "../../composables/useActionReceipt";
import { useBottomAnchor } from "../../composables/useBottomAnchor";

const props = defineProps({
  /**
   * Lift the pill clear of another bottom-anchored element (the selection bar),
   * in pixels. Measured by the caller - never assumed, because the pill wraps
   * and grows on coarse pointers.
   */
  liftPx: { type: Number, default: 0 },
  /**
   * Hide the PILL while another surface renders the same receipt (the lightbox
   * has its own narration in its own chrome). The component stays mounted: it
   * carries the one app-wide live region, and the lightbox deliberately has
   * none, so unmounting this would silence every undo announcement made there.
   */
  pillHidden: { type: Boolean, default: false },
});

// The receipt contract - wording, glyphs, keycaps, the drain window, the
// hover/focus/hidden-tab pause and the focus-preserving action - is shared with
// the lightbox's own narration (`OverlayActionReceipt.vue`). This surface owns
// the single app-wide live region; the lightbox's does not announce.
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
  announcement,
  pause,
  resume,
  takeAction,
} = useActionReceipt();

/** The button's accessible name: the verb alone does not say what it undoes. */
const actionAccessibleName = computed(
  () => `${actionLabel.value}: ${text.value}`,
);

const wrapperStyle = computed(() => ({
  paddingBottom: `${Math.max(0, props.liftPx)}px`,
}));

// Registered so the notice stack sits clear of whatever this occupies. The
// wrapper (pill + lift) is the measured element, not the pill alone.
const wrapperEl = ref(null);
useBottomAnchor("action-receipt", wrapperEl);

// The pill is REPLACED (a new node, keyed on the store's raise counter) when
// undo flips it to "Undone … Redo", so the keyboard has to be put back on the
// button the flip produced (WCAG 2.4.3). The button is `aria-disabled` rather
// than `disabled` for the same reason: disabling a focused control moves focus
// off it.
function onUndo(event) {
  return takeAction(event, () =>
    wrapperEl.value?.querySelector?.(".r-btn")?.focus?.(),
  );
}
</script>

<template>
  <div
    ref="wrapperEl"
    class="receipt-slot"
    :style="wrapperStyle"
    data-testid="action-receipt-slot"
  >
    <!-- One persistent region, always mounted, so an announcement is never
         raced by the node that carries it. It is also the ONLY one in the app:
         the lightbox's narration deliberately has none, because the lightbox
         does not `inert` or `aria-hidden` the grid, so this region still speaks
         from underneath it. Do not gate it on the lightbox being closed, and do
         not add a second one there.

         If the lightbox is ever made a proper modal (`aria-modal` + `inert`),
         this region goes inert with the rest of the grid and undo announcements
         go silent everywhere. The fix then is to MOVE this one region up beside
         NoticeHost, not to duplicate it. -->
    <span
      class="visually-hidden"
      role="status"
      aria-live="polite"
      aria-atomic="true"
      data-testid="action-receipt-announcement"
      >{{ announcement }}</span
    >
    <transition name="receipt">
      <div
        v-if="receipt && !pillHidden"
        :key="pillKey"
        class="receipt"
        :class="{ 'receipt--undone': undone, 'receipt--blocked': blocked }"
        :style="drainStyle"
        @mouseenter="pause"
        @mouseleave="resume"
        @focusin="pause"
        @focusout="resume"
      >
        <v-icon class="r-ico" size="18">{{ glyph }}</v-icon>
        <span class="r-text">{{ text }}</span>
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
            @click="onUndo"
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
/* The slot spans from the bottom edge up through the pill. Pointer-transparent
   so it never eats a click aimed at the grid underneath; only the pill itself
   takes pointer events. */
.receipt-slot {
  position: absolute;
  bottom: var(--space-5);
  left: 0;
  right: 0;
  z-index: var(--z-floating);
  display: flex;
  justify-content: center;
  pointer-events: none;
  /* The lift changes when the selection pill appears or leaves; without this
     the receipt jumps instead of moving with it. */
  transition: padding-bottom var(--dur-2) var(--ease-standard);
}

/* Visually identical to `.floating-selection-bar`, deliberately: the two share
   one slot and are mutually exclusive in time, so two surfaces here would read
   as two systems.
   --elevation-3, not -4: -4 is reserved for dialogs and lightbox chrome, and
   the pill this mirrors is -3 for exactly that reason. */
.receipt {
  position: relative;
  width: max-content;
  max-width: calc(100% - var(--space-6));
  display: flex;
  align-items: center;
  gap: var(--space-3);
  /* 6px block padding matches the selection pill's occupied height. Off-grid
     and carried deliberately: that dimension belongs to the UI/UX-gated
     action-bar reconciliation (visual-language.md §5/§13), not to this change. */
  padding: 6px var(--space-4);
  border-radius: var(--radius-pill);
  background: rgba(var(--v-theme-surface), 0.86);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.14);
  box-shadow: var(--elevation-3);
  backdrop-filter: blur(12px);
  pointer-events: auto;
  /* Deliberately NO overflow: hidden - it would clip both the drain's tail
     inside the pill's cap radius and the focus ring on the inner controls. */
}

.r-ico {
  color: rgb(var(--v-theme-on-surface));
  flex-shrink: 0;
}

.r-text {
  font-size: var(--text-base);
  line-height: var(--leading-snug);
  color: rgb(var(--v-theme-on-surface));
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* The coalescing count. Not a badge: a badge overlays its host without shifting
   layout, and this sits inline in the pill's flow. `+N` rather than `×N` -
   "N more merged into this step", which is what coalescing actually did. */
.r-more {
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface), 0.7);
  flex-shrink: 0;
}

/* The limit, stated up front on a one-way operation, in place of the button. */
.r-limit {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
  padding-inline: var(--space-3);
  white-space: nowrap;
  flex-shrink: 0;
}

/* Underlined semibold `on-surface`, not a `primary` label: an accent or primary
   foreground is never small body text, and the underline makes the control
   unambiguously actionable without relying on colour (WCAG 1.4.1). */
.r-btn {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  color: rgb(var(--v-theme-on-surface));
  font-family: inherit;
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  text-decoration: underline;
  text-underline-offset: 2px;
}
.r-btn:hover:not([aria-disabled="true"]) {
  background: var(--hover-wash);
}
/* `aria-disabled`, never the attribute: disabling a control the keyboard is
   currently ON moves focus to <body>, and this button flips to Redo the moment
   the round trip lands. */
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

/* ── Countdown drain ─────────────────────────────────────────────────────────
   Inset to the pill's straight run. The cap radius is half the pill height
   (~27px), so a full-bleed hairline would need `overflow: hidden` on the pill,
   which clips the hairline's own tail AND the focus ring on the inner controls
   (a WCAG 2.4.11 regression). --space-7 clears the cap; --space-1 is the
   documented hairline inset. */
.r-progress {
  position: absolute;
  inset-inline: var(--space-7);
  bottom: var(--space-1);
  height: var(--countdown-h);
  border-radius: var(--radius-pill);
  background: rgba(var(--v-theme-on-surface), 0.12);
  overflow: hidden;
  pointer-events: none;
}

/* scaleX, never an animated `width`: width relayouts the pill every frame. */
.r-progress::after {
  content: "";
  display: block;
  height: 100%;
  border-radius: inherit;
  background: rgba(var(--v-theme-on-surface), 0.45);
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

/* WCAG 2.2.1. The store's dismissal timer pauses on the same two conditions, so
   a frozen hairline never sits under a pill that vanishes anyway. */
.receipt:hover .r-progress.run::after,
.receipt:focus-within .r-progress.run::after {
  animation-play-state: paused;
}

/* Enter is a 250ms rise with no bounce, exit is quicker - the design's timing.
   It is slower than the selection pill's --dur-2/--dur-1 on purpose: the two
   never co-occur, and a slightly slower arrival reads as "read me". */
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
     the time-remaining readout, not decoration, and the global reduced-motion
     collapse would run it to completion in 0.001ms and leave it frozen at
     scaleX(0) - asserting "expired" about a live receipt. A 2px linear hairline
     is at the bottom of the vestibular-risk scale. Defensible only because the
     drain is never the deadline on the capability: undo survives the receipt in
     the toolbar control, and hover/focus pauses the clock. */
  .r-progress.run::after {
    animation-duration: var(--r-drain-dur, 5000ms) !important;
  }
}
</style>
