// The Mixed stacks page's queue state: the focus, the selection, the member
// cursor and the marks.
//
// The page is the THIRD queue, not a list with buttons on it. A row is one live
// stack, its tiles are that stack's members, and the verdicts are split /
// unstack / keep. Everything that made the review queue workable at speed is
// therefore inherited rather than re-invented: the same focus model, the same
// multi-selection gestures, the same auto-advance, the same key handler
// (`createDedupKeyHandler`, parameterised) and the same action receipt.
//
// What lives HERE is only the per-page view state. It is deliberately not in
// the store: marks, the member cursor and the selection are not server state
// and they must not outlive the destination, while `useDedupStore` owns the
// rows, the reads and the writes.
//
// **Marks are the whole model.** The server pre-marks the members it believes
// are strangers and the row opens with those marks already applied, exactly as
// the review queue opens with the server's exclusions applied on an unstackable
// candidate. From there `X` (and a click on a tile) marks and unmarks, with no
// distinction between an engine mark and a user mark: they behave identically
// and compose into the one list the primary button acts on.

import { computed, ref, watch } from "vue";

import {
  isMixedStackStackable,
  mixedStackEngineMarks,
  mixedStackMembers,
} from "../utils/dedup";

/**
 * Build the Mixed stacks queue's view state over a dedup store.
 *
 * @param {Object} store - the dedup store (or anything with `mixedStacks` and
 *   `mixedBusyStackId`).
 * @returns {Object} the state and actions the page and its key handler use.
 */
export function useMixedStackQueue(store) {
  /** The keyboard cursor's row, as an index into the loaded rows. */
  const focusIndex = ref(0);
  /** Stack ids (as strings) in the multi-selection. */
  const selectedIds = ref(new Set());
  /** Where a Shift-range extends from. */
  const anchorIndex = ref(0);
  /**
   * `stackId -> { fingerprint, ids }`, the marks the user has adjusted.
   *
   * Absent means "the engine's marks, unedited", which is why this is lazy: a
   * row nobody has touched needs no entry, and a row that is re-read picks its
   * server marks back up for free. The fingerprint is what makes that safe: a
   * stack whose membership changed under a held edit is a stack whose marks
   * point at pictures that may no longer be in it, so the edit is dropped
   * rather than replayed against a different set.
   */
  const marks = ref({});
  /** `stackId -> member index`: which tile `X` acts on. */
  const cursors = ref({});

  const rows = computed(() => store.mixedStacks ?? []);
  const focusedRow = computed(() => rows.value[focusIndex.value] ?? null);
  const selectionCount = computed(() => selectedIds.value.size);

  /**
   * The rows the selection holds, in list order.
   *
   * Order matters: a bulk Keep reports what it did, and reporting it in
   * selection order rather than list order would describe a different list from
   * the one on screen.
   */
  const selectedRows = computed(() =>
    rows.value.filter((row) => selectedIds.value.has(keyOf(row))),
  );

  /**
   * A row's key.
   * @param {Object|number|string} row - a row, or a stack id.
   * @returns {string}
   */
  function keyOf(row) {
    const id = row?.stack_id ?? row;
    return id === null || id === undefined ? "" : String(id);
  }

  // The list shortens under the cursor on every resolve (that IS the
  // auto-advance: the row leaves and the next one takes its place), and it can
  // shorten by more than one when a bulk Keep lands. Clamping here rather than
  // in each action keeps every path honest, including a reload the page did not
  // ask for.
  watch(
    () => rows.value.length,
    (length) => {
      if (length === 0) {
        focusIndex.value = 0;
        return;
      }
      if (focusIndex.value > length - 1) focusIndex.value = length - 1;
      // A selected row that has left takes its selection with it, or the
      // selection bar counts rows nobody can see.
      const live = new Set(rows.value.map(keyOf));
      for (const id of [...selectedIds.value]) {
        if (!live.has(id)) selectedIds.value.delete(id);
      }
    },
  );

  /**
   * Move the cursor to a row, clamped. The queue never wraps and neither does
   * this: an End that fell off the bottom would lose the user's place.
   * @param {number} index
   */
  function setFocus(index) {
    const last = rows.value.length - 1;
    if (last < 0) {
      focusIndex.value = 0;
      return;
    }
    focusIndex.value = Math.max(0, Math.min(last, Math.round(index)));
  }

  function focusNext() {
    setFocus(focusIndex.value + 1);
  }

  function focusPrev() {
    setFocus(focusIndex.value - 1);
  }

  function focusStart() {
    setFocus(0);
  }

  /**
   * The last row the page HOLDS.
   *
   * Deliberately not the last row that exists: this list pages behind a
   * `Show more` the user presses, so there is no unloaded tail to chase and an
   * End that fetched one would contradict the control next to it.
   */
  function focusEnd() {
    setFocus(rows.value.length - 1);
  }

  /**
   * Whether a row is in the multi-selection.
   * @param {Object|number|string} row
   * @returns {boolean}
   */
  function isSelected(row) {
    return selectedIds.value.has(keyOf(row));
  }

  /**
   * Ctrl/Cmd-click: add or remove one row, and move the cursor to it.
   * @param {number} index
   */
  function toggleSelected(index) {
    const row = rows.value[index];
    if (!row) return;
    const key = keyOf(row);
    const next = new Set(selectedIds.value);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    selectedIds.value = next;
    anchorIndex.value = index;
    setFocus(index);
  }

  /**
   * Shift-click: take everything between the anchor and here.
   * @param {number} index
   */
  function selectRange(index) {
    const from = Math.min(anchorIndex.value, index);
    const to = Math.max(anchorIndex.value, index);
    const next = new Set(selectedIds.value);
    for (let i = from; i <= to; i += 1) {
      const row = rows.value[i];
      if (row) next.add(keyOf(row));
    }
    selectedIds.value = next;
    setFocus(index);
  }

  function clearSelection() {
    if (!selectedIds.value.size) return;
    selectedIds.value = new Set();
  }

  /**
   * Ctrl+A: every row the page holds.
   *
   * No truncation and no paging: unlike the review queue this list is tens of
   * rows behind an explicit `Show more`, so "all" means all of what is on
   * screen and the count says so.
   *
   * @returns {{selected: number, total: number}}
   */
  function selectAll() {
    selectedIds.value = new Set(rows.value.map(keyOf));
    anchorIndex.value = 0;
    return { selected: selectedIds.value.size, total: rows.value.length };
  }

  /**
   * One row's members, in canonical stack order.
   * @param {Object} row
   * @returns {Array<Object>}
   */
  function membersFor(row) {
    return mixedStackMembers(row);
  }

  /**
   * The marks in force on a row: the user's edit, or the engine's opening set.
   *
   * @param {Object} row
   * @returns {Array<number>} picture ids.
   */
  function marksFor(row) {
    const held = marks.value[keyOf(row)];
    if (
      held &&
      held.fingerprint === String(row?.membership_fingerprint ?? "")
    ) {
      return held.ids;
    }
    return mixedStackEngineMarks(row);
  }

  /**
   * Whether one member is marked as a stranger.
   * @param {Object} row
   * @param {number} pictureId
   * @returns {boolean}
   */
  function isMarked(row, pictureId) {
    return marksFor(row).some((id) => String(id) === String(pictureId));
  }

  /**
   * Mark or unmark one member.
   *
   * Symmetric, and symmetric over the ENGINE'S marks too: a member the server
   * pre-marked unmarks on the first press exactly as a member the user marked
   * does. The two are one list, because the button acts on one list.
   *
   * @param {Object} row
   * @param {number} pictureId
   * @returns {boolean|string} true when the mark moved, `"locked"` when a
   *   locked picture set freezes the whole stack, so nothing on the row is
   *   markable at all.
   */
  function toggleMark(row, pictureId) {
    if (pictureId === null || pictureId === undefined) return false;
    // A locked set refuses split and unstack alike, and refuses the WHOLE
    // stack. A mark that could never be acted on is a gesture the row must not
    // accept, for the same reason the queue refuses a cover on a frozen unit.
    if (!isMixedStackStackable(row)) return "locked";
    const current = marksFor(row);
    const wanted = String(pictureId);
    const ids = current.some((id) => String(id) === wanted)
      ? current.filter((id) => String(id) !== wanted)
      : [...current, pictureId];
    marks.value = {
      ...marks.value,
      [keyOf(row)]: {
        fingerprint: String(row?.membership_fingerprint ?? ""),
        ids,
      },
    };
    return true;
  }

  /**
   * Which member the cursor is on, as an index into {@link membersFor}.
   * @param {Object} row
   * @returns {number}
   */
  function cursorFor(row) {
    const held = Number(cursors.value[keyOf(row)]);
    const count = membersFor(row).length;
    if (!count) return -1;
    if (!Number.isFinite(held)) return 0;
    return Math.max(0, Math.min(count - 1, held));
  }

  /**
   * Move the member cursor.
   * @param {Object|number|string} row
   * @param {number} index
   */
  function setCursor(row, index) {
    cursors.value = { ...cursors.value, [keyOf(row)]: index };
  }

  /**
   * Move the member cursor onto one picture.
   *
   * This is what `1`-`9` does, through the shared key handler's `setCover`
   * hook: the digits address the same tiles on both queues and mean the same
   * thing, "point at this one". On this page pointing is all they do; the mark
   * is `X`, a second and deliberate press.
   *
   * @param {number|string} stackId
   * @param {number} pictureId
   */
  function setCursorToPicture(stackId, pictureId) {
    const key = String(stackId);
    const row = rows.value.find((entry) => keyOf(entry) === key);
    if (!row) return;
    const index = membersFor(row).findIndex(
      (member) => String(member.pictureId) === String(pictureId),
    );
    if (index >= 0) setCursor(row, index);
  }

  /**
   * The picture under the member cursor, which is what `X` acts on.
   * @param {Object} row
   * @returns {number|null}
   */
  function memberIdAtCursor(row) {
    const members = membersFor(row);
    const index = cursorFor(row);
    return index < 0 ? null : (members[index]?.pictureId ?? null);
  }

  /**
   * The row's members in the shape the shared key handler's digit branch reads.
   *
   * It wants `{coverPictureId, stackable}` per addressable thing, which on the
   * review queue is a unit and here is a member. `stackable` is the row's, not
   * the member's: a locked set freezes the whole stack, so either every tile is
   * addressable or none is.
   *
   * @param {Object} row
   * @returns {Array<Object>}
   */
  function unitsFor(row) {
    const stackable = isMixedStackStackable(row);
    return membersFor(row).map((member) => ({
      coverPictureId: member.pictureId,
      stackable,
    }));
  }

  /**
   * Drop the view state a row leaves behind.
   *
   * Not merely tidy: stack ids are reused by nothing, but a `Keep` that is
   * undone brings the same row back and it must come back with the server's
   * marks rather than with the edit that preceded a decision the user reversed.
   *
   * @param {number|string} stackId
   */
  function forgetRow(stackId) {
    const key = String(stackId);
    if (key in marks.value) {
      const next = { ...marks.value };
      delete next[key];
      marks.value = next;
    }
    if (key in cursors.value) {
      const next = { ...cursors.value };
      delete next[key];
      cursors.value = next;
    }
  }

  /** Forget every edit: the page was left, or the list was replaced wholesale. */
  function reset() {
    focusIndex.value = 0;
    anchorIndex.value = 0;
    selectedIds.value = new Set();
    marks.value = {};
    cursors.value = {};
  }

  return {
    rows,
    focusIndex,
    focusedRow,
    setFocus,
    focusNext,
    focusPrev,
    focusStart,
    focusEnd,
    selectionCount,
    selectedRows,
    isSelected,
    toggleSelected,
    selectRange,
    clearSelection,
    selectAll,
    membersFor,
    marksFor,
    isMarked,
    toggleMark,
    cursorFor,
    setCursor,
    setCursorToPicture,
    memberIdAtCursor,
    unitsFor,
    forgetRow,
    reset,
  };
}
