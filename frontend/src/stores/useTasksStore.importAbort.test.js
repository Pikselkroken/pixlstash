import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";

// The store imports the singleton apiClient (+ isReadOnly); mock so no HTTP runs.
vi.mock("../utils/apiClient", () => ({
  apiClient: { get: vi.fn() },
  isReadOnly: { value: false },
}));

import { useTasksStore } from "./useTasksStore";

beforeEach(() => {
  setActivePinia(createPinia());
});

// These cover the import-abort subsystem that ImageImporter now wires live: a
// registered handler must fire on abortImportRun, and must NOT fire once the run
// is unregistered (the terminal / unmount cleanup path).
describe("useTasksStore import abort wiring", () => {
  it("invokes the registered handler when abortImportRun is called", () => {
    const store = useTasksStore();
    const handler = vi.fn();
    store.registerImportAbort("import-1", handler);

    store.abortImportRun("import-1");

    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("does not fire after unregisterImportAbort (cleanup path)", () => {
    const store = useTasksStore();
    const handler = vi.fn();
    store.registerImportAbort("import-2", handler);
    store.unregisterImportAbort("import-2");

    store.abortImportRun("import-2");

    expect(handler).not.toHaveBeenCalled();
  });

  it("abortImportRun is a no-op for an unknown run id", () => {
    const store = useTasksStore();
    expect(() => store.abortImportRun("does-not-exist")).not.toThrow();
  });

  it("ignores a non-function handler", () => {
    const store = useTasksStore();
    store.registerImportAbort("import-3", null);
    expect(() => store.abortImportRun("import-3")).not.toThrow();
  });

  it("carries the honest `abortable` flag through setImportRun", () => {
    const store = useTasksStore();
    // Committed server-side rows are not client-abortable → no cancel affordance.
    store.setImportRun("import-4", { current: 0, total: 10 });
    expect(store.importRuns["import-4"].abortable).toBe(false);
    // A run flagged abortable surfaces the cancel affordance in the Tasks tab.
    store.setImportRun("import-5", { current: 0, total: 10, abortable: true });
    expect(store.importRuns["import-5"].abortable).toBe(true);
  });
});
