// An export asks where to save it (the desktop app otherwise drops it in
// Downloads without a word). The desktop shell's dialog writes it; a browser
// downloads it, under its own "ask where to save" setting.

import { afterEach, describe, expect, it, vi } from "vitest";

import { saveFileAs } from "./downloadFile";

const blob = () => new Blob(["{}"], { type: "application/json" });

describe("saveFileAs", () => {
  afterEach(() => {
    delete window.pixlstashDesktop;
    vi.restoreAllMocks();
  });

  it("writes through the desktop Save dialog", async () => {
    const completeMediaSaveAs = vi.fn(async () => ({ saved: true }));
    window.pixlstashDesktop = {
      beginMediaSaveAs: vi.fn(async () => ({ canceled: false, saveId: "s1" })),
      completeMediaSaveAs,
    };
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click");
    expect(await saveFileAs(blob(), "w.json")).toBe(true);
    expect(window.pixlstashDesktop.beginMediaSaveAs).toHaveBeenCalledWith(
      "w.json",
    );
    const [saveId, bytes] = completeMediaSaveAs.mock.calls[0];
    expect(saveId).toBe("s1");
    expect(new TextDecoder().decode(bytes)).toBe("{}");
    expect(click).not.toHaveBeenCalled();
  });

  it("writes nothing when the desktop dialog is cancelled", async () => {
    const completeMediaSaveAs = vi.fn();
    window.pixlstashDesktop = {
      beginMediaSaveAs: vi.fn(async () => ({ canceled: true })),
      completeMediaSaveAs,
    };
    expect(await saveFileAs(blob(), "w.json")).toBe(false);
    expect(completeMediaSaveAs).not.toHaveBeenCalled();
  });

  it("releases the desktop request and throws when the write fails", async () => {
    const cancelMediaSaveAs = vi.fn();
    window.pixlstashDesktop = {
      beginMediaSaveAs: vi.fn(async () => ({ canceled: false, saveId: "s1" })),
      completeMediaSaveAs: vi.fn(async () => ({ saved: false })),
      cancelMediaSaveAs,
    };
    await expect(saveFileAs(blob(), "w.json")).rejects.toThrow();
    expect(cancelMediaSaveAs).toHaveBeenCalledWith("s1");
  });

  it("downloads in a browser, with no desktop shell", async () => {
    global.URL.createObjectURL = vi.fn(() => "blob:x");
    global.URL.revokeObjectURL = vi.fn();
    const names = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(
      function record() {
        names.push(this.download);
      },
    );
    expect(await saveFileAs(blob(), "w.json")).toBe(true);
    expect(names).toEqual(["w.json"]);
  });
});
