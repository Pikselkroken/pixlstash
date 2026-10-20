// The Edit tab's selection: the JSON its gestures make, which the server
// renders and a saved edit would repeat.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { effectScope, nextTick, ref } from "vue";

const renderPictureMask = vi.fn();
vi.mock("../api/pictures", () => ({
  renderPictureMask: (...a) => renderPictureMask(...a),
}));

import { usePaintSelection } from "./usePaintSelection";

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
    expect(body).toMatchObject({ version: 1, refine: { grow: 6, feather: 0, fill_holes: 0 } });
    expect(paint.maskUrl.value).toBe("blob:mask");
  });
});
