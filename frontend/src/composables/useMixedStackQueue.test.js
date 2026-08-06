// The Mixed stacks queue's view state.
//
// The three things pinned here are the three that are easy to get subtly wrong
// and impossible to see in a screenshot: a mark toggles symmetrically over the
// ENGINE'S marks as well as the user's, an edit does not survive a change to
// the stack it was made about, and the cursor never points off the end of a
// list that shortened under it.

import { describe, it, expect } from "vitest";
import { nextTick, ref } from "vue";

import { useMixedStackQueue } from "./useMixedStackQueue";

/** One `MixedStackModel` row, in the backend's shape. */
function row(over = {}) {
  return {
    stack_id: 42,
    member_count: 5,
    member_ids: [7, 8, 9, 10, 11],
    membership_fingerprint: "fp-1",
    component_count: 2,
    components: [[7, 8, 9, 10], [11]],
    largest_component_size: 4,
    stranded_picture_ids: [11],
    unhashed_picture_ids: [],
    suggested_action: "split",
    leader_picture_id: 7,
    ...over,
  };
}

/** A stand-in for the dedup store's mixed half. */
function fakeStore(rows = [row()]) {
  const mixedStacks = ref(rows);
  return {
    get mixedStacks() {
      return mixedStacks.value;
    },
    set mixedStacks(next) {
      mixedStacks.value = next;
    },
    mixedBusyStackId: null,
  };
}

describe("useMixedStackQueue: the marks", () => {
  // The server pre-marks the members it believes are strangers, exactly as the
  // review queue opens with the server's exclusions already applied.
  it("opens with the engine's marks, with no edit recorded", () => {
    const store = fakeStore();
    const q = useMixedStackQueue(store);
    expect(q.marksFor(store.mixedStacks[0])).toEqual([11]);
    expect(q.isMarked(store.mixedStacks[0], 11)).toBe(true);
    expect(q.isMarked(store.mixedStacks[0], 7)).toBe(false);
  });

  // ONE stranger treatment: an engine mark unmarks on the first press exactly
  // as a user mark does, because the button acts on one list and a user cannot
  // act on a distinction the button does not make.
  it("unmarks an engine mark on the first press, and marks it back", () => {
    const store = fakeStore();
    const q = useMixedStackQueue(store);
    const stack = store.mixedStacks[0];

    expect(q.toggleMark(stack, 11)).toBe(true);
    expect(q.marksFor(stack)).toEqual([]);

    expect(q.toggleMark(stack, 11)).toBe(true);
    expect(q.marksFor(stack)).toEqual([11]);
  });

  it("marks and unmarks a member the engine left alone", () => {
    const store = fakeStore();
    const q = useMixedStackQueue(store);
    const stack = store.mixedStacks[0];

    q.toggleMark(stack, 8);
    expect(q.marksFor(stack)).toEqual([11, 8]);
    q.toggleMark(stack, 8);
    expect(q.marksFor(stack)).toEqual([11]);
  });

  // A locked picture set refuses split and unstack alike, and refuses the WHOLE
  // stack, so nothing on the row is markable at all. A mark that could never be
  // acted on is a gesture the row must not accept.
  it("refuses every mark on a frozen row", () => {
    const store = fakeStore([row({ stackable: false })]);
    const q = useMixedStackQueue(store);
    const stack = store.mixedStacks[0];
    for (const id of stack.member_ids) {
      expect(q.toggleMark(stack, id)).toBe("locked");
    }
    expect(q.marksFor(stack)).toEqual([11]);
  });

  // A stack whose membership changed under a held edit is a stack whose marks
  // may point at pictures that are no longer in it, so the edit is dropped
  // rather than replayed against a different set.
  it("drops an edit when the stack's membership changes underneath it", () => {
    const store = fakeStore();
    const q = useMixedStackQueue(store);
    q.toggleMark(store.mixedStacks[0], 8);
    expect(q.marksFor(store.mixedStacks[0])).toEqual([11, 8]);

    store.mixedStacks = [
      row({ membership_fingerprint: "fp-2", stranded_picture_ids: [9] }),
    ];
    expect(q.marksFor(store.mixedStacks[0])).toEqual([9]);
  });

  it("forgets a row's edit on request", () => {
    const store = fakeStore();
    const q = useMixedStackQueue(store);
    q.toggleMark(store.mixedStacks[0], 11);
    expect(q.marksFor(store.mixedStacks[0])).toEqual([]);
    q.forgetRow(42);
    expect(q.marksFor(store.mixedStacks[0])).toEqual([11]);
  });
});

describe("useMixedStackQueue: the member cursor", () => {
  it("starts on the first member and moves onto a named picture", () => {
    const store = fakeStore();
    const q = useMixedStackQueue(store);
    const stack = store.mixedStacks[0];
    expect(q.cursorFor(stack)).toBe(0);
    expect(q.memberIdAtCursor(stack)).toBe(7);

    q.setCursorToPicture(42, 10);
    expect(q.cursorFor(stack)).toBe(3);
    expect(q.memberIdAtCursor(stack)).toBe(10);
  });

  // The digits address the same tiles on both queues and mean the same thing,
  // "point at this one", which is what lets one key handler serve both.
  it("offers the members in the shape the shared key handler reads", () => {
    const store = fakeStore();
    const q = useMixedStackQueue(store);
    expect(q.unitsFor(store.mixedStacks[0])).toEqual(
      [7, 8, 9, 10, 11].map((id) => ({ coverPictureId: id, stackable: true })),
    );
  });

  it("reports a frozen row's members as unaddressable", () => {
    const store = fakeStore([row({ stackable: false })]);
    const q = useMixedStackQueue(store);
    expect(
      q.unitsFor(store.mixedStacks[0]).every((u) => u.stackable === false),
    ).toBe(true);
  });

  it("clamps a cursor that points past a shorter member list", () => {
    const store = fakeStore();
    const q = useMixedStackQueue(store);
    q.setCursor(store.mixedStacks[0], 4);
    store.mixedStacks = [
      row({ member_ids: [7, 8], member_count: 2, membership_fingerprint: "z" }),
    ];
    expect(q.cursorFor(store.mixedStacks[0])).toBe(1);
  });
});

describe("useMixedStackQueue: the focus and the selection", () => {
  it("clamps the focus when the list shortens under it", async () => {
    const store = fakeStore([row({ stack_id: 1 }), row({ stack_id: 2 })]);
    const q = useMixedStackQueue(store);
    q.setFocus(1);
    expect(q.focusedRow.value.stack_id).toBe(2);

    store.mixedStacks = [row({ stack_id: 1 })];
    await nextTick();
    expect(q.focusIndex.value).toBe(0);
    expect(q.focusedRow.value.stack_id).toBe(1);
  });

  it("never wraps at either end", () => {
    const store = fakeStore([row({ stack_id: 1 }), row({ stack_id: 2 })]);
    const q = useMixedStackQueue(store);
    q.focusPrev();
    expect(q.focusIndex.value).toBe(0);
    q.focusEnd();
    q.focusNext();
    expect(q.focusIndex.value).toBe(1);
    q.focusStart();
    expect(q.focusIndex.value).toBe(0);
  });

  it("extends a range from the anchor and clears on request", () => {
    const store = fakeStore([1, 2, 3, 4].map((id) => row({ stack_id: id })));
    const q = useMixedStackQueue(store);
    q.toggleSelected(1);
    q.selectRange(3);
    expect(q.selectionCount.value).toBe(3);
    expect(q.selectedRows.value.map((r) => r.stack_id)).toEqual([2, 3, 4]);
    q.clearSelection();
    expect(q.selectionCount.value).toBe(0);
  });

  // A selection that counted rows nobody can see would put a bulk Keep on a
  // list the user never saw the size of.
  it("drops a selected row that has left the list", async () => {
    const store = fakeStore([row({ stack_id: 1 }), row({ stack_id: 2 })]);
    const q = useMixedStackQueue(store);
    q.selectAll();
    expect(q.selectionCount.value).toBe(2);

    store.mixedStacks = [row({ stack_id: 1 })];
    await nextTick();
    expect(q.selectionCount.value).toBe(1);
  });

  it("forgets everything on reset", () => {
    const store = fakeStore([row({ stack_id: 1 }), row({ stack_id: 2 })]);
    const q = useMixedStackQueue(store);
    q.setFocus(1);
    q.selectAll();
    q.toggleMark(store.mixedStacks[0], 7);
    q.reset();
    expect(q.focusIndex.value).toBe(0);
    expect(q.selectionCount.value).toBe(0);
    expect(q.marksFor(store.mixedStacks[0])).toEqual([11]);
  });
});
