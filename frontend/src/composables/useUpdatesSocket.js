import { onUnmounted, watch } from "vue";
import {
  API_BASE_URL,
  appendShareToken,
  isReadOnly,
  toBackendWebSocketUrl,
} from "../utils/apiClient";
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
import { useMovesStore } from "../stores/useMovesStore";
import { useNoticeStore } from "../stores/useNoticeStore";
import { useTasksStore } from "../stores/useTasksStore";
import {
  isFullRestoreRequestInFlight,
  prepareForFullRestoreTransition,
  reloadAfterFullRestore,
} from "../utils/fullRestoreTransition";

const BACKEND_URL = API_BASE_URL;

// Coalescing window for incoming grid-driving WS events. A burst of foreign
// events accumulates over this window and applies once per category instead of
// one fetch-and-rebuild per event.
const GRID_WS_COALESCE_MS = 200;

/**
 * Apply the server's WebSocket close contract.
 *
 * Code 1012 with the switch reason is not a transient disconnect: every id and
 * store value in this SPA belongs to the retired library. Reload the document
 * instead of reconnecting the socket underneath stale client state.
 */
export function handleUpdatesSocketClose(event, { reload, reconnect }) {
  if (event?.code === 1012 && event?.reason === "Library switched") {
    reload();
    return;
  }
  reconnect();
}

/** Coalescing key for the GPU-memory notice: one card, updated in place, so a
 *  retry sequence reads as one event rather than three stacked warnings. */
export const VRAM_OOM_NOTICE_KEY = "vram-oom";

// How long a "retrying" card stays up. Stated explicitly rather than left to
// the level default (6 s), which is shorter than the backend's pause between
// attempts - the card would expire and the next frame would open a NEW one, so
// the key would coalesce nothing and the user would get a flicker of three
// separate warnings. This has to outlive `TaskRunner.VRAM_OOM_RETRY_PAUSE_S`.
const VRAM_OOM_RETRY_NOTICE_MS = 15000;

// Why the card names another program: the app's own models are sized against
// the configured budget, so the usual reason there is suddenly no room is
// something else on the card - a ComfyUI run, a game, another app's model. That
// is also the one thing the person reading the toast can act on.
const VRAM_CULPRIT = "another program is probably holding the card";

/**
 * The card shown while a GPU task is fighting for VRAM, in its four states.
 *
 * @param {object} payload - the `vram_oom` event.
 * @param {number} payload.attempt - the attempt this frame is about (1-based).
 * @param {number} payload.max_attempts - attempts the task gets in total.
 * @param {boolean} payload.gave_up - the retry sequence ended without the work.
 * @param {boolean} payload.recovered - a later attempt succeeded.
 * @param {Array<{name: string, used_mb: number}>} [payload.other_processes] -
 *   the other processes on the card, largest first, when nvidia-smi could
 *   say; the first one is named in place of the generic guess.
 * @returns {{level: string, text: string, timeout: number|undefined}}
 */
export function vramOomNotice({
  attempt,
  max_attempts: max,
  gave_up,
  recovered,
  other_processes: others = [],
}) {
  const used = Number(attempt) || 0;
  const total = Number(max) || 0;
  // Named when the backend could see it, guessed when it could not.
  const top = Array.isArray(others) && others.length ? others[0] : null;
  const culprit = top
    ? `${top.name} is holding ${(Number(top.used_mb) / 1024).toFixed(1)} GB of the card`
    : VRAM_CULPRIT;
  if (recovered) {
    return {
      level: "success",
      text: `GPU memory freed up - the work finished on attempt ${used} of ${total}.`,
      timeout: undefined,
    };
  }
  if (gave_up) {
    // Only an exhausted sequence can promise the two things below. A sequence
    // that ended early - the task died of something else, or the app is
    // shutting down - says what happened and promises nothing.
    const text =
      used >= total
        ? `Ran out of GPU memory after ${total} attempts - ${culprit}. Nothing was changed; this work will be tried again later.`
        : `Ran out of GPU memory, and the work stopped on attempt ${used} of ${total}.`;
    return { level: "warning", text, timeout: undefined };
  }
  return {
    level: "warning",
    text: `Ran out of GPU memory - ${culprit}. Retrying - attempt ${used} of ${total} used.`,
    timeout: VRAM_OOM_RETRY_NOTICE_MS,
  };
}

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
  const tasksStore = useTasksStore();
  const gridStore = useGridStore();
  const sortStore = useSortStore();
  const filterStore = useFilterStore();
  const selectionStore = useSelectionStore();
  const searchStore = useSearchStore();
  const operationStore = useOperationStore();
  const snapshotsStore = useSnapshotsStore();
  const dedupStore = useDedupStore();
  const movesStore = useMovesStore();
  const noticeStore = useNoticeStore();

  let updatesSocket = null;
  let updatesReconnectTimer = null;
  let reconnectEnabled = false;
  let gridWsCoalesceTimer = null;
  let fullRestorePending = false;
  // "A sidebar strip a few seconds after the moves stop" (release plan §4
  // Phase 5): reorganising a folder queues one EXTERNAL_MOVES_PENDING event
  // per scan, and a scan runs once per batch rather than once per file, but
  // this debounce is what keeps a burst of scans (several reference folders
  // settling around the same time) from re-fetching the queue once per event.
  let externalMovesPendingTimer = null;
  const EXTERNAL_MOVES_PENDING_DEBOUNCE_MS = 3000;
  let fullRestoreTransitioning = false;

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
    // The backend authenticates the WebSocket handshake (the HTTP auth
    // middleware does not cover WebSockets). A full session authenticates via
    // the same-origin session cookie; a share/read-only session has no cookie,
    // so append its READ token as ?token= the same way HTTP requests do.
    return appendShareToken(toBackendWebSocketUrl(`${BACKEND_URL}/ws/updates`));
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
    // detection change never affects grid membership or order - don't reload or
    // raise the "view changed" pill for it.
    if (field === "detections") return false;
    // Neither does a rotate: `pixels` means the file's own bytes changed (an
    // in-place rotate, or an undo/redo of one). The card renders differently but
    // does not move - no sort reads orientation, and no filter selects on it -
    // so reloading the grid or raising the "view changed" pill would both be
    // wrong for what is really a repaint of one tile.
    if (field === "pixels") return false;
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
    refreshThumbnailUrls: (ids) =>
      gridContainer.value?.refreshThumbnailUrls?.(ids),
    applyRotatedCards: (ids) => gridContainer.value?.applyRotatedCards?.(ids),
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
    reconnectEnabled = true;
    if (updatesSocket) return;
    const url = buildUpdatesSocketUrl();
    if (!url) return;
    const ws = new WebSocket(url);
    updatesSocket = ws;

    // A full restore replaces both the database and the authentication context.
    // The STARTED frame reaches established tabs before the server drains their
    // sockets; close code 1012 is the fallback for a tab that missed that frame.
    // Disconnect first so the reload cannot race the ordinary reconnect timer.
    function transitionAfterFullRestore() {
      if (fullRestoreTransitioning) return;
      fullRestoreTransitioning = true;
      disconnectUpdatesSocket();
      reloadAfterFullRestore();
    }

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
        // can't reconcile it - keep the existing wsTagUpdate signal that lets the
        // grid re-rank in place. (Imports still flow through the normal path.)
        const pictureIds = Array.isArray(payload.picture_ids)
          ? payload.picture_ids
          : [];
        // Signal the open lightbox to re-fetch its card's smart_score. The overlay
        // always displays the score (independent of grid sort), so this fires for
        // any smart_score change regardless of the current sort and regardless of
        // origin - matching on picture id + field, not origin, so it covers both
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
        // tab) must not reshuffle the user's filtered view - the grid raises a
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
      } else if (payload?.type === "vram_oom" && !isReadOnly.value) {
        // A fact about the machine, not about the library, so no grid-filter or
        // origin decision applies. The backend only delivers it to owner-level
        // sockets; the read-only guard matches the sibling branches rather than
        // relying on that alone. One keyed card: the retries and the closing
        // frame update it in place rather than stacking three warnings.
        const notice = vramOomNotice(payload);
        noticeStore.push({ ...notice, key: VRAM_OOM_NOTICE_KEY });
      } else if (
        payload?.type === "external_moves_pending" &&
        !isReadOnly.value
      ) {
        // No count on the wire (server.py's broadcaster deliberately sends
        // none - the queue is reclassified live, so any number sent here
        // could already be wrong by the time it renders). Debounced re-fetch
        // is the whole reaction.
        if (externalMovesPendingTimer) clearTimeout(externalMovesPendingTimer);
        externalMovesPendingTimer = setTimeout(() => {
          externalMovesPendingTimer = null;
          movesStore.fetchPending();
        }, EXTERNAL_MOVES_PENDING_DEBOUNCE_MS);
      } else if (payload?.type === "snapshot_created" && !isReadOnly.value) {
        snapshotsStore.onSnapshotCreated();
      } else if (payload?.type === "snapshot_deleted" && !isReadOnly.value) {
        snapshotsStore.onSnapshotDeleted(payload);
      } else if (payload?.type === "restore_started" && !isReadOnly.value) {
        if (payload?.resource_type === "full") {
          if (isFullRestoreRequestInFlight()) {
            // Preserve the initiating tab's established behavior: its open POST
            // reports success/failure and RestoreConfirmDialog reloads only once
            // that response settles.
            snapshotsStore.onRestoreStarted(payload);
          } else {
            // Clear every store that contains reads made under the old
            // credential immediately, but keep the socket alive until the
            // server's 1012 drain close. Reloading before the barrier closes
            // could let the new document reconnect to the old database and
            // then reload a second time at cutover.
            fullRestorePending = true;
            prepareForFullRestoreTransition();
          }
          return;
        }
        snapshotsStore.onRestoreStarted(payload);
      } else if (payload?.type === "restore_completed" && !isReadOnly.value) {
        if (payload?.resource_type === "full") {
          transitionAfterFullRestore();
          return;
        }
        snapshotsStore.onRestoreCompleted();
        gridStore.wsUpdateKey = Date.now();
        gridStore.refreshGridVersion();
        refreshSidebar();
      } else if (payload?.type === "restore_failed" && !isReadOnly.value) {
        if (payload?.resource_type === "full") {
          transitionAfterFullRestore();
          return;
        }
        snapshotsStore.onRestoreFailed(payload);
        gridStore.wsUpdateKey = Date.now();
        gridStore.refreshGridVersion();
        refreshSidebar();
      }
    };

    ws.onclose = (event) => {
      if (updatesSocket === ws) updatesSocket = null;
      // Close code 1012 is overloaded. A library switch sends it with an
      // explicit "Library switched" reason; the restore barrier and the
      // WebSocket admission refusal send it with no reason at all. Only the
      // reason separates them, so match the switch before the restore branches
      // or a switch would be transitioned as though it were a restore.
      const isLibrarySwitch =
        event?.code === 1012 && event?.reason === "Library switched";
      if (!isLibrarySwitch) {
        if (event?.code === 1012) {
          // The restore barrier deliberately uses Service Restart (1012). The
          // initiating tab must keep its long-running HTTP request alive; every
          // other tab immediately clears session stores and bootstraps afresh.
          if (isFullRestoreRequestInFlight()) {
            reconnectEnabled = false;
            return;
          }
          transitionAfterFullRestore();
          return;
        }
        if (fullRestorePending) {
          // Once STARTED was observed, even a network-shaped close cannot make
          // incremental reconnection safe: the tab has already discarded its
          // pre-restore state and must bootstrap a coherent session.
          transitionAfterFullRestore();
          return;
        }
        if (!reconnectEnabled) return;
      }
      if (updatesReconnectTimer) {
        clearTimeout(updatesReconnectTimer);
      }
      handleUpdatesSocketClose(event, {
        reload: () => window.location.reload(),
        reconnect: () => {
          updatesReconnectTimer = setTimeout(() => {
            updatesReconnectTimer = null;
            connectUpdatesSocket();
          }, 2000);
        },
      });
    };
  }

  function disconnectUpdatesSocket() {
    reconnectEnabled = false;
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
    // The user opted in to the reshuffle - reconcile by refetching + re-sorting.
    wsStore.clearSortChangedExternalIds();
    fullGridReload();
  }

  // ImageGrid asks to raise the "view changed externally" pill for an external
  // tag change under an active tag filter (instead of reshuffling the filtered
  // grid under the user). Skip ids already queued in the "new pictures" pill so a
  // just-imported batch being tagged doesn't double-pill.
  //
  // While the tagger is running the ids are held instead: a pass over a
  // library changes tags in eight-picture batches, and raising the pill per
  // batch had it back on screen seconds after every refresh for as long as the
  // run lasted. One pill when the run ends says the same thing once.
  // A Set, so a long pass costs one insert per id rather than a rebuild of
  // everything held so far on every eight-picture batch.
  const heldSortChangedIds = new Set();
  function onFlagSortChanged(ids) {
    if (!Array.isArray(ids) || !ids.length) return;
    if (tasksStore.taggingActive) {
      for (const id of ids) heldSortChangedIds.add(id);
      return;
    }
    const pending = new Set(wsStore.pendingExternalImportIds);
    const fresh = ids.filter((id) => !pending.has(id));
    if (fresh.length) wsStore.addSortChangedExternalIds(fresh);
  }

  watch(
    () => tasksStore.taggingActive,
    (active) => {
      if (active || !heldSortChangedIds.size) return;
      const ids = Array.from(heldSortChangedIds);
      heldSortChangedIds.clear();
      onFlagSortChanged(ids);
    },
  );

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
      heldSortChangedIds.clear();
    },
  );

  onUnmounted(() => {
    disconnectUpdatesSocket();
    gridWsScheduler.cancel();
    if (externalMovesPendingTimer) {
      clearTimeout(externalMovesPendingTimer);
      externalMovesPendingTimer = null;
    }
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
