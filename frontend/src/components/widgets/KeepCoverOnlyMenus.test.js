// The Keep-cover-only menu item, in both places it appears: the grid's context
// menu and the selection pill's overflow. The two are separate components with
// separate templates, so the same rules are asserted against both: a rule
// enforced in one menu and forgotten in the other is the shape of bug this
// covers.
//
// What the rules are (docs/design/keep-cover-only.md):
//   * the label counts STACKS, because the action ignores loose pictures;
//   * partial eligibility is stated in the label, not discovered afterwards;
//   * it wears the shipped `.ctx-item--danger` and sits in the trailing danger
//     group, ordered by escalating severity before Delete;
//   * it is disabled with the lock reason only when EVERY named stack is
//     locked, because a locked set refuses the whole stack, never one member.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { mount } from "@vue/test-utils";

vi.mock("../../utils/apiClient", async () => {
  const { ref } = await import("vue");
  return {
    apiClient: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
    isReadOnly: ref(false), // a real ref so the menu template unwraps it
    onSessionReset: () => () => {},
  };
});

import { isReadOnly } from "../../utils/apiClient";
import ImageGridContextMenu from "./ImageGridContextMenu.vue";
import SelectionMenu from "../panels/SelectionMenu.vue";

const globalStubs = {
  global: {
    stubs: { "v-icon": true, teleport: true, AddToEntityControl: true },
  },
};

beforeEach(() => {
  setActivePinia(createPinia());
});

function mountContextMenu(props = {}) {
  return mount(ImageGridContextMenu, {
    props: {
      allPicturesId: "ALL",
      unassignedPicturesId: "UNASSIGNED",
      scrapheapPicturesId: "SCRAPHEAP",
      backendUrl: "http://x",
      visible: true,
      selectedCharacter: "ALL",
      selectedImageIds: ["10", "11", "12"],
      keepCoverOnlyStackCount: 3,
      ...props,
    },
    ...globalStubs,
  });
}

function mountSelectionMenu(props = {}) {
  return mount(SelectionMenu, {
    props: {
      open: true,
      backendUrl: "http://x",
      isReadOnly: false,
      isScrapheapView: false,
      selectedImageIds: ["10", "11", "12"],
      selectedCount: 3,
      keepCoverOnlyStackCount: 3,
      ...props,
    },
    ...globalStubs,
  });
}

/** The menu's Keep-cover-only button, or undefined when it is not offered. */
function keepCoverItem(wrapper) {
  return wrapper
    .findAll("button.ctx-item")
    .find((b) => b.text().startsWith("Keep cover only"));
}

const MENUS = [
  ["ImageGridContextMenu", mountContextMenu, "keep-cover-only"],
  ["SelectionMenu", mountSelectionMenu, "keep-cover-only"],
];

describe.each(MENUS)("%s: the Keep cover only item", (_name, mountMenu, event) => {
  it("counts stacks in its label, not the tiles that were clicked", () => {
    const item = keepCoverItem(mountMenu());
    expect(item.text()).toContain("Keep cover only (3 stacks)");
  });

  // Ignoring loose pictures is only honest if the label says how many of the
  // selection the action will actually touch.
  it("reports partial eligibility when the selection is mixed", () => {
    const item = keepCoverItem(
      mountMenu({
        selectedImageIds: Array.from({ length: 20 }, (_, i) => String(i)),
        selectedCount: 20,
        keepCoverOnlyStackCount: 12,
      }),
    );
    expect(item.text()).toContain("Keep cover only (12 of 20)");
  });

  it("is not offered when the selection names no stack", () => {
    expect(keepCoverItem(mountMenu({ keepCoverOnlyStackCount: 0 }))).toBeUndefined();
  });

  it("is not offered in the Scrapheap view", () => {
    const wrapper = mountMenu({
      isScrapheapView: true,
      selectedCharacter: "SCRAPHEAP",
    });
    expect(keepCoverItem(wrapper)).toBeUndefined();
  });

  it("wears the shipped danger treatment", () => {
    expect(keepCoverItem(mountMenu()).classes()).toContain("ctx-item--danger");
  });

  // Escalating severity: Keep cover only (recoverable, stacks only) before
  // Delete (the whole selection to the Scrapheap).
  it("sits before Delete in the trailing danger group", () => {
    const danger = mountMenu()
      .findAll("button.ctx-item--danger")
      .map((b) => b.text());
    const keep = danger.findIndex((t) => t.startsWith("Keep cover only"));
    const del = danger.findIndex((t) => t.includes("Delete"));
    expect(keep).toBeGreaterThanOrEqual(0);
    expect(del).toBeGreaterThan(keep);
  });

  it("emits its intent when clicked", async () => {
    const wrapper = mountMenu();
    await keepCoverItem(wrapper).trigger("click");
    expect(wrapper.emitted(event)).toHaveLength(1);
  });

  // A locked set refuses the WHOLE stack, so a selection in which every stack
  // is locked provably cannot do anything: say why instead of failing quietly.
  it("is disabled with the lock reason when every named stack is locked", () => {
    const reason = "Locked: every selected stack is held by the locked set 'X'.";
    const item = keepCoverItem(mountMenu({ keepCoverOnlyLockReason: reason }));
    expect(item.attributes("disabled")).toBeDefined();
    expect(item.attributes("title")).toBe(reason);
  });

  // A mixed selection stays live and the dialog reports the skips, which is the
  // same rule the shipped Delete item follows.
  it("stays enabled when only some stacks are locked", () => {
    const item = keepCoverItem(mountMenu({ keepCoverOnlyLockReason: null }));
    expect(item.attributes("disabled")).toBeUndefined();
  });

});

// Read-only is read from two different places by the two menus (the grid menu
// takes the session ref straight from apiClient; the pill's menu is handed a
// prop by SelectionBar), so it is asserted once per menu rather than shared.
describe("Keep cover only in a read-only session", () => {
  it("is disabled in the grid's context menu", async () => {
    isReadOnly.value = true;
    try {
      expect(keepCoverItem(mountContextMenu()).attributes("disabled")).toBeDefined();
    } finally {
      isReadOnly.value = false;
    }
  });

  it("is disabled in the selection pill's overflow", () => {
    expect(
      keepCoverItem(mountSelectionMenu({ isReadOnly: true })).attributes(
        "disabled",
      ),
    ).toBeDefined();
  });
});

describe("the selection pill keeps it in the overflow only", () => {
  // A floating pill over a photo grid is the wrong place for an error-filled
  // control, and this is periodic cleanup rather than a high-frequency verb.
  it("offers no top-level Keep cover only button", () => {
    const wrapper = mountSelectionMenu();
    const items = wrapper.findAll("button.ctx-item");
    expect(
      items.filter((b) => b.text().startsWith("Keep cover only")),
    ).toHaveLength(1);
  });
});
