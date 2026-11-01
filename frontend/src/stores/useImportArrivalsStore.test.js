import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { useImportArrivalsStore } from "./useImportArrivalsStore";

function file(name, size = 10) {
  return new File([new Uint8Array(size)], name);
}

beforeEach(() => {
  setActivePinia(createPinia());
  URL.createObjectURL = vi.fn((f) => `blob:${f.name}`);
  URL.revokeObjectURL = vi.fn();
});

describe("useImportArrivalsStore (#2512)", () => {
  it("draws a tile per picture on the drop, keyed for the upload", () => {
    const store = useImportArrivalsStore();
    store.beginReading([file("cover.psd")]);
    const keys = store.begin(
      "r1",
      [file("a.png"), file("a.txt"), file("clip.mp4")],
      { bytesTotal: 30 },
    );

    expect(keys).toHaveLength(3);
    expect(new Set(keys).size).toBe(3);
    const run = store.runs[0];
    // The caption sidecar is streamed but is not a picture; the refused file
    // is a tile but is not part of what is being imported.
    expect(run.tiles.map((t) => [t.name, t.state])).toEqual([
      ["a.png", "uploading"],
      ["clip.mp4", "uploading"],
      ["cover.psd", "unsupported"],
    ]);
    expect(run.total).toBe(2);
    expect(run.counts.unsupported).toBe(1);
    expect(run.tiles[0].previewUrl).toBe("blob:a.png");
    // A video cannot be drawn by an <img>: it gets the named tile.
    expect(run.tiles[1].previewUrl).toBeNull();
    expect(store.reading.active).toBe(false);
  });

  it("marks each tile by its own outcome, and a zip by its best one", () => {
    const store = useImportArrivalsStore();
    const [a, b, c, zip] = store.begin("r1", [
      file("a.png"),
      file("b.png"),
      file("c.png"),
      file("pack.zip"),
    ]);
    store.markChecking("r1");
    const run = store.runs[0];
    expect(run.tiles.every((t) => t.state === "checking")).toBe(true);

    store.applyOutcomes("r1", [
      { key: a, outcome: "new", picture_id: null },
      { key: b, outcome: "duplicate", picture_id: 7 },
      { key: zip, outcome: "new", picture_id: null },
      { key: zip, outcome: "duplicate", picture_id: 8 },
    ]);
    store.applyOutcomes("r1", [
      { key: c, outcome: "scrapheaped", picture_id: 9 },
    ]);

    const byName = Object.fromEntries(run.tiles.map((t) => [t.name, t]));
    expect(byName["a.png"].state).toBe("new");
    expect(byName["b.png"]).toMatchObject({ state: "duplicate", pictureId: 7 });
    expect(byName["c.png"]).toMatchObject({
      state: "scrapheaped",
      pictureId: 9,
    });
    expect(byName["pack.zip"].state).toBe("new");
    expect(run.counts).toEqual({
      added: 2,
      duplicate: 2,
      scrapheaped: 1,
      unsupported: 0,
      failed: 0,
    });

    // A file the server could not read is not "not supported".
    store.applyOutcomes("r1", [{ key: a, outcome: "failed", picture_id: null }]);
    expect(run.counts.failed).toBe(1);
    expect(run.counts.unsupported).toBe(0);

    // A restored Scrapheap match moves between the receipt's counts.
    store.markRestored("r1", c);
    expect(byName["c.png"].state).toBe("duplicate");
    expect(run.counts.scrapheaped).toBe(0);
    expect(run.counts.duplicate).toBe(3);

    store.land("r1");
    expect(byName["a.png"].state).toBe("landed");
    expect(byName["b.png"].state).toBe("duplicate");
    expect(run.stage).toBe("landed");
  });

  it("turns a file the server skipped at staging into a not-supported tile", () => {
    const store = useImportArrivalsStore();
    const files = [file("ok.png"), file("bad.png")];
    const keys = store.begin("r1", files);
    store.markSkipped("r1", keys, files, ["bad.png"]);
    const run = store.runs[0];
    expect(run.tiles.map((t) => t.state)).toEqual(["uploading", "unsupported"]);
    expect(run.total).toBe(1);
    expect(run.counts.unsupported).toBe(1);
  });

  it("keeps a running import on navigation and lets a receipt go", () => {
    const store = useImportArrivalsStore();
    store.begin("running", [file("a.png")]);
    store.begin("finished", [file("b.png")]);
    store.land("finished");

    store.dismissFinished();

    expect(store.runs.map((run) => run.id)).toEqual(["running"]);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:b.png");
  });
});
