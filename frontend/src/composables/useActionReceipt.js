// useActionReceipt.js - the receipt contract, once, for every surface that
// narrates it.
//
// The store (`useOperationStore`) holds at most ONE live receipt and owns its
// dwell timer. What a receipt *reads as* - which glyph, which sentence, which
// verb on the button, which keycaps, how long the hairline drains, when the
// countdown freezes - is the same on every surface that shows it. Only the
// chrome differs: the grid gets a light pill in the selection bar's slot
// (`ActionReceipt.vue`), the lightbox gets a dark HUD in its own vocabulary
// (`OverlayActionReceipt.vue`).
//
// So the contract lives here and the components are left with markup and
// styles. That is also what keeps the two honest: a change to the wording, the
// pause rule or the announcement cannot land on one surface and miss the other.
//
// The one thing that is NOT shared is the live region. Exactly one region may
// announce the receipt app-wide, or a screen reader hears every action twice
// (the grid stays mounted underneath the lightbox). Pass `announce: false` on
// the secondary surface; the primary one keeps speaking.

import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";

import { useOperationStore } from "../stores/useOperationStore";
import {
  isApplePlatform,
  redoKeyHint,
  undoKeyHint,
} from "../utils/shortcutHints";

/** Settling window before the live region speaks, so a burst reads once. */
const ANNOUNCE_THROTTLE_MS = 350;

/**
 * Everything a receipt surface needs except its markup.
 *
 * @param {Object} [options]
 * @param {boolean} [options.announce=true] - own the single app-wide live
 *   region. Only one surface may; a second one double-speaks.
 * @returns {Object} the receipt view model plus its lifecycle handlers.
 */
export function useActionReceipt({ announce = true } = {}) {
  const store = useOperationStore();

  const receipt = computed(() => store.receipt);
  const undone = computed(() => receipt.value?.mode === "undone");
  const blocked = computed(() => receipt.value?.mode === "blocked");

  /** The glyph: the action's own icon, or the undo/limit glyph once it flips. */
  const glyph = computed(() => {
    if (!receipt.value) return "";
    if (undone.value) return "mdi-undo-variant";
    if (blocked.value) return "mdi-information-outline";
    return receipt.value.icon;
  });

  const actionLabel = computed(() => (undone.value ? "Redo" : "Undo"));
  const actionGlyph = computed(() =>
    undone.value ? "mdi-redo-variant" : "mdi-undo-variant",
  );
  const keyHint = computed(() =>
    undone.value ? redoKeyHint() : undoKeyHint(),
  );

  /**
   * The sentence. A multi-step undo says how far it went instead of naming only
   * the oldest step it reverted, which would understate what just happened.
   *
   * An action that deliberately left something alone gets a SECOND sentence
   * here (`entry.note`), on the same pill rather than in a notice of its own:
   * splitting one action across two surfaces is how the half that needed a
   * decision gets dismissed with the half that did not. The note is dropped
   * once the pill flips to "Undone", where it would describe work that has just
   * been taken back.
   */
  const text = computed(() => {
    const entry = receipt.value;
    if (!entry) return "";
    if (undone.value && entry.steps > 1) {
      return `Undone ${entry.steps} steps: ${entry.summary}`;
    }
    if (undone.value) return `Undone: ${entry.summary}`;
    return entry.note ? `${entry.summary}. ${entry.note}` : entry.summary;
  });

  /** The standard attribute for "this control has a keyboard shortcut". */
  const actionKeyShortcut = computed(() =>
    undone.value
      ? isApplePlatform()
        ? "Shift+Meta+Z"
        : "Control+Y"
      : isApplePlatform()
        ? "Meta+Z"
        : "Control+Z",
  );

  // The drain's duration is a dismissal timeout, not a motion token: the motion
  // scale tops out at 420ms. It is handed to CSS as a custom property so the
  // hairline and the store's timer always describe the same window.
  const drainStyle = computed(() => ({
    "--r-drain-dur": `${receipt.value?.durationMs ?? 0}ms`,
  }));

  // Keyed on the store's raise counter so a receipt REPLACED in place still
  // remounts and the drain restarts from full.
  const pillKey = computed(() => receipt.value?.key ?? 0);

  // WCAG 2.2.1 - the countdown freezes on hover and on focus-within, and each
  // surface's CSS pauses its hairline on the same two conditions so the two
  // never disagree.
  function pause() {
    store.pauseReceipt();
  }
  function resume() {
    store.resumeReceipt();
  }

  // …and while the tab is hidden, or a receipt raised just before a tab switch
  // expires unread. Mirrors NoticeHost's handling of the same hazard.
  function onVisibilityChange() {
    if (typeof document === "undefined") return;
    if (document.hidden) store.pauseReceipt();
    else store.resumeReceipt();
  }

  // What the persistent region says. Throttled so a burst announces the outcome
  // once, rather than reading every intermediate step aloud.
  const announcement = ref("");
  let announceTimer = null;

  if (announce) {
    watch(text, (value) => {
      if (announceTimer != null) clearTimeout(announceTimer);
      if (!value) {
        announcement.value = "";
        return;
      }
      announceTimer = setTimeout(() => {
        announceTimer = null;
        announcement.value = value;
      }, ANNOUNCE_THROTTLE_MS);
    });
  }

  onMounted(() => {
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onVisibilityChange);
    }
  });

  onBeforeUnmount(() => {
    if (typeof document !== "undefined") {
      document.removeEventListener("visibilitychange", onVisibilityChange);
    }
    if (announceTimer != null) clearTimeout(announceTimer);
    // A surface that unmounts mid-hover (a view change under the pointer)
    // would otherwise leave the countdown frozen forever: no mouseleave will
    // ever fire, so the stale receipt survives in the store and resurfaces on
    // whichever surface renders next. Releasing the pause lets the countdown
    // finish - or dismiss immediately if it had already drained.
    store.resumeReceipt();
  });

  // A receipt raised while the tab is hidden starts with a running countdown
  // (the store arms it unconditionally). Re-apply the hidden-tab pause so it
  // does not expire unseen - the same hazard `onVisibilityChange` covers for a
  // receipt that was already up.
  watch(pillKey, () => {
    if (typeof document !== "undefined" && document.hidden) {
      store.pauseReceipt();
    }
  });

  /**
   * Take the action and keep the keyboard where it was.
   *
   * The surface's button is REPLACED (a new node, keyed on the store's raise
   * counter) when undo flips it to "Undone … Redo". Without this, a user who
   * reached Undo with the keyboard has focus dropped to `<body>` and has to tab
   * from the top of the document to reach the Redo the flip just produced -
   * WCAG 2.4.3.
   *
   * @param {Event} event - the click, so the caller need not track focus.
   * @param {Function} refocus - called after the flip to restore the keyboard.
   * @returns {Promise<void>}
   */
  async function takeAction(event, refocus) {
    if (store.busy) return;
    const hadFocus = event?.currentTarget === document.activeElement;
    await (undone.value ? store.redo() : store.undo());
    if (!hadFocus) return;
    await nextTick();
    refocus?.();
  }

  return {
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
  };
}
