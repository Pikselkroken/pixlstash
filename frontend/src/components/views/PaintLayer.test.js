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
});
