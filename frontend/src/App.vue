<script setup>
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  reactive,
  ref,
  watch,
} from "vue";
import { useTheme } from "vuetify";
import { useRoute, useRouter } from "vue-router";
import { useReviewRoute } from "./composables/useReviewRoute";
import {
  API_BASE_URL,
  appendShareToken,
  isReadOnly,
  sessionContext,
} from "./utils/apiClient";
import { getUserConfig, patchUserConfig } from "./api/config";
import { useSelectionStore } from "./stores/useSelectionStore";
import { useFilterStore } from "./stores/useFilterStore";
import { useSortStore } from "./stores/useSortStore";
import { useGridStore } from "./stores/useGridStore";
import { useExportStore } from "./stores/useExportStore";
import { useSidebarStore } from "./stores/useSidebarStore";
import { useUserPrefsStore } from "./stores/useUserPrefsStore";
import { useProjectStore } from "./stores/useProjectStore";
import { useWsStore } from "./stores/useWsStore";
import { useSearchStore } from "./stores/useSearchStore";
import { useReviewSessionsStore } from "./stores/useReviewSessionsStore";
import { useSnapshotsStore } from "./stores/useSnapshotsStore";
import { useTasksStore } from "./stores/useTasksStore";
import { useLockedSetsStore } from "./stores/useLockedSetsStore";
import { useOperationStore } from "./stores/useOperationStore";
import { useDedupStore } from "./stores/useDedupStore";
import {
  ALL_PICTURES_ID,
  SCRAPHEAP_PICTURES_ID,
  UNASSIGNED_PICTURES_ID,
  useViewStore,
} from "./stores/useViewStore";
import { redoKeyHint, undoKeyHint } from "./utils/shortcutHints";
import { useGridRealtimeSync } from "./composables/useGridRealtimeSync";

import SideBar from "./components/panels/SideBar.vue";
import TitleBar from "./components/TitleBar.vue";
import PhotosImportDialog from "./components/io/PhotosImportDialog.vue";
import RestoreConfirmDialog from "./components/widgets/RestoreConfirmDialog.vue";
import ImageGrid from "./components/views/ImageGrid.vue";
import DuplicateQueue from "./components/views/DuplicateQueue.vue";
import ReviewSessionsOverlay from "./components/views/ReviewSessionsOverlay.vue";
import StatsSidebar from "./components/panels/StatsSidebar.vue";
import ThumbnailUpgradeBanner from "./components/panels/ThumbnailUpgradeBanner.vue";
import NoticeHost from "./components/widgets/NoticeHost.vue";
import { useFloatingBottomInset } from "./composables/useBottomAnchor";
import { toPx } from "./utils/floatingBottom.js";
import { isInternalImageDrag } from "./utils/media.js";
import {
  clampSizeLevel,
  nearestSizeLevelForColumns,
} from "./utils/thumbnailSizes";

const BACKEND_URL = API_BASE_URL;

// --- Stores ---
const selectionStore = useSelectionStore();
const filterStore = useFilterStore();
const sortStore = useSortStore();
const gridStore = useGridStore();
const exportStore = useExportStore();
const sidebarStore = useSidebarStore();
const userPrefsStore = useUserPrefsStore();
const projectStore = useProjectStore();
const wsStore = useWsStore();
const searchStore = useSearchStore();
const reviewSessionsStore = useReviewSessionsStore();
const snapshotsStore = useSnapshotsStore();
const tasksStore = useTasksStore();
const lockedSetsStore = useLockedSetsStore();
const operationStore = useOperationStore();
const dedupStore = useDedupStore();
// Owns route → view resolution (the app's single route watcher). Route pushing
// stays here in App.vue; see stores/useViewStore.js.
const viewStore = useViewStore();
// Keycap labels for the shortcuts dialog. The binding accepts Ctrl and Meta
// everywhere; only the hint is platform-specific.
const undoKeyHintKeys = undoKeyHint();
const redoKeyHintKeys = redoKeyHint();

// --- Router ---
const route = useRoute();
const router = useRouter();

// Keeps the tag-review overlay in the URL (?review=…), the same way ImageGrid
// keeps the image lightbox in ?overlay=<id>. See useReviewRoute.js.
useReviewRoute(route, router, reviewSessionsStore, { watch });

// The multi-select (union/overlap) bar is shown at the grid's bottom edge
// whenever more than one character or set is selected (mirrors ImageGrid's
// isMultiCharacterView / isSetOverlapView). Used to lift the F1 shortcuts FAB
// above that bar so it overlaps the visible grid, not the bar.
const multiSelectBarShown = computed(
  () =>
    (selectionStore.selectedCharacterIds?.length ?? 0) > 1 ||
    (selectionStore.selectedSetIds?.length ?? 0) > 1,
);

// --- Theme ---
const theme = useTheme();

// --- Component & DOM refs ---
const gridContainer = ref(null);
const sidebarRef = ref(null);
const statsSidebarRef = ref(null);
const mainAreaRef = ref(null);
const gridWrapperRef = ref(null);

// --- Local UI state ---
const shortcutsDialogOpen = ref(false);
const updateCheckDialogOpen = ref(false);
const photosDialogOpen = ref(false);
const folderScanning = ref(false);
const installType = ref("pip");
const dockerVariant = ref("gpu");
const loading = ref(null);
const error = ref(null);

// --- Config tracking ---
const configLoaded = ref(false);
const configLoading = ref(false);
const configApplying = ref(false);
const configSnapshot = ref({});
const config = reactive({
  sort: "",
  thumbnail: 256,
  thumbnail_mode: "square",
  sidebar_thumbnail_size: 64,
  show_stars: true,
  show_face_bboxes: false,
  show_problem_icon: true,
  expand_all_stacks: true,
  date_format: "locale",
  theme_mode: "light",
  stack_strictness: 0.92,
});

// --- Layout constants ---
const MIN_THUMBNAIL_SIZE = 96;
const MAX_THUMBNAIL_SIZE = 384;
const MIN_COLUMNS = 2;
const MAX_COLUMNS = 14;
const SIDEBAR_HIDE_BREAKPOINT = 790;
const STATS_HIDE_BREAKPOINT = 1280;
const SIDEBAR_REFRESH_DEBOUNCE_MS = 150;
const SIDEBAR_REFRESH_PICTURES_DEBOUNCE_MS = 800;
// Coalescing window for incoming grid-driving WS events (see
// useGridRealtimeSync). A burst of foreign events accumulates over this window
// and applies once per category instead of one fetch+rebuild per event.
const GRID_WS_COALESCE_MS = 200;

// --- Non-reactive internals ---
let mainAreaResizeObserver = null;
let updatesSocket = null;
let updatesReconnectTimer = null;
let columnsMenuCloseTimeout = null;
let sidebarRefreshDebounceTimeout = null;
let sidebarRefreshPicturesDebounceTimeout = null;
let sidebarRefreshPicturesFlash = false;
let gridWsCoalesceTimer = null;
// Unsubscribe handle for the desktop tray's "Settings" event (desktop only).
let stopOpenSettings = null;

// --- Computed ---
// Maps the current route to a sidebar folder key ('rf-{id}' or 'if-{id}') so
// the sidebar can highlight the correct folder on deep-link or back-navigation.
// Parsed once, by useViewStore.
const activeFolderKey = computed(() => viewStore.activeFolderKey);

const activeCategoryLabel = computed(() => {
  if (selectionStore.selectedFolderFilter) {
    return selectionStore.selectedFolderFilter.label || "Folder";
  }
  if (selectionStore.selectedSetIds.length > 1) {
    const modeLabel =
      { union: "Union", intersection: "Overlap", difference: "Difference" }[
        selectionStore.setMultiMode
      ] || "Multi";
    return `Sets – ${modeLabel} (${selectionStore.selectedSetIds.length})`;
  }
  if (selectionStore.selectedSet) {
    return selectionStore.lastSelectedSetLabel || "Picture Set";
  }
  if (selectionStore.selectedCharacterIds.length > 1) {
    const modeLabel =
      { union: "Union", intersection: "Overlap", difference: "Difference" }[
        selectionStore.characterMultiMode
      ] || "Multi";
    return `People – ${modeLabel} (${selectionStore.selectedCharacterIds.length})`;
  }
  if (selectionStore.selectedCharacter === ALL_PICTURES_ID)
    return "All Pictures";
  if (selectionStore.selectedCharacter === UNASSIGNED_PICTURES_ID)
    return "Unassigned Pictures";
  if (selectionStore.selectedCharacter === SCRAPHEAP_PICTURES_ID)
    return "Scrapheap";
  if (selectionStore.selectedCharacter) {
    return selectionStore.lastSelectedCharacterLabel || "Category";
  }
  return "All Pictures";
});

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
      if (wsStore.isUploadInProgress && payload?.type === "picture_imported") {
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

function onRestoreConfirmed() {
  gridStore.wsUpdateKey = Date.now();
  gridStore.refreshGridVersion();
  refreshSidebar();
}

function refreshSidebar(options = {}) {
  sidebarRef.value?.refreshSidebar(options);
  // The locked-sets store shares the sidebar's refresh triggers (manual emits,
  // characters_changed, and pictures_changed via the debounced pictures path,
  // which also fires on a lock/unlock PATCH's CHANGED_PICTURES event). The store
  // coalesces overlapping fetches, so calling it here on every refresh is cheap.
  lockedSetsStore.fetch();
  // The duplicates badge rides the same triggers: an import, a stack or a
  // verdict all move the count, and every one of them already causes a sidebar
  // refresh. The per-scope cache goes with it, since a context menu opened
  // afterwards must not quote a pre-change number.
  if (!isReadOnly.value) {
    dedupStore.invalidateScopeCounts();
    dedupStore.refreshCounts();
  }
}

function refreshSidebarDebounced() {
  if (sidebarRefreshDebounceTimeout) {
    clearTimeout(sidebarRefreshDebounceTimeout);
  }
  sidebarRefreshDebounceTimeout = setTimeout(() => {
    sidebarRefreshDebounceTimeout = null;
    refreshSidebar();
  }, SIDEBAR_REFRESH_DEBOUNCE_MS);
}

function refreshSidebarPicturesDebounced(flash) {
  if (flash) sidebarRefreshPicturesFlash = true;
  if (sidebarRefreshPicturesDebounceTimeout) {
    clearTimeout(sidebarRefreshPicturesDebounceTimeout);
  }
  sidebarRefreshPicturesDebounceTimeout = setTimeout(() => {
    sidebarRefreshPicturesDebounceTimeout = null;
    const doFlash = sidebarRefreshPicturesFlash;
    sidebarRefreshPicturesFlash = false;
    refreshSidebar(doFlash ? { flashCounts: true } : {});
  }, SIDEBAR_REFRESH_PICTURES_DEBOUNCE_MS);
}

/**
 * Open the settings dialog. `tab` deep-links to a nav entry (e.g. "scrapheap"
 * from the scrapheap header's "change" link); omitted callers land on Appearance.
 * @param {string} [tab]
 */
function openSettingsDialog(tab = "") {
  sidebarRef.value?.openSettingsDialog?.(typeof tab === "string" ? tab : "");
}

// ── Notice surface placement (notice-surface.md §2.2) ───────────────────────
// App.vue owns `--floating-bottom-h`: the height of the tallest bottom-anchored
// floating element currently visible inside the notice column's footprint, plus
// its gap. The elements themselves register through `useBottomAnchor` (the
// SelectionBar pill, and the grid breadcrumb below 600px), each reporting a
// MEASURED height from a ResizeObserver — the pill wraps and grows on coarse
// pointers, so a constant would let a notice overlap it.
const appViewportEl = ref(null);
const { inset: floatingBottomInset } = useFloatingBottomInset();

watch(
  [floatingBottomInset, appViewportEl],
  ([inset, el]) => {
    if (!el) return;
    el.style.setProperty("--floating-bottom-h", toPx(inset));
  },
  { immediate: true },
);

// The lightbox is a dark surface; the notice host takes its `--on-dark`
// modifier there so a white card does not read as foreign chrome (§2.5).
const lightboxOpen = ref(false);
const noticeOnDark = computed(() => lightboxOpen.value);

function openImportDialog() {
  photosDialogOpen.value = true;
}

async function handleLocalImport({ files, projectId } = {}) {
  photosDialogOpen.value = false;
  await nextTick();
  sidebarRef.value?.startLocalImport?.(files, projectId ?? null);
}

function isInsideImageGrid(event) {
  const target = event?.target;
  if (!(target instanceof Element)) return false;
  return Boolean(target.closest(".image-grid, .grid-scroll-wrapper"));
}

function isExternalFileDragEvent(event) {
  const dataTransfer = event?.dataTransfer;
  if (!dataTransfer) return false;
  // An internal app drag (grid thumbnail → sidebar character/set/project) is
  // never a file import — bail before the files check. On the desktop shell
  // (Electron) such a drag ALSO populates dataTransfer.files with the dragged
  // in-page image as a real File (the web does not), so without this guard the
  // window-level import handler mistakes the assign-drag for an external file
  // drop and imports the picture instead of assigning it.
  if (isInternalImageDrag(dataTransfer)) return false;
  const files = dataTransfer.files;
  if (files && files.length > 0) return true;
  const types = dataTransfer.types ? Array.from(dataTransfer.types) : [];
  return types.includes("Files") || types.includes("application/x-moz-file");
}

function handleWindowDragOver(event) {
  if (!isExternalFileDragEvent(event)) return;
  // The review overlay is a modal review surface; dropping files into it
  // must never start an import. Skip preventDefault so the drag is not shown as
  // droppable here.
  if (reviewSessionsStore.overlayOpen) return;
  event.preventDefault();
}

function handleWindowDrop(event) {
  if (!isExternalFileDragEvent(event)) return;
  // While the review overlay is open, swallow the drop without importing
  // (still preventDefault so the browser does not navigate to the dropped file).
  if (reviewSessionsStore.overlayOpen) {
    event.preventDefault();
    return;
  }
  event.preventDefault();
  if (isInsideImageGrid(event)) {
    return;
  }
  const droppedFiles = Array.from(event.dataTransfer?.files || []);
  if (!droppedFiles.length) return;
  const projectId = sidebarRef.value?.currentProjectId ?? null;
  sidebarRef.value?.startLocalImport?.(droppedFiles, projectId);
}

function handleWindowPaste(event) {
  // Ignore paste events originating from editable elements (text inputs etc.)
  const target = event.target;
  if (
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target?.isContentEditable
  ) {
    return;
  }
  const items = Array.from(event.clipboardData?.items || []);
  const mediaFiles = items
    .filter(
      (item) =>
        item.kind === "file" &&
        (item.type.startsWith("image/") || item.type.startsWith("video/")),
    )
    .map((item) => item.getAsFile())
    .filter(Boolean);
  if (!mediaFiles.length) return;
  event.preventDefault();
  const projectId = sidebarRef.value?.currentProjectId ?? null;
  sidebarRef.value?.startLocalImport?.(mediaFiles, projectId);
}

function updateSidebarBreakpoints() {
  if (typeof window !== "undefined") {
    sidebarStore.sidebarForcedHidden =
      window.innerWidth < SIDEBAR_HIDE_BREAKPOINT;
    sidebarStore.statsForcedHidden = window.innerWidth < STATS_HIDE_BREAKPOINT;
  }
}

function updateIsMobile() {
  updateSidebarBreakpoints();
  updateMaxColumns();
}

function updateMaxColumns() {
  // Maintain the responsive column bounds. gridStore.columns is derived from
  // the size level and clamps itself to these bounds, so there is nothing to
  // write back here — updating the bounds re-evaluates the derived count.
  const width = mainAreaRef.value?.clientWidth ?? window.innerWidth ?? 0;
  if (!width) {
    gridStore.minColumns = MIN_COLUMNS;
    gridStore.maxColumns = MAX_COLUMNS;
    return;
  }
  const availableWidth = Math.max(0, width - 8);
  const computedMin = Math.max(
    1,
    Math.ceil(availableWidth / MAX_THUMBNAIL_SIZE),
  );
  const computedMax = Math.max(
    computedMin,
    Math.floor(availableWidth / MIN_THUMBNAIL_SIZE),
  );
  gridStore.minColumns = Math.max(MIN_COLUMNS, computedMin);
  gridStore.maxColumns = Math.min(MAX_COLUMNS, computedMax);
}

function closeSidebarIfMobile() {
  if (sidebarStore.sidebarForcedHidden) {
    sidebarStore.hideAutoSidebar();
  }
}

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
    handleClearSearch();
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
  closeSidebarIfMobile();
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
    closeSidebarIfMobile();
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
  closeSidebarIfMobile();
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
 * Push a route without cluttering history on duplicate navigations.
 * Swallows NavigationDuplicated errors (vue-router throws on same-route push).
 */
function pushAppRoute(target) {
  if (route.query.token) {
    target.query = { token: route.query.token, ...target.query };
  }
  router.push(target).catch(() => {});
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
    if (scope.id !== undefined && scope.id !== null) query.scope_id = scope.id;
    if (scope.label) query.scope_label = scope.label;
    if (scope.icon) query.scope_icon = scope.icon;
  }
  pushAppRoute({ name: "duplicates", query });
}

// Route -> stores: install the app's single route watcher (immediately on
// mount for deep-linking, then on every navigation). The parsing and the
// writes live in useViewStore; App.vue keeps only the route PUSHING above.
viewStore.startRouteSync(route, { watch });

// A navigation retires the live undo receipt (owner decision, 2026-07-29):
// the pill narrates something that happened on the view being left, and a
// receipt carried into the next view reads as a fresh event there. Ctrl+Z
// keeps working everywhere regardless — the receipt is narration, not the
// undo affordance itself.
watch(
  () => route.fullPath,
  (next, prev) => {
    if (prev !== undefined && next !== prev) operationStore.dismissReceipt();
  },
);

// Stateless sidebar tabs: switching the Global ↔ Project mode (or the
// project picker) must not navigate or change the grid — the route is the
// single source of truth. These handlers therefore only mirror the value
// into the store (used for sidebar scoping); they never push a route.
// Grid navigation happens via explicit entry clicks (handleViewProject,
// handleSelectCharacter, handleSelectSet, handleSelectFolder).
function handleUpdateProjectViewMode(mode) {
  projectStore.projectViewMode = mode;
}

function handleUpdateSelectedProjectId(id) {
  projectStore.selectedProjectId = id;
}

// Explicit "view this project" entry click → navigate. useViewStore (watching
// the route) sets projectViewMode/selectedProjectId from the URL, which scopes
// the grid to the project.
function handleViewProject(id) {
  if (id == null) return;
  pushAppRoute({ name: "project", params: { id: String(id) } });
}

async function handleUpdateSelectedSort({ sort, descending }) {
  sortStore.selectedSort = sort;
  sortStore.selectedDescending = descending;
  closeSidebarIfMobile();
}

function handleUpdateSortOptions(options) {
  sortStore.sortOptions = Array.isArray(options) ? options : [];
}

function handleStackStatsUpdate(payload) {
  const expanded = Number(payload?.expanded ?? 0);
  const total = Number(payload?.total ?? 0);
  gridStore.expandedStackCount = Number.isFinite(expanded)
    ? Math.max(0, expanded)
    : 0;
  gridStore.totalStackCount = Number.isFinite(total) ? Math.max(0, total) : 0;
}

function handleUpdateSimilarityCharacter(val) {
  sortStore.selectedSimilarityCharacter = val;
  gridStore.refreshGridVersion();
  closeSidebarIfMobile();
}

function handleUpdateSimilarityOptions(options) {
  sortStore.similarityCharacterOptions = Array.isArray(options) ? options : [];
}

function handleUpdateHiddenTags(tags) {
  const nextTags = Array.isArray(tags) ? tags : [];
  if (
    userPrefsStore.hiddenTags.length === nextTags.length &&
    userPrefsStore.hiddenTags.every((tag, index) => tag === nextTags[index])
  ) {
    return;
  }
  userPrefsStore.hiddenTags = nextTags;
}

function handleUpdateApplyTagFilter(value) {
  const nextValue = Boolean(value);
  if (userPrefsStore.applyTagFilter === nextValue) return;
  userPrefsStore.applyTagFilter = nextValue;
}

function handleUpdateDateFormat(value) {
  if (value == null) return;
  const nextValue = String(value);
  if (nextValue === userPrefsStore.dateFormat) return;
  userPrefsStore.dateFormat = nextValue;
}

function handleUpdateThemeMode(value) {
  if (value == null) return;
  userPrefsStore.themeMode = String(value);
}

async function handleUpdateCheckForUpdates(value) {
  userPrefsStore.checkForUpdates = value;
  try {
    await patchUserConfig({ check_for_updates: value });
  } catch (e) {
    console.error("Failed to save check_for_updates preference:", e);
  }
}

function handleUpdateSidebarThumbnailSize(value) {
  const nextValue = Number(value);
  if (!Number.isFinite(nextValue)) return;
  userPrefsStore.sidebarThumbnailSize = nextValue;
}

// The sidebar's Scrapheap context menu asks to empty the heap. The sidebar has
// already switched the view to the scrapheap; defer to the next tick so the grid
// is showing that view before we open its existing consent-gated empty-forever
// confirm (whose post-confirm refetch then reconciles the right view). The grid
// is still fetching that view at this point, by construction — the confirm is
// deliberately not gated on that, and takes its counts from the server preview.
function handleEmptyScrapheapFromSidebar() {
  nextTick(() => gridContainer.value?.confirmEmptyScrapheap?.());
}

// The sidebar's person context menu asks for more pictures of that person
// (#636). The search runs across the whole library rather than inside the
// current view, so nothing here changes the selection or the route: the grid
// owns the search mode and shows its own result bar.
function handleSuggestPicturesForCharacter(character) {
  if (character?.id == null) return;
  nextTick(() =>
    gridContainer.value?.suggestPicturesForCharacter?.({
      id: character.id,
      name: character.name,
    }),
  );
}

// Open the stats sidebar and focus its Tasks tab. Shared by the thumbnail-mode
// "View progress" notice action and the ThumbnailUpgradeBanner's link, so both
// use the same statsSidebarRef.focusTasksTab() plumbing.
function focusTasksTabPanel() {
  sidebarStore.statsOpen = true;
  nextTick(() => statsSidebarRef.value?.focusTasksTab?.());
}

function handleUpdateThumbnailMode(value) {
  if (value !== "square" && value !== "justified") return;
  if (value === gridStore.thumbnailMode) return;
  // Apply immediately with no regeneration: both modes render from the same
  // stored bitmap (justified shows it whole; square crops it to the stored
  // rectangle), so the grid re-lays-out at once. The persist watch saves it,
  // and the radiogroup's aria-checked change is what a screen reader announces.
  gridStore.thumbnailMode = value;
}

function handleUpdateSidebarWidth(value) {
  const nextValue = Number(value);
  if (!Number.isFinite(nextValue)) return;
  userPrefsStore.sidebarWidth = nextValue;
}

async function fetchConfig() {
  if (isReadOnly.value) {
    userPrefsStore.themeMode = "dark";
    userPrefsStore.sidebarThumbnailSize = 32;
    gridStore.showProblemIcon = true;
    gridStore.showFaceBboxes = false;
    gridStore.showStars = true;
    return;
  }
  if (configLoading.value) return;
  configLoading.value = true;
  configApplying.value = true;
  try {
    const cfg = await getUserConfig();
    const sortValue = cfg.sort_order ?? cfg.sort;
    if (typeof sortValue === "string" && sortValue) {
      sortStore.selectedSort = sortValue;
    }
    if (typeof cfg.show_keyboard_hint === "boolean")
      userPrefsStore.showKeyboardHint = cfg.show_keyboard_hint;
    if (typeof cfg.show_face_bboxes === "boolean") {
      gridStore.showFaceBboxes = cfg.show_face_bboxes;
    }
    if (typeof cfg.show_problem_icon === "boolean") {
      gridStore.showProblemIcon = cfg.show_problem_icon;
    }
    if (typeof cfg.expand_all_stacks === "boolean") {
      gridStore.showStacks = cfg.expand_all_stacks;
    } else if (typeof cfg.show_stacks === "boolean") {
      gridStore.showStacks = cfg.show_stacks;
    }
    if (typeof cfg.compact_mode === "boolean") {
      gridStore.compactMode = cfg.compact_mode;
    }
    if (typeof cfg.sidebar_docked === "boolean") {
      sidebarStore.setSidebarDocked(cfg.sidebar_docked);
    }
    if (typeof cfg.sidebar_pinned === "boolean") {
      sidebarStore.setSidebarPinned(cfg.sidebar_pinned);
    }
    if (typeof cfg.date_format === "string" && cfg.date_format) {
      userPrefsStore.dateFormat = cfg.date_format;
    }
    if (typeof cfg.theme_mode === "string" && cfg.theme_mode) {
      userPrefsStore.themeMode = cfg.theme_mode;
    }
    if (typeof cfg.descending === "boolean") {
      sortStore.selectedDescending = cfg.descending;
    }
    if (typeof cfg.thumbnail_size_level === "number") {
      gridStore.sizeLevel = clampSizeLevel(cfg.thumbnail_size_level);
    } else if (typeof cfg.columns === "number") {
      // Legacy config predating the size ladder: derive the nearest level so
      // an old install keeps roughly the same tile size after upgrading.
      gridStore.sizeLevel = nearestSizeLevelForColumns(cfg.columns);
    }
    if (cfg.thumbnail_mode === "square" || cfg.thumbnail_mode === "justified") {
      gridStore.thumbnailMode = cfg.thumbnail_mode;
    }
    if (typeof cfg.sidebar_thumbnail_size === "number") {
      userPrefsStore.sidebarThumbnailSize = cfg.sidebar_thumbnail_size;
    }
    if (typeof cfg.sidebar_width === "number") {
      userPrefsStore.sidebarWidth = cfg.sidebar_width;
    }
    if (cfg.stack_strictness != null) {
      sortStore.stackThreshold = String(cfg.stack_strictness);
    }
    config.sort_order = sortValue || sortStore.selectedSort;
    config.descending = sortStore.selectedDescending;
    config.columns = gridStore.columns;
    config.thumbnail_mode = gridStore.thumbnailMode;
    config.sidebar_thumbnail_size = userPrefsStore.sidebarThumbnailSize;
    config.sidebar_width = userPrefsStore.sidebarWidth;
    config.show_stars =
      typeof cfg.show_stars === "boolean"
        ? cfg.show_stars
        : gridStore.showStars;
    config.show_face_bboxes =
      typeof cfg.show_face_bboxes === "boolean"
        ? cfg.show_face_bboxes
        : gridStore.showFaceBboxes;
    config.show_problem_icon =
      typeof cfg.show_problem_icon === "boolean"
        ? cfg.show_problem_icon
        : gridStore.showProblemIcon;
    config.expand_all_stacks =
      typeof cfg.expand_all_stacks === "boolean"
        ? cfg.expand_all_stacks
        : typeof cfg.show_stacks === "boolean"
          ? cfg.show_stacks
          : gridStore.showStacks;
    config.compact_mode =
      typeof cfg.compact_mode === "boolean"
        ? cfg.compact_mode
        : gridStore.compactMode;
    config.sidebar_docked =
      typeof cfg.sidebar_docked === "boolean"
        ? cfg.sidebar_docked
        : sidebarStore.sidebarDocked;
    config.sidebar_pinned =
      typeof cfg.sidebar_pinned === "boolean"
        ? cfg.sidebar_pinned
        : sidebarStore.sidebarPinned;
    config.date_format = userPrefsStore.dateFormat;
    config.theme_mode = userPrefsStore.themeMode;
    config.stack_strictness =
      cfg.stack_strictness != null
        ? cfg.stack_strictness
        : config.stack_strictness;
    const similarityValue =
      cfg.similarity_character ?? cfg.selected_similarity_character;
    sortStore.selectedSimilarityCharacter =
      similarityValue ?? sortStore.selectedSimilarityCharacter ?? null;
    const newHiddenTags = Array.isArray(cfg.hidden_tags)
      ? cfg.hidden_tags
      : [];
    if (
      userPrefsStore.hiddenTags.length !== newHiddenTags.length ||
      userPrefsStore.hiddenTags.some((tag, i) => tag !== newHiddenTags[i])
    ) {
      userPrefsStore.hiddenTags = newHiddenTags;
    }
    userPrefsStore.applyTagFilter = Boolean(cfg.apply_tag_filter);
    const rawPt = cfg.smart_score_penalised_tags;
    if (rawPt && typeof rawPt === "object" && !Array.isArray(rawPt)) {
      userPrefsStore.penalisedTagWeights = Object.fromEntries(
        Object.entries(rawPt).map(([k, v]) => [
          String(k).trim().toLowerCase(),
          Number(v) || 0,
        ]),
      );
    } else if (Array.isArray(rawPt)) {
      userPrefsStore.penalisedTagWeights = Object.fromEntries(
        rawPt.map((t) => [
          String(t || "")
            .trim()
            .toLowerCase(),
          3,
        ]),
      );
    } else {
      userPrefsStore.penalisedTagWeights = {};
    }
    config.selectedSimilarityCharacter = sortStore.selectedSimilarityCharacter;
    configSnapshot.value = {
      sort: sortStore.selectedSort || "",
      descending: sortStore.selectedDescending,
      columns: typeof gridStore.columns === "number" ? gridStore.columns : null,
      sidebar_thumbnail_size:
        typeof userPrefsStore.sidebarThumbnailSize === "number"
          ? userPrefsStore.sidebarThumbnailSize
          : null,
      show_keyboard_hint: userPrefsStore.showKeyboardHint,
      show_face_bboxes: gridStore.showFaceBboxes,
      show_problem_icon: gridStore.showProblemIcon,
      expand_all_stacks: gridStore.showStacks,
      compact_mode: gridStore.compactMode,
      sidebar_docked: sidebarStore.sidebarDocked,
      sidebar_pinned: sidebarStore.sidebarPinned,
      date_format: userPrefsStore.dateFormat,
      theme_mode: userPrefsStore.themeMode,
      similarity_character: sortStore.selectedSimilarityCharacter,
      stack_strictness:
        cfg.stack_strictness != null
          ? Number(cfg.stack_strictness)
          : null,
      hidden_tags: userPrefsStore.hiddenTags,
      apply_tag_filter: userPrefsStore.applyTagFilter,
    };
    filterStore.comfyuiConfigured = Boolean(cfg?.comfyui_url);
    if (typeof cfg?.public_url === "string" && cfg.public_url) {
      userPrefsStore.publicUrl = cfg.public_url;
    }
    userPrefsStore.embedWatermark = Boolean(cfg?.embed_watermark);
    userPrefsStore.hidePurgeSnapshotWarning = Boolean(
      cfg?.hide_purge_snapshot_warning,
    );
    const cfu = cfg?.check_for_updates;
    userPrefsStore.checkForUpdates =
      cfu === true ? true : cfu === false ? false : null;
    if (userPrefsStore.checkForUpdates === null) {
      updateCheckDialogOpen.value = true;
    }
  } catch (e) {
    console.error("Failed to fetch user config:", e);
  } finally {
    configApplying.value = false;
    configLoading.value = false;
    configLoaded.value = true;
  }
}

async function patchConfigUIOptions() {
  if (!configLoaded.value || configLoading.value || configApplying.value)
    return;
  const patch = {};
  if (sortStore.selectedSort) patch.sort = sortStore.selectedSort;
  patch.descending = sortStore.selectedDescending;
  patch.thumbnail_size_level = gridStore.sizeLevel;
  // Keep the legacy `columns` field in sync with the derived count so older
  // clients (and anything still reading `columns`) stay consistent.
  if (gridStore.columns) patch.columns = gridStore.columns;
  if (userPrefsStore.sidebarThumbnailSize) {
    patch.sidebar_thumbnail_size = userPrefsStore.sidebarThumbnailSize;
  }
  if (userPrefsStore.sidebarWidth) {
    patch.sidebar_width = userPrefsStore.sidebarWidth;
  }
  if (typeof userPrefsStore.showKeyboardHint === "boolean")
    patch.show_keyboard_hint = userPrefsStore.showKeyboardHint;
  if (typeof gridStore.showFaceBboxes === "boolean") {
    patch.show_face_bboxes = gridStore.showFaceBboxes;
  }
  if (typeof gridStore.showProblemIcon === "boolean") {
    patch.show_problem_icon = gridStore.showProblemIcon;
  }
  if (typeof gridStore.showStacks === "boolean") {
    patch.expand_all_stacks = gridStore.showStacks;
  }
  if (typeof gridStore.compactMode === "boolean") {
    patch.compact_mode = gridStore.compactMode;
  }
  if (
    gridStore.thumbnailMode === "square" ||
    gridStore.thumbnailMode === "justified"
  ) {
    patch.thumbnail_mode = gridStore.thumbnailMode;
  }
  if (typeof sidebarStore.sidebarDocked === "boolean") {
    patch.sidebar_docked = sidebarStore.sidebarDocked;
  }
  if (typeof sidebarStore.sidebarPinned === "boolean") {
    patch.sidebar_pinned = sidebarStore.sidebarPinned;
  }
  if (
    typeof userPrefsStore.dateFormat === "string" &&
    userPrefsStore.dateFormat
  ) {
    patch.date_format = userPrefsStore.dateFormat;
  }
  if (
    typeof userPrefsStore.themeMode === "string" &&
    userPrefsStore.themeMode
  ) {
    patch.theme_mode = userPrefsStore.themeMode;
  }
  if (sortStore.selectedSimilarityCharacter != null) {
    patch.similarity_character = sortStore.selectedSimilarityCharacter;
  }
  if (sortStore.stackThreshold != null && sortStore.stackThreshold !== "") {
    const parsed = parseFloat(String(sortStore.stackThreshold));
    if (Number.isFinite(parsed)) {
      patch.stack_strictness = parsed;
    }
  }

  const snapshot = configSnapshot.value || {};
  const changed = Object.fromEntries(
    Object.entries(patch).filter(([key, value]) => snapshot[key] !== value),
  );
  if (Object.keys(changed).length === 0) {
    return;
  }

  try {
    await patchUserConfig(changed);
    configSnapshot.value = { ...snapshot, ...changed };
  } catch (e) {
    console.error("Error patching user config:", e);
  }
}

/**
 * Is a modal surface (a dialog, the lightbox) currently covering the app?
 *
 * There is no shared flag for this: every dialog owns its own `open` ref, so
 * the honest single source is the scrim Vuetify renders for every active
 * overlay. Used to decline global shortcuts whose feedback would be invisible
 * behind it.
 */
function isModalOverlayOpen() {
  if (typeof document === "undefined") return false;
  return document.querySelector(".v-overlay--active .v-overlay__scrim") != null;
}

function handleGlobalKeydown(e) {
  // The review overlay is modal and owns its own keyboard handler; don't
  // run the app/grid shortcuts (scroll, search, help) behind it.
  if (reviewSessionsStore.overlayOpen) return;
  // The LIGHTBOX owns the keyboard too, with its own undo binding and its own
  // receipt. This used to be enforced implicitly by listener order — the
  // overlay mounted before App, ran first, and stopImmediatePropagation()
  // silenced this handler — but the Duplicates view unmounts and remounts the
  // grid (and the overlay inside it), which re-registers their listeners
  // AFTER this one and silently flips that order. The result was one Ctrl+Z
  // running TWO undos: this handler's, then the overlay's, which the
  // operation store's busy-queue happily executed as a queued second step.
  // Ownership is therefore stated here explicitly, on the same DOM signal the
  // overlay renders (`.image-overlay` is v-if'd on open), not on ordering.
  if (
    typeof document !== "undefined" &&
    document.querySelector(".image-overlay") != null
  ) {
    return;
  }
  // Match the strictness the grid and the lightbox already use: a SELECT and an
  // ARIA textbox are typing surfaces too, and the event target matters as much
  // as `document.activeElement` (a Vuetify combobox moves focus around).
  const isEditable = [e.target, document.activeElement].some(
    (el) =>
      el instanceof HTMLElement &&
      (el.isContentEditable ||
        ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) ||
        el.getAttribute("role") === "textbox"),
  );

  // The auto-hide sidebar is revealed by hover (or tap), so WCAG 2.1 SC 1.4.13
  // "Content on Hover or Focus" applies: it must be dismissible without moving
  // the pointer. Escape is that mechanism, and for a touch user it is the only
  // dismissal besides the scrim itself. Deliberately not preventDefault-ed, and
  // skipped while typing, so the other Escape owners keep theirs. The sidebar
  // context menu stops propagation from a capture-phase listener and never
  // reaches here at all.
  if (
    e.key === "Escape" &&
    !isEditable &&
    sidebarStore.sidebarOverlay &&
    sidebarStore.sidebarVisible
  ) {
    sidebarStore.hideAutoSidebar();
  }

  // Undo / redo. Global by design: the shortcut has to work with no receipt on
  // screen, and every undo raises one, so the result is always narrated. Ctrl
  // and Meta are both accepted (the HINT is platform-specific, the binding is
  // not); Ctrl+Shift+Z is the macOS redo convention and is accepted everywhere.
  //
  // Four guards, each for its own reason:
  //   * typing: a text field keeps its own native undo stack;
  //   * read-only: the endpoints are owner-only anyway;
  //   * auto-repeat: a HELD Ctrl+Z must not walk the whole stack;
  //   * a modal DIALOG owns the screen: the receipt lives on --z-floating,
  //     under any dialog scrim, so an undo fired from there would mutate the
  //     library with no visible narration. That breaks the design's own "every
  //     undo raises a receipt" invariant, so the shortcut declines rather than
  //     acting blind.
  //
  // The lightbox is NOT covered by that last guard and never was:
  // `isModalOverlayOpen()` looks for a Vuetify scrim, and `.image-overlay`
  // renders its own. The lightbox is excluded by the explicit `.image-overlay`
  // check at the top of this handler (listener ORDER used to do it, until the
  // Duplicates view's grid remount flipped it — see that comment). Undo works
  // in the lightbox through its own key handler plus `OverlayActionReceipt`,
  // fitted to that surface's GUI per the owner's ruling.
  if (
    (e.ctrlKey || e.metaKey) &&
    !e.altKey &&
    !e.repeat &&
    !isEditable &&
    !isReadOnly.value &&
    !isModalOverlayOpen()
  ) {
    const key = e.key?.toLowerCase();
    if (key === "z" && !e.shiftKey) {
      e.preventDefault();
      operationStore.undo();
    } else if (key === "y" || (key === "z" && e.shiftKey)) {
      e.preventDefault();
      operationStore.redo();
    }
  }

  const keys = ["Home", "End", "PageUp", "PageDown"];
  if (keys.includes(e.key) && !isEditable) {
    // These keys drive grid scrolling only. Prevent the browser's default
    // scroll so they don't also scroll the sidebar (or the page).
    e.preventDefault();
    const grid = gridContainer.value;
    if (grid && typeof grid.onGlobalKeyPress === "function") {
      grid.onGlobalKeyPress(e.key, e);
    }
  }
  if (e.key === "f" && !e.ctrlKey && !e.metaKey && !e.altKey) {
    if (!isEditable) {
      e.preventDefault();
      searchStore.requestSearchFocus();
    }
  }
  if (
    (e.key === "?" || e.key === "F1") &&
    !e.ctrlKey &&
    !e.metaKey &&
    !e.altKey &&
    !isEditable
  ) {
    e.preventDefault();
    shortcutsDialogOpen.value = !shortcutsDialogOpen.value;
  }
  if (
    e.key === "F2" &&
    !e.ctrlKey &&
    !e.metaKey &&
    !e.altKey &&
    !isEditable &&
    !isReadOnly.value
  ) {
    const gridHasFocus = gridContainer.value?.hasCursorFocus === true;
    if (!gridHasFocus) {
      e.preventDefault();
      sidebarRef.value?.openCurrentSelectionEditor?.();
    }
  }
}

function resolveThemeName(mode) {
  return mode === "dark" ? "pixlStashDark" : "pixlStashLight";
}

async function handleImagesAssignedToCharacter({ characterId, imageIds }) {
  const current = selectionStore.selectedCharacter;
  // Unassigned view: assigned pictures leave the unassigned bucket — drop their
  // tiles from the grid immediately.
  if (current === UNASSIGNED_PICTURES_ID && !selectionStore.selectedSet) {
    if (
      gridContainer.value &&
      typeof gridContainer.value.removeImagesById === "function"
    ) {
      gridContainer.value.removeImagesById(imageIds);
    }
    return;
  }
  // Viewing a specific character: reassigning pictures (and their whole stack)
  // to a DIFFERENT character moves them out of this view. Refetch so they
  // disappear right away instead of lingering until the view changes — a plain
  // removeImagesById can't catch every stack member (a collapsed drag only
  // carries the leader id).
  const isSpecificCharacterView =
    current != null &&
    !selectionStore.selectedSet &&
    String(current) !== String(ALL_PICTURES_ID) &&
    String(current) !== String(UNASSIGNED_PICTURES_ID) &&
    String(current) !== String(SCRAPHEAP_PICTURES_ID);
  if (isSpecificCharacterView && String(current) !== String(characterId)) {
    gridStore.refreshGridVersion();
  }
}

function handleImagesMoved({ imageIds, kind, refresh }) {
  if (kind === "reference-folder" || refresh) {
    wsStore.clearSortChangedExternalIds();
    gridStore.refreshGridVersion();
    refreshSidebar();
    return;
  }
  if (
    selectionStore.selectedCharacter !== UNASSIGNED_PICTURES_ID ||
    selectionStore.selectedSet
  ) {
    return;
  }
  if (
    gridContainer.value &&
    typeof gridContainer.value.removeImagesById === "function"
  ) {
    gridContainer.value.removeImagesById(imageIds);
  }
}

function handleFacesAssignedToCharacter() {
  if (
    gridContainer.value &&
    typeof gridContainer.value.clearFaceSelection === "function"
  ) {
    gridContainer.value.clearFaceSelection();
  }
}

function refreshExportCount() {
  const counts = gridContainer.value?.getExportCount?.();
  if (!counts) return;
  exportStore.exportSelectedCount = Number(counts.selectedCount) || 0;
  exportStore.exportTotalCount = Number(counts.totalCount) || 0;
}

function confirmExportZip() {
  gridContainer.value?.exportCurrentViewToZip({
    exportType: exportStore.exportType,
    captionMode: exportStore.exportCaptionMode,
    tagFormat: exportStore.exportTagFormat,
    includeCharacterName: exportStore.exportIncludeCharacterName,
    useOriginalFileNames: exportStore.exportUseOriginalFileNames,
    resolution: exportStore.exportResolution,
    bboxMode: exportStore.exportBboxMode,
  });
  exportStore.exportMenuOpen = false;
}

// --- Review tags overlay ---
// Visibility lives in the store so the grid toolbar can open it directly.

function handleClearSearch() {
  searchStore.searchQuery = "";
  searchStore.searchInput = "";
  searchStore.isSearchHistoryOpen = false;
  gridStore.refreshGridVersion();
}

function handleResetToAll() {
  selectionStore.selectedCharacter = ALL_PICTURES_ID;
  selectionStore.selectedSet = null;
  selectionStore.selectedSetIds = [];
  selectionStore.lastSelectedCharacterLabel = "All Pictures";
  sortStore.selectedSort = "DATE";
  sortStore.selectedDescending = true;
  sortStore.selectedSimilarityCharacter = null;
  searchStore.searchQuery = "";
  filterStore.resetFilters();
  gridStore.refreshGridVersion();
  closeSidebarIfMobile();
}

// --- Watchers ---
watch(
  () => searchStore.searchQuery,
  (newVal, oldVal) => {
    if (searchStore.searchInput !== newVal) {
      searchStore.searchInput = newVal || "";
    }
    if (!newVal && oldVal) {
      gridStore.refreshGridVersion();
    }
  },
);

watch([() => searchStore.searchInput, () => searchStore.searchHistory], () => {
  const needle = (searchStore.searchInput || "").trim();
  if (!needle) {
    searchStore.isSearchHistoryOpen = false;
    return;
  }
  searchStore.isSearchHistoryOpen =
    searchStore.filteredSearchHistory.length > 0;
});

watch(
  [() => sortStore.selectedSort, () => sortStore.selectedDescending],
  () => {
    patchConfigUIOptions();
    // No refreshGridVersion() here: ImageGrid's selection watch already fires
    // when selectedSort/selectedDescending props change, so calling
    // refreshGridVersion() would produce a redundant second grid fetch.
  },
);

watch(
  () => userPrefsStore.hiddenTags,
  () => {
    gridStore.refreshGridVersion();
    if (userPrefsStore.applyTagFilter) {
      refreshSidebarDebounced();
    }
  },
);

watch(
  () => userPrefsStore.applyTagFilter,
  () => {
    gridStore.refreshGridVersion();
    refreshSidebarDebounced();
  },
);

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

watch(
  () => gridStore.thumbnailSize,
  () => {
    patchConfigUIOptions();
    updateMaxColumns();
  },
);

watch(
  () => userPrefsStore.showKeyboardHint,
  () => {
    if (!configLoaded.value) return;
    patchConfigUIOptions();
  },
);

watch(
  [
    () => gridStore.showFaceBboxes,
    () => gridStore.showProblemIcon,
    () => gridStore.showStacks,
    () => gridStore.compactMode,
  ],
  () => {
    patchConfigUIOptions();
  },
);

watch(
  [
    () => gridStore.showFaceBboxes,
    () => gridStore.showProblemIcon,
    () => gridStore.showStacks,
  ],
  () => {},
  { immediate: true },
);

watch(
  () => sortStore.selectedSimilarityCharacter,
  () => {
    patchConfigUIOptions();
  },
);

watch(
  () => sortStore.stackThreshold,
  () => {
    if (!configLoaded.value) return;
    patchConfigUIOptions();
  },
);

watch(
  () => gridStore.sizeLevel,
  () => {
    if (!configLoaded.value) return;
    patchConfigUIOptions();
  },
);

watch(
  () => gridStore.thumbnailMode,
  () => {
    if (!configLoaded.value) return;
    patchConfigUIOptions();
  },
);

watch(
  () => sidebarStore.sidebarDocked,
  () => {
    if (!configLoaded.value) return;
    patchConfigUIOptions();
  },
);

watch(
  () => sidebarStore.sidebarPinned,
  () => {
    if (!configLoaded.value) return;
    patchConfigUIOptions();
  },
);

watch(
  () => userPrefsStore.sidebarThumbnailSize,
  () => {
    if (!configLoaded.value) return;
    patchConfigUIOptions();
  },
);

watch(
  () => userPrefsStore.sidebarWidth,
  () => {
    if (!configLoaded.value) return;
    patchConfigUIOptions();
  },
);

watch(
  () => userPrefsStore.dateFormat,
  () => {
    if (!configLoaded.value) return;
    patchConfigUIOptions();
    gridStore.refreshGridVersion();
  },
);

watch(
  () => gridStore.gridVersion,
  () => {
    wsStore.clearPendingExternalImportIds();
    wsStore.clearSortChangedExternalIds();
  },
);

watch(
  () => userPrefsStore.themeMode,
  (value) => {
    theme.global.name.value = resolveThemeName(value);
    if (!configLoaded.value) return;
    patchConfigUIOptions();
  },
  { immediate: true },
);

watch(
  () => exportStore.exportMenuOpen,
  async (isOpen) => {
    if (!isOpen) return;
    await nextTick();
    refreshExportCount();
  },
);

watch(
  () => sidebarStore.statsOpen,
  () => {
    updateIsMobile();
  },
);

// --- Lifecycle ---
onMounted(async () => {
  // Start the app-wide tasks poll so the activity indicators (Tasks-tab icon,
  // stats-sidebar light) are live everywhere, not only while the Tasks tab is
  // open. The store self-throttles when idle and pauses on a hidden tab.
  tasksStore.startPolling();
  fetch("/version")
    .then((r) => r.json())
    .then((data) => {
      if (typeof data?.install_type === "string") {
        installType.value = data.install_type;
      }
      if (typeof data?.docker_variant === "string") {
        dockerVariant.value = data.docker_variant;
      }
    })
    .catch(() => {});
  await fetchConfig();
  // Snapshots are owner-only (full unscoped access); READ / share sessions
  // would 403 on every fetch otherwise.
  if (!isReadOnly.value) {
    snapshotsStore.fetchSnapshots();
    // Seed the undo stack so the toolbar control is correctly enabled on the
    // first frame. This read establishes the "already seen" watermark, so the
    // history it returns cannot pop a receipt for something that happened
    // before the tab existed.
    operationStore.refresh({ narrate: false });
  }
  // Navigate to the scoped resource when a share token is active
  const ctx = sessionContext.value;
  if (ctx && ctx.scope !== "ALL") {
    if (ctx.resource_type === "picture_set") {
      selectionStore.selectedSet = ctx.resource_id;
      selectionStore.selectedCharacter = ALL_PICTURES_ID;
    } else if (ctx.resource_type === "character") {
      selectionStore.selectedCharacter = ctx.resource_id;
      selectionStore.selectedSet = null;
    } else if (ctx.resource_type === "project") {
      projectStore.selectedProjectId = ctx.resource_id;
      projectStore.projectViewMode = "project";
      selectionStore.selectedSet = null;
      selectionStore.selectedCharacter = ALL_PICTURES_ID;
    }
  }
  updateIsMobile();
  window.addEventListener("resize", updateIsMobile);
  window.addEventListener("keydown", handleGlobalKeydown);
  window.addEventListener("dragover", handleWindowDragOver, true);
  window.addEventListener("drop", handleWindowDrop, true);
  window.addEventListener("paste", handleWindowPaste, true);
  // Desktop tray → "Settings" opens the Settings dialog directly.
  if (window.pixlstashDesktop?.onOpenSettings) {
    stopOpenSettings = window.pixlstashDesktop.onOpenSettings(() =>
      openSettingsDialog(),
    );
  }
  refreshSidebar();
  updateMaxColumns();
  connectUpdatesSocket();
  if (typeof ResizeObserver !== "undefined" && mainAreaRef.value) {
    mainAreaResizeObserver = new ResizeObserver(() => {
      updateMaxColumns();
      updateIsMobile();
    });
    mainAreaResizeObserver.observe(mainAreaRef.value);
    if (gridWrapperRef.value) {
      mainAreaResizeObserver.observe(gridWrapperRef.value);
    }
  }
});

onBeforeUnmount(() => {
  disconnectUpdatesSocket();
  tasksStore.stopPolling();
  if (stopOpenSettings) stopOpenSettings();
  window.removeEventListener("resize", updateIsMobile);
  window.removeEventListener("keydown", handleGlobalKeydown);
  window.removeEventListener("dragover", handleWindowDragOver, true);
  window.removeEventListener("drop", handleWindowDrop, true);
  window.removeEventListener("paste", handleWindowPaste, true);
  if (mainAreaResizeObserver) {
    mainAreaResizeObserver.disconnect();
    mainAreaResizeObserver = null;
  }
  if (columnsMenuCloseTimeout) {
    clearTimeout(columnsMenuCloseTimeout);
    columnsMenuCloseTimeout = null;
  }
  if (sidebarRefreshDebounceTimeout) {
    clearTimeout(sidebarRefreshDebounceTimeout);
    sidebarRefreshDebounceTimeout = null;
  }
  if (sidebarRefreshPicturesDebounceTimeout) {
    clearTimeout(sidebarRefreshPicturesDebounceTimeout);
    sidebarRefreshPicturesDebounceTimeout = null;
  }
  gridWsScheduler.cancel();
});

defineExpose({
  get sidebarVisible() {
    return sidebarStore.sidebarVisible;
  },
  get sidebarDocked() {
    return sidebarStore.sidebarDocked;
  },
  get mediaTypeFilter() {
    return filterStore.mediaTypeFilter;
  },
});
</script>
<template>
  <v-app>
    <div ref="appViewportEl" class="app-viewport">
      <TitleBar
        :install-type="installType"
        :check-for-updates="userPrefsStore.checkForUpdates"
      />
      <!-- App-level status strip: spans the whole shell above BOTH rails and the
           grid. Thumbnail regeneration repaints grid tiles, sidebar thumbnails
           and the Tasks row alike, so it is not a property of the grid column;
           mounting it inside `.main-area` used to push the stats rail down while
           leaving the left rail alone. -->
      <ThumbnailUpgradeBanner @view-progress="focusTasksTabPanel" />
      <div class="file-manager">
        <!-- Auto-hide (unpinned): a thin strip at the left edge reveals the
             sidebar overlay on hover (or tap, on touch). -->
        <div
          v-if="sidebarStore.sidebarOverlay && !sidebarStore.sidebarVisible"
          class="sidebar-hover-trigger"
          title="Show sidebar"
          @mouseenter="sidebarStore.revealSidebar()"
          @click="sidebarStore.revealSidebar()"
        >
          <span class="sidebar-hover-trigger-tab">
            <v-icon size="18">mdi-chevron-right</v-icon>
          </span>
        </div>
        <div
          class="sidebar-shell"
          :class="{
            open: sidebarStore.sidebarVisible,
            'sidebar-overlay': sidebarStore.sidebarOverlay,
          }"
          @mouseleave="
            sidebarStore.sidebarOverlay && sidebarStore.hideAutoSidebar()
          "
        >
          <SideBar
            ref="sidebarRef"
            :docked="sidebarStore.effectiveDocked"
            :selectedCharacter="selectionStore.selectedCharacter"
            :selectedCharacterIds="selectionStore.selectedCharacterIds"
            :allPicturesId="ALL_PICTURES_ID"
            :unassignedPicturesId="UNASSIGNED_PICTURES_ID"
            :scrapheapPicturesId="SCRAPHEAP_PICTURES_ID"
            :selectedSet="selectionStore.selectedSet"
            :selectedSetIds="selectionStore.selectedSetIds"
            :searchQuery="searchStore.searchQuery"
            :selectedSort="sortStore.selectedSort"
            :selectedDescending="sortStore.selectedDescending"
            :backendUrl="BACKEND_URL"
            :publicUrl="userPrefsStore.publicUrl"
            :embedWatermark="userPrefsStore.embedWatermark"
            :selectedSimilarityCharacter="sortStore.selectedSimilarityCharacter"
            :sidebarThumbnailSize="userPrefsStore.sidebarThumbnailSize"
            :sidebarWidth="userPrefsStore.sidebarWidth"
            :dateFormat="userPrefsStore.dateFormat"
            :themeMode="userPrefsStore.themeMode"
            :hasFolderFilter="selectionStore.selectedFolderFilter != null"
            :activeFolderKey="activeFolderKey"
            :externalProjectViewMode="projectStore.projectViewMode"
            :externalSelectedProjectId="projectStore.selectedProjectId"
            :checkForUpdates="userPrefsStore.checkForUpdates"
            :installType="installType"
            :dockerVariant="dockerVariant"
            :showKeyboardHint="userPrefsStore.showKeyboardHint"
            :thumbnailMode="gridStore.thumbnailMode"
            @update:thumbnail-mode="handleUpdateThumbnailMode"
            @empty-scrapheap="handleEmptyScrapheapFromSidebar"
            @suggest-pictures-for-character="handleSuggestPicturesForCharacter"
            @update:show-keyboard-hint="
              userPrefsStore.showKeyboardHint = $event
            "
            @update:similarity-options="handleUpdateSimilarityOptions"
            @update:sort-options="handleUpdateSortOptions"
            @update:hidden-tags="handleUpdateHiddenTags"
            @update:apply-tag-filter="handleUpdateApplyTagFilter"
            @update:comfyui-configured="filterStore.comfyuiConfigured = $event"
            @update:public-url="userPrefsStore.publicUrl = $event"
            @update:embed-watermark="userPrefsStore.embedWatermark = $event"
            @update:date-format="handleUpdateDateFormat"
            @update:theme-mode="handleUpdateThemeMode"
            @update:sidebar-thumbnail-size="handleUpdateSidebarThumbnailSize"
            @update:sidebar-width="handleUpdateSidebarWidth"
            @update:project-view-mode="handleUpdateProjectViewMode"
            @update:selected-project-id="handleUpdateSelectedProjectId"
            @view-project="handleViewProject"
            @select-character="handleSelectCharacter"
            :isDuplicatesView="isDuplicatesView"
            @select-duplicates="handleSelectDuplicates"
            @select-set="handleSelectSet"
            @select-folder="handleSelectFolder"
            @update:folder-scanning="folderScanning = $event"
            @images-assigned-to-character="handleImagesAssignedToCharacter"
            @images-moved="handleImagesMoved"
            @faces-assigned-to-character="handleFacesAssignedToCharacter"
            @update:selected-sort="handleUpdateSelectedSort"
            @update:similarity-character="handleUpdateSimilarityCharacter"
            @open-import-dialog="openImportDialog"
            @update:set-error="error = $event"
            @update:set-loading="loading = $event"
            @update:check-for-updates="handleUpdateCheckForUpdates"
          />
        </div>
        <!-- Click-outside scrim for the auto-hide sidebar. Purely a dimming
             surface and a tap target, so it is hidden from assistive tech; the
             keyboard/AT equivalent of clicking it is Escape (handleGlobalKeydown). -->
        <Transition name="backdrop-fade">
          <div
            v-if="sidebarStore.sidebarVisible && sidebarStore.sidebarOverlay"
            class="sidebar-backdrop"
            aria-hidden="true"
            @click="sidebarStore.hideAutoSidebar()"
          ></div>
        </Transition>

        <!-- Update-check consent dialog -->
        <v-dialog v-model="updateCheckDialogOpen" max-width="420" persistent>
          <v-card class="update-check-dialog">
            <v-card-title class="update-check-title"
              >Get notified about new versions?</v-card-title
            >
            <v-card-text class="update-check-body">
              PixlStash can check once a day and show a notice in the sidebar
              when a newer version is out, including security fixes you'll want
              to install promptly. It only ever sends your current version and
              install type (e.g. pip or docker), anonymously. You can turn this
              off any time in Settings → Behaviour.
              <br />
              <span class="update-check-note"
                >This check is served by our CDN (Cloudflare). We only ever see
                aggregate counts and never anything that identifies you
                personally.</span
              >
            </v-card-text>
            <v-card-actions class="update-check-actions">
              <v-btn
                variant="tonal"
                @click="
                  () => {
                    updateCheckDialogOpen = false;
                    handleUpdateCheckForUpdates(false);
                  }
                "
                >Not now</v-btn
              >
              <v-btn
                color="primary"
                variant="elevated"
                @click="
                  () => {
                    updateCheckDialogOpen = false;
                    handleUpdateCheckForUpdates(true);
                  }
                "
                >Notify me</v-btn
              >
            </v-card-actions>
          </v-card>
        </v-dialog>
        <PhotosImportDialog
          v-model:open="photosDialogOpen"
          :default-project-id="sidebarRef?.currentProjectId ?? null"
          :backend-url="BACKEND_URL"
          @local-import="handleLocalImport"
          @project-created="refreshSidebar"
        />
        <RestoreConfirmDialog
          v-model:open="snapshotsStore.restoreDialogOpen"
          :snapshot-id="snapshotsStore.restoreDialogSnapshotId"
          :resources="snapshotsStore.restoreDialogResources"
          @confirmed="onRestoreConfirmed"
        />
        <main :class="['main-area']" ref="mainAreaRef">
          <div
            :class="[
              'main-content',
              selectionStore.selectedCharacter ? 'accent-border' : '',
            ]"
          >
            <div
              ref="gridWrapperRef"
              style="
                flex: 1;
                min-width: 0;
                position: relative;
                overflow: hidden;
              "
            >
              <!-- Duplicates is a destination, not a filter, so it replaces
                   the grid rather than floating over it. The grid stays
                   unmounted while the queue is open, which is also what keeps
                   its fetches and its WebSocket reconciliation quiet. -->
              <DuplicateQueue
                v-if="isDuplicatesView"
                @open-settings="openSettingsDialog"
              />
              <ImageGrid
                v-else
                ref="gridContainer"
                :thumbnailSize="gridStore.thumbnailSize"
                :sidebarVisible="sidebarStore.sidebarVisible"
                :backendUrl="BACKEND_URL"
                :selectedCharacter="selectionStore.selectedCharacter"
                :selectedCharacterIds="selectionStore.selectedCharacterIds"
                :characterMultiMode="selectionStore.characterMultiMode"
                :selectedSet="selectionStore.selectedSet"
                :selectedSetIds="selectionStore.selectedSetIds"
                :setMultiMode="selectionStore.setMultiMode"
                :searchQuery="searchStore.searchQuery"
                :activeCategoryLabel="activeCategoryLabel"
                :isAllPicturesActive="selectionStore.isAllPicturesActive"
                :selectedSort="sortStore.selectedSort"
                :selectedDescending="sortStore.selectedDescending"
                :similarityCharacter="sortStore.selectedSimilarityCharacter"
                :stackThreshold="sortStore.stackThreshold"
                :showStars="gridStore.showStars"
                :gridVersion="gridStore.gridVersion"
                :wsUpdateKey="gridStore.wsUpdateKey"
                :wsTagUpdate="wsStore.wsTagUpdate"
                :wsDescriptionUpdate="wsStore.wsDescriptionUpdate"
                :wsSmartScoreUpdate="wsStore.wsSmartScoreUpdate"
                :wsDetectionUpdate="wsStore.wsDetectionUpdate"
                :wsPluginProgress="wsStore.wsPluginProgress"
                :showFaceBboxes="gridStore.showFaceBboxes"
                :showDetections="gridStore.showDetections"
                :showProblemIcon="gridStore.showProblemIcon"
                :penalisedTagWeights="userPrefsStore.penalisedTagWeights"
                :showStacks="gridStore.showStacks"
                :compactMode="gridStore.compactMode"
                :themeMode="userPrefsStore.themeMode"
                :dateFormat="userPrefsStore.dateFormat"
                :allPicturesId="ALL_PICTURES_ID"
                :unassignedPicturesId="UNASSIGNED_PICTURES_ID"
                :scrapheapPicturesId="SCRAPHEAP_PICTURES_ID"
                :projectViewMode="projectStore.projectViewMode"
                :selectedProjectId="projectStore.selectedProjectId"
                :characterProjectIds="projectStore.characterProjectIds"
                :setProjectIds="projectStore.setProjectIds"
                :setDifferenceBaseId="selectionStore.setDifferenceBaseId"
                :selectedSetNames="selectionStore.selectedSetNames"
                :publicUrl="userPrefsStore.publicUrl"
                :embedWatermark="userPrefsStore.embedWatermark"
                :folderScanning="folderScanning"
                :columns="gridStore.columns"
                :sizeLevel="gridStore.sizeLevel"
                :thumbnailMode="gridStore.thumbnailMode"
                @clear-search="handleClearSearch"
                @search-all="handleSearchAllPictures"
                @update:selected-sort="handleUpdateSelectedSort"
                @refresh-sidebar="refreshSidebar"
                @reset-to-all="handleResetToAll"
                @update:stack-stats="handleStackStatsUpdate"
                @clear-multi-selection="
                  () => {
                    selectionStore.selectedCharacterIds.length > 1
                      ? ((selectionStore.selectedCharacter = ALL_PICTURES_ID),
                        (selectionStore.selectedCharacterIds = []))
                      : ((selectionStore.selectedSet = null),
                        (selectionStore.selectedSetIds = []));
                  }
                "
                @update:character-multi-mode="
                  (v) => {
                    selectionStore.setCharacterMultiMode(v);
                  }
                "
                @update:set-multi-mode="
                  (v) => {
                    selectionStore.setSetMultiMode(v);
                  }
                "
                @update:set-difference-base-id="
                  (v) => {
                    selectionStore.setSetDifferenceBaseId(v);
                  }
                "
                @import-started="wsStore.isUploadInProgress = true"
                @import-ended="wsStore.isUploadInProgress = false"
                :pendingExternalImportCount="wsStore.pendingExternalImportCount"
                :sortChangedExternalCount="wsStore.sortChangedExternalCount"
                @load-pending-imports="loadPendingExternalImports"
                @load-sort-changed="loadSortChangedExternal"
                @flag-sort-changed="onFlagSortChanged"
                @update:visible-range-label="
                  gridStore.visibleRangeLabel = $event
                "
                @update:match-count="gridStore.matchCount = $event"
                @update:overlay-open="lightboxOpen = $event"
                @open-duplicates="handleSelectDuplicates({})"
                @open-settings="openSettingsDialog"
                @open-import="openImportDialog"
                @local-import="handleLocalImport"
                @confirm-export-zip="confirmExportZip"
              />
            </div>
          </div>
        </main>
        <!-- Peer of the left sidebar, NOT nested in the grid column: both rails
             then span the full height of `.file-manager` and nothing stacked in
             the main area can push one rail down without the other. -->
        <StatsSidebar ref="statsSidebarRef" />
      </div>
      <ReviewSessionsOverlay
        v-if="reviewSessionsStore.overlayOpen"
        :backendUrl="BACKEND_URL"
        @close="reviewSessionsStore.overlayOpen = false"
      />
      <!-- The notice surface. LAST child of `.app-viewport` on purpose
           (notice-surface.md §8): its buttons then come last in DOM order, so a
           keyboard user reaches them after the page content, not before it. It
           is global — it renders over the lightbox, the review overlay and
           Settings — so it must not be nested inside the grid column. -->
      <NoticeHost :on-dark="noticeOnDark" />
    </div>
    <button
      v-show="
        userPrefsStore.showKeyboardHint && !reviewSessionsStore.overlayOpen
      "
      class="shortcuts-fab"
      :class="{
        'shortcuts-fab--above-bar': multiSelectBarShown,
        'shortcuts-fab--stats-open': sidebarStore.statsOpen,
      }"
      type="button"
      title="Keyboard shortcuts (F1)"
      @click="shortcutsDialogOpen = true"
    >
      <v-icon size="20">mdi-keyboard</v-icon><span>F1</span>
    </button>
    <v-dialog v-model="shortcutsDialogOpen" max-width="480">
      <v-card class="shortcuts-dialog">
        <v-card-title class="shortcuts-dialog-title"
          >Keyboard shortcuts</v-card-title
        >
        <v-card-text class="shortcuts-dialog-body">
          <table class="shortcuts-table">
            <tbody>
              <tr>
                <td colspan="2" class="shortcuts-section">Grid view</td>
              </tr>
              <tr>
                <td><kbd>F</kbd></td>
                <td>Open search</td>
              </tr>
              <tr :class="{ 'shortcut-disabled': isReadOnly }">
                <td><kbd>1</kbd> – <kbd>5</kbd></td>
                <td>Set star rating on hovered / selected image(s)</td>
              </tr>
              <tr :class="{ 'shortcut-disabled': isReadOnly }">
                <td><kbd>T</kbd></td>
                <td>Tag selected images</td>
              </tr>
              <tr>
                <td><kbd>Ctrl</kbd>+<kbd>A</kbd></td>
                <td>Select all images</td>
              </tr>
              <tr :class="{ 'shortcut-disabled': isReadOnly }">
                <td>
                  <template v-for="(key, i) in undoKeyHintKeys" :key="key"
                    ><span v-if="i > 0">+</span><kbd>{{ key }}</kbd></template
                  >
                </td>
                <td>Undo the last change</td>
              </tr>
              <tr :class="{ 'shortcut-disabled': isReadOnly }">
                <td>
                  <template v-for="(key, i) in redoKeyHintKeys" :key="key"
                    ><span v-if="i > 0">+</span><kbd>{{ key }}</kbd></template
                  >
                </td>
                <td>Redo the change you just undid</td>
              </tr>
              <tr>
                <td><kbd>G</kbd></td>
                <td>Focus first visible image (start keyboard navigation)</td>
              </tr>
              <tr>
                <td><kbd>←</kbd> <kbd>→</kbd> <kbd>↑</kbd> <kbd>↓</kbd></td>
                <td>Move cursor and select image</td>
              </tr>
              <tr>
                <td><kbd>Shift</kbd>+<kbd>Arrow</kbd></td>
                <td>Extend selection</td>
              </tr>
              <tr>
                <td><kbd>Ctrl</kbd>+<kbd>Arrow</kbd></td>
                <td>Move cursor without changing selection</td>
              </tr>
              <tr>
                <td><kbd>Space</kbd></td>
                <td>Toggle selection of cursor image</td>
              </tr>
              <tr>
                <td><kbd>Enter</kbd></td>
                <td>Open cursor image</td>
              </tr>
              <tr :class="{ 'shortcut-disabled': isReadOnly }">
                <td><kbd>Delete</kbd></td>
                <td>Delete selected images</td>
              </tr>
              <tr>
                <td><kbd>Esc</kbd></td>
                <td>Clear selection</td>
              </tr>
              <tr>
                <td><kbd>S</kbd></td>
                <td>Open selection menu</td>
              </tr>
              <tr>
                <td><kbd>Home</kbd> / <kbd>End</kbd></td>
                <td>Jump to first / last image</td>
              </tr>
              <tr>
                <td><kbd>Page Up</kbd> / <kbd>Page Down</kbd></td>
                <td>Scroll image grid</td>
              </tr>
              <tr>
                <td colspan="2" class="shortcuts-section">Image overlay</td>
              </tr>
              <tr>
                <td><kbd>←</kbd> <kbd>→</kbd></td>
                <td>Previous / next image</td>
              </tr>
              <tr :class="{ 'shortcut-disabled': isReadOnly }">
                <td><kbd>1</kbd> – <kbd>5</kbd></td>
                <td>Set star rating</td>
              </tr>
              <tr :class="{ 'shortcut-disabled': isReadOnly }">
                <td><kbd>T</kbd></td>
                <td>Add tag</td>
              </tr>
              <tr>
                <td><kbd>Z</kbd></td>
                <td>Toggle zoom</td>
              </tr>
              <tr>
                <td><kbd>I</kbd></td>
                <td>Toggle info panel</td>
              </tr>
              <tr>
                <td><kbd>Esc</kbd></td>
                <td>Close overlay</td>
              </tr>
              <tr>
                <td colspan="2" class="shortcuts-section">General</td>
              </tr>
              <tr :class="{ 'shortcut-disabled': isReadOnly }">
                <td><kbd>F2</kbd></td>
                <td>Edit selected character or picture set</td>
              </tr>
              <tr>
                <td><kbd>?</kbd> / <kbd>F1</kbd></td>
                <td>Show / hide this dialog</td>
              </tr>
            </tbody>
          </table>
        </v-card-text>
      </v-card>
    </v-dialog>
  </v-app>
</template>
<style src="./App.css"></style>
