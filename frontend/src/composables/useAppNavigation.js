import { MODEL_SHELF_ROUTES } from "../router/routeNames";
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
import { markEnd, markStart } from "../utils/perfMarks";

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
    // Times a sidebar click through to the DOM update it causes (nextTick),
    // i.e. click-to-visible-response for the app's most common navigation.
    markStart("pixlstash:interaction-navigate");
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
      markEnd("pixlstash:interaction-navigate");
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
    markEnd("pixlstash:interaction-navigate");
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
  // ROUTING - URL ↔ Store sync
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
      // A folder payload with no id of any kind - what an "About your
      // library" finding points at. It travels as `?path=` rather than being
      // dropped, and `useViewStore.parseFolderPath` reads it back.
      //
      // NOT the sidebar's subfolder case: `FolderTreeNode.vue` requires
      // `rfId`, so a subfolder click takes the ref-folder branch above and its
      // path is still lost from the URL. Fixing that is a change to the folder
      // tree's route restoration, not to this branch.
      pushAppRoute({
        name: "all-pictures",
        query: f.pathPrefix ? { path: f.pathPrefix } : {},
      });
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
  // be imported - the same destination, a second tab - so the sidebar's Models
  // entry stays the current page across both and no second destination lights.
  // A READ session is never showing it: the shelf lists the owner's machine and
  // every route behind it is owner-only, so mounting it would only fire requests
  // the credential can never satisfy (issue #1014). Gating the predicate rather
  // than the component keeps the decision in the one place that already answers
  // "is the shelf showing".
  const isModelsView = computed(
    () => !isReadOnly.value && MODEL_SHELF_ROUTES.includes(route.name),
  );

  // …and a pasted /models URL is bounced to the library rather than left on a
  // route that renders the grid under a Models heading. The sidebar row cannot
  // reach this: it is inert for a READ session, so the only way in is an
  // address bar.
  //
  // A watcher and not a router guard. The router's first navigation resolves at
  // mount, before `Root.vue` has fetched the session context, and for this
  // session nothing ever navigates a second time - so a guard would see "not
  // read-only" on exactly the boot it exists to catch, and never run again.
  //
  // It writes no selection or project state, so `useViewStore` remains the only
  // route→store watcher; this one only navigates, which is this file's job.
  //
  // `replace` and not `push`, so Back leaves the app rather than returning to
  // the bounce - and through the same token-preserving path every other
  // navigation here uses, because the ONLY session that reaches this line is
  // one whose credential lives in `?token=`. Dropping it would leave a share
  // visitor on a URL that 401s the moment they reload or bookmark it.
  // `/insights` and `/moves` bounce for the same reason and through the same
  // watcher: GET /insights and GET /moves/pending are both owner-only, so a
  // READ session that pasted either URL would mount a screen whose only
  // request is a guaranteed 403.
  watch(
    [isReadOnly, () => route.name],
    ([readOnly, name]) => {
      if (
        readOnly &&
        (MODEL_SHELF_ROUTES.includes(name) ||
          name === "insights" ||
          name === "moves")
      )
        replaceAppRoute({ name: "all-pictures" });
    },
    { immediate: true },
  );

  /** Open the model shelf. */
  function handleSelectModels() {
    pushAppRoute({ name: "models" });
  }

  // "About your library" is a destination for the same reason Duplicates is: it
  // shows findings rather than pictures, so it has no selection to express.
  //
  // Gated on `isReadOnly` exactly like the shelf, and for the reason in issue
  // #1014: GET /insights is owner-only, so mounting the screen for a READ
  // session would only fire a request the credential can never satisfy. The
  // predicate is gated rather than the component, so the one place that
  // answers "is this showing" stays the one place that decides.
  const isInsightsView = computed(
    () => !isReadOnly.value && route.name === "insights",
  );

  /** Open the read-only findings about this library. */
  function handleSelectInsights() {
    pushAppRoute({ name: "insights" });
  }

  // Moves is a destination for the same reason Insights is: it reports on the
  // library rather than expressing a selection within it, gated on
  // `isReadOnly` for the same reason (GET /moves/pending is owner-only,
  // issue #1014's pattern).
  const isMovesView = computed(
    () => !isReadOnly.value && route.name === "moves",
  );

  /** Open the reconciliation queue for moves made outside PixlStash. */
  function handleSelectMoves() {
    pushAppRoute({ name: "moves" });
  }

  /**
   * Act on one finding's button: open the tool it names, on the pictures it
   * counted. The `kind` vocabulary is the backend's (`routes/insights.py`).
   *
   * `settings` is not handled here - the settings dialog is not a route, so
   * App.vue takes that one straight to `openSettingsDialog`.
   *
   * @param {{kind: string, path?: string, folder_label?: string}} action
   */
  function handleInsightAction(action) {
    if (!action) return;
    if (
      action.kind === "unassigned_in_folder" ||
      action.kind === "unassigned_with_face"
    ) {
      const query = {};
      if (action.path) query.path = action.path;
      // The face half of the unnamed-faces finding: unassigned AND holding a
      // face is exactly the set that finding counted.
      if (action.kind === "unassigned_with_face") query.face = "with_face";
      pushAppRoute({
        name: "character",
        params: { id: UNASSIGNED_PICTURES_ID },
        query,
      });
      return;
    }
    if (action.kind === "duplicates_in_folder" && action.path) {
      handleSelectDuplicates({
        type: "folder",
        id: action.path,
        label: action.folder_label || action.path,
        icon: "mdi-folder-outline",
      });
      return;
    }
    // Two folders with no usable common ancestor: the whole queue, unscoped.
    if (action.kind === "duplicates") handleSelectDuplicates({});
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
    isInsightsView,
    isMovesView,
    handleSelectModels,
    handleSelectInsights,
    handleSelectMoves,
    handleInsightAction,
    handleSelectCharacter,
    handleSelectSet,
    handleSelectFolder,
    handleSearchAllPictures,
    handleSelectDuplicates,
    pushAppRoute,
    pushRouteForCurrentSelection,
  };
}
