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
 * Coalescing key for the "nothing that way" answer beside a local receipt, so
 * holding the chord updates one card instead of stacking them (notice spec
 * §9.1).
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

  function handleGlobalKeydown(e) {
    // The review overlay is modal and owns its own keyboard handler; don't
    // run the app/grid shortcuts (scroll, search, help) behind it.
    if (reviewSessionsStore.overlayOpen) return;
    // The LIGHTBOX owns the keyboard too, with its own undo binding and its own
    // receipt. This used to be enforced implicitly by listener order - the
    // lightbox mounted before App, ran first, and stopImmediatePropagation()
    // silenced this handler - but the Duplicates view unmounts and remounts the
    // grid (and the lightbox inside it), which re-registers their listeners
    // AFTER this one and silently flips that order. The result was one Ctrl+Z
    // running TWO undos: this handler's, then the lightbox's, which the
    // operation store's busy-queue happily executed as a queued second step.
    // Ownership is therefore stated here explicitly, on the same DOM signal the
    // lightbox renders (`.lightbox` is v-if'd on open), not on ordering.
    if (
      typeof document !== "undefined" &&
      document.querySelector(".lightbox") != null
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

    // Escape leaves the sidebar's Find in library from anywhere, not only from
    // its field (#2448): the filter keeps the sidebar narrowed after focus has
    // moved to the grid. Not while typing elsewhere or under a dialog, and not
    // preventDefault-ed, so the other Escape owners (the grid's selection)
    // keep theirs.
    if (e.key === "Escape" && !isEditable && !isModalOverlayOpen()) {
      sidebarRef.value?.clearFind?.();
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
    // Every destination that replaces the grid (Duplicates, the model shelf,
    // Workflows, Moves, Insights) mounts `UndoControl` in its toolbar tail
    // (it rides in `TbGlobalActions`) and an `ActionReceipt`, so the chord
    // acts there too: the control names the step and carries the "Changed
    // elsewhere" warning, and the receipt narrates the result. They used to
    // decline (#1415) because they had neither.
    //
    // The lightbox is NOT covered by that last guard and never was:
    // `isModalOverlayOpen()` looks for a Vuetify scrim, and `.lightbox`
    // renders its own. The lightbox is excluded by the explicit `.lightbox`
    // check at the top of this handler (listener ORDER used to do it, until the
    // Duplicates view's grid remount flipped it - see that comment). Undo works
    // in the lightbox through its own key handler plus `LightboxActionReceipt`,
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
        // A local receipt (the shelf's set edits, #1573) is only live while
        // its screen shows it (`setLocalReceiptHost`), and its pill shows the
        // chord, so the chord takes that pill's action.
        const local = operationStore.receipt?.local
          ? operationStore.receipt
          : null;
        if (local && wantsUndo === (local.mode !== "undone")) {
          operationStore.takeLocalReceiptAction();
        } else if (local) {
          // The pill offers the other direction; "not undoable" would be false.
          noticeStore.push({
            level: "info",
            key: SHELF_NO_UNDO_KEY,
            text: wantsUndo
              ? "Nothing more to undo here - Redo puts that change back."
              : "Nothing to redo - Undo takes that change back.",
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
      !isReadOnly.value &&
      !isModalOverlayOpen() &&
      // A list with its own rename (Workflows, Models) has taken this press.
      !e.defaultPrevented
    ) {
      // Whatever has the grid's cursor: the grid has no F2 of its own, and a
      // clicked picture used to swallow this one.
      e.preventDefault();
      sidebarRef.value?.openCurrentSelectionEditor?.();
    }
  }

  onMounted(() => window.addEventListener("keydown", handleGlobalKeydown));
  onUnmounted(() => window.removeEventListener("keydown", handleGlobalKeydown));
}
