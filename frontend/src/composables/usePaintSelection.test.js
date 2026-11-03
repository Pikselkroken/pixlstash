// The Edit tab's selection: the JSON its gestures make, which the server
// renders and a saved edit would repeat.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { effectScope, nextTick, ref } from "vue";

const renderPictureMask = vi.fn();
vi.mock("../api/pictures", () => ({
  renderPictureMask: (...a) => renderPictureMask(...a),
}));

import { extendSidesAt, usePaintSelection } from "./usePaintSelection";

let scope;
const pictureId = ref(7);
const dims = ref({ naturalWidth: 2000, naturalHeight: 1000 });

function make() {
  scope = effectScope();
  return scope.run(() => usePaintSelection({ pictureId, dims }));
}

beforeEach(() => {
  vi.useFakeTimers();
  pictureId.value = 7;
  dims.value = { naturalWidth: 2000, naturalHeight: 1000 };
  renderPictureMask.mockReset().mockResolvedValue(new Blob(["png"]));
  globalThis.URL.createObjectURL = vi.fn(() => "blob:mask");
  globalThis.URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  scope?.stop();
  vi.useRealTimers();
});

describe("usePaintSelection", () => {
  it("turns a drag into a box step in fractions, with the bar's mode", () => {
    const paint = make();
    paint.setTool("rect");
    paint.begin({ x: 0.6, y: 0.5 });
    paint.move({ x: 0.2, y: 0.1 });
    paint.end();
    expect(paint.selection.value.steps).toEqual([
      { tool: "rect", mode: "new", box: [0.2, 0.1, 0.4, 0.4] },
    ]);
  });

  it("lets Shift add and Alt take away, whatever the bar says", () => {
    const paint = make();
    paint.setTool("wand");
    paint.setMode("intersect");
    paint.begin({ x: 0.5, y: 0.5 }, { shift: true });
    paint.begin({ x: 0.1, y: 0.1 }, { alt: true });
    paint.begin({ x: 0.9, y: 0.9 });
    expect(paint.steps.value.map((step) => step.mode)).toEqual([
      "add",
      "subtract",
      "intersect",
    ]);
    expect(paint.steps.value[0]).toMatchObject({ point: [0.5, 0.5], tolerance: 32 });
  });

  it("retunes the wand's tolerance by dragging on from the click, as GIMP does", () => {
    const paint = make();
    paint.setTool("wand");
    paint.begin({ x: 0.5, y: 0.5 });
    paint.move({ x: 0.7, y: 0.5 });
    expect(paint.steps.value).toHaveLength(1);
    expect(paint.steps.value[0].tolerance).toBe(32 + 51);
    paint.move({ x: 0.3, y: 0.3 });
    paint.end();
    // Left and up take away, never below zero; the next click starts there.
    expect(paint.steps.value[0].tolerance).toBe(0);
    expect(paint.tolerance.value).toBe(0);
    expect(paint.draft.value).toBeNull();
  });

  it("sizes a brush stroke by the picture's shorter side; the eraser subtracts", () => {
    const paint = make();
    paint.setTool("brush");
    paint.setBrushSize(50);
    paint.begin({ x: 0.1, y: 0.1 });
    paint.move({ x: 0.2, y: 0.1 });
    paint.end();
    paint.setTool("eraser");
    paint.begin({ x: 0.3, y: 0.3 });
    paint.end();
    const [brush, eraser] = paint.steps.value;
    // 50 px of a 1000 px shorter side.
    expect(brush).toEqual({
      tool: "brush",
      mode: "add",
      radius: 0.05,
      points: [
        [0.1, 0.1],
        [0.2, 0.1],
      ],
    });
    expect(eraser.mode).toBe("subtract");
  });

  it("draws nothing for a click, and keeps a crop frame off the selection", () => {
    const paint = make();
    paint.setTool("ellipse");
    paint.begin({ x: 0.5, y: 0.5 });
    paint.end();
    paint.setTool("crop");
    paint.begin({ x: 0.1, y: 0.1 });
    paint.move({ x: 0.5, y: 0.6 });
    paint.end();
    expect(paint.steps.value).toEqual([]);
    expect(paint.cropBox.value).toEqual([0.1, 0.1, 0.4, 0.5]);
  });

  it("leaves the black-and-white view on the next gesture and on Clear", () => {
    const paint = make();
    paint.setTool("wand");
    paint.toggleMask();
    expect(paint.showMask.value).toBe(true);
    paint.begin({ x: 0.5, y: 0.5 });
    expect(paint.showMask.value).toBe(false);
    paint.toggleMask();
    paint.clear();
    expect(paint.showMask.value).toBe(false);
  });

  it("redoes a step taken back until a new step is drawn", () => {
    const paint = make();
    paint.setTool("wand");
    paint.begin({ x: 0.5, y: 0.5 });
    paint.end();
    paint.begin({ x: 0.1, y: 0.1 });
    paint.end();
    const [, second] = paint.steps.value;
    paint.undo();
    expect(paint.canRedo.value).toBe(true);
    paint.redo();
    expect(paint.steps.value.at(-1)).toEqual(second);
    expect(paint.canRedo.value).toBe(false);
    paint.undo();
    paint.begin({ x: 0.9, y: 0.9 });
    paint.end();
    expect(paint.canRedo.value).toBe(false);
  });

  it("undoes, inverts, and starts afresh on another picture", async () => {
    const paint = make();
    paint.setTool("wand");
    paint.begin({ x: 0.5, y: 0.5 });
    paint.invert();
    expect(paint.steps.value.at(-1)).toEqual({ tool: "invert" });
    paint.undo();
    expect(paint.steps.value).toHaveLength(1);
    pictureId.value = 8;
    await nextTick();
    expect(paint.hasSelection.value).toBe(false);
  });

  it("starts grow, feather and fill holes from the picture's size until one is set", async () => {
    const paint = make();
    // 1% / 1% / 2% of the shorter side, 1000px here.
    expect({ ...paint.refine }).toEqual({ grow: 10, feather: 10, fill_holes: 20 });
    dims.value = { naturalWidth: 300, naturalHeight: 200 };
    await nextTick();
    // A small picture gets the floors, not a 2px seam.
    expect({ ...paint.refine }).toEqual({ grow: 4, feather: 4, fill_holes: 8 });
    paint.setRefine("feather", 30);
    dims.value = { naturalWidth: 20000, naturalHeight: 16000 };
    await nextTick();
    // The owner's values stay theirs on the next picture.
    expect({ ...paint.refine }).toEqual({ grow: 4, feather: 30, fill_holes: 8 });
  });

  it("asks the server for the mask a moment after the last change", async () => {
    const paint = make();
    paint.setTool("wand");
    paint.begin({ x: 0.5, y: 0.5 });
    paint.setRefine("grow", 6);
    await nextTick();
    expect(renderPictureMask).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(200);
    expect(renderPictureMask).toHaveBeenCalledTimes(1);
    const [id, body] = renderPictureMask.mock.calls[0];
    expect(id).toBe(7);
    expect(body).toMatchObject({ version: 1, refine: { grow: 6, feather: 10, fill_holes: 20 } });
    expect(paint.maskUrl.value).toBe("blob:mask");
    // A failed render shows no mask rather than the last selection's.
    renderPictureMask.mockRejectedValueOnce(new Error("offline"));
    paint.setRefine("grow", 8);
    await vi.advanceTimersByTimeAsync(200);
    expect(paint.maskError.value).toBe("Could not show the selection.");
    expect(paint.maskUrl.value).toBe("");
  });

  describe("Extend", () => {
    it("stays extending after the tool is put down, until it is cleared", () => {
      // The Edit tab keeps its outpaint card, its prompt help and the run's
      // `extend` on this: putting the tool down must not drop them.
      const paint = make();
      paint.setTool("extend");
      paint.setExtension("bottom", 64);
      paint.setTool(null);
      expect(paint.tool.value).toBe(null);
      expect(paint.extending.value).toBe(true);
      paint.clearExtension();
      expect(paint.extending.value).toBe(false);
    });

    it("drags an edge out in the picture's pixels, snapped to 8", () => {
      const paint = make();
      paint.setTool("extend");
      // The right edge of a 2000 x 1000 picture, dragged 0.1 (200 px) out.
      paint.begin({ x: 1, y: 0.5 }, { tolerance: { x: 0.01, y: 0.01 } });
      paint.move({ x: 1.1003, y: 0.7 });
      paint.end();
      expect({ ...paint.extension }).toEqual({
        left: 0,
        top: 0,
        right: 200,
        bottom: 0,
        feather: 40,
      });
      paint.setExtension("left", 13);
      expect(paint.extension.left).toBe(16);
      // Up, as the server rounds: a typed 3 is 8, not 0.
      paint.setExtension("left", 3);
      expect(paint.extension.left).toBe(8);
      paint.setExtension("left", 13);
      expect(paint.extend.value).toEqual({ ...paint.extension });
      expect(paint.extendedSize.value).toEqual({ width: 2216, height: 1000 });
    });

    it("a corner takes two sides, and a press off the frame takes none", () => {
      const paint = make();
      paint.setTool("extend");
      paint.begin({ x: -0.004, y: 1.004 }, { tolerance: { x: 0.01, y: 0.01 } });
      paint.move({ x: -0.104, y: 1.204 });
      paint.end();
      expect(paint.extension.left).toBe(200);
      expect(paint.extension.bottom).toBe(200);
      paint.begin({ x: 0.5, y: 0.5 }, { tolerance: { x: 0.01, y: 0.01 } });
      expect(paint.draft.value).toBeNull();
    });

    it("holds each side to the picture's own size on the 8 px grid", () => {
      dims.value = { naturalWidth: 1003, naturalHeight: 600 };
      const paint = make();
      paint.setExtension("left", 700);
      paint.setExtension("right", 1500);
      // Each side on its own: left keeps its 700 beside a full right.
      expect(paint.extension.left).toBe(704);
      // 1003 rounds down to 1000: the server rounds up, so 1008 would be refused.
      expect(paint.extension.right).toBe(1000);
      paint.setExtension("bottom", 9999);
      expect(paint.extension.bottom).toBe(600);
      paint.setExtension("top", -50);
      expect(paint.extension.top).toBe(0);
      paint.setExtendFeather(500);
      expect(paint.extension.feather).toBe(200);
    });

    it("is never held with a selection: each drops the other", () => {
      const paint = make();
      paint.setTool("rect");
      paint.begin({ x: 0.1, y: 0.1 });
      paint.move({ x: 0.5, y: 0.5 });
      paint.end();
      expect(paint.hasSelection.value).toBe(true);
      paint.setTool("extend");
      expect(paint.hasSelection.value).toBe(false);
      paint.setExtension("top", 64);
      expect(paint.hasExtension.value).toBe(true);
      paint.setTool("rect");
      paint.begin({ x: 0.1, y: 0.1 });
      paint.move({ x: 0.5, y: 0.5 });
      paint.end();
      expect(paint.hasSelection.value).toBe(true);
      expect(paint.hasExtension.value).toBe(false);
      expect(paint.extend.value).toBeNull();
    });

    it("Clear and another picture drop the extension; the feather stays", async () => {
      const paint = make();
      paint.setExtension("bottom", 80);
      paint.setExtendFeather(12);
      paint.clear();
      expect(paint.hasExtension.value).toBe(false);
      expect(paint.extension.feather).toBe(12);
      paint.setExtension("bottom", 80);
      pictureId.value = 8;
      await nextTick();
      expect(paint.hasExtension.value).toBe(false);
    });

    it("finds the sides under a press against the extended frame", () => {
      const ext = { left: 100, top: 0, right: 0, bottom: 50 };
      const natural = { width: 1000, height: 500 };
      const tol = { x: 0.01, y: 0.02 };
      expect(extendSidesAt({ x: -0.1, y: 0.5 }, ext, natural, tol)).toEqual(["left"]);
      expect(extendSidesAt({ x: 1.005, y: 0.5 }, ext, natural, tol)).toEqual(["right"]);
      expect(extendSidesAt({ x: 0.5, y: 0 }, ext, natural, tol)).toEqual(["top"]);
      expect(extendSidesAt({ x: 0.5, y: 1.1 }, ext, natural, tol)).toEqual(["bottom"]);
      expect(extendSidesAt({ x: 1, y: 1.1 }, ext, natural, tol)).toEqual([
        "right",
        "bottom",
      ]);
      // On the picture's own left edge, which is no longer the frame's.
      expect(extendSidesAt({ x: 0, y: 0.5 }, ext, natural, tol)).toEqual([]);
      // Past the frame's end along the edge's line.
      expect(extendSidesAt({ x: -0.1, y: 1.5 }, ext, natural, tol)).toEqual([]);
    });
  });
});
