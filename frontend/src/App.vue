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
import {
  apiClient,
  API_BASE_URL,
  isReadOnly,
  sessionContext,
} from "./utils/apiClient";
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
import { useSnapshotsStore } from "./stores/useSnapshotsStore";

import SideBar from "./components/panels/SideBar.vue";
import PhotosImportDialog from "./components/io/PhotosImportDialog.vue";
import RestoreConfirmDialog from "./components/widgets/RestoreConfirmDialog.vue";
import ImageGrid from "./components/views/ImageGrid.vue";
import SearchOverlay from "./components/views/SearchOverlay.vue";
import StatsSidebar from "./components/panels/StatsSidebar.vue";

const BACKEND_URL = API_BASE_URL;
const ALL_PICTURES_ID = "ALL";
const UNASSIGNED_PICTURES_ID = "UNASSIGNED";
const SCRAPHEAP_PICTURES_ID = "SCRAPHEAP";

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
const snapshotsStore = useSnapshotsStore();

// --- Router ---
const route = useRoute();
const router = useRouter();

// --- Theme ---
const theme = useTheme();

// --- Component & DOM refs ---
const gridContainer = ref(null);
const sidebarRef = ref(null);
const toolbarRef = ref(null);
const mainAreaRef = ref(null);
const gridWrapperRef = ref(null);

// --- Local UI state ---
const shortcutsDialogOpen = ref(false);
const updateCheckDialogOpen = ref(false);
const photosDialogOpen = ref(false);
const folderScanning = ref(false);
const installType = ref("pip");
const dockerVariant = ref("gpu");
const columnsMenuOpen = ref(false);
const overlaysMenuOpen = ref(false);
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
const SIDEBAR_HIDE_BREAKPOINT = 1000;
const STATS_HIDE_BREAKPOINT = 1280;
const COLUMNS_MENU_CLOSE_DELAY_MS = 300;
const SIDEBAR_REFRESH_DEBOUNCE_MS = 150;
const SIDEBAR_REFRESH_PICTURES_DEBOUNCE_MS = 800;

// --- Non-reactive internals ---
let mainAreaResizeObserver = null;
let updatesSocket = null;
let updatesReconnectTimer = null;
let columnsMenuCloseTimeout = null;
let sidebarRefreshDebounceTimeout = null;
let sidebarRefreshPicturesDebounceTimeout = null;
let sidebarRefreshPicturesFlash = false;

// --- Computed ---
// Maps the current route to a sidebar folder key ('rf-{id}' or 'if-{id}') so
// the sidebar can highlight the correct folder on deep-link or back-navigation.
const activeFolderKey = computed(() => {
  const { name, params } = route;
  if (name === "ref-folder" && params.id) return `rf-${params.id}`;
  if (name === "import-folder" && params.id) return `if-${params.id}`;
  return null;
});

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
function buildUpdatesSocketUrl() {
  if (!BACKEND_URL) return "";
  const wsBase = BACKEND_URL.replace(/^http/i, "ws");
  return `${wsBase}/ws/updates`;
}

function shouldRefreshForPictureChange() {
  if (selectionStore.selectedSetIds.length) return false;
  const selectedChar = selectionStore.selectedCharacter;
  if (
    selectedChar &&
    selectedChar !== ALL_PICTURES_ID &&
    selectedChar !== UNASSIGNED_PICTURES_ID &&
    selectedChar !== SCRAPHEAP_PICTURES_ID
  ) {
    return false;
  }
  if ((searchStore.searchQuery || "").trim()) return false;
  return true;
}

function sendUpdatesFilters() {
  if (!updatesSocket) return;
  if (updatesSocket.readyState !== WebSocket.OPEN) return;
  updatesSocket.send(
    JSON.stringify({
      type: "set_filters",
      selected_character: selectionStore.selectedCharacter,
      selected_set: selectionStore.selectedSet,
      selected_sets: selectionStore.selectedSetIds,
      search_query: searchStore.searchQuery,
    }),
  );
}

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
    let payload = null;
    try {
      payload = JSON.parse(event.data);
    } catch (e) {
      return;
    }
    const isPictureChange =
      payload?.type === "pictures_changed" ||
      payload?.type === "picture_imported";
    if (isPictureChange) {
      if (!wsStore.isUploadInProgress) {
        refreshSidebarPicturesDebounced(true);
      }
      const pictureIds = Array.isArray(payload.picture_ids)
        ? payload.picture_ids
        : [];
      if (
        pictureIds.length > 0 &&
        sortStore.selectedSort === "LIKENESS_GROUPS" &&
        payload?.type !== "picture_imported"
      ) {
        const nextKey = (wsStore.wsTagUpdate?.key || 0) + 1;
        wsStore.wsTagUpdate = { key: nextKey, pictureIds };
        return;
      }
      if (
        payload?.type === "picture_imported" &&
        payload?.source !== "user" &&
        !wsStore.isUploadInProgress
      ) {
        // External import (API, watch-folder): show pill, don't auto-refresh
        wsStore.pendingExternalImportCount += Math.max(1, pictureIds.length);
      } else if (
        shouldRefreshForPictureChange() ||
        payload?.type === "picture_imported"
      ) {
        gridStore.wsUpdateKey = Date.now();
        gridStore.refreshGridVersion();
      }
    } else if (payload?.type === "characters_changed") {
      refreshSidebar();
    } else if (payload?.type === "tags_changed") {
      const pictureIds = Array.isArray(payload.picture_ids)
        ? payload.picture_ids
        : [];
      const nextKey = (wsStore.wsTagUpdate?.key || 0) + 1;
      wsStore.wsTagUpdate = { key: nextKey, pictureIds };
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
  gridStore.wsUpdateKey = Date.now();
  gridStore.refreshGridVersion();
}

function onRestoreConfirmed() {
  gridStore.wsUpdateKey = Date.now();
  gridStore.refreshGridVersion();
  refreshSidebar();
}

function refreshSidebar(options = {}) {
  sidebarRef.value?.refreshSidebar(options);
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

function openSettingsDialog() {
  sidebarRef.value?.openSettingsDialog?.();
}

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
  const files = dataTransfer.files;
  if (files && files.length > 0) return true;
  const types = dataTransfer.types ? Array.from(dataTransfer.types) : [];
  return types.includes("Files") || types.includes("application/x-moz-file");
}

function handleWindowDragOver(event) {
  if (!isExternalFileDragEvent(event)) return;
  event.preventDefault();
}

function handleWindowDrop(event) {
  if (!isExternalFileDragEvent(event)) return;
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

const STATS_SIDEBAR_WIDTH = 288;

function toolbarWidth() {
  // gridWrapperRef is the flex:1 div that wraps ImageGrid — its clientWidth
  // is exactly the toolbar width regardless of sidebar states.
  return gridWrapperRef.value?.clientWidth ?? window.innerWidth ?? 0;
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

function toggleDock() {
  sidebarStore.persistSidebarDocked(!sidebarStore.sidebarDocked);
}

function clampColumnsToBounds() {
  if (gridStore.columns > gridStore.maxColumns) {
    gridStore.columns = gridStore.maxColumns;
  }
  if (gridStore.columns < gridStore.minColumns) {
    gridStore.columns = gridStore.minColumns;
  }
}

function updateMaxColumns() {
  const width = mainAreaRef.value?.clientWidth ?? window.innerWidth ?? 0;
  if (!width) {
    gridStore.minColumns = MIN_COLUMNS;
    gridStore.maxColumns = MAX_COLUMNS;
    clampColumnsToBounds();
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
  clampColumnsToBounds();
}

function closeSidebarIfMobile() {
  if (sidebarStore.sidebarForcedHidden) {
    sidebarStore.sidebarVisible = false;
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
  wsStore.pendingExternalImportCount = 0;
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
      pushAppRoute({
        name: "project-set",
        params: { projectId: projId, id: String(sel.selectedSetIds[0]) },
      });
      return;
    }
    if (
      sel.selectedCharacter &&
      sel.selectedCharacter !== ALL_PICTURES_ID &&
      sel.selectedCharacter !== SCRAPHEAP_PICTURES_ID
    ) {
      pushAppRoute({
        name: "project-character",
        params: { projectId: projId, id: String(sel.selectedCharacter) },
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

/**
 * Apply the current route params/query to the Pinia stores.
 * Called on initial load (immediate) and on every route change so that
 * back/forward navigation and direct URL entry update the grid correctly.
 *
 * This function is intentionally idempotent — writing the same values to
 * reactive refs is a no-op in Vue's reactivity system, so it is safe to
 * call it on every route tick without triggering unnecessary re-renders.
 */
// True array equality by numeric content — avoids spurious reactive updates
// when applyRouteToStores writes the same IDs that handleSelect* already set.
function _sameNumIds(a, b) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (Number(a[i]) !== Number(b[i])) return false;
  }
  return true;
}

function applyRouteToStores() {
  const { name, params, query } = route;

  if (name === "all-pictures") {
    selectionStore.selectedFolderFilter = null;
    selectionStore.selectedSet = null;
    if (selectionStore.selectedSetIds.length > 0)
      selectionStore.selectedSetIds = [];
    if (String(selectionStore.selectedCharacter) !== String(ALL_PICTURES_ID))
      selectionStore.selectedCharacter = ALL_PICTURES_ID;
    if (selectionStore.selectedCharacterIds.length > 0)
      selectionStore.selectedCharacterIds = [];
    selectionStore.lastSelectedCharacterLabel = "All Pictures";
    projectStore.projectViewMode = "global";
    projectStore.selectedProjectId = null;
  } else if (name === "character") {
    const charIdRaw = params.id || ALL_PICTURES_ID;
    const charIdNum = Number(charIdRaw);
    const charId = Number.isFinite(charIdNum) ? charIdNum : String(charIdRaw);
    const idsRaw = query.ids;
    const modeRaw = query.mode;
    const ids = idsRaw
      ? String(idsRaw)
          .split(",")
          .map(Number)
          .filter((id) => Number.isFinite(id) && id > 0)
      : [];
    selectionStore.selectedFolderFilter = null;
    selectionStore.selectedSet = null;
    if (selectionStore.selectedSetIds.length > 0)
      selectionStore.selectedSetIds = [];
    if (String(selectionStore.selectedCharacter) !== String(charId))
      selectionStore.selectedCharacter = charId;
    if (!_sameNumIds(selectionStore.selectedCharacterIds, ids))
      selectionStore.selectedCharacterIds = ids;
    if (ids.length > 1 && modeRaw) {
      selectionStore.characterMultiMode = String(modeRaw);
    }
    if (charId === ALL_PICTURES_ID) {
      selectionStore.lastSelectedCharacterLabel = "All Pictures";
    } else if (charId === UNASSIGNED_PICTURES_ID) {
      selectionStore.lastSelectedCharacterLabel = "Unassigned Pictures";
    }
    projectStore.projectViewMode = "global";
    projectStore.selectedProjectId = null;
  } else if (name === "scrapheap") {
    selectionStore.selectedFolderFilter = null;
    selectionStore.selectedSet = null;
    if (selectionStore.selectedSetIds.length > 0)
      selectionStore.selectedSetIds = [];
    if (
      String(selectionStore.selectedCharacter) !== String(SCRAPHEAP_PICTURES_ID)
    )
      selectionStore.selectedCharacter = SCRAPHEAP_PICTURES_ID;
    if (selectionStore.selectedCharacterIds.length > 0)
      selectionStore.selectedCharacterIds = [];
    selectionStore.lastSelectedCharacterLabel = "Scrapheap";
    projectStore.projectViewMode = "global";
    projectStore.selectedProjectId = null;
  } else if (name === "set") {
    const primaryId = Number(params.id);
    const idsRaw = query.ids;
    const modeRaw = query.mode;
    const baseRaw = query.base;
    const ids = idsRaw
      ? String(idsRaw)
          .split(",")
          .map(Number)
          .filter((id) => Number.isFinite(id) && id > 0)
      : Number.isFinite(primaryId) && primaryId > 0
        ? [primaryId]
        : [];
    selectionStore.selectedFolderFilter = null;
    selectionStore.selectedCharacter = null;
    if (selectionStore.selectedCharacterIds.length > 0)
      selectionStore.selectedCharacterIds = [];
    const nextSet = ids[0] ?? null;
    if (selectionStore.selectedSet !== nextSet)
      selectionStore.selectedSet = nextSet;
    if (!_sameNumIds(selectionStore.selectedSetIds, ids))
      selectionStore.selectedSetIds = ids;
    if (ids.length > 1 && modeRaw) {
      selectionStore.setMultiMode = String(modeRaw);
    }
    if (ids.length > 1 && baseRaw) {
      const baseId = Number(baseRaw);
      if (Number.isFinite(baseId) && baseId > 0) {
        selectionStore.setDifferenceBaseId = baseId;
      }
    }
    projectStore.projectViewMode = "global";
    projectStore.selectedProjectId = null;
  } else if (name === "project") {
    const projectId = Number(params.id);
    projectStore.projectViewMode = "project";
    projectStore.selectedProjectId =
      Number.isFinite(projectId) && projectId > 0 ? projectId : null;
    if (String(selectionStore.selectedCharacter) !== String(ALL_PICTURES_ID))
      selectionStore.selectedCharacter = ALL_PICTURES_ID;
    if (selectionStore.selectedCharacterIds.length > 0)
      selectionStore.selectedCharacterIds = [];
    selectionStore.selectedSet = null;
    if (selectionStore.selectedSetIds.length > 0)
      selectionStore.selectedSetIds = [];
    selectionStore.selectedFolderFilter = null;
    selectionStore.lastSelectedCharacterLabel = "All Pictures";
  } else if (name === "project-character") {
    const projectId = Number(params.projectId);
    const charIdRaw = params.id || ALL_PICTURES_ID;
    const charIdNum = Number(charIdRaw);
    const charId = Number.isFinite(charIdNum) ? charIdNum : String(charIdRaw);
    projectStore.projectViewMode = "project";
    projectStore.selectedProjectId =
      Number.isFinite(projectId) && projectId > 0 ? projectId : null;
    selectionStore.selectedFolderFilter = null;
    selectionStore.selectedSet = null;
    if (selectionStore.selectedSetIds.length > 0)
      selectionStore.selectedSetIds = [];
    if (String(selectionStore.selectedCharacter) !== String(charId))
      selectionStore.selectedCharacter = charId;
    if (selectionStore.selectedCharacterIds.length > 0)
      selectionStore.selectedCharacterIds = [];
  } else if (name === "project-set") {
    const projectId = Number(params.projectId);
    const setId = Number(params.id);
    projectStore.projectViewMode = "project";
    projectStore.selectedProjectId =
      Number.isFinite(projectId) && projectId > 0 ? projectId : null;
    selectionStore.selectedFolderFilter = null;
    selectionStore.selectedCharacter = null;
    if (selectionStore.selectedCharacterIds.length > 0)
      selectionStore.selectedCharacterIds = [];
    const nextSet = Number.isFinite(setId) && setId > 0 ? setId : null;
    if (selectionStore.selectedSet !== nextSet)
      selectionStore.selectedSet = nextSet;
    if (!_sameNumIds(selectionStore.selectedSetIds, nextSet ? [nextSet] : []))
      selectionStore.selectedSetIds = nextSet ? [nextSet] : [];
    selectionStore.lastSelectedCharacterLabel = "All Pictures";
  } else if (name === "ref-folder" || name === "import-folder") {
    // Folder routes — clear all other selection state. The sidebar will emit
    // select-folder with the full payload once it loads the folder data.
    projectStore.projectViewMode = "global";
    projectStore.selectedProjectId = null;
    if (String(selectionStore.selectedCharacter) !== String(ALL_PICTURES_ID))
      selectionStore.selectedCharacter = ALL_PICTURES_ID;
    if (selectionStore.selectedCharacterIds.length > 0)
      selectionStore.selectedCharacterIds = [];
    selectionStore.selectedSet = null;
    if (selectionStore.selectedSetIds.length > 0)
      selectionStore.selectedSetIds = [];
    selectionStore.lastSelectedCharacterLabel = "All Pictures";
  }
}

// Sync route → stores on every navigation (and immediately on mount for deep-linking).
watch(route, applyRouteToStores, { immediate: true, deep: true });

async function handleUpdateSearchQuery(value) {
  const nextQuery = typeof value === "string" ? value.trim() : "";
  searchStore.searchInput = nextQuery;
  searchStore.searchQuery = nextQuery;
  searchStore.addToSearchHistory(nextQuery);
}

function handleUpdateProjectViewMode(mode) {
  projectStore.projectViewMode = mode;
  if (mode === "global") {
    pushAppRoute({ name: "all-pictures" });
  } else if (mode === "project" && projectStore.selectedProjectId != null) {
    pushAppRoute({
      name: "project",
      params: { id: String(projectStore.selectedProjectId) },
    });
  }
}

function handleUpdateSelectedProjectId(id) {
  projectStore.selectedProjectId = id;
  if (projectStore.projectViewMode === "project" && id != null) {
    pushAppRoute({ name: "project", params: { id: String(id) } });
  }
}

async function handleUpdateSelectedSort({ sort, descending }) {
  sortStore.selectedSort = sort;
  sortStore.selectedDescending = descending;
  closeSidebarIfMobile();
}

function handleUpdateSortOptions(options) {
  sortStore.sortOptions = Array.isArray(options) ? options : [];
}

function handleUpdateStackThreshold(value) {
  sortStore.stackThreshold = value;
}

function handleStackStatsUpdate(payload) {
  const expanded = Number(payload?.expanded ?? 0);
  const total = Number(payload?.total ?? 0);
  gridStore.expandedStackCount = Number.isFinite(expanded)
    ? Math.max(0, expanded)
    : 0;
  gridStore.totalStackCount = Number.isFinite(total) ? Math.max(0, total) : 0;
}

function handleExpandAllStacks() {
  nextTick(() => {
    gridContainer.value?.expandAllStacks?.();
  });
}

function handleCollapseAllStacks() {
  nextTick(() => {
    gridContainer.value?.collapseAllStacks?.();
  });
}

function handleComfyuiRunGrid(payload) {
  gridContainer.value?.runComfyuiOnGridImages(payload);
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
    await apiClient.patch("/users/me/config", { check_for_updates: value });
  } catch (e) {
    console.error("Failed to save check_for_updates preference:", e);
  }
}

function handleUpdateSidebarThumbnailSize(value) {
  const nextValue = Number(value);
  if (!Number.isFinite(nextValue)) return;
  userPrefsStore.sidebarThumbnailSize = nextValue;
}

function handleColumnsEnd() {
  if (columnsMenuCloseTimeout) {
    clearTimeout(columnsMenuCloseTimeout);
  }
  columnsMenuCloseTimeout = setTimeout(() => {
    columnsMenuOpen.value = false;
    columnsMenuCloseTimeout = null;
  }, COLUMNS_MENU_CLOSE_DELAY_MS);
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
    const res = await apiClient.get("/users/me/config");
    const sortValue = res.data.sort_order ?? res.data.sort;
    if (typeof sortValue === "string" && sortValue) {
      sortStore.selectedSort = sortValue;
    }
    if (typeof res.data.show_keyboard_hint === "boolean")
      userPrefsStore.showKeyboardHint = res.data.show_keyboard_hint;
    if (typeof res.data.show_face_bboxes === "boolean") {
      gridStore.showFaceBboxes = res.data.show_face_bboxes;
    }
    if (typeof res.data.show_problem_icon === "boolean") {
      gridStore.showProblemIcon = res.data.show_problem_icon;
    }
    if (typeof res.data.expand_all_stacks === "boolean") {
      gridStore.showStacks = res.data.expand_all_stacks;
    } else if (typeof res.data.show_stacks === "boolean") {
      gridStore.showStacks = res.data.show_stacks;
    }
    if (typeof res.data.compact_mode === "boolean") {
      gridStore.compactMode = res.data.compact_mode;
    }
    if (typeof res.data.date_format === "string" && res.data.date_format) {
      userPrefsStore.dateFormat = res.data.date_format;
    }
    if (typeof res.data.theme_mode === "string" && res.data.theme_mode) {
      userPrefsStore.themeMode = res.data.theme_mode;
    }
    if (typeof res.data.descending === "boolean") {
      sortStore.selectedDescending = res.data.descending;
    }
    if (typeof res.data.columns === "number") {
      gridStore.columns = res.data.columns;
    }
    if (typeof res.data.sidebar_thumbnail_size === "number") {
      userPrefsStore.sidebarThumbnailSize = res.data.sidebar_thumbnail_size;
    }
    if (res.data.stack_strictness != null) {
      sortStore.stackThreshold = String(res.data.stack_strictness);
    }
    config.sort_order = sortValue || sortStore.selectedSort;
    config.descending = sortStore.selectedDescending;
    config.columns = gridStore.columns;
    config.sidebar_thumbnail_size = userPrefsStore.sidebarThumbnailSize;
    config.show_stars =
      typeof res.data.show_stars === "boolean"
        ? res.data.show_stars
        : gridStore.showStars;
    config.show_face_bboxes =
      typeof res.data.show_face_bboxes === "boolean"
        ? res.data.show_face_bboxes
        : gridStore.showFaceBboxes;
    config.show_problem_icon =
      typeof res.data.show_problem_icon === "boolean"
        ? res.data.show_problem_icon
        : gridStore.showProblemIcon;
    config.expand_all_stacks =
      typeof res.data.expand_all_stacks === "boolean"
        ? res.data.expand_all_stacks
        : typeof res.data.show_stacks === "boolean"
          ? res.data.show_stacks
          : gridStore.showStacks;
    config.compact_mode =
      typeof res.data.compact_mode === "boolean"
        ? res.data.compact_mode
        : gridStore.compactMode;
    config.date_format = userPrefsStore.dateFormat;
    config.theme_mode = userPrefsStore.themeMode;
    config.stack_strictness =
      res.data.stack_strictness != null
        ? res.data.stack_strictness
        : config.stack_strictness;
    const similarityValue =
      res.data.similarity_character ?? res.data.selected_similarity_character;
    sortStore.selectedSimilarityCharacter =
      similarityValue ?? sortStore.selectedSimilarityCharacter ?? null;
    const newHiddenTags = Array.isArray(res.data.hidden_tags)
      ? res.data.hidden_tags
      : [];
    if (
      userPrefsStore.hiddenTags.length !== newHiddenTags.length ||
      userPrefsStore.hiddenTags.some((tag, i) => tag !== newHiddenTags[i])
    ) {
      userPrefsStore.hiddenTags = newHiddenTags;
    }
    userPrefsStore.applyTagFilter = Boolean(res.data.apply_tag_filter);
    const rawPt = res.data.smart_score_penalised_tags;
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
      date_format: userPrefsStore.dateFormat,
      theme_mode: userPrefsStore.themeMode,
      similarity_character: sortStore.selectedSimilarityCharacter,
      stack_strictness:
        res.data.stack_strictness != null
          ? Number(res.data.stack_strictness)
          : null,
      hidden_tags: userPrefsStore.hiddenTags,
      apply_tag_filter: userPrefsStore.applyTagFilter,
    };
    filterStore.comfyuiConfigured = Boolean(res.data?.comfyui_url);
    if (typeof res.data?.public_url === "string" && res.data.public_url) {
      userPrefsStore.publicUrl = res.data.public_url;
    }
    userPrefsStore.embedWatermark = Boolean(res.data?.embed_watermark);
    const cfu = res.data?.check_for_updates;
    userPrefsStore.checkForUpdates =
      cfu === true ? true : cfu === false ? false : null;
    if (userPrefsStore.checkForUpdates === null) {
      updateCheckDialogOpen.value = true;
    }
  } catch (e) {
    console.error("Failed to fetch /users/me/config:", e);
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
  if (gridStore.columns) patch.columns = gridStore.columns;
  if (userPrefsStore.sidebarThumbnailSize) {
    patch.sidebar_thumbnail_size = userPrefsStore.sidebarThumbnailSize;
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
    const response = await apiClient.patch("/users/me/config", changed);
    const updatedConfig = await response.data;
    configSnapshot.value = { ...snapshot, ...changed };
  } catch (e) {
    console.error("Error patching /users/me/config:", e);
  }
}

function handleGlobalKeydown(e) {
  const tag = document.activeElement?.tagName?.toLowerCase();
  const isEditable =
    tag === "input" ||
    tag === "textarea" ||
    document.activeElement?.isContentEditable;

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
      openSearchOverlay();
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

function handleImagesMovedToSet({ imageIds }) {
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

function handleFacesAssignedToCharacter({ characterId, faceIds }) {
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
  });
  exportStore.exportMenuOpen = false;
}

// --- Search Overlay ---

function openSearchOverlay() {
  searchStore.searchOverlayVisible = true;
}

function closeSearchOverlay() {
  searchStore.searchOverlayVisible = false;
}

function handleClearSearch() {
  searchStore.searchQuery = "";
  searchStore.searchInput = "";
  searchStore.isSearchHistoryOpen = false;
  gridStore.refreshGridVersion();
}

function blurSearchInput() {
  toolbarRef.value?.blurSearchInput?.();
}

function blurSearch(event) {
  if (event && event.target) {
    event.target.blur();
  }
  blurSearchInput();
}

function addToSearchHistory(query) {
  searchStore.addToSearchHistory(query);
}

function applySearchHistory(query) {
  searchStore.searchInput = query;
  searchStore.commitSearch();
  searchStore.isSearchHistoryOpen = false;
  nextTick(() => {
    blurSearchInput();
  });
}

function clearSearchHistory() {
  searchStore.clearSearchHistory();
}

function commitSearch() {
  searchStore.commitSearch();
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
  ([face, problem, stacks]) => {},
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
  () => gridStore.columns,
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
    wsStore.pendingExternalImportCount = 0;
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
    <div class="app-viewport">
      <div class="file-manager">
        <div
          class="sidebar-shell"
          :class="{ open: sidebarStore.sidebarVisible }"
        >
          <SideBar
            ref="sidebarRef"
            :docked="sidebarStore.sidebarDocked"
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
            @update:project-view-mode="handleUpdateProjectViewMode"
            @update:selected-project-id="handleUpdateSelectedProjectId"
            @select-character="handleSelectCharacter"
            @select-set="handleSelectSet"
            @select-folder="handleSelectFolder"
            @update:folder-scanning="folderScanning = $event"
            @images-assigned-to-character="handleImagesAssignedToCharacter"
            @images-moved="handleImagesMovedToSet"
            @faces-assigned-to-character="handleFacesAssignedToCharacter"
            @toggle-dock="toggleDock"
            @update:selected-sort="handleUpdateSelectedSort"
            @update:similarity-character="handleUpdateSimilarityCharacter"
            @open-import-dialog="openImportDialog"
            @update:set-error="error = $event"
            @update:set-loading="loading = $event"
            @update:check-for-updates="handleUpdateCheckForUpdates"
          />
        </div>
        <Transition name="backdrop-fade">
          <div
            v-if="
              sidebarStore.sidebarVisible && sidebarStore.sidebarForcedHidden
            "
            class="sidebar-backdrop"
            @click="sidebarStore.sidebarVisible = false"
          ></div>
        </Transition>

        <!-- Update-check consent dialog -->
        <v-dialog v-model="updateCheckDialogOpen" max-width="420" persistent>
          <v-card class="update-check-dialog">
            <v-card-title class="update-check-title"
              >Check for updates automatically?</v-card-title
            >
            <v-card-text class="update-check-body">
              When enabled, PixlStash checks for a newer version once per day.
              <br />
              <span class="update-check-note"
                >The request sends your installed version and install type (e.g.
                pip or docker) anonymously. No IP addresses are stored.</span
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
                >No</v-btn
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
                >Yes</v-btn
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
            style="margin-top: 0; flex-direction: row; align-items: stretch"
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
              <ImageGrid
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
                :wsPluginProgress="wsStore.wsPluginProgress"
                :mediaTypeFilter="filterStore.mediaTypeFilter"
                :comfyuiModelFilter="filterStore.comfyuiModelFilter"
                :comfyuiLoraFilter="filterStore.comfyuiLoraFilter"
                :comfyuiConfigured="filterStore.comfyuiConfigured"
                :minScoreFilter="filterStore.minScoreFilter"
                :maxScoreFilter="filterStore.maxScoreFilter"
                :smartScoreBucketFilter="filterStore.smartScoreBucketFilter"
                :resolutionBucketFilter="filterStore.resolutionBucketFilter"
                :tagFilter="filterStore.tagFilter"
                :tagRejectedFilter="filterStore.tagRejectedFilter"
                :tagConfidenceAboveFilter="filterStore.tagConfidenceAboveFilter"
                :tagConfidenceBelowFilter="filterStore.tagConfidenceBelowFilter"
                :faceBboxFilter="filterStore.faceBboxFilter"
                :sharedOnlyFilter="filterStore.sharedOnlyFilter"
                :unassignedOnlyFilter="filterStore.unassignedOnlyFilter"
                :showFaceBboxes="gridStore.showFaceBboxes"
                :showProblemIcon="gridStore.showProblemIcon"
                :penalisedTagWeights="userPrefsStore.penalisedTagWeights"
                :showStacks="gridStore.showStacks"
                :compactMode="gridStore.compactMode"
                :themeMode="userPrefsStore.themeMode"
                :dateFormat="userPrefsStore.dateFormat"
                :hiddenTags="userPrefsStore.hiddenTags"
                :applyTagFilter="userPrefsStore.applyTagFilter"
                :allPicturesId="ALL_PICTURES_ID"
                :unassignedPicturesId="UNASSIGNED_PICTURES_ID"
                :scrapheapPicturesId="SCRAPHEAP_PICTURES_ID"
                :projectViewMode="projectStore.projectViewMode"
                :selectedProjectId="projectStore.selectedProjectId"
                :characterProjectIds="projectStore.characterProjectIds"
                :setProjectIds="projectStore.setProjectIds"
                :setDifferenceBaseId="selectionStore.setDifferenceBaseId"
                :selectedSetNames="selectionStore.selectedSetNames"
                :referenceFolderIdFilter="
                  selectionStore.selectedFolderFilter?.referenceFolderId ?? null
                "
                :filePathPrefixFilter="
                  selectionStore.selectedFolderFilter?.pathPrefix ?? null
                "
                :importSourceFolderFilter="
                  selectionStore.selectedFolderFilter?.importSourceFolder ??
                  null
                "
                :publicUrl="userPrefsStore.publicUrl"
                :embedWatermark="userPrefsStore.embedWatermark"
                :folderScanning="folderScanning"
                :columns="gridStore.columns"
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
                @load-pending-imports="loadPendingExternalImports"
                @update:visible-range-label="
                  gridStore.visibleRangeLabel = $event
                "
                @open-settings="openSettingsDialog"
                @open-import="openImportDialog"
                @confirm-export-zip="confirmExportZip"
              />
            </div>
            <StatsSidebar
              :open="sidebarStore.statsOpen"
              :backendUrl="BACKEND_URL"
              :selectedCharacter="selectionStore.selectedCharacter"
              :selectedCharacterIds="selectionStore.selectedCharacterIds"
              :characterMode="selectionStore.characterMultiMode"
              :selectedSet="selectionStore.selectedSet"
              :selectedSetIds="selectionStore.selectedSetIds"
              :setMode="selectionStore.setMultiMode"
              :setDifferenceBaseId="selectionStore.setDifferenceBaseId"
              :projectViewMode="projectStore.projectViewMode"
              :selectedProjectId="projectStore.selectedProjectId"
              :tagFilter="filterStore.tagFilter"
              :tagRejectedFilter="filterStore.tagRejectedFilter"
              :mediaTypeFilter="filterStore.mediaTypeFilter"
              :minScoreFilter="filterStore.minScoreFilter"
              :maxScoreFilter="filterStore.maxScoreFilter"
              :smartScoreBucketFilter="filterStore.smartScoreBucketFilter"
              :resolutionBucketFilter="filterStore.resolutionBucketFilter"
              :faceBboxFilter="filterStore.faceBboxFilter"
              :sharedOnlyFilter="filterStore.sharedOnlyFilter"
              :unassignedOnlyFilter="filterStore.unassignedOnlyFilter"
              :filePathPrefixFilter="
                selectionStore.selectedFolderFilter?.pathPrefix ?? null
              "
              :importSourceFolderFilter="
                selectionStore.selectedFolderFilter?.importSourceFolder ?? null
              "
              :allPicturesId="ALL_PICTURES_ID"
              :unassignedPicturesId="UNASSIGNED_PICTURES_ID"
              :scrapheapPicturesId="SCRAPHEAP_PICTURES_ID"
              :penalisedTagWeights="userPrefsStore.penalisedTagWeights"
              :tagConfidenceAboveFilter="filterStore.tagConfidenceAboveFilter"
              :tagConfidenceBelowFilter="filterStore.tagConfidenceBelowFilter"
              :wsTagUpdate="wsStore.wsTagUpdate"
              @filter-tag="
                (tag) => {
                  if (filterStore.tagFilter.includes(tag))
                    filterStore.tagFilter = filterStore.tagFilter.filter(
                      (t) => t !== tag,
                    );
                  else filterStore.tagFilter = [...filterStore.tagFilter, tag];
                }
              "
              @filter-tags="
                (tags) => {
                  const allPresent = tags.every((t) =>
                    filterStore.tagFilter.includes(t),
                  );
                  if (allPresent)
                    filterStore.tagFilter = filterStore.tagFilter.filter(
                      (t) => !tags.includes(t),
                    );
                  else
                    filterStore.tagFilter = [
                      ...new Set([...filterStore.tagFilter, ...tags]),
                    ];
                }
              "
              @filter-confidence-above="
                (entry) => {
                  if (filterStore.tagConfidenceAboveFilter.includes(entry))
                    filterStore.tagConfidenceAboveFilter =
                      filterStore.tagConfidenceAboveFilter.filter(
                        (e) => e !== entry,
                      );
                  else
                    filterStore.tagConfidenceAboveFilter = [
                      ...filterStore.tagConfidenceAboveFilter,
                      entry,
                    ];
                }
              "
              @clear-tag-filter="
                (tags) => {
                  filterStore.tagFilter = filterStore.tagFilter.filter(
                    (t) => !tags.includes(t),
                  );
                }
              "
              @clear-confidence-filter="
                (entries) => {
                  filterStore.tagConfidenceAboveFilter =
                    filterStore.tagConfidenceAboveFilter.filter(
                      (e) => !entries.includes(e),
                    );
                }
              "
              @update:minScoreFilter="(v) => (filterStore.minScoreFilter = v)"
              @update:maxScoreFilter="(v) => (filterStore.maxScoreFilter = v)"
              @update:smartScoreBucketFilter="
                (v) => (filterStore.smartScoreBucketFilter = v)
              "
              @update:resolutionBucketFilter="
                (v) => (filterStore.resolutionBucketFilter = v)
              "
              @toggle="
                sidebarStore.toggleStats();
                updateIsMobile();
              "
            />
          </div>
        </main>
      </div>
      <SearchOverlay
        v-if="searchStore.searchOverlayVisible"
        :modelValue="searchStore.searchQuery"
        :history="searchStore.searchHistory"
        @search="handleUpdateSearchQuery"
        @close="closeSearchOverlay"
        @clear-history="clearSearchHistory"
      />
    </div>
    <button
      v-show="userPrefsStore.showKeyboardHint"
      class="shortcuts-fab"
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
