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
  listGroupTags: vi.fn(),
  createTagGroup: vi.fn(),
  reorderTagGroups: vi.fn(),
  assignTagsToGroup: vi.fn(),
  importTagGroups: vi.fn(),
}));

// moveTags refreshes the history control after a filing; that is not under test.
vi.mock("../../stores/useOperationStore", () => ({
  useOperationStore: () => ({ refresh: vi.fn().mockResolvedValue() }),
}));

// The import dialog sits on AppDialog, which sits on Vuetify's VDialog: a
// passthrough that renders its slot only while open, as the real one does.
vi.mock("vuetify/components", () => ({
  VIcon: { name: "v-icon", template: "<i><slot /></i>" },
  VDialog: {
    name: "v-dialog",
    props: ["modelValue"],
    template: '<div v-if="modelValue" class="v-dialog-stub"><slot /></div>',
  },
  VTooltip: {
    template: '<slot name="activator" :props="{}" :is-active="false" />',
  },
}));

import TagGroupsBoard from "./TagGroupsBoard.vue";
import { useTagGroupsStore } from "../../stores/useTagGroupsStore";
import * as tagGroupsApi from "../../api/tagGroups";
import { useNoticeStore } from "../../stores/useNoticeStore";

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
// `tag_count` counts every tag filed in a group, the two unknown ones too, and
// the counts on the left come from these fields, never from the rows loaded.
const GROUPS = [
  { id: 2, name: "Meta", position: 0, tag_count: 1 },
  { id: 1, name: "Character", position: 1, tag_count: 3 },
];
const GROUP_MAP = {
  groups: GROUPS,
  tags: { "red hood": 1, "film grain": 2 },
  ungrouped_count: 3,
  all_count: 7,
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

// The server's paged list, for the library's tags above: filtered by group, q
// and sort as the route does, then cut to the page asked for.
function groupOfTag(name) {
  return GROUP_MAP.tags[name] ?? null;
}
function serverPage(params = {}) {
  let rows = TAGS.map((t) => ({ ...t, group_id: groupOfTag(t.tag) }));
  if (params.group === "none") rows = rows.filter((r) => r.group_id == null);
  else if (params.group !== "all" && params.group != null) {
    rows = rows.filter((r) => r.group_id === Number(params.group));
  }
  if (params.q) rows = rows.filter((r) => r.tag.includes(params.q));
  rows = rows.slice().sort((a, b) =>
    params.sort === "name"
      ? a.tag.localeCompare(b.tag)
      : b.count - a.count || a.tag.localeCompare(b.tag),
  );
  const offset = params.offset ?? 0;
  const limit = params.limit ?? 200;
  return { total: rows.length, tags: rows.slice(offset, offset + limit) };
}

// The debounce on the filter field is 250 ms; wait it out, then let the page land.
async function settle() {
  await new Promise((r) => setTimeout(r, 280));
  await flushPromises();
}

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
  tagGroupsApi.listGroupTags.mockImplementation(async (p) => serverPage(p));
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
  it("lists All, Ungrouped and the groups in rail order, counted from the map", async () => {
    const wrapper = await mountBoard();
    const rows = wrapper.findAll(".tgb-groups > .tgb-row");
    expect(rows.map((r) => r.find(".tgb-row-name").text())).toEqual([
      "All tags",
      "Ungrouped",
      "Meta",
      "Character",
    ]);
    // 7 is all_count (two known tags nobody has, plus the five in the library);
    // Character's 3 is its tag_count, unknown tags included.
    expect(rows.map((r) => r.find(".tgb-row-count").text())).toEqual([
      "7",
      "3",
      "1",
      "3",
    ]);
  });

  it("a known tag with no picture shows 0", async () => {
    const wrapper = await mountBoard();
    tagGroupsApi.listGroupTags.mockResolvedValueOnce({
      total: 1,
      tags: [{ tag: "unknown a", count: 0, group_id: 1 }],
    });
    await groupRow(wrapper, 1).trigger("click");
    await flushPromises();
    expect(tagRow(wrapper, "unknown a").find(".tgb-tag-count").text()).toBe("0");
  });

  it("choosing a group requests that group and lists only its tags, with the Ungrouped hint on Ungrouped", async () => {
    const wrapper = await mountBoard();
    await groupRow(wrapper, 2).trigger("click");
    await flushPromises();
    expect(tagGroupsApi.listGroupTags).toHaveBeenLastCalledWith(
      expect.objectContaining({ group: "2", offset: 0, limit: 200 }),
    );
    expect(visibleNames(wrapper)).toEqual(["film grain"]);
    expect(wrapper.find(".tgb-hint").exists()).toBe(false);

    await wrapper.find(".tgb-groups > .tgb-row:nth-child(2)").trigger("click");
    await flushPromises();
    expect(tagGroupsApi.listGroupTags).toHaveBeenLastCalledWith(
      expect.objectContaining({ group: "none" }),
    );
    expect(visibleNames(wrapper)).toEqual(["aiko", "pine", "moss"]);
    expect(wrapper.find(".tgb-hint").text()).toContain(
      "Tags no group claims yet",
    );
  });

  it("All asks for group all", async () => {
    const wrapper = await mountBoard();
    await groupRow(wrapper, 1).trigger("click");
    await wrapper.find(".tgb-groups > .tgb-row").trigger("click");
    await flushPromises();
    expect(tagGroupsApi.listGroupTags).toHaveBeenLastCalledWith(
      expect.objectContaining({ group: "all" }),
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
    await flushPromises();
    expect(tagGroupsApi.listGroupTags).toHaveBeenLastCalledWith(
      expect.objectContaining({ sort: "name" }),
    );
    expect(visibleNames(wrapper)).toEqual([
      "aiko",
      "film grain",
      "moss",
      "pine",
      "red hood",
    ]);
  });

  it("the filter text is sent as q, after the debounce", async () => {
    const wrapper = await mountBoard();
    tagGroupsApi.listGroupTags.mockClear();
    await wrapper.setProps({ filter: "pin" });
    await flushPromises();
    expect(tagGroupsApi.listGroupTags).not.toHaveBeenCalled();
    await settle();
    expect(tagGroupsApi.listGroupTags).toHaveBeenCalledTimes(1);
    expect(tagGroupsApi.listGroupTags).toHaveBeenLastCalledWith(
      expect.objectContaining({ q: "pin", offset: 0 }),
    );
    expect(visibleNames(wrapper)).toEqual(["pine"]);
  });

  it("a filter it opens with is in the first page asked for", async () => {
    tagGroupsApi.listGroupTags.mockClear();
    const wrapper = await mountBoard({ filter: " pin " });
    expect(tagGroupsApi.listGroupTags).toHaveBeenCalledTimes(1);
    expect(tagGroupsApi.listGroupTags).toHaveBeenLastCalledWith(
      expect.objectContaining({ q: "pin", offset: 0 }),
    );
    expect(visibleNames(wrapper)).toEqual(["pine"]);
  });

  it("Show more asks for the next offset and appends the page", async () => {
    const big = Array.from({ length: 450 }, (_, i) => ({
      tag: `tag ${String(i).padStart(3, "0")}`,
      count: 450 - i,
      group_id: null,
    }));
    tagGroupsApi.listGroupTags.mockImplementation(async (p) => ({
      total: big.length,
      tags: big.slice(p.offset ?? 0, (p.offset ?? 0) + (p.limit ?? 200)),
    }));
    const wrapper = await mountBoard();
    expect(tagRows(wrapper)).toHaveLength(200);
    await wrapper.find(".tgb-more .app-btn").trigger("click");
    await flushPromises();
    expect(tagGroupsApi.listGroupTags).toHaveBeenLastCalledWith(
      expect.objectContaining({ offset: 200, limit: 200 }),
    );
    expect(tagRows(wrapper)).toHaveLength(400);
    await wrapper.find(".tgb-more .app-btn").trigger("click");
    await flushPromises();
    expect(tagRows(wrapper)).toHaveLength(450);
    expect(wrapper.find(".tgb-more").exists()).toBe(false);
  });

  it("drops a page that lands after the choice moved on", async () => {
    const pending = {};
    tagGroupsApi.listGroupTags.mockImplementation(
      (p) => new Promise((resolve) => (pending[p.group] = resolve)),
    );
    const wrapper = await mountBoard();
    // All is in flight; then Meta is chosen and answers first.
    await groupRow(wrapper, 2).trigger("click");
    pending["2"]({ total: 1, tags: [{ tag: "film grain", count: 118, group_id: 2 }] });
    await flushPromises();
    expect(visibleNames(wrapper)).toEqual(["film grain"]);
    // The old All page lands late: it must not overwrite Meta's list.
    pending["all"]({ total: 5, tags: TAGS });
    await flushPromises();
    expect(visibleNames(wrapper)).toEqual(["film grain"]);
  });

  it("a read that fails says so, and the board keeps its other controls", async () => {
    tagGroupsApi.listGroupTags.mockRejectedValueOnce(new Error("down"));
    const wrapper = await mountBoard();
    expect(wrapper.find('[role="alert"]').text()).toBe("Couldn't read the tags.");
    expect(wrapper.find(".tgb-groups").exists()).toBe(true);
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
  it("a move re-reads the chosen list", async () => {
    const wrapper = await mountBoard();
    await groupRow(wrapper, 2).trigger("click");
    await flushPromises();
    tagGroupsApi.listGroupTags.mockClear();
    await tagRow(wrapper, "film grain").trigger("click");
    await wrapper.find(".tgb-move > .tgb-pill-verb").trigger("click");
    await wrapper.findAll(".tgb-move-menu [role='menuitem']")[1].trigger("click");
    await flushPromises();
    expect(tagGroupsApi.assignTagsToGroup).toHaveBeenCalledWith(["film grain"], 1);
    expect(tagGroupsApi.listGroupTags).toHaveBeenCalledTimes(1);
  });

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

describe("TagGroupsBoard: import a tag file", () => {
  // Small counts only: the notice's thousands separator is the locale's.
  const PLAN = {
    rows: 900,
    filed: 42,
    from_defaults: 0,
    from_ungrouped: 42,
    user_conflicts: 0,
    user_conflict_sample: [],
    user_overwritten: 0,
    groups_created: ["Wildlife", "Mood"],
    skipped_uncategorised: 5,
  };
  const PLAN_CONFLICT = {
    ...PLAN,
    user_conflicts: 2,
    user_conflict_sample: ["red hood", "moss"],
  };

  function pickFile(wrapper, name = "danbooru_tags.xlsx") {
    const input = wrapper.find('input[type="file"]');
    Object.defineProperty(input.element, "files", {
      value: [new File(["x"], name)],
      configurable: true,
    });
    return input.trigger("change");
  }

  const importButton = (wrapper) => wrapper.find('[data-testid="tgb-import"]');
  const questionText = (wrapper) =>
    wrapper.find('[data-testid="tid-message"]');
  const notices = () => useNoticeStore().notices.map((n) => n.text);

  beforeEach(() => {
    tagGroupsApi.importTagGroups.mockReset();
  });

  it("says what it reads, shows a spreadsheet glyph, and filters the picker by type", async () => {
    const wrapper = await mountBoard();
    const button = importButton(wrapper);
    const tip = button.find(".tip").attributes("data-tip");
    expect(tip).toMatch(/\.xlsx or \.csv/);
    expect(tip).toMatch(/english, category and subcategory/);
    expect(button.text()).toContain("mdi-file-excel-outline");
    const accept = wrapper.find('[data-testid="tgb-file"]').attributes("accept");
    expect(accept.split(",")).toEqual([
      ".xlsx",
      ".csv",
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      "text/csv",
    ]);
  });

  it("with no conflicts: one dry run, then the import, with the file's win and no move of the user's", async () => {
    tagGroupsApi.importTagGroups
      .mockResolvedValueOnce({ plan: PLAN })
      .mockResolvedValueOnce({ plan: PLAN, ...GROUP_MAP });
    const wrapper = await mountBoard();
    await pickFile(wrapper);
    await flushPromises();

    expect(tagGroupsApi.importTagGroups).toHaveBeenCalledTimes(2);
    expect(tagGroupsApi.importTagGroups.mock.calls[0][1]).toEqual({ dryRun: true });
    expect(tagGroupsApi.importTagGroups.mock.calls[1][1]).toEqual({
      dryRun: false,
      overwriteUser: false,
    });
    expect(questionText(wrapper).exists()).toBe(false);
    expect(notices()).toEqual([
      "Filed 42 tags from danbooru_tags.xlsx · 2 new groups",
    ]);
    expect(notices().join(" ")).not.toMatch(/not in your library/);
  });

  it("with tags the user filed by hand, asks first; Keep my filing commits without moving them", async () => {
    tagGroupsApi.importTagGroups
      .mockResolvedValueOnce({ plan: PLAN_CONFLICT })
      .mockResolvedValueOnce({ plan: PLAN_CONFLICT, ...GROUP_MAP });
    const wrapper = await mountBoard();
    await pickFile(wrapper);
    await flushPromises();

    expect(tagGroupsApi.importTagGroups).toHaveBeenCalledTimes(1);
    expect(questionText(wrapper).text()).toBe(
      "2 tags you filed yourself are in other groups in this file.",
    );
    expect(wrapper.text()).toContain("For example: red hood, moss");

    await wrapper.find('[data-testid="tid-keep"]').trigger("click");
    await flushPromises();
    expect(tagGroupsApi.importTagGroups).toHaveBeenCalledTimes(2);
    expect(tagGroupsApi.importTagGroups.mock.calls[1][1]).toEqual({
      dryRun: false,
      overwriteUser: false,
    });
    expect(questionText(wrapper).exists()).toBe(false);
    expect(notices()).toHaveLength(1);
  });

  it("Move them too commits with overwriteUser true", async () => {
    tagGroupsApi.importTagGroups
      .mockResolvedValueOnce({ plan: PLAN_CONFLICT })
      .mockResolvedValueOnce({ plan: PLAN_CONFLICT, ...GROUP_MAP });
    const wrapper = await mountBoard();
    await pickFile(wrapper);
    await flushPromises();

    await wrapper.find('[data-testid="tid-move"]').trigger("click");
    await flushPromises();
    expect(tagGroupsApi.importTagGroups).toHaveBeenCalledTimes(2);
    expect(tagGroupsApi.importTagGroups.mock.calls[1][1]).toEqual({
      dryRun: false,
      overwriteUser: true,
    });
  });

  it("Cancel and Esc both commit nothing", async () => {
    tagGroupsApi.importTagGroups.mockResolvedValue({ plan: PLAN_CONFLICT });
    const wrapper = await mountBoard();

    await pickFile(wrapper);
    await flushPromises();
    const cancel = wrapper
      .findAll(".app-btn")
      .find((b) => b.text().startsWith("Cancel"));
    await cancel.trigger("click");
    await flushPromises();
    expect(questionText(wrapper).exists()).toBe(false);

    await pickFile(wrapper);
    await flushPromises();
    await wrapper.find(".app-dialog").trigger("keydown", { key: "Escape" });
    await flushPromises();
    expect(questionText(wrapper).exists()).toBe(false);

    // Dry runs only: nothing was committed.
    expect(tagGroupsApi.importTagGroups).toHaveBeenCalledTimes(2);
    expect(
      tagGroupsApi.importTagGroups.mock.calls.every((c) => c[1].dryRun),
    ).toBe(true);
    expect(notices()).toEqual([]);
  });

  it("focus returns to Import once the question is answered", async () => {
    tagGroupsApi.importTagGroups.mockResolvedValue({ plan: PLAN_CONFLICT });
    const wrapper = mount(TagGroupsBoard, {
      attachTo: document.body,
      global: globalOpts,
    });
    await flushPromises();
    try {
      await pickFile(wrapper);
      await flushPromises();
      expect(document.activeElement).not.toBe(importButton(wrapper).element);
      await wrapper.find('[data-testid="tid-keep"]').trigger("click");
      await flushPromises();
      expect(document.activeElement).toBe(importButton(wrapper).element);
    } finally {
      wrapper.unmount();
    }
  });

  it("an error shows the server's detail and commits nothing", async () => {
    tagGroupsApi.importTagGroups.mockRejectedValueOnce(
      Object.assign(new Error("Request failed"), {
        response: { status: 400, data: { detail: "Missing columns: english" } },
      }),
    );
    const wrapper = await mountBoard();
    await pickFile(wrapper);
    await flushPromises();
    expect(tagGroupsApi.importTagGroups).toHaveBeenCalledTimes(1);
    const notice = useNoticeStore().notices.at(-1);
    expect(notice.level).toBe("error");
    expect(notice.text).toContain("Missing columns: english");
  });

  it("while the file is read, the control says so and cannot be pressed again", async () => {
    let answer;
    tagGroupsApi.importTagGroups.mockReturnValueOnce(new Promise((r) => (answer = r)));
    const wrapper = await mountBoard();
    await pickFile(wrapper);
    await flushPromises();
    expect(importButton(wrapper).text()).toBe("mdi-loading Importing…");
    expect(importButton(wrapper).attributes("disabled")).toBeDefined();
    answer({ plan: PLAN });
    tagGroupsApi.importTagGroups.mockResolvedValueOnce({ plan: PLAN, ...GROUP_MAP });
    await flushPromises();
    expect(importButton(wrapper).text()).toBe("mdi-file-excel-outline Import…");
  });

  it("a read-only session has no Import control", async () => {
    session.readOnly.value = true;
    const wrapper = await mountBoard();
    expect(importButton(wrapper).exists()).toBe(false);
    expect(wrapper.find('input[type="file"]').exists()).toBe(false);
  });
});
