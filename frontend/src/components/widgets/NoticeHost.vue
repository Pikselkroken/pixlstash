<script setup>
/**
 * The notice surface - one instance, mounted as the LAST child of
 * `.app-viewport`. Built to `docs/design/notice-surface.md`.
 *
 * Placement (§2.2): fixed, bottom-centre of the app viewport, spanning the full
 * width and centring on it - deliberately NOT tracking the sidebar or stats
 * panel, because the host also has to render on the login screen, over
 * ImageOverlay, over ReviewSessionsOverlay and inside Settings, none of which
 * have a grid column to centre on. One stable anchor beats a conditional one.
 *
 * The column is `pointer-events: none` so it never eats clicks aimed at the grid
 * underneath; only the cards themselves take pointer events.
 *
 * Its block inset is `--notice-safe-bottom` (declared in style.css), which is
 * `--space-5` plus `--floating-bottom-h` - the measured height of whatever
 * bottom-anchored chrome is currently parked in this column's footprint. App.vue
 * owns that variable. Overlap with the selection pill is therefore impossible by
 * construction rather than by a hardcoded guess.
 *
 * The host carries NO role and NO aria-live: the cards announce themselves, and
 * a live region on the container would double-announce them.
 */
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { VIcon } from "vuetify/components";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { NARROW_VIEWPORT_MAX_PX } from "../../utils/floatingBottom";

const props = defineProps({
  /**
   * Render the dark-surface variant (spec §2.5). A white `surface` card over the
   * lightbox is legible but reads as foreign chrome.
   */
  onDark: { type: Boolean, default: false },
});

const store = useNoticeStore();

// Spec §5 - max visible 3, dropping to 2 below 600px.
const NARROW_QUERY = `(max-width: ${NARROW_VIEWPORT_MAX_PX}px)`;
let narrowMql = null;

function applyCap(isNarrow) {
  store.setMaxVisible(isNarrow ? 2 : 3);
}

function onNarrowChange(event) {
  applyCap(event.matches);
}

// Spec §6 rule 3 - the countdown pauses while the tab is hidden. Without this a
// notice pushed just before a tab switch expires unread, which is precisely the
// silently-lost message the store exists to prevent.
function onVisibilityChange() {
  if (typeof document === "undefined") return;
  if (document.hidden) store.pauseAll();
  else store.resumeAll();
}

onMounted(() => {
  if (typeof window !== "undefined" && window.matchMedia) {
    narrowMql = window.matchMedia(NARROW_QUERY);
    applyCap(narrowMql.matches);
    narrowMql.addEventListener("change", onNarrowChange);
  }
  if (typeof document !== "undefined") {
    document.addEventListener("visibilitychange", onVisibilityChange);
  }
});

onBeforeUnmount(() => {
  narrowMql?.removeEventListener?.("change", onNarrowChange);
  if (typeof document !== "undefined") {
    document.removeEventListener("visibilitychange", onVisibilityChange);
  }
});

// Spec §3.1 - outline glyphs throughout, matching the rest of the app.
const GLYPHS = {
  info: "mdi-information-outline",
  success: "mdi-check-circle-outline",
  warning: "mdi-alert-outline",
  error: "mdi-alert-circle-outline",
};

const cards = computed(() => store.visible);

/**
 * Spec §8 - only `error` interrupts. A warning is a partial outcome, not an
 * emergency; announcing it assertively would train users to ignore assertive
 * announcements, which is how the real errors get missed.
 */
function roleFor(level) {
  return level === "error" ? "alert" : "status";
}

// Esc dismisses the newest notice ONLY while focus is inside the host (§6).
// A global binding would steal Esc from the grid selection and the
// SelectionBar menus - a behaviour change that is UI/UX's call, not ours.
function onKeydown(event) {
  if (event.key !== "Escape") return;
  const newest = cards.value[cards.value.length - 1];
  if (!newest) return;
  event.stopPropagation();
  store.dismiss(newest.id);
}

// Hover and focus pause the countdown (WCAG 2.2.1). Tracked per card so one
// hovered notice does not freeze the whole stack.
const hostEl = ref(null);
</script>

<template>
  <div
    ref="hostEl"
    class="notice-host"
    :class="{ 'notice-host--on-dark': props.onDark }"
    @keydown="onKeydown"
  >
    <TransitionGroup name="notice" tag="div" class="notice-stack">
      <div
        v-for="card in cards"
        :key="card.id"
        class="notice-card"
        :class="`notice-card--${card.level}`"
        :role="roleFor(card.level)"
        aria-atomic="true"
        @mouseenter="store.pause(card.id)"
        @mouseleave="store.resume(card.id)"
        @focusin="store.pause(card.id)"
        @focusout="store.resume(card.id)"
      >
        <span class="notice-rail" aria-hidden="true"></span>
        <v-icon class="notice-glyph" size="16" aria-hidden="true">
          {{ GLYPHS[card.level] || GLYPHS.info }}
        </v-icon>
        <span class="notice-message" :title="card.text">{{ card.text }}</span>
        <span
          v-if="card.count > 1"
          class="notice-count"
          :aria-label="`${card.count} occurrences`"
          >×{{ card.count }}</span
        >
        <button
          v-if="card.action"
          type="button"
          class="notice-action"
          @click="store.invokeAction(card.id)"
        >
          {{ card.action.label }}
        </button>
        <button
          type="button"
          class="notice-dismiss"
          aria-label="Dismiss notification"
          @click="store.dismiss(card.id)"
        >
          <v-icon size="16" aria-hidden="true">mdi-close</v-icon>
        </button>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
/* ── The column (spec §2.2) ────────────────────────────────────────────────
   Fixed, full-width, centred, and inert to the pointer. `bottom` transitions so
   the stack rises and settles when the selection pill appears or goes - the
   notices move, the pill never does: it is a control the cursor is heading for
   and a transient message must not displace it. */
.notice-host {
  position: fixed;
  left: 0;
  right: 0;
  bottom: var(--notice-safe-bottom, var(--space-5));
  z-index: var(--z-notice);
  display: flex;
  flex-direction: column;
  align-items: center;
  pointer-events: none;
  transition: bottom var(--dur-2) var(--ease-standard);
}

.notice-stack {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
}

/* ── The card (spec §4) ────────────────────────────────────────────────────
   Opaque on purpose: the selection pill's translucent + backdrop-filter look is
   a legibility gamble over an arbitrary photo grid, and a message the user must
   read does not take that gamble. The status tint is a separate layer over the
   opaque base (::before), so the fill never affects the text. */
.notice-card {
  position: relative;
  isolation: isolate;
  pointer-events: auto;
  box-sizing: border-box;
  width: min(100% - 2 * var(--space-5), var(--notice-max-w));
  min-width: 280px;
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  padding: var(--space-4) var(--space-5);
  padding-inline-start: calc(var(--space-5) + var(--space-2));
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgba(var(--v-theme-on-surface), 0.2);
  border-radius: var(--radius-md);
  /* --elevation-3, not -4: -4 is reserved for dialogs and lightbox chrome, and
     a notice must read as lighter than a modal. */
  box-shadow: var(--elevation-3);
  overflow: hidden;
}

/* Status tint layer - sits over the opaque base, under the content. */
.notice-card::before {
  content: "";
  position: absolute;
  inset: 0;
  z-index: -1;
  background: var(--notice-tint, transparent);
}

/* The rail follows the card's radius because the card clips. */
.notice-rail {
  position: absolute;
  inset-block: 0;
  inset-inline-start: 0;
  width: var(--space-2);
  background: var(--notice-status, transparent);
}

/* Per-variant: ONLY the tint, border and rail carry the hue. The glyph and the
   message stay `on-surface` in every variant - see spec §3.2. Three of the four
   status colours fail the 3:1 non-text floor as a foreground on their own tinted
   card in the light theme, so a coloured glyph would be a variant that silently
   fails on one theme. */
.notice-card--info {
  --notice-status: rgb(var(--v-theme-info));
  --notice-tint: rgba(var(--v-theme-info), 0.08);
  border-color: rgba(var(--v-theme-info), 0.5);
}
.notice-card--success {
  --notice-status: rgb(var(--v-theme-success));
  --notice-tint: rgba(var(--v-theme-success), 0.08);
  border-color: rgba(var(--v-theme-success), 0.5);
}
.notice-card--warning {
  --notice-status: rgb(var(--v-theme-warning));
  --notice-tint: rgba(var(--v-theme-warning), 0.08);
  border-color: rgba(var(--v-theme-warning), 0.5);
}
.notice-card--error {
  --notice-status: rgb(var(--v-theme-error));
  --notice-tint: rgba(var(--v-theme-error), 0.08);
  border-color: rgba(var(--v-theme-error), 0.5);
}

.notice-glyph {
  flex-shrink: 0;
  color: rgb(var(--v-theme-on-surface));
  /* Optical centre on the message's first line. */
  margin-top: 1px;
}

.notice-message {
  flex: 1;
  min-width: 0;
  font-size: var(--text-base);
  font-weight: var(--weight-regular);
  line-height: var(--leading-snug);
  color: rgb(var(--v-theme-on-surface));
  /* Clamp at 3 lines; the full string stays reachable via `title`, so nothing
     is permanently truncated out of reach. */
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* ×N coalescing count (spec §5). */
.notice-count {
  flex-shrink: 0;
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface), 0.7);
  line-height: var(--leading-snug);
  margin-top: 1px;
}

/* Action - underlined `on-surface`, NOT `primary`: olive primary on the tinted
   card measures 4.30:1 at 13px, at or under the 4.5 floor. Underlined semibold
   on-surface is 14.3:1 and is unambiguously actionable without colour. */
.notice-action {
  flex-shrink: 0;
  margin-inline-start: auto;
  font-family: inherit;
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-on-surface));
  text-decoration: underline;
  text-underline-offset: 2px;
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
}

.notice-action:hover {
  background: var(--hover-wash);
}

.notice-dismiss {
  position: relative;
  flex-shrink: 0;
  width: 24px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border-radius: var(--radius-sm);
  color: rgba(var(--v-theme-on-surface), 0.7);
}

/* Hit area expanded to 40×40 (WCAG 2.5.8 floor is 24×24) without punching a
   40px hole in the layout. */
.notice-dismiss::before {
  content: "";
  position: absolute;
  inset: -8px;
}

.notice-dismiss:hover {
  color: rgb(var(--v-theme-on-surface));
  background: var(--hover-wash);
}

/* ── Dark-surface variant (spec §2.5) ─────────────────────────────────────── */
.notice-host--on-dark .notice-card {
  background: rgb(var(--v-theme-dark-surface));
  border-color: rgba(var(--v-theme-on-dark-surface), 0.2);
}
.notice-host--on-dark .notice-glyph,
.notice-host--on-dark .notice-message,
.notice-host--on-dark .notice-action {
  color: rgb(var(--v-theme-on-dark-surface));
}
.notice-host--on-dark .notice-count,
.notice-host--on-dark .notice-dismiss {
  color: rgba(var(--v-theme-on-dark-surface), 0.7);
}
.notice-host--on-dark .notice-dismiss:hover {
  color: rgb(var(--v-theme-on-dark-surface));
}
/* The hue swaps too, not just the alpha. A `dark-surface` stays dark in both
   themes, so the theme's own status hues are the wrong values on it - the light
   theme's deepened `success` reads 2.96:1 against this card. The dark-surface
   status set is tuned for exactly this and measures 4.12:1 – 5.46:1 there. */
.notice-host--on-dark .notice-card--info {
  --notice-status: rgb(var(--v-theme-dark-surface-info));
  --notice-tint: rgba(var(--v-theme-dark-surface-info), 0.14);
}
.notice-host--on-dark .notice-card--success {
  --notice-status: rgb(var(--v-theme-dark-surface-success));
  --notice-tint: rgba(var(--v-theme-dark-surface-success), 0.14);
}
.notice-host--on-dark .notice-card--warning {
  --notice-status: rgb(var(--v-theme-dark-surface-warning));
  --notice-tint: rgba(var(--v-theme-dark-surface-warning), 0.14);
}
.notice-host--on-dark .notice-card--error {
  --notice-status: rgb(var(--v-theme-dark-surface-error));
  --notice-tint: rgba(var(--v-theme-dark-surface-error), 0.14);
}

/* ── Narrow viewports (spec §2.4) ─────────────────────────────────────────── */
@media (max-width: 600px) {
  .notice-card {
    width: calc(100% - 2 * var(--space-4));
    min-width: 0;
  }
}

/* ── Motion (spec §7) ─────────────────────────────────────────────────────── */
.notice-enter-active {
  transition:
    opacity var(--dur-2) var(--ease-decelerate),
    transform var(--dur-2) var(--ease-decelerate);
}

.notice-leave-active {
  transition:
    opacity var(--dur-1) var(--ease-accelerate),
    transform var(--dur-1) var(--ease-accelerate);
  /* Taken out of flow so the siblings' FLIP move is the only reflow. */
  position: absolute;
}

.notice-enter-from {
  opacity: 0;
  transform: translateY(var(--space-4));
}

.notice-leave-to {
  opacity: 0;
  transform: translateY(var(--space-2));
}

/* Stack reflow: transform only - never an animated height, which would reflow
   the whole column every frame. */
.notice-move {
  transition: transform var(--dur-2) var(--ease-standard);
}

/* design-tokens.css already collapses every duration globally under reduced
   motion; this is the extra step that file cannot do - a zero-duration
   transition still SNAPS a translateY into place, which is the flicker the
   setting exists to prevent. Opacity-only cross-fade, no travel, no FLIP. */
@media (prefers-reduced-motion: reduce) {
  .notice-enter-from,
  .notice-leave-to {
    transform: none;
  }
  .notice-move,
  .notice-host {
    transition: none;
  }
}
</style>
