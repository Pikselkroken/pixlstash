import { ref, computed, nextTick } from "vue";
import { apiClient, isReadOnly } from "../utils/apiClient";
import { getStackColor, getStackThreshold } from "../utils/utils.js";
import { getPictureId, PIL_IMAGE_EXTENSIONS, VIDEO_EXTENSIONS } from "../utils/media.js";
import { debounce } from "lodash-es";

const LIKENESS_GROUPS_SORT_KEY = "LIKENESS_GROUPS";

/**
 * Manages grid image fetching: fetch state, URL query building, and the
 * debounced fetch trigger.
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
  // ============================================================
  // GRID FETCH STATE
  // ============================================================
  const imagesLoading = ref(false);
  const imagesError = ref(null);
  const totalAllPicturesCount = ref(0);
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
    if (typeof props.selectedSort !== "string") return "";
    return props.selectedSort.trim().toUpperCase();
  }

  function buildGridFetchKey() {
    const selectedSetIds = Array.isArray(props.selectedSetIds)
      ? props.selectedSetIds
          .map((id) => Number(id))
          .filter((id) => Number.isFinite(id) && id > 0)
          .sort((a, b) => a - b)
      : [];
    const selectedCharacterIds = normalizedSelectedCharacterIds.value;
    return JSON.stringify({
      selectedCharacter: props.selectedCharacter ?? null,
      selectedCharacterIds,
      isMultiCharacterView: selectedCharacterIds.length > 1,
      characterMultiMode:
        selectedCharacterIds.length > 1
          ? (props.characterMultiMode ?? "union")
          : null,
      selectedSet: props.selectedSet ?? null,
      selectedSetIds,
      isSetOverlapView: selectedSetIds.length > 1,
      setMultiMode:
        selectedSetIds.length > 1 ? (props.setMultiMode ?? "intersection") : null,
      setDifferenceBaseId:
        selectedSetIds.length > 1 && props.setMultiMode === "difference"
          ? (props.setDifferenceBaseId ?? null)
          : null,
      projectViewMode: props.projectViewMode ?? "global",
      selectedProjectId: props.selectedProjectId ?? null,
      searchQuery: props.searchQuery ?? "",
      selectedSort: props.selectedSort ?? "",
      selectedDescending: props.selectedDescending ?? null,
      stackThreshold: props.stackThreshold ?? null,
      mediaTypeFilter: props.mediaTypeFilter ?? "all",
      impossibleSources: props.impossibleSources ?? [],
      similarityCharacter: props.similarityCharacter ?? null,
      comfyuiModelFilter: props.comfyuiModelFilter ?? [],
      comfyuiLoraFilter: props.comfyuiLoraFilter ?? [],
      referenceFolderIdFilter: props.referenceFolderIdFilter ?? null,
      filePathPrefixFilter: props.filePathPrefixFilter ?? null,
      importSourceFolderFilter: props.importSourceFolderFilter ?? null,
      unassignedOnlyFilter: props.unassignedOnlyFilter ?? false,
      applyTagFilter: props.applyTagFilter ?? false,
      reverseImageSearchPictureIds: reverseImageSearchPictureIds?.value ?? [],
      faceLikenessSearchFaceId: faceLikenessSearchFaceId?.value ?? null,
    });
  }

  function _appendSelectionParams(params) {
    if (hasSetSelection.value) {
      if (isSetOverlapView.value) {
        for (const setId of normalizedSelectedSetIds.value) {
          params.append("set_ids", String(setId));
        }
        params.append("set_mode", props.setMultiMode ?? "intersection");
        if (
          props.setMultiMode === "difference" &&
          props.setDifferenceBaseId != null
        ) {
          params.append("base_set_id", String(props.setDifferenceBaseId));
        }
        if (props.projectViewMode === "project") {
          // Derive effective project_id from per-set data; skip when sets span multiple projects.
          const pidSet = new Set(
            normalizedSelectedSetIds.value.map(
              (id) => props.setProjectIds?.[id] ?? null,
            ),
          );
          if (pidSet.size === 1) {
            const pid = [...pidSet][0];
            params.append("project_id", pid != null ? pid : "UNASSIGNED");
          }
        }
      } else if (primarySelectedSetId.value != null) {
        params.append("set_id", String(primarySelectedSetId.value));
        if (props.projectViewMode === "project") {
          params.append(
            "project_id",
            props.selectedProjectId != null
              ? props.selectedProjectId
              : "UNASSIGNED",
          );
        }
      }
    } else if (isMultiCharacterView.value) {
      for (const charId of normalizedSelectedCharacterIds.value) {
        params.append("character_ids", String(charId));
      }
      params.append("character_mode", props.characterMultiMode ?? "union");
      if (props.projectViewMode === "project") {
        // Derive effective project_id from per-character data; if all chars share
        // the same project use it, if they span multiple projects skip the filter.
        const pidSet = new Set(
          normalizedSelectedCharacterIds.value.map(
            (id) => props.characterProjectIds?.[id] ?? null,
          ),
        );
        if (pidSet.size === 1) {
          const pid = [...pidSet][0];
          params.append("project_id", pid != null ? pid : "UNASSIGNED");
        }
      }
    } else if (
      props.selectedCharacter !== undefined &&
      props.selectedCharacter !== null &&
      props.selectedCharacter !== "" &&
      props.selectedCharacter !== props.allPicturesId
    ) {
      if (props.selectedCharacter === String(props.scrapheapPicturesId)) {
        params.append("only_deleted", "true");
      } else {
        params.append("character_id", props.selectedCharacter);
        if (props.projectViewMode === "project") {
          params.append(
            "project_id",
            props.selectedProjectId != null
              ? props.selectedProjectId
              : "UNASSIGNED",
          );
        }
      }
    } else if (
      props.selectedCharacter === props.allPicturesId &&
      props.unassignedOnlyFilter
    ) {
      params.append("character_id", props.unassignedPicturesId);
      if (props.projectViewMode === "project") {
        params.append(
          "project_id",
          props.selectedProjectId != null
            ? props.selectedProjectId
            : "UNASSIGNED",
        );
      }
    } else if (
      props.selectedCharacter === props.allPicturesId &&
      props.projectViewMode === "project"
    ) {
      params.append(
        "project_id",
        props.selectedProjectId != null ? props.selectedProjectId : "UNASSIGNED",
      );
    }
  }

  function _appendMediaTypeParams(params) {
    if (props.mediaTypeFilter === "images") {
      for (const ext of PIL_IMAGE_EXTENSIONS) {
        params.append("format", ext.toUpperCase());
      }
    } else if (props.mediaTypeFilter === "videos") {
      for (const ext of VIDEO_EXTENSIONS) {
        params.append("format", ext.toUpperCase());
      }
    }
  }

  function buildPictureIdsQueryParams() {
    const params = new URLSearchParams();
    _appendSelectionParams(params);
    if (
      props.selectedSort === "CHARACTER_LIKENESS" &&
      props.similarityCharacter
    ) {
      params.append("reference_character_id", props.similarityCharacter);
    }
    if (props.searchQuery && props.searchQuery.trim()) {
      params.append("query", props.searchQuery.trim());
    } else {
      if (props.selectedSort && props.selectedSort.trim()) {
        params.append("sort", props.selectedSort.trim());
      }
      if (typeof props.selectedDescending === "boolean") {
        params.append("descending", props.selectedDescending ? "true" : "false");
      } else {
        console.warn(
          "[ImageGrid.vue] selectedDescending is not boolean, skipping param. Type:",
          typeof props.selectedDescending,
        );
      }
    }
    params.append("fields", "grid");
    _appendMediaTypeParams(params);
    (props.comfyuiModelFilter || []).forEach((m) =>
      params.append("comfyui_model", m),
    );
    (props.comfyuiLoraFilter || []).forEach((l) =>
      params.append("comfyui_lora", l),
    );
    if (props.minScoreFilter != null) {
      params.append("min_score", props.minScoreFilter);
    }
    if (props.maxScoreFilter != null) {
      params.append("max_score", props.maxScoreFilter);
    }
    if (props.smartScoreBucketFilter != null) {
      params.append("smart_score_bucket", props.smartScoreBucketFilter);
    }
    if (props.resolutionBucketFilter != null) {
      params.append("resolution_bucket", props.resolutionBucketFilter);
    }
    (props.tagFilter || []).forEach((t) => params.append("tag", t));
    (props.tagRejectedFilter || []).forEach((t) =>
      params.append("rejected_tag", t),
    );
    (props.tagConfidenceAboveFilter || []).forEach((e) =>
      params.append("tag_confidence_above", e),
    );
    (props.tagConfidenceBelowFilter || []).forEach((e) =>
      params.append("tag_confidence_below", e),
    );
    if (props.applyTagFilter) {
      params.append("apply_tag_filter", "true");
    }
    if (props.referenceFolderIdFilter != null) {
      params.append("reference_folder_id", String(props.referenceFolderIdFilter));
    }
    if (props.filePathPrefixFilter != null) {
      params.append("file_path_prefix", props.filePathPrefixFilter);
    }
    if (props.importSourceFolderFilter != null) {
      params.append("import_source_folder", props.importSourceFolderFilter);
    }
    if (props.faceBboxFilter != null) {
      params.append("face_filter", props.faceBboxFilter);
    }
    (props.impossibleSources || []).forEach((s) =>
      params.append("impossible_tag_source", s),
    );
    if (props.sharedOnlyFilter) {
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
    (props.comfyuiModelFilter || []).forEach((m) =>
      params.append("comfyui_model", m),
    );
    (props.comfyuiLoraFilter || []).forEach((l) =>
      params.append("comfyui_lora", l),
    );
    if (props.minScoreFilter != null) {
      params.append("min_score", props.minScoreFilter);
    }
    if (props.maxScoreFilter != null) {
      params.append("max_score", props.maxScoreFilter);
    }
    if (props.smartScoreBucketFilter != null) {
      params.append("smart_score_bucket", props.smartScoreBucketFilter);
    }
    if (props.resolutionBucketFilter != null) {
      params.append("resolution_bucket", props.resolutionBucketFilter);
    }
    (props.tagFilter || []).forEach((t) => params.append("tag", t));
    (props.tagRejectedFilter || []).forEach((t) =>
      params.append("rejected_tag", t),
    );
    (props.tagConfidenceAboveFilter || []).forEach((e) =>
      params.append("tag_confidence_above", e),
    );
    (props.tagConfidenceBelowFilter || []).forEach((e) =>
      params.append("tag_confidence_below", e),
    );
    if (props.faceBboxFilter != null) {
      params.append("face_filter", props.faceBboxFilter);
    }
    (props.impossibleSources || []).forEach((s) =>
      params.append("impossible_tag_source", s),
    );
    if (props.applyTagFilter) {
      params.append("apply_tag_filter", "true");
    }
    if (props.sharedOnlyFilter) {
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
        selectedSort: props.selectedSort ?? null,
        selectedCharacter: props.selectedCharacter ?? null,
        selectedSet: props.selectedSet ?? null,
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

      const _hasSearch = !!props.searchQuery?.trim();
      const _isLikenessSort = props.selectedSort === LIKENESS_GROUPS_SORT_KEY;
      const _hasReverseImageSearch =
        !_hasSearch && !!reverseImageSearchPictureIds?.value?.length;
      const _hasFaceLikenessSearch =
        !_hasSearch && !_hasReverseImageSearch && !!faceLikenessSearchFaceId?.value;

      if (_isLikenessSort) {
        fetchMode = "likeness-groups";
        const threshold = getStackThreshold(props.stackThreshold);
        const likenessGroupParams = buildLikenessGroupQueryParams();
        const url = `${
          props.backendUrl
        }/pictures/likeness-groups?threshold=${encodeURIComponent(threshold)}${
          likenessGroupParams ? `&${likenessGroupParams}` : ""
        }`;
        const res = await apiClient.get(url);
        const data = await res.data;
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
      } else if (_hasFaceLikenessSearch) {
        fetchMode = "face-likeness-search";
        // Face likeness search: POST to face-search with source_face_id.
        const queryFaceId = faceLikenessSearchFaceId.value;
        const faceRes = await apiClient.post(
          `${props.backendUrl}/pictures/face-search?source_face_id=${queryFaceId}&top_n=500`,
        );
        const faceResults = Array.isArray(faceRes.data) ? faceRes.data : [];
        if (!faceResults.length) {
          images = [];
        } else {
          const idOrder = faceResults.map((r) => r.picture_id);
          const idParams = new URLSearchParams();
          idOrder.forEach((id) => idParams.append("id", id));
          idParams.append("fields", "grid");
          const picturesRes = await apiClient.get(
            `${props.backendUrl}/pictures?${idParams.toString()}`,
          );
          const picturesById = {};
          for (const pic of Array.isArray(picturesRes.data)
            ? picturesRes.data
            : []) {
            picturesById[pic.id] = pic;
          }
          images = idOrder.map((id) => picturesById[id]).filter(Boolean);
        }
      } else if (_hasReverseImageSearch) {
        fetchMode = "reverse-image-search";
        // Reverse image search: POST to likeness-search with stored CLIP embeddings.
        // Multiple IDs are combined with min similarity (must match all sources).
        const queryPicIds = reverseImageSearchPictureIds.value;
        const likenessParams = new URLSearchParams();
        queryPicIds.forEach((id) => likenessParams.append("source_picture_ids", id));
        likenessParams.append("top_n", "500");
        likenessParams.append("threshold", "0.05");
        const likenessRes = await apiClient.post(
          `${props.backendUrl}/pictures/likeness-search?${likenessParams.toString()}`,
        );
        const likenessResults = Array.isArray(likenessRes.data)
          ? likenessRes.data
          : [];
        if (!likenessResults.length) {
          images = [];
        } else {
          const idOrder = likenessResults.map((r) => r.picture_id);
          const idParams = new URLSearchParams();
          idOrder.forEach((id) => idParams.append("id", id));
          idParams.append("fields", "grid");
          const picturesRes = await apiClient.get(
            `${props.backendUrl}/pictures?${idParams.toString()}`,
          );
          const picturesById = {};
          for (const pic of Array.isArray(picturesRes.data)
            ? picturesRes.data
            : []) {
            picturesById[pic.id] = pic;
          }
          images = idOrder.map((id) => picturesById[id]).filter(Boolean);
        }
      } else if (_hasSearch) {
        fetchMode = "text-search";
        // Use /pictures/search endpoint for text search
        const params = buildPictureIdsQueryParams();
        const url = `${
          props.backendUrl
        }/pictures/search?query=${encodeURIComponent(
          props.searchQuery.trim(),
        )}&threshold=0.1&top_n=10000${params ? `&${params}` : ""}`;
        const res = await apiClient.get(url);
        const data = await res.data;
        images = data;
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
        const _selChar = props.selectedCharacter;
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
        const _fbCols = props.columns || 1;
        const _fbViewH = scrollWrapper.value?.clientHeight || 0;
        const _fbViewW = scrollWrapper.value?.clientWidth || 0;
        const _fbCellH = _fbViewW > 0
          ? Math.round(_fbViewW / _fbCols) + (props.compactMode ? 0 : 24)
          : (rowHeight?.value > 0 ? rowHeight.value : 200);
        const _fbVisibleItems = _fbViewH > 0 && _fbCellH > 0
          ? Math.ceil(_fbViewH / _fbCellH) * _fbCols
          : 0;
        const FIRST_BATCH = Math.max(200, _fbVisibleItems + _fbCols * 2);
        const LAST_BATCH = Math.max(200, _fbVisibleItems + _fbCols * 2);
        // Pass sort/descending to the stream so pictures arrive in the right
        // order.  Count URL stays param-free — sort never affects COUNT(*).
        const _sort = props.selectedSort?.trim();
        const _desc = typeof props.selectedDescending === 'boolean'
          ? props.selectedDescending
          : true;
        // For CHARACTER_LIKENESS the backend also needs reference_character_id in the
        // stream URL (count URL only needs the character filter, not the reference).
        const _refCharSuffix =
          _sort === 'CHARACTER_LIKENESS' && props.similarityCharacter
            ? `&reference_character_id=${encodeURIComponent(props.similarityCharacter)}`
            : '';
        const _sortSuffix = _sort
          ? `&sort=${encodeURIComponent(_sort)}&descending=${_desc}${_refCharSuffix}`
          : '';
        // Build character/set + project filter params for count and stream URLs.
        const _charP = new URLSearchParams();
        if (hasSetSelection.value) {
          // Set view — mirrors _appendSelectionParams set branch.
          if (isSetOverlapView.value) {
            for (const setId of normalizedSelectedSetIds.value) {
              _charP.append('set_ids', String(setId));
            }
            _charP.set('set_mode', props.setMultiMode ?? 'intersection');
            if (props.setMultiMode === 'difference' && props.setDifferenceBaseId != null) {
              _charP.set('base_set_id', String(props.setDifferenceBaseId));
            }
            if (props.projectViewMode === 'project') {
              const _pidSet = new Set(
                normalizedSelectedSetIds.value.map(id => props.setProjectIds?.[id] ?? null)
              );
              if (_pidSet.size === 1) {
                const _pid = [..._pidSet][0];
                _charP.set('project_id', _pid != null ? String(_pid) : 'UNASSIGNED');
              }
            }
          } else if (primarySelectedSetId.value != null) {
            _charP.set('set_id', String(primarySelectedSetId.value));
            if (props.projectViewMode === 'project') {
              _charP.set('project_id', props.selectedProjectId != null ? String(props.selectedProjectId) : 'UNASSIGNED');
            }
          }
        } else if (_charIds.length > 1) {
          for (const id of _charIds) _charP.append('character_ids', String(id));
          _charP.set('character_mode', props.characterMultiMode ?? 'union');
          // Only apply project filter when all selected characters share the same project.
          if (props.projectViewMode === 'project') {
            const _pidSet = new Set(_charIds.map(id => props.characterProjectIds?.[id] ?? null));
            if (_pidSet.size === 1) {
              const _pid = [..._pidSet][0];
              _charP.set('project_id', _pid != null ? String(_pid) : 'UNASSIGNED');
            }
          }
        } else if (_selChar === String(props.scrapheapPicturesId)) {
          _charP.set('only_deleted', 'true');
        } else if (
          _selChar != null &&
          _selChar !== '' &&
          _selChar !== props.allPicturesId
        ) {
          _charP.set('character_id', String(_selChar));
          if (props.projectViewMode === 'project') {
            _charP.set('project_id', props.selectedProjectId != null ? String(props.selectedProjectId) : 'UNASSIGNED');
          }
        } else if (_selChar === props.allPicturesId && props.unassignedOnlyFilter) {
          _charP.set('character_id', String(props.unassignedPicturesId));
          if (props.projectViewMode === 'project') {
            _charP.set('project_id', props.selectedProjectId != null ? String(props.selectedProjectId) : 'UNASSIGNED');
          }
        } else if (props.projectViewMode === 'project') {
          _charP.set('project_id', props.selectedProjectId != null ? String(props.selectedProjectId) : 'UNASSIGNED');
        }
        if (props.referenceFolderIdFilter != null) {
          _charP.set('reference_folder_id', String(props.referenceFolderIdFilter));
        }
        if (props.importSourceFolderFilter != null) {
          _charP.set('import_source_folder', String(props.importSourceFolderFilter));
        }
        // Filter params
        if (props.filePathPrefixFilter != null) {
          _charP.set('file_path_prefix', String(props.filePathPrefixFilter));
        }
        const _charSuffix = _charP.size ? `&${_charP.toString()}` : '';
        // Build media type format filter params for count and stream URLs.
        const _formatP = new URLSearchParams();
        _appendMediaTypeParams(_formatP);
        const _formatSuffix = _formatP.size ? `&${_formatP.toString()}` : '';
        // Build filter-menu params for count and stream URLs.
        // Each labelled block corresponds to a separate commit — remove any single
        // block to bisect a regression.
        const _filterP = new URLSearchParams();
        // Filter params: score range
        if (props.minScoreFilter != null) _filterP.set('min_score', String(props.minScoreFilter));
        if (props.maxScoreFilter != null) _filterP.set('max_score', String(props.maxScoreFilter));
        // Filter params: smart score bucket
        if (props.smartScoreBucketFilter != null) _filterP.set('smart_score_bucket', String(props.smartScoreBucketFilter));
        // Filter params: resolution bucket
        if (props.resolutionBucketFilter != null) _filterP.set('resolution_bucket', String(props.resolutionBucketFilter));
        // Filter params: ComfyUI model / LoRA
        (props.comfyuiModelFilter || []).forEach((m) => _filterP.append('comfyui_model', m));
        (props.comfyuiLoraFilter || []).forEach((l) => _filterP.append('comfyui_lora', l));
        // Filter params: tag filters
        (props.tagFilter || []).forEach((t) => _filterP.append('tag', t));
        (props.tagRejectedFilter || []).forEach((t) => _filterP.append('rejected_tag', t));
        (props.tagConfidenceAboveFilter || []).forEach((e) => _filterP.append('tag_confidence_above', e));
        (props.tagConfidenceBelowFilter || []).forEach((e) => _filterP.append('tag_confidence_below', e));
        // Filter params: face bbox filter
        if (props.faceBboxFilter != null) _filterP.set('face_filter', String(props.faceBboxFilter));
        // Filter params: impossible-tag sources (repeatable, OR'd)
        (props.impossibleSources || []).forEach((s) => _filterP.append('impossible_tag_source', s));
        // Filter params: shared only
        if (props.sharedOnlyFilter) _filterP.set('shared_only', 'true');
        // Filter params: hidden-tag filter
        if (props.applyTagFilter) _filterP.set('apply_tag_filter', 'true');
        const _filterSuffix = _filterP.size ? `&${_filterP.toString()}` : '';
        const streamBase =
          `${props.backendUrl}/pictures/stream?fields=grid&grid_lite=true&stack_leaders_only=true${_charSuffix}${_sortSuffix}${_formatSuffix}${_filterSuffix}`;

        async function timeRequest(requestPromise) {
          const startedAt = getNowMs();
          const response = await requestPromise;
          return {
            response,
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
                thumbnail_width: pic.thumbnail_width ?? existing?.thumbnail_width,
                thumbnail_height: pic.thumbnail_height ?? existing?.thumbnail_height,
              };
            }
          }
          allGridImages.value = grid;
        };

        // 1. Fast total count — single indexed SQL query.
        const countStartedAt = getNowMs();
        const countRes = await apiClient.get(`${props.backendUrl}/pictures/count?stack_leaders_only=true${_charSuffix}${_formatSuffix}${_filterSuffix}`);
        if (fetchAllGridImages.lastRequestId !== requestId) return;
        fetchPhaseTimings.countMs = Math.max(0, getNowMs() - countStartedAt);
        const total =
          typeof countRes.data?.count === 'number' ? countRes.data.count : 0;

        // 2. Pre-build placeholder grid — scroll area immediately reflects full size.
        const placeholderStartedAt = getNowMs();
        const cols = props.columns || 1;
        // Compute window count from actual viewport capacity so visibleEnd covers
        // all initially visible items even when the viewport shows more than VIEW_WINDOW.
        // Derive cell height from clientWidth/cols (square thumbnails) rather than
        // rowHeight?.value, which may still hold the initial thumbnailSize estimate
        // when this runs (DOM measurement via updateRowHeightFromGrid is async).
        const _fastViewW = scrollWrapper.value?.clientWidth || 0;
        const _fastViewH = scrollWrapper.value?.clientHeight || 0;
        const _effectiveRowHeight0 = _fastViewW > 0
          ? Math.round(_fastViewW / cols) + (props.compactMode ? 0 : 24)
          : (rowHeight?.value > 0 ? rowHeight.value : Math.round(
              Math.min(384, Math.max(128, props.thumbnailSize || 128)) +
              (props.compactMode ? 0 : 24),
            ));
        const _viewportItemCount0 = _fastViewH > 0 && _effectiveRowHeight0 > 0
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
        fetchPhaseTimings.placeholderMs = Math.max(0, getNowMs() - placeholderStartedAt);

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
        const potentialLastBatchStart = Math.max(FIRST_BATCH, total - LAST_BATCH);
        const backgroundGap = Math.max(0, potentialLastBatchStart - FIRST_BATCH);
        // Fetch tail in parallel with the first batch whenever the gap is large
        // enough to matter for user responsiveness.  Use a fixed threshold of
        // 1000 items — independent of BG_BATCH — so that medium-sized
        // collections (gap 1000–5000) still get the tail batch early instead of
        // waiting for a single large background request to complete.
        const TAIL_THRESHOLD = 1000;
        const shouldFetchTailEarly = total > FIRST_BATCH && backgroundGap > TAIL_THRESHOLD;
        // Effective end of the background region: extends to `total` when we
        // skip the early tail so every item is eventually fetched.
        const lastBatchStart = shouldFetchTailEarly ? potentialLastBatchStart : total;

        // Kick off the first batch and the optional tail batch concurrently, but
        // do NOT block visible-thumbnail loading on the tail (which fills
        // off-screen end-of-grid cells and is often the slower of the two).
        // Await the first batch alone, splice it and request its thumbnails, then
        // await the tail. Both requests are already in flight, so the tail is not
        // delayed by this ordering.
        const firstReqPromise = timeRequest(
          apiClient.get(`${streamBase}&offset=0&batch_limit=${FIRST_BATCH}`),
        );
        // Tail batch: for collections where the gap exceeds TAIL_THRESHOLD.
        // `.catch` keeps it from becoming an unhandled rejection if we bail out
        // (stale requestId) before awaiting it below.
        const tailReqPromise = (shouldFetchTailEarly
          ? timeRequest(
              apiClient.get(
                `${streamBase}&offset=${potentialLastBatchStart}&batch_limit=${LAST_BATCH}`,
              ),
            )
          : Promise.resolve(null)
        ).catch(() => null);

        const firstResTimed = await firstReqPromise;
        if (fetchAllGridImages.lastRequestId !== requestId) return;
        fetchPhaseTimings.firstBatchMs = firstResTimed?.elapsedMs ?? null;

        const firstPics = firstResTimed?.response?.data?.pictures ?? [];
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
        if (lastResTimed?.response) {
          const lastPics = lastResTimed?.response?.data?.pictures ?? [];
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
          const bgResTimed = await timeRequest(apiClient.get(
            `${streamBase}&offset=${bgOff}&batch_limit=${limit}`,
          ));
          if (fetchAllGridImages.lastRequestId !== requestId) return;
          const bgOffset = bgOff;
          bgOff += limit;
          const bgNetworkElapsedMs = bgResTimed.elapsedMs;
          const bgUiStartedAt = getNowMs();
          const bgPics = bgResTimed?.response?.data?.pictures ?? [];
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
        fetchPhaseTimings.postProcessMs = Math.max(0, getNowMs() - postProcessStartedAt);
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
          ? images.map((img) => getPictureId(img?.id)).filter((id) => id !== null)
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
        // Don't replace allGridImages while the overlay is open — the filmstrip
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
      if (props.sharedOnlyFilter && !isReadOnly.value) {
        const next = new Set(sharedPictureIds.value);
        for (const img of newImages) {
          if (img.id) next.add(img.id);
        }
        sharedPictureIds.value = next;
      }
      if (isSetOverlapView.value) {
        totalCurrentCategoryCount.value = newImages.length;
      }
      const cols = props.columns || 1;
      // Compute window count from actual viewport capacity so visibleEnd covers
      // all initially visible items even when the viewport shows more than VIEW_WINDOW.
      // Derive cell height from clientWidth/cols (square thumbnails) rather than
      // rowHeight?.value, which may still hold the initial thumbnailSize estimate
      // when this runs (DOM measurement via updateRowHeightFromGrid is async).
      const _slowViewW = scrollWrapper.value?.clientWidth || 0;
      const _slowViewH = scrollWrapper.value?.clientHeight || 0;
      const _effectiveRowHeight1 = _slowViewW > 0
        ? Math.round(_slowViewW / cols) + (props.compactMode ? 0 : 24)
        : (rowHeight?.value > 0 ? rowHeight.value : Math.round(
            Math.min(384, Math.max(128, props.thumbnailSize || 128)) +
            (props.compactMode ? 0 : 24),
          ));
      const _viewportItemCount1 = _slowViewH > 0 && _effectiveRowHeight1 > 0
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
      imagesError.value = e.message;
      // Don't wipe the grid on a transient error while the overlay is open —
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
  }

  async function fetchAllPicturesCount() {
    try {
      const res = await apiClient.get(
        `${props.backendUrl}/characters/${props.allPicturesId}/summary${props.applyTagFilter ? "?apply_tag_filter=true" : ""}`,
      );
      const data = await res.data;
      totalAllPicturesCount.value = Number(data.image_count) || 0;
    } catch (e) {
      console.warn("[ImageGrid.vue] Failed to fetch all pictures count:", e);
    }

    try {
      let url = `${props.backendUrl}/characters/${props.allPicturesId}/summary`;
      const selectedCharacter = String(props.selectedCharacter ?? "");
      if (isSetOverlapView.value) {
        totalCurrentCategoryCount.value = Number(allGridImages.value.length) || 0;
        return;
      }
      const selectedSetId = primarySelectedSetId.value;
      if (
        selectedSetId !== null &&
        selectedSetId !== undefined &&
        String(selectedSetId) !== ""
      ) {
        const setRes = await apiClient.get(`${props.backendUrl}/picture_sets`);
        const setList = await setRes.data;
        const selectedSetNumericId = Number(selectedSetId);
        const selectedSet = Array.isArray(setList)
          ? setList.find((item) => {
              const itemId = Number(item?.id);
              if (Number.isFinite(selectedSetNumericId)) {
                return Number.isFinite(itemId) && itemId === selectedSetNumericId;
              }
              return String(item?.id) === String(selectedSetId);
            })
          : null;
        totalCurrentCategoryCount.value = Number(selectedSet?.picture_count) || 0;
        return;
      }
      if (selectedCharacter === String(props.allPicturesId)) {
        if (props.projectViewMode === "project") {
          const pid =
            props.selectedProjectId != null
              ? props.selectedProjectId
              : "UNASSIGNED";
          url = `${props.backendUrl}/projects/${pid}/summary`;
        }
      } else if (selectedCharacter === String(props.unassignedPicturesId)) {
        if (props.projectViewMode === "project") {
          const pid =
            props.selectedProjectId != null
              ? props.selectedProjectId
              : "UNASSIGNED";
          url = `${props.backendUrl}/characters/${props.unassignedPicturesId}/summary?project_id=${pid}`;
        } else {
          url = `${props.backendUrl}/characters/${props.unassignedPicturesId}/summary`;
        }
      } else if (selectedCharacter === String(props.scrapheapPicturesId)) {
        url = `${props.backendUrl}/characters/${props.scrapheapPicturesId}/summary`;
      } else if (
        selectedCharacter &&
        !hasSetSelection.value &&
        selectedCharacter !== String(props.allPicturesId)
      ) {
        if (props.projectViewMode === "project") {
          const pid =
            props.selectedProjectId != null
              ? props.selectedProjectId
              : "UNASSIGNED";
          url = `${props.backendUrl}/characters/${selectedCharacter}/summary?project_id=${pid}`;
        } else {
          url = `${props.backendUrl}/characters/${selectedCharacter}/summary`;
        }
      }

      if (props.applyTagFilter) {
        url += (url.includes("?") ? "&" : "?") + "apply_tag_filter=true";
      }

      const scopedRes = await apiClient.get(url);
      const scopedData = await scopedRes.data;
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
