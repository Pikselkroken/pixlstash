// Which side the grid context menu's row tips open on. The menu stacks at the
// same z-index Vuetify gives a lone tip and won the tie, so its tips render
// inside it (`data-tooltip-layer`, see Tooltip.vue); the value is the side, and
// it must keep a tip off the hovered row and off any open sub-menu.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { mount, flushPromises } from "@vue/test-utils";

vi.mock("../../utils/apiClient", async () => {
  const { ref } = await import("vue");
  return {
    API_BASE_URL: "/api/v1",
    apiClient: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
    isReadOnly: ref(false),
    onSessionReset: () => () => {},
    sessionContext: ref(null),
  };
});

import ImageGridContextMenu from "./ImageGridContextMenu.vue";

beforeEach(() => {
  setActivePinia(createPinia());
  document.documentElement.style.setProperty("--tooltip-max-w", "280px");
});

afterEach(() => {
  document.documentElement.style.removeProperty("--tooltip-max-w");
});

async function sideAt(x) {
  const w = mount(ImageGridContextMenu, {
    props: {
      scrapheapPicturesId: "SCRAPHEAP",
      backendUrl: "http://x",
      visible: false,
      selectedCharacter: "ALL",
      selectedImageIds: ["10"],
      x,
      y: 100,
    },
    global: {
      stubs: { Tooltip: true, teleport: true, AddToEntityControl: true },
    },
  });
  await w.setProps({ visible: true });
  await flushPromises();
  const menu = w.find(".image-ctx-menu");
  const result = {
    side: menu.attributes("data-tooltip-layer"),
    flipped: menu.classes("ctx-flip-sub"),
  };
  w.unmount();
  return result;
}

describe("ImageGridContextMenu tooltip side", () => {
  it("opens tips on the left while sub-menus open right and a tip fits", async () => {
    expect(window.innerWidth).toBeGreaterThan(600 + 185 + 8);
    expect(await sideAt(600)).toEqual({ side: "start", flipped: false });
  });

  it("opens tips above the row when there is no room on the left", async () => {
    expect(await sideAt(100)).toEqual({ side: "top", flipped: false });
  });

  // Sub-menus flip left exactly when the right has no room, so the left is
  // the sub-menus' side and the right is too narrow: above is all that is left.
  it("opens tips above the row when sub-menus open left", async () => {
    expect(await sideAt(window.innerWidth - 50)).toEqual({
      side: "top",
      flipped: true,
    });
  });
});
