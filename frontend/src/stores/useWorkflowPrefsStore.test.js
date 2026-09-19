// The Grid | List memory (v1.12 F2). Two things a reading cannot confirm: that
// the choice survives the store being rebuilt, and that a `localStorage` that
// THROWS costs the switch its memory and nothing else.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { useWorkflowPrefsStore } from "./useWorkflowPrefsStore";

const KEY = "pixlstash:workflowStackView";

/** A `localStorage` stand-in; `mode` decides whether it works or throws. */
function installStorage(mode, seed = null) {
  const values = new Map();
  if (seed !== null) values.set(KEY, seed);
  const storage = {
    getItem: vi.fn((key) => {
      if (mode === "throws") throw new Error("test-storage-denied");
      return values.has(key) ? values.get(key) : null;
    }),
    setItem: vi.fn((key, value) => {
      if (mode === "throws") throw new Error("test-storage-denied");
      values.set(key, value);
    }),
  };
  vi.stubGlobal("window", { localStorage: storage });
  return storage;
}

describe("useWorkflowPrefsStore", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    vi.spyOn(console, "warn").mockImplementation(() => {});
    setActivePinia(createPinia());
  });

  it("opens on Grid when nothing has been stored", () => {
    installStorage("works");
    expect(useWorkflowPrefsStore().stackView).toBe("grid");
  });

  it("remembers the choice for the next session", () => {
    const storage = installStorage("works");
    useWorkflowPrefsStore().setStackView("list");
    expect(storage.setItem).toHaveBeenCalledWith(KEY, "list");

    // A second store over the same storage is the next session reading it
    // back — the assertion is about what was WRITTEN, not about the ref that
    // was set, which would pass with no persistence at all.
    setActivePinia(createPinia());
    expect(useWorkflowPrefsStore().stackView).toBe("list");
  });

  it("keeps working when localStorage throws on the way in", () => {
    installStorage("throws");
    expect(useWorkflowPrefsStore().stackView).toBe("grid");
  });

  it("keeps working when localStorage throws on the way out", () => {
    installStorage("throws");
    const store = useWorkflowPrefsStore();
    expect(() => store.setStackView("list")).not.toThrow();
    // The switch still moved: the preference is not remembered, which is a
    // different thing from the screen refusing to change.
    expect(store.stackView).toBe("list");
  });

  it("reads a stored value it does not recognise as the default", () => {
    installStorage("works", "carousel");
    expect(useWorkflowPrefsStore().stackView).toBe("grid");
  });

  it("refuses to store a view it cannot draw", () => {
    const storage = installStorage("works");
    const store = useWorkflowPrefsStore();
    store.setStackView("carousel");
    expect(store.stackView).toBe("grid");
    expect(storage.setItem).not.toHaveBeenCalled();
  });
});
