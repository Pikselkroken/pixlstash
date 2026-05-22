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
      similarityCharacter: props.similarityCharacter ?? null,
      comfyuiModelFilter: props.comfyuiModelFilter ?? [],
      comfyuiLoraFilter: props.comfyuiLoraFilter ?? [],
      referenceFolderIdFilter: props.referenceFolderIdFilter ?? null,
      filePathPrefixFilter: props.filePathPrefixFilter ?? null,
      importSourceFolderFilter: props.importSourceFolderFilter ?? null,
      unassignedOnlyFilter: props.unassignedOnlyFilter ?? false,
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
      params.append("character_id", props.selectedCharacter);
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
    let sortedFetchStartedAt = 0;
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

      // ─── FAST GRID PATH ─────────────────────────────────────────────────────
      // Bypasses sort/filter/search to establish the optimised loading baseline.
      // Set USE_FAST_GRID_PATH = false to restore sort/filter/search features.
      // Strategy:
      //   1. Fast SELECT COUNT(*) → total
      //   2. Pre-build placeholder grid so END key works before streaming starts
      //   3. First batch (visible area) + last batch (END cells) in parallel
      //   4. Background stream fills the middle at BG_BATCH rows per request
      const USE_FAST_GRID_PATH = true;
      // Fast path is not applicable to text search (Python-level post-filter
      // breaks LIMIT/OFFSET count accuracy) or non-streamable sort views.
      const _hasSearch = !!props.searchQuery?.trim();
      const _isLikenessSort =
        props.selectedSort === "CHARACTER_LIKENESS" ||
        props.selectedSort === LIKENESS_GROUPS_SORT_KEY;
      // Fast path is also not applicable when the character view requires
      // special backend logic that either returns null for count (UNASSIGNED)
      // or bypasses count_only entirely (non-numeric special views like SCRAPHEAP).
      const _fastCharIds = normalizedSelectedCharacterIds.value;
      const _fastSelChar = props.selectedCharacter;
      const _isUnassignedView =
        _fastSelChar === props.allPicturesId && !!props.unassignedOnlyFilter;
      const _isSpecialCharView =
        _fastSelChar != null &&
        _fastSelChar !== '' &&
        _fastSelChar !== props.allPicturesId &&
        !String(_fastSelChar).match(/^\d+$/);
      if (USE_FAST_GRID_PATH && !_hasSearch && !_isLikenessSort && !_isUnassignedView && !_isSpecialCharView) {
        // For sorted fetches the progress bar is meaningless in the fast path
        // — data is already in the DB, LIMIT/OFFSET is instant.
        if (isSortedFetch && options?.showProgress === true) {
          completeSmartScoreProgress(loadId, 0, true);
        }
        const FIRST_BATCH = 200; // visible area + render buffer
        const LAST_BATCH = 200;  // cells visible when pressing END
        const BG_BATCH = 1000;   // background fill rate
        // Pass sort/descending to the stream so pictures arrive in the right
        // order.  Count URL stays param-free — sort never affects COUNT(*).
        const _sort = props.selectedSort?.trim();
        const _desc = typeof props.selectedDescending === 'boolean'
          ? props.selectedDescending
          : true;
        const _sortSuffix = _sort
          ? `&sort=${encodeURIComponent(_sort)}&descending=${_desc}`
          : '';
        // Build character + project filter params for count and stream URLs.
        const _charP = new URLSearchParams();
        if (_fastCharIds.length > 1) {
          for (const id of _fastCharIds) _charP.append('character_ids', String(id));
          _charP.set('character_mode', props.characterMultiMode ?? 'union');
          // Only apply project filter when all selected characters share the same project.
          if (props.projectViewMode === 'project') {
            const _pidSet = new Set(_fastCharIds.map(id => props.characterProjectIds?.[id] ?? null));
            if (_pidSet.size === 1) {
              const _pid = [..._pidSet][0];
              _charP.set('project_id', _pid != null ? String(_pid) : 'UNASSIGNED');
            }
          }
        } else if (
          _fastSelChar != null &&
          _fastSelChar !== '' &&
          _fastSelChar !== props.allPicturesId
        ) {
          _charP.set('character_id', String(_fastSelChar));
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
        const _charSuffix = _charP.size ? `&${_charP.toString()}` : '';
        const streamBase =
          `${props.backendUrl}/pictures/stream?fields=grid&stack_leaders_only=true${_charSuffix}${_sortSuffix}`;

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
                hands: existing?.hands ?? [],
                penalised_tags: existing?.penalised_tags ?? [],
                thumbnail_width: pic.thumbnail_width ?? existing?.thumbnail_width,
                thumbnail_height: pic.thumbnail_height ?? existing?.thumbnail_height,
              };
            }
          }
          allGridImages.value = grid;
        };

        // 1. Fast total count — single indexed SQL query.
        const countRes = await apiClient.get(`${props.backendUrl}/pictures/count?stack_leaders_only=true${_charSuffix}`);
        if (fetchAllGridImages.lastRequestId !== requestId) return;
        const total =
          typeof countRes.data?.count === 'number' ? countRes.data.count : 0;

        // 2. Pre-build placeholder grid — scroll area immediately reflects full size.
        const cols = props.columns || 1;
        const windowCount = Math.max(cols, divisibleViewWindow.value || cols);
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

        if (total === 0) {
          hasLoadedOnce.value = true;
          initialRender.value = false;
          lastFetchSuccess.value = { key: fetchKey, at: Date.now() };
          return;
        }

        // 3. First + last batches in parallel.
        // lastBatchStart: where the tail batch begins.  When total fits inside
        // FIRST_BATCH + LAST_BATCH the tail immediately follows the head.
        const lastBatchStart = Math.max(FIRST_BATCH, total - LAST_BATCH);
        const [firstRes, lastRes] = await Promise.all([
          apiClient.get(`${streamBase}&offset=0&batch_limit=${FIRST_BATCH}`),
          // Tail batch: needed whenever there are any cells beyond the first batch.
          total > FIRST_BATCH
            ? apiClient.get(
                `${streamBase}&offset=${lastBatchStart}&batch_limit=${LAST_BATCH}`,
              )
            : Promise.resolve(null),
        ]);
        if (fetchAllGridImages.lastRequestId !== requestId) return;

        const firstPics = firstRes?.data?.pictures ?? [];
        splicePictures(firstPics, 0);
        hasLoadedOnce.value = true;
        initialRender.value = false;
        const prefetchEnd = Math.min(
          total,
          visibleEnd.value + (divisibleViewWindow.value || windowCount),
        );
        fetchThumbnailsBatch(visibleStart.value, prefetchEnd);

        if (lastRes) {
          const lastPics = lastRes?.data?.pictures ?? [];
          splicePictures(lastPics, lastBatchStart);
        }

        lastFetchSuccess.value = { key: fetchKey, at: Date.now() };

        // 4. Background stream: fill the gap between first and last batches.
        let bgOffset = FIRST_BATCH;
        while (bgOffset < lastBatchStart) {
          if (fetchAllGridImages.lastRequestId !== requestId) return;
          const limit = Math.min(BG_BATCH, lastBatchStart - bgOffset);
          const res = await apiClient.get(
            `${streamBase}&offset=${bgOffset}&batch_limit=${limit}`,
          );
          if (fetchAllGridImages.lastRequestId !== requestId) return;
          const bgPics = res?.data?.pictures ?? [];
          splicePictures(bgPics, bgOffset);
          updateVisibleThumbnails();
          bgOffset += limit;
          await nextTick();
        }

        return; // early return — existing sort/filter/search paths are bypassed
      }
      // ─── END FAST GRID PATH ─────────────────────────────────────────────────

      if (props.selectedSort === LIKENESS_GROUPS_SORT_KEY) {
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
      } else if (props.searchQuery && props.searchQuery.trim()) {
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
      } else if (hasSetSelection.value && !isSetOverlapView.value) {
        const params = buildPictureIdsQueryParams();
        const url = `${props.backendUrl}/picture_sets/${primarySelectedSetId.value}${
          params ? `?${params}` : ""
        }`;
        const res = await apiClient.get(url);
        const data = await res.data;
        images = data.pictures || [];
      } else {
        // STREAMING PATH — incrementally fetches and commits batches.
        //
        // Completion is determined by the backend's `done` flag (derived from
        // the SQL pre-filter row count), not by comparing the response length
        // against `batch_limit`.  This is the key reliability property: even
        // when a batch is shrunk by post-filtering (hidden tags, stack-leader
        // dedup) the loader keeps fetching until the server signals done.
        const params = buildPictureIdsQueryParams();
        // 100 items fills the visible viewport + render buffer in one batch;
        // subsequent batches use a larger size for background throughput.
        const FIRST_BATCH_LIMIT = 100;
        const NEXT_BATCH_LIMIT = 1000;
        const MAX_BATCHES = 200; // safety cap; 200 * 1000 = 200k rows
        let streamOffset = 0;
        let streamBatchLimit = FIRST_BATCH_LIMIT;
        let firstBatch = true;
        let accumulatedRaw = [];
        let batchesFetched = 0;
        // Fire a fast SELECT COUNT(*) in parallel with the stream so that the
        // grid can be pre-extended with placeholders before streaming finishes.
        // This lets the END key jump to the real bottom immediately.
        const countUrl = `${props.backendUrl}/pictures/count${params ? `?${params}` : ''}`;
        const countPromise = apiClient
          .get(countUrl)
          .then((r) => (typeof r.data?.count === 'number' ? r.data.count : null))
          .catch(() => null);

        const commitStreamingBatch = (isFirst) => {
          lastFetchedGridImages.value = accumulatedRaw.slice();
          syncExpandAllStacksFromFetchedImages();
          // TODO: re-enable collapseStackImages once streaming baseline is stable
          const collapsed = accumulatedRaw;
          const nextIdSet = new Set(
            Array.isArray(collapsed)
              ? collapsed
                  .map((img) => getPictureId(img?.id))
                  .filter((id) => id !== null)
              : [],
          );
          if (isFirst) {
            const shouldHighlight =
              highlightNextFetch.value && hasLoadedOnce.value;
            if (shouldHighlight) {
              const newIds = [];
              nextIdSet.forEach((id) => {
                if (!previousImageIds.has(id)) newIds.push(id);
              });
              if (newIds.length) triggerNewImageHighlight(newIds);
            }
            previousImageIds.clear();
          }
          nextIdSet.forEach((id) => previousImageIds.add(id));
          if (isFirst) {
            highlightNextFetch.value = false;
            hasLoadedOnce.value = true;
          }
          const newImages = mapGridImages(collapsed);
          if (isFirst) {
            resetThumbnailState();
          }
          if (overlayOpen.value) {
            pendingGridImages.value = newImages;
            pendingOverlayGridRefresh.value = true;
          } else {
            // Preserve any tail placeholders (from the parallel count pre-extension)
            // so they aren't truncated on each batch commit.
            const current = allGridImages.value;
            allGridImages.value =
              current.length > newImages.length
                ? [...newImages, ...current.slice(newImages.length)]
                : newImages;
          }
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
          if (isFirst) {
            const cols = props.columns || 1;
            const windowCount = Math.max(
              cols,
              divisibleViewWindow.value || cols,
            );
            if (!fetchStartedWithPreserveScroll) {
              visibleStart.value = 0;
              visibleEnd.value = Math.min(newImages.length, windowCount);
            } else {
              visibleEnd.value = Math.min(visibleEnd.value, newImages.length);
              if (visibleStart.value > visibleEnd.value)
                visibleStart.value = Math.max(0, visibleEnd.value - 1);
            }
            // Always fire thumbnail fetch for the first batch regardless of
            // initialRender state. A second fetch call (e.g. after
            // apply_tag_filter is applied) replaces allGridImages with fresh
            // objects that have null thumbnails; without this, those images
            // would have no thumbnail URL until the next updateVisibleThumbnails
            // call. fetchThumbnailsBatch synchronously pre-fills the URLs from
            // imported_at so images start rendering immediately.
            const prefetchEnd = Math.min(
              newImages.length,
              visibleEnd.value + divisibleViewWindow.value,
            );
            fetchThumbnailsBatch(visibleStart.value, prefetchEnd);
          }
          return null;
        };

        while (batchesFetched < MAX_BATCHES) {
          const url = `${props.backendUrl}/pictures/stream?offset=${streamOffset}&batch_limit=${streamBatchLimit}${
            params ? `&${params}` : ""
          }`;
          const res = await apiClient.get(url);
          const data = await res.data;
          if (fetchAllGridImages.lastRequestId !== requestId) {
            if (isSortedFetch && options?.showProgress === true)
              completeSmartScoreProgress(loadId, 0, false);
            return;
          }
          const batchPictures = Array.isArray(data?.pictures)
            ? data.pictures
            : [];
          accumulatedRaw = accumulatedRaw.concat(batchPictures);
          commitStreamingBatch(firstBatch);
          const isFirstBatch = firstBatch;
          firstBatch = false;
          batchesFetched += 1;
          if (isFirstBatch) {
            // Expand the render buffer immediately so the prefetched cells
            // are added to the DOM and can show thumbnails as they arrive.
            // Thumbnail URLs are already pre-filled synchronously by
            // fetchThumbnailsBatch, so there is no need to await the POST.
            initialRender.value = false;
            // When the parallel count response arrives, pre-extend allGridImages
            // with placeholder items so the END key jumps to the real bottom
            // before streaming has finished filling every item.
            countPromise.then((total) => {
              if (fetchAllGridImages.lastRequestId !== requestId) return;
              if (overlayOpen.value) return;
              if (typeof total !== 'number') return;
              const current = allGridImages.value;
              if (total <= current.length) return;
              const extra = total - current.length;
              const base = current.length;
              const placeholders = Array.from({ length: extra }, (_, i) => ({
                id: null,
                idx: base + i,
              }));
              allGridImages.value = [...current, ...placeholders];
            });
          } else {
            // Keep thumbnails current for any newly visible range.
            updateVisibleThumbnails();
          }
          if (data?.done) break;
          if (
            typeof data?.next_offset !== "number" ||
            data.next_offset <= streamOffset
          ) {
            // Defensive: backend signalled non-progressing next_offset.
            break;
          }
          streamOffset = data.next_offset;
          streamBatchLimit = NEXT_BATCH_LIMIT;
          // Yield to the event loop so the UI can paint between batches.
          await nextTick();
        }
        images = accumulatedRaw;
        // Trim leftover tail placeholders from the parallel count pre-extension.
        // The SELECT COUNT(*) may be a slight over-estimate when the hidden-tag
        // post-filter removes pictures that were counted at the SQL layer.
        if (!overlayOpen.value && allGridImages.value.length > images.length) {
          allGridImages.value = allGridImages.value.slice(0, images.length);
        }
        // Streaming path has already applied all per-batch post-processing.
        // Skip the legacy single-shot post-process block below.
        if (fetchAllGridImages.lastRequestId !== requestId) {
          if (isSortedFetch && options?.showProgress === true)
            completeSmartScoreProgress(loadId, 0, false);
          return;
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
        // Mark the streaming branch as handled and short-circuit the legacy
        // post-process block by jumping ahead via a flag.
        fetchAllGridImages._streamingHandled = true;
      }
      if (fetchAllGridImages.lastRequestId !== requestId) {
        if (isSortedFetch && options?.showProgress === true)
          completeSmartScoreProgress(loadId, 0, false);
        return;
      }
      if (fetchAllGridImages._streamingHandled) {
        // Streaming branch already did its own post-processing per batch and
        // finalized the load. Reset the flag and skip the legacy single-shot
        // post-process below.
        fetchAllGridImages._streamingHandled = false;
      } else {
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
      const windowCount = Math.max(cols, divisibleViewWindow.value || cols);
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
      }
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

  const debouncedFetchAllGridImages = debounce(fetchAllGridImages, 200);

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
