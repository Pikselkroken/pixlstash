// The model-folder registry, and the scans the server runs against it.
//
// The assertions worth having are the ones that pin decisions which are easy to
// "simplify" back into bugs: nobody starts a scan, so "scanning" is whatever the
// server's `scan_status` says and the poll must stop once none is in flight;
// only the first scan of a folder added here is announced, never the server's
// own rescans; and forgetting a folder is only cheap to undo because the row's
// fields are captured BEFORE the request that destroys them.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";

const listModelFolders = vi.fn();
const createModelFolder = vi.fn();
const forgetModelFolder = vi.fn();
const setDefaultModelFolder = vi.fn();
const listModelFolderDirs = vi.fn();

vi.mock("../api/modelFolders", () => ({
  MANAGED_KIND: "managed",
  SOURCE_KIND: "source",
  DOWNLOADS_KIND: "downloads",
  CREATABLE_KINDS: ["user", "source", "downloads"],
  listModelFolders: (...a) => listModelFolders(...a),
  createModelFolder: (...a) => createModelFolder(...a),
  forgetModelFolder: (...a) => forgetModelFolder(...a),
  setDefaultModelFolder: (...a) => setDefaultModelFolder(...a),
  listModelFolderDirs: (...a) => listModelFolderDirs(...a),
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
    path: "/home/me/loras",
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
});

afterEach(() => {
  vi.useRealTimers();
});

describe("scanning", () => {
  it("is scanning while the server says so, and stops polling when it is not", async () => {
    listModelFolders.mockResolvedValue([folder({ scan_status: "running" })]);
    const store = useModelFoldersStore();
    await store.refresh();
    expect(store.scanningIds.has(1)).toBe(true);

    await vi.advanceTimersByTimeAsync(3000);
    expect(listModelFolders).toHaveBeenCalledTimes(2);

    listModelFolders.mockResolvedValue([folder({ scan_status: "completed" })]);
    await vi.advanceTimersByTimeAsync(3000);
    expect(store.scanningIds.size).toBe(0);
    const reads = listModelFolders.mock.calls.length;
    await vi.advanceTimersByTimeAsync(9000);
    expect(listModelFolders).toHaveBeenCalledTimes(reads);
  });

  it("announces the first scan of a folder it added, and not the server's own", async () => {
    // Folder 1 is being rescanned by the server; folder 2 was just added.
    createModelFolder.mockResolvedValue(
      folder({ id: 2, scan_status: "pending" }),
    );
    listModelFolders.mockResolvedValue([
      folder({ id: 1, path: "/home/me/other", scan_status: "running" }),
      folder({ id: 2, scan_status: "pending" }),
    ]);
    const store = useModelFoldersStore();
    await store.add({ path: "/home/me/loras", kind: "user" });
    expect(useNoticeStore().notices.at(-1).text).toContain(
      "Looking for models",
    );

    listModelFolders.mockResolvedValue([
      folder({ id: 1, path: "/home/me/other", scan_status: "completed" }),
      folder({ id: 2, scan_status: "completed", file_count: 91 }),
    ]);
    await vi.advanceTimersByTimeAsync(3000);
    const texts = useNoticeStore().notices.map((n) => n.text);
    expect(texts.at(-1)).toBe("Scanned loras. 91 files listed.");
    expect(texts.some((t) => t.includes("other"))).toBe(false);

    // Announced once: the next read does not say it again.
    await store.refresh();
    expect(useNoticeStore().notices.map((n) => n.text)).toEqual(texts);
  });

  it("says so when the first scan of a folder it added failed", async () => {
    createModelFolder.mockResolvedValue(
      folder({ id: 2, scan_status: "pending" }),
    );
    listModelFolders.mockResolvedValue([
      folder({ id: 2, scan_status: "failed", scan_error: "disk on fire" }),
    ]);
    const store = useModelFoldersStore();
    await store.add({ path: "/home/me/loras", kind: "user" });
    const notice = useNoticeStore().notices.at(-1);
    expect(notice.level).toBe("error");
    expect(notice.text).toBe("Could not scan loras: disk on fire");
  });

  it("promises no scan for an output root, which is taken from", async () => {
    createModelFolder.mockResolvedValue(
      folder({ id: 2, kind: "source", path: "/runs" }),
    );
    const store = useModelFoldersStore();

    expect(await store.add({ path: "/runs", kind: "source" })).toBe(true);
    const notice = useNoticeStore().notices.at(-1);
    expect(notice.text).toContain("ready to import");
    expect(notice.text).not.toContain("Looking for models");
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
      path: "/home/me/loras",
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

describe("subfolder listings", () => {
  it("lets a forced read supersede one in flight, and keeps the newer answer", async () => {
    const store = useModelFoldersStore();
    let answerStale;
    listModelFolderDirs
      .mockReset()
      .mockImplementationOnce(
        () => new Promise((resolve) => (answerStale = resolve)),
      )
      .mockResolvedValueOnce({ dirs: [{ name: "new" }] });
    const stale = store.loadDirs(1, "");
    // A scan landed while the first read was out: it must read again.
    await store.loadDirs(1, "", { force: true });
    expect(listModelFolderDirs).toHaveBeenCalledTimes(2);
    answerStale({ dirs: [{ name: "old" }] });
    await stale;
    expect(store.dirListings.get(store.dirsKey(1, "")).listing.dirs).toEqual([
      { name: "new" },
    ]);
  });
});

describe("session reset", () => {
  it("drops the host paths and abandons the poll", async () => {
    // Absolute paths on this machine are owner-only, so none of it may survive
    // into a share or read-only session that could never have asked for it.
    listModelFolders.mockResolvedValue([folder({ scan_status: "running" })]);
    const store = useModelFoldersStore();
    await store.refresh();
    expect(store.scanningIds.has(1)).toBe(true);
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
    expect(basename("/home/me/loras/")).toBe("loras");
    expect(basename("C:\\models\\lora")).toBe("lora");
  });
});
