import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { reactive, watch, nextTick } from "vue";

import { parseRouteView, useViewStore } from "./useViewStore";
import { useSelectionStore } from "./useSelectionStore";
import { useProjectStore } from "./useProjectStore";
import { useFilterStore } from "./useFilterStore";

// Route → view resolution is the Phase 3 seam that replaced App.vue's inline
// `applyRouteToStores`. These tests pin the URL shapes the router declares
// (router/index.js) plus the two behaviours that were bugs before: the ids
// fallback that makes multi-select accumulate, and folder routes NOT clearing
// the folder filter the sidebar owns.

function routeOf(name, params = {}, query = {}) {
  return { name, params, query };
}

beforeEach(() => {
  setActivePinia(createPinia());
});

describe("parseRouteView", () => {
  it("resolves the all-pictures route to the global all view", () => {
    expect(parseRouteView(routeOf("all-pictures"))).toMatchObject({
      projectViewMode: "global",
      selectedProjectId: null,
      selectedCharacter: "ALL",
      selectedCharacterIds: [],
      selectedSet: null,
      selectedSetIds: [],
      characterLabel: "All Pictures",
      clearFolderFilter: true,
    });
  });

  it("keeps the pseudo-ids as strings and numeric character ids as numbers", () => {
    expect(parseRouteView(routeOf("character", { id: "7" }))).toMatchObject({
      selectedCharacter: 7,
      selectedCharacterIds: [7],
      characterLabel: null,
    });
    expect(
      parseRouteView(routeOf("character", { id: "UNASSIGNED" })),
    ).toMatchObject({
      selectedCharacter: "UNASSIGNED",
      selectedCharacterIds: [],
      characterLabel: "Unassigned Pictures",
    });
    expect(parseRouteView(routeOf("scrapheap"))).toMatchObject({
      selectedCharacter: "SCRAPHEAP",
      characterLabel: "Scrapheap",
    });
  });

  it("falls back to the route's own id when there is no ?ids= query", () => {
    // Regression: falling back to [] cleared the multi-set after a single
    // select, so the next Ctrl/Cmd-click restarted from empty.
    expect(
      parseRouteView(routeOf("character", { id: "7" })).selectedCharacterIds,
    ).toEqual([7]);
    expect(parseRouteView(routeOf("set", { id: "3" })).selectedSetIds).toEqual([
      3,
    ]);
  });

  it("parses a multi-selection and its mode, dropping junk ids", () => {
    const view = parseRouteView(
      routeOf("character", { id: "7" }, { ids: "7,9,x,0,-2", mode: "union" }),
    );
    expect(view.selectedCharacterIds).toEqual([7, 9]);
    expect(view.characterMultiMode).toBe("union");
  });

  it("ignores mode/base on a single-entity route so the sticky mode survives", () => {
    const view = parseRouteView(
      routeOf("set", { id: "3" }, { mode: "difference", base: "3" }),
    );
    expect(view.setMultiMode).toBeNull();
    expect(view.setDifferenceBaseId).toBeNull();
  });

  it("parses a set difference base only when it is a real id", () => {
    const q = { ids: "3,4", mode: "difference" };
    expect(
      parseRouteView(routeOf("set", { id: "3" }, { ...q, base: "4" }))
        .setDifferenceBaseId,
    ).toBe(4);
    expect(
      parseRouteView(routeOf("set", { id: "3" }, { ...q, base: "nope" }))
        .setDifferenceBaseId,
    ).toBeNull();
  });

  it("scopes the project routes and rejects an unusable project id", () => {
    expect(parseRouteView(routeOf("project", { id: "12" }))).toMatchObject({
      projectViewMode: "project",
      selectedProjectId: 12,
      selectedCharacter: "ALL",
    });
    expect(
      parseRouteView(routeOf("project", { id: "nope" })).selectedProjectId,
    ).toBeNull();
    expect(
      parseRouteView(
        routeOf("project-character", { projectId: "12", id: "7" }),
      ),
    ).toMatchObject({
      projectViewMode: "project",
      selectedProjectId: 12,
      selectedCharacter: 7,
      // The project-character branch deliberately leaves the label alone.
      characterLabel: null,
    });
    expect(
      parseRouteView(routeOf("project-set", { projectId: "12", id: "3" })),
    ).toMatchObject({
      projectViewMode: "project",
      selectedProjectId: 12,
      selectedCharacter: null,
      selectedSet: 3,
      selectedSetIds: [3],
    });
  });

  it("does not clear the folder filter on folder routes, and yields the key", () => {
    // The sidebar owns the folder payload and emits select-folder once it has
    // loaded the folder; the route must not wipe what it just set.
    expect(parseRouteView(routeOf("ref-folder", { id: "5" }))).toMatchObject({
      clearFolderFilter: false,
      folderKey: "rf-5",
      selectedCharacter: "ALL",
      characterLabel: "All Pictures",
    });
    expect(
      parseRouteView(routeOf("import-folder", { id: "5" })).folderKey,
    ).toBe("if-5");
  });

  it("carries a folder path on any grid route, and names the folder by its leaf", () => {
    // `?path=` is the folder facet for folders that have no id: a subfolder,
    // and a folder an "About your library" finding points at. It rides on
    // every grid route for the same reason `?stack_state=` does, which is what
    // makes `/character/UNASSIGNED?path=…` - the unassigned pictures in ONE
    // folder - expressible at all.
    const onAll = parseRouteView(
      routeOf("all-pictures", {}, { path: "/home/me/library/_unsorted" }),
    );
    expect(onAll).toMatchObject({
      clearFolderFilter: false,
      folderFilter: {
        pathPrefix: "/home/me/library/_unsorted",
        label: "_unsorted",
      },
    });

    const onCharacter = parseRouteView(
      routeOf(
        "character",
        { id: "UNASSIGNED" },
        { path: "C:\\Users\\me\\Shoots" },
      ),
    );
    expect(onCharacter.selectedCharacter).toBe("UNASSIGNED");
    // A Windows path reaches a browser that is not on Windows, so the leaf is
    // split on both separators.
    expect(onCharacter.folderFilter.label).toBe("Shoots");

    // No path means the route says nothing about the folder facet, and the
    // ordinary clear-it rule stands.
    expect(parseRouteView(routeOf("all-pictures"))).toMatchObject({
      clearFolderFilter: true,
      folderFilter: null,
    });
    expect(
      parseRouteView(routeOf("all-pictures", {}, { path: "  " })).folderFilter,
    ).toBeNull();
  });

  it("reads the face facet additively, like the stack state", () => {
    // `/character/UNASSIGNED?face=with_face` is what the unnamed-faces finding
    // opens: unassigned means no face here is named, `with_face` means there
    // is one, and the pair is exactly the set the finding counted.
    expect(
      parseRouteView(
        routeOf("character", { id: "UNASSIGNED" }, { face: "with_face" }),
      ).faceFilter,
    ).toBe("with_face");
    // Additive: an absent or unrecognised value means "leave the filter store
    // alone", so navigating anywhere does not clear a filter the panel set.
    expect(parseRouteView(routeOf("all-pictures")).faceFilter).toBeNull();
    expect(
      parseRouteView(routeOf("all-pictures", {}, { face: "sideways" }))
        .faceFilter,
    ).toBeNull();
  });

  it("returns null for a route the grid is not driven from", () => {
    expect(parseRouteView(routeOf("something-else"))).toBeNull();
    expect(parseRouteView(undefined)).toBeNull();
  });
});

describe("useViewStore.applyRoute", () => {
  it("writes the parsed view into the selection and project stores", () => {
    const viewStore = useViewStore();
    const selection = useSelectionStore();
    const project = useProjectStore();
    selection.selectedFolderFilter = { label: "stale" };

    viewStore.applyRoute(
      routeOf(
        "set",
        { id: "3" },
        { ids: "3,4", mode: "difference", base: "4" },
      ),
    );

    expect(selection.selectedFolderFilter).toBeNull();
    expect(selection.selectedCharacter).toBeNull();
    expect(selection.selectedSet).toBe(3);
    expect(selection.selectedSetIds).toEqual([3, 4]);
    expect(selection.setMultiMode).toBe("difference");
    expect(selection.setDifferenceBaseId).toBe(4);
    expect(project.projectViewMode).toBe("global");
  });

  it("leaves the sidebar's folder filter alone on a folder route", () => {
    const viewStore = useViewStore();
    const selection = useSelectionStore();
    const filter = { referenceFolderId: 5, label: "Refs" };
    selection.selectedFolderFilter = filter;

    viewStore.applyRoute(routeOf("ref-folder", { id: "5" }));

    expect(selection.selectedFolderFilter).toStrictEqual(filter);
    expect(viewStore.activeFolderKey).toBe("rf-5");
  });

  it("writes the ?path= folder filter, and replaces a stale one", () => {
    const viewStore = useViewStore();
    const selection = useSelectionStore();
    selection.selectedFolderFilter = {
      pathPrefix: "/home/me/old",
      label: "old",
    };

    viewStore.applyRoute(
      routeOf("character", { id: "UNASSIGNED" }, { path: "/home/me/new" }),
    );

    expect(selection.selectedFolderFilter).toStrictEqual({
      pathPrefix: "/home/me/new",
      label: "new",
    });
    expect(selection.selectedCharacter).toBe("UNASSIGNED");

    // Re-applying the same path is inert: writing an equal-but-new object
    // every tick would refetch the grid on every route tick.
    const written = selection.selectedFolderFilter;
    viewStore.applyRoute(
      routeOf("character", { id: "UNASSIGNED" }, { path: "/home/me/new" }),
    );
    expect(selection.selectedFolderFilter).toBe(written);

    // And navigating away from it clears it, like any other grid route.
    viewStore.applyRoute(routeOf("all-pictures"));
    expect(selection.selectedFolderFilter).toBeNull();
  });

  it("lets a folder route's own payload win over ?path=", () => {
    // The sidebar owns a ref-folder's payload and sets it once the folder has
    // loaded. Overwriting it from `?path=` would drop `reference_folder_id`
    // from the grid query. Nothing pushes this combination today; the guard is
    // here so nothing can start to.
    const viewStore = useViewStore();
    const selection = useSelectionStore();
    const owned = {
      referenceFolderId: 5,
      pathPrefix: "/home/me/refs",
      label: "Refs",
    };
    selection.selectedFolderFilter = owned;

    viewStore.applyRoute(
      routeOf("ref-folder", { id: "5" }, { path: "/home/me/refs/sub" }),
    );

    expect(selection.selectedFolderFilter).toStrictEqual(owned);
  });

  it("applies the face facet without clearing it on the next route", () => {
    const viewStore = useViewStore();
    const filter = useFilterStore();

    viewStore.applyRoute(
      routeOf("character", { id: "UNASSIGNED" }, { face: "with_face" }),
    );
    expect(filter.faceBboxFilter).toBe("with_face");

    // Additive: a route that says nothing about it leaves it alone.
    viewStore.applyRoute(routeOf("all-pictures"));
    expect(filter.faceBboxFilter).toBe("with_face");
  });

  it("is idempotent: re-applying the same route rewrites nothing", () => {
    const viewStore = useViewStore();
    const selection = useSelectionStore();
    const route = routeOf(
      "character",
      { id: "7" },
      { ids: "7,9", mode: "union" },
    );
    viewStore.applyRoute(route);

    const seen = [];
    watch(
      () => [selection.selectedCharacter, selection.selectedCharacterIds],
      () => seen.push("changed"),
      { deep: true },
    );
    viewStore.applyRoute(route);
    viewStore.applyRoute(route);

    expect(seen).toEqual([]);
  });

  it("does not churn when a numeric id arrives as a string", () => {
    const viewStore = useViewStore();
    const selection = useSelectionStore();
    viewStore.applyRoute(routeOf("character", { id: "7" }));
    selection.selectedCharacter = "7";

    viewStore.applyRoute(routeOf("character", { id: "7" }));

    expect(selection.selectedCharacter).toBe("7");
  });

  it("ignores an unknown route entirely", () => {
    const viewStore = useViewStore();
    const selection = useSelectionStore();
    viewStore.applyRoute(routeOf("set", { id: "3" }));
    viewStore.applyRoute(routeOf("something-else"));

    expect(selection.selectedSetIds).toEqual([3]);
    expect(viewStore.activeFolderKey).toBeNull();
  });
});

// The only filter the route owns. It exists because the Duplicates queue-clear
// screen routes to All Pictures with the stacked filter applied, and that
// destination has to be reloadable rather than a state only one click can
// produce.
describe("the stack-state filter in the URL", () => {
  it("carries the filter into the store on a real navigation", () => {
    const viewStore = useViewStore();
    const filters = useFilterStore();
    viewStore.applyRoute(routeOf("all-pictures", {}, { stack_state: "stacked" }));
    expect(filters.stackStateFilter).toBe("stacked");
  });

  it("works on any grid route, not only All Pictures", () => {
    const viewStore = useViewStore();
    const filters = useFilterStore();
    viewStore.applyRoute(
      routeOf("set", { id: "3" }, { stack_state: "unstacked" }),
    );
    expect(filters.stackStateFilter).toBe("unstacked");
  });

  // ADDITIVE ONLY. Resetting on every route tick would silently clear a filter
  // the user set from the filter panel the moment they navigated anywhere,
  // which no other filter does.
  it("leaves the filter alone when the route says nothing about it", () => {
    const viewStore = useViewStore();
    const filters = useFilterStore();
    filters.stackStateFilter = "stacked";
    viewStore.applyRoute(routeOf("character", { id: "7" }));
    expect(filters.stackStateFilter).toBe("stacked");
  });

  it("ignores a value the grid cannot ask the server for", () => {
    const viewStore = useViewStore();
    const filters = useFilterStore();
    viewStore.applyRoute(routeOf("all-pictures", {}, { stack_state: "wat" }));
    expect(filters.stackStateFilter).toBe("all");
  });
});

describe("useViewStore.startRouteSync", () => {
  it("applies immediately and on every navigation, and replaces a prior watcher", async () => {
    const viewStore = useViewStore();
    const selection = useSelectionStore();

    const stop = vi.fn();
    const fakeWatch = vi.fn(() => stop);
    viewStore.startRouteSync(routeOf("character", { id: "7" }), {
      watch: fakeWatch,
    });
    expect(fakeWatch).toHaveBeenCalledTimes(1);
    expect(fakeWatch.mock.calls[0][2]).toEqual({ immediate: true, deep: true });

    // A second install (App.vue remounting) stops the first watcher.
    viewStore.startRouteSync(routeOf("character", { id: "7" }), {
      watch: fakeWatch,
    });
    expect(stop).toHaveBeenCalledTimes(1);

    // With the real `watch` and a reactive route (what vue-router hands us),
    // the store applies on install and follows every navigation after it.
    const live = reactive(routeOf("all-pictures"));
    viewStore.startRouteSync(live, { watch });
    expect(selection.selectedCharacter).toBe("ALL");
    Object.assign(live, routeOf("set", { id: "3" }));
    await nextTick();
    expect(selection.selectedSetIds).toEqual([3]);
  });
});
