// The rotate items in the selection bar's overflow menu.
//
// Their sibling test is `widgets/ImageGridContextMenuRotate.test.js`, and the
// duplication is deliberate: #403 holds the two menus to the same action list
// for a multi-picture selection, and the pair shipped wired to the context menu
// alone. A guardrail that only exists in Playwright catches that at the end of
// a 200-runner-minute gate; these mount both menus in milliseconds.
//
// The rules are the context menu's rules, because they have to be: the label
// says how much it is about to turn, the item stays LIVE over a mixed selection
// (the parent greys it only when nothing selected can rotate at all), and a
// read-only session cannot rotate at all.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { mount } from "@vue/test-utils";

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

vi.mock("../../api/snapshots", () => ({
  hashCompareSnapshot: vi.fn(),
}));

import SelectionMenu from "./SelectionMenu.vue";
import { ROTATE_FORMAT_REASON } from "../../utils/rotate";

beforeEach(() => {
  setActivePinia(createPinia());
});

function mountMenu(props = {}) {
  return mount(SelectionMenu, {
    props: {
      open: true,
      selectedCount: 3,
      selectedImageIds: ["10", "11", "12"],
      isReadOnly: false,
      isScrapheapView: false,
      ...props,
    },
    global: {
      stubs: {
        // Renders its slot so the glyph name survives into the markup - the
        // only handle on these items once a greyed state replaces the label.
        "v-icon": { template: "<i><slot /></i>" },
        teleport: true,
        AddToEntityControl: true,
      },
    },
  });
}

/** The rotate items, found by their glyph so a label change cannot hide them. */
function rotateItems(wrapper) {
  const buttons = wrapper.findAll("button.ctx-item");
  const withIcon = (glyph) => buttons.find((b) => b.html().includes(glyph));
  return {
    left: withIcon("mdi-rotate-left"),
    right: withIcon("mdi-rotate-right"),
  };
}

describe("SelectionMenu - rotate", () => {
  it("offers the pair at all, which is the parity #403 asserts", () => {
    const { left, right } = rotateItems(mountMenu());
    expect(left).toBeDefined();
    expect(right).toBeDefined();
  });

  it("names the count over a multi-selection, word for word as the context menu does", () => {
    const { left, right } = rotateItems(mountMenu());
    expect(left.text()).toContain("Rotate 3 photos left");
    expect(right.text()).toContain("Rotate 3 photos right");
  });

  it("drops the count for a single picture", () => {
    const wrapper = mountMenu({ selectedCount: 1, selectedImageIds: ["10"] });
    const { left, right } = rotateItems(wrapper);
    expect(left.text()).toContain("Rotate left");
    expect(right.text()).toContain("Rotate right");
    expect(left.text()).not.toContain("photos");
  });

  it("emits the direction it is labelled with, and closes", async () => {
    const wrapper = mountMenu();
    const { left, right } = rotateItems(wrapper);

    await left.trigger("click");
    await right.trigger("click");

    expect(wrapper.emitted("rotate-left")).toHaveLength(1);
    expect(wrapper.emitted("rotate-right")).toHaveLength(1);
    expect(wrapper.emitted("close")).toHaveLength(2);
  });

  it("stays live when only some of the selection can rotate", () => {
    const { left } = rotateItems(mountMenu({ rotateBlockReason: null }));
    expect(left.attributes("disabled")).toBeUndefined();
  });

  it("greys with the reason when nothing selected can rotate", () => {
    const wrapper = mountMenu({ rotateBlockReason: ROTATE_FORMAT_REASON });
    const { left, right } = rotateItems(wrapper);

    expect(left.attributes("disabled")).toBeDefined();
    expect(right.attributes("disabled")).toBeDefined();
    expect(left.attributes("title")).toBe(ROTATE_FORMAT_REASON);
    expect(left.attributes("title")).toContain("Filters > Rotate");
  });

  it("is greyed in a read-only session, which cannot rotate at all", () => {
    const { left, right } = rotateItems(mountMenu({ isReadOnly: true }));
    expect(left.attributes("disabled")).toBeDefined();
    expect(right.attributes("disabled")).toBeDefined();
  });

  it("is greyed with nothing selected", () => {
    const wrapper = mountMenu({ selectedCount: 0, selectedImageIds: [] });
    const { left } = rotateItems(wrapper);
    expect(left.attributes("disabled")).toBeDefined();
  });

  it("is absent in the scrapheap, where the context menu omits it too", () => {
    const { left, right } = rotateItems(mountMenu({ isScrapheapView: true }));
    expect(left).toBeUndefined();
    expect(right).toBeUndefined();
  });
});
