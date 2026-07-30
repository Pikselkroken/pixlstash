// The triage queue's keyboard model.
//
// The queue is designed to be worked without a mouse: the whole point of the
// focused-row treatment is that `Enter` and `S` can never be ambiguous about
// which group they hit. That makes the key handler load-bearing rather than a
// convenience, so it lives here as a pure factory that takes its dependencies
// by parameter and can be exercised without mounting the view.
//
// The model, in one table:
//
//   ArrowUp / ArrowDown   move the focus (k / j alias the same pair)
//   PageUp / PageDown     move the focus a screenful of rows at a time
//   Home / End            jump to the first / TRUE last open group. End over a
//                         large unloaded gap fetches the tail page directly
//                         (random access, one request); Home undoes the jump
//                         by resetting to the first page.
//   Enter                 stack the focused group
//   S                     keep the focused group separate
//   C                     open Compare on the focused group
//   1 - 9                 point at that candidate and make it the cover
//   X                     leave the candidate under the cursor out of the stack
//                         (refused, and said out loud, once only two are left:
//                         a stack needs two members and the server refuses one)
//   Ctrl+Z / Cmd+Z        undo, through the shared operation store
//   Escape                close Compare, otherwise hand control back to the
//                         queue (`onEscape`)
//
// While Compare is open the per-group keys stay live (`Enter`, `S`, `1`-`9`,
// `X`, `Escape`), because Compare exists so the decision is made without a
// second trip — and `Up`/`Down` (or `j`/`k`) switch the COMPARED GROUP in
// place, since the dialog renders the focused group and shows exactly where
// you went. A verdict there keeps Compare open and the auto-advance flips it
// to the next group; the view closes it only when the queue runs out. Only
// the jump keys (`Home`, `End`, `PageUp`/`PageDown`) go quiet, since a
// multi-row leap behind a dialog reads as the queue teleporting.
//
// Five guards decline the whole handler, each for its own reason:
//   * a text field has focus (a control keeps its own editing keys),
//   * `Enter` while a button or link has focus (that key belongs to the
//     control the user tabbed to, not to the queue underneath it),
//   * the session is read-only (there is no verdict to give),
//   * the key is an auto-repeat (a held `Enter` must not empty the queue),
//   * a modifier other than the undo pair is down (that is a browser shortcut).

import { candidateId } from "../utils/dedup";

/** Keys that move the focus down one group. */
const NEXT_KEYS = new Set(["arrowdown", "j"]);

/** Keys that move the focus up one group. */
const PREV_KEYS = new Set(["arrowup", "k"]);

/**
 * Take ownership of a key: stop the default action AND stop the event reaching
 * the app shell.
 *
 * The shell owns `Ctrl+Z` globally, so a queue that only called
 * `preventDefault` would undo twice on one press. Claiming the key here keeps
 * the queue's model in one place instead of teaching the shell about it.
 *
 * @param {KeyboardEvent} event
 */
function claim(event) {
  event.preventDefault();
  if (typeof event.stopPropagation === "function") event.stopPropagation();
}

/**
 * Whether the event originated inside something that owns its own keys.
 * @param {KeyboardEvent} event
 * @returns {boolean}
 */
function isTypingTarget(event) {
  const target = event?.target;
  if (!target) return false;
  if (target.isContentEditable) return true;
  const tag = String(target.tagName || "").toLowerCase();
  if (tag === "input" || tag === "textarea" || tag === "select") return true;
  // A slider or spinner thumb (Vuetify renders a focusable div, not an
  // input) owns its arrow keys exactly as a native range input does. Acting
  // on the queue as well would double every press: one size step AND one row
  // moved.
  const role = target.getAttribute?.("role");
  return role === "slider" || role === "spinbutton";
}

/**
 * Whether the event originated on a control that `Enter` already activates.
 *
 * A row carries a Stack, a Keep separate and a Compare button, and Compare's
 * footer carries three more. Once a user has tabbed onto one of them, `Enter`
 * belongs to that button: claiming it here would stack the group while the
 * focus ring sits on Close, which is the worst kind of surprise the queue can
 * produce. Every one of those buttons does the same job as the shortcut it
 * displaces, so declining costs nothing.
 *
 * @param {KeyboardEvent} event
 * @returns {boolean}
 */
function isActivatableTarget(event) {
  const target = event?.target;
  if (!target) return false;
  const tag = String(target.tagName || "").toLowerCase();
  if (tag === "button" || tag === "summary") return true;
  if (tag === "a") return Boolean(target.getAttribute?.("href"));
  return target.getAttribute?.("role") === "button";
}

/**
 * Build the queue's keydown handler.
 *
 * @param {Object} deps
 * @param {Object} deps.store - the dedup store (or any object with the same
 *   surface: `groups`, `focusIndex`, `focusedGroup`, `busy`, `focusNext`,
 *   `focusPrev`, `setFocus`, `focusStart`, `focusEnd`, `setCover`,
 *   `toggleExcluded`, `coverIdFor`, `stack`, `keepSeparate`).
 * @param {function(): boolean} deps.isCompareOpen
 * @param {function(): void} deps.openCompare
 * @param {function(): void} deps.closeCompare
 * @param {function(): void} deps.undo - the shared operation-store undo.
 * @param {function(): boolean} [deps.isReadOnly] - defaults to never read-only.
 * @param {function(): void} [deps.selectAll] - what `Ctrl+A` runs. Defaults to
 *   the store's action; the view overrides it to narrate the result, which it
 *   has to await.
 * @param {function(KeyboardEvent=): boolean} [deps.isBlocked] - an extra
 *   decline hook for a surface that is not Compare (the auto-stack dialog
 *   blocks everything; the tier popover blocks only events from inside
 *   itself, which is why the event is passed).
 * @param {function(): void} [deps.onEscape] - what `Escape` does when Compare
 *   is closed. The queue uses it to dismiss its popover and to hand control
 *   back to the list, so `Escape` is never a key that does nothing.
 * @param {function(Object): void} [deps.onExclusionRefused] - called with the
 *   group when `X` was refused because the stack floor was reached. The view
 *   narrates it; this handler has no opinion on how.
 * @param {function(): number} [deps.pageRows] - how many rows `PageUp` and
 *   `PageDown` move. The viewport's row capacity, which only the view can
 *   measure; the fallback is a conservative screenful.
 * @param {Object} [deps.zoom] - the Compare dialog's blink-compare surface:
 *   `{ isOpen, open, close, flip, to, togglePixels }`. The zoom's state lives
 *   in the dialog; the KEYS live here, so the queue keeps exactly one
 *   keyboard owner. Omitted (the default no-op) in tests that predate it.
 * @returns {function(KeyboardEvent): void}
 */
/**
 * The page step used when the view cannot measure its viewport (a test, or a
 * list that has not laid out yet). Deliberately smaller than any real screenful
 * so a blind guess never overshoots the queue.
 */
const DEFAULT_PAGE_ROWS = 5;

const NO_ZOOM = {
  isOpen: () => false,
  open: () => {},
  close: () => {},
  flip: () => {},
  to: () => {},
  togglePixels: () => {},
};

export function createDedupKeyHandler({
  store,
  isCompareOpen,
  openCompare,
  closeCompare,
  undo,
  isReadOnly = () => false,
  isBlocked = () => false,
  onEscape = () => {},
  onExclusionRefused = () => {},
  selectAll = null,
  pageRows = () => DEFAULT_PAGE_ROWS,
  zoom = NO_ZOOM,
}) {
  /**
   * A page move in rows, never zero: a viewport too short to hold one row must
   * still advance, or the key reads as dead.
   * @returns {number}
   */
  function rowsPerPage() {
    const rows = Math.floor(Number(pageRows()));
    return Number.isFinite(rows) && rows >= 1 ? rows : DEFAULT_PAGE_ROWS;
  }

  /**
   * Point `1`-`9` and `X` at the focused group, from the list or from Compare.
   * @param {KeyboardEvent} event
   * @param {string} key
   * @param {Object} group
   * @returns {boolean} whether the key was one of these.
   */
  function handleCoverKeys(event, key, group) {
    if (key === "x") {
      claim(event);
      const coverId = store.coverIdFor(group);
      if (coverId !== null && coverId !== undefined) {
        const applied = store.toggleExcluded(group, coverId);
        // The store refuses an exclusion that would drop the group below the
        // two members a stack needs. Refusing silently is how a one-key action
        // reads as a dead key, so the refusal is handed to the view to say out
        // loud rather than swallowed here.
        if (applied === false) onExclusionRefused(group);
      }
      return true;
    }
    if (/^[1-9]$/.test(key)) {
      // Claimed before the candidate is looked up, so `5` on a group of two is
      // a no-op for the queue rather than an unclaimed key that falls through
      // to the app shell. Every other key this handler recognises behaves that
      // way; a digit that only sometimes did was the odd one out.
      claim(event);
      const candidate = group.candidates?.[Number(key) - 1];
      if (!candidate) return true;
      store.setCover(group.signature, candidateId(candidate));
      return true;
    }
    return false;
  }

  return function handleKeydown(event) {
    if (!event) return;
    if (event.repeat) return;
    if (isTypingTarget(event)) return;

    const key = String(event.key || "").toLowerCase();
    const undoChord = (event.ctrlKey || event.metaKey) && !event.altKey;

    // Undo is the one chord that survives every other guard except read-only:
    // it is the escape hatch a user reaches for precisely when something went
    // wrong, including while a dialog is up.
    if (undoChord && key === "z" && !event.shiftKey) {
      if (isReadOnly()) return;
      claim(event);
      undo();
      return;
    }
    // Select-all is the second chord the queue claims: every group in the
    // queue, on the open queue and the Decided page alike. Claimed so the
    // browser does not also select the page's text.
    if (
      (event.ctrlKey || event.metaKey) &&
      !event.altKey &&
      !event.shiftKey &&
      key === "a" &&
      !isCompareOpen() &&
      !isBlocked()
    ) {
      claim(event);
      if (selectAll) selectAll();
      else store.selectAll?.();
      return;
    }
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (key === "enter" && isActivatableTarget(event)) return;

    if (isCompareOpen()) {
      // ── The blink compare, when it is up, owns the flip keys ────────────
      // Escape peels one layer (zoom → compare → queue), arrows and digits
      // flip the candidate IN PLACE so differences read as motion, P toggles
      // actual pixels. Enter/S fall through to the verdict keys below —
      // deciding from inside the zoom is the point of having it.
      if (zoom.isOpen()) {
        if (key === "escape") {
          claim(event);
          zoom.close();
          return;
        }
        if (key === "arrowright" || key === "arrowdown") {
          claim(event);
          zoom.flip(1);
          return;
        }
        if (key === "arrowleft" || key === "arrowup") {
          claim(event);
          zoom.flip(-1);
          return;
        }
        if (/^[1-9]$/.test(key)) {
          claim(event);
          zoom.to(Number(key) - 1);
          return;
        }
        if (key === "p") {
          claim(event);
          zoom.togglePixels();
          return;
        }
      } else if (key === "z") {
        claim(event);
        zoom.open();
        return;
      }
      if (key === "escape") {
        claim(event);
        closeCompare();
        return;
      }
      // ── Up/Down switch GROUPS from inside Compare ─────────────────────
      // The dialog renders the focused group, so a focus move flips it in
      // place (zoom and fit reset per group, exactly as on a verdict's
      // advance) — no place is lost, the dialog shows where you went. The
      // ZOOM layer above keeps ALL its arrows for candidate flipping: two
      // meanings for one axis in one layer is how a key stops being
      // trusted. Clamped at the queue's ends like every focus move (the
      // queue never wraps), live in read-only (reading is not a verdict),
      // and a move mid end-chase cancels the chase like any other.
      if (NEXT_KEYS.has(key)) {
        claim(event);
        store.focusNext();
        return;
      }
      if (PREV_KEYS.has(key)) {
        claim(event);
        store.focusPrev();
        return;
      }
      if (isReadOnly() || store.busy) return;
      const group = store.focusedGroup;
      if (!group) return;
      // A verdict from inside Compare does NOT close Compare: the store's
      // auto-advance moves the focus to the next open group and the dialog,
      // which renders the focused group, flips to it in place — a run of
      // decisions is made without reopening anything. The zoom layer closes
      // (the next group starts un-zoomed by contract), and the view closes
      // the dialog itself once the queue has nothing left to show.
      if (key === "enter") {
        claim(event);
        zoom.close();
        store.stack(group);
        return;
      }
      if (key === "s") {
        claim(event);
        zoom.close();
        store.keepSeparate(group);
        return;
      }
      // The cover and exclusion keys stay live here: Compare is the view that
      // shows the fields the cover is chosen on, so making the user close it to
      // press a number would be the second trip Compare exists to remove.
      handleCoverKeys(event, key, group);
      return;
    }

    // Escape resolves before the block guard, so the popover that raised the
    // guard is exactly the thing Escape can dismiss.
    if (key === "escape") {
      claim(event);
      onEscape();
      return;
    }

    // The event is handed over so the view can scope a popover's block by
    // TARGET: the tier menu owns only the keys pressed inside itself, so the
    // queue keeps working underneath it once a commit handed focus back.
    if (isBlocked(event)) return;

    // Navigation stays live in a read-only session: reading the queue is not a
    // verdict, and freezing the arrow keys there would make it unreadable.
    if (NEXT_KEYS.has(key)) {
      claim(event);
      store.focusNext();
      return;
    }
    if (PREV_KEYS.has(key)) {
      claim(event);
      store.focusPrev();
      return;
    }
    // A page move is a focus move like any other, so it auto-loads at the tail
    // and drags the scroll with it. `setFocus` clamps, which is what makes
    // PageDown on the last screenful land on the last row rather than nowhere.
    if (key === "pagedown") {
      claim(event);
      store.setFocus(store.focusIndex + rowsPerPage());
      return;
    }
    if (key === "pageup") {
      claim(event);
      store.setFocus(store.focusIndex - rowsPerPage());
      return;
    }
    if (key === "home") {
      // The queue's first group. After an End jump the window no longer
      // contains the top, so the store resets to the first page; on a
      // top-anchored window it is a plain focus move.
      claim(event);
      store.focusStart();
      return;
    }
    if (key === "end") {
      // The queue's TRUE end, not the last row the client happens to hold:
      // the total is known a priori, so one press must reach the real last
      // group. Over a large gap the store fetches the tail page directly and
      // rebases its window onto it; either way the focus lands on the last
      // row that actually exists.
      claim(event);
      store.focusEnd();
      return;
    }

    const group = store.focusedGroup;
    if (!group) return;

    if (key === "c") {
      claim(event);
      openCompare();
      return;
    }

    if (isReadOnly() || store.busy) return;

    if (key === "enter") {
      claim(event);
      store.stack(group);
      return;
    }
    if (key === "s") {
      claim(event);
      store.keepSeparate(group);
      return;
    }
    handleCoverKeys(event, key, group);
  };
}
