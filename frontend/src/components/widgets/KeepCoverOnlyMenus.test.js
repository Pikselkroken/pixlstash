// The Edit stack ▸ submenu, in both menus that carry it: the grid's context
// menu and the selection pill's overflow. Both mount the same EditStackMenu, so
// these tests are about what each menu hands it and what comes back out; a menu
// that forgets to pass a prop or forward an event is the shape of bug covered.
//
// Keep cover only lives in the submenu now, and its rules still hold
// (docs/design/keep-cover-only.md):
//   * the label counts STACKS, because the action ignores loose pictures;
//   * partial eligibility is stated in the label, not discovered afterwards;
//   * it wears the shipped `.ctx-item--danger`;
//   * it is disabled with the lock reason only when EVERY named stack is
//     locked, because a locked set refuses the whole stack, never one member.

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
    // Both menus read `useEntityListsStore.canSeeProjects` to decide whether to
    // render the Project row at all, and that getter derives from the session's
    // scope. An owner session (no share token, no resource scope) is the
    // baseline every assertion in this file was written against.
    sessionContext: ref(null),
  };
});

import { isReadOnly } from "../../utils/apiClient";
import ImageGridContextMenu from "./ImageGridContextMenu.vue";
import SelectionMenu from "../panels/SelectionMenu.vue";

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

const globalStubs = {
  global: {
    stubs: {
      "v-icon": true,
      Tooltip: TooltipStub,
      teleport: true,
      AddToEntityControl: true,
    },
  },
};

const SEVERAL_STACKS = { kind: "stacks", stacks: 3 };
const SECOND_OF_FOUR = { kind: "member", count: 4, position: 1 };

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
      stackEdit: SEVERAL_STACKS,
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
      stackEdit: SEVERAL_STACKS,
      keepCoverOnlyStackCount: 3,
      ...props,
    },
    ...globalStubs,
  });
}

/** The Edit stack row's wrapper, or undefined when the menu has none. */
function editStackWrap(wrapper) {
  return wrapper
    .findAll(".ctx-submenu-wrap")
    .find((w) => w.find("button.ctx-item").text().startsWith("Edit stack"));
}

/** Open the Edit stack submenu and answer its rows. */
async function submenuItems(wrapper) {
  const wrap = editStackWrap(wrapper);
  await wrap.trigger("mouseenter");
  return wrap.findAll(".ctx-submenu button.ctx-item");
}

async function submenuItem(wrapper, label) {
  return (await submenuItems(wrapper)).find((b) => b.text().startsWith(label));
}

const MENUS = [
  ["ImageGridContextMenu", mountContextMenu],
  ["SelectionMenu", mountSelectionMenu],
];

describe.each(MENUS)("%s: the Edit stack row", (_name, mountMenu) => {
  it("is not offered when the selection names no stack", () => {
    expect(editStackWrap(mountMenu({ stackEdit: null }))).toBeUndefined();
  });

  it("is not offered in the Scrapheap view", () => {
    const wrapper = mountMenu({
      isScrapheapView: true,
      selectedCharacter: "SCRAPHEAP",
    });
    expect(editStackWrap(wrapper)).toBeUndefined();
  });

  // The three rows it replaces must not survive at the top level.
  it("replaces the top-level Unstack and Keep rows", () => {
    const topLevel = mountMenu()
      .findAll("button.ctx-item")
      .filter((b) => !b.element.closest(".ctx-submenu"))
      .map((b) => b.text());
    expect(topLevel.some((t) => t.startsWith("Unstack"))).toBe(false);
    expect(topLevel.some((t) => t.startsWith("Keep"))).toBe(false);
    expect(topLevel.some((t) => t.startsWith("Edit stack"))).toBe(true);
  });

  it("says where the picture sits, and offers the order first", async () => {
    const wrapper = mountMenu({
      selectedImageIds: ["11"],
      selectedCount: 1,
      stackEdit: SECOND_OF_FOUR,
      keepCoverOnlyStackCount: 1,
    });
    const labels = (await submenuItems(wrapper)).map((b) => b.text());
    const head = editStackWrap(wrapper).find(".edit-stack-head").text();
    expect(head).toContain("Stack of 4");
    expect(head).toContain("this is 2nd");
    expect(labels.slice(0, 3).map((t) => t.split(/Ctrl|⌘/)[0])).toEqual([
      "Make cover",
      "Move earlier",
      "Move later",
    ]);
    expect(labels.some((t) => t.startsWith("Take out of stack"))).toBe(true);
    expect(labels).toContain("Unstack all 4");
  });

  it.each([
    ["Make cover", "cover"],
    ["Move earlier", "earlier"],
    ["Move later", "later"],
  ])("%s asks for the %s move", async (label, where) => {
    const wrapper = mountMenu({
      selectedImageIds: ["11"],
      selectedCount: 1,
      stackEdit: SECOND_OF_FOUR,
    });
    await (await submenuItem(wrapper, label)).trigger("click");
    expect(wrapper.emitted("stack-move")).toEqual([[where]]);
  });

  it("cannot move the cover earlier or the last picture later", async () => {
    const cover = mountMenu({
      stackEdit: { kind: "member", count: 4, position: 0 },
    });
    expect(
      (await submenuItem(cover, "Make cover")).attributes("disabled"),
    ).toBeDefined();
    expect(
      (await submenuItem(cover, "Move earlier")).attributes("disabled"),
    ).toBeDefined();
    expect(
      (await submenuItem(cover, "Move later")).attributes("disabled"),
    ).toBeUndefined();
    const last = mountMenu({
      stackEdit: { kind: "member", count: 4, position: 3 },
    });
    expect(
      (await submenuItem(last, "Move later")).attributes("disabled"),
    ).toBeDefined();
  });

  // One word used to mean two things; now each has its own row.
  it("takes one picture out apart from unstacking the lot", async () => {
    const wrapper = mountMenu({ stackEdit: SECOND_OF_FOUR });
    await (await submenuItem(wrapper, "Take out of stack")).trigger("click");
    expect(wrapper.emitted("remove-from-stack")).toHaveLength(1);
    const again = mountMenu({ stackEdit: SECOND_OF_FOUR });
    await (await submenuItem(again, "Unstack all 4")).trigger("click");
    expect(again.emitted("dissolve-stacks")).toHaveLength(1);
  });

  it("offers to open a collapsed stack instead of moves", async () => {
    const wrapper = mountMenu({ stackEdit: { kind: "collapsed", count: 5 } });
    const labels = (await submenuItems(wrapper)).map((b) => b.text());
    expect(labels.some((t) => t.startsWith("Move"))).toBe(false);
    await (await submenuItem(wrapper, "Open stack")).trigger("click");
    expect(wrapper.emitted("open-stack")).toHaveLength(1);
  });

  // "Make cover" means nothing across three stacks, and a greyed row would
  // only add length.
  it("leaves the order rows out for several stacks", async () => {
    const labels = (await submenuItems(mountMenu())).map((b) => b.text());
    expect(labels.some((t) => t.startsWith("Make cover"))).toBe(false);
    expect(labels).toContain("Unstack 3 stacks");
  });
});

describe.each(MENUS)("%s: Keep cover only", (_name, mountMenu) => {
  it("counts stacks in its label, not the tiles that were clicked", async () => {
    const item = await submenuItem(mountMenu(), "Keep cover only");
    expect(item.text()).toContain("Keep cover only (3 stacks)");
    expect(item.text()).toContain("Every picture but the 3 covers");
  });

  // Ignoring loose pictures is only honest if the label says how many of the
  // selection the action will actually touch.
  it("reports partial eligibility when the selection is mixed", async () => {
    const item = await submenuItem(
      mountMenu({
        selectedImageIds: Array.from({ length: 20 }, (_, i) => String(i)),
        selectedCount: 20,
        stackEdit: { kind: "stacks", stacks: 12 },
        keepCoverOnlyStackCount: 12,
      }),
      "Keep cover only",
    );
    expect(item.text()).toContain("Keep cover only (12 of 20)");
  });

  it("says what moves for a single stack", async () => {
    const item = await submenuItem(
      mountMenu({ stackEdit: SECOND_OF_FOUR, keepCoverOnlyStackCount: 1 }),
      "Keep cover only",
    );
    expect(item.text()).toContain("The other 3 move to the Scrapheap.");
  });

  it("is not offered when no named stack can collapse", async () => {
    const item = await submenuItem(
      mountMenu({ keepCoverOnlyStackCount: 0 }),
      "Keep cover only",
    );
    expect(item).toBeUndefined();
  });

  it("wears the shipped danger treatment", async () => {
    const item = await submenuItem(mountMenu(), "Keep cover only");
    expect(item.classes()).toContain("ctx-item--danger");
  });

  it("emits its intent when clicked", async () => {
    const wrapper = mountMenu();
    await (await submenuItem(wrapper, "Keep cover only")).trigger("click");
    expect(wrapper.emitted("keep-cover-only")).toEqual([[]]);
  });

  // A locked set refuses the WHOLE stack, so a selection in which every stack
  // is locked provably cannot do anything: say why instead of failing quietly.
  it("is disabled with the lock reason when every named stack is locked", async () => {
    const reason = "Locked: every selected stack is held by the locked set 'X'.";
    const item = await submenuItem(
      mountMenu({ keepCoverOnlyLockReason: reason }),
      "Keep cover only",
    );
    expect(item.attributes("disabled")).toBeDefined();
    expect(tip(item)).toBe(reason);
  });

  it("stays enabled when only some stacks are locked", async () => {
    const item = await submenuItem(
      mountMenu({ keepCoverOnlyLockReason: null }),
      "Keep cover only",
    );
    expect(item.attributes("disabled")).toBeUndefined();
  });

  // Taken out until it has a design of its own.
  it("has no Keep recipes only beside it", async () => {
    const wrapper = mountMenu();
    const labels = (await submenuItems(wrapper)).map((b) => b.text());
    expect(labels.some((t) => t.startsWith("Keep recipes only"))).toBe(false);
    expect(wrapper.text()).not.toContain("Keep recipes only");
  });
});

// Read-only is read from two different places by the two menus (the grid menu
// takes the session ref straight from apiClient; the pill's menu is handed a
// prop by SelectionBar), so it is asserted once per menu rather than shared.
describe("Edit stack in a read-only session", () => {
  it("is disabled in the grid's context menu", async () => {
    isReadOnly.value = true;
    try {
      const wrapper = mountContextMenu({ stackEdit: SECOND_OF_FOUR });
      const items = await submenuItems(wrapper);
      expect(items.length).toBeGreaterThanOrEqual(5);
      for (const item of items) {
        expect(item.attributes("disabled")).toBeDefined();
      }
    } finally {
      isReadOnly.value = false;
    }
  });

  it("is disabled in the selection pill's overflow", async () => {
    const wrapper = mountSelectionMenu({
      isReadOnly: true,
      stackEdit: SECOND_OF_FOUR,
    });
    const items = await submenuItems(wrapper);
    expect(items.length).toBeGreaterThanOrEqual(5);
    for (const item of items) {
      expect(item.attributes("disabled")).toBeDefined();
    }
  });
});

// The lightbox menu offers the order alone: taking a stack apart there would
// rebuild the filmstrip under the picture being looked at.
describe("the lightbox menu", () => {
  it("offers the order rows and nothing that takes the stack apart", async () => {
    const wrapper = mountContextMenu({
      lightboxMode: true,
      selectedImageIds: ["11"],
      contextImage: { id: "11" },
      stackEdit: SECOND_OF_FOUR,
    });
    const labels = (await submenuItems(wrapper)).map(
      (b) => b.text().split(/Ctrl|⌘/)[0],
    );
    expect(labels).toEqual(["Make cover", "Move earlier", "Move later"]);
    await (await submenuItem(wrapper, "Move later")).trigger("click");
    expect(wrapper.emitted("stack-move")).toEqual([["later"]]);
  });
});

describe("the Edit stack submenu and the keyboard", () => {
  it("shuts when focus leaves it, not when focus moves inside it", async () => {
    const wrapper = mountContextMenu({ stackEdit: SECOND_OF_FOUR });
    const wrap = editStackWrap(wrapper);
    await wrap.trigger("focusin");
    const inside = wrap.find(".ctx-submenu button.ctx-item").element;
    await wrap.trigger("focusout", { relatedTarget: inside });
    expect(wrap.find(".ctx-submenu").exists()).toBe(true);
    await wrap.trigger("focusout", { relatedTarget: document.body });
    expect(wrap.find(".ctx-submenu").exists()).toBe(false);
  });
});

describe("the lightbox menu with nothing to order", () => {
  it("has no Edit stack row rather than an empty submenu", () => {
    const wrapper = mountContextMenu({
      lightboxMode: true,
      selectedImageIds: ["11"],
      contextImage: { id: "11" },
      stackEdit: { kind: "collapsed", count: 4 },
    });
    expect(editStackWrap(wrapper)).toBeUndefined();
  });
});
