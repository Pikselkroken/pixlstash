// The folder drop rules and the one decision about whether a drop asks first
// (#2206). A drop that is a rename starts with no dialog; a copy across drives
// reads its facts and waits for the reader.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

const moveModelFolderDir = vi.fn();
vi.mock("../api/modelFolders", async (importOriginal) => ({
  ...(await importOriginal()),
  moveModelFolderDir: (...args) => moveModelFolderDir(...args),
}));
vi.mock("../api/modelMoves", () => ({
  startModelMove: vi.fn(),
  getModelMoveStatus: vi.fn(async () => ({ status: "idle", results: [] })),
  cancelModelMove: vi.fn(),
  relocateModelFolder: vi.fn(),
}));

import {
  askFolderMove,
  folderDropRefused,
  folderMechanismWords,
  isWithin,
  parentPath,
} from "./useModelFolderMove";
import { useModelMovesStore } from "../stores/useModelMovesStore";

const SOURCE = { folderId: 1, path: "Lora/sdxl" };

describe("where a folder may be dropped", () => {
  it("reads the parent of a folder path, and the root for a top-level one", () => {
    expect(parentPath("Lora/sdxl")).toBe("Lora");
    expect(parentPath("Lora")).toBe("");
    expect(parentPath("")).toBe("");
  });

  it("means the directory itself or a descendant in the same folder, not a name that merely starts alike", () => {
    expect(isWithin(SOURCE, { folderId: 1, path: "Lora/sdxl" })).toBe(true);
    expect(isWithin(SOURCE, { folderId: 1, path: "Lora/sdxl/v2" })).toBe(true);
    expect(isWithin(SOURCE, { folderId: 1, path: "Lora/sdxl2" })).toBe(false);
    expect(isWithin(SOURCE, { folderId: 2, path: "Lora/sdxl" })).toBe(false);
  });

  it("refuses a drive or locked folder, the folder itself, its descendants, and the folder it is already in", () => {
    expect(folderDropRefused(SOURCE, null)).toBe(true);
    expect(folderDropRefused(SOURCE, { folderId: 2, path: "", locked: true })).toBe(true);
    expect(folderDropRefused(SOURCE, { folderId: 1, path: "Lora/sdxl" })).toBe(true);
    expect(folderDropRefused(SOURCE, { folderId: 1, path: "Lora/sdxl/v2" })).toBe(true);
    expect(folderDropRefused(SOURCE, { folderId: 1, path: "Lora" })).toBe(true);
  });

  it("takes any other folder, including the root of another registered folder", () => {
    expect(folderDropRefused(SOURCE, { folderId: 1, path: "VAE" })).toBe(false);
    expect(folderDropRefused(SOURCE, { folderId: 2, path: "" })).toBe(false);
    expect(folderDropRefused(SOURCE, { folderId: 2, path: "Lora" })).toBe(false);
  });

  it("names the drive words the way the server measures the drive", () => {
    const devices = new Map([
      [1, { device_id: "d1", label: "System" }],
      [2, { device_id: "d1", label: "System" }],
      [3, { device_id: "d2", label: "Drive B" }],
      [4, { device_id: null }],
    ]);
    expect(folderMechanismWords(SOURCE, { folderId: 2 }, devices)).toBe(
      "Moved on the same drive, nothing copied",
    );
    expect(folderMechanismWords(SOURCE, { folderId: 3 }, devices)).toBe(
      "Copied to another drive, then removed",
    );
    // An unmeasured drive is never guessed as "same".
    expect(folderMechanismWords(SOURCE, { folderId: 4 }, devices)).toBe(
      "May need copying, so it can take a while",
    );
  });
});

describe("whether a folder drop asks first", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    moveModelFolderDir.mockReset();
    vi.useFakeTimers();
  });

  afterEach(() => {
    useModelMovesStore().resetForSession();
    vi.useRealTimers();
  });

  it("starts a rename-or-same-drive move at once, with no confirmation", async () => {
    moveModelFolderDir.mockImplementation(async (id, body) =>
      body.dryRun
        ? { same_drive: true }
        : { status: "running", kind: "folder", total: 1, done: 0, results: [] },
    );
    const result = await askFolderMove(SOURCE, { folderId: 1, path: "VAE" });
    expect(result.started).toBe(true);
    expect(moveModelFolderDir).toHaveBeenCalledTimes(2);
  });

  it("hands a copy across drives back with its facts, and moves nothing", async () => {
    moveModelFolderDir.mockResolvedValue({ same_drive: false, bytes: 10 });
    const result = await askFolderMove(SOURCE, { folderId: 2, path: "" });
    expect(result.started).toBe(false);
    expect(result.plan.bytes).toBe(10);
    expect(moveModelFolderDir).toHaveBeenCalledTimes(1);
  });
});
