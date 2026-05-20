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
        const params = buildPictureIdsQueryParams();
        // Only use allowed parameters: sort, offset, limit, threshold
        const url = `${props.backendUrl}/pictures?offset=0${
          params ? `&${params}` : ""
        }`;
        const res = await apiClient.get(url);
        const data = await res.data;
        images = data;
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
