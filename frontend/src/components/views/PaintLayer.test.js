// The wash is rebuilt at display size: at once for a new mask, but only once
// a resize settles, never on every frame of one.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { reactive, ref } from "vue";
import PaintLayer from "./PaintLayer.vue";

function paintState() {
  return {
    draft: ref(null),
    cropBox: ref(null),
    maskUrl: ref(""),
    showMask: ref(false),
    brushSize: ref(10),
  };
}

describe("PaintLayer", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
      clearRect() {},
    });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("rebuilds once a resize settles, not on every step of it", async () => {
    const dims = reactive({ width: 400, height: 300, naturalWidth: 800 });
    const paint = paintState();
    const layer = mount(PaintLayer, { props: { paint, dims } });
    const canvas = layer.find("canvas").element;
    paint.maskUrl.value = "blob:mask";
    await layer.vm.$nextTick();
    expect(canvas.width).toBe(400);
    for (const width of [420, 440, 460]) {
      dims.width = width;
      await layer.vm.$nextTick();
      vi.advanceTimersByTime(50);
    }
    expect(canvas.width).toBe(400);
    vi.advanceTimersByTime(150);
    expect(canvas.width).toBe(460);
    layer.unmount();
  });

  it("draws Extend's frame round the picture, a handle on each side and corner", () => {
    const dims = reactive({ width: 400, height: 200, naturalWidth: 1000, naturalHeight: 500 });
    const paint = {
      ...paintState(),
      extending: ref(true),
      extension: reactive({ left: 100, top: 0, right: 300, bottom: 50, feather: 40 }),
    };
    const layer = mount(PaintLayer, { props: { paint, dims, scale: 0.5, hot: "left" } });
    const at = Object.fromEntries(
      layer.findAll(".paint-extend-handle").map((el) => [
        el.attributes("data-sides"),
        [parseFloat(el.element.style.left), parseFloat(el.element.style.top)],
      ]),
    );
    // -10% to 130% across, 0% to 110% down, in percent of the picture.
    expect(at).toEqual({
      "left,top": [-10, 0],
      top: [60, 0],
      "right,top": [130, 0],
      left: [-10, 55],
      right: [130, 55],
      "left,bottom": [-10, 110],
      bottom: [60, 110],
      "right,bottom": [130, 110],
    });
    const handle = layer.find('[data-sides="left"]');
    expect(handle.classes()).toContain("paint-extend-handle--hot");
    // 10 px on screen at half scale.
    expect(handle.element.style.width).toBe("20px");
    const frame = layer.find(".paint-extend-frame").element.style;
    expect([frame.left, frame.top, frame.width, frame.height]).toEqual([
      "-10%",
      "0%",
      "140%",
      "110%",
    ]);
    paint.extending.value = false;
    return layer.vm.$nextTick().then(() => {
      expect(layer.find(".paint-extend-frame").exists()).toBe(false);
      layer.unmount();
    });
  });
});
