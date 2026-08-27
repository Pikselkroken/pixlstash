// The model-folder registry, and the scans it waits on.
//
// The assertions worth having are the ones that pin decisions which are easy to
// "simplify" back into bugs: the API reports a scan STARTED, never finished, so
// the only completion signal is `last_checked` advancing and the poll must both
// find it and give up when it never comes; and forgetting a folder is only
// cheap to undo because the row's fields are captured BEFORE the request that
// destroys them.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";

const listModelFolders = vi.fn();
const createModelFolder = vi.fn();
const forgetModelFolder = vi.fn();
const rescanModelFolder = vi.fn();

vi.mock("../api/modelFolders", () => ({
  MANAGED_KIND: "managed",
  SOURCE_KIND: "source",
  CREATABLE_KINDS: ["user", "source"],
  listModelFolders: (...a) => listModelFolders(...a),
  createModelFolder: (...a) => createModelFolder(...a),
  forgetModelFolder: (...a) => forgetModelFolder(...a),
  rescanModelFolder: (...a) => rescanModelFolder(...a),
}));

const fetchRows = vi.fn();
vi.mock("./useModelShelfStore", () => ({
  useModelShelfStore: () => ({ fetchRows }),
}));

import {
  useModelFoldersStore,
  basename,
  countLabel,
} from "./useModelFoldersStore";
import { useNoticeStore } from "./useNoticeStore";

/** One row of the shape `GET /model-folders` really returns. */
function folder(overrides = {}) {
  return {
    id: 1,
    path: "/home/g/loras",
    kind: "user",
    owner: null,
    movable: "per_item",
    host_path: null,
    delete_after_import: false,
    last_checked: null,
    created_at: "2026-08-01T10:00:00",
    file_count: 91,
    ...overrides,
  };
}

beforeEach(() => {
  setActivePinia(createPinia());
  vi.useFakeTimers();
  fetchRows.mockReset();
  listModelFolders.mockReset().mockResolvedValue([folder()]);
  createModelFolder.mockReset().mockResolvedValue(folder({ id: 2 }));
  forgetModelFolder
    .mockReset()
    .mockResolvedValue({ status: "success", id: 1, tombstoned_files: 91 });
  rescanModelFolder.mockReset().mockResolvedValue({ status: "started", id: 1 });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("scanning", () => {
  it("waits for last_checked to advance, then refreshes the shelf", async () => {
    // The route answers 202 the instant the thread starts, so "started" is not
    // "done". Treating the response as completion would refresh the shelf
    // before a single file had been read.
    const store = useModelFoldersStore();
    await store.refresh();
    await store.scan(1);
    expect(store.scanningIds.has(1)).toBe(true);
    expect(fetchRows).not.toHaveBeenCalled();

    // A poll that finds the same stamp leaves the scan running.
    await vi.advanceTimersByTimeAsync(3000);
    expect(store.scanningIds.has(1)).toBe(true);

    listModelFolders.mockResolvedValue([
      folder({ last_checked: "2026-08-09T12:00:00", file_count: 91 }),
    ]);
    await vi.advanceTimersByTimeAsync(3000);
    expect(store.scanningIds.has(1)).toBe(false);
    expect(fetchRows).toHaveBeenCalledTimes(1);
    expect(useNoticeStore().notices[0].text).toContain("91 files listed");
  });

  it("gives up on a scan that never stamps, instead of polling forever", async () => {
    // The scanner logs its exception and returns WITHOUT touching
    // `last_checked`, so a crash is indistinguishable from a slow read on the
    // wire. Unbounded, this poll would run for the life of the tab.
    const store = useModelFoldersStore();
    await store.refresh();
    await store.scan(1);

    await vi.advanceTimersByTimeAsync(10 * 60 * 1000 + 3000);
    expect(store.scanningIds.has(1)).toBe(false);
    expect(fetchRows).not.toHaveBeenCalled();
    const notice = useNoticeStore().notices.at(-1);
    expect(notice.level).toBe("warning");
    expect(notice.text).toContain("Still scanning");
  });

  it("does not watch a source folder, which is taken from and never scanned", async () => {
    rescanModelFolder.mockResolvedValue({ status: "skipped", id: 1 });
    const store = useModelFoldersStore();
    await store.refresh();
    await store.scan(1);
    expect(store.scanningIds.size).toBe(0);
  });

  it("does not even submit a scan when the folder added is an output root", async () => {
    // The server answers a source folder's scan with `skipped`, so submitting
    // one spends a task to do nothing - and the toast that promises it would be
    // describing work that never happens. Its runs are read live instead.
    createModelFolder.mockResolvedValue(
      folder({ id: 2, kind: "source", path: "/runs" }),
    );
    const store = useModelFoldersStore();

    expect(await store.add({ path: "/runs", kind: "source" })).toBe(true);
    expect(rescanModelFolder).not.toHaveBeenCalled();
    const notice = useNoticeStore().notices.at(-1);
    expect(notice.text).toContain("ready to import");
    expect(notice.text).not.toContain("Looking for models");
  });

  it("still scans an ordinary folder, which is the reason the branch exists", async () => {
    // The positive control. A branch that skipped the scan for everything would
    // pass the assertion above and break every other add.
    rescanModelFolder.mockResolvedValue({ status: "started", id: 2 });
    const store = useModelFoldersStore();

    await store.add({ path: "/home/g/loras", kind: "user" });
    expect(rescanModelFolder).toHaveBeenCalled();
    expect(useNoticeStore().notices.at(-1).text).toContain(
      "Looking for models",
    );
  });

  it("names the one output root, and nothing else", async () => {
    // What every "is it set yet" control in the UI reads.
    listModelFolders.mockResolvedValue([
      folder({ id: 1, kind: "user" }),
      folder({ id: 2, kind: "source", path: "/runs" }),
      folder({ id: 3, kind: "managed", path: "/store" }),
    ]);
    const store = useModelFoldersStore();
    await store.refresh();
    expect(store.sourceFolder?.path).toBe("/runs");
  });

  it("has no output root until one is registered", async () => {
    listModelFolders.mockResolvedValue([folder({ id: 1, kind: "user" })]);
    const store = useModelFoldersStore();
    await store.refresh();
    expect(store.sourceFolder).toBeUndefined();
  });
});

describe("forgetting", () => {
  it("offers the way back with the fields the row had before the delete", async () => {
    // The undo is the whole reason this needs no confirmation prompt. Reading
    // the path back after the DELETE would read nothing.
    const store = useModelFoldersStore();
    listModelFolders.mockResolvedValue([
      folder({ host_path: "/mnt/host/loras" }),
    ]);
    await store.refresh();
    const row = store.folders[0];

    listModelFolders.mockResolvedValue([]);
    await store.forget(row);

    const notice = useNoticeStore().notices.at(-1);
    expect(notice.text).toContain("91 files left the shelf");
    expect(notice.action.label).toBe("Add it back");

    await notice.action.handler();
    expect(createModelFolder).toHaveBeenCalledWith({
      path: "/home/g/loras",
      kind: "user",
      hostPath: "/mnt/host/loras",
      deleteAfterImport: false,
    });
  });

  it("says nothing was listed rather than 0 files", async () => {
    forgetModelFolder.mockResolvedValue({ tombstoned_files: 0 });
    const store = useModelFoldersStore();
    await store.refresh();
    await store.forget(store.folders[0]);
    expect(useNoticeStore().notices.at(-1).text).toContain(
      "Nothing was listed from it",
    );
  });
});

describe("session reset", () => {
  it("drops the host paths and abandons the poll", async () => {
    // Absolute paths on this machine are owner-only, so none of it may survive
    // into a share or read-only session that could never have asked for it.
    const store = useModelFoldersStore();
    await store.refresh();
    await store.scan(1);
    store.resetForSession();

    expect(store.folders).toEqual([]);
    expect(store.loaded).toBe(false);
    expect(store.scanningIds.size).toBe(0);

    listModelFolders.mockClear();
    await vi.advanceTimersByTimeAsync(9000);
    expect(listModelFolders).not.toHaveBeenCalled();
  });
});

describe("sentence helpers", () => {
  it("never reads '1 files'", () => {
    expect(countLabel(1)).toBe("1 file");
    expect(countLabel(1204)).toBe("1,204 files");
  });

  it("names the folder by its last segment, trailing slash or not", () => {
    expect(basename("/home/g/loras/")).toBe("loras");
    expect(basename("C:\\models\\lora")).toBe("lora");
  });
});
