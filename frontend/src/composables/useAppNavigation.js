import { MODEL_SHELF_ROUTES, WORKFLOW_ROUTES } from "../router/routeNames";
import { computed, nextTick, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { isReadOnly } from "../utils/apiClient";
import { useSelectionStore } from "../stores/useSelectionStore";
import { useProjectStore } from "../stores/useProjectStore";
import { useSearchStore } from "../stores/useSearchStore";
import { useSortStore } from "../stores/useSortStore";
import { useFilterStore } from "../stores/useFilterStore";
import { useWsStore } from "../stores/useWsStore";
import {
  ALL_PICTURES_ID,
  SCRAPHEAP_PICTURES_ID,
  UNASSIGNED_PICTURES_ID,
} from "../stores/useViewStore";

/**
 * The app's navigation handlers: the sidebar's entry clicks, and the route
 * pushes that follow them.
 *
 * The direction matters. Reading the route back into the stores belongs to
 * useViewStore, which owns the app's single route watcher; everything here is
 * the other way round - a user gesture updates the selection stores and then
 * pushes the URL that expresses it. Keeping the two apart is what makes "the
 * route is the single source of truth for what the grid shows" hold.
 *
 * @param {object} hooks
 * @param {Function} hooks.onClearSearch - clear the active search (App.vue owns
 *   the search bar's own state).
 * @param {Function} hooks.onNavigated - called after a user-initiated
 *   navigation, so the mobile sidebar can close itself.
 */
export function useAppNavigation({ onClearSearch, onNavigated } = {}) {
  const route = useRoute();
  const router = useRouter();
  const selectionStore = useSelectionStore();
  const projectStore = useProjectStore();
  const searchStore = useSearchStore();
  const sortStore = useSortStore();
  const filterStore = useFilterStore();
  const wsStore = useWsStore();

  function SelectionPayload(payload) {
    if (payload && typeof payload === "object") {
      const ids = Array.isArray(payload.ids)
        ? payload.ids
            .map((id) => Number(id))
            .filter((id) => Number.isFinite(id) && id > 0)
        : [];
      return {
        id: payload.id ?? payload.value ?? null,
        label: payload.label ?? payload.name ?? null,
        ids,
        projectIds:
          payload.projectIds && typeof payload.projectIds === "object"
            ? payload.projectIds
            : {},
        projectContext: payload.projectContext ?? null,
      };
    }
    return {
      id: payload ?? null,
      label: null,
      ids: [],
      projectIds: {},
      projectContext: null,
    };
  }

  function clearSearchForCategoryChange() {
    if (
      (searchStore.searchQuery || "").trim() ||
      (searchStore.searchInput || "").trim()
    ) {
      onClearSearch?.();
    }
  }

  async function handleSelectCharacter(payload) {
    selectionStore.selectedFolderFilter = null;
    const {
      id: charId,
      label,
      ids,
      projectIds,
      projectContext,
    } = SelectionPayload(payload);
    projectStore.characterProjectIds = projectIds;
    if (projectContext) {
      projectStore.projectViewMode = projectContext.mode;
      projectStore.selectedProjectId = projectContext.projectId;
    }
    clearSearchForCategoryChange();
    if (charId == null) {
      selectionStore.selectedCharacter = null;
      await nextTick();
      return;
    }
    if (label) {
      selectionStore.lastSelectedCharacterLabel = label;
    } else if (charId === ALL_PICTURES_ID) {
      selectionStore.lastSelectedCharacterLabel = "All Pictures";
    } else if (charId === UNASSIGNED_PICTURES_ID) {
      selectionStore.lastSelectedCharacterLabel = "Unassigned Pictures";
    } else if (charId === SCRAPHEAP_PICTURES_ID) {
      selectionStore.lastSelectedCharacterLabel = "Scrapheap";
    }
    if (
      charId === SCRAPHEAP_PICTURES_ID &&
      sortStore.selectedSort === "LIKENESS_GROUPS"
    ) {
      sortStore.selectedSort = "DATE";
    }
    selectionStore.selectedCharacter = charId;
    selectionStore.selectedCharacterIds = ids.length ? ids : [];
    if (ids.length <= 1) {
      selectionStore.setCharacterMultiMode("union");
    }
    if (charId !== ALL_PICTURES_ID) {
      filterStore.unassignedOnlyFilter = false;
    }
    wsStore.clearPendingExternalImportIds();
    wsStore.clearSortChangedExternalIds();
    selectionStore.selectedSet = null;
    selectionStore.selectedSetIds = [];
    await nextTick();
    onNavigated?.();
    pushRouteForCurrentSelection();
  }

  async function handleSelectSet(payload) {
    selectionStore.selectedFolderFilter = null;
    const {
      id: setId,
      label,
      ids,
      projectIds,
      projectContext,
    } = SelectionPayload(payload);
    projectStore.setProjectIds = projectIds;
    if (projectContext) {
      projectStore.projectViewMode = projectContext.mode;
      projectStore.selectedProjectId = projectContext.projectId;
    }
    const names = payload && payload.names ? payload.names : {};
    clearSearchForCategoryChange();
    const nextIds = ids.length
      ? ids
      : setId != null
        ? [Number(setId)].filter((id) => Number.isFinite(id) && id > 0)
        : [];

    if (!nextIds.length) {
      const fallbackLabel =
        projectStore.projectViewMode === "project"
          ? "Project Pictures"
          : "All Pictures";
      selectionStore.selectedCharacter = ALL_PICTURES_ID;
      selectionStore.selectedCharacterIds = [];
      selectionStore.lastSelectedCharacterLabel = fallbackLabel;
      selectionStore.selectedSet = null;
      selectionStore.selectedSetIds = [];
      await nextTick();
      onNavigated?.();
      return;
    }
    if (label && nextIds.length === 1) {
      selectionStore.lastSelectedSetLabel = label;
    } else if (nextIds.length > 1) {
      selectionStore.lastSelectedSetLabel = `Set Overlap (${nextIds.length})`;
    }
    selectionStore.selectedSetIds = nextIds;
    selectionStore.selectedSet = nextIds[0];
    selectionStore.selectedCharacter = null;
    selectionStore.selectedCharacterIds = [];
    selectionStore.selectedSetNames = names;
    if (
      selectionStore.setDifferenceBaseId !== null &&
      !nextIds.includes(selectionStore.setDifferenceBaseId)
    ) {
      selectionStore.setSetDifferenceBaseId(null);
    }
    if (nextIds.length === 1) {
      selectionStore.setSetMultiMode("intersection");
      selectionStore.setSetDifferenceBaseId(null);
    }
    onNavigated?.();
    pushRouteForCurrentSelection();
  }

  function handleSearchAllPictures() {
    selectionStore.selectedCharacter = ALL_PICTURES_ID;
    selectionStore.selectedCharacterIds = [];
    selectionStore.selectedSet = null;
    selectionStore.selectedSetIds = [];
    selectionStore.selectedFolderFilter = null;
    selectionStore.lastSelectedCharacterLabel = "All Pictures";
    pushAppRoute({ name: "all-pictures" });
  }

  function handleSelectFolder(payload) {
    if (!payload) {
      selectionStore.selectedFolderFilter = null;
      pushAppRoute({ name: "all-pictures" });
      return;
    }
    selectionStore.selectedFolderFilter = payload;
    selectionStore.selectedCharacter = ALL_PICTURES_ID;
    selectionStore.selectedCharacterIds = [];
    selectionStore.selectedSet = null;
    selectionStore.selectedSetIds = [];
    pushRouteForCurrentSelection();
  }

  // ============================================================
  // ROUTING — URL ↔ Store sync
  // ============================================================

  /**
   * Carry the share token onto a route target. A share session's credential
   * lives in `?token=`, so a navigation that drops it leaves the visitor on a
   * URL that 401s on the next reload.
   */
  function withShareToken(target) {
    if (route.query.token) {
      target.query = { token: route.query.token, ...target.query };
    }
    return target;
  }

  /**
   * Push a route without cluttering history on duplicate navigations.
   * Swallows NavigationDuplicated errors (vue-router throws on same-route push).
   */
  function pushAppRoute(target) {
    router.push(withShareToken(target)).catch(() => {});
  }

  /** Same, replacing the current entry rather than adding one. */
  function replaceAppRoute(target) {
    router.replace(withShareToken(target)).catch(() => {});
  }

  /**
   * Build and push the correct app route for the current store selection state.
   * Called at the end of each user-initiated navigation handler so the URL
   * always reflects what the grid is showing.
   */
  function pushRouteForCurrentSelection() {
    const sel = selectionStore;
    const proj = projectStore;

    if (sel.selectedFolderFilter) {
      const f = sel.selectedFolderFilter;
      if (f.referenceFolderId != null) {
        pushAppRoute({
          name: "ref-folder",
          params: { id: String(f.referenceFolderId) },
        });
        return;
      }
      if (f.importFolderId != null) {
        pushAppRoute({
          name: "import-folder",
          params: { id: String(f.importFolderId) },
        });
        return;
      }
      // Path-based subfolder — no dedicated route; fall through to all-pictures.
      pushAppRoute({ name: "all-pictures" });
      return;
    }

    if (proj.projectViewMode === "project" && proj.selectedProjectId != null) {
      const projId = String(proj.selectedProjectId);
      if (sel.selectedSetIds.length > 0) {
        const query = {};
        if (sel.selectedSetIds.length > 1) {
          query.ids = sel.selectedSetIds.join(",");
          query.mode = sel.setMultiMode || "intersection";
          if (
            sel.setMultiMode === "difference" &&
            sel.setDifferenceBaseId != null
          ) {
            query.base = String(sel.setDifferenceBaseId);
          }
        }
        pushAppRoute({
          name: "project-set",
          params: { projectId: projId, id: String(sel.selectedSetIds[0]) },
          query,
        });
        return;
      }
      if (
        sel.selectedCharacter &&
        sel.selectedCharacter !== ALL_PICTURES_ID &&
        sel.selectedCharacter !== SCRAPHEAP_PICTURES_ID
      ) {
        const query = {};
        if (sel.selectedCharacterIds.length > 1) {
          query.ids = sel.selectedCharacterIds.join(",");
          query.mode = sel.characterMultiMode || "union";
        }
        pushAppRoute({
          name: "project-character",
          params: { projectId: projId, id: String(sel.selectedCharacter) },
          query,
        });
        return;
      }
      pushAppRoute({
        name: "project",
        params: { id: projId },
      });
      return;
    }

    if (sel.selectedSetIds.length > 0) {
      const query = {};
      if (sel.selectedSetIds.length > 1) {
        query.ids = sel.selectedSetIds.join(",");
        query.mode = sel.setMultiMode || "intersection";
        if (
          sel.setMultiMode === "difference" &&
          sel.setDifferenceBaseId != null
        ) {
          query.base = String(sel.setDifferenceBaseId);
        }
      }
      pushAppRoute({
        name: "set",
        params: { id: String(sel.selectedSetIds[0]) },
        query,
      });
      return;
    }

    if (sel.selectedCharacter === SCRAPHEAP_PICTURES_ID) {
      pushAppRoute({ name: "scrapheap" });
      return;
    }

    if (!sel.selectedCharacter || sel.selectedCharacter === ALL_PICTURES_ID) {
      pushAppRoute({ name: "all-pictures" });
      return;
    }

    const query = {};
    if (sel.selectedCharacterIds.length > 1) {
      query.ids = sel.selectedCharacterIds.join(",");
      query.mode = sel.characterMultiMode || "union";
    }
    pushAppRoute({
      name: "character",
      params: { id: String(sel.selectedCharacter) },
      query,
    });
  }

  // The Duplicates destination is addressed by route name, not by a sentinel in
  // the selection store: it shows no pictures, so it has no selection to express.
  const isDuplicatesView = computed(() => route.name === "duplicates");

  // Same reasoning for the model shelf: it lists files on this machine, not
  // pictures in the library, so it is a route rather than a selection.
  // Both of the shelf's views. `/models/runs` is the ai-toolkit runs waiting to
  // be imported — the same destination, a second tab — so the sidebar's Models
  // entry stays the current page across both and no second destination lights.
  // A READ session is never showing it: the shelf lists the owner's machine and
  // every route behind it is owner-only, so mounting it would only fire requests
  // the credential can never satisfy (issue #1014). Gating the predicate rather
  // than the component keeps the decision in the one place that already answers
  // "is the shelf showing".
  const isModelsView = computed(
    () => !isReadOnly.value && MODEL_SHELF_ROUTES.includes(route.name),
  );

  // The workflow library is a destination on the same reasoning: it lists
  // graphs the hub knows about, not pictures in the library, so it is a route
  // rather than a selection. Read-only for the same reason too — every
  // /workflows route is owner-only, so a READ session mounting it would only
  // fire requests its credential can never satisfy.
  const isWorkflowsView = computed(
    () => !isReadOnly.value && WORKFLOW_ROUTES.includes(route.name),
  );

  /** Open the workflow library. */
  function handleSelectWorkflows() {
    pushAppRoute({ name: "workflows" });
  }

  // …and a pasted /models URL is bounced to the library rather than left on a
  // route that renders the grid under a Models heading. The sidebar row cannot
  // reach this: it is inert for a READ session, so the only way in is an
  // address bar.
  //
  // A watcher and not a router guard. The router's first navigation resolves at
  // mount, before `Root.vue` has fetched the session context, and for this
  // session nothing ever navigates a second time — so a guard would see "not
  // read-only" on exactly the boot it exists to catch, and never run again.
  //
  // It writes no selection or project state, so `useViewStore` remains the only
  // route→store watcher; this one only navigates, which is this file's job.
  //
  // `replace` and not `push`, so Back leaves the app rather than returning to
  // the bounce — and through the same token-preserving path every other
  // navigation here uses, because the ONLY session that reaches this line is
  // one whose credential lives in `?token=`. Dropping it would leave a share
  // visitor on a URL that 401s the moment they reload or bookmark it.
  watch(
    [isReadOnly, () => route.name],
    ([readOnly, name]) => {
      if (
        readOnly &&
        (MODEL_SHELF_ROUTES.includes(name) || WORKFLOW_ROUTES.includes(name))
      )
        replaceAppRoute({ name: "all-pictures" });
    },
    { immediate: true },
  );

  /** Open the model shelf. */
  function handleSelectModels() {
    pushAppRoute({ name: "models" });
  }

  /**
   * Open the duplicate triage queue, optionally scoped to one collection object.
   *
   * The scope travels in the query rather than in a store, so a scoped queue is a
   * link the user can bookmark and reload, and a back-navigation out of one lands
   * somewhere that still makes sense.
   *
   * @param {Object} [scope]
   * @param {string} [scope.type] - "project", "set", "character" or "folder".
   * @param {number|string} [scope.id]
   * @param {string} [scope.label] - what the scope pill reads.
   * @param {string} [scope.icon] - the pill's mdi glyph.
   */
  function handleSelectDuplicates(scope = {}) {
    const query = {};
    if (scope.type && scope.type !== "library") {
      query.scope = scope.type;
      if (scope.id !== undefined && scope.id !== null)
        query.scope_id = scope.id;
      if (scope.label) query.scope_label = scope.label;
      if (scope.icon) query.scope_icon = scope.icon;
    }
    pushAppRoute({ name: "duplicates", query });
  }

  return {
    isDuplicatesView,
    isModelsView,
    isWorkflowsView,
    handleSelectModels,
    handleSelectWorkflows,
    handleSelectCharacter,
    handleSelectSet,
    handleSelectFolder,
    handleSearchAllPictures,
    handleSelectDuplicates,
    pushAppRoute,
    pushRouteForCurrentSelection,
  };
}
