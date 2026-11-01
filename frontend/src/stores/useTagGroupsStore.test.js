import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { ref } from "vue";

const isReadOnly = ref(false);
const resetHandlers = [];
vi.mock("../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  get isReadOnly() {
    return isReadOnly;
  },
  onSessionReset: (handler) => {
    resetHandlers.push(handler);
    return () => {};
  },
}));
vi.mock("../api/tagGroups", () => ({
  getTagGroups: vi.fn(),
  createTagGroup: vi.fn(),
  reorderTagGroups: vi.fn(),
  assignTagsToGroup: vi.fn(),
  importTagGroups: vi.fn(),
}));
const refresh = vi.fn().mockResolvedValue();
vi.mock("./useOperationStore", () => ({
  useOperationStore: () => ({ refresh }),
}));

import {
  assignTagsToGroup,
  getTagGroups,
  importTagGroups,
  reorderTagGroups,
} from "../api/tagGroups";
import { groupToken, isGroupToken, useTagGroupsStore } from "./useTagGroupsStore";
import { useFilterStore } from "./useFilterStore";
import { useNoticeStore } from "./useNoticeStore";

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

  it("never asks in a read-only session, which cannot read the map", async () => {
    isReadOnly.value = true;
    const store = useTagGroupsStore();
    await store.fetch();
    await store.fetch({ force: true });
    expect(getTagGroups).not.toHaveBeenCalled();
    expect(store.hasGroups).toBe(false);
  });

  it("asks again on the next mount after a dropped connection or a server error", async () => {
    getTagGroups
      .mockRejectedValueOnce(new Error("Network Error"))
      .mockRejectedValueOnce(Object.assign(new Error("500"), { response: { status: 500 } }))
      .mockResolvedValueOnce(MAP);
    const store = useTagGroupsStore();
    await store.fetch();
    await store.fetch();
    await store.fetch();
    expect(getTagGroups).toHaveBeenCalledTimes(3);
    expect(store.hasGroups).toBe(true);
  });

  it("does not ask again on every mount after a refusal", async () => {
    getTagGroups.mockRejectedValue(Object.assign(new Error("403"), { response: { status: 403 } }));
    const store = useTagGroupsStore();
    await store.fetch();
    await store.fetch();
    expect(getTagGroups).toHaveBeenCalledTimes(1);
    expect(store.hasGroups).toBe(false);
  });

  it("keeps a group a partial order leaves out in its place, after the listed ones", async () => {
    getTagGroups.mockResolvedValue({
      groups: [
        { id: 1, name: "A", position: 0 },
        { id: 2, name: "B", position: 1 },
        { id: 3, name: "C", position: 2 },
      ],
      tags: {},
    });
    const store = useTagGroupsStore();
    await store.fetch();
    let answer;
    reorderTagGroups.mockReturnValueOnce(new Promise((r) => (answer = r)));
    const pending = store.reorder([3]);
    expect(store.groups.map((g) => g.id)).toEqual([3, 1, 2]);
    answer({ groups: store.groups.map((g, i) => ({ ...g, position: i })), tags: {} });
    await pending;
  });

  it("puts the old order back when a reorder fails", async () => {
    const store = useTagGroupsStore();
    await store.fetch();
    reorderTagGroups.mockRejectedValue(new Error("boom"));
    expect(await store.reorder([1, 2])).toBe(false);
    expect(store.groups.map((g) => g.id)).toEqual([2, 1]);
  });
});

describe("the counts the map carries", () => {
  it("takes the library's ungrouped count and the known total, and keeps each group's tag_count", async () => {
    getTagGroups.mockResolvedValueOnce({
      groups: [{ id: 2, name: "Character", position: 0, tag_count: 40 }],
      tags: { "red hood": 2 },
      ungrouped_count: 312,
      all_count: 1284,
    });
    const store = useTagGroupsStore();
    await store.fetch();
    expect(store.ungroupedCount).toBe(312);
    expect(store.allCount).toBe(1284);
    expect(store.groups[0].tag_count).toBe(40);
  });

  it("holds no counts for a map that lacks them, and clears them on a library switch", async () => {
    const store = useTagGroupsStore();
    await store.fetch();
    expect(store.ungroupedCount).toBe(0);
    expect(store.allCount).toBe(0);
    getTagGroups.mockResolvedValueOnce({
      groups: [],
      tags: {},
      ungrouped_count: 5,
      all_count: 9,
    });
    await store.fetch({ force: true });
    expect(store.allCount).toBe(9);
    resetHandlers.at(-1)();
    expect(store.ungroupedCount).toBe(0);
    expect(store.allCount).toBe(0);
  });
});

describe("a library switch", () => {
  it("drops a read that started under the previous library", async () => {
    let answer;
    getTagGroups.mockReturnValueOnce(new Promise((r) => (answer = r)));
    const store = useTagGroupsStore();
    const old = store.fetch();
    resetHandlers.at(-1)();
    getTagGroups.mockResolvedValueOnce({ groups: [], tags: {} });
    const fresh = store.fetch();
    answer(MAP);
    await old;
    await fresh;
    expect(getTagGroups).toHaveBeenCalledTimes(2);
    expect(store.hasGroups).toBe(false);
    expect(store.groupFor("red hood")).toBeNull();
  });
});

describe("forced reads and late answers", () => {
  it("a forced read never joins a read that started before it was asked for", async () => {
    let first;
    getTagGroups.mockReturnValueOnce(new Promise((r) => (first = r)));
    const store = useTagGroupsStore();
    const early = store.fetch();
    const after = { ...MAP, tags: { forest: 2 } };
    getTagGroups.mockResolvedValueOnce(after);
    const forced = store.fetch({ force: true });
    first(MAP);
    await early;
    await forced;
    expect(getTagGroups).toHaveBeenCalledTimes(2);
    expect(store.groupFor("forest")?.name).toBe("Character");
    expect(store.groupFor("red hood")).toBeNull();
  });

  it("a move answered after a library switch does not file anything in the new one", async () => {
    let answer;
    assignTagsToGroup.mockReturnValueOnce(new Promise((r) => (answer = r)));
    const store = useTagGroupsStore();
    const moved = store.moveTags(["forest"], 1);
    resetHandlers.at(-1)();
    answer(MAP);
    expect(await moved).toBe(false);
    expect(store.hasGroups).toBe(false);
    expect(refresh).not.toHaveBeenCalled();
  });

  it("a reorder refused after a library switch does not put the old groups back", async () => {
    const store = useTagGroupsStore();
    await store.fetch();
    let refuse;
    reorderTagGroups.mockReturnValueOnce(new Promise((_, r) => (refuse = r)));
    const reordered = store.reorder([1, 2]);
    resetHandlers.at(-1)();
    refuse(new Error("gone"));
    expect(await reordered).toBe(false);
    expect(store.groups).toEqual([]);
  });

  it("bumps its revision when a move changes the map", async () => {
    const store = useTagGroupsStore();
    await store.fetch();
    assignTagsToGroup.mockResolvedValue(MAP);
    const before = store.revision;
    await store.moveTags(["forest"], 2);
    expect(store.revision).toBe(before + 1);
  });
});

describe("view choices", () => {
  it("default to List and Groups, and are remembered", () => {
    const store = useTagGroupsStore();
    expect(store.railView).toBe("list");
    expect(store.boardView).toBe("groups");
    store.setRailView("groups");
    store.setBoardView("health");
    store.toggleCollapsed(null);
    setActivePinia(createPinia());
    const again = useTagGroupsStore();
    expect(again.railView).toBe("groups");
    expect(again.boardView).toBe("health");
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
    // The server reads these as literal tag names, so they are not groups.
    expect(isGroupToken("__group:abc")).toBe(false);
    expect(isGroupToken("__group:")).toBe(false);
    expect(isGroupToken("__group:3 ")).toBe(false);
  });
});

describe("importing a tag file", () => {
  const PLAN = { filed: 3, groups_created: [], user_conflicts: 0 };
  const file = new File(["x"], "danbooru_tags.csv");

  it("a preview answers the plan and files nothing", async () => {
    const store = useTagGroupsStore();
    await store.fetch();
    importTagGroups.mockResolvedValue({ plan: PLAN });
    expect(await store.previewImport(file)).toEqual(PLAN);
    expect(importTagGroups).toHaveBeenCalledWith(file, { dryRun: true });
    expect(store.groupFor("red hood")?.name).toBe("Character");
    expect(refresh).not.toHaveBeenCalled();
  });

  it("a commit takes the map, bumps the revision, re-reads history and passes the choice on", async () => {
    const store = useTagGroupsStore();
    await store.fetch();
    importTagGroups.mockResolvedValue({ plan: PLAN, groups: MAP.groups, tags: { forest: 2 } });
    const before = store.revision;
    expect(await store.commitImport(file, true)).toEqual(PLAN);
    expect(importTagGroups).toHaveBeenCalledWith(file, { overwriteUser: true });
    expect(store.groupFor("forest")?.name).toBe("Character");
    expect(store.revision).toBe(before + 1);
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("a commit answered after a library switch files nothing in the new one", async () => {
    let answer;
    importTagGroups.mockReturnValueOnce(new Promise((r) => (answer = r)));
    const store = useTagGroupsStore();
    const imported = store.commitImport(file, false);
    resetHandlers.at(-1)();
    answer({ plan: PLAN, groups: MAP.groups, tags: { forest: 2 } });
    expect(await imported).toBeNull();
    expect(store.hasGroups).toBe(false);
    expect(refresh).not.toHaveBeenCalled();
  });

  it("a refused preview says why, through the server's detail, and returns nothing", async () => {
    const store = useTagGroupsStore();
    importTagGroups.mockRejectedValue(
      Object.assign(new Error("Request failed"), {
        response: { status: 400, data: { detail: "Missing columns: english" } },
      }),
    );
    expect(await store.previewImport(file)).toBeNull();
    const notice = useNoticeStore().notices.at(-1);
    expect(notice.level).toBe("error");
    expect(notice.text).toContain("Missing columns: english");
    expect(refresh).not.toHaveBeenCalled();
  });
});
