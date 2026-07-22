import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useNoticeStore } from "./useNoticeStore";

beforeEach(() => {
  setActivePinia(createPinia());
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useNoticeStore", () => {
  it("pushes a notice and returns its id", () => {
    const store = useNoticeStore();
    const id = store.push({ level: "info", text: "hello", timeout: 0 });
    expect(store.notices).toHaveLength(1);
    expect(store.notices[0]).toMatchObject({ id, level: "info", text: "hello" });
  });

  it("level wrappers set the level and text", () => {
    const store = useNoticeStore();
    store.error("boom", { timeout: 0 });
    store.success("yay", { timeout: 0 });
    expect(store.notices.map((n) => n.level)).toEqual(["error", "success"]);
    expect(store.notices.map((n) => n.text)).toEqual(["boom", "yay"]);
  });

  it("errors are sticky by default (no auto-dismiss)", () => {
    const store = useNoticeStore();
    store.error("boom");
    vi.advanceTimersByTime(60_000);
    expect(store.notices).toHaveLength(1);
  });

  it("auto-dismisses after the timeout elapses", () => {
    const store = useNoticeStore();
    store.push({ level: "info", text: "bye", timeout: 1000 });
    expect(store.notices).toHaveLength(1);
    vi.advanceTimersByTime(1000);
    expect(store.notices).toHaveLength(0);
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
});
