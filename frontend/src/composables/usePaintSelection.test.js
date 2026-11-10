// The Edit tab's selection: the JSON its gestures make, which the server
// renders and a saved edit would repeat.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { effectScope, nextTick, ref } from "vue";

const renderPictureMask = vi.fn();
vi.mock("../api/pictures", () => ({
  renderPictureMask: (...a) => renderPictureMask(...a),
}));

import { maskBounds, usePaintSelection } from "./usePaintSelection";

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

});

// Crop taken up over a selection starts its frame around it. The mask is read
// back through an <img> and a canvas, which jsdom has neither of: both are
// stood in for. Each rendered mask URL holds the `mask.data` (4 x 2 pixels)
// current when the server answered, and the canvas reads back the one drawn.
describe("usePaintSelection - crop over a selection", () => {
  const mask = { data: null };
  let decode;
  let getContext;
  let urls;

  /** A 4 x 2 mask with the given [x, y] pixels selected. */
  function maskOf(pixels) {
    const data = new Uint8ClampedArray(4 * 2 * 4);
    for (const [x, y] of pixels) data[(y * 4 + x) * 4] = 255;
    return data;
  }

  function markRect(paint, from, to) {
    paint.setTool("rect");
    paint.begin(from);
    paint.move(to);
    paint.end();
  }

  beforeEach(() => {
    // Each render its own URL, as the browser's are.
    urls = 0;
    const rendered = new Map();
    globalThis.URL.createObjectURL = vi.fn(() => {
      const url = `blob:mask-${++urls}`;
      rendered.set(url, mask.data);
      return url;
    });
    let drawn = null;
    mask.data = maskOf([
      [2, 1],
      [3, 1],
    ]);
    decode = vi.fn(() => Promise.resolve());
    vi.stubGlobal(
      "Image",
      class {
        naturalWidth = 4;
        naturalHeight = 2;
        decode = decode;
      },
    );
    getContext = vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
      drawImage(image) {
        drawn = rendered.get(image.src);
      },
      getImageData: () => ({ data: drawn }),
    });
  });

  afterEach(() => {
    getContext.mockRestore();
    vi.unstubAllGlobals();
  });

  it("takes the frame from the selection's bounds, and leaves one already set", async () => {
    const paint = make();
    // No selection: Crop starts with no frame, as it always did.
    paint.setTool("crop");
    await vi.advanceTimersByTimeAsync(200);
    expect(paint.cropBox.value).toBe(null);

    markRect(paint, { x: 0.5, y: 0.5 }, { x: 1, y: 1 });
    paint.setTool("crop");
    // Before the mask lands there is nothing to read; once it does, the frame follows.
    expect(paint.cropBox.value).toBe(null);
    await vi.advanceTimersByTimeAsync(200);
    expect(decode).toHaveBeenCalled();
    expect(paint.cropBox.value).toEqual([0.5, 0.5, 0.5, 0.5]);

    // A frame the owner set is theirs: taking Crop up again keeps it.
    paint.setCropBox([0.1, 0.1, 0.2, 0.2]);
    paint.setTool(null);
    await nextTick();
    paint.setTool("crop");
    await vi.advanceTimersByTimeAsync(200);
    expect(paint.cropBox.value).toEqual([0.1, 0.1, 0.2, 0.2]);
  });

  it("does not bring back a frame cleared with Esc when the mask changes", async () => {
    const paint = make();
    markRect(paint, { x: 0.5, y: 0.5 }, { x: 1, y: 1 });
    await vi.advanceTimersByTimeAsync(200);
    paint.setTool("crop");
    await vi.advanceTimersByTimeAsync(200);
    expect(paint.cropBox.value).toEqual([0.5, 0.5, 0.5, 0.5]);
    // Esc, as the lightbox does it, then a refine slider: a new mask lands.
    paint.cropBox.value = null;
    paint.setRefine("grow", 9);
    await vi.advanceTimersByTimeAsync(200);
    expect(urls).toBe(2);
    expect(paint.cropBox.value).toBe(null);
  });

  it("starts from the selection as it stands, not the mask being replaced", async () => {
    const paint = make();
    markRect(paint, { x: 0, y: 0 }, { x: 0.5, y: 0.5 });
    mask.data = maskOf([[0, 0]]);
    await vi.advanceTimersByTimeAsync(200);
    // The selection moves on and Crop is taken up before its mask is back.
    markRect(paint, { x: 0.5, y: 0.5 }, { x: 1, y: 1 });
    mask.data = maskOf([
      [2, 1],
      [3, 1],
    ]);
    paint.setTool("crop");
    await vi.advanceTimersByTimeAsync(0);
    expect(paint.cropBox.value).toBe(null);
    await vi.advanceTimersByTimeAsync(200);
    expect(paint.cropBox.value).toEqual([0.5, 0.5, 0.5, 0.5]);
  });

  it("sets no frame when crop is put down while the mask is read", async () => {
    let finish;
    decode.mockImplementation(() => new Promise((resolve) => (finish = resolve)));
    const paint = make();
    markRect(paint, { x: 0.5, y: 0.5 }, { x: 1, y: 1 });
    await vi.advanceTimersByTimeAsync(200);
    paint.setTool("crop");
    await nextTick();
    expect(decode).toHaveBeenCalledTimes(1);
    paint.setTool(null);
    await nextTick();
    finish();
    await vi.advanceTimersByTimeAsync(0);
    expect(paint.cropBox.value).toBe(null);
  });
});

describe("maskBounds", () => {
  it("boxes the pixels at the ants' edge and up, in fractions", () => {
    const data = new Uint8ClampedArray(4 * 4 * 4);
    // (1, 1) is selected, (2, 2) is half-feathered in, (3, 3) is only a fringe.
    data[(1 * 4 + 1) * 4] = 255;
    data[(2 * 4 + 2) * 4] = 128;
    data[(3 * 4 + 3) * 4] = 127;
    expect(maskBounds(data, 4, 4)).toEqual([0.25, 0.25, 0.5, 0.5]);
    expect(maskBounds(new Uint8ClampedArray(16 * 4), 4, 4)).toBe(null);
  });
});
