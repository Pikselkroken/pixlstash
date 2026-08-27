// useSidebarExpansion - the sidebar's shape survives a reload.
//
// The case that matters is the project tree: projects are expanded by default
// and a watcher expands each one the first time it is seen, so persisting the
// expanded set would be undone on every boot. What is stored is the collapsed
// set, and the tests below pin that a remembered collapse wins over the
// auto-expand while a brand new project still opens.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { nextTick } from "vue";

import {
  useSidebarExpansion,
  SIDEBAR_EXPANSION_STORAGE_KEY as KEY,
} from "./useSidebarExpansion";

/** The blob as it sits in localStorage, or null when nothing was written. */
function stored() {
  const raw = window.localStorage.getItem(KEY);
  return raw === null ? null : JSON.parse(raw);
}

function write(state) {
  window.localStorage.setItem(KEY, JSON.stringify({ v: 1, ...state }));
}

beforeEach(() => {
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe("defaults", () => {
  it("opens every section on a fresh install", () => {
    const s = useSidebarExpansion();
    expect(s.peopleSectionCollapsed.value).toBe(false);
    expect(s.setsSectionCollapsed.value).toBe(false);
    expect(s.referenceFoldersCollapsed.value).toBe(false);
    expect(s.importFoldersCollapsed.value).toBe(false);
    // The folder tree is the one that starts closed.
    expect(s.expandedFolderIds.value.size).toBe(0);
  });

  it("writes nothing until something is toggled", async () => {
    useSidebarExpansion();
    await nextTick();
    expect(stored()).toBeNull();
  });
});

describe("persistence", () => {
  it("stores a collapsed section and restores it", async () => {
    const first = useSidebarExpansion();
    first.togglePeopleSection();
    await nextTick();
    expect(stored().peopleCollapsed).toBe(true);

    const second = useSidebarExpansion();
    expect(second.peopleSectionCollapsed.value).toBe(true);
    expect(second.setsSectionCollapsed.value).toBe(false);
  });

  it("restores folder-tree keys of both kinds", async () => {
    const first = useSidebarExpansion();
    first.toggleFolderExpanded(7); // reference-folder id
    first.toggleFolderExpanded("/images/refs/portraits"); // subfolder path
    await nextTick();

    const second = useSidebarExpansion();
    expect(second.expandedFolderIds.value.has(7)).toBe(true);
    expect(second.expandedFolderIds.value.has("/images/refs/portraits")).toBe(
      true,
    );
  });

  it("drops a blob written by a different schema version", () => {
    window.localStorage.setItem(
      KEY,
      JSON.stringify({ v: 99, peopleCollapsed: true }),
    );
    expect(useSidebarExpansion().peopleSectionCollapsed.value).toBe(false);
  });

  it("falls back to defaults on unparseable state", () => {
    window.localStorage.setItem(KEY, "{not json");
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(useSidebarExpansion().peopleSectionCollapsed.value).toBe(false);
    expect(warn).toHaveBeenCalled();
  });

  it("ignores junk entries in a stored key list", () => {
    write({ collapsedProjectIds: [3, null, {}, "", "UNASSIGNED"] });
    const s = useSidebarExpansion();
    expect([...s.collapsedProjectIds.value]).toEqual([3, "UNASSIGNED"]);
  });
});

describe("project tree", () => {
  it("expands projects it has not seen before", () => {
    const s = useSidebarExpansion();
    s.syncProjectExpansion([1, 2]);
    expect([...s.expandedProjectIds.value].sort()).toEqual([1, 2]);
  });

  it("keeps a remembered collapse closed while new projects still open", () => {
    write({ collapsedProjectIds: [2] });
    const s = useSidebarExpansion();
    s.syncProjectExpansion([1, 2, 3]);
    expect(s.expandedProjectIds.value.has(1)).toBe(true);
    expect(s.expandedProjectIds.value.has(2)).toBe(false);
    expect(s.expandedProjectIds.value.has(3)).toBe(true);
  });

  it("does not re-expand a project collapsed during the session", () => {
    const s = useSidebarExpansion();
    s.syncProjectExpansion([1, 2]);
    s.toggleProjectExpanded(1);
    // A later fetch of the same list must not undo the click.
    s.syncProjectExpansion([1, 2]);
    expect(s.expandedProjectIds.value.has(1)).toBe(false);
  });

  it("records a collapse and clears it again on re-expand", async () => {
    const s = useSidebarExpansion();
    s.syncProjectExpansion([1]);
    s.toggleProjectExpanded(1);
    await nextTick();
    expect(stored().collapsedProjectIds).toEqual([1]);

    s.toggleProjectExpanded(1);
    await nextTick();
    expect(stored().collapsedProjectIds).toEqual([]);
  });

  it("forgets projects that no longer exist", async () => {
    write({ collapsedProjectIds: [1, 2], projectSetsCollapsed: [2] });
    const s = useSidebarExpansion();
    s.syncProjectExpansion([1]);
    await nextTick();
    expect(stored().collapsedProjectIds).toEqual([1]);
    expect(stored().projectSetsCollapsed).toEqual([]);
  });

  it("keeps stored ids while the project list is still empty", async () => {
    write({ collapsedProjectIds: [1, 2] });
    const s = useSidebarExpansion();
    // An empty list on boot means "not fetched yet" as often as "none exist".
    s.syncProjectExpansion([]);
    await nextTick();
    expect([...s.collapsedProjectIds.value]).toEqual([1, 2]);
  });

  it("remembers a collapsed People/Sets sub-section per project", async () => {
    const first = useSidebarExpansion();
    first.toggleProjectTreePeople(4);
    first.toggleProjectTreeSets(9);
    await nextTick();

    const second = useSidebarExpansion();
    expect(second.projectTreePeopleCollapsed.value.has(4)).toBe(true);
    expect(second.projectTreeSetsCollapsed.value.has(9)).toBe(true);
    expect(second.projectTreePeopleCollapsed.value.has(9)).toBe(false);
  });
});

describe("unavailable storage", () => {
  it("still toggles when localStorage throws", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.spyOn(window.localStorage.__proto__, "setItem").mockImplementation(
      () => {
        throw new Error("QuotaExceededError");
      },
    );
    const s = useSidebarExpansion();
    s.toggleSetsSection();
    await nextTick();
    expect(s.setsSectionCollapsed.value).toBe(true);
    expect(warn).toHaveBeenCalled();
  });
});
