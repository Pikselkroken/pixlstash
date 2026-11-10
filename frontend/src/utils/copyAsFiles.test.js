import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { setActivePinia, createPinia } from "pinia";

vi.mock("../api/pictures", () => ({ makeShareCopies: vi.fn() }));

import { makeShareCopies } from "../api/pictures";
import { useNoticeStore } from "../stores/useNoticeStore";
import {
  KEPT_DATA_BADGE,
  canCopyFiles,
  copyPicturesAsFiles,
} from "./copyAsFiles";

const copyFilesToClipboard = vi.fn(async () => ({ copied: 1 }));

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
  window.pixlstashDesktop = { copyFilesToClipboard };
});
afterEach(() => {
  delete window.pixlstashDesktop;
});

function notices() {
  const store = useNoticeStore();
  return {
    success: vi.spyOn(store, "success"),
    error: vi.spyOn(store, "error"),
  };
}

describe("copyPicturesAsFiles", () => {
  it("is available only in the desktop app", () => {
    expect(canCopyFiles()).toBe(true);
    delete window.pixlstashDesktop;
    expect(canCopyFiles()).toBe(false);
  });

  it("stages share copies, hands their paths to the desktop and shows the receipt", async () => {
    const n = notices();
    makeShareCopies.mockResolvedValue({
      files: [{ path: "/home/me/a.png" }, { path: "/home/me/b.png" }],
      skipped: [],
    });
    expect(await copyPicturesAsFiles([1, 2])).toBe(2);
    expect(makeShareCopies).toHaveBeenCalledWith([1, 2], {
      link_originals: true,
      file_names: "made",
    });
    expect(copyFilesToClipboard).toHaveBeenCalledWith([
      "/home/me/a.png",
      "/home/me/b.png",
    ]);
    expect(n.success).toHaveBeenCalledWith("Copied 2 files.", expect.anything());
  });

  it("badges the receipt when a file kept its generation data", async () => {
    const n = notices();
    makeShareCopies.mockResolvedValue({
      files: [
        { path: "/home/me/a.png", metadata: "removed" },
        { path: "/home/me/b.png", metadata: "kept" },
      ],
      skipped: [],
    });
    await copyPicturesAsFiles([1, 2]);
    expect(n.success).toHaveBeenCalledWith(
      "Copied 2 files.",
      expect.objectContaining({ badge: KEPT_DATA_BADGE }),
    );
  });

  it("leaves the badge off when every file was stripped", async () => {
    const n = notices();
    makeShareCopies.mockResolvedValue({
      files: [{ path: "/home/me/a.png", metadata: "removed" }],
      skipped: [],
    });
    await copyPicturesAsFiles([1]);
    expect(n.success).toHaveBeenCalledWith(
      "Copied 1 file.",
      expect.objectContaining({ badge: null }),
    );
  });

  it("says how many could not be read", async () => {
    const n = notices();
    makeShareCopies.mockResolvedValue({
      files: [{ path: "/home/me/a.png" }],
      skipped: [{ picture_id: 2, reason: "missing" }],
    });
    await copyPicturesAsFiles([1, 2]);
    expect(n.success).toHaveBeenCalledWith(
      "Copied 1 file. 1 could not be read.",
      expect.anything(),
    );
  });

  it("touches no clipboard when nothing was staged", async () => {
    const n = notices();
    makeShareCopies.mockResolvedValue({
      files: [],
      skipped: [{ picture_id: 1, reason: "missing" }],
    });
    expect(await copyPicturesAsFiles([1])).toBe(0);
    expect(copyFilesToClipboard).not.toHaveBeenCalled();
    expect(n.error).toHaveBeenCalled();
  });

  it("hands over any number of pictures, as links to the originals", async () => {
    makeShareCopies.mockResolvedValue({
      files: [{ path: "/home/me/a.png" }],
      skipped: [],
    });
    const ids = Array.from({ length: 5000 }, (_, i) => i + 1);
    expect(await copyPicturesAsFiles(ids)).toBe(1);
    expect(makeShareCopies).toHaveBeenCalledWith(ids, { link_originals: true, file_names: "made" });
  });

  it("reports the server's reason when staging is refused", async () => {
    const n = notices();
    vi.spyOn(console, "error").mockImplementation(() => {});
    makeShareCopies.mockRejectedValue({
      response: { data: { detail: "The staging folder is inside a library." } },
    });
    expect(await copyPicturesAsFiles([1])).toBe(0);
    expect(copyFilesToClipboard).not.toHaveBeenCalled();
    expect(n.error.mock.calls[0][0]).toContain("inside a library");
  });
});
