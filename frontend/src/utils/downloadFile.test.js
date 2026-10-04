// An export asks where to save it (the desktop app otherwise drops it in
// Downloads without a word). The desktop shell's dialog wins, then the
// browser's picker; cancelling either writes nothing.

import { afterEach, describe, expect, it, vi } from "vitest";

import { saveFileAs } from "./downloadFile";

const blob = () => new Blob(["{}"], { type: "application/json" });

describe("saveFileAs", () => {
  afterEach(() => {
    delete window.pixlstashDesktop;
    delete window.showSaveFilePicker;
    vi.restoreAllMocks();
  });

  it("writes through the desktop Save dialog", async () => {
    const completeMediaSaveAs = vi.fn(async () => ({ saved: true }));
    window.pixlstashDesktop = {
      beginMediaSaveAs: vi.fn(async () => ({ canceled: false, saveId: "s1" })),
      completeMediaSaveAs,
    };
    window.showSaveFilePicker = vi.fn();
    expect(await saveFileAs(blob(), "w.json")).toBe(true);
    expect(window.pixlstashDesktop.beginMediaSaveAs).toHaveBeenCalledWith(
      "w.json",
    );
    expect(completeMediaSaveAs).toHaveBeenCalledWith("s1", expect.anything());
    expect(window.showSaveFilePicker).not.toHaveBeenCalled();
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

  it("uses the browser picker, and a cancel there is not an error", async () => {
    const write = vi.fn();
    const close = vi.fn();
    window.showSaveFilePicker = vi.fn(async () => ({
      createWritable: async () => ({ write, close }),
    }));
    expect(await saveFileAs(blob(), "w.json")).toBe(true);
    expect(window.showSaveFilePicker).toHaveBeenCalledWith({
      suggestedName: "w.json",
    });
    expect(close).toHaveBeenCalled();

    window.showSaveFilePicker = vi.fn(async () => {
      throw new DOMException("cancelled", "AbortError");
    });
    expect(await saveFileAs(blob(), "w.json")).toBe(false);
  });
});
