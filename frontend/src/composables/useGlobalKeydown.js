import { onMounted, onUnmounted } from "vue";
import { isReadOnly } from "../utils/apiClient";
import { useReviewSessionsStore } from "../stores/useReviewSessionsStore";
import { useNoticeStore } from "../stores/useNoticeStore";
import { useOperationStore } from "../stores/useOperationStore";
import { useSearchStore } from "../stores/useSearchStore";
import { useSidebarStore } from "../stores/useSidebarStore";
import { isTypingTarget } from "../utils/dom.js";

/**
 * The app-wide keyboard shortcuts: undo/redo, search focus, sidebar and grid
 * navigation.
 *
 * Every modal surface owns its own keyboard handling, so this handler declines
 * whenever one is up rather than competing with it.
 *
 * @param {object} deps
 * @param {import("vue").Ref} deps.gridContainer - the grid, for the shortcuts
 *   it exposes imperatively.
 * @param {import("vue").Ref} deps.sidebarRef - the sidebar, same.
 * @param {import("vue").Ref} deps.shortcutsDialogOpen - the keyboard-help
 *   dialog, which its own shortcut toggles.
 */
/**
 * Coalescing key for the shelf's "no undo here" answer, so holding the chord
 * updates one card instead of stacking them (notice spec §9.1).
 */
export const SHELF_NO_UNDO_KEY = "shelf-no-undo";

export function useGlobalKeydown({
  gridContainer,
  sidebarRef,
  shortcutsDialogOpen,
}) {
  const reviewSessionsStore = useReviewSessionsStore();
  const noticeStore = useNoticeStore();
  const operationStore = useOperationStore();
  const searchStore = useSearchStore();
  const sidebarStore = useSidebarStore();

  /**
   * Is a modal surface (a dialog, the lightbox) currently covering the app?
   *
   * There is no shared flag for this: every dialog owns its own `open` ref, so
   * the honest single source is the scrim Vuetify renders for every active
   * overlay. Used to decline global shortcuts whose feedback would be invisible
   * behind it.
   */
  function isModalOverlayOpen() {
    if (typeof document === "undefined") return false;
    return (
      document.querySelector(".v-overlay--active .v-overlay__scrim") != null
    );
  }

  /**
   * Is the model shelf the mounted destination?
   *
   * The shelf replaces the grid, and it is the one destination that neither
   * writes the operation log nor mounts an `ActionReceipt` - and since it also
   * dropped `UndoControl`, nothing on that screen can narrate an undo or carry
   * the "Changed elsewhere" warning. Read off the same kind of DOM signal the
   * lightbox check above uses, for the same reason: there is no shared
   * which-view-is-up flag, and `.shelf` is v-if'd on the route.
   */
  function isModelShelfOpen() {
    if (typeof document === "undefined") return false;
    return document.querySelector(".shelf") != null;
  }

  function handleGlobalKeydown(e) {
    // The review overlay is modal and owns its own keyboard handler; don't
    // run the app/grid shortcuts (scroll, search, help) behind it.
    if (reviewSessionsStore.overlayOpen) return;
    // The LIGHTBOX owns the keyboard too, with its own undo binding and its own
    // receipt. This used to be enforced implicitly by listener order - the
    // overlay mounted before App, ran first, and stopImmediatePropagation()
    // silenced this handler - but the Duplicates view unmounts and remounts the
    // grid (and the overlay inside it), which re-registers their listeners
    // AFTER this one and silently flips that order. The result was one Ctrl+Z
    // running TWO undos: this handler's, then the overlay's, which the
    // operation store's busy-queue happily executed as a queued second step.
    // Ownership is therefore stated here explicitly, on the same DOM signal the
    // overlay renders (`.image-overlay` is v-if'd on open), not on ordering.
    if (
      typeof document !== "undefined" &&
      document.querySelector(".image-overlay") != null
    ) {
      return;
    }
    // Match the strictness the grid and the lightbox already use: a SELECT and an
    // ARIA textbox are typing surfaces too, and the event target matters as much
    // as `document.activeElement` (a Vuetify combobox moves focus around).
    const isEditable = isTypingTarget(e.target);

    // The auto-hide sidebar is revealed by hover (or tap), so WCAG 2.1 SC 1.4.13
    // "Content on Hover or Focus" applies: it must be dismissible without moving
    // the pointer. Escape is that mechanism, and for a touch user it is the only
    // dismissal besides the scrim itself. Deliberately not preventDefault-ed, and
    // skipped while typing, so the other Escape owners keep theirs. The sidebar
    // context menu stops propagation from a capture-phase listener and never
    // reaches here at all.
    if (
      e.key === "Escape" &&
      !isEditable &&
      sidebarStore.sidebarOverlay &&
      sidebarStore.sidebarVisible
    ) {
      sidebarStore.hideAutoSidebar();
    }

    // Undo / redo. Global by design: the shortcut has to work with no receipt on
    // screen, and every undo raises one, so the result is always narrated. Ctrl
    // and Meta are both accepted (the HINT is platform-specific, the binding is
    // not); Ctrl+Shift+Z is the macOS redo convention and is accepted everywhere.
    //
    // Five guards, each for its own reason:
    //   * typing: a text field keeps its own native undo stack;
    //   * read-only: the endpoints are owner-only anyway;
    //   * auto-repeat: a HELD Ctrl+Z must not walk the whole stack;
    //   * a modal DIALOG owns the screen: the receipt lives on --z-floating,
    //     under any dialog scrim, so an undo fired from there would mutate the
    //     library with no visible narration. That breaks the design's own "every
    //     undo raises a receipt" invariant, so the shortcut declines rather than
    //     acting blind.
    //
    // The MODEL SHELF declines rather than acts, for the same invariant
    // arrived at from the other side: it mounts no receipt and (deliberately)
    // no UndoControl, so the chord there would revert a library action taken
    // on a screen the reader has left, with nothing on this one to say it
    // happened - not even the "Changed elsewhere" warning, whose only renderer
    // is the control the shelf does not have. It declines OUT LOUD: a chord
    // that does nothing at all teaches nothing, and the next thing a reader
    // does is press it again. The notice surface is app-wide and already
    // renders over the shelf, so this costs no new chrome; the coalescing key
    // is what keeps a repeated press one card rather than a wall.
    //
    // The lightbox is NOT covered by that last guard and never was:
    // `isModalOverlayOpen()` looks for a Vuetify scrim, and `.image-overlay`
    // renders its own. The lightbox is excluded by the explicit `.image-overlay`
    // check at the top of this handler (listener ORDER used to do it, until the
    // Duplicates view's grid remount flipped it - see that comment). Undo works
    // in the lightbox through its own key handler plus `OverlayActionReceipt`,
    // fitted to that surface's GUI per the owner's ruling.
    if (
      (e.ctrlKey || e.metaKey) &&
      !e.altKey &&
      !e.repeat &&
      !isEditable &&
      !isReadOnly.value &&
      !isModalOverlayOpen()
    ) {
      const key = e.key?.toLowerCase();
      const wantsUndo = key === "z" && !e.shiftKey;
      const wantsRedo = key === "y" || (key === "z" && e.shiftKey);
      if (wantsUndo || wantsRedo) {
        e.preventDefault();
        if (isModelShelfOpen()) {
          noticeStore.push({
            level: "info",
            key: SHELF_NO_UNDO_KEY,
            text: "Model shelf changes aren't undoable - undo and redo apply to library actions in the grid.",
          });
        } else if (wantsUndo) {
          operationStore.undo();
        } else {
          operationStore.redo();
        }
      }
    }

    const keys = ["Home", "End", "PageUp", "PageDown"];
    if (keys.includes(e.key) && !isEditable) {
      // These keys drive grid scrolling only. Prevent the browser's default
      // scroll so they don't also scroll the sidebar (or the page).
      e.preventDefault();
      const grid = gridContainer.value;
      if (grid && typeof grid.onGlobalKeyPress === "function") {
        grid.onGlobalKeyPress(e.key, e);
      }
    }
    if (e.key === "f" && !e.ctrlKey && !e.metaKey && !e.altKey) {
      if (!isEditable) {
        e.preventDefault();
        searchStore.requestSearchFocus();
      }
    }
    if (
      (e.key === "?" || e.key === "F1") &&
      !e.ctrlKey &&
      !e.metaKey &&
      !e.altKey &&
      !isEditable
    ) {
      e.preventDefault();
      shortcutsDialogOpen.value = !shortcutsDialogOpen.value;
    }
    if (
      e.key === "F2" &&
      !e.ctrlKey &&
      !e.metaKey &&
      !e.altKey &&
      !isEditable &&
      !isReadOnly.value
    ) {
      const gridHasFocus = gridContainer.value?.hasCursorFocus === true;
      if (!gridHasFocus) {
        e.preventDefault();
        sidebarRef.value?.openCurrentSelectionEditor?.();
      }
    }
  }

  onMounted(() => window.addEventListener("keydown", handleGlobalKeydown));
  onUnmounted(() => window.removeEventListener("keydown", handleGlobalKeydown));
}
