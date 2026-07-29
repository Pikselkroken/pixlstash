import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { useOneTimeNotice, ONE_TIME_NOTICE_PREFIX } from "./useOneTimeNotice";

const realStorage = window.localStorage;

/**
 * Swap in a storage object that throws, the way private mode and a full quota
 * do. jsdom's own `localStorage` is a host object whose methods cannot be
 * reliably spied, so the whole property is replaced for the duration.
 */
function withBrokenStorage(overrides) {
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: {
      getItem: () => null,
      setItem: () => {},
      removeItem: () => {},
      clear: () => {},
      ...overrides,
    },
  });
}

beforeEach(() => {
  realStorage.clear();
});

afterEach(() => {
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: realStorage,
  });
  vi.restoreAllMocks();
});

describe("useOneTimeNotice", () => {
  it("shows on a first visit", () => {
    const { visible } = useOneTimeNotice("dedup-migration");
    expect(visible.value).toBe(true);
  });

  // The whole point: a migration nudge that came back after a reload would be
  // an ad, not a nudge.
  it("stays hidden after a dismissal survives a reload", () => {
    const first = useOneTimeNotice("dedup-migration");
    first.dismiss();
    expect(first.visible.value).toBe(false);

    const second = useOneTimeNotice("dedup-migration");
    expect(second.visible.value).toBe(false);
  });

  it("namespaces its key", () => {
    const { storageKey, dismiss } = useOneTimeNotice("dedup-migration");
    expect(storageKey).toBe(`${ONE_TIME_NOTICE_PREFIX}dedup-migration`);
    dismiss();
    expect(window.localStorage.getItem(storageKey)).toBe("1");
  });

  it("dismissing twice is a no-op", () => {
    const notice = useOneTimeNotice("dedup-migration");
    notice.dismiss();
    notice.dismiss();
    expect(notice.visible.value).toBe(false);
  });

  it("reset makes it show again", () => {
    const notice = useOneTimeNotice("dedup-migration");
    notice.dismiss();
    notice.reset();
    expect(notice.visible.value).toBe(true);
    expect(window.localStorage.getItem(notice.storageKey)).toBe(null);
  });

  // Two notices must not share a flag, or dismissing one silences the other.
  it("keeps separate notices independent", () => {
    const a = useOneTimeNotice("one");
    const b = useOneTimeNotice("two");
    a.dismiss();
    expect(useOneTimeNotice("one").visible.value).toBe(false);
    expect(useOneTimeNotice("two").visible.value).toBe(true);
    expect(b.visible.value).toBe(true);
  });

  // Private mode throws on getItem. Showing the notice once more is the right
  // failure mode; crashing the view that hosts it is not.
  it("treats unreadable storage as not yet seen", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    withBrokenStorage({
      getItem: () => {
        throw new Error("denied");
      },
    });
    const { visible } = useOneTimeNotice("dedup-migration");
    expect(visible.value).toBe(true);
    expect(warn).toHaveBeenCalled();
  });

  it("survives storage refusing the write", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    withBrokenStorage({
      setItem: () => {
        throw new Error("quota");
      },
    });
    const notice = useOneTimeNotice("dedup-migration");
    expect(() => notice.dismiss()).not.toThrow();
    expect(notice.visible.value).toBe(false);
    expect(warn).toHaveBeenCalled();
  });
});
