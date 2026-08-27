// useViewStore.js - route → view resolution: the one place the app parses the
// URL into the selection/project state the grid renders.
//
// The route stays the SINGLE SOURCE OF TRUTH for what the grid shows (see
// frontend_architecture.md §2 "Key Design Principles" and the Phase 0 pin specs
// `route-as-truth.spec.js` / `stateless-tabs.spec.js`). This store is merely the
// route's parsed reflection: it never pushes a route and never decides
// navigation. Route *pushing* stays in App.vue (`pushAppRoute` /
// `pushRouteForCurrentSelection`), where the nav handlers live.
//
// The contract this file exists to hold:
//
//   * exactly ONE route watcher in the whole app, installed by
//     `startRouteSync` and called once from App.vue. It is the only writer of
//     route-derived selection/project state;
//   * parsing is a pure function (`parseRouteView`), so every URL shape is
//     unit-testable without a router, a grid, or a mounted App;
//   * applying is idempotent. Writing a value a ref already holds is a no-op in
//     Vue's reactivity, and the guards below additionally skip writes that
//     differ only by number-vs-string, so a route tick that changes nothing
//     re-renders nothing;
//   * a resource-scoped credential's PROJECT scope comes from the token, never
//     from the URL (`scopeProjectToSession`, issue #717). Parsing stays pure;
//     the narrowing happens in `applyRoute`, between the parse and the write.

import { computed, ref } from "vue";
import { defineStore } from "pinia";

import { useSelectionStore } from "./useSelectionStore";
import { useProjectStore } from "./useProjectStore";
import { useFilterStore } from "./useFilterStore";
import { sessionContext } from "../utils/apiClient";

export const ALL_PICTURES_ID = "ALL";
export const UNASSIGNED_PICTURES_ID = "UNASSIGNED";
export const SCRAPHEAP_PICTURES_ID = "SCRAPHEAP";

/**
 * Parse a `?ids=1,2,3` multi-selection list.
 *
 * With no `ids` query we fall back to the single route id (when it is a real
 * entity id, > 0). Falling back to `[]` would clear the multi-set after a single
 * select, so the next Ctrl/Cmd-click would start from empty and never
 * accumulate, the original cause of "multi-select works for sets, not people".
 *
 * @param {string|string[]|undefined} idsRaw `route.query.ids`
 * @param {number} fallbackId the route's own primary id (may be NaN)
 * @returns {number[]}
 */
function parseIds(idsRaw, fallbackId) {
  if (idsRaw) {
    return String(idsRaw)
      .split(",")
      .map(Number)
      .filter((id) => Number.isFinite(id) && id > 0);
  }
  return Number.isFinite(fallbackId) && fallbackId > 0 ? [fallbackId] : [];
}

// A multi mode (`?mode=union|intersection|difference`) only means something for
// a multi-selection; a single-entity route must not rewrite the sticky mode.
function parseMultiMode(ids, modeRaw) {
  return ids.length > 1 && modeRaw ? String(modeRaw) : null;
}

// `?base=<setId>`: the subtrahend of a set difference. Same rule as the mode.
function parseBaseId(ids, baseRaw) {
  if (ids.length <= 1 || !baseRaw) return null;
  const baseId = Number(baseRaw);
  return Number.isFinite(baseId) && baseId > 0 ? baseId : null;
}

// Character ids are a mixed space: numeric character ids plus the pseudo-ids
// "ALL" / "UNASSIGNED" / "SCRAPHEAP", which stay strings.
function parseCharacterId(raw) {
  const value = raw || ALL_PICTURES_ID;
  const num = Number(value);
  return Number.isFinite(num) ? num : String(value);
}

function parseProjectId(raw) {
  const projectId = Number(raw);
  return Number.isFinite(projectId) && projectId > 0 ? projectId : null;
}

/**
 * The stack-state filter a URL may carry (`?stack_state=stacked`).
 *
 * The only filter the route owns, and it is deliberately ADDITIVE: an absent or
 * unrecognised param resolves to `null`, which means "leave the filter store
 * alone". Resetting it on every route tick would silently clear a filter the
 * user set from the filter panel the moment they navigated anywhere.
 *
 * It exists because the Duplicates queue-clear screen routes to All Pictures
 * with the stacked filter applied, and that destination has to be reloadable
 * and Back-able rather than a state only one click can produce.
 *
 * @param {string|string[]|undefined} raw `route.query.stack_state`
 * @returns {string|null}
 */
function parseStackState(raw) {
  const value = Array.isArray(raw) ? raw[0] : raw;
  const text = value == null ? "" : String(value);
  return ["all", "stacked", "unstacked", "unresolved"].includes(text)
    ? text
    : null;
}

/**
 * `?path=<absolute folder>`: the folder facet for folders that have no id.
 *
 * A reference folder and an import folder each have a route of their own
 * (`/ref-folder/:id`, `/import-folder/:id`) and the sidebar owns their payload.
 * A folder an "About your library" finding points at has no id of any kind,
 * which is why this param exists.
 *
 * It rides on every grid route rather than only on `/`, for the same reason
 * `?stack_state=` does: the facet is not specific to one view shape, and
 * `/character/UNASSIGNED?path=…` - the unassigned pictures in one folder - is
 * exactly what a finding opens.
 *
 * **It does NOT yet fix the sidebar's subfolder selection.** A subfolder click
 * carries `referenceFolderId` (`FolderTreeNode.vue` requires it), so
 * `pushRouteForCurrentSelection` takes its ref-folder branch and the subfolder
 * path is still dropped from the URL. Closing that means teaching the
 * sidebar's `activeFolderKey` watcher to restore a subfolder rather than the
 * folder root, which is a change to the folder tree and not to this param.
 *
 * @param {string|string[]|undefined} raw `route.query.path`
 * @returns {{pathPrefix: string, label: string}|null}
 */
function parseFolderPath(raw) {
  const value = Array.isArray(raw) ? raw[0] : raw;
  const text = value == null ? "" : String(value).trim();
  if (!text) return null;
  // The label is the last component, which is what the owner calls the folder.
  // Split on both separators: the path comes from the server and may be a
  // Windows path while the browser showing it is not.
  const leaf = text
    .replace(/[/\\]+$/, "")
    .split(/[/\\]/)
    .pop();
  return { pathPrefix: text, label: leaf || text };
}

/**
 * `?face=with_face|without_face`: the face facet, on the same terms as
 * `?stack_state=`.
 *
 * ADDITIVE, like the stack state and unlike `?path=`: an absent or unrecognised
 * value means "leave `useFilterStore.faceBboxFilter` alone", so navigating
 * anywhere does not silently clear a filter the filter panel set.
 *
 * It exists because "About your library" opens its unnamed-faces finding on
 * `/character/UNASSIGNED?face=with_face` - unassigned means no face here is
 * named, `with_face` means there is one, and the pair is exactly the set the
 * finding counted. Without it the destination would be every unassigned
 * picture, most of which have no face at all.
 *
 * @param {string|string[]|undefined} raw `route.query.face`
 * @returns {string|null}
 */
function parseFaceFilter(raw) {
  const value = Array.isArray(raw) ? raw[0] : raw;
  const text = value == null ? "" : String(value);
  return ["with_face", "without_face"].includes(text) ? text : null;
}

function characterLabelFor(charId) {
  if (charId === ALL_PICTURES_ID) return "All Pictures";
  if (charId === UNASSIGNED_PICTURES_ID) return "Unassigned Pictures";
  return null;
}

/**
 * The neutral view every route branch starts from: the global All-Pictures
 * view. Branches below override only what their URL actually says.
 *
 * `null` fields mean "leave the store's current value alone" (sticky multi
 * modes, the difference base, the category label); everything else is written
 * on every route tick.
 */
function baseView(name) {
  return {
    name,
    projectViewMode: "global",
    selectedProjectId: null,
    selectedCharacter: ALL_PICTURES_ID,
    selectedCharacterIds: [],
    characterMultiMode: null,
    selectedSet: null,
    selectedSetIds: [],
    setMultiMode: null,
    setDifferenceBaseId: null,
    // Folder routes are the one exception: the sidebar owns the folder filter
    // payload and emits `select-folder` once it has loaded the folder, so the
    // route must not clear what the sidebar just set.
    clearFolderFilter: true,
    characterLabel: null,
    folderKey: null,
    // `{pathPrefix, label}` from `?path=`, or null. Read on every grid route.
    folderFilter: null,
    // `null` = "leave the filter store alone" (see `parseStackState`).
    stackState: null,
    faceFilter: null,
  };
}

/**
 * Resolve a route to the view it denotes. Pure: no store access, no side
 * effects. Returns `null` for a route this app does not drive the grid from.
 *
 * @param {{name: string, params: object, query: object}} route
 * @returns {object|null}
 */
export function parseRouteView(route) {
  const { name, params = {}, query = {} } = route || {};
  const view = baseView(name);
  // Read once, for every grid route: the filter is not specific to one view
  // shape, and a `/set/3?stack_state=stacked` link should work as well as
  // `/?stack_state=stacked`.
  view.stackState = parseStackState(query.stack_state);
  // Same reasoning, same placement: a facet that is not specific to one view
  // shape is read once, for every grid route.
  view.folderFilter = parseFolderPath(query.path);
  if (view.folderFilter) view.clearFolderFilter = false;
  view.faceFilter = parseFaceFilter(query.face);

  if (name === "all-pictures") {
    view.characterLabel = "All Pictures";
    return view;
  }

  if (name === "scrapheap") {
    view.selectedCharacter = SCRAPHEAP_PICTURES_ID;
    view.characterLabel = "Scrapheap";
    return view;
  }

  if (name === "character" || name === "project-character") {
    const charId = parseCharacterId(params.id);
    const ids = parseIds(query.ids, Number(params.id || ALL_PICTURES_ID));
    view.selectedCharacter = charId;
    view.selectedCharacterIds = ids;
    view.characterMultiMode = parseMultiMode(ids, query.mode);
    if (name === "project-character") {
      view.projectViewMode = "project";
      view.selectedProjectId = parseProjectId(params.projectId);
    } else {
      view.characterLabel = characterLabelFor(charId);
    }
    return view;
  }

  if (name === "set" || name === "project-set") {
    const ids = parseIds(query.ids, Number(params.id));
    view.selectedCharacter = null;
    view.selectedSet = ids[0] ?? null;
    view.selectedSetIds = ids;
    view.setMultiMode = parseMultiMode(ids, query.mode);
    view.setDifferenceBaseId = parseBaseId(ids, query.base);
    if (name === "project-set") {
      view.projectViewMode = "project";
      view.selectedProjectId = parseProjectId(params.projectId);
      view.characterLabel = "All Pictures";
    }
    return view;
  }

  if (name === "project") {
    view.projectViewMode = "project";
    view.selectedProjectId = parseProjectId(params.id);
    view.characterLabel = "All Pictures";
    return view;
  }

  if (name === "ref-folder" || name === "import-folder") {
    view.clearFolderFilter = false;
    view.characterLabel = "All Pictures";
    if (params.id) {
      view.folderKey =
        name === "ref-folder" ? `rf-${params.id}` : `if-${params.id}`;
    }
    return view;
  }

  return null;
}

// True array equality by numeric content. Avoids spurious reactive updates
// when the route applies the same ids a `handleSelect*` handler already set.
function sameNumIds(a, b) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (Number(a[i]) !== Number(b[i])) return false;
  }
  return true;
}

/**
 * Narrow a parsed view's PROJECT scope to what the current credential may ask
 * for (issue #717).
 *
 * A share link carries whatever pathname the owner happened to be standing on
 * when they minted it: Settings is a dialog rather than a route, so a token
 * created while browsing `/project/5` produces `/project/5?token=…`. The route
 * stays the single source of truth for what the grid shows, but only within
 * what the credential can see. A `character` / `picture_set` / `picture` token
 * has an empty visible-project set server-side, so `project_id` from such a
 * session is a guaranteed 403 and a silently empty grid.
 *
 * One rule, applied on every route tick rather than once at mount: a
 * resource-scoped credential takes its project scope from the TOKEN, never
 * from the URL. Doing it here rather than in App.vue's mount-time block keeps
 * this store the single writer of the grid's project scope, and survives a
 * later navigation (Back onto the shared link would otherwise re-break it).
 *
 * An owner session and a whole-library READ share both have no
 * `resource_type`, and both are left exactly as the URL says: the backend
 * places no project restriction on them, so narrowing here would be
 * over-blocking.
 *
 * @param {object|null} view the parsed view, mutated in place
 * @param {object|null} ctx `sessionContext` from the transport
 * @returns {object|null} the same view
 */
function scopeProjectToSession(view, ctx) {
  const resourceType = ctx?.resource_type;
  if (!view || !resourceType) return view;

  const tokenProjectId = parseProjectId(ctx.resource_id);
  if (resourceType === "project" && tokenProjectId != null) {
    view.projectViewMode = "project";
    view.selectedProjectId = tokenProjectId;
  } else {
    view.projectViewMode = "global";
    view.selectedProjectId = null;
  }
  return view;
}

/**
 * Write a parsed view into the selection/project stores.
 *
 * Every write is guarded so re-applying the same route is inert. The character
 * guard compares stringified values because the id space mixes numbers and the
 * "ALL"/"UNASSIGNED"/"SCRAPHEAP" pseudo-ids, and `5 !== "5"` would otherwise
 * churn the grid on every route tick.
 */
function applyView(view, selectionStore, projectStore, filterStore) {
  if (!view) return;

  if (view.clearFolderFilter) {
    selectionStore.selectedFolderFilter = null;
  } else if (view.folderFilter && !view.folderKey) {
    // A `?path=` route owns the folder facet - EXCEPT on a folder route, where
    // the sidebar owns the payload and sets it once the folder has loaded.
    // Without the `folderKey` guard, `/ref-folder/5?path=/sub` would overwrite
    // the sidebar's `{referenceFolderId, pathPrefix, label}` with a bare
    // `{pathPrefix, label}` and drop `reference_folder_id` from the grid
    // query. Nothing pushes that combination today; the guard is here so
    // nothing can start to.
    const current = selectionStore.selectedFolderFilter;
    if (current?.pathPrefix !== view.folderFilter.pathPrefix) {
      selectionStore.selectedFolderFilter = view.folderFilter;
    }
  }

  // Additive only: a route that says nothing about the stack filter leaves
  // whatever the filter panel set. See `parseStackState`.
  if (view.stackState && filterStore.stackStateFilter !== view.stackState) {
    filterStore.stackStateFilter = view.stackState;
  }

  // Same additive rule (see `parseFaceFilter`).
  if (view.faceFilter && filterStore.faceBboxFilter !== view.faceFilter) {
    filterStore.faceBboxFilter = view.faceFilter;
  }

  if (
    String(selectionStore.selectedCharacter) !== String(view.selectedCharacter)
  ) {
    selectionStore.selectedCharacter = view.selectedCharacter;
  }
  if (
    !sameNumIds(selectionStore.selectedCharacterIds, view.selectedCharacterIds)
  ) {
    selectionStore.selectedCharacterIds = view.selectedCharacterIds;
  }
  if (view.characterMultiMode) {
    selectionStore.characterMultiMode = view.characterMultiMode;
  }

  if (selectionStore.selectedSet !== view.selectedSet) {
    selectionStore.selectedSet = view.selectedSet;
  }
  if (!sameNumIds(selectionStore.selectedSetIds, view.selectedSetIds)) {
    selectionStore.selectedSetIds = view.selectedSetIds;
  }
  if (view.setMultiMode) selectionStore.setMultiMode = view.setMultiMode;
  if (view.setDifferenceBaseId != null) {
    selectionStore.setDifferenceBaseId = view.setDifferenceBaseId;
  }

  if (view.characterLabel) {
    selectionStore.lastSelectedCharacterLabel = view.characterLabel;
  }

  projectStore.projectViewMode = view.projectViewMode;
  projectStore.selectedProjectId = view.selectedProjectId;
}

export const useViewStore = defineStore("view", () => {
  const selectionStore = useSelectionStore();
  const projectStore = useProjectStore();
  const filterStore = useFilterStore();

  // The last parsed route. `null` for a route the grid is not driven from.
  const view = ref(null);

  // Sidebar folder highlight key ('rf-{id}' / 'if-{id}') for the current route,
  // so a deep link or a Back navigation lights the right folder row.
  const activeFolderKey = computed(() => view.value?.folderKey ?? null);

  let stopRouteWatch = null;

  /**
   * Parse `route` and apply it to the stores. Safe to call on every route tick.
   * @param {object} route a (reactive) route location
   */
  function applyRoute(route) {
    view.value = scopeProjectToSession(
      parseRouteView(route),
      sessionContext.value,
    );
    applyView(view.value, selectionStore, projectStore, filterStore);
  }

  /**
   * Install the app's one and only route watcher. Called once, from App.vue.
   *
   * `watch` is injected (same pattern as `useReviewRoute`) so this store stays
   * unit-testable, and so the watcher is created in the CALLER's effect scope:
   * it therefore dies with App.vue rather than outliving it on the store. A
   * second call (App.vue remounting after a re-login) replaces the first.
   *
   * @param {object} route reactive route (useRoute())
   * @param {{watch: Function}} vue injected `watch`
   * @returns {Function} stop handle
   */
  function startRouteSync(route, { watch }) {
    stopRouteWatch?.();
    stopRouteWatch = watch(route, () => applyRoute(route), {
      immediate: true,
      deep: true,
    });
    return stopRouteWatch;
  }

  return { view, activeFolderKey, applyRoute, startRouteSync };
});
