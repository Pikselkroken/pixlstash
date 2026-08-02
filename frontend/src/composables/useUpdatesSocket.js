import { onUnmounted, watch } from "vue";
import { API_BASE_URL, appendShareToken, isReadOnly } from "../utils/apiClient";
import { useGridRealtimeSync } from "./useGridRealtimeSync";
import { useWsStore } from "../stores/useWsStore";
import { useGridStore } from "../stores/useGridStore";
import { useSortStore } from "../stores/useSortStore";
import { useFilterStore } from "../stores/useFilterStore";
import { useSelectionStore } from "../stores/useSelectionStore";
import { useSearchStore } from "../stores/useSearchStore";
import { useOperationStore } from "../stores/useOperationStore";
import { useSnapshotsStore } from "../stores/useSnapshotsStore";
import { useDedupStore } from "../stores/useDedupStore";

const BACKEND_URL = API_BASE_URL;

// Coalescing window for incoming grid-driving WS events. A burst of foreign
// events accumulates over this window and applies once per category instead of
// one fetch-and-rebuild per event.
const GRID_WS_COALESCE_MS = 200;

/**
 * The live-updates channel: the /updates WebSocket, the filter handshake that
 * tells the backend which events this client cares about, and the reconnect
 * loop.
 *
 * Applying an event to the grid is useGridRealtimeSync's job; this composable
 * owns the socket around it, and hands it an imperative grid surface plus the
 * coalescing scheduler.
 *
 * @param {object} deps
 * @param {import("vue").Ref} deps.gridContainer - the grid's template ref.
 * @param {Function} deps.refreshSidebar
 * @param {Function} deps.refreshSidebarPicturesDebounced
 */
export function useUpdatesSocket({
  gridContainer,
  refreshSidebar,
  refreshSidebarPicturesDebounced,
}) {
  const wsStore = useWsStore();
  const gridStore = useGridStore();
  const sortStore = useSortStore();
  const filterStore = useFilterStore();
  const selectionStore = useSelectionStore();
  const searchStore = useSearchStore();
  const operationStore = useOperationStore();
  const snapshotsStore = useSnapshotsStore();
  const dedupStore = useDedupStore();

  let updatesSocket = null;
  let updatesReconnectTimer = null;
  let gridWsCoalesceTimer = null;

  // --- WebSocket ---
  // Event types that can carry a recorded operation (the reversible metadata
  // facets of backend_architecture.md §21). `picture_imported` is deliberately
  // absent: imports are not undoable in v1.9, so they never appear in the stack.
  const OPERATION_BEARING_EVENTS = new Set([
    "pictures_changed",
    "tags_changed",
    "characters_changed",
    "descriptions_changed",
  ]);

  function buildUpdatesSocketUrl() {
    if (!BACKEND_URL) return "";
    const wsBase = BACKEND_URL.replace(/^http/i, "ws");
    // The backend authenticates the WebSocket handshake (the HTTP auth
    // middleware does not cover WebSockets). A full session authenticates via
    // the same-origin session cookie; a share/read-only session has no cookie,
    // so append its READ token as ?token= the same way HTTP requests do.
    return appendShareToken(`${wsBase}/ws/updates`);
  }

  // A `pictures_changed` event may carry a `fields` list naming the columns that
  // changed. When every changed field is invisible to the current sort + active
  // filters (e.g. a background `smart_score` recompute while sorting by date),
  // the grid/sidebar don't need to react at all. An event with no `fields`
  // (user edits, imports, plugin output, …) is treated as "unknown" and always
  // refreshes, preserving the previous behaviour.
  function pictureChangeFieldAffectsView(field) {
    if (field === "smart_score") {
      return (
        sortStore.selectedSort === "SMART_SCORE" ||
        filterStore.smartScoreBucketFilter != null
      );
    }
    // Detections are an opt-in overlay layer, never a sort/filter field, so a
    // detection change never affects grid membership or order — don't reload or
    // raise the "view changed" pill for it.
    if (field === "detections") return false;
    // Unknown field → assume it can affect the view, so refresh to be safe.
    return true;
  }

  function pictureChangeAffectsView(fields) {
    if (!Array.isArray(fields) || fields.length === 0) return true;
    return fields.some(pictureChangeFieldAffectsView);
  }

  function sendUpdatesFilters() {
    if (!updatesSocket) return;
    if (updatesSocket.readyState !== WebSocket.OPEN) return;
    updatesSocket.send(
      JSON.stringify({
        type: "set_filters",
        client_id: wsStore.clientId,
        selected_character: selectionStore.selectedCharacter,
        selected_set: selectionStore.selectedSet,
        selected_sets: selectionStore.selectedSetIds,
        search_query: searchStore.searchQuery,
      }),
    );
  }

  // Imperative grid API surface used by the realtime-sync composable. Each method
  // delegates to the ImageGrid template-ref's defineExpose'd methods (Tier-3
  // imperative API), no-oping safely if the grid isn't mounted yet.
  const gridApi = {
    insertGridImagesById: (ids) =>
      gridContainer.value?.insertGridImagesById?.(ids),
    refreshGridImage: (id) => gridContainer.value?.refreshGridImage?.(id),
    refreshStackFacets: (ids) => gridContainer.value?.refreshStackFacets?.(ids),
    repositionImageByScore: (id, score) =>
      gridContainer.value?.repositionImageByScore?.(id, score),
    repositionImageBySmartScore: (id) =>
      gridContainer.value?.repositionImageBySmartScore?.(id),
    refreshSmartScoreForImage: (id) =>
      gridContainer.value?.refreshSmartScoreForImage?.(id),
    removeImagesById: (ids) => gridContainer.value?.removeImagesById?.(ids),
    isImagesLoading: () => gridContainer.value?.isImagesLoading?.() ?? false,
    isOverlayOpen: () => gridContainer.value?.isOverlayOpen?.() ?? false,
    markOverlayDeferredRefresh: () =>
      gridContainer.value?.markOverlayDeferredRefresh?.(),
  };

  function fullGridReload() {
    gridStore.wsUpdateKey = Date.now();
    gridStore.refreshGridVersion();
  }

  // Fixed-window scheduler for the realtime-sync coalescer. The composable arms
  // one flush per window (it skips schedule() while a flush is already pending),
  // so the first queued event starts a GRID_WS_COALESCE_MS timer and a
  // back-to-back burst flushes once at its end. cancel() lets onBeforeUnmount
  // drop a pending flush.
  const gridWsScheduler = {
    schedule(flush) {
      if (gridWsCoalesceTimer) clearTimeout(gridWsCoalesceTimer);
      gridWsCoalesceTimer = setTimeout(() => {
        gridWsCoalesceTimer = null;
        flush();
      }, GRID_WS_COALESCE_MS);
    },
    cancel() {
      if (gridWsCoalesceTimer) {
        clearTimeout(gridWsCoalesceTimer);
        gridWsCoalesceTimer = null;
      }
    },
  };

  const gridRealtimeSync = useGridRealtimeSync({
    getMyClientId: () => wsStore.clientId,
    grid: gridApi,
    wsStore,
    pictureChangeAffectsView,
    getSelectedSort: () => sortStore.selectedSort,
    reload: fullGridReload,
    refreshSidebar: (flash) => refreshSidebarPicturesDebounced(flash),
    scheduler: gridWsScheduler,
  });

  function connectUpdatesSocket() {
    if (updatesSocket) return;
    const url = buildUpdatesSocketUrl();
    if (!url) return;
    const ws = new WebSocket(url);
    updatesSocket = ws;

    ws.onopen = () => {
      sendUpdatesFilters();
    };

    ws.onmessage = (event) => {
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch {
        return;
      }
      // The operation log has no WS event of its own: a metadata mutation
      // announces itself as a picture/tag/character change, and that is the
      // signal the undo stack may have moved. Origin is read from the event
      // `data` (never a contextvar) and only decides whether the change may
      // narrate itself; an external one updates the stack silently.
      if (OPERATION_BEARING_EVENTS.has(payload?.type)) {
        operationStore.onPictureEvent(payload);
      }
      // The Duplicates queue holds a snapshot of a server read that a scrapheap
      // move elsewhere invalidates: a soft-deleted picture must not stay in a
      // loaded group, and a group left with one live unit must leave the queue.
      // Routed here rather than through useGridRealtimeSync because it is a
      // different destination with a different decision (rows are dropped, not
      // cards), and here rather than in DuplicateQueue.vue because the store
      // outlives the view. Origin is deliberately not consulted: this store
      // never applies a scrapheap move optimistically, so its own tab's echo is
      // as new to it as another tab's.
      if (payload?.type === "pictures_changed" && !isReadOnly.value) {
        dedupStore.applyPictureEvent(payload);
      }
      const isPictureChange =
        payload?.type === "pictures_changed" ||
        payload?.type === "picture_imported";
      if (isPictureChange) {
        // LIKENESS_GROUPS reorders the whole grid wholesale, so a targeted op
        // can't reconcile it — keep the existing wsTagUpdate signal that lets the
        // grid re-rank in place. (Imports still flow through the normal path.)
        const pictureIds = Array.isArray(payload.picture_ids)
          ? payload.picture_ids
          : [];
        // Signal the open lightbox to re-fetch its card's smart_score. The overlay
        // always displays the score (independent of grid sort), so this fires for
        // any smart_score change regardless of the current sort and regardless of
        // origin — matching on picture id + field, not origin, so it covers both
        // origin-stamped interactive tag edits and the origin-less bulk drain that
        // rides a penalised-tag settings change. `fields` absent = full change.
        if (payload?.type === "pictures_changed" && pictureIds.length > 0) {
          const changedFields = Array.isArray(payload.fields)
            ? payload.fields
            : [];
          const touchesSmartScore =
            changedFields.length === 0 || changedFields.includes("smart_score");
          if (touchesSmartScore) {
            const nextKey = (wsStore.wsSmartScoreUpdate?.key || 0) + 1;
            wsStore.wsSmartScoreUpdate = { key: nextKey, pictureIds };
          }
          // Signal the open lightbox to re-fetch its detection boxes when a
          // Segment run lands. The grid's card-content refresh is deferred under
          // an open overlay (§9.1) and the overlay reads its boxes straight from
          // the detections endpoint, so it needs its own signal. The backend
          // always stamps this change `fields: ["detections"]`, so match on the
          // explicit field only.
          if (changedFields.includes("detections")) {
            const nextKey = (wsStore.wsDetectionUpdate?.key || 0) + 1;
            wsStore.wsDetectionUpdate = { key: nextKey, pictureIds };
          }
        }
        if (
          pictureIds.length > 0 &&
          sortStore.selectedSort === "LIKENESS_GROUPS" &&
          payload?.type !== "picture_imported" &&
          pictureChangeAffectsView(payload.fields)
        ) {
          if (!wsStore.isUploadInProgress) {
            refreshSidebarPicturesDebounced(true);
          }
          const nextKey = (wsStore.wsTagUpdate?.key || 0) + 1;
          wsStore.wsTagUpdate = { key: nextKey, pictureIds };
          return;
        }
        // Own upload in progress: the import dialog drives the grid; ignore the
        // echo so it doesn't double-count or reload mid-upload.
        if (
          wsStore.isUploadInProgress &&
          payload?.type === "picture_imported"
        ) {
          return;
        }
        // Everything else goes through the origin-aware decision table.
        gridRealtimeSync.handleMessage(payload);
      } else if (payload?.type === "characters_changed") {
        refreshSidebar();
      } else if (payload?.type === "tags_changed") {
        const pictureIds = Array.isArray(payload.picture_ids)
          ? payload.picture_ids
          : [];
        // Origin-aware: only this tab's own tag edits may refresh a tag-filtered
        // grid in place. A tag change from outside (background tagging, another
        // tab) must not reshuffle the user's filtered view — the grid raises a
        // click-to-refresh pill instead (see ImageGrid's wsTagUpdate watcher).
        // The flag rides on wsTagUpdate; the overlay still refreshes its open
        // card's tags for any origin.
        const isOwn = !!(
          payload.origin_client_id &&
          wsStore.clientId &&
          payload.origin_client_id === wsStore.clientId
        );
        const nextKey = (wsStore.wsTagUpdate?.key || 0) + 1;
        wsStore.wsTagUpdate = { key: nextKey, pictureIds, external: !isOwn };
      } else if (payload?.type === "descriptions_changed") {
        const pictureIds = Array.isArray(payload.picture_ids)
          ? payload.picture_ids
          : [];
        const nextKey = (wsStore.wsDescriptionUpdate?.key || 0) + 1;
        wsStore.wsDescriptionUpdate = { key: nextKey, pictureIds };
      } else if (payload?.type === "plugin_progress") {
        wsStore.wsPluginProgress = {
          key: Date.now(),
          payload,
        };
      } else if (payload?.type === "snapshot_created" && !isReadOnly.value) {
        snapshotsStore.onSnapshotCreated();
      } else if (payload?.type === "snapshot_deleted" && !isReadOnly.value) {
        snapshotsStore.onSnapshotDeleted(payload);
      } else if (payload?.type === "restore_started" && !isReadOnly.value) {
        snapshotsStore.onRestoreStarted(payload);
      } else if (payload?.type === "restore_completed" && !isReadOnly.value) {
        snapshotsStore.onRestoreCompleted();
        gridStore.wsUpdateKey = Date.now();
        gridStore.refreshGridVersion();
        refreshSidebar();
      } else if (payload?.type === "restore_failed" && !isReadOnly.value) {
        snapshotsStore.onRestoreFailed(payload);
        gridStore.wsUpdateKey = Date.now();
        gridStore.refreshGridVersion();
        refreshSidebar();
      }
    };

    ws.onclose = () => {
      updatesSocket = null;
      if (updatesReconnectTimer) {
        clearTimeout(updatesReconnectTimer);
      }
      updatesReconnectTimer = setTimeout(() => {
        updatesReconnectTimer = null;
        connectUpdatesSocket();
      }, 2000);
    };
  }

  function disconnectUpdatesSocket() {
    if (updatesReconnectTimer) {
      clearTimeout(updatesReconnectTimer);
      updatesReconnectTimer = null;
    }
    if (updatesSocket) {
      updatesSocket.close();
      updatesSocket = null;
    }
  }

  function loadPendingExternalImports() {
    const ids = wsStore.pendingExternalImportIds.slice();
    wsStore.clearPendingExternalImportIds();
    if (!ids.length) {
      fullGridReload();
      return;
    }
    // Splice just the new ids in place; fall back to a full reload if the grid
    // ref isn't available (e.g. unmounted) or is mid-fetch.
    const grid = gridContainer.value;
    if (grid?.insertGridImagesById && !grid.isImagesLoading?.()) {
      grid.insertGridImagesById(ids);
    } else {
      fullGridReload();
    }
  }

  function loadSortChangedExternal() {
    // The user opted in to the reshuffle — reconcile by refetching + re-sorting.
    wsStore.clearSortChangedExternalIds();
    fullGridReload();
  }

  // ImageGrid asks to raise the "view changed externally" pill for an external
  // tag change under an active tag filter (instead of reshuffling the filtered
  // grid under the user). Skip ids already queued in the "new pictures" pill so a
  // just-imported batch being tagged doesn't double-pill.
  function onFlagSortChanged(ids) {
    if (!Array.isArray(ids) || !ids.length) return;
    const pending = new Set(wsStore.pendingExternalImportIds);
    const fresh = ids.filter((id) => !pending.has(id));
    if (fresh.length) wsStore.addSortChangedExternalIds(fresh);
  }

  // The backend only sends events this client's current view could care about,
  // so any change to what the view is has to be re-announced.
  watch(
    [
      () => selectionStore.selectedCharacter,
      () => selectionStore.selectedSet,
      () => selectionStore.selectedSetIds,
      () => searchStore.searchQuery,
    ],
    () => {
      sendUpdatesFilters();
    },
  );

  // A grid rebuild has reconciled whatever the pills were offering, so the
  // queued ids are stale.
  watch(
    () => gridStore.gridVersion,
    () => {
      wsStore.clearPendingExternalImportIds();
      wsStore.clearSortChangedExternalIds();
    },
  );

  onUnmounted(() => {
    disconnectUpdatesSocket();
    gridWsScheduler.cancel();
  });

  return {
    connectUpdatesSocket,
    disconnectUpdatesSocket,
    sendUpdatesFilters,
    fullGridReload,
    loadPendingExternalImports,
    loadSortChangedExternal,
    onFlagSortChanged,
  };
}
