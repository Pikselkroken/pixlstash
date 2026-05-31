import { ref, computed } from "vue";
import {
  applyStackOrderToList,
  buildStackLeaderMap,
  buildStackReorderedMembers,
  getStackBadgeCount,
  getPictureStackId,
  normalizeStackIdValue,
  sortStackMembers,
} from "../utils/stack.js";
import {
  applyStackBackgroundAlpha,
  arraysEqualByString,
  getStackColor,
  getStackColorIndexFromId,
  shiftRangesForDelta,
} from "../utils/utils.js";
import { getPictureId } from "../utils/media.js";
import { apiClient } from "../utils/apiClient";

const LIKENESS_GROUPS_SORT_KEY = "LIKENESS_GROUPS";

/**
 * Manages stack expand/collapse state, stack visual styling, stack reorder
 * drag, and high-level stack CRUD operations (create, dissolve, reorder).
 *
 * @param {Object} deps - Reactive refs from other composables / ImageGrid
 * @param {Object} props - Component props
 * @param {Function} emit - Component emit function
 * @param {Object} callbacks - Functions provided by ImageGrid
 */
export function useStackOrdering(
  {
    allGridImages,
    lastFetchedGridImages,
    loadedRanges,
    visibleStart,
    visibleEnd,
    renderBuffer,
    divisibleViewWindow,
    stackReorderDrag,
    stackReorderHoverId,
    stackReorderHoverSide,
    setStackReorderHoverId,
    setStackReorderHoverSide,
    selectedImageIds,
    preserveScrollOnNextFetch,
  },
  props,
  emit,
  {
    invalidateVisibleThumbnailRanges,
    updateVisibleThumbnails,
    debouncedFetchAllGridImages,
    fetchThumbnailsForRangeNow,
    maybeRefreshThumbnailsForRange,
    markVisibleFetchSuppressedForExpand,
    clearSelection,
    getPendingRanges,
    setPendingRanges,
  },
) {
  // ── Stack expand/collapse state ───────────────────────────────────────────
  const expandedStackIds = ref(new Set());
  const expandedStackMembers = ref(new Map());
  const expandedStackLoading = ref(new Set());
  const expandedStackLoadPromises = new Map();

  // ── Stack visual order map ────────────────────────────────────────────────
  const stackVisualOrderMap = computed(() => {
    const images = allGridImages.value;
    const cols = Math.max(1, props.columns || 1);
    const result = new Map();
    let stackAppearanceIndex = 0;
    for (let i = 0; i < images.length; i++) {
      const sid = getPictureStackId(images[i]);
      if (sid != null && !result.has(sid)) {
        const row = Math.floor(i / cols);
        const col = i % cols;
        result.set(sid, { index: stackAppearanceIndex, row, col });
        stackAppearanceIndex++;
      }
    }
    return result;
  });

  // ── Private helpers ───────────────────────────────────────────────────────
  function setGridIndices(items) {
    for (let i = 0; i < items.length; i += 1) {
      items[i].idx = i;
    }
  }

  function hydrateGridImage(img, idx, existingById) {
    const existing = img?.id ? existingById.get(getPictureId(img.id)) : null;
    return {
      ...img,
      idx,
      thumbnail: existing?.thumbnail ?? null,
      penalised_tags: Array.isArray(existing?.penalised_tags)
        ? existing.penalised_tags
        : [],
      faces: Array.isArray(existing?.faces) ? existing.faces : [],
      thumbnail_width: existing?.thumbnail_width ?? img?.thumbnail_width,
      thumbnail_height: existing?.thumbnail_height ?? img?.thumbnail_height,
    };
  }

  function adjustScrollWindowForDelta(changeIndex, delta, totalLength) {
    if (!Number.isFinite(delta) || delta === 0) return;
    if (changeIndex < visibleStart.value) {
      visibleStart.value = Math.max(0, visibleStart.value + delta);
    }
    if (changeIndex < visibleEnd.value) {
      visibleEnd.value = Math.max(0, visibleEnd.value + delta);
    }
    const maxEnd = Math.max(0, totalLength);
    if (visibleEnd.value > maxEnd) visibleEnd.value = maxEnd;
    if (visibleStart.value > visibleEnd.value) {
      visibleStart.value = Math.max(0, visibleEnd.value - 1);
    }
  }

  // ── Grid data mapping (used by stack expand/collapse) ─────────────────────
  function collapseStackImages(images) {
    if (!Array.isArray(images) || images.length === 0) return [];
    const counts = new Map();
    for (const img of images) {
      const stackId = getPictureStackId(img);
      if (!stackId) continue;
      counts.set(stackId, (counts.get(stackId) || 0) + 1);
    }
    if (!counts.size) return images;
    const leaders = buildStackLeaderMap(images);
    const seen = new Set();
    const collapsed = [];
    for (const img of images) {
      const stackId = getPictureStackId(img);
      if (!stackId) {
        collapsed.push(img);
        continue;
      }
      const leaderId = leaders.get(stackId);
      if (leaderId && img?.id != null && String(img.id) !== leaderId) {
        continue;
      }
      if (seen.has(stackId)) continue;
      seen.add(stackId);
      const localCount = counts.get(stackId) || 1;
      const serverCount = Number(img?.stack_count ?? img?.stackCount ?? 0);
      const stackCount = Math.max(localCount, serverCount) || 1;
      if (expandedStackIds.value.has(stackId)) {
        const expanded = buildExpandedStackImages(stackId, img, stackCount);
        if (expanded.length) {
          collapsed.push(...expanded);
          continue;
        }
      }
      collapsed.push({
        ...img,
        stackCount,
      });
    }
    return collapsed;
  }

  function mapGridImages(images) {
    const existingById = new Map(
      allGridImages.value
        .filter((img) => img && img.id != null)
        .map((img) => [getPictureId(img.id), img]),
    );
    const uniqueImages = Array.isArray(images)
      ? (() => {
          const seen = new Set();
          return images.filter((img) => {
            const id = getPictureId(img?.id);
            if (id == null) return true;
            if (seen.has(id)) return false;
            seen.add(id);
            return true;
          });
        })()
      : [];
    return uniqueImages.map((img, i) => {
      return hydrateGridImage(img, i, existingById);
    });
  }

  // ── Stack visual helpers ──────────────────────────────────────────────────
  function getStackCardColor(img) {
    if (!img) return null;
    if (typeof img.stackColor === "string" && img.stackColor) {
      return img.stackColor;
    }
    const stackIndex =
      typeof img.stackIndex === "number"
        ? img.stackIndex
        : typeof img.stack_index === "number"
          ? img.stack_index
          : null;
    if (typeof stackIndex === "number") {
      return getStackColor(stackIndex);
    }
    const stackId = getPictureStackId(img);
    if (stackId == null) return null;
    const visualEntry = stackVisualOrderMap.value.get(stackId);
    if (visualEntry != null) {
      return getStackColor(visualEntry.index, visualEntry.row, visualEntry.col);
    }
    const index = getStackColorIndexFromId(stackId);
    if (index === null) return null;
    return getStackColor(index);
  }

  function isStackExpandedForImage(img) {
    const stackId = getPictureStackId(img);
    if (!stackId) return false;
    return expandedStackIds.value.has(stackId);
  }

  // ── Expand/collapse helpers ───────────────────────────────────────────────
  function getRenderedStackMemberIds(stackId) {
    if (!stackId) return [];
    return allGridImages.value
      .filter((item) => getPictureStackId(item) === stackId && item?.id != null)
      .map((item) => String(item.id));
  }

  function getLocalStackMembers(stackId) {
    if (!stackId) return [];
    const source = Array.isArray(lastFetchedGridImages.value)
      ? lastFetchedGridImages.value
      : [];
    if (!source.length) return [];
    const members = source.filter(
      (img) => getPictureStackId(img) === stackId,
    );
    const activeSort = String(props.selectedSort || "").toUpperCase();
    const useBackendOrder =
      !!activeSort && activeSort !== LIKENESS_GROUPS_SORT_KEY;
    return useBackendOrder ? members : sortStackMembers(members);
  }

  function cacheExpandedStackMembers(stackId, members) {
    if (!stackId || !Array.isArray(members) || members.length === 0)
      return false;
    const activeSort = String(props.selectedSort || "").toUpperCase();
    const useBackendOrder =
      !!activeSort && activeSort !== LIKENESS_GROUPS_SORT_KEY;
    const sorted = useBackendOrder ? members.slice() : sortStackMembers(members);
    const ordered = sorted
      .filter((img) => img && img.id != null)
      .map((img) =>
        img.stack_id !== undefined || img.stackId !== undefined
          ? img
          : { ...img, stack_id: normalizeStackIdValue(stackId) },
      );
    if (!ordered.length) return false;
    const nextMembers = new Map(expandedStackMembers.value);
    nextMembers.set(stackId, {
      ids: ordered.map((img) => String(img.id)),
      images: ordered,
    });
    expandedStackMembers.value = nextMembers;
    return true;
  }

  function getExpandedStackCount(stackId, fallbackCount) {
    const entry = expandedStackMembers.value.get(stackId);
    const ids = Array.isArray(entry?.ids) ? entry.ids : [];
    if (ids.length) return ids.length;
    const images = Array.isArray(entry?.images) ? entry.images : [];
    if (images.length) return images.length;
    const fallback = Number(fallbackCount ?? 0);
    return Number.isFinite(fallback) && fallback > 0 ? fallback : 1;
  }

  function buildExpandedStackImages(stackId, fallbackImg, stackCount) {
    const entry = expandedStackMembers.value.get(stackId);
    const ids = Array.isArray(entry?.ids) ? entry.ids : [];
    const images = Array.isArray(entry?.images) ? entry.images : [];
    const activeSort = String(props.selectedSort || "").toUpperCase();
    const useBackendOrder =
      !!activeSort && activeSort !== LIKENESS_GROUPS_SORT_KEY;
    const sourceImages = ids.length
      ? images
      : useBackendOrder
        ? images.slice()
        : sortStackMembers(images);
    const imageById = new Map(
      sourceImages
        .filter((img) => img && img.id != null)
        .map((img) => [String(img.id), img]),
    );
    const ordered = [];
    const seen = new Set();
    const stackValue = normalizeStackIdValue(stackId);
    const addImage = (img) => {
      if (!img || img.id == null) return;
      const key = String(img.id);
      if (seen.has(key)) return;
      seen.add(key);
      const withStack =
        img.stack_id !== undefined || img.stackId !== undefined
          ? img
          : { ...img, stack_id: stackValue };
      ordered.push(withStack);
    };

    if (ids.length) {
      for (const id of ids) {
        addImage(imageById.get(String(id)));
      }
    } else {
      for (const img of sourceImages) {
        addImage(img);
      }
    }

    if (fallbackImg?.id != null && !seen.has(String(fallbackImg.id))) {
      addImage(fallbackImg);
    }

    const fallbackIdStr =
      fallbackImg?.id != null ? String(fallbackImg.id) : null;
    if (fallbackIdStr) {
      const headerIdx = ordered.findIndex(
        (img) => String(img?.id) === fallbackIdStr,
      );
      if (headerIdx !== -1) {
        ordered[headerIdx] = { ...ordered[headerIdx], stackCount };
      }
    } else if (ordered.length) {
      ordered[0] = { ...ordered[0], stackCount };
    }
    return ordered;
  }

  function insertExpandedStackMembers(stackId, fallbackCount) {
    if (!stackId) return 0;
    const items = allGridImages.value.slice();
    if (!items.length) return 0;
    const headerIndex = items.findIndex(
      (item) => getPictureStackId(item) === stackId,
    );
    if (headerIndex === -1) return 0;
    const header = items[headerIndex];
    const stackCount = getExpandedStackCount(
      stackId,
      fallbackCount ?? header?.stackCount,
    );
    const expanded = buildExpandedStackImages(stackId, header, stackCount);
    if (!expanded.length) return 0;
    const headerId = header?.id != null ? String(header.id) : null;
    const filtered = items.filter((item) => {
      if (getPictureStackId(item) !== stackId) return true;
      if (headerId && item?.id != null) {
        return String(item.id) === headerId;
      }
      return false;
    });
    const filteredHeaderIndex = filtered.findIndex(
      (item) => getPictureStackId(item) === stackId,
    );
    if (filteredHeaderIndex === -1) return 0;
    const existingById = new Map(
      allGridImages.value
        .filter((img) => img && img.id != null)
        .map((img) => [getPictureId(img.id), img]),
    );
    const expandedHeader = expanded[0];
    const mergedHeader = hydrateGridImage(
      { ...expandedHeader, ...header, stackCount },
      0,
      existingById,
    );
    const insertItems = expanded
      .filter((img) => img && img.id != null)
      .filter((img) => String(img.id) !== headerId)
      .map((img) => hydrateGridImage(img, 0, existingById));
    const insertIndex = filteredHeaderIndex + 1;
    const before = filtered.slice(0, filteredHeaderIndex);
    const after = filtered.slice(filteredHeaderIndex + 1);
    const result = [...before, mergedHeader, ...insertItems, ...after];
    setGridIndices(result);
    allGridImages.value = result;
    const insertCount = insertItems.length;
    const removedExistingCount = items.length - filtered.length;
    const netDelta = insertCount - removedExistingCount;
    const affectedEnd =
      insertIndex + Math.max(insertCount, removedExistingCount);
    if (netDelta !== 0 || removedExistingCount > 0) {
      loadedRanges.value = shiftRangesForDelta(
        loadedRanges.value,
        insertIndex,
        netDelta,
        affectedEnd,
      );
      setPendingRanges(
        shiftRangesForDelta(
          getPendingRanges(),
          insertIndex,
          netDelta,
          affectedEnd,
        ),
      );
      adjustScrollWindowForDelta(insertIndex, netDelta, result.length);
    }
    if (insertCount > 0) {
      markVisibleFetchSuppressedForExpand(
        insertIndex,
        insertIndex + insertCount + 1,
      );
      fetchThumbnailsForRangeNow(
        insertIndex,
        insertIndex + insertCount + 1,
        "stack-expand-insert",
      );
    } else {
      maybeRefreshThumbnailsForRange(insertIndex, insertIndex + 1);
    }
    return insertCount;
  }

  function removeExpandedStackMembers(stackId) {
    if (!stackId) return;
    const items = allGridImages.value.slice();
    if (!items.length) return;
    const headerIndex = items.findIndex(
      (item) => getPictureStackId(item) === stackId,
    );
    if (headerIndex === -1) return;
    let removedCount = 0;
    let keptHeader = false;
    const filtered = items.filter((item) => {
      if (getPictureStackId(item) !== stackId) return true;
      if (!keptHeader) {
        keptHeader = true;
        return true;
      }
      removedCount += 1;
      return false;
    });
    if (filtered.length === items.length) return;
    setGridIndices(filtered);
    allGridImages.value = filtered;
    if (removedCount > 0) {
      const removeStart = headerIndex + 1;
      const removeEnd = headerIndex + 1 + removedCount;
      loadedRanges.value = shiftRangesForDelta(
        loadedRanges.value,
        removeStart,
        -removedCount,
        removeEnd,
      );
      setPendingRanges(
        shiftRangesForDelta(
          getPendingRanges(),
          removeStart,
          -removedCount,
          removeEnd,
        ),
      );
      adjustScrollWindowForDelta(removeStart, -removedCount, filtered.length);
      maybeRefreshThumbnailsForRange(removeStart, removeStart + 1);
    }
  }

  function collectExpandableStackIds(images) {
    if (!Array.isArray(images) || images.length === 0) return [];
    const counts = new Map();
    for (const img of images) {
      const stackId = getPictureStackId(img);
      if (!stackId) continue;
      counts.set(stackId, (counts.get(stackId) || 0) + 1);
    }
    const expandable = new Set();
    for (const img of images) {
      const stackId = getPictureStackId(img);
      if (!stackId) continue;
      const countFromImage = Number(img?.stackCount ?? img?.stack_count ?? 0);
      const countFromPresence = counts.get(stackId) || 0;
      if (countFromImage > 1 || countFromPresence > 1) {
        expandable.add(stackId);
      }
    }
    return Array.from(expandable);
  }

  function emitStackStats() {
    const expandable = collectExpandableStackIds(lastFetchedGridImages.value);
    const expandableSet = new Set(expandable);
    let expanded = 0;
    for (const stackId of expandedStackIds.value || []) {
      if (expandableSet.has(stackId)) {
        expanded += 1;
      }
    }
    emit("update:stack-stats", {
      expanded,
      total: expandable.length,
    });
  }

  function syncExpandAllStacksFromFetchedImages() {
    const autoIds = collectExpandableStackIds(lastFetchedGridImages.value);
    const autoIdSet = new Set(autoIds);
    const currentIds = Array.from(expandedStackIds.value || []);
    const nextIds = new Set(currentIds.filter((id) => autoIdSet.has(id)));
    let changed = false;
    for (const stackId of currentIds) {
      if (!nextIds.has(stackId)) {
        changed = true;
        break;
      }
    }
    if (changed) {
      expandedStackIds.value = nextIds;
    }
  }

  function rebuildGridImagesFromLastFetch() {
    const source = Array.isArray(lastFetchedGridImages.value)
      ? lastFetchedGridImages.value
      : [];
    syncExpandAllStacksFromFetchedImages();
    const collapsed = collapseStackImages(source);
    const newImages = mapGridImages(collapsed);
    allGridImages.value = newImages;
    if (visibleStart.value >= newImages.length) {
      const cols = Math.max(1, props.columns || 1);
      const windowCount = Math.max(cols, divisibleViewWindow.value || cols);
      visibleStart.value = 0;
      visibleEnd.value = Math.min(newImages.length, windowCount);
    } else if (visibleEnd.value > newImages.length) {
      visibleEnd.value = newImages.length;
    }
    invalidateVisibleThumbnailRanges();
    updateVisibleThumbnails();
  }

  async function ensureStackMembersLoaded(stackId, expectedCount = null) {
    if (!stackId) return false;
    const expected = Number(expectedCount ?? 0);
    const minExpected =
      Number.isFinite(expected) && expected > 0 ? expected : 0;
    const localMembers = getLocalStackMembers(stackId);
    if (
      localMembers.length &&
      (minExpected <= 0 || localMembers.length >= minExpected)
    ) {
      cacheExpandedStackMembers(stackId, localMembers);
      return true;
    }
    const existing = expandedStackMembers.value.get(stackId);
    if (existing && Array.isArray(existing.images) && existing.images.length) {
      if (minExpected <= 0 || existing.images.length >= minExpected) {
        return true;
      }
    }
    const inFlight = expandedStackLoadPromises.get(stackId);
    if (inFlight) {
      await inFlight;
      const afterWait = expandedStackMembers.value.get(stackId);
      return !!(
        afterWait &&
        Array.isArray(afterWait.images) &&
        afterWait.images.length
      );
    }

    const loadPromise = (async () => {
      const nextLoading = new Set(expandedStackLoading.value);
      nextLoading.add(stackId);
      expandedStackLoading.value = nextLoading;
      try {
        const stackUrl = new URL(
          `${props.backendUrl}/stacks/${stackId}/pictures`,
        );
        stackUrl.searchParams.set("fields", "grid");
        const activeSort = props.selectedSort ?? "";
        const isStackSort =
          !activeSort || activeSort === LIKENESS_GROUPS_SORT_KEY;
        if (activeSort) {
          stackUrl.searchParams.set("sort", activeSort);
        }
        if (typeof props.selectedDescending === "boolean") {
          stackUrl.searchParams.set(
            "descending",
            props.selectedDescending ? "true" : "false",
          );
        }
        const picsRes = await apiClient.get(stackUrl.toString());
        const picsData = await picsRes.data;
        const pics = Array.isArray(picsData) ? picsData : [];
        const sorted = isStackSort ? sortStackMembers(pics) : pics;
        const ordered = sorted
          .filter((img) => img && img.id != null)
          .map((img) =>
            img.stack_id !== undefined || img.stackId !== undefined
              ? img
              : { ...img, stack_id: normalizeStackIdValue(stackId) },
          );
        const pictureIds = ordered.map((img) => String(img.id));
        const nextMembers = new Map(expandedStackMembers.value);
        nextMembers.set(stackId, {
          ids: pictureIds,
          images: ordered,
        });
        expandedStackMembers.value = nextMembers;
        return true;
      } catch (e) {
        console.error("Failed to load stack members:", e);
        return false;
      } finally {
        const cleared = new Set(expandedStackLoading.value);
        cleared.delete(stackId);
        expandedStackLoading.value = cleared;
        expandedStackLoadPromises.delete(stackId);
      }
    })();

    expandedStackLoadPromises.set(stackId, loadPromise);
    return await loadPromise;
  }

  async function refreshExpandedStacksAfterFetch() {
    const expanded = Array.from(expandedStackIds.value || []);
    if (!expanded.length) return;
    const fetchStart = visibleStart.value;
    const fetchEnd = visibleEnd.value;
    const nextExpanded = new Set(expandedStackIds.value);

    for (const stackId of expanded) {
      removeExpandedStackMembers(stackId);
    }

    const toLoad = [];
    for (const stackId of expanded) {
      const headerIndex = allGridImages.value.findIndex(
        (item) => getPictureStackId(item) === stackId,
      );
      if (headerIndex === -1) {
        nextExpanded.delete(stackId);
        continue;
      }
      if (headerIndex < fetchStart || headerIndex >= fetchEnd) {
        continue;
      }
      const header = allGridImages.value[headerIndex];
      const fallbackCount = header?.stackCount ?? header?.stack_count ?? null;
      toLoad.push({ stackId, fallbackCount });
    }

    const fetchResults = await Promise.all(
      toLoad.map(({ stackId, fallbackCount }) =>
        ensureStackMembersLoaded(stackId, fallbackCount).then((loaded) => ({
          stackId,
          fallbackCount,
          loaded,
        })),
      ),
    );

    for (const { stackId, fallbackCount, loaded } of fetchResults) {
      if (loaded !== false) {
        const insertedCount = insertExpandedStackMembers(stackId, fallbackCount);
        if (insertedCount <= 0) {
          nextExpanded.delete(stackId);
        }
      } else {
        nextExpanded.delete(stackId);
      }
    }

    if (nextExpanded.size !== expandedStackIds.value.size) {
      expandedStackIds.value = nextExpanded;
    }
  }

  async function loadExpandedStacksInView() {
    if (!expandedStackIds.value.size) return;
    const start = Math.max(0, visibleStart.value - renderBuffer.value);
    const end = Math.min(
      allGridImages.value.length,
      visibleEnd.value + renderBuffer.value,
    );
    const slice = allGridImages.value.slice(start, end);
    const seen = new Set();
    const pending = [];
    for (const img of slice) {
      const stackId = getPictureStackId(img);
      if (!stackId || seen.has(stackId)) continue;
      seen.add(stackId);
      if (!expandedStackIds.value.has(stackId)) continue;
      const entry = expandedStackMembers.value.get(stackId);
      if (entry && Array.isArray(entry.images) && entry.images.length > 0)
        continue;
      pending.push(stackId);
    }
    if (!pending.length) return;
    for (const stackId of pending) {
      if (!expandedStackIds.value.has(stackId)) continue;
      const headerIndex = allGridImages.value.findIndex(
        (item) => getPictureStackId(item) === stackId,
      );
      if (headerIndex === -1) continue;
      const header = allGridImages.value[headerIndex];
      const fallbackCount = header?.stackCount ?? header?.stack_count ?? null;
      const loaded = await ensureStackMembersLoaded(stackId, fallbackCount);
      if (loaded !== false && expandedStackIds.value.has(stackId)) {
        removeExpandedStackMembers(stackId);
        const insertedCount = insertExpandedStackMembers(stackId, fallbackCount);
        if (insertedCount <= 0) {
          const nextExpanded = new Set(expandedStackIds.value);
          nextExpanded.delete(stackId);
          expandedStackIds.value = nextExpanded;
        }
      }
    }
  }

  async function expandAllStacks() {
    const autoIds = collectExpandableStackIds(lastFetchedGridImages.value);
    expandedStackIds.value = new Set(autoIds);
    rebuildGridImagesFromLastFetch();
    await refreshExpandedStacksAfterFetch();
  }

  async function collapseAllStacks() {
    expandedStackIds.value = new Set();
    rebuildGridImagesFromLastFetch();
    await refreshExpandedStacksAfterFetch();
  }

  async function toggleStackExpand(img) {
    const stackId = getPictureStackId(img);
    if (!stackId) return;
    if (expandedStackIds.value.has(stackId)) {
      const nextIds = new Set(expandedStackIds.value);
      nextIds.delete(stackId);
      expandedStackIds.value = nextIds;
      removeExpandedStackMembers(stackId);
      return;
    }
    const nextIds = new Set(expandedStackIds.value);
    nextIds.add(stackId);
    expandedStackIds.value = nextIds;
    const stackCount = getStackBadgeCount(img);
    let insertedCount = 0;
    const localMembers = getLocalStackMembers(stackId);
    if (localMembers.length > 1) {
      cacheExpandedStackMembers(stackId, localMembers);
      insertedCount = insertExpandedStackMembers(stackId, stackCount);
    }

    const loaded = await ensureStackMembersLoaded(stackId, stackCount);
    if (!expandedStackIds.value.has(stackId)) {
      return;
    }
    if (loaded !== false) {
      const renderedIds = getRenderedStackMemberIds(stackId);
      const latestEntry = expandedStackMembers.value.get(stackId);
      const latestIds = Array.isArray(latestEntry?.ids) ? latestEntry.ids : [];
      if (
        insertedCount > 0 &&
        latestIds.length &&
        arraysEqualByString(renderedIds, latestIds)
      ) {
        return;
      }
      removeExpandedStackMembers(stackId);
      insertedCount = insertExpandedStackMembers(stackId, stackCount);
      if (insertedCount <= 0) {
        const resetExpanded = new Set(expandedStackIds.value);
        resetExpanded.delete(stackId);
        expandedStackIds.value = resetExpanded;
        removeExpandedStackMembers(stackId);
      }
      return;
    }

    if (insertedCount <= 0) {
      const resetExpanded = new Set(expandedStackIds.value);
      resetExpanded.delete(stackId);
      expandedStackIds.value = resetExpanded;
      removeExpandedStackMembers(stackId);
    }
  }

  function prefetchStackMembers(img) {
    const stackId = getPictureStackId(img);
    if (!stackId) return;
    void ensureStackMembersLoaded(stackId, getStackBadgeCount(img));
  }

  // ── Stack visual functions ────────────────────────────────────────────────
  function getStackCardStyle(img) {
    if (!img) return {};
    if (!isStackExpandedForImage(img)) {
      return {};
    }
    const color = applyStackBackgroundAlpha(getStackCardColor(img));
    if (!color) return {};
    return {
      backgroundColor: color,
      borderRadius: "0px",
      boxShadow: "none",
    };
  }

  function getStackBadgeIconStyle(img) {
    const color = getStackCardColor(img);
    if (!color) return {};
    return { color };
  }

  function getStackBandStyle(img) {
    if (!img || !getPictureStackId(img)) return null;
    if (!isStackExpandedForImage(img)) return null;
    const color = getStackCardColor(img);
    if (!color) return null;
    return {
      borderBottom: `8px solid ${color}`,
    };
  }

  // ── Stack reorder drag ────────────────────────────────────────────────────
  function getStackReorderCount(stackId, fallbackCount) {
    if (!stackId) return 0;
    const entry = expandedStackMembers.value.get(stackId);
    const ids = Array.isArray(entry?.ids) ? entry.ids : [];
    if (ids.length) return ids.length;
    const images = Array.isArray(entry?.images) ? entry.images : [];
    if (images.length) return images.length;
    const fallback = Number(fallbackCount ?? 0);
    return Number.isFinite(fallback) ? fallback : 0;
  }

  function getDragImageIdFromEvent(event) {
    const raw = event?.dataTransfer?.getData("application/json");
    if (!raw) return null;
    try {
      const payload = JSON.parse(raw);
      if (payload?.type === "image-ids") {
        const ids = Array.isArray(payload.imageIds) ? payload.imageIds : [];
        if (ids.length === 1) return String(ids[0]);
      }
    } catch (err) {
      return null;
    }
    return null;
  }

  function buildStackReorderDragState(sourceId) {
    if (!sourceId) return null;
    const source = allGridImages.value.find(
      (item) => item?.id != null && String(item.id) === String(sourceId),
    );
    if (!source) return null;
    const stackId = getPictureStackId(source);
    if (!stackId || !expandedStackIds.value.has(stackId)) return null;
    const count = getStackReorderCount(stackId, getStackBadgeCount(source));
    if (count <= 1) return null;
    return { stackId, imageId: String(sourceId) };
  }

  function handleStackReorderDragOver(img, event) {
    let drag = stackReorderDrag.value;
    if (!drag) {
      const sourceId = getDragImageIdFromEvent(event);
      drag = buildStackReorderDragState(sourceId);
      if (drag) {
        stackReorderDrag.value = drag;
      }
    }
    if (!drag || !img?.id) return;
    const stackId = getPictureStackId(img);
    if (!stackId || stackId !== drag.stackId) return;
    event.preventDefault();
    event.stopPropagation();
    setStackReorderHoverId(img.id);
    const bounds = event?.currentTarget?.getBoundingClientRect?.();
    if (
      bounds &&
      Number.isFinite(bounds.left) &&
      Number.isFinite(bounds.width)
    ) {
      const mid = bounds.left + bounds.width / 2;
      const side = event.clientX <= mid ? "left" : "right";
      setStackReorderHoverSide(side);
    }
    if (event?.dataTransfer) {
      event.dataTransfer.dropEffect = "move";
    }
  }

  function handleStackReorderDragLeave(img, event) {
    if (!stackReorderHoverId.value || !img?.id) return;
    if (String(img.id) !== stackReorderHoverId.value) return;
    const nextTarget = event?.relatedTarget;
    if (nextTarget && event?.currentTarget?.contains?.(nextTarget)) return;
    setStackReorderHoverId(null);
    setStackReorderHoverSide(null);
  }

  function applyStackOrderLocal(stackId, orderedIds) {
    const items = allGridImages.value.slice();
    const stackItems = items.filter(
      (item) => getPictureStackId(item) === stackId && item?.id != null,
    );
    if (stackItems.length <= 1) return;
    const stackCount = getStackReorderCount(
      stackId,
      getStackBadgeCount(stackItems[0]),
    );
    const orderedMembers = buildStackReorderedMembers(
      stackItems,
      orderedIds,
      stackCount,
    );
    if (!orderedMembers.length) return;
    const nextGrid = applyStackOrderToList(items, stackId, orderedMembers);
    setGridIndices(nextGrid);
    allGridImages.value = nextGrid;

    const nextMembers = new Map(expandedStackMembers.value);
    nextMembers.set(stackId, {
      ids: orderedMembers.map((item) => String(item.id)),
      images: orderedMembers,
    });
    expandedStackMembers.value = nextMembers;

    const nextFetched = applyStackOrderToList(
      Array.isArray(lastFetchedGridImages.value)
        ? lastFetchedGridImages.value.slice()
        : [],
      stackId,
      orderedMembers,
    );
    lastFetchedGridImages.value = nextFetched;
  }

  async function persistStackOrder(stackId, orderedIds, previousIds) {
    if (!stackId || !orderedIds.length) return;
    try {
      await apiClient.patch(`${props.backendUrl}/stacks/${stackId}/order`, {
        picture_ids: orderedIds.map((id) => Number(id)).filter(Number.isFinite),
      });
    } catch (err) {
      alert(`Failed to save stack order: ${err?.message || err}`);
      if (Array.isArray(previousIds) && previousIds.length) {
        applyStackOrderLocal(stackId, previousIds);
      }
    }
  }

  function handleStackReorderDrop(img, event) {
    let drag = stackReorderDrag.value;
    stackReorderDrag.value = null;
    const hoverSide = stackReorderHoverSide.value;
    setStackReorderHoverId(null);
    setStackReorderHoverSide(null);
    if (!drag) {
      const sourceId = getDragImageIdFromEvent(event);
      drag = buildStackReorderDragState(sourceId);
    }
    if (!drag || !img?.id) return;
    const stackId = getPictureStackId(img);
    if (!stackId || stackId !== drag.stackId) return;
    event.preventDefault();
    event.stopPropagation();
    const sourceId = String(drag.imageId);
    const targetId = String(img.id);
    if (sourceId === targetId) return;

    const stackItems = allGridImages.value.filter(
      (item) => getPictureStackId(item) === stackId && item?.id != null,
    );
    const currentIds = stackItems.map((item) => String(item.id));
    const fromIndex = currentIds.indexOf(sourceId);
    const toIndex = currentIds.indexOf(targetId);
    if (fromIndex === -1 || toIndex === -1 || fromIndex === toIndex) return;

    const nextIds = currentIds.slice();
    const [moved] = nextIds.splice(fromIndex, 1);
    const targetIndex = nextIds.indexOf(targetId);
    let insertIndex = targetIndex;
    if (hoverSide === "right") {
      insertIndex = targetIndex + 1;
    }
    if (insertIndex < 0) insertIndex = 0;
    if (insertIndex > nextIds.length) insertIndex = nextIds.length;
    nextIds.splice(insertIndex, 0, moved);

    applyStackOrderLocal(stackId, nextIds);
    void persistStackOrder(stackId, nextIds, currentIds);
  }

  // ── Stack CRUD actions ────────────────────────────────────────────────────
  function getLikenessGroupId(img) {
    if (!img) return null;
    const raw = img.stackIndex ?? img.stack_index ?? null;
    if (raw === null || raw === undefined) return null;
    const value = Number(raw);
    return Number.isFinite(value) ? value : null;
  }

  async function createStackFromSelection() {
    const ids = Array.isArray(selectedImageIds.value)
      ? selectedImageIds.value
      : [];
    if (!ids.length) return;
    const gridImages = Array.isArray(allGridImages.value)
      ? allGridImages.value
      : [];
    const gridIndexById = new Map(
      gridImages.map((img, i) => [String(img?.id), i]),
    );
    const sortedIds = ids.slice().sort((a, b) => {
      const ia = gridIndexById.get(String(a)) ?? Infinity;
      const ib = gridIndexById.get(String(b)) ?? Infinity;
      return ia - ib;
    });
    try {
      await apiClient.post(`${props.backendUrl}/stacks`, {
        picture_ids: sortedIds,
      });
      clearSelection();
      preserveScrollOnNextFetch.value = true;
      debouncedFetchAllGridImages();
    } catch (e) {
      console.error("Failed to create stack from selection:", e);
    }
  }

  async function dissolveSelectedStacks() {
    const stackIds = selectedMultipleStackIds.value;
    if (!stackIds.length) return;
    try {
      await Promise.all(
        stackIds.map(async (stackId) => {
          let idsToRemove;
          try {
            const res = await apiClient.get(
              `${props.backendUrl}/stacks/${stackId}`,
            );
            idsToRemove = res.data?.picture_ids;
          } catch {
            idsToRemove = null;
          }
          if (!Array.isArray(idsToRemove) || !idsToRemove.length) return;
          await apiClient.delete(
            `${props.backendUrl}/stacks/${stackId}/members`,
            { data: { picture_ids: idsToRemove } },
          );
          const removed = new Set(idsToRemove.map((id) => getPictureId(id)));
          allGridImages.value = allGridImages.value.map((img) => {
            if (!img || !removed.has(getPictureId(img.id))) return img;
            return {
              ...img,
              stack_id: null,
              stackId: null,
              stack_index: null,
              stackIndex: null,
              stack_position: null,
              stackPosition: null,
              stack_count: null,
              stackCount: null,
            };
          });
          const nextMembers = new Map(expandedStackMembers.value);
          nextMembers.delete(stackId);
          expandedStackMembers.value = nextMembers;
          const nextExpanded = new Set(expandedStackIds.value);
          if (nextExpanded.delete(stackId)) expandedStackIds.value = nextExpanded;
        }),
      );
      clearSelection();
      preserveScrollOnNextFetch.value = true;
      debouncedFetchAllGridImages();
    } catch (e) {
      console.error("Failed to dissolve selected stacks:", e);
    }
  }

  async function removeSelectedFromStack() {
    const stackId = selectedStackId.value;
    const ids = Array.isArray(selectedImageIds.value)
      ? selectedImageIds.value
      : [];
    if (!stackId || !ids.length) return;

    let idsToRemove = ids;
    if (!expandedStackIds.value.has(stackId)) {
      try {
        const stackRes = await apiClient.get(
          `${props.backendUrl}/stacks/${stackId}`,
        );
        const allMemberIds = stackRes.data?.picture_ids;
        if (Array.isArray(allMemberIds) && allMemberIds.length) {
          idsToRemove = allMemberIds;
        }
      } catch (e) {
        console.error(
          "Failed to fetch all stack members for dissolve, falling back to selected ids:",
          e,
        );
      }
    }

    try {
      await apiClient.delete(
        `${props.backendUrl}/stacks/${stackId}/members`,
        { data: { picture_ids: idsToRemove } },
      );
      const removed = new Set(idsToRemove.map((id) => getPictureId(id)));
      allGridImages.value = allGridImages.value.map((img) => {
        if (!img || !removed.has(getPictureId(img.id))) {
          return img;
        }
        return {
          ...img,
          stack_id: null,
          stackId: null,
          stack_index: null,
          stackIndex: null,
          stack_position: null,
          stackPosition: null,
          stack_count: null,
          stackCount: null,
        };
      });
      const nextMembers = new Map(expandedStackMembers.value);
      const entry = nextMembers.get(stackId);
      if (entry) {
        const nextIds = Array.isArray(entry.ids)
          ? entry.ids.filter((id) => !removed.has(getPictureId(id)))
          : [];
        const nextImages = Array.isArray(entry.images)
          ? entry.images.filter(
              (img) => !removed.has(getPictureId(img?.id)),
            )
          : [];
        if (nextIds.length || nextImages.length) {
          nextMembers.set(stackId, { ids: nextIds, images: nextImages });
        } else {
          nextMembers.delete(stackId);
          const nextExpanded = new Set(expandedStackIds.value);
          if (nextExpanded.delete(stackId)) {
            expandedStackIds.value = nextExpanded;
          }
        }
        expandedStackMembers.value = nextMembers;
      }
      clearSelection();
      preserveScrollOnNextFetch.value = true;
      debouncedFetchAllGridImages();
    } catch (e) {
      console.error("Failed to remove selected images from stack:", e);
    }
  }

  async function createStacksFromSelectedGroups() {
    if (props.selectedSort !== LIKENESS_GROUPS_SORT_KEY) return;
    const ids = Array.isArray(selectedImageIds.value)
      ? selectedImageIds.value
      : [];
    if (!ids.length) return;

    const source = Array.isArray(lastFetchedGridImages.value)
      ? lastFetchedGridImages.value
      : allGridImages.value;
    const images = Array.isArray(source) ? source : [];
    const imageById = new Map(
      images
        .filter((img) => img && img.id != null)
        .map((img) => [String(img.id), img]),
    );

    const groupIds = new Set();
    for (const id of ids) {
      const img = imageById.get(String(id));
      const groupId = getLikenessGroupId(img);
      if (groupId != null) {
        groupIds.add(groupId);
      }
    }

    if (!groupIds.size) return;

    const groupsToStack = [];
    const skippedGroups = [];
    for (const groupId of groupIds) {
      const members = images.filter(
        (img) => getLikenessGroupId(img) === groupId && img?.id != null,
      );
      const memberIds = Array.from(
        new Set(members.map((img) => Number(img.id)).filter(Number.isFinite)),
      );
      if (memberIds.length < 2) continue;
      const membersByStack = new Map();
      for (const member of members) {
        const sId = getPictureStackId(member);
        if (!sId) continue;
        if (!membersByStack.has(sId)) {
          membersByStack.set(sId, []);
        }
        membersByStack.get(sId).push(member);
      }
      if (membersByStack.size > 1) {
        skippedGroups.push(groupId);
        continue;
      }
      if (membersByStack.size === 1) {
        const [sId, stackedMembers] = Array.from(
          membersByStack.entries(),
        )[0];
        const stackedAnchorId = stackedMembers?.[0]?.id;
        const unstackedIds = memberIds.filter(
          (id) => !getPictureStackId(imageById.get(String(id))),
        );
        if (!unstackedIds.length) continue;
        const payloadIds = [stackedAnchorId, ...unstackedIds]
          .filter((id) => id != null)
          .map((id) => Number(id))
          .filter(Number.isFinite);
        if (payloadIds.length < 2) continue;
        groupsToStack.push(payloadIds);
        continue;
      }
      groupsToStack.push(memberIds);
    }

    if (!groupsToStack.length) return;

    try {
      for (const memberIds of groupsToStack) {
        await apiClient.post(`${props.backendUrl}/stacks`, {
          picture_ids: memberIds,
        });
      }
      if (skippedGroups.length) {
        alert(
          `Skipped ${skippedGroups.length} group(s) containing multiple stacks.`,
        );
      }
      clearSelection();
      preserveScrollOnNextFetch.value = true;
      debouncedFetchAllGridImages();
    } catch (e) {
      console.error("Failed to create stacks from groups:", e);
    }
  }

  // Computed helpers (need selectedImageIds + allGridImages) ─────────────────
  const selectedStackId = computed(() => {
    const ids = Array.isArray(selectedImageIds.value)
      ? selectedImageIds.value
      : [];
    if (!ids.length) return null;
    const images = Array.isArray(allGridImages.value)
      ? allGridImages.value
      : [];
    if (!images.length) return null;
    const imageById = new Map(
      images
        .filter((img) => img && img.id != null)
        .map((img) => [String(img.id), img]),
    );
    let stackId = null;
    for (const id of ids) {
      const img = imageById.get(String(id));
      const currentStackId = getPictureStackId(img);
      if (!currentStackId) return null;
      if (stackId === null) {
        stackId = currentStackId;
        continue;
      }
      if (stackId !== currentStackId) return null;
    }
    return stackId;
  });

  const selectedMultipleStackIds = computed(() => {
    const ids = Array.isArray(selectedImageIds.value)
      ? selectedImageIds.value
      : [];
    if (!ids.length) return [];
    const images = Array.isArray(allGridImages.value)
      ? allGridImages.value
      : [];
    const imageById = new Map(
      images
        .filter((img) => img && img.id != null)
        .map((img) => [String(img.id), img]),
    );
    const stackIds = new Set();
    for (const id of ids) {
      const img = imageById.get(String(id));
      const sId = getPictureStackId(img);
      if (sId) stackIds.add(sId);
    }
    return [...stackIds];
  });

  const showRemoveFromStack = computed(
    () => selectedStackId.value !== null,
  );

  return {
    // State
    expandedStackIds,
    expandedStackMembers,
    expandedStackLoading,
    stackVisualOrderMap,
    // Computeds re-exported for ImageGrid template
    selectedStackId,
    selectedMultipleStackIds,
    showRemoveFromStack,
    // Data helpers (used by useGridFetch and elsewhere)
    collapseStackImages,
    getLocalStackMembers,
    ensureStackMembersLoaded,
    mapGridImages,
    setGridIndices,
    hydrateGridImage,
    // Stack visual
    getStackCardStyle,
    getStackCardColor,
    getStackBadgeIconStyle,
    getStackBandStyle,
    isStackExpandedForImage,
    // Stack expand / collapse
    rebuildGridImagesFromLastFetch,
    refreshExpandedStacksAfterFetch,
    loadExpandedStacksInView,
    expandAllStacks,
    collapseAllStacks,
    toggleStackExpand,
    prefetchStackMembers,
    emitStackStats,
    syncExpandAllStacksFromFetchedImages,
    collectExpandableStackIds,
    // Stack reorder drag
    handleStackReorderDragOver,
    handleStackReorderDragLeave,
    handleStackReorderDrop,
    // Stack CRUD
    createStackFromSelection,
    dissolveSelectedStacks,
    removeSelectedFromStack,
    getLikenessGroupId,
    createStacksFromSelectedGroups,
  };
}

