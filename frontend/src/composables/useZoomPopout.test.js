import { describe, it, expect } from "vitest";
import { nextTick, ref } from "vue";
import { useZoomPopout } from "./useZoomPopout";

describe("useZoomPopout", () => {
  it("closes when a reload drops the zoomed picture from the list", async () => {
    const list = ref([1, 2, 3]);
    const zoom = useZoomPopout(list);
    zoom.openZoom(2);
    await nextTick();
    list.value = [1, 3];
    await nextTick();
    expect(zoom.zoomedId.value).toBe(null);
  });

  it("stays open when the zoomed picture survives the reload", async () => {
    const list = ref([1, 2, 3]);
    const zoom = useZoomPopout(list);
    zoom.openZoom(2);
    await nextTick();
    list.value = [2, 4];
    await nextTick();
    expect(zoom.zoomedId.value).toBe(2);
    expect(zoom.hasNext.value).toBe(true);
  });
});
