<script setup>
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";
import { useTheme } from "vuetify";
import { useRoute, useRouter } from "vue-router";
import { useReviewRoute } from "./composables/useReviewRoute";
import { API_BASE_URL, isReadOnly, sessionContext } from "./utils/apiClient";
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
import { useOperationStore } from "./stores/useOperationStore";
import {
  ALL_PICTURES_ID,
  SCRAPHEAP_PICTURES_ID,
  UNASSIGNED_PICTURES_ID,
  useViewStore,
} from "./stores/useViewStore";
import { useAppConfig } from "./composables/useAppConfig";
import { useAppNavigation } from "./composables/useAppNavigation";
import { useGlobalKeydown } from "./composables/useGlobalKeydown";
import { useWindowFileImport } from "./composables/useWindowFileImport";
import { useAppSettingsHandlers } from "./composables/useAppSettingsHandlers";
import { useUpdatesSocket } from "./composables/useUpdatesSocket";
import { useSidebarRefresh } from "./composables/useSidebarRefresh";
import { useViewportLayout } from "./composables/useViewportLayout";
import { useAppEntityActions } from "./composables/useAppEntityActions";

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
import ShortcutsDialog from "./components/widgets/ShortcutsDialog.vue";
import { useFloatingBottomInset } from "./composables/useBottomAnchor";
import { toPx } from "./utils/floatingBottom.js";
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
const operationStore = useOperationStore();
// Owns route → view resolution (the app's single route watcher). Route pushing
// stays here in App.vue; see stores/useViewStore.js.
const viewStore = useViewStore();
// Keycap labels for the shortcuts dialog. The binding accepts Ctrl and Meta
// everywhere; only the hint is platform-specific.

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
// Loading the user's config and persisting UI options back lives in
// useAppConfig; App.vue only supplies the layout re-measure that a
// thumbnail-size change needs.
// Sidebar entry clicks and the route pushes that follow them. Reading the
// route back into the stores is useViewStore's job, not this one's.
const {
  isDuplicatesView,
  handleSelectCharacter,
  handleSelectSet,
  handleSelectFolder,
  handleSearchAllPictures,
  handleSelectDuplicates,
  pushAppRoute,
} = useAppNavigation({
  onClearSearch: () => handleClearSearch(),
  onNavigated: () => closeSidebarIfMobile(),
});

const {
  refreshSidebar,
  refreshSidebarDebounced,
  refreshSidebarPicturesDebounced,
} = useSidebarRefresh({ sidebarRef });

const { updateIsMobile, updateMaxColumns, closeSidebarIfMobile } =
  useViewportLayout({ mainAreaRef });

// The live-updates channel. App.vue owns its lifecycle - it connects on
// mount and disconnects on unmount - but the socket, its filter handshake and
// the realtime-sync wiring live in the composable.
const {
  connectUpdatesSocket,
  disconnectUpdatesSocket,
  sendUpdatesFilters,
  loadPendingExternalImports,
  loadSortChangedExternal,
  onFlagSortChanged,
} = useUpdatesSocket({
  gridContainer,
  refreshSidebar: (options) => refreshSidebar(options),
  refreshSidebarPicturesDebounced: (flash) =>
    refreshSidebarPicturesDebounced(flash),
});

const {
  handleImagesAssignedToCharacter,
  handleImagesMoved,
  handleFacesAssignedToCharacter,
  refreshExportCount,
  confirmExportZip,
  handleClearSearch,
  handleResetToAll,
} = useAppEntityActions({
  gridContainer,
  refreshSidebar,
  onNavigated: () => closeSidebarIfMobile(),
});

useGlobalKeydown({ gridContainer, sidebarRef, shortcutsDialogOpen });
useWindowFileImport({ sidebarRef });

const {
  handleUpdateProjectViewMode,
  handleUpdateSelectedProjectId,
  handleViewProject,
  handleUpdateSelectedSort,
  handleUpdateSortOptions,
  handleStackStatsUpdate,
  handleUpdateSimilarityCharacter,
  handleUpdateSimilarityOptions,
  handleUpdateHiddenTags,
  handleUpdateApplyTagFilter,
  handleUpdateDateFormat,
  handleUpdateThemeMode,
  handleUpdateCheckForUpdates,
  handleUpdateSidebarThumbnailSize,
  handleEmptyScrapheapFromSidebar,
  handleSuggestPicturesForCharacter,
  focusTasksTabPanel,
  handleUpdateThumbnailMode,
  handleUpdateSidebarWidth,
} = useAppSettingsHandlers({
  gridContainer,
  statsSidebarRef,
  onNavigated: () => closeSidebarIfMobile(),
  pushAppRoute,
});

const { fetchConfig } = useAppConfig({
  onThumbnailSizeChanged: () => updateMaxColumns(),
  onUpdateCheckUndecided: () => {
    updateCheckDialogOpen.value = true;
  },
});

// --- Non-reactive internals ---
let mainAreaResizeObserver = null;
let columnsMenuCloseTimeout = null;
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

function onRestoreConfirmed() {
  gridStore.wsUpdateKey = Date.now();
  gridStore.refreshGridVersion();
  refreshSidebar();
}

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
function resolveThemeName(mode) {
  return mode === "dark" ? "pixlStashDark" : "pixlStashLight";
}

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
  if (mainAreaResizeObserver) {
    mainAreaResizeObserver.disconnect();
    mainAreaResizeObserver = null;
  }
  if (columnsMenuCloseTimeout) {
    clearTimeout(columnsMenuCloseTimeout);
    columnsMenuCloseTimeout = null;
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
                :backendUrl="BACKEND_URL"
                :activeCategoryLabel="activeCategoryLabel"
                :folderScanning="folderScanning"
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
                @import-started="wsStore.isUploadInProgress = true"
                @import-ended="wsStore.isUploadInProgress = false"
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
    <ShortcutsDialog v-model="shortcutsDialogOpen" />
  </v-app>
</template>
<style src="./App.css"></style>
