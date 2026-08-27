import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useNoticeStore } from "./useNoticeStore";

// ─────────────────────────────────────────────────────────────────────────────
// REGRESSION TESTS for three defects QA found in the notice store, against
// docs/design/notice-surface.md §5 / §9.2 / §9.3.
//
// Each was written to the SPEC and shipped as `it.fails` while the bug was live.
// All three are fixed, so the markers are gone and these are ordinary
// assertions - the reproductions are kept verbatim because they are the precise
// conditions the bugs needed, and none of them is obvious from the code.
//
//   D1  §5 "errors outrank"   - an error was queued behind non-errors whenever
//                               the pending queue was non-empty, and a bystander
//                               notice was destroyed for nothing on the way.
//                               FIXED: the error is inserted into the visible
//                               window and the bystander is demoted, not killed.
//   D2  §9.3 pause/resume     - a coalesced repeat cancelled a hover / focus
//                               pause, so the card auto-dismissed out from under
//                               the cursor (WCAG 2.2.1).
//                               FIXED: the per-notice `paused` flag survives a
//                               coalesce.
//   D3  §9.2 timers off-screen - a notice DEMOTED out of the visible window by a
//                               cap drop kept its running timer and expired
//                               unseen - §9.2's bug in the demotion direction.
//                               FIXED: timers are reconciled in both
//                               directions, and a demoted notice's window is
//                               restored in full.
// ─────────────────────────────────────────────────────────────────────────────

beforeEach(() => {
  setActivePinia(createPinia());
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("D1 (fixed) - an error is never queued behind a full pending queue (§5)", () => {
  // The bug: push() freed a slot when notices.length >= maxVisible, but
  // `notices` counts PENDING notices too, and the freed slot went to the next
  // notice in push order - not to the error, which is appended last.
  it("promotes an error into the visible window immediately", () => {
    const store = useNoticeStore();
    for (let i = 1; i <= 5; i++) {
      store.push({ level: "success", text: `s${i}`, timeout: 0 });
    }
    expect(store.visible.map((n) => n.text)).toEqual(["s1", "s2", "s3"]);

    store.error("BOOM");

    // Spec §5: "An error is never queued behind a success."
    expect(store.visible.map((n) => n.text)).toContain("BOOM");
  });

  it("does not destroy a bystander notice when it cannot show the error", () => {
    const store = useNoticeStore();
    for (let i = 1; i <= 5; i++) {
      store.push({ level: "success", text: `s${i}`, timeout: 0 });
    }
    store.error("BOOM");
    // The bystander that yields the visible slot is DEMOTED to the front of the
    // pending queue, not destroyed: losing a message is never the thing that
    // buys the room, the insert is.
    expect(store.notices.map((n) => n.text)).toContain("s1");
    // ...and it is first in line for the next free slot.
    expect(store.pending[0].text).toBe("s1");
  });
});

describe("D2 (fixed) - coalescing preserves a hover/focus pause (§9.3)", () => {
  // The bug: push()'s coalescing branch called clearTimer(existing.id), which
  // deleted the whole timer entry INCLUDING its `paused: true` flag, and the
  // timer reconcile then started a fresh countdown. The card expired while the
  // cursor was still on it - exactly what WCAG 2.2.1 forbids.
  it("keeps a hover-paused notice on screen when a repeat coalesces", () => {
    const store = useNoticeStore();
    const id = store.push({ level: "success", text: "Saved.", key: "save" });
    store.pause(id); // cursor enters the card

    store.push({ level: "success", text: "Saved.", key: "save" }); // repeat arrives

    vi.advanceTimersByTime(120000); // the cursor never left
    expect(store.notices).toHaveLength(1);
  });

  // The global (document.hidden) pause survives, because startTimer() re-checks
  // `globallyPaused`. Only the per-notice flag is lost - recorded here so a fix
  // is not mistaken for a regression in the global path.
  it("global pause survives a coalesced repeat (this half is correct)", () => {
    const store = useNoticeStore();
    store.push({ level: "info", text: "x", key: "k" });
    store.pauseAll();
    store.push({ level: "info", text: "x", key: "k" });
    vi.advanceTimersByTime(120000);
    expect(store.notices).toHaveLength(1);
  });
});

describe("D3 (fixed) - a demoted notice's timer is stopped (§9.2)", () => {
  // The bug: the timer reconcile only ever STARTED timers for the currently
  // visible window; it never stopped the timer of a notice just pushed out of
  // it. Reachable in the shipped UI: NoticeHost's
  // matchMedia listener drops the cap 3 → 2 when the viewport crosses 600px
  // (a window resize, a tablet rotation, or the desktop shell being narrowed).
  it("does not expire a notice that was demoted off-screen", () => {
    const store = useNoticeStore();
    store.success("a");
    store.success("b");
    store.success("c");
    const T = store.notices[0].timeout;

    vi.advanceTimersByTime(T - 100);
    store.setMaxVisible(2); // viewport narrowed; "c" is demoted, unseen
    expect(store.visible.map((n) => n.text)).toEqual(["a", "b"]);

    vi.advanceTimersByTime(200); // "a" and "b" expire on schedule
    // "c" was never displayed for its full window, so it must survive to be
    // promoted - not expire behind the other two.
    expect(store.notices.map((n) => n.text)).toEqual(["c"]);
  });
});
