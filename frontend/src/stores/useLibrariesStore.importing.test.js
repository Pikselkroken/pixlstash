// The shell names the library being imported for the first time.
//
// Such a library is deliberately absent from `libraries`: it does not exist
// until its import finishes, so it cannot be switched to or managed. Without
// `importing_name` the shell would show nothing at all on the one occasion the
// machine has no other library - see
// business/plans/pixlstash-temp-vault-first-import-plan.md.

import { beforeEach, describe, it, expect, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";

const { listLibraries } = vi.hoisted(() => ({ listLibraries: vi.fn() }));
vi.mock("../api/libraries", async (importOriginal) => ({
  ...(await importOriginal()),
  listLibraries,
}));

import { useLibrariesStore } from "./useLibrariesStore";

describe("useLibrariesStore importing name", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    listLibraries.mockReset();
  });

  it("carries the name of a library being imported, with nothing listed", async () => {
    listLibraries.mockResolvedValue({
      libraries: [],
      can_manage: true,
      importing_name: "Holiday Snaps",
    });
    const store = useLibrariesStore();

    await store.refresh();

    expect(store.importingName).toBe("Holiday Snaps");
    expect(store.libraries).toEqual([]);
    expect(store.activeLibrary).toBeUndefined();
  });

  it("is empty once the import has finished and the library is listed", async () => {
    listLibraries.mockResolvedValue({
      libraries: [{ uuid: "a", name: "Holiday Snaps", is_active: true }],
      can_manage: true,
    });
    const store = useLibrariesStore();

    await store.refresh();

    expect(store.importingName).toBe("");
    expect(store.activeLibrary.name).toBe("Holiday Snaps");
  });
});
