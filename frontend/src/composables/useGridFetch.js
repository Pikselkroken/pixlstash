import { ref, computed, nextTick } from "vue";
import { isReadOnly } from "../utils/apiClient";
import {
  getPictureCount,
  streamPictures,
  getLikenessGroups,
  faceSearch,
  characterFaceSearch,
  likenessSearch,
  searchPictures,
  listPicturesByIds,
} from "../api/pictures";
import { getCharacterSummary } from "../api/characters";
import { getProjectSummary } from "../api/projects";
import { useEntityListsStore } from "../stores/useEntityListsStore";
import { getStackColor, getStackThreshold } from "../utils/utils.js";
import { cutFaceSuggestions } from "../utils/faceSuggestionCut.js";
import {
  getPictureId,
  PIL_IMAGE_EXTENSIONS,
  VIDEO_EXTENSIONS,
} from "../utils/media.js";
import { debounce } from "../utils/utils";
import { useFilterStore } from "../stores/useFilterStore";
import { useGridStore } from "../stores/useGridStore";
import { useSelectionStore } from "../stores/useSelectionStore";
import { useUserPrefsStore } from "../stores/useUserPrefsStore";
import { useSortStore } from "../stores/useSortStore";
import { useProjectStore } from "../stores/useProjectStore";
import { useSearchStore } from "../stores/useSearchStore";
import {
  ALL_PICTURES_ID,
  SCRAPHEAP_PICTURES_ID,
  UNASSIGNED_PICTURES_ID,
} from "../stores/useViewStore";

const LIKENESS_GROUPS_SORT_KEY = "LIKENESS_GROUPS";

/**
 * Manages grid image fetching: fetch state, URL query building, and the
 * debounced fetch trigger.
 *
 * The filter facets of the query and the grid geometry come from the stores
 * directly (Phase 3);
 * `props` still supplies the selection/sort/project fields that have not been
 * migrated yet.
 *
 * @param {Object} deps - Reactive state from other composables / ImageGrid
 * @param {Object} props - Component props
 * @param {Object} callbacks - Functions provided by ImageGrid or other composables
 */
export function useGridFetch(
  {
    allGridImages,
    lastFetchedGridImages,
    scrollWrapper,
    preserveScrollOnNextFetch,
    pendingScrollTop,
    overlayOpen,
    pendingGridImages,
    pendingOverlayGridRefresh,
    visibleStart,
    visibleEnd,
    divisibleViewWindow,
    initialRender,
    rowHeight,
    sharedPictureIds,
    guestConsentState,
    guestSessionId,
    highlightNextFetch,
    hasLoadedOnce,
    previousImageIds,
    normalizedSelectedCharacterIds,
    normalizedSelectedSetIds,
    hasSetSelection,
    isSetOverlapView,
    isMultiCharacterView,
    primarySelectedSetId,
    smartScoreProgress,
    exportProgress,
    reverseImageSearchPictureIds,
    faceLikenessSearchFaceId,
    faceSearchCharacter,
    faceSearchThreshold,
    faceSearchMinRefs,
    faceSearchRanked,
  },
  props,
  {
    collapseStackImages,
    mapGridImages,
    syncExpandAllStacksFromFetchedImages,
    refreshExpandedStacksAfterFetch,
    resetThumbnailState,
    triggerNewImageHighlight,
    updateVisibleThumbnails,
    fetchThumbnailsBatch,
    maybeRefreshOverlayForComfyui,
    startSmartScoreProgress,
    completeSmartScoreProgress,
    onGridFetchStart,
    onGridVisibleMetadataReady,
    onGridFetchDone,
  },
) {
  const sortStore = useSortStore();
  const projectStore = useProjectStore();
  const searchStore = useSearchStore();
  const filterStore = useFilterStore();
  const gridStore = useGridStore();
  const selectionStore = useSelectionStore();
  const userPrefsStore = useUserPrefsStore();

  // The three folder facets are one selection in the sidebar; the query wants
  // them as separate params.
  const referenceFolderIdFilter = computed(
    () => selectionStore.selectedFolderFilter?.referenceFolderId ?? null,
  );
  const filePathPrefixFilter = computed(
    () => selectionStore.selectedFolderFilter?.pathPrefix ?? null,
  );
  const importSourceFolderFilter = computed(
    () => selectionStore.selectedFolderFilter?.importSourceFolder ?? null,
  );

  // ============================================================
  // GRID FETCH STATE
  // ============================================================
  const imagesLoading = ref(false);
  const imagesError = ref(null);
  const totalAllPicturesCount = ref(0);
  // Whether the count above is an ANSWER or still the initial zero. The catch
  // below swallows a failure and leaves the ref at 0, which reads as "the
  // library is empty" - true for a fresh install and false for a share session
  // the summary route refuses, a backend restart, or any timeout. Anything that
  // acts on emptiness has to know which zero it is looking at.
  const totalAllPicturesCountLoaded = ref(false);
  const totalCurrentCategoryCount = ref(0);
  const gridReady = ref(false);
  const gridLoadEpoch = ref(0);
  const lastFetchKey = ref("");
  const lastFetchError = ref({ key: "", at: 0 });
  const lastFetchSuccess = ref({ key: "", at: 0 });
  const smartScoreLoadingVisible = computed(
    () =>
      !!getActiveSortKey() &&
      smartScoreProgress.visible &&
      !exportProgress.visible,
  );

  // ============================================================
  // GRID FETCH FUNCTIONS
  // ============================================================
  function getNowMs() {
    return typeof performance !== "undefined" ? performance.now() : Date.now();
  }

  function getActiveSortKey() {
    if (typeof sortStore.selectedSort !== "string") return "";
    return sortStore.selectedSort.trim().toUpperCase();
  }

  function buildGridFetchKey() {
    const selectedSetIds = Array.isArray(selectionStore.selectedSetIds)
      ? selectionStore.selectedSetIds
          .map((id) => Number(id))
          .filter((id) => Number.isFinite(id) && id > 0)
          .sort((a, b) => a - b)
      : [];
    const selectedCharacterIds = normalizedSelectedCharacterIds.value;
    return JSON.stringify({
      selectedCharacter: selectionStore.selectedCharacter ?? null,
      selectedCharacterIds,
      isMultiCharacterView: selectedCharacterIds.length > 1,
      characterMultiMode:
        selectedCharacterIds.length > 1
          ? (selectionStore.characterMultiMode ?? "union")
          : null,
      selectedSet: selectionStore.selectedSet ?? null,
      selectedSetIds,
      isSetOverlapView: selectedSetIds.length > 1,
      setMultiMode:
        selectedSetIds.length > 1
          ? (selectionStore.setMultiMode ?? "intersection")
          : null,
      setDifferenceBaseId:
        selectedSetIds.length > 1 &&
        selectionStore.setMultiMode === "difference"
          ? (selectionStore.setDifferenceBaseId ?? null)
          : null,
      projectViewMode: projectStore.projectViewMode ?? "global",
      selectedProjectId: projectStore.selectedProjectId ?? null,
      searchQuery: searchStore.searchQuery ?? "",
      selectedSort: sortStore.selectedSort ?? "",
      selectedDescending: sortStore.selectedDescending ?? null,
      stackThreshold: sortStore.stackThreshold ?? null,
      mediaTypeFilter: filterStore.mediaTypeFilter ?? "all",
      impossibleSources: filterStore.impossibleSources ?? [],
      // Changes which pictures the grid shows, so an unforced fetch must not
      // early-return as a no-op against the previous state's key.
      stackStateFilter: filterStore.stackStateFilter ?? "all",
      similarityCharacter: sortStore.selectedSimilarityCharacter ?? null,
      comfyuiModelFilter: filterStore.comfyuiModelFilter ?? [],
      comfyuiLoraFilter: filterStore.comfyuiLoraFilter ?? [],
      referenceFolderIdFilter: referenceFolderIdFilter.value ?? null,
      filePathPrefixFilter: filePathPrefixFilter.value ?? null,
      importSourceFolderFilter: importSourceFolderFilter.value ?? null,
      unassignedOnlyFilter: filterStore.unassignedOnlyFilter ?? false,
      applyTagFilter: userPrefsStore.applyTagFilter ?? false,
      reverseImageSearchPictureIds: reverseImageSearchPictureIds?.value ?? [],
      faceLikenessSearchFaceId: faceLikenessSearchFaceId?.value ?? null,
      faceSearchCharacterId: faceSearchCharacter?.value?.id ?? null,
      // Both suggestion knobs belong in the key: moving either changes which
      // pictures the grid shows, so a fetch that early-returns as a no-op would
      // leave the grid disagreeing with the count in the bar. The rebuild costs
      // no network call - the ranked list and its rows are both cached.
      faceSearchThreshold: faceSearchCharacter?.value
        ? (faceSearchThreshold?.value ?? null)
        : null,
      faceSearchMinRefs: faceSearchCharacter?.value
        ? (faceSearchMinRefs?.value ?? null)
        : null,
    });
  }

  function _appendSelectionParams(params) {
    if (hasSetSelection.value) {
      if (isSetOverlapView.value) {
        for (const setId of normalizedSelectedSetIds.value) {
          params.append("set_ids", String(setId));
        }
        params.append(
          "set_mode",
          selectionStore.setMultiMode ?? "intersection",
        );
        if (
          selectionStore.setMultiMode === "difference" &&
          selectionStore.setDifferenceBaseId != null
        ) {
          params.append(
            "base_set_id",
            String(selectionStore.setDifferenceBaseId),
          );
        }
        if (projectStore.projectViewMode === "project") {
          // Derive effective project_id from per-set data; skip when sets span multiple projects.
          const pidSet = new Set(
            normalizedSelectedSetIds.value.map(
              (id) => projectStore.setProjectIds?.[id] ?? null,
            ),
          );
          if (pidSet.size === 1) {
            const pid = [...pidSet][0];
            params.append("project_id", pid != null ? pid : "UNASSIGNED");
          }
        }
      } else if (primarySelectedSetId.value != null) {
        params.append("set_id", String(primarySelectedSetId.value));
        if (projectStore.projectViewMode === "project") {
          params.append(
            "project_id",
            projectStore.selectedProjectId != null
              ? projectStore.selectedProjectId
              : "UNASSIGNED",
          );
        }
      }
    } else if (isMultiCharacterView.value) {
      for (const charId of normalizedSelectedCharacterIds.value) {
        params.append("character_ids", String(charId));
      }
      params.append(
        "character_mode",
        selectionStore.characterMultiMode ?? "union",
      );
      if (projectStore.projectViewMode === "project") {
        // Derive effective project_id from per-character data; if all chars share
        // the same project use it, if they span multiple projects skip the filter.
        const pidSet = new Set(
          normalizedSelectedCharacterIds.value.map(
            (id) => projectStore.characterProjectIds?.[id] ?? null,
          ),
        );
        if (pidSet.size === 1) {
          const pid = [...pidSet][0];
          params.append("project_id", pid != null ? pid : "UNASSIGNED");
        }
      }
    } else if (
      selectionStore.selectedCharacter !== undefined &&
      selectionStore.selectedCharacter !== null &&
      selectionStore.selectedCharacter !== "" &&
      selectionStore.selectedCharacter !== ALL_PICTURES_ID
    ) {
      if (selectionStore.selectedCharacter === String(SCRAPHEAP_PICTURES_ID)) {
        params.append("only_deleted", "true");
      } else {
        params.append("character_id", selectionStore.selectedCharacter);
        if (projectStore.projectViewMode === "project") {
          params.append(
            "project_id",
            projectStore.selectedProjectId != null
              ? projectStore.selectedProjectId
              : "UNASSIGNED",
          );
        }
      }
    } else if (
      selectionStore.selectedCharacter === ALL_PICTURES_ID &&
      filterStore.unassignedOnlyFilter
    ) {
      params.append("character_id", UNASSIGNED_PICTURES_ID);
      if (projectStore.projectViewMode === "project") {
        params.append(
          "project_id",
          projectStore.selectedProjectId != null
            ? projectStore.selectedProjectId
            : "UNASSIGNED",
        );
      }
    } else if (
      selectionStore.selectedCharacter === ALL_PICTURES_ID &&
      projectStore.projectViewMode === "project"
    ) {
      params.append(
        "project_id",
        projectStore.selectedProjectId != null
          ? projectStore.selectedProjectId
          : "UNASSIGNED",
      );
    }
  }

  function _appendMediaTypeParams(params) {
    if (filterStore.mediaTypeFilter === "images") {
      for (const ext of PIL_IMAGE_EXTENSIONS) {
        params.append("format", ext.toUpperCase());
      }
    } else if (filterStore.mediaTypeFilter === "videos") {
      for (const ext of VIDEO_EXTENSIONS) {
        params.append("format", ext.toUpperCase());
      }
    }
  }

  function buildPictureIdsQueryParams() {
    const params = new URLSearchParams();
    _appendSelectionParams(params);
    if (
      sortStore.selectedSort === "CHARACTER_LIKENESS" &&
      sortStore.selectedSimilarityCharacter
    ) {
      params.append(
        "reference_character_id",
        sortStore.selectedSimilarityCharacter,
      );
    }
    if (searchStore.searchQuery && searchStore.searchQuery.trim()) {
      params.append("query", searchStore.searchQuery.trim());
    } else {
      if (sortStore.selectedSort && sortStore.selectedSort.trim()) {
        params.append("sort", sortStore.selectedSort.trim());
      }
      if (typeof sortStore.selectedDescending === "boolean") {
        params.append(
          "descending",
          sortStore.selectedDescending ? "true" : "false",
        );
      } else {
        console.warn(
          "[ImageGrid.vue] selectedDescending is not boolean, skipping param. Type:",
          typeof sortStore.selectedDescending,
        );
      }
    }
    params.append("fields", "grid");
    _appendMediaTypeParams(params);
    (filterStore.comfyuiModelFilter || []).forEach((m) =>
      params.append("comfyui_model", m),
    );
    (filterStore.comfyuiLoraFilter || []).forEach((l) =>
      params.append("comfyui_lora", l),
    );
    if (filterStore.minScoreFilter != null) {
      params.append("min_score", filterStore.minScoreFilter);
    }
    if (filterStore.maxScoreFilter != null) {
      params.append("max_score", filterStore.maxScoreFilter);
    }
    if (filterStore.unscoredOnlyFilter) {
      params.append("unscored", "1");
    }
    if (filterStore.smartScoreBucketFilter != null) {
      params.append("smart_score_bucket", filterStore.smartScoreBucketFilter);
    }
    if (filterStore.resolutionBucketFilter != null) {
      params.append("resolution_bucket", filterStore.resolutionBucketFilter);
    }
    (filterStore.tagFilter || []).forEach((t) => params.append("tag", t));
    (filterStore.tagRejectedFilter || []).forEach((t) =>
      params.append("rejected_tag", t),
    );
    (filterStore.tagConfidenceAboveFilter || []).forEach((e) =>
      params.append("tag_confidence_above", e),
    );
    (filterStore.tagConfidenceBelowFilter || []).forEach((e) =>
      params.append("tag_confidence_below", e),
    );
    if (userPrefsStore.applyTagFilter) {
      params.append("apply_tag_filter", "true");
    }
    if (referenceFolderIdFilter.value != null) {
      params.append(
        "reference_folder_id",
        String(referenceFolderIdFilter.value),
      );
    }
    if (filePathPrefixFilter.value != null) {
      params.append("file_path_prefix", filePathPrefixFilter.value);
    }
    if (importSourceFolderFilter.value != null) {
      params.append("import_source_folder", importSourceFolderFilter.value);
    }
    if (filterStore.faceBboxFilter != null) {
      params.append("face_filter", filterStore.faceBboxFilter);
    }
    (filterStore.impossibleSources || []).forEach((s) =>
      params.append("impossible_tag_source", s),
    );
    // "all" is the absence of the filter, so it is expressed by omission rather
    // than by a sentinel the backend would have to know a second spelling for.
    if (
      filterStore.stackStateFilter &&
      filterStore.stackStateFilter !== "all"
    ) {
      params.append("stack_state", filterStore.stackStateFilter);
    }
    if (filterStore.sharedOnlyFilter) {
      params.append("shared_only", "true");
    }
    // For rejected-consent guests: pass the in-memory session ID so the backend
    // can overlay their scores for the current page session (no cookie available).
    if (
      isReadOnly.value &&
      guestConsentState.value === "rejected" &&
      guestSessionId.value
    ) {
      params.append("guest_session_id", guestSessionId.value);
    }
    return params.toString();
  }

  function buildLikenessGroupQueryParams() {
    const params = new URLSearchParams();
    _appendSelectionParams(params);
    _appendMediaTypeParams(params);
    (filterStore.comfyuiModelFilter || []).forEach((m) =>
      params.append("comfyui_model", m),
    );
    (filterStore.comfyuiLoraFilter || []).forEach((l) =>
      params.append("comfyui_lora", l),
    );
    if (filterStore.minScoreFilter != null) {
      params.append("min_score", filterStore.minScoreFilter);
    }
    if (filterStore.maxScoreFilter != null) {
      params.append("max_score", filterStore.maxScoreFilter);
    }
    if (filterStore.unscoredOnlyFilter) {
      params.append("unscored", "1");
    }
    if (filterStore.smartScoreBucketFilter != null) {
      params.append("smart_score_bucket", filterStore.smartScoreBucketFilter);
    }
    if (filterStore.resolutionBucketFilter != null) {
      params.append("resolution_bucket", filterStore.resolutionBucketFilter);
    }
    (filterStore.tagFilter || []).forEach((t) => params.append("tag", t));
    (filterStore.tagRejectedFilter || []).forEach((t) =>
      params.append("rejected_tag", t),
    );
    (filterStore.tagConfidenceAboveFilter || []).forEach((e) =>
      params.append("tag_confidence_above", e),
    );
    (filterStore.tagConfidenceBelowFilter || []).forEach((e) =>
      params.append("tag_confidence_below", e),
    );
    if (filterStore.faceBboxFilter != null) {
      params.append("face_filter", filterStore.faceBboxFilter);
    }
    (filterStore.impossibleSources || []).forEach((s) =>
      params.append("impossible_tag_source", s),
    );
    // "all" is the absence of the filter, so it is expressed by omission rather
    // than by a sentinel the backend would have to know a second spelling for.
    if (
      filterStore.stackStateFilter &&
      filterStore.stackStateFilter !== "all"
    ) {
      params.append("stack_state", filterStore.stackStateFilter);
    }
    if (userPrefsStore.applyTagFilter) {
      params.append("apply_tag_filter", "true");
    }
    if (filterStore.sharedOnlyFilter) {
      params.append("shared_only", "true");
    }
    return params.toString();
  }

  // ============================================================
  // GRID FETCH
  // ============================================================
  async function fetchAllGridImages(options = {}) {
    const force = options?.force === true;
    const activeSortKey = getActiveSortKey();
    const isSortedFetch = !!activeSortKey;
    const fetchStartedAt = getNowMs();
    let fetchMode = "default";
    let fetchSucceeded = false;
    let fetchError = null;
    let sortedFetchStartedAt = 0;
    const fetchPhaseTimings = {
      countMs: null,
      placeholderMs: null,
      firstBatchMs: null,
      tailBatchMs: null,
      backgroundTotalMs: 0,
      backgroundNetworkTotalMs: 0,
      backgroundUiTotalMs: 0,
      backgroundSlowestBatchMs: 0,
      backgroundSlowestNetworkBatchMs: 0,
      backgroundSlowestUiBatchMs: 0,
      backgroundBatchCount: 0,
      postProcessMs: null,
    };
    // Capture scroll-preservation intent *synchronously* before any await so
    // that it is not affected by the gridVersion watcher clearing it later.
    const fetchStartedWithPreserveScroll = preserveScrollOnNextFetch.value;
    if (
      fetchStartedWithPreserveScroll &&
      pendingScrollTop.value === null &&
      scrollWrapper.value
    ) {
      pendingScrollTop.value = scrollWrapper.value.scrollTop;
    }
    const fetchKey = buildGridFetchKey();
    const now = Date.now();
    if (!force && imagesLoading.value && lastFetchKey.value === fetchKey) {
      const lastActivity = Math.max(
        lastFetchSuccess.value.at || 0,
        lastFetchError.value.at || 0,
      );
      if (now - lastActivity < 2500) {
        return;
      }
      imagesLoading.value = false;
    }
    if (
      !force &&
      lastFetchSuccess.value.key === fetchKey &&
      now - lastFetchSuccess.value.at < 1200
    ) {
      return;
    }
    if (
      !force &&
      lastFetchError.value.key === fetchKey &&
      now - lastFetchError.value.at < 2500
    ) {
      return;
    }
    lastFetchKey.value = fetchKey;
    const loadId = (gridLoadEpoch.value += 1);
    if (typeof onGridFetchStart === "function") {
      onGridFetchStart({
        loadId,
        fetchKey,
        force,
        selectedSort: sortStore.selectedSort ?? null,
        selectedCharacter: selectionStore.selectedCharacter ?? null,
        selectedSet: selectionStore.selectedSet ?? null,
        visibleStart: visibleStart.value,
        visibleEnd: visibleEnd.value,
      });
    }
    gridReady.value = false;
    imagesLoading.value = true;
    imagesError.value = null;
    if (isSortedFetch && options?.showProgress === true) {
      sortedFetchStartedAt = getNowMs();
      startSmartScoreProgress(loadId, activeSortKey);
    }
    const requestId = Date.now();
    fetchAllGridImages.lastRequestId = requestId;
    try {
      let images = [];

      const _hasSearch = !!searchStore.searchQuery?.trim();
      const _isLikenessSort =
        sortStore.selectedSort === LIKENESS_GROUPS_SORT_KEY;
      const _hasReverseImageSearch =
        !_hasSearch && !!reverseImageSearchPictureIds?.value?.length;
      const _hasCharacterFaceSearch =
        !_hasSearch &&
        !_hasReverseImageSearch &&
        !!faceSearchCharacter?.value?.id;
      const _hasFaceLikenessSearch =
        !_hasSearch &&
        !_hasReverseImageSearch &&
        !_hasCharacterFaceSearch &&
        !!faceLikenessSearchFaceId?.value;

      if (_isLikenessSort) {
        fetchMode = "likeness-groups";
        const threshold = getStackThreshold(sortStore.stackThreshold);
        const likenessGroupParams = buildLikenessGroupQueryParams();
        const data = await getLikenessGroups(threshold, likenessGroupParams);
        if (fetchAllGridImages.lastRequestId !== requestId) {
          if (isSortedFetch && options?.showProgress === true)
            completeSmartScoreProgress(loadId, 0, false);
          return;
        }
        const likenessGroupImages = Array.isArray(data) ? data : [];
        images = likenessGroupImages.map((img) => {
          const stackIndex =
            typeof img.stack_index === "number"
              ? img.stack_index
              : typeof img.stackIndex === "number"
                ? img.stackIndex
                : null;
          return {
            ...img,
            stackIndex,
            stackColor:
              typeof stackIndex === "number" ? getStackColor(stackIndex) : null,
          };
        });
      } else if (_hasCharacterFaceSearch) {
        fetchMode = "character-face-search";
        // "Suggest more pictures of <person>" (#636): query with the character's
        // reference faces, and let BOTH suggestion sliders re-cut the SAME
        // ranked list. The ranked refs and their picture rows are both cached
        // against the character id, so dragging either costs no round trip,
        // which is the difference between a slider that feels live and one that
        // stutters. That is also why the reference-agreement knob is served by
        // `reference_likeness` on each match rather than by re-querying with a
        // different combine mode: a server-side k-of-n would put a round trip
        // under a drag. Only a change of character (or an explicit force)
        // refetches.
        const character = faceSearchCharacter.value;
        const cached = faceSearchRanked?.value;
        let ranked =
          !force && cached?.characterId === character.id
            ? cached.matches
            : null;
        if (!ranked) {
          let raw;
          try {
            raw = await characterFaceSearch(character.id);
          } catch (error) {
            error.gridFetchPhase = "character-face-search-request";
            throw error;
          }
          if (fetchAllGridImages.lastRequestId !== requestId) {
            if (isSortedFetch && options?.showProgress === true)
              completeSmartScoreProgress(loadId, 0, false);
            return;
          }
          ranked = Array.isArray(raw) ? raw : [];
          const rowsById = {};
          if (ranked.length) {
            const rows = await listPicturesByIds(
              ranked.map((r) => r.picture_id),
              { fields: "grid" },
            );
            if (fetchAllGridImages.lastRequestId !== requestId) {
              if (isSortedFetch && options?.showProgress === true)
                completeSmartScoreProgress(loadId, 0, false);
              return;
            }
            for (const pic of Array.isArray(rows) ? rows : []) {
              rowsById[pic.id] = pic;
            }
          }
          if (faceSearchRanked) {
            faceSearchRanked.value = {
              characterId: character.id,
              matches: ranked,
              rowsById,
            };
          }
        }
        const rowsById = faceSearchRanked?.value?.rowsById ?? {};
        images = cutFaceSuggestions(
          ranked,
          faceSearchThreshold?.value ?? 0,
          faceSearchMinRefs?.value ?? 1,
        )
          .map((r) => rowsById[r.picture_id])
          .filter(Boolean);
      } else if (_hasFaceLikenessSearch) {
        fetchMode = "face-likeness-search";
        // Face likeness search: POST to face-search with source_face_id.
        const queryFaceId = faceLikenessSearchFaceId.value;
        const faceResultsRaw = await faceSearch(queryFaceId);
        const faceResults = Array.isArray(faceResultsRaw) ? faceResultsRaw : [];
        if (!faceResults.length) {
          images = [];
        } else {
          const idOrder = faceResults.map((r) => r.picture_id);
          const rows = await listPicturesByIds(idOrder, {
            fields: "grid",
          });
          const picturesById = {};
          for (const pic of Array.isArray(rows) ? rows : []) {
            picturesById[pic.id] = pic;
          }
          images = idOrder.map((id) => picturesById[id]).filter(Boolean);
        }
      } else if (_hasReverseImageSearch) {
        fetchMode = "reverse-image-search";
        // Reverse image search: POST to likeness-search with stored CLIP embeddings.
        // Multiple IDs are combined with min similarity (must match all sources).
        const queryPicIds = reverseImageSearchPictureIds.value;
        const likenessRaw = await likenessSearch(queryPicIds);
        const likenessResults = Array.isArray(likenessRaw) ? likenessRaw : [];
        if (!likenessResults.length) {
          images = [];
        } else {
          const idOrder = likenessResults.map((r) => r.picture_id);
          const rows = await listPicturesByIds(idOrder, {
            fields: "grid",
          });
          const picturesById = {};
          for (const pic of Array.isArray(rows) ? rows : []) {
            picturesById[pic.id] = pic;
          }
          images = idOrder.map((id) => picturesById[id]).filter(Boolean);
        }
      } else if (_hasSearch) {
        fetchMode = "text-search";
        // Use /pictures/search endpoint for text search
        const params = buildPictureIdsQueryParams();
        images = await searchPictures(searchStore.searchQuery.trim(), {
          query: params,
        });
      } else {
        fetchMode = "stream";
        // Overlay open: the streaming path rebuilds a placeholder grid and
        // re-fills allGridImages from the (now possibly narrower) filter query,
        // which would drop the picture being viewed out of the grid mid-session.
        // Unlike the id-list/search modes below, streaming writes allGridImages
        // through its own return paths and never reaches the shared overlayOpen
        // guard. Defer the whole reconcile to overlay close instead.
        if (overlayOpen.value) {
          pendingOverlayGridRefresh.value = true;
          // We started the sort progress bar above but are deferring this fetch
          // to overlay-close, so it will never reach the completion below.
          // Dismiss the bar now, otherwise "Sorting by …" is stranded forever.
          if (isSortedFetch && options?.showProgress === true)
            completeSmartScoreProgress(loadId, 0, false);
          return;
        }
        // Streaming: COUNT(*) → placeholder grid → parallel first/last batches → background fill.
        //   1. Fast SELECT COUNT(*) → total
        //   2. Pre-build placeholder grid so END key works before streaming starts
        //   3. First batch (visible area) + last batch (END cells) in parallel
        //   4. Background stream fills the middle at BG_BATCH rows per request
        const _charIds = normalizedSelectedCharacterIds.value;
        const _selChar = selectionStore.selectedCharacter;
        if (isSortedFetch && options?.showProgress === true) {
          completeSmartScoreProgress(loadId, 0, true);
        }
        // Compute FIRST_BATCH to cover all items visible in the viewport so that
        // fetchThumbnailsBatch (called after splicing the first batch) never
        // encounters placeholder items (no id) for visible cells.  Placeholders
        // are skipped by fetchThumbnailsBatch but the range is still marked as
        // loaded, causing those cells to remain as permanent spinners.
        // Cell height is derived from clientWidth/cols (square thumbnails) rather
        // than rowHeight?.value, which may still hold the initial thumbnailSize
        // estimate when this runs (DOM measurement via updateRowHeightFromGrid
        // happens asynchronously and may not have fired yet).
        const _fbCols = gridStore.columns || 1;
        const _fbViewH = scrollWrapper.value?.clientHeight || 0;
        const _fbViewW = scrollWrapper.value?.clientWidth || 0;
        const _fbCellH =
          _fbViewW > 0
            ? Math.round(_fbViewW / _fbCols) + (gridStore.compactMode ? 0 : 24)
            : rowHeight?.value > 0
              ? rowHeight.value
              : 200;
        const _fbVisibleItems =
          _fbViewH > 0 && _fbCellH > 0
            ? Math.ceil(_fbViewH / _fbCellH) * _fbCols
            : 0;
        const FIRST_BATCH = Math.max(200, _fbVisibleItems + _fbCols * 2);
        const LAST_BATCH = Math.max(200, _fbVisibleItems + _fbCols * 2);
        // Pass sort/descending to both the stream and the count URLs.  The sort
        // is not always count-neutral: CHARACTER_LIKENESS joins through Face and
        // changes the row set, so the count must run over the same query as the
        // stream or the placeholder grid ends up larger than the stream can fill.
        const _sort = sortStore.selectedSort?.trim();
        const _desc =
          typeof sortStore.selectedDescending === "boolean"
            ? sortStore.selectedDescending
            : true;
        // For CHARACTER_LIKENESS the backend also needs reference_character_id in
        // both the stream and count URLs - the reference determines the row set.
        const _refCharSuffix =
          _sort === "CHARACTER_LIKENESS" &&
          sortStore.selectedSimilarityCharacter
            ? `&reference_character_id=${encodeURIComponent(sortStore.selectedSimilarityCharacter)}`
            : "";
        const _sortSuffix = _sort
          ? `&sort=${encodeURIComponent(_sort)}&descending=${_desc}${_refCharSuffix}`
          : "";
        // Build character/set + project filter params for count and stream URLs.
        const _charP = new URLSearchParams();
        if (hasSetSelection.value) {
          // Set view - mirrors _appendSelectionParams set branch.
          if (isSetOverlapView.value) {
            for (const setId of normalizedSelectedSetIds.value) {
              _charP.append("set_ids", String(setId));
            }
            _charP.set(
              "set_mode",
              selectionStore.setMultiMode ?? "intersection",
            );
            if (
              selectionStore.setMultiMode === "difference" &&
              selectionStore.setDifferenceBaseId != null
            ) {
              _charP.set(
                "base_set_id",
                String(selectionStore.setDifferenceBaseId),
              );
            }
            if (projectStore.projectViewMode === "project") {
              const _pidSet = new Set(
                normalizedSelectedSetIds.value.map(
                  (id) => projectStore.setProjectIds?.[id] ?? null,
                ),
              );
              if (_pidSet.size === 1) {
                const _pid = [..._pidSet][0];
                _charP.set(
                  "project_id",
                  _pid != null ? String(_pid) : "UNASSIGNED",
                );
              }
            }
          } else if (primarySelectedSetId.value != null) {
            _charP.set("set_id", String(primarySelectedSetId.value));
            if (projectStore.projectViewMode === "project") {
              _charP.set(
                "project_id",
                projectStore.selectedProjectId != null
                  ? String(projectStore.selectedProjectId)
                  : "UNASSIGNED",
              );
            }
          }
        } else if (_charIds.length > 1) {
          for (const id of _charIds) _charP.append("character_ids", String(id));
          _charP.set(
            "character_mode",
            selectionStore.characterMultiMode ?? "union",
          );
          // Only apply project filter when all selected characters share the same project.
          if (projectStore.projectViewMode === "project") {
            const _pidSet = new Set(
              _charIds.map(
                (id) => projectStore.characterProjectIds?.[id] ?? null,
              ),
            );
            if (_pidSet.size === 1) {
              const _pid = [..._pidSet][0];
              _charP.set(
                "project_id",
                _pid != null ? String(_pid) : "UNASSIGNED",
              );
            }
          }
        } else if (_selChar === String(SCRAPHEAP_PICTURES_ID)) {
          _charP.set("only_deleted", "true");
        } else if (
          _selChar != null &&
          _selChar !== "" &&
          _selChar !== ALL_PICTURES_ID
        ) {
          _charP.set("character_id", String(_selChar));
          if (projectStore.projectViewMode === "project") {
            _charP.set(
              "project_id",
              projectStore.selectedProjectId != null
                ? String(projectStore.selectedProjectId)
                : "UNASSIGNED",
            );
          }
        } else if (
          _selChar === ALL_PICTURES_ID &&
          filterStore.unassignedOnlyFilter
        ) {
          _charP.set("character_id", String(UNASSIGNED_PICTURES_ID));
          if (projectStore.projectViewMode === "project") {
            _charP.set(
              "project_id",
              projectStore.selectedProjectId != null
                ? String(projectStore.selectedProjectId)
                : "UNASSIGNED",
            );
          }
        } else if (projectStore.projectViewMode === "project") {
          _charP.set(
            "project_id",
            projectStore.selectedProjectId != null
              ? String(projectStore.selectedProjectId)
              : "UNASSIGNED",
          );
        }
        if (referenceFolderIdFilter.value != null) {
          _charP.set(
            "reference_folder_id",
            String(referenceFolderIdFilter.value),
          );
        }
        if (importSourceFolderFilter.value != null) {
          _charP.set(
            "import_source_folder",
            String(importSourceFolderFilter.value),
          );
        }
        // Filter params
        if (filePathPrefixFilter.value != null) {
          _charP.set("file_path_prefix", String(filePathPrefixFilter.value));
        }
        const _charSuffix = _charP.size ? `&${_charP.toString()}` : "";
        // Build media type format filter params for count and stream URLs.
        const _formatP = new URLSearchParams();
        _appendMediaTypeParams(_formatP);
        const _formatSuffix = _formatP.size ? `&${_formatP.toString()}` : "";
        // Build filter-menu params for count and stream URLs.
        // Each labelled block corresponds to a separate commit - remove any single
        // block to bisect a regression.
        const _filterP = new URLSearchParams();
        // Filter params: score range
        if (filterStore.minScoreFilter != null)
          _filterP.set("min_score", String(filterStore.minScoreFilter));
        if (filterStore.maxScoreFilter != null)
          _filterP.set("max_score", String(filterStore.maxScoreFilter));
        if (filterStore.unscoredOnlyFilter) _filterP.set("unscored", "1");
        // Filter params: smart score bucket
        if (filterStore.smartScoreBucketFilter != null)
          _filterP.set(
            "smart_score_bucket",
            String(filterStore.smartScoreBucketFilter),
          );
        // Filter params: resolution bucket
        if (filterStore.resolutionBucketFilter != null)
          _filterP.set(
            "resolution_bucket",
            String(filterStore.resolutionBucketFilter),
          );
        // Filter params: ComfyUI model / LoRA
        (filterStore.comfyuiModelFilter || []).forEach((m) =>
          _filterP.append("comfyui_model", m),
        );
        (filterStore.comfyuiLoraFilter || []).forEach((l) =>
          _filterP.append("comfyui_lora", l),
        );
        // Filter params: tag filters
        (filterStore.tagFilter || []).forEach((t) => _filterP.append("tag", t));
        (filterStore.tagRejectedFilter || []).forEach((t) =>
          _filterP.append("rejected_tag", t),
        );
        (filterStore.tagConfidenceAboveFilter || []).forEach((e) =>
          _filterP.append("tag_confidence_above", e),
        );
        (filterStore.tagConfidenceBelowFilter || []).forEach((e) =>
          _filterP.append("tag_confidence_below", e),
        );
        // Filter params: face bbox filter
        if (filterStore.faceBboxFilter != null)
          _filterP.set("face_filter", String(filterStore.faceBboxFilter));
        // Filter params: impossible-tag sources (repeatable, OR'd)
        (filterStore.impossibleSources || []).forEach((s) =>
          _filterP.append("impossible_tag_source", s),
        );
        // Filter params: stack state. "all" is the absence of the filter, so it
        // is expressed by omission rather than by a sentinel the backend would
        // have to know a second spelling for. This is the default grid path;
        // the search and likeness-group builders carry the same param, and
        // leaving it out here was why moving the Stacks segments did nothing.
        if (
          filterStore.stackStateFilter &&
          filterStore.stackStateFilter !== "all"
        )
          _filterP.set("stack_state", String(filterStore.stackStateFilter));
        // Filter params: shared only
        if (filterStore.sharedOnlyFilter) _filterP.set("shared_only", "true");
        // Filter params: hidden-tag filter
        if (userPrefsStore.applyTagFilter)
          _filterP.set("apply_tag_filter", "true");
        const _filterSuffix = _filterP.size ? `&${_filterP.toString()}` : "";
        const streamQuery = `fields=grid&grid_lite=true&stack_leaders_only=true${_charSuffix}${_sortSuffix}${_formatSuffix}${_filterSuffix}`;

        // Wraps a resource-module call (which resolves to the response BODY,
        // not the Axios envelope) so the grid can report per-batch timings.
        async function timeRequest(requestPromise) {
          const startedAt = getNowMs();
          const body = await requestPromise;
          return {
            body,
            elapsedMs: Math.max(0, getNowMs() - startedAt),
          };
        }

        // Splice raw picture metadata into the placeholder grid at `offset`,
        // preserving thumbnail/face data for cells already loaded.
        const splicePictures = (pictures, offset) => {
          if (!pictures.length) return;
          const grid = allGridImages.value.slice();
          for (let i = 0; i < pictures.length; i++) {
            const idx = offset + i;
            if (idx < grid.length) {
              const pic = pictures[i];
              const existing = grid[idx];
              grid[idx] = {
                ...pic,
                idx,
                thumbnail: existing?.thumbnail ?? null,
                faces: existing?.faces ?? [],
                penalised_tags: existing?.penalised_tags ?? [],
                thumbnail_width:
                  pic.thumbnail_width ?? existing?.thumbnail_width,
                thumbnail_height:
                  pic.thumbnail_height ?? existing?.thumbnail_height,
                square_crop_x: pic.square_crop_x ?? existing?.square_crop_x,
                square_crop_y: pic.square_crop_y ?? existing?.square_crop_y,
                square_crop_side:
                  pic.square_crop_side ?? existing?.square_crop_side,
              };
            }
          }
          allGridImages.value = grid;
        };

        // 1. Fast total count - single indexed SQL query.
        const countStartedAt = getNowMs();
        const countBody = await getPictureCount(
          `stack_leaders_only=true${_charSuffix}${_sortSuffix}${_formatSuffix}${_filterSuffix}`,
        );
        if (fetchAllGridImages.lastRequestId !== requestId) return;
        fetchPhaseTimings.countMs = Math.max(0, getNowMs() - countStartedAt);
        const total =
          typeof countBody?.count === "number" ? countBody.count : 0;

        // 2. Pre-build placeholder grid - scroll area immediately reflects full size.
        const placeholderStartedAt = getNowMs();
        const cols = gridStore.columns || 1;
        // Compute window count from actual viewport capacity so visibleEnd covers
        // all initially visible items even when the viewport shows more than VIEW_WINDOW.
        // Derive cell height from clientWidth/cols (square thumbnails) rather than
        // rowHeight?.value, which may still hold the initial thumbnailSize estimate
        // when this runs (DOM measurement via updateRowHeightFromGrid is async).
        const _fastViewW = scrollWrapper.value?.clientWidth || 0;
        const _fastViewH = scrollWrapper.value?.clientHeight || 0;
        const _effectiveRowHeight0 =
          _fastViewW > 0
            ? Math.round(_fastViewW / cols) + (gridStore.compactMode ? 0 : 24)
            : rowHeight?.value > 0
              ? rowHeight.value
              : Math.round(
                  Math.min(384, Math.max(128, gridStore.thumbnailSize || 128)) +
                    (gridStore.compactMode ? 0 : 24),
                );
        const _viewportItemCount0 =
          _fastViewH > 0 && _effectiveRowHeight0 > 0
            ? Math.ceil(_fastViewH / _effectiveRowHeight0) * cols
            : 0;
        const windowCount = Math.max(
          cols,
          _viewportItemCount0 || divisibleViewWindow.value || cols,
        );
        resetThumbnailState();
        allGridImages.value = Array.from({ length: total }, (_, i) => ({
          id: null,
          idx: i,
        }));
        if (!fetchStartedWithPreserveScroll) {
          visibleStart.value = 0;
          visibleEnd.value = Math.min(total, windowCount);
        }
        gridReady.value = true; // render placeholder grid immediately
        fetchPhaseTimings.placeholderMs = Math.max(
          0,
          getNowMs() - placeholderStartedAt,
        );

        if (total === 0) {
          hasLoadedOnce.value = true;
          initialRender.value = false;
          lastFetchSuccess.value = { key: fetchKey, at: Date.now() };
          return;
        }

        // 3. First (+ optional tail) batches + pre-launch background.
        //
        // API enforces batch_limit <= 5000.
        const BG_BATCH = 5000;
        // potentialLastBatchStart: where a dedicated tail batch would begin.
        const potentialLastBatchStart = Math.max(
          FIRST_BATCH,
          total - LAST_BATCH,
        );
        const backgroundGap = Math.max(
          0,
          potentialLastBatchStart - FIRST_BATCH,
        );
        // Fetch tail in parallel with the first batch whenever the gap is large
        // enough to matter for user responsiveness.  Use a fixed threshold of
        // 1000 items - independent of BG_BATCH - so that medium-sized
        // collections (gap 1000–5000) still get the tail batch early instead of
        // waiting for a single large background request to complete.
        const TAIL_THRESHOLD = 1000;
        const shouldFetchTailEarly =
          total > FIRST_BATCH && backgroundGap > TAIL_THRESHOLD;
        // Effective end of the background region: extends to `total` when we
        // skip the early tail so every item is eventually fetched.
        const lastBatchStart = shouldFetchTailEarly
          ? potentialLastBatchStart
          : total;

        // Kick off the first batch and the optional tail batch concurrently, but
        // do NOT block visible-thumbnail loading on the tail (which fills
        // off-screen end-of-grid cells and is often the slower of the two).
        // Await the first batch alone, splice it and request its thumbnails, then
        // await the tail. Both requests are already in flight, so the tail is not
        // delayed by this ordering.
        const firstReqPromise = timeRequest(
          streamPictures(streamQuery, {
            offset: 0,
            batchLimit: FIRST_BATCH,
          }),
        );
        // Tail batch: for collections where the gap exceeds TAIL_THRESHOLD.
        // `.catch` keeps it from becoming an unhandled rejection if we bail out
        // (stale requestId) before awaiting it below.
        const tailReqPromise = (
          shouldFetchTailEarly
            ? timeRequest(
                streamPictures(streamQuery, {
                  offset: potentialLastBatchStart,
                  batchLimit: LAST_BATCH,
                }),
              )
            : Promise.resolve(null)
        ).catch(() => null);

        const firstResTimed = await firstReqPromise;
        if (fetchAllGridImages.lastRequestId !== requestId) return;
        fetchPhaseTimings.firstBatchMs = firstResTimed?.elapsedMs ?? null;

        const firstPics = firstResTimed?.body?.pictures ?? [];
        splicePictures(firstPics, 0);
        hasLoadedOnce.value = true;
        initialRender.value = false;
        const prefetchEnd = Math.min(
          total,
          visibleEnd.value + (divisibleViewWindow.value || windowCount),
        );
        // Request thumbnails for the actually-visible cells first; defer the
        // off-screen margin by a frame so the visible thumbnails are not queued
        // behind margin ones over the browser's limited per-origin connections.
        fetchThumbnailsBatch(visibleStart.value, visibleEnd.value, {
          reason: "initial-visible-prefetch",
        });
        if (prefetchEnd > visibleEnd.value) {
          const marginStart = visibleEnd.value;
          requestAnimationFrame(() => {
            if (fetchAllGridImages.lastRequestId !== requestId) return;
            fetchThumbnailsBatch(marginStart, prefetchEnd, {
              reason: "initial-margin-prefetch",
            });
          });
        }
        if (typeof onGridVisibleMetadataReady === "function") {
          onGridVisibleMetadataReady({
            loadId,
            total,
            firstBatchCount: firstPics.length,
            visibleStart: visibleStart.value,
            visibleEnd: prefetchEnd,
          });
        }

        const lastResTimed = await tailReqPromise;
        if (fetchAllGridImages.lastRequestId !== requestId) return;
        fetchPhaseTimings.tailBatchMs = lastResTimed?.elapsedMs ?? null;
        if (lastResTimed?.body) {
          const lastPics = lastResTimed?.body?.pictures ?? [];
          splicePictures(lastPics, potentialLastBatchStart);
        }

        lastFetchSuccess.value = { key: fetchKey, at: Date.now() };

        // Sync lastFetchedGridImages after the initial batches so that
        // removeImagesById/rebuildGridImagesFromLastFetch works correctly even
        // if a delete happens before background streaming finishes.
        lastFetchedGridImages.value = allGridImages.value.filter(
          (img) => img && img.id != null,
        );

        // 4. Background stream: fill remaining items sequentially after first
        // batch is rendered.  Sequential (not parallel) to avoid DB contention
        // that would slow the already-visible first-batch response.
        let bgOff = FIRST_BATCH;
        while (bgOff < lastBatchStart) {
          if (fetchAllGridImages.lastRequestId !== requestId) return;
          const limit = Math.min(BG_BATCH, lastBatchStart - bgOff);
          const bgResTimed = await timeRequest(
            streamPictures(streamQuery, {
              offset: bgOff,
              batchLimit: limit,
            }),
          );
          if (fetchAllGridImages.lastRequestId !== requestId) return;
          const bgOffset = bgOff;
          bgOff += limit;
          const bgNetworkElapsedMs = bgResTimed.elapsedMs;
          const bgUiStartedAt = getNowMs();
          const bgPics = bgResTimed?.body?.pictures ?? [];
          splicePictures(bgPics, bgOffset);
          updateVisibleThumbnails();
          // Keep lastFetchedGridImages current so deletes during streaming work.
          lastFetchedGridImages.value = allGridImages.value.filter(
            (img) => img && img.id != null,
          );
          await nextTick();
          const bgUiElapsedMs = Math.max(0, getNowMs() - bgUiStartedAt);
          const bgElapsedMs = bgNetworkElapsedMs + bgUiElapsedMs;
          fetchPhaseTimings.backgroundTotalMs += bgElapsedMs;
          fetchPhaseTimings.backgroundNetworkTotalMs += bgNetworkElapsedMs;
          fetchPhaseTimings.backgroundUiTotalMs += bgUiElapsedMs;
          fetchPhaseTimings.backgroundSlowestBatchMs = Math.max(
            fetchPhaseTimings.backgroundSlowestBatchMs,
            bgElapsedMs,
          );
          fetchPhaseTimings.backgroundSlowestNetworkBatchMs = Math.max(
            fetchPhaseTimings.backgroundSlowestNetworkBatchMs,
            bgNetworkElapsedMs,
          );
          fetchPhaseTimings.backgroundSlowestUiBatchMs = Math.max(
            fetchPhaseTimings.backgroundSlowestUiBatchMs,
            bgUiElapsedMs,
          );
          fetchPhaseTimings.backgroundBatchCount += 1;
        }

        // Sync lastFetchedGridImages so that removeImagesById/rebuildGridImagesFromLastFetch
        // works correctly after a delete during streaming.
        const postProcessStartedAt = getNowMs();
        lastFetchedGridImages.value = allGridImages.value.filter(
          (img) => img && img.id != null,
        );
        // Safety net: trim trailing placeholders the stream never filled.  If the
        // count and stream queries ever drift again (stream yields fewer rows than
        // COUNT(*)), the surplus tail cells would sit as permanent spinners.  Only
        // the contiguous trailing run of id-less cells is dropped - mid-grid holes
        // are left alone.
        const _finalGrid = allGridImages.value;
        let _filledEnd = _finalGrid.length;
        while (_filledEnd > 0 && _finalGrid[_filledEnd - 1]?.id == null) {
          _filledEnd -= 1;
        }
        if (_filledEnd < _finalGrid.length) {
          console.warn(
            "[ImageGrid.vue] Stream returned fewer rows than the count; trimming",
            _finalGrid.length - _filledEnd,
            "trailing placeholder cells.",
          );
          allGridImages.value = _finalGrid.slice(0, _filledEnd);
          if (visibleEnd.value > _filledEnd) {
            visibleEnd.value = _filledEnd;
          }
        }
        fetchPhaseTimings.postProcessMs = Math.max(
          0,
          getNowMs() - postProcessStartedAt,
        );
        fetchSucceeded = true;
        return;
      }

      if (fetchAllGridImages.lastRequestId !== requestId) {
        if (isSortedFetch && options?.showProgress === true)
          completeSmartScoreProgress(loadId, 0, false);
        return;
      }
      lastFetchedGridImages.value = Array.isArray(images) ? images.slice() : [];
      syncExpandAllStacksFromFetchedImages();
      images = collapseStackImages(images);
      const shouldHighlight = highlightNextFetch.value && hasLoadedOnce.value;
      const nextIdSet = new Set(
        Array.isArray(images)
          ? images
              .map((img) => getPictureId(img?.id))
              .filter((id) => id !== null)
          : [],
      );
      if (shouldHighlight) {
        const newIds = [];
        nextIdSet.forEach((id) => {
          if (!previousImageIds.has(id)) {
            newIds.push(id);
          }
        });
        if (newIds.length) {
          triggerNewImageHighlight(newIds);
        }
      }
      previousImageIds.clear();
      nextIdSet.forEach((id) => previousImageIds.add(id));
      highlightNextFetch.value = false;
      hasLoadedOnce.value = true;
      const newImages = mapGridImages(images);
      resetThumbnailState();
      if (overlayOpen.value) {
        // Don't replace allGridImages while the overlay is open - the filmstrip
        // and prev/next navigation read from it directly. Store the fetched
        // result and apply it once the overlay closes.
        pendingGridImages.value = newImages;
        pendingOverlayGridRefresh.value = true;
      } else {
        allGridImages.value = newImages;
      }
      // When the shared-only filter is active every returned image is shared by
      // definition. Pre-seed sharedPictureIds immediately so badges appear
      // without waiting for the async batch-check round trip.
      if (filterStore.sharedOnlyFilter && !isReadOnly.value) {
        const next = new Set(sharedPictureIds.value);
        for (const img of newImages) {
          if (img.id) next.add(img.id);
        }
        sharedPictureIds.value = next;
      }
      if (isSetOverlapView.value) {
        totalCurrentCategoryCount.value = newImages.length;
      }
      const cols = gridStore.columns || 1;
      // Compute window count from actual viewport capacity so visibleEnd covers
      // all initially visible items even when the viewport shows more than VIEW_WINDOW.
      // Derive cell height from clientWidth/cols (square thumbnails) rather than
      // rowHeight?.value, which may still hold the initial thumbnailSize estimate
      // when this runs (DOM measurement via updateRowHeightFromGrid is async).
      const _slowViewW = scrollWrapper.value?.clientWidth || 0;
      const _slowViewH = scrollWrapper.value?.clientHeight || 0;
      const _effectiveRowHeight1 =
        _slowViewW > 0
          ? Math.round(_slowViewW / cols) + (gridStore.compactMode ? 0 : 24)
          : rowHeight?.value > 0
            ? rowHeight.value
            : Math.round(
                Math.min(384, Math.max(128, gridStore.thumbnailSize || 128)) +
                  (gridStore.compactMode ? 0 : 24),
              );
      const _viewportItemCount1 =
        _slowViewH > 0 && _effectiveRowHeight1 > 0
          ? Math.ceil(_slowViewH / _effectiveRowHeight1) * cols
          : 0;
      const windowCount = Math.max(
        cols,
        _viewportItemCount1 || divisibleViewWindow.value || cols,
      );
      if (!fetchStartedWithPreserveScroll) {
        // Normal (non-preserve) fetch: jump to top so thumbnails load from index 0.
        visibleStart.value = 0;
        visibleEnd.value = Math.min(newImages.length, windowCount);
      } else {
        // Scroll-preserving fetch: keep visibleStart/End as-is so
        // updateVisibleThumbnails loads the range the user is actually viewing.
        visibleEnd.value = Math.min(visibleEnd.value, newImages.length);
        if (visibleStart.value > visibleEnd.value)
          visibleStart.value = Math.max(0, visibleEnd.value - 1);
      }
      if (initialRender.value) {
        const prefetchEnd = Math.min(
          newImages.length,
          visibleEnd.value + divisibleViewWindow.value,
        );
        fetchThumbnailsBatch(visibleStart.value, prefetchEnd);
      }
      await refreshExpandedStacksAfterFetch();
      await maybeRefreshOverlayForComfyui();
      requestAnimationFrame(() => {
        if (initialRender.value) {
          initialRender.value = false;
          updateVisibleThumbnails();
        }
      });
      lastFetchSuccess.value = { key: fetchKey, at: Date.now() };
      if (isSortedFetch) {
        const elapsedMs = Math.max(0, getNowMs() - sortedFetchStartedAt);
        completeSmartScoreProgress(loadId, elapsedMs, true);
      }
      fetchSucceeded = true;
    } catch (e) {
      if (fetchAllGridImages.lastRequestId !== requestId) {
        if (isSortedFetch && options?.showProgress === true)
          completeSmartScoreProgress(loadId, 0, false);
        return;
      }
      fetchError = e;
      imagesError.value = e.message;
      // Don't wipe the grid on a transient error while the overlay is open -
      // the user would see the grid flash empty behind the overlay.
      if (!overlayOpen.value) {
        allGridImages.value = [];
      }
      lastFetchError.value = { key: fetchKey, at: Date.now() };
      if (isSortedFetch) {
        const elapsedMs = Math.max(0, getNowMs() - sortedFetchStartedAt);
        completeSmartScoreProgress(loadId, elapsedMs, false);
      }
    } finally {
      if (typeof onGridFetchDone === "function") {
        onGridFetchDone({
          loadId,
          fetchMode,
          success: fetchSucceeded,
          elapsedMs: Math.max(0, getNowMs() - fetchStartedAt),
          resultCount: Array.isArray(allGridImages.value)
            ? allGridImages.value.length
            : 0,
          ...fetchPhaseTimings,
        });
      }
      if (loadId === gridLoadEpoch.value) {
        imagesLoading.value = false;
        gridReady.value = true;
      }
    }
    if (!initialRender.value) {
      updateVisibleThumbnails();
    }
    if (pendingScrollTop.value !== null && scrollWrapper.value) {
      const targetTop = pendingScrollTop.value;
      pendingScrollTop.value = null;
      nextTick(() => {
        if (!scrollWrapper.value) return;
        const maxScroll =
          scrollWrapper.value.scrollHeight - scrollWrapper.value.clientHeight;
        const clamped = Math.max(0, Math.min(targetTop, maxScroll));
        scrollWrapper.value.scrollTop = clamped;
        updateVisibleThumbnails();
      });
    }
    return { success: fetchSucceeded, fetchMode, error: fetchError };
  }

  async function fetchAllPicturesCount() {
    try {
      const data = await getCharacterSummary(
        ALL_PICTURES_ID,
        userPrefsStore.applyTagFilter ? { apply_tag_filter: true } : undefined,
      );
      totalAllPicturesCount.value = Number(data.image_count) || 0;
      totalAllPicturesCountLoaded.value = true;
    } catch (e) {
      console.warn("[ImageGrid.vue] Failed to fetch all pictures count:", e);
    }

    try {
      // The scoped count comes from one of two summary resources; the ladder
      // below picks which one and under what id, then a single call runs it.
      let summaryKind = "character";
      let summaryId = ALL_PICTURES_ID;
      let summaryProjectId = null;
      const selectedCharacter = String(selectionStore.selectedCharacter ?? "");
      if (isSetOverlapView.value) {
        totalCurrentCategoryCount.value =
          Number(allGridImages.value.length) || 0;
        return;
      }
      const selectedSetId = primarySelectedSetId.value;
      if (
        selectedSetId !== null &&
        selectedSetId !== undefined &&
        String(selectedSetId) !== ""
      ) {
        // One shared, in-flight-de-duplicated read of the set list: this is the
        // same request the sidebar makes on the same triggers, and the count
        // shown here must be the fresh one, so it forces a revalidation rather
        // than reading whatever happens to be cached.
        const setList = await useEntityListsStore().refresh("sets");
        const selectedSetNumericId = Number(selectedSetId);
        const selectedSet = Array.isArray(setList)
          ? setList.find((item) => {
              const itemId = Number(item?.id);
              if (Number.isFinite(selectedSetNumericId)) {
                return (
                  Number.isFinite(itemId) && itemId === selectedSetNumericId
                );
              }
              return String(item?.id) === String(selectedSetId);
            })
          : null;
        totalCurrentCategoryCount.value =
          Number(selectedSet?.picture_count) || 0;
        return;
      }
      const inProjectView = projectStore.projectViewMode === "project";
      const activeProjectId =
        projectStore.selectedProjectId != null
          ? projectStore.selectedProjectId
          : "UNASSIGNED";
      if (selectedCharacter === String(ALL_PICTURES_ID)) {
        if (inProjectView) {
          summaryKind = "project";
          summaryId = activeProjectId;
        }
      } else if (selectedCharacter === String(UNASSIGNED_PICTURES_ID)) {
        summaryId = UNASSIGNED_PICTURES_ID;
        if (inProjectView) summaryProjectId = activeProjectId;
      } else if (selectedCharacter === String(SCRAPHEAP_PICTURES_ID)) {
        summaryId = SCRAPHEAP_PICTURES_ID;
      } else if (selectedCharacter && !hasSetSelection.value) {
        summaryId = selectedCharacter;
        if (inProjectView) summaryProjectId = activeProjectId;
      }

      const summaryParams = {};
      if (summaryProjectId != null) summaryParams.project_id = summaryProjectId;
      if (userPrefsStore.applyTagFilter) summaryParams.apply_tag_filter = true;
      const hasParams = Object.keys(summaryParams).length > 0;

      const scopedData =
        summaryKind === "project"
          ? await getProjectSummary(
              summaryId,
              hasParams ? summaryParams : undefined,
            )
          : await getCharacterSummary(
              summaryId,
              hasParams ? summaryParams : undefined,
            );
      totalCurrentCategoryCount.value = Number(scopedData.image_count) || 0;
    } catch (e) {
      console.warn("[ImageGrid.vue] Failed to fetch scoped category count:", e);
      totalCurrentCategoryCount.value = 0;
    }
  }

  const debouncedFetchAllGridImages = debounce(fetchAllGridImages, 1000);

  return {
    imagesLoading,
    imagesError,
    totalAllPicturesCount,
    totalAllPicturesCountLoaded,
    totalCurrentCategoryCount,
    gridReady,
    gridLoadEpoch,
    lastFetchKey,
    lastFetchError,
    lastFetchSuccess,
    smartScoreLoadingVisible,
    buildGridFetchKey,
    buildPictureIdsQueryParams,
    buildLikenessGroupQueryParams,
    fetchAllGridImages,
    fetchAllPicturesCount,
    debouncedFetchAllGridImages,
  };
}
