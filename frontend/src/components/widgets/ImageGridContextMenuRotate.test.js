// The rotate items in the grid's context menu.
//
// Two rules carry the weight here, and both are about honesty rather than
// mechanics: the label has to say how much it is about to turn (a bare "Rotate
// left" over twelve tiles reads as an action on the one under the cursor), and
// the item stays LIVE over a mixed selection - the parent only greys it when
// nothing selected can be rotated at all, because refusing the whole selection
// over one WebP is a worse outcome than doing the work that can be done.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { mount } from "@vue/test-utils";

vi.mock("../../utils/apiClient", async () => {
  const { ref } = await import("vue");
  return {
    API_BASE_URL: "/api/v1",
    apiClient: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
    isReadOnly: ref(false), // a real ref so the menu template unwraps it
    onSessionReset: () => () => {},
    sessionContext: ref(null),
  };
});

import { isReadOnly } from "../../utils/apiClient";
import ImageGridContextMenu from "./ImageGridContextMenu.vue";
import { ROTATE_FORMAT_REASON } from "../../utils/rotate";

beforeEach(() => {
  setActivePinia(createPinia());
  isReadOnly.value = false;
});

function mountMenu(props = {}) {
  return mount(ImageGridContextMenu, {
    props: {
      scrapheapPicturesId: "SCRAPHEAP",
      backendUrl: "http://x",
      visible: true,
      selectedCharacter: "ALL",
      selectedImageIds: ["10", "11", "12"],
      ...props,
    },
    global: {
      stubs: {
        // Renders its slot, so the glyph name survives into the markup. The
        // default `true` stub drops it, and the glyph is the only handle on
        // these items that survives a greyed state (where the label is replaced
        // by the refusal).
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

describe("ImageGridContextMenu - rotate", () => {
  it("names the count over a multi-selection", () => {
    const wrapper = mountMenu();
    const { left, right } = rotateItems(wrapper);
    expect(left.text()).toContain("Rotate 3 photos left");
    expect(right.text()).toContain("Rotate 3 photos right");
  });

  it("drops the count for a single picture", () => {
    const wrapper = mountMenu({ selectedImageIds: ["10"] });
    const { left, right } = rotateItems(wrapper);
    // `toContain`, because the stubbed glyph rides in the same text node.
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
    // The menu goes away on the action, like every other item here: it is the
    // parent's `visible` that closes it, so the event is what proves it asked.
    expect(wrapper.emitted("close")).toHaveLength(2);
  });

  it("stays live when only some of the selection can rotate", () => {
    // The parent computes the reason and passes null while any picture can
    // turn; the menu must not add a gate of its own on top of it.
    const wrapper = mountMenu({ rotateBlockReason: null });
    const { left } = rotateItems(wrapper);
    expect(left.attributes("disabled")).toBeUndefined();
  });

  it("greys with the reason when nothing selected can rotate", () => {
    const wrapper = mountMenu({ rotateBlockReason: ROTATE_FORMAT_REASON });
    const { left, right } = rotateItems(wrapper);

    expect(left.attributes("disabled")).toBeDefined();
    expect(right.attributes("disabled")).toBeDefined();
    // The tooltip carries the refusal rather than repeating the label, and
    // points at the route that still works.
    expect(left.attributes("title")).toBe(ROTATE_FORMAT_REASON);
    expect(left.attributes("title")).toContain("Filters > Rotate");
  });

  it("is greyed in a read-only session, which cannot rotate at all", () => {
    // The endpoint is owner-only: a share or otherwise scoped token is refused
    // outright, so offering the action would advertise a 403.
    isReadOnly.value = true;
    const wrapper = mountMenu();
    const { left, right } = rotateItems(wrapper);
    expect(left.attributes("disabled")).toBeDefined();
    expect(right.attributes("disabled")).toBeDefined();
  });

  it("is greyed with nothing selected", () => {
    const wrapper = mountMenu({ selectedImageIds: [] });
    const { left } = rotateItems(wrapper);
    expect(left.attributes("disabled")).toBeDefined();
  });
});
