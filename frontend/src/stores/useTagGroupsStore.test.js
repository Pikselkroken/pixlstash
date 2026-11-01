import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { ref } from "vue";

const isReadOnly = ref(false);
vi.mock("../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  get isReadOnly() {
    return isReadOnly;
  },
  onSessionReset: () => () => {},
}));
vi.mock("../api/tagGroups", () => ({
  getTagGroups: vi.fn(),
  createTagGroup: vi.fn(),
  reorderTagGroups: vi.fn(),
  assignTagsToGroup: vi.fn(),
}));
const refresh = vi.fn().mockResolvedValue();
vi.mock("./useOperationStore", () => ({
  useOperationStore: () => ({ refresh }),
}));

import { assignTagsToGroup, getTagGroups, reorderTagGroups } from "../api/tagGroups";
import { groupToken, isGroupToken, useTagGroupsStore } from "./useTagGroupsStore";
import { useFilterStore } from "./useFilterStore";

const MAP = {
  groups: [
    { id: 1, name: "General", position: 1 },
    { id: 2, name: "Character", position: 0 },
  ],
  tags: { "red hood": 2, forest: 1 },
};

beforeEach(() => {
  setActivePinia(createPinia());
  isReadOnly.value = false;
  vi.clearAllMocks();
  window.localStorage.clear();
  getTagGroups.mockResolvedValue(MAP);
});

describe("the map", () => {
  it("orders groups by position and files tags by name", async () => {
    const store = useTagGroupsStore();
    await store.fetch();
    expect(store.groups.map((g) => g.name)).toEqual(["Character", "General"]);
    expect(store.groupFor("red hood")?.name).toBe("Character");
    expect(store.groupFor("Red Hood")).toBeNull();
    expect(store.nameOf(null)).toBe("Ungrouped");
  });

  it("moves tags, takes the server's map and re-reads the history", async () => {
    const store = useTagGroupsStore();
    await store.fetch();
    assignTagsToGroup.mockResolvedValue({ ...MAP, tags: { "red hood": 1, forest: 1 } });
    expect(await store.moveTags(["red hood", "red hood"], 1)).toBe(true);
    expect(assignTagsToGroup).toHaveBeenCalledWith(["red hood"], 1);
    expect(store.groupFor("red hood")?.name).toBe("General");
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("sends a move even when its own copy says the tag is there already", async () => {
    // The copy can be 30 s old: another tab may have moved it since.
    const store = useTagGroupsStore();
    await store.fetch();
    assignTagsToGroup.mockResolvedValue(MAP);
    expect(await store.moveTags(["forest"], 1)).toBe(true);
    expect(assignTagsToGroup).toHaveBeenCalledWith(["forest"], 1);
  });

  it("re-reads a grid narrowed by a group term after a move, and only that grid", async () => {
    const store = useTagGroupsStore();
    const filters = useFilterStore();
    await store.fetch();
    assignTagsToGroup.mockResolvedValue(MAP);
    filters.tagFilter = ["forest"];
    filters.tagRejectedFilter = ["__group:1"];
    const has = filters.tagFilter;
    const lacks = filters.tagRejectedFilter;
    await store.moveTags(["forest"], 2);
    expect(filters.tagFilter).toBe(has);
    expect(filters.tagRejectedFilter).not.toBe(lacks);
    expect(filters.tagRejectedFilter).toEqual(["__group:1"]);
  });

  it("does not ask again on every mount after a refusal", async () => {
    getTagGroups.mockRejectedValue(Object.assign(new Error("403"), { response: { status: 403 } }));
    const store = useTagGroupsStore();
    await store.fetch();
    await store.fetch();
    expect(getTagGroups).toHaveBeenCalledTimes(1);
    expect(store.hasGroups).toBe(false);
  });

  it("puts the old order back when a reorder fails", async () => {
    const store = useTagGroupsStore();
    await store.fetch();
    reorderTagGroups.mockRejectedValue(new Error("boom"));
    expect(await store.reorder([1, 2])).toBe(false);
    expect(store.groups.map((g) => g.id)).toEqual([2, 1]);
  });
});

describe("view choices", () => {
  it("default to List and Health and are remembered", () => {
    const store = useTagGroupsStore();
    expect(store.railView).toBe("list");
    expect(store.boardView).toBe("health");
    store.setRailView("groups");
    store.setBoardView("groups");
    store.toggleCollapsed(null);
    setActivePinia(createPinia());
    const again = useTagGroupsStore();
    expect(again.railView).toBe("groups");
    expect(again.boardView).toBe("groups");
    expect(again.isCollapsed(null)).toBe(true);
    expect(again.isCollapsed(2)).toBe(false);
  });
});

describe("filter terms", () => {
  it("spell a group and Ungrouped", () => {
    expect(groupToken(3)).toBe("__group:3");
    expect(groupToken(null)).toBe("__group:none");
    expect(isGroupToken("__group:3")).toBe(true);
    expect(isGroupToken("group:3")).toBe(false);
  });
});
