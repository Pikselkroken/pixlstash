<script setup>
import {
  computed,
  defineAsyncComponent,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";
import { useTheme } from "vuetify";
import { rememberTheme } from "./utils/themeMemory";
import { storeToRefs } from "pinia";
import { useRoute, useRouter } from "vue-router";
import { useReviewRoute } from "./composables/useReviewRoute";
import { isReadOnly, sessionContext } from "./utils/apiClient";
import { getInstallId } from "./api/telemetry";
import { defaultInstallType } from "./utils/telemetryPayload";
import { useSelectionStore } from "./stores/useSelectionStore";
import { useFilterStore } from "./stores/useFilterStore";
import { useGridStore } from "./stores/useGridStore";
import { useSidebarStore } from "./stores/useSidebarStore";
import { useUserPrefsStore } from "./stores/useUserPrefsStore";
import { useFolderMappingStore } from "./stores/useFolderMappingStore";
import { useProjectStore } from "./stores/useProjectStore";
import { useWsStore } from "./stores/useWsStore";
import { useReviewSessionsStore } from "./stores/useReviewSessionsStore";
import { useSnapshotsStore } from "./stores/useSnapshotsStore";
import { useTasksStore } from "./stores/useTasksStore";
import { useOperationStore } from "./stores/useOperationStore";
import { useNoticeStore } from "./stores/useNoticeStore";
import {
  useLibrariesStore,
  useLibrarySwitchStore,
} from "./stores/useLibrariesStore";
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
import { useSearchBarSync } from "./composables/useSearchBarSync";
import { libraryDocumentTitle } from "./utils/libraryChrome";
import { markEnd, markStart } from "./utils/perfMarks";

import SideBar from "./components/panels/SideBar.vue";
import TitleBar from "./components/TitleBar.vue";
import PhotosImportDialog from "./components/io/PhotosImportDialog.vue";
import RestoreConfirmDialog from "./components/widgets/RestoreConfirmDialog.vue";
import TelemetryConsentDialog from "./components/dialogs/TelemetryConsentDialog.vue";
import ImageGrid from "./components/views/ImageGrid.vue";
import StatsSidebar from "./components/panels/StatsSidebar.vue";
import ThumbnailUpgradeBanner from "./components/panels/ThumbnailUpgradeBanner.vue";
import NoticeHost from "./components/widgets/NoticeHost.vue";
import ShortcutsDialog from "./components/widgets/ShortcutsDialog.vue";
import ConfirmDialog from "./components/widgets/ConfirmDialog.vue";
import LibrarySwitchOverlay from "./components/settings/LibrarySwitchOverlay.vue";
import { useFloatingBottomInset } from "./composables/useBottomAnchor";
import { toPx } from "./utils/floatingBottom.js";

// These surfaces are mutually exclusive with the primary grid (or explicitly
// opened on demand), so keep their heavier feature code out of app startup.
const DuplicateQueue = defineAsyncComponent(
  () => import("./components/views/DuplicateQueue.vue"),
);
const ModelShelf = defineAsyncComponent(
  () => import("./components/views/ModelShelf.vue"),
);
const LibraryInsights = defineAsyncComponent(
  () => import("./components/views/LibraryInsights.vue"),
);
const MovesReview = defineAsyncComponent(
  () => import("./components/views/MovesReview.vue"),
);
const ReviewSessionsOverlay = defineAsyncComponent(
  () => import("./components/views/ReviewSessionsOverlay.vue"),
);

// --- Stores ---
const selectionStore = useSelectionStore();
const filterStore = useFilterStore();
const gridStore = useGridStore();
const sidebarStore = useSidebarStore();
const userPrefsStore = useUserPrefsStore();
const projectStore = useProjectStore();
const wsStore = useWsStore();
const reviewSessionsStore = useReviewSessionsStore();
const snapshotsStore = useSnapshotsStore();
const tasksStore = useTasksStore();
const operationStore = useOperationStore();
const librariesStore = useLibrariesStore();
const librarySwitchStore = useLibrarySwitchStore();
const { activeLibrary } = storeToRefs(librariesStore);
const { overlayOpen: librarySwitchOverlayOpen } =
  storeToRefs(librarySwitchStore);
const noticeStore = useNoticeStore();
// Owns route → view resolution (the app's single route watcher). Route pushing
// stays here in App.vue; see stores/useViewStore.js.
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
const telemetryConsentOpen = ref(false);
const telemetryConsentIsUpgrade = ref(false);
// Initial setup comes first: the telemetry question waits until the library
// has been counted and any folder-mapping wizard (a pending mapping, or the
// first-run offer to import the pictures already in the folder) is closed.
const librarySettled = ref(false);
const mappingStore = useFolderMappingStore();
const telemetryConsentVisible = computed(
  () =>
    telemetryConsentOpen.value &&
    librarySettled.value &&
    !mappingStore.wizardOpen &&
    !mappingStore.pending,
);
const telemetryInstallIsNew = ref(false);
const photosDialogOpen = ref(false);
// Seeded from the marker the server substituted into the document, not from
// "pip" -- see `defaultInstallType` for why the value held here before
// `/version` answers is the one that actually gets reported. The fetch below
// still refreshes it for everything else that reads this ref.
const installType = ref(defaultInstallType());
const appVersion = ref("");
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
  isModelsView,
  isInsightsView,
  isMovesView,
  handleSelectModels,
  handleSelectInsights,
  handleSelectMoves,
  handleInsightAction,
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
  confirmExportZip,
  confirmExportFolder,
  handleClearSearch,
  handleResetToAll,
} = useAppEntityActions({
  gridContainer,
  refreshSidebar,
  onNavigated: () => closeSidebarIfMobile(),
  onTagFilterChanged: () => refreshSidebarDebounced(),
});

useSearchBarSync();

useGlobalKeydown({ gridContainer, sidebarRef, shortcutsDialogOpen });
useWindowFileImport({ sidebarRef });

const {
  handleViewProject,
  handleStackStatsUpdate,
  handleUpdateCheckForUpdates,
  handleEmptyScrapheapFromSidebar,
  handleSuggestPicturesForCharacter,
  focusTasksTabPanel,
} = useAppSettingsHandlers({
  gridContainer,
  statsSidebarRef,
  pushAppRoute,
});

async function handleTelemetryDecision(patch) {
  const saved = await userPrefsStore.saveTelemetry(patch);
  if (saved) {
    telemetryConsentOpen.value = false;
    return;
  }
  noticeStore.error(
    "Couldn’t save your privacy choices. The dialog is still open so you can retry.",
    { key: "telemetry-consent-save" },
  );
}

const { fetchConfig } = useAppConfig({
  onThumbnailSizeChanged: () => updateMaxColumns(),
  onTelemetryConsentRequired: async ({ isUpgrade }) => {
    telemetryConsentIsUpgrade.value = isUpgrade;
    try {
      const identity = await getInstallId();
      telemetryInstallIsNew.value = identity?.is_new_install === true;
    } catch (e) {
      telemetryInstallIsNew.value = false;
      console.error("Failed to read install classification:", e);
    }
    nextTick(() => {
      telemetryConsentOpen.value = true;
    });
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

const activeCategoryLabel = computed(() => {
  if (selectionStore.selectedFolderFilter) {
    const folder = selectionStore.selectedFolderFilter.label || "Folder";
    // A folder filter and the unassigned view together are ONE destination:
    // "About your library" opens its unsorted-pile finding on exactly that
    // pair. Naming only the folder would head a grid holding 900 of a
    // folder's 1,000 pictures with the folder's own name, which reads as the
    // whole of it.
    return selectionStore.selectedCharacter === UNASSIGNED_PICTURES_ID
      ? `Unassigned in ${folder}`
      : folder;
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

const activeLibraryName = computed(() =>
  isReadOnly.value ? "" : (activeLibrary.value?.name ?? ""),
);

watch(
  activeLibraryName,
  (name) => {
    document.title = libraryDocumentTitle(name, isReadOnly.value);
  },
  { immediate: true },
);

function onRestoreConfirmed() {
  gridStore.wsUpdateKey = Date.now();
  gridStore.refreshGridVersion();
  refreshSidebar();
}

/**
 * Open the settings dialog. `tab` deep-links to a nav entry (e.g. "scrapheap"
 * from the scrapheap header's "change" link); omitted callers land on Appearance.
 * @param {string} [tab]
 */
function openSettingsDialog(tab = "") {
  sidebarRef.value?.openSettingsDialog?.(typeof tab === "string" ? tab : "");
}

/** The empty library's "Choose a folder…" - a reference folder, read in place.
 *
 * Not the add-folder type chooser: its other option is an import folder, which
 * copies files in, and the button that reaches this promises the opposite.
 */
function openAddReferenceFolder() {
  sidebarRef.value?.openReferenceFolderEditor?.();
}

/** The library is empty: let the sidebar ask whether its folder is. */
function offerLoosePictures() {
  return sidebarRef.value?.offerLoosePictures?.();
}

/** The first count is in; an empty library gets its import offer first. */
async function onLibraryLoaded({ empty }) {
  if (empty) await offerLoosePictures();
  librarySettled.value = true;
}

// ── Notice surface placement (notice-surface.md §2.2) ───────────────────────
// App.vue owns `--floating-bottom-h`: the height of the tallest bottom-anchored
// floating element currently visible inside the notice column's footprint, plus
// its gap. The elements themselves register through `useBottomAnchor` (the
// SelectionBar pill, and the grid breadcrumb below 600px), each reporting a
// MEASURED height from a ResizeObserver - the pill wraps and grows on coarse
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
  // `undefined` means "the caller did not say", and the answer is the project
  // being looked at - which is what both drop paths pass
  // (useWindowFileImport.js, useGridDragDrop.js) and what PhotosImportDialog is
  // handed as its default. Defaulted here rather than at each caller so a
  // caller that omits it cannot silently land pictures outside the project the
  // person is standing in. `null` still means "deliberately no project".
  const target =
    projectId === undefined
      ? (sidebarRef.value?.currentProjectId ?? null)
      : projectId;
  sidebarRef.value?.startLocalImport?.(files, target);
}

// undo affordance itself.
// Route -> stores: install the app's single route watcher (immediately on
// mount for deep-linking, then on every navigation). The parsing and the
// writes live in useViewStore; the route PUSHING lives in useAppNavigation.
useViewStore().startRouteSync(route, { watch });

// A navigation retires the live undo receipt (owner decision, 2026-07-29): the
// pill narrates something that happened on the view being left, and a receipt
// carried into the next view reads as a fresh event there. Ctrl+Z keeps working
// regardless - the receipt is narration, not the undo affordance itself.
watch(
  () => route.fullPath,
  (next, prev) => {
    if (prev !== undefined && next !== prev) operationStore.dismissReceipt();
  },
);

// Stateless sidebar tabs: switching the Global ↔ Project mode (or the
// project picker) must not navigate or change the grid - the route is the
// single source of truth. These handlers therefore only mirror the value
// into the store (used for sidebar scoping); they never push a route.
// Grid navigation happens via explicit entry clicks (handleViewProject,
// handleSelectCharacter, handleSelectSet, handleSelectFolder).
function resolveThemeName(mode) {
  return mode === "dark" ? "pixlStashDark" : "pixlStashLight";
}

watch(
  () => userPrefsStore.themeMode,
  (value) => {
    theme.global.name.value = resolveThemeName(value);
    // Remember it for the next launch's first paint, which happens long before
    // the config that decided this one can be asked for.
    rememberTheme(value === "dark" ? "dark" : "light");
  },
  { immediate: true },
);

// --- Lifecycle ---
onMounted(async () => {
  markStart("pixlstash:app-mounted-to-interactive");
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
      if (typeof data?.version === "string") {
        appVersion.value = data.version;
      }
      if (typeof data?.docker_variant === "string") {
        dockerVariant.value = data.docker_variant;
      }
    })
    .catch(() => {});
  // fetchConfig() (GET /users/me/config) and librariesStore.refresh() (GET
  // /libraries) are independent reads with no shared state - each already
  // handles its own errors internally. Awaiting them one after another used
  // to serialize two network round trips in front of refreshSidebar() and
  // connectUpdatesSocket() below for no reason; fire them and move on so the
  // sidebar/grid/WebSocket start immediately instead of queuing behind them.
  fetchConfig();
  // Snapshots are owner-only (full unscoped access); READ / share sessions
  // would 403 on every fetch otherwise.
  if (!isReadOnly.value) {
    librariesStore.refresh();
    snapshotsStore.fetchSnapshots();
    // Seed the undo stack so the toolbar control is correctly enabled on the
    // first frame. This read establishes the "already seen" watermark, so the
    // history it returns cannot pop a receipt for something that happened
    // before the tab existed.
    operationStore.refresh({ narrate: false });
  }
  // Select the scoped resource when a share token is active. This normalises
  // what is SELECTED only; what the grid is scoped BY is
  // `useViewStore.scopeProjectToSession`, which runs on every route tick rather
  // than once here (issue #717 - a share link carries whatever pathname the
  // owner minted it from, and a mount-time write loses to the next navigation).
  // The project branch below therefore agrees with that store rather than
  // competing with it, and still covers the routes it parses no view from.
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
  // Everything above that the app shell needs to be usable (sidebar refresh,
  // WebSocket connect, layout measurement) has now been kicked off; the
  // remaining config/library/undo-history fetches finish in the background
  // and update reactively when they land.
  markEnd("pixlstash:app-mounted-to-interactive");
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
  <v-app :inert="librarySwitchOverlayOpen">
    <div ref="appViewportEl" class="app-viewport">
      <TitleBar
        :install-type="installType"
        :check-for-updates="userPrefsStore.checkForUpdates"
        :active-library-name="activeLibraryName"
        @open-libraries="openSettingsDialog('libraries')"
      />
      <!-- App-level status strip: spans the whole shell above BOTH rails and the
           grid. Thumbnail regeneration repaints grid tiles, sidebar thumbnails
           and the Tasks row alike, so it is not a property of the grid column;
           mounting it inside `.main-area` used to push the stats rail down while
           leaving the left rail alone. -->
      <ThumbnailUpgradeBanner
        :inert="librarySwitchOverlayOpen"
        @view-progress="focusTasksTabPanel"
      />
      <div
        class="file-manager"
        :inert="librarySwitchOverlayOpen"
        :aria-hidden="librarySwitchOverlayOpen ? 'true' : undefined"
      >
        <!-- Auto-hide (unpinned): a thin strip at the left edge reveals the
             sidebar overlay on hover (or tap, on touch). -->
        <div
          v-if="
            sidebarStore.sidebarOverlay &&
            !sidebarStore.sidebarVisible &&
            !sidebarStore.sidebarForcedHidden
          "
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
            :installType="installType"
            :dockerVariant="dockerVariant"
            @empty-scrapheap="handleEmptyScrapheapFromSidebar"
            @suggest-pictures-for-character="handleSuggestPicturesForCharacter"
            @view-project="handleViewProject"
            @select-character="handleSelectCharacter"
            @select-insights="handleSelectInsights"
            @select-moves="handleSelectMoves"
            @select-duplicates="handleSelectDuplicates"
            @select-models="handleSelectModels"
            @select-set="handleSelectSet"
            @select-folder="handleSelectFolder"
            @images-assigned-to-character="handleImagesAssignedToCharacter"
            @images-moved="handleImagesMoved"
            @faces-assigned-to-character="handleFacesAssignedToCharacter"
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

        <TelemetryConsentDialog
          :open="telemetryConsentVisible"
          :is-upgrade="telemetryConsentIsUpgrade"
          :update-checks-enabled="userPrefsStore.checkForUpdates === true"
          :version="appVersion"
          :install-type="installType"
          :is-new-install="telemetryInstallIsNew"
          @decide="handleTelemetryDecision"
        />
        <PhotosImportDialog
          v-model:open="photosDialogOpen"
          :default-project-id="sidebarRef?.currentProjectId ?? null"
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
              <!-- The model shelf lists files on this machine rather than
                   pictures in the library, so like Duplicates it replaces the
                   grid instead of floating over it, and the grid stays
                   unmounted while it is open. -->
              <ModelShelf
                v-else-if="isModelsView"
                @open-settings="openSettingsDialog"
              />
              <!-- "About your library" reads the library rather than showing
                   it, so like the two above it replaces the grid instead of
                   floating over it. `act` carries one finding's action: the
                   settings pane is a dialog rather than a route, so that one
                   kind lands here; every other kind is a navigation and
                   belongs to useAppNavigation. -->
              <LibraryInsights
                v-else-if="isInsightsView"
                @act="
                  (action) =>
                    action.kind === 'settings'
                      ? openSettingsDialog(action.tab)
                      : handleInsightAction(action)
                "
              />
              <!-- Moves is a destination for the same reason Insights is: it
                   reports on the queue rather than showing the library, so it
                   replaces the grid instead of floating over it. -->
              <MovesReview v-else-if="isMovesView" />
              <ImageGrid
                v-else
                ref="gridContainer"
                :activeCategoryLabel="activeCategoryLabel"
                @clear-search="handleClearSearch"
                @search-all="handleSearchAllPictures"
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
                @choose-folder="openAddReferenceFolder"
                @library-empty="offerLoosePictures"
                @library-loaded="onLibraryLoaded"
                @confirm-export-zip="confirmExportZip"
                @confirm-export-folder="confirmExportFolder"
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
        :inert="librarySwitchOverlayOpen"
        @close="reviewSessionsStore.overlayOpen = false"
      />
      <!-- The notice surface. LAST child of `.app-viewport` on purpose
           (notice-surface.md §8): its buttons then come last in DOM order, so a
           keyboard user reaches them after the page content, not before it. It
           is global - it renders over the lightbox, the review overlay and
           Settings - so it must not be nested inside the grid column. -->
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
      :disabled="librarySwitchOverlayOpen"
      @click="shortcutsDialogOpen = true"
    >
      <v-icon size="20">mdi-keyboard</v-icon><span>F1</span>
    </button>
    <ShortcutsDialog v-model="shortcutsDialogOpen" />
    <ConfirmDialog />
    <LibrarySwitchOverlay />
  </v-app>
</template>
<style src="./App.css"></style>
