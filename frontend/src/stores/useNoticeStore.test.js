import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useNoticeStore, resolveTimeout } from "./useNoticeStore";

beforeEach(() => {
  setActivePinia(createPinia());
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useNoticeStore - basics", () => {
  it("pushes a notice and returns its id", () => {
    const store = useNoticeStore();
    const id = store.push({ level: "info", text: "hello", timeout: 0 });
    expect(store.notices).toHaveLength(1);
    expect(store.notices[0]).toMatchObject({
      id,
      level: "info",
      text: "hello",
    });
  });

  it("dismiss removes a single notice by id", () => {
    const store = useNoticeStore();
    const a = store.push({ text: "a", timeout: 0 });
    store.push({ text: "b", timeout: 0 });
    store.dismiss(a);
    expect(store.notices.map((n) => n.text)).toEqual(["b"]);
  });

  it("clear empties the queue", () => {
    const store = useNoticeStore();
    store.push({ text: "a", timeout: 0 });
    store.push({ text: "b", timeout: 0 });
    store.clear();
    expect(store.notices).toHaveLength(0);
  });

  it("falls back to info for an unknown level", () => {
    const store = useNoticeStore();
    store.push({ level: "banana", text: "x", timeout: 0 });
    expect(store.notices[0].level).toBe("info");
  });

  // §9.5 - a blank card tells the user nothing and hides the call-site bug.
  it("refuses an empty-text notice and logs it", () => {
    const store = useNoticeStore();
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(store.push({ text: "" })).toBeNull();
    expect(store.push({ text: "   " })).toBeNull();
    expect(store.push({})).toBeNull();
    expect(store.notices).toHaveLength(0);
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });
});

describe("resolveTimeout - reading-time floor (§6 rule 2)", () => {
  it("keeps a sticky timeout sticky", () => {
    expect(resolveTimeout({ baseTimeout: 0, text: "boom" })).toBe(0);
  });

  it("raises a short default to the reading time of a long message", () => {
    const text = "x".repeat(100); // 2000 + 60*100 = 8000
    expect(resolveTimeout({ baseTimeout: 3000, text })).toBe(8000);
  });

  it("never exceeds the 12s ceiling", () => {
    const text = "x".repeat(1000);
    expect(resolveTimeout({ baseTimeout: 3000, text })).toBe(12000);
  });

  it("never drops below the level default", () => {
    expect(resolveTimeout({ baseTimeout: 6000, text: "hi" })).toBe(6000);
  });

  // §6 rule 1 - a 3s window to hit "Undo" fails WCAG 2.2.1.
  it("forces any notice with an action to be sticky", () => {
    expect(
      resolveTimeout({ baseTimeout: 3000, text: "done", hasAction: true }),
    ).toBe(0);
  });
});

describe("useNoticeStore - sticky errors (§6)", () => {
  it("never auto-dismisses an error", () => {
    const store = useNoticeStore();
    store.error("Couldn't delete the picture.");
    expect(store.notices[0].timeout).toBe(0);
    vi.advanceTimersByTime(120000);
    expect(store.notices).toHaveLength(1);
  });

  it("auto-dismisses a success once its reading time elapses", () => {
    const store = useNoticeStore();
    store.success("Saved.");
    const effective = store.notices[0].timeout;
    expect(effective).toBeGreaterThan(0);
    vi.advanceTimersByTime(effective - 1);
    expect(store.notices).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(store.notices).toHaveLength(0);
  });

  it("makes a notice with an action sticky whatever its level", () => {
    const store = useNoticeStore();
    store.success("Undone.", { action: { label: "Undo", handler: () => {} } });
    expect(store.notices[0].timeout).toBe(0);
    vi.advanceTimersByTime(60000);
    expect(store.notices).toHaveLength(1);
  });
});

describe("useNoticeStore - coalescing (§9.1)", () => {
  it("collapses repeats with the same key into one card with a count", () => {
    const store = useNoticeStore();
    for (let i = 0; i < 50; i++) {
      store.push({ level: "error", text: "Couldn't delete.", key: "del-fail" });
    }
    expect(store.notices).toHaveLength(1);
    expect(store.notices[0].count).toBe(50);
  });

  it("updates the coalesced message so a running total can be shown", () => {
    const store = useNoticeStore();
    store.push({ level: "error", text: "1 failed", key: "k" });
    store.push({ level: "error", text: "2 failed", key: "k" });
    expect(store.notices).toHaveLength(1);
    expect(store.notices[0].text).toBe("2 failed");
    expect(store.notices[0].count).toBe(2);
  });

  it("keeps different keys apart", () => {
    const store = useNoticeStore();
    store.push({ level: "error", text: "a", key: "a" });
    store.push({ level: "error", text: "b", key: "b" });
    expect(store.notices).toHaveLength(2);
  });

  it("does not coalesce keyless notices", () => {
    const store = useNoticeStore();
    store.push({ level: "error", text: "same" });
    store.push({ level: "error", text: "same" });
    expect(store.notices).toHaveLength(2);
  });

  it("restarts the countdown when a coalesced notice recurs", () => {
    const store = useNoticeStore();
    store.push({ level: "success", text: "ok", key: "k" });
    const effective = store.notices[0].timeout;
    vi.advanceTimersByTime(effective - 10);
    store.push({ level: "success", text: "ok", key: "k" });
    // Would have expired here without the restart.
    vi.advanceTimersByTime(20);
    expect(store.notices).toHaveLength(1);
  });
});

describe("useNoticeStore - cap and pending queue (§9.2)", () => {
  it("renders at most maxVisible notices but keeps the rest queued", () => {
    const store = useNoticeStore();
    for (let i = 0; i < 6; i++) store.push({ text: `n${i}`, timeout: 0 });
    expect(store.notices).toHaveLength(6);
    expect(store.visible).toHaveLength(3);
    expect(store.pending).toHaveLength(3);
  });

  // The bug the store exists to prevent: a queued notice expiring unseen.
  // All four share one timeout T. If the 4th's timer had started at push time
  // it would expire at T alongside the three visible ones; because it starts on
  // PROMOTION it survives to 2T, which is what these two tests pin down.
  it("does not start a pending notice's timer until it becomes visible", () => {
    const store = useNoticeStore();
    for (let i = 0; i < 4; i++) store.success(`n${i}`);
    const fourth = store.notices[3];
    const T = store.notices[0].timeout;

    vi.advanceTimersByTime(T);
    // The three visible ones are gone; the queued one is not.
    expect(store.notices.map((n) => n.id)).toEqual([fourth.id]);
  });

  it("starts the timer when a pending notice is promoted", () => {
    const store = useNoticeStore();
    for (let i = 0; i < 4; i++) store.success(`n${i}`);
    const T = store.notices[0].timeout;

    vi.advanceTimersByTime(T);
    expect(store.notices).toHaveLength(1);
    // Promoted at T, so it gets a full fresh window rather than a partial one.
    vi.advanceTimersByTime(T - 1);
    expect(store.notices).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(store.notices).toHaveLength(0);
  });

  it("honours a cap change from the host (3 to 2 below 600px)", () => {
    const store = useNoticeStore();
    for (let i = 0; i < 4; i++) store.push({ text: `n${i}`, timeout: 0 });
    store.setMaxVisible(2);
    expect(store.visible).toHaveLength(2);
    expect(store.pending).toHaveLength(2);
  });

  it("ignores a nonsense cap", () => {
    const store = useNoticeStore();
    store.setMaxVisible(0);
    store.setMaxVisible(-3);
    store.setMaxVisible("many");
    expect(store.maxVisible).toBe(3);
  });

  // §5 - an error is never queued behind a success. The oldest non-error yields
  // its visible slot, but is DEMOTED rather than destroyed: the insert is what
  // buys the room, so there is nothing to be gained by losing the message.
  it("displaces the oldest non-error to make room for an error", () => {
    const store = useNoticeStore();
    store.push({ level: "success", text: "s1", timeout: 0 });
    store.push({ level: "info", text: "i1", timeout: 0 });
    store.push({ level: "info", text: "i2", timeout: 0 });
    store.error("boom");
    expect(store.visible.map((n) => n.text)).toContain("boom");
    expect(store.notices.map((n) => n.text)).toContain("s1");
    expect(store.pending.map((n) => n.text)).toEqual(["s1"]);
  });

  it("does not evict another error", () => {
    const store = useNoticeStore();
    store.error("e1");
    store.error("e2");
    store.error("e3");
    store.error("e4");
    expect(store.notices).toHaveLength(4);
    expect(store.notices.map((n) => n.text)).toContain("e1");
  });
});

describe("useNoticeStore - pause and resume (§9.3, WCAG 2.2.1)", () => {
  it("pauses a countdown and banks the remaining time", () => {
    const store = useNoticeStore();
    const id = store.info("hover me");
    const effective = store.notices[0].timeout;
    vi.advanceTimersByTime(effective - 500);
    store.pause(id);
    vi.advanceTimersByTime(60000);
    expect(store.notices).toHaveLength(1);
    store.resume(id);
    vi.advanceTimersByTime(499);
    expect(store.notices).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(store.notices).toHaveLength(0);
  });

  it("pauseAll / resumeAll freeze and restart every countdown", () => {
    const store = useNoticeStore();
    store.info("a");
    store.info("b");
    const effective = store.notices[0].timeout;
    vi.advanceTimersByTime(100);
    store.pauseAll();
    vi.advanceTimersByTime(60000);
    expect(store.notices).toHaveLength(2);
    store.resumeAll();
    vi.advanceTimersByTime(effective);
    expect(store.notices).toHaveLength(0);
  });

  it("resumeAll does not restart an individually paused notice", () => {
    const store = useNoticeStore();
    const a = store.info("a");
    store.info("b");
    store.pause(a);
    store.pauseAll();
    store.resumeAll();
    vi.advanceTimersByTime(60000);
    expect(store.notices.map((n) => n.id)).toEqual([a]);
  });
});

describe("useNoticeStore - action contract (§9.4)", () => {
  it("invoking the action dismisses the notice", () => {
    const store = useNoticeStore();
    const handler = vi.fn();
    const id = store.push({
      text: "undo me",
      action: { label: "Undo", handler },
    });
    store.invokeAction(id);
    expect(handler).toHaveBeenCalledTimes(1);
    expect(store.notices).toHaveLength(0);
  });

  it("keeps the notice when the handler returns false", () => {
    const store = useNoticeStore();
    const id = store.push({
      text: "retry me",
      action: { label: "Retry", handler: () => false },
    });
    store.invokeAction(id);
    expect(store.notices).toHaveLength(1);
  });

  it("dismisses even when the handler throws", () => {
    const store = useNoticeStore();
    const err = vi.spyOn(console, "error").mockImplementation(() => {});
    const id = store.push({
      text: "boom",
      action: {
        label: "Go",
        handler: () => {
          throw new Error("nope");
        },
      },
    });
    store.invokeAction(id);
    expect(store.notices).toHaveLength(0);
    expect(err).toHaveBeenCalled();
    err.mockRestore();
  });

  it("ignores an action-less notice", () => {
    const store = useNoticeStore();
    const id = store.push({ text: "plain", timeout: 0 });
    store.invokeAction(id);
    expect(store.notices).toHaveLength(1);
  });
});

describe("useNoticeStore - dismissByKey (§9.6 scoped notices)", () => {
  it("dismisses the notice carrying the key and leaves the rest", () => {
    const store = useNoticeStore();
    store.push({ text: "scoped", timeout: 0, key: "locked-card" });
    store.push({ text: "unrelated", timeout: 0, key: "other" });

    expect(store.dismissByKey("locked-card")).toBe(1);
    expect(store.notices.map((n) => n.key)).toEqual(["other"]);
  });

  it("clears the dismissed notice's timer", () => {
    const store = useNoticeStore();
    store.push({ text: "scoped", timeout: 5000, key: "locked-card" });
    store.dismissByKey("locked-card");
    // A stale timer firing against a dropped id would throw or resurrect state;
    // running the clock proves neither happens.
    vi.advanceTimersByTime(10000);
    expect(store.notices).toEqual([]);
  });

  it("promotes a pending notice when a scoped one is retired", () => {
    const store = useNoticeStore();
    store.setMaxVisible(1);
    store.push({ text: "scoped", timeout: 0, key: "locked-card" });
    store.push({ text: "queued", timeout: 0, key: "queued" });
    expect(store.visible.map((n) => n.key)).toEqual(["locked-card"]);

    store.dismissByKey("locked-card");
    expect(store.visible.map((n) => n.key)).toEqual(["queued"]);
  });

  it("is a no-op for an unknown or null key", () => {
    const store = useNoticeStore();
    store.push({ text: "scoped", timeout: 0, key: "locked-card" });
    expect(store.dismissByKey("never-pushed")).toBe(0);
    expect(store.dismissByKey(null)).toBe(0);
    expect(store.notices).toHaveLength(1);
  });
});

describe("useNoticeStore - explicit timeout outranks the action rule (§6 rule 1)", () => {
  it("keeps action⇒sticky as the default", () => {
    expect(
      resolveTimeout({ baseTimeout: 3000, text: "done", hasAction: true }),
    ).toBe(0);
  });

  it("honours a window the caller asked for on an action card", () => {
    expect(
      resolveTimeout({
        baseTimeout: 6000,
        text: "hi",
        hasAction: true,
        explicit: true,
      }),
    ).toBe(6000);
  });

  it("still lets an explicit 0 mean sticky", () => {
    expect(
      resolveTimeout({
        baseTimeout: 0,
        text: "hi",
        hasAction: true,
        explicit: true,
      }),
    ).toBe(0);
  });

  it("does not cap an explicit window at the reading-time ceiling", () => {
    // The ceiling exists to bound the COMPUTED reading time. Applying it to a
    // deliberate choice silently rewrote a 30s card as 12s.
    expect(
      resolveTimeout({ baseTimeout: 30000, text: "hi", explicit: true }),
    ).toBe(30000);
  });

  it("still raises an explicit window to the reading time of a long message", () => {
    const text = "x".repeat(100); // 2000 + 60*100 = 8000
    expect(resolveTimeout({ baseTimeout: 3000, text, explicit: true })).toBe(
      8000,
    );
  });

  it("auto-dismisses an action card that asked for a window", () => {
    const store = useNoticeStore();
    store.warning("3 selected pictures are in locked sets.", {
      timeout: 6000,
      action: { label: "Help", handler: () => {} },
    });
    const effective = store.notices[0].timeout;
    expect(effective).toBeGreaterThan(0);
    vi.advanceTimersByTime(effective);
    expect(store.notices).toEqual([]);
  });
});
