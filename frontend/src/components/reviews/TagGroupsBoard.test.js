// The Groups view of Tags to review (TagGroupsBoard.vue): the rail-ordered
// groups with their counts, choosing a group, selection, the selection pill's
// Move to group and Review, drag onto a group, reorder, and new-group naming.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { mount, flushPromises } from "@vue/test-utils";
import { h } from "vue";

const session = vi.hoisted(() => ({ readOnly: { value: false } }));

vi.mock("../../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  onSessionReset: () => () => {},
  sessionContext: { value: null },
  apiClient: {
    get: vi.fn().mockResolvedValue({ data: [] }),
    post: vi.fn().mockResolvedValue({ data: {} }),
  },
  isReadOnly: session.readOnly,
}));

vi.mock("../../api/tagGroups", () => ({
  getTagGroups: vi.fn(),
  createTagGroup: vi.fn(),
  reorderTagGroups: vi.fn(),
  assignTagsToGroup: vi.fn(),
}));

vi.mock("../../api/tags", () => ({
  listTags: vi.fn(),
}));

// moveTags refreshes the history control after a filing; that is not under test.
vi.mock("../../stores/useOperationStore", () => ({
  useOperationStore: () => ({ refresh: vi.fn().mockResolvedValue() }),
}));

import TagGroupsBoard from "./TagGroupsBoard.vue";
import { useTagGroupsStore } from "../../stores/useTagGroupsStore";
import * as tagGroupsApi from "../../api/tagGroups";
import * as tagsApi from "../../api/tags";

const VIcon = {
  name: "v-icon",
  setup:
    (_props, { slots }) =>
    () =>
      h("i", { class: "v-icon" }, slots.default?.()),
};

// The one tooltip surface, reduced to what a test can read: the tip's text as
// `data-tip` on the activator.
const TooltipStub = {
  name: "Tooltip",
  props: ["text", "shortcut", "location", "disabled", "describe", "activator"],
  template: `<slot
      v-if="$slots.activator"
      name="activator"
      :props="{ 'data-tip': text || undefined }"
    /><span v-else-if="text" class="tip" :data-tip="text" />`,
};

const globalOpts = { stubs: { Tooltip: TooltipStub, "v-icon": VIcon } };

// Rail order is by `position`, so Meta (position 0) comes before Character.
const GROUPS = [
  { id: 2, name: "Meta", position: 0 },
  { id: 1, name: "Character", position: 1 },
];
const GROUP_MAP = {
  groups: GROUPS,
  tags: { "red hood": 1, "film grain": 2 },
};

// Picture counts: pine 133, aiko 212 - so the Pictures order is
// aiko, pine, film grain, moss, red hood; by name it is aiko, film grain,
// moss, pine, red hood.
const TAGS = [
  { tag: "red hood", count: 10 },
  { tag: "film grain", count: 118 },
  { tag: "aiko", count: 212 },
  { tag: "pine", count: 133 },
  { tag: "moss", count: 26 },
];

let store;

async function mountBoard(props = {}) {
  const wrapper = mount(TagGroupsBoard, { props, global: globalOpts });
  await flushPromises();
  return wrapper;
}

function tagRows(wrapper) {
  return wrapper.findAll(".tgb-tag");
}

function tagRow(wrapper, name) {
  const row = tagRows(wrapper).find(
    (w) => w.find(".tgb-tag-name").text() === name,
  );
  if (!row) throw new Error(`no tag row for ${name}`);
  return row;
}

function visibleNames(wrapper) {
  return tagRows(wrapper).map((w) => w.find(".tgb-tag-name").text());
}

function groupRow(wrapper, id) {
  return wrapper.find(`.tgb-row--group[data-group-id="${id}"]`);
}

function dataTransfer() {
  return {
    effectAllowed: "",
    setData() {},
    getData() {
      return "";
    },
  };
}

beforeEach(async () => {
  setActivePinia(createPinia());
  session.readOnly.value = false;
  vi.clearAllMocks();
  tagGroupsApi.getTagGroups.mockResolvedValue(GROUP_MAP);
  tagsApi.listTags.mockResolvedValue(TAGS);
  tagGroupsApi.assignTagsToGroup.mockResolvedValue(GROUP_MAP);
  tagGroupsApi.reorderTagGroups.mockResolvedValue(GROUP_MAP);
  tagGroupsApi.createTagGroup.mockResolvedValue({
    ...GROUP_MAP,
    group: { id: 3, name: "Wildlife", position: 2 },
  });
  store = useTagGroupsStore();
  await store.fetch({ force: true });
});

describe("TagGroupsBoard: the chooser", () => {
  it("lists All, Ungrouped and the groups in rail order with their counts", async () => {
    const wrapper = await mountBoard();
    const rows = wrapper.findAll(".tgb-groups > .tgb-row");
    expect(rows.map((r) => r.find(".tgb-row-name").text())).toEqual([
      "All tags",
      "Ungrouped",
      "Meta",
      "Character",
    ]);
    expect(rows.map((r) => r.find(".tgb-row-count").text())).toEqual([
      "5",
      "3",
      "1",
      "1",
    ]);
  });

  it("choosing a group lists only its tags, with the Ungrouped hint on Ungrouped", async () => {
    const wrapper = await mountBoard();
    await groupRow(wrapper, 2).trigger("click");
    expect(visibleNames(wrapper)).toEqual(["film grain"]);
    expect(wrapper.find(".tgb-hint").exists()).toBe(false);

    await wrapper.find(".tgb-groups > .tgb-row:nth-child(2)").trigger("click");
    expect(visibleNames(wrapper)).toEqual(["aiko", "pine", "moss"]);
    expect(wrapper.find(".tgb-hint").text()).toContain(
      "Tags no group claims yet",
    );
  });

  it("sorts by picture count, or by name", async () => {
    const wrapper = await mountBoard();
    expect(visibleNames(wrapper)).toEqual([
      "aiko",
      "pine",
      "film grain",
      "moss",
      "red hood",
    ]);
    await wrapper.find(".tgb-sort").setValue("name");
    expect(visibleNames(wrapper)).toEqual([
      "aiko",
      "film grain",
      "moss",
      "pine",
      "red hood",
    ]);
  });

  it("the filter narrows the tag list here too", async () => {
    const wrapper = await mountBoard({ filter: "pin" });
    expect(visibleNames(wrapper)).toEqual(["pine"]);
  });

  it("says the groups cover the whole library, not the board's scope", async () => {
    const wrapper = await mountBoard();
    expect(wrapper.find(".tgb-scope-note").text()).toContain(
      "whole library",
    );
  });
});

describe("TagGroupsBoard: selection", () => {
  it("click selects one; Ctrl-click toggles; Shift-click selects the range", async () => {
    const wrapper = await mountBoard();
    await tagRow(wrapper, "aiko").trigger("click");
    expect(wrapper.vm.selected).toEqual(new Set(["aiko"]));

    await tagRow(wrapper, "moss").trigger("click", { ctrlKey: true });
    expect(wrapper.vm.selected).toEqual(new Set(["aiko", "moss"]));

    await tagRow(wrapper, "moss").trigger("click", { ctrlKey: true });
    expect(wrapper.vm.selected).toEqual(new Set(["aiko"]));

    // The anchor is the last Ctrl-clicked tag (moss), so re-anchor on aiko:
    // aiko is row 0, film grain row 2 in the Pictures order.
    await tagRow(wrapper, "aiko").trigger("click");
    await tagRow(wrapper, "film grain").trigger("click", { shiftKey: true });
    expect(wrapper.vm.selected).toEqual(
      new Set(["aiko", "pine", "film grain"]),
    );
  });

  it("Esc clears the selection, and reports whether it had one", async () => {
    const wrapper = await mountBoard();
    expect(wrapper.vm.clearSelection()).toBe(false);
    await tagRow(wrapper, "aiko").trigger("click");
    expect(wrapper.vm.clearSelection()).toBe(true);
    expect(wrapper.vm.selected.size).toBe(0);
    await flushPromises();
    expect(wrapper.find(".tgb-pill").exists()).toBe(false);
  });

  it("Esc closes an open Move menu first, and keeps the selection", async () => {
    const wrapper = await mountBoard();
    await tagRow(wrapper, "aiko").trigger("click");
    await wrapper.find(".tgb-move > .tgb-pill-verb").trigger("click");
    expect(wrapper.find(".tgb-move-menu").exists()).toBe(true);
    expect(wrapper.vm.escape()).toBe(true);
    await flushPromises();
    expect(wrapper.find(".tgb-move-menu").exists()).toBe(false);
    expect(wrapper.vm.selected).toEqual(new Set(["aiko"]));
    // The next Esc has no menu to close, so it clears the selection.
    expect(wrapper.vm.escape()).toBe(true);
    expect(wrapper.vm.selected.size).toBe(0);
  });

  it("the pill shows the count and is absent with nothing selected", async () => {
    const wrapper = await mountBoard();
    expect(wrapper.find(".tgb-pill").exists()).toBe(false);
    await tagRow(wrapper, "aiko").trigger("click");
    await tagRow(wrapper, "pine").trigger("click", { ctrlKey: true });
    expect(wrapper.find(".tgb-pill-count").text()).toBe("2 tags");
  });
});

describe("TagGroupsBoard: the pill", () => {
  it("Move to group files the selection under the chosen group", async () => {
    const wrapper = await mountBoard();
    await tagRow(wrapper, "aiko").trigger("click");
    await tagRow(wrapper, "pine").trigger("click", { ctrlKey: true });

    await wrapper.find(".tgb-move > .tgb-pill-verb").trigger("click");
    const items = wrapper.findAll(".tgb-move-menu [role='menuitem']");
    expect(items.map((i) => i.text())).toEqual([
      "Meta",
      "Character",
      "Ungrouped",
    ]);
    await items[1].trigger("click");
    await flushPromises();

    expect(tagGroupsApi.assignTagsToGroup).toHaveBeenCalledWith(
      ["aiko", "pine"],
      1,
    );
    expect(wrapper.vm.selected.size).toBe(0);
  });

  it("Move to group Ungrouped passes null", async () => {
    const wrapper = await mountBoard();
    await tagRow(wrapper, "film grain").trigger("click");
    await wrapper.find(".tgb-move > .tgb-pill-verb").trigger("click");
    const items = wrapper.findAll(".tgb-move-menu [role='menuitem']");
    await items[2].trigger("click");
    await flushPromises();
    expect(tagGroupsApi.assignTagsToGroup).toHaveBeenCalledWith(
      ["film grain"],
      null,
    );
  });

  it("Review is enabled for one tag and emits start-review with it", async () => {
    const wrapper = await mountBoard();
    await tagRow(wrapper, "aiko").trigger("click");
    const review = wrapper.find(".tgb-pill-verb--key");
    expect(review.attributes("disabled")).toBeUndefined();
    await review.trigger("click");
    expect(wrapper.emitted("start-review")).toEqual([["aiko"]]);
  });

  it("Review is disabled for two tags, and its tooltip says why", async () => {
    const wrapper = await mountBoard();
    await tagRow(wrapper, "aiko").trigger("click");
    await tagRow(wrapper, "pine").trigger("click", { ctrlKey: true });
    const review = wrapper.find(".tgb-pill-verb--key");
    expect(review.attributes("disabled")).toBeDefined();
    expect(wrapper.find(".tgb-review-wrap").attributes("data-tip")).toMatch(
      /one tag at a time/,
    );
    await review.trigger("click");
    expect(wrapper.emitted("start-review")).toBeUndefined();
  });
});

describe("TagGroupsBoard: drag", () => {
  it("dropping a selection on a group row files all of it there", async () => {
    const wrapper = await mountBoard();
    await tagRow(wrapper, "aiko").trigger("click");
    await tagRow(wrapper, "pine").trigger("click", { ctrlKey: true });

    await tagRow(wrapper, "aiko").trigger("dragstart", {
      dataTransfer: dataTransfer(),
    });
    await groupRow(wrapper, 1).trigger("dragover");
    expect(groupRow(wrapper, 1).classes()).toContain("tgb-row--drop");
    expect(groupRow(wrapper, 1).find(".tgb-droplabel").text()).toBe(
      "2 tags → Character · up to 345 pictures",
    );
    await groupRow(wrapper, 1).trigger("drop");
    await flushPromises();

    expect(tagGroupsApi.assignTagsToGroup).toHaveBeenCalledWith(
      ["aiko", "pine"],
      1,
    );
  });

  it("dragging one unselected tag drags just that tag, with a single-tag label", async () => {
    const wrapper = await mountBoard();
    await tagRow(wrapper, "moss").trigger("dragstart", {
      dataTransfer: dataTransfer(),
    });
    await groupRow(wrapper, 2).trigger("dragover");
    expect(groupRow(wrapper, 2).find(".tgb-droplabel").text()).toBe(
      "moss → Meta · on 26 pictures",
    );
    await groupRow(wrapper, 2).trigger("drop");
    await flushPromises();
    expect(tagGroupsApi.assignTagsToGroup).toHaveBeenCalledWith(["moss"], 2);
  });

  it("a drop on the group the tags are already in is no drop", async () => {
    const wrapper = await mountBoard();
    // moss is Ungrouped: the Ungrouped row takes no wash and sends nothing.
    await tagRow(wrapper, "moss").trigger("dragstart", {
      dataTransfer: dataTransfer(),
    });
    const ungrouped = wrapper.findAll(".tgb-groups > .tgb-row")[1];
    expect(ungrouped.text()).toContain("Ungrouped");
    await ungrouped.trigger("dragover");
    expect(ungrouped.classes()).not.toContain("tgb-row--drop");
    await ungrouped.trigger("drop");
    await flushPromises();
    expect(tagGroupsApi.assignTagsToGroup).not.toHaveBeenCalled();
  });

  it("a group row takes no drop of tags already filed there", async () => {
    const wrapper = await mountBoard();
    await wrapper.findAll(".tgb-groups > .tgb-row")[0].trigger("click");
    await tagRow(wrapper, "red hood").trigger("dragstart", {
      dataTransfer: dataTransfer(),
    });
    await groupRow(wrapper, 1).trigger("dragover");
    expect(groupRow(wrapper, 1).classes()).not.toContain("tgb-row--drop");
    expect(groupRow(wrapper, 1).find(".tgb-droplabel").exists()).toBe(false);
    await groupRow(wrapper, 1).trigger("drop");
    await flushPromises();
    expect(tagGroupsApi.assignTagsToGroup).not.toHaveBeenCalled();
  });

  it("All tags is not a drop target", async () => {
    const wrapper = await mountBoard();
    await tagRow(wrapper, "moss").trigger("dragstart", {
      dataTransfer: dataTransfer(),
    });
    const all = wrapper.findAll(".tgb-groups > .tgb-row")[0];
    await all.trigger("dragover");
    await all.trigger("drop");
    await flushPromises();
    expect(all.classes()).not.toContain("tgb-row--drop");
    expect(tagGroupsApi.assignTagsToGroup).not.toHaveBeenCalled();
  });
});

describe("TagGroupsBoard: reorder", () => {
  it("Alt+Down on a group moves it one place and saves the order", async () => {
    const wrapper = await mountBoard();
    // Rail order is Meta (2), Character (1); Alt+Down on Meta swaps them.
    await groupRow(wrapper, 2).trigger("keydown", {
      key: "ArrowDown",
      altKey: true,
    });
    await flushPromises();
    expect(tagGroupsApi.reorderTagGroups).toHaveBeenCalledWith([1, 2]);
  });

  it("Alt+Up on the first group does nothing", async () => {
    const wrapper = await mountBoard();
    await groupRow(wrapper, 2).trigger("keydown", {
      key: "ArrowUp",
      altKey: true,
    });
    await flushPromises();
    expect(tagGroupsApi.reorderTagGroups).not.toHaveBeenCalled();
  });

  it("dragging a group onto another takes its place and saves the order", async () => {
    const wrapper = await mountBoard();
    await groupRow(wrapper, 2).trigger("dragstart", {
      dataTransfer: dataTransfer(),
    });
    await groupRow(wrapper, 1).trigger("dragover");
    await groupRow(wrapper, 1).trigger("drop");
    await flushPromises();
    expect(tagGroupsApi.reorderTagGroups).toHaveBeenCalledWith([1, 2]);
    expect(tagGroupsApi.assignTagsToGroup).not.toHaveBeenCalled();
  });
});

describe("TagGroupsBoard: reorder with three groups", () => {
  it("dragging the last group onto the first lands it before the first", async () => {
    tagGroupsApi.getTagGroups.mockResolvedValueOnce({
      groups: [
        { id: 3, name: "Copyright", position: 0 },
        { id: 2, name: "Meta", position: 1 },
        { id: 1, name: "Character", position: 2 },
      ],
      tags: {},
    });
    await store.fetch({ force: true });
    const wrapper = await mountBoard();
    await groupRow(wrapper, 1).trigger("dragstart", {
      dataTransfer: dataTransfer(),
    });
    await groupRow(wrapper, 3).trigger("dragover");
    await groupRow(wrapper, 3).trigger("drop");
    await flushPromises();
    // Up the rail: the dragged group takes the target's place, the rest shift.
    expect(tagGroupsApi.reorderTagGroups).toHaveBeenCalledWith([1, 3, 2]);
  });
});

describe("TagGroupsBoard: new group", () => {
  it("+ names a group in place; Enter creates it and chooses it", async () => {
    const wrapper = await mountBoard();
    await wrapper.find('button[aria-label="New group"]').trigger("click");
    const input = wrapper.find(".tgb-input");
    await input.setValue("Wildlife");
    await input.trigger("keydown", { key: "Enter" });
    await flushPromises();
    expect(tagGroupsApi.createTagGroup).toHaveBeenCalledWith("Wildlife");
  });

  it("Esc cancels without creating", async () => {
    const wrapper = await mountBoard();
    await wrapper.find('button[aria-label="New group"]').trigger("click");
    const input = wrapper.find(".tgb-input");
    await input.setValue("Wildlife");
    await input.trigger("keydown", { key: "Escape" });
    await flushPromises();
    expect(wrapper.find(".tgb-input").exists()).toBe(false);
    expect(tagGroupsApi.createTagGroup).not.toHaveBeenCalled();
  });
});

describe("TagGroupsBoard: read-only", () => {
  it("offers no drag, no +, and no pill", async () => {
    session.readOnly.value = true;
    const wrapper = await mountBoard();
    expect(wrapper.find('button[aria-label="New group"]').exists()).toBe(
      false,
    );
    expect(tagRow(wrapper, "aiko").attributes("draggable")).toBe("false");
    await tagRow(wrapper, "aiko").trigger("click");
    expect(wrapper.find(".tgb-pill").exists()).toBe(false);
  });
});
