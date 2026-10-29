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

vi.mock("../../api/characters", () => ({
  getCharacterName: vi.fn().mockResolvedValue({ name: "alice" }),
}));
vi.mock("../../api/snapshots", () => ({
  hashCompareSnapshot: vi.fn().mockResolvedValue({ identical_ids: [] }),
}));

import { isReadOnly } from "../../utils/apiClient";
import { hashCompareSnapshot } from "../../api/snapshots";
import { getCharacterName } from "../../api/characters";
import { useSnapshotsStore } from "../../stores/useSnapshotsStore";
import ImageGridContextMenu from "./ImageGridContextMenu.vue";
import { ROTATE_FORMAT_REASON } from "../../utils/rotate";
import { COMPARE_PICK_REASON } from "../../utils/compare";

beforeEach(() => {
  setActivePinia(createPinia());
  isReadOnly.value = false;
});

// The tooltip is stubbed to carry its text on the element it wraps or sits in:
// the real one renders nothing until it opens, and needs Vuetify besides.
const TooltipStub = {
  name: "Tooltip",
  props: ["text", "activator"],
  template:
    '<span class="tip" :data-text="text"><slot name="activator" :props="{}" /></span>',
};

/** The tooltip text a control carries, whether the tip sits inside or around it. */
function tip(wrapper) {
  const inner = wrapper.find(".tip");
  if (inner.exists()) return inner.attributes("data-text");
  return wrapper.element.parentElement?.dataset.text;
}

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
        Tooltip: TooltipStub,
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
// The rows live in a group's submenu, rendered only while it is open, the way
// a hover opens it.
async function openGroup(wrapper, group) {
  const wrap = wrapper.find(`[data-group="${group}"]`);
  if (wrap.exists()) await wrap.trigger("mouseenter");
}

async function rotateItems(wrapper) {
  await openGroup(wrapper, "adjust");
  const buttons = wrapper.findAll("button.ctx-item");
  const withIcon = (glyph) => buttons.find((b) => b.html().includes(glyph));
  return {
    left: withIcon("mdi-rotate-left"),
    right: withIcon("mdi-rotate-right"),
  };
}

describe("ImageGridContextMenu - rotate", () => {
  it("names the count over a multi-selection", async () => {
    const wrapper = mountMenu();
    const { left, right } = await rotateItems(wrapper);
    expect(left.text()).toContain("Rotate 3 photos left");
    expect(right.text()).toContain("Rotate 3 photos right");
  });

  it("drops the count for a single picture", async () => {
    const wrapper = mountMenu({ selectedImageIds: ["10"] });
    const { left, right } = await rotateItems(wrapper);
    // `toContain`, because the stubbed glyph rides in the same text node.
    expect(left.text()).toContain("Rotate left");
    expect(right.text()).toContain("Rotate right");
    expect(left.text()).not.toContain("photos");
  });

  it("emits the direction it is labelled with, and closes", async () => {
    const wrapper = mountMenu();
    const { left, right } = await rotateItems(wrapper);

    await left.trigger("click");
    await right.trigger("click");

    expect(wrapper.emitted("rotate-left")).toHaveLength(1);
    expect(wrapper.emitted("rotate-right")).toHaveLength(1);
    // The menu goes away on the action, like every other item here: it is the
    // parent's `visible` that closes it, so the event is what proves it asked.
    expect(wrapper.emitted("close")).toHaveLength(2);
  });

  it("stays live when only some of the selection can rotate", async () => {
    // The parent computes the reason and passes null while any picture can
    // turn; the menu must not add a gate of its own on top of it.
    const wrapper = mountMenu({ rotateBlockReason: null });
    const { left } = await rotateItems(wrapper);
    expect(left.attributes("disabled")).toBeUndefined();
  });

  it("greys with the reason when nothing selected can rotate", async () => {
    const wrapper = mountMenu({ rotateBlockReason: ROTATE_FORMAT_REASON });
    const { left, right } = await rotateItems(wrapper);

    expect(left.attributes("disabled")).toBeDefined();
    expect(right.attributes("disabled")).toBeDefined();
    // The tooltip carries the refusal rather than repeating the label, and
    // points at the route that still works.
    expect(tip(left)).toBe(ROTATE_FORMAT_REASON);
    expect(tip(left)).toContain("Filters > Rotate");
  });

  it("is greyed in a read-only session, which cannot rotate at all", async () => {
    // The endpoint is owner-only: a share or otherwise scoped token is refused
    // outright, so offering the action would advertise a 403.
    isReadOnly.value = true;
    const wrapper = mountMenu();
    const { left, right } = await rotateItems(wrapper);
    expect(left.attributes("disabled")).toBeDefined();
    expect(right.attributes("disabled")).toBeDefined();
  });

  it("is greyed with nothing selected", async () => {
    const wrapper = mountMenu({ selectedImageIds: [] });
    const { left } = await rotateItems(wrapper);
    expect(left.attributes("disabled")).toBeDefined();
  });
});

// Compare shares this file's menu environment: any two pictures, ComfyUI or not.
describe("ImageGridContextMenu - compare", () => {
  const compareItem = async (wrapper) => {
    await openGroup(wrapper, "find");
    return wrapper
      .findAll("button.ctx-item")
      .find((b) => b.html().includes("mdi-compare-horizontal"));
  };

  it("is offered without ComfyUI, greyed with its reason short of two", async () => {
    const item = await compareItem(
      mountMenu({ comfyuiConfigured: false, compareBlockReason: COMPARE_PICK_REASON }),
    );
    expect(item.attributes("disabled")).toBeDefined();
    expect(item.text()).toContain("Select two");
    expect(tip(item)).toBe(COMPARE_PICK_REASON);
  });

  it("asks the grid to compare two pictures", async () => {
    const wrapper = mountMenu({ comfyuiConfigured: false, compareBlockReason: null });
    const item = await compareItem(wrapper);
    expect(item.attributes("disabled")).toBeUndefined();
    await item.trigger("click");
    await new Promise((r) => setTimeout(r, 0));
    expect(wrapper.emitted("compare-pictures")).toHaveLength(1);
  });
});

// The grouping rules from the reorganised menu, in this file's environment.
describe("ImageGridContextMenu - groups", () => {
  const groups = (wrapper) =>
    wrapper.findAll("[data-group]").map((w) => w.attributes("data-group"));
  const taggers = [{ name: "wd14", display_name: "WD14 Tagger" }];

  it("folds the tools into the four groups and Restore, in a fixed order", () => {
    const wrapper = mountMenu({ comfyuiConfigured: true, taggerPlugins: taggers });
    expect(groups(wrapper)).toEqual([
      "analyse",
      "generate",
      "adjust",
      "restore",
      "find",
    ]);
  });

  it("drops Generate without ComfyUI, as its rows always were", () => {
    const wrapper = mountMenu({ taggerPlugins: taggers });
    expect(groups(wrapper)).toEqual(["analyse", "adjust", "restore", "find"]);
  });

  it("draws Analyse as the bare Segment row when it has no models to list", () => {
    const wrapper = mountMenu();
    expect(groups(wrapper)).not.toContain("analyse");
    const segment = wrapper
      .findAll("button.ctx-item")
      .find((b) => b.html().includes("mdi-shape-outline"));
    expect(segment.element.closest(".ctx-submenu")).toBeNull();
  });

  it("lists the models under a heading inside Analyse, not a third level", async () => {
    const wrapper = mountMenu({ taggerPlugins: taggers });
    await wrapper.find('[data-group="analyse"]').trigger("mouseenter");
    const sub = wrapper.find('[data-group="analyse"] .ctx-submenu');
    expect(sub.find(".ctx-label").text()).toBe("Tag automatically");
    expect(sub.text()).toContain("WD14 Tagger");
    expect(sub.find(".ctx-submenu").exists()).toBe(false);
  });

  it("opens a group on click too, so Enter on a focused group row works", async () => {
    const wrapper = mountMenu();
    const trigger = wrapper.find('[data-group="adjust"] > .ctx-item');
    expect(trigger.attributes("aria-expanded")).toBe("false");
    await trigger.trigger("click");
    expect(wrapper.find('[data-group="adjust"] .ctx-submenu').exists()).toBe(true);
    expect(
      wrapper.find('[data-group="adjust"] > .ctx-item').attributes("aria-expanded"),
    ).toBe("true");
  });

  it("keeps the snapshots out of Adjust, in their own submenu", async () => {
    const wrapper = mountMenu();
    useSnapshotsStore().snapshots = [{ id: 5, is_compatible: true, kind: "MANUAL" }];
    await wrapper.find('[data-group="adjust"]').trigger("mouseenter");
    expect(wrapper.find('[data-group="adjust"] .ctx-submenu').text()).not.toContain(
      "MANUAL",
    );
    await wrapper.find('[data-group="restore"]').trigger("mouseenter");
    expect(wrapper.find('[data-group="restore"] .ctx-submenu').text()).toContain(
      "MANUAL",
    );
  });

  it("compares snapshots on opening Restore, which a read-only session lacks", async () => {
    const snapshot = { id: 5, is_compatible: true, kind: "MANUAL" };
    hashCompareSnapshot.mockClear();
    let wrapper = mountMenu();
    useSnapshotsStore().snapshots = [snapshot];
    await wrapper.find('[data-group="restore"]').trigger("mouseenter");
    expect(hashCompareSnapshot).toHaveBeenCalledTimes(1);
    wrapper.unmount();

    hashCompareSnapshot.mockClear();
    isReadOnly.value = true;
    wrapper = mountMenu();
    useSnapshotsStore().snapshots = [snapshot];
    expect(wrapper.find('[data-group="restore"]').exists()).toBe(false);
    await wrapper.find('[data-group="adjust"]').trigger("mouseenter");
    expect(hashCompareSnapshot).not.toHaveBeenCalled();
  });

  it("loads face names on opening Find & compare over one picture only", async () => {
    const contextImage = {
      id: 10,
      faces: [
        { id: 1, frame_index: 0, character_id: 3 },
        { id: 2, frame_index: 0, character_id: 4 },
      ],
    };
    getCharacterName.mockClear();
    let wrapper = mountMenu({ contextImage });
    await wrapper.find('[data-group="find"]').trigger("mouseenter");
    expect(getCharacterName).not.toHaveBeenCalled();
    wrapper.unmount();

    wrapper = mountMenu({ contextImage, selectedImageIds: ["10"] });
    await wrapper.find('[data-group="find"]').trigger("mouseenter");
    expect(getCharacterName).toHaveBeenCalledTimes(2);
  });
});
