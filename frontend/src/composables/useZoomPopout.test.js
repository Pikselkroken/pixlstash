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

  // The thumbnails sit in scrolling lists: a plain wheel must scroll, never
  // open the zoom. Ctrl/Meta+wheel up (or a pinch) opens it.
  function wheel(deltaY, mods = {}) {
    const el = document.createElement("div");
    el.getBoundingClientRect = () => ({ left: 0, top: 0, width: 100, height: 100 });
    const event = new window.WheelEvent("wheel", {
      deltaY,
      clientX: 25,
      clientY: 50,
      cancelable: true,
      ...mods,
    });
    Object.defineProperty(event, "currentTarget", { value: el });
    return event;
  }

  it("a plain wheel scrolls and never opens the zoom", () => {
    const zoom = useZoomPopout(ref([1, 2]));
    for (const deltaY of [-100, 100]) {
      const event = wheel(deltaY);
      zoom.onTileWheel(event, 1);
      expect(event.defaultPrevented).toBe(false);
    }
    expect(zoom.zoomedId.value).toBe(null);
  });

  it("Ctrl or Meta + wheel up opens the zoom at the cursor", () => {
    for (const mods of [{ ctrlKey: true }, { metaKey: true }]) {
      const zoom = useZoomPopout(ref([1, 2]));
      const out = wheel(100, mods);
      zoom.onTileWheel(out, 1);
      expect(zoom.zoomedId.value).toBe(null);
      // The page itself never zooms under a thumbnail.
      expect(out.defaultPrevented).toBe(true);
      const event = wheel(-100, mods);
      zoom.onTileWheel(event, 2);
      expect(event.defaultPrevented).toBe(true);
      expect(zoom.zoomedId.value).toBe(2);
      expect(zoom.zoomEntry.value).toMatchObject({ deltaY: -100, fx: 0.25, fy: 0.5 });
    }
  });
});
