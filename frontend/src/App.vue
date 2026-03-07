<script setup>
import nlp from "compromise";
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
import { apiClient, API_BASE_URL } from "./utils/apiClient";

import SideBar from "./components/SideBar.vue";
import PhotosImportDialog from "./components/PhotosImportDialog.vue";
import ImageGrid from "./components/ImageGrid.vue";
import SearchOverlay from "./components/SearchOverlay.vue";
import Toolbar from "./components/Toolbar.vue";

const BACKEND_URL = API_BASE_URL;
const ALL_PICTURES_ID = "ALL";
const UNASSIGNED_PICTURES_ID = "UNASSIGNED";
const SCRAPHEAP_PICTURES_ID = "SCRAPHEAP";

// --- Template & Component Refs ---
const gridContainer = ref(null);
const selectedImageIds = ref([]);
let lastSelectedIndex = null;
const sidebarRef = ref(null);
const toolbarRef = ref(null);

const selectedCharacter = ref(ALL_PICTURES_ID);
const selectedSet = ref(null);
const selectedReferenceCharacter = ref(null);
const selectedSort = ref("");
const selectedDescending = ref(true);
const stackThreshold = ref(null);
const sortOptions = ref([]);
// --- Search & Filtering State ---
const searchQuery = ref("");
const searchInput = ref("");
const lastSelectedCharacterLabel = ref("All Pictures");
const lastSelectedSetLabel = ref("Picture Set");
const lastSelectedReferenceLabel = ref("Reference Pictures");
const searchHistory = ref([]);
const isSearchHistoryOpen = ref(false);
const MAX_SEARCH_HISTORY = 8;
const filteredSearchHistory = computed(() => {
  const needle = (searchInput.value || "").trim().toLowerCase();
  if (!needle) {
    return searchHistory.value;
  }
  return searchHistory.value.filter((item) =>
    item.toLowerCase().startsWith(needle),
  );
});
const showStars = ref(true);
const showFaceBboxes = ref(false);
const showFormat = ref(true);
const showResolution = ref(true);
const showProblemIcon = ref(true);
const showStacks = ref(true);
const expandedStackCount = ref(0);
const totalStackCount = ref(0);
const dateFormat = ref("locale");
const themeMode = ref("light");
const theme = useTheme();

const activeCategoryLabel = computed(() => {
  if (selectedSet.value) {
    return lastSelectedSetLabel.value || "Picture Set";
  }
  if (selectedReferenceCharacter.value) {
    return lastSelectedReferenceLabel.value || "Reference Pictures";
  }
  if (selectedCharacter.value === ALL_PICTURES_ID) return "All Pictures";
  if (selectedCharacter.value === UNASSIGNED_PICTURES_ID)
    return "Unassigned Pictures";
  if (selectedCharacter.value === SCRAPHEAP_PICTURES_ID) return "Scrapheap";
  if (selectedCharacter.value) {
    return lastSelectedCharacterLabel.value || "Category";
  }
  return "All Pictures";
});

const isAllPicturesActive = computed(
  () =>
    !selectedSet.value &&
    !selectedReferenceCharacter.value &&
    selectedCharacter.value === ALL_PICTURES_ID,
);

const thumbnailSize = ref(256);
const sidebarThumbnailSize = ref(48);
const photosDialogOpen = ref(false);
const columns = ref(4); // Default columns
const MIN_THUMBNAIL_SIZE = 96;
const MAX_THUMBNAIL_SIZE = 384;
const MIN_COLUMNS = 2;
const MAX_COLUMNS = 14;
const minColumns = ref(6);
const maxColumns = ref(12);
const mainAreaRef = ref(null);
let mainAreaResizeObserver = null;
const sidebarVisible = ref(true);
const isMobile = ref(false);
const MOBILE_BREAKPOINT = 1024;

// --- Media Type Filter State ---
const mediaTypeFilter = ref("all"); // 'all', 'images', 'videos'

const gridVersion = ref(0);
const wsUpdateKey = ref(0);
const wsTagUpdate = ref({ key: 0, pictureIds: [] });
const wsPluginProgress = ref({ key: 0, payload: null });
const columnsMenuOpen = ref(false);
const overlaysMenuOpen = ref(false);
const configLoaded = ref(false);
const COLUMNS_MENU_CLOSE_DELAY_MS = 300;
const SIDEBAR_REFRESH_DEBOUNCE_MS = 150;
let columnsMenuCloseTimeout = null;
let sidebarRefreshDebounceTimeout = null;
const updatesSocket = ref(null);
let updatesReconnectTimer = null;
const configLoading = ref(false);
const configApplying = ref(false);
const configSnapshot = ref({});
const hiddenTags = ref([]);
const applyTagFilter = ref(false);

function refreshGridVersion() {
  gridVersion.value++;
}

function buildUpdatesSocketUrl() {
  if (!BACKEND_URL) return "";
  const wsBase = BACKEND_URL.replace(/^http/i, "ws");
  return `${wsBase}/ws/updates`;
}

function shouldRefreshForPictureChange() {
  if (selectedSet.value) return false;
  const selectedChar = selectedCharacter.value;
  if (
    selectedChar &&
    selectedChar !== ALL_PICTURES_ID &&
    selectedChar !== UNASSIGNED_PICTURES_ID &&
    selectedChar !== SCRAPHEAP_PICTURES_ID
  ) {
    return false;
  }
  if ((searchQuery.value || "").trim()) return false;
  return true;
}

function sendUpdatesFilters() {
  if (!updatesSocket.value) return;
  if (updatesSocket.value.readyState !== WebSocket.OPEN) return;
  updatesSocket.value.send(
    JSON.stringify({
      type: "set_filters",
      selected_character: selectedCharacter.value,
      selected_set: selectedSet.value,
      search_query: searchQuery.value,
    }),
  );
}

function connectUpdatesSocket() {
  if (updatesSocket.value) return;
  const url = buildUpdatesSocketUrl();
  if (!url) return;
  const ws = new WebSocket(url);
  updatesSocket.value = ws;

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
      refreshSidebar({ flashCounts: true });
      const pictureIds = Array.isArray(payload.picture_ids)
        ? payload.picture_ids
        : [];
      if (
        pictureIds.length > 0 &&
        selectedSort.value === "PICTURE_STACKS" &&
        payload?.type !== "picture_imported"
      ) {
        const nextKey = (wsTagUpdate.value?.key || 0) + 1;
        wsTagUpdate.value = { key: nextKey, pictureIds };
        return;
      }
      if (
        shouldRefreshForPictureChange() ||
        payload?.type === "picture_imported"
      ) {
        wsUpdateKey.value = Date.now();
        refreshGridVersion();
      }
    } else if (payload?.type === "tags_changed") {
      const pictureIds = Array.isArray(payload.picture_ids)
        ? payload.picture_ids
        : [];
      const nextKey = (wsTagUpdate.value?.key || 0) + 1;
      wsTagUpdate.value = { key: nextKey, pictureIds };
    } else if (payload?.type === "plugin_progress") {
      wsPluginProgress.value = {
        key: Date.now(),
        payload,
      };
    }
  };

  ws.onclose = () => {
    updatesSocket.value = null;
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
  if (updatesSocket.value) {
    updatesSocket.value.close();
    updatesSocket.value = null;
  }
}

// --- Export Menu State ---
const exportMenuOpen = ref(false);
const exportType = ref("full");
const exportCaptionMode = ref("description");
const exportIncludeCharacterName = ref(true);
const exportResolution = ref("original");
const exportSelectedCount = ref(0);
const exportTotalCount = ref(0);
const exportCount = computed(() =>
  exportSelectedCount.value > 0
    ? exportSelectedCount.value
    : exportTotalCount.value,
);
const exportCaptionOptions = [
  { title: "No Captions", value: "none" },
  { title: "Description", value: "description" },
  { title: "Tags", value: "tags" },
];
const exportTypeOptions = [
  { title: "Full images", value: "full" },
  { title: "Face crops", value: "face" },
];
const exportResolutionOptions = [
  { title: "Original", value: "original" },
  { title: "Half Size", value: "half" },
  { title: "Quarter Size", value: "quarter" },
];
const exportTypeLocksCaptions = computed(() => exportType.value !== "full");

watch(
  exportType,
  (value) => {
    if (value !== "full") {
      exportCaptionMode.value = "tags";
      exportIncludeCharacterName.value = false;
    }
  },
  { immediate: true },
);

// --- Config Dialog State ---.
const config = reactive({
  sort: "",
  thumbnail: 256,
  sidebar_thumbnail_size: 64,
  show_stars: true,
  show_face_bboxes: false,
  show_format: true,
  show_resolution: true,
  show_problem_icon: true,
  expand_all_stacks: true,
  date_format: "locale",
  theme_mode: "light",
  stack_strictness: 0.92,
});

const loading = ref(false);
const error = ref(null);

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

function openSettingsDialog() {
  sidebarRef.value?.openSettingsDialog?.();
}

function openImportDialog() {
  photosDialogOpen.value = true;
}

async function handleLocalImport(files) {
  photosDialogOpen.value = false;
  await nextTick();
  sidebarRef.value?.startLocalImport?.(files);
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
  sidebarRef.value?.startLocalImport?.(droppedFiles);
}

function updateIsMobile() {
  if (typeof window !== "undefined") {
    isMobile.value = window.innerWidth <= MOBILE_BREAKPOINT;
  }
  updateMaxColumns();
}

function clampColumnsToBounds() {
  if (columns.value > maxColumns.value) {
    columns.value = maxColumns.value;
  }
  if (columns.value < minColumns.value) {
    columns.value = minColumns.value;
  }
}

function updateMaxColumns() {
  const width = mainAreaRef.value?.clientWidth ?? window.innerWidth ?? 0;
  if (!width) {
    minColumns.value = MIN_COLUMNS;
    maxColumns.value = MAX_COLUMNS;
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
  minColumns.value = Math.max(MIN_COLUMNS, computedMin);
  maxColumns.value = Math.min(MAX_COLUMNS, computedMax);
  clampColumnsToBounds();
}

function closeSidebarIfMobile() {
  if (isMobile.value) {
    sidebarVisible.value = false;
  }
}

function SelectionPayload(payload) {
  if (payload && typeof payload === "object") {
    return {
      id: payload.id ?? payload.value ?? null,
      label: payload.label ?? payload.name ?? null,
    };
  }
  return { id: payload ?? null, label: null };
}

function clearSearchForCategoryChange() {
  if ((searchQuery.value || "").trim() || (searchInput.value || "").trim()) {
    handleClearSearch();
  }
}

async function handleSelectCharacter(payload) {
  const { id: charId, label } = SelectionPayload(payload);
  clearSearchForCategoryChange();
  if (charId == null) {
    selectedCharacter.value = null;
    selectedReferenceCharacter.value = null;
    await nextTick();
    return;
  }
  if (label) {
    lastSelectedCharacterLabel.value = label;
  } else if (charId === ALL_PICTURES_ID) {
    lastSelectedCharacterLabel.value = "All Pictures";
  } else if (charId === UNASSIGNED_PICTURES_ID) {
    lastSelectedCharacterLabel.value = "Unassigned Pictures";
  } else if (charId === SCRAPHEAP_PICTURES_ID) {
    lastSelectedCharacterLabel.value = "Scrapheap";
  }
  selectedCharacter.value = charId;
  selectedSet.value = null; // Clear set selection
  selectedReferenceCharacter.value = null;
  await nextTick(); // Ensure reactivity propagates the change
  closeSidebarIfMobile();
}

async function handleSelectReferencePictures(payload) {
  const { id: charId, label } = SelectionPayload(payload);
  clearSearchForCategoryChange();
  if (charId == null) {
    selectedReferenceCharacter.value = null;
    await nextTick();
    return;
  }
  lastSelectedReferenceLabel.value =
    label || lastSelectedReferenceLabel.value || "Reference Pictures";
  selectedReferenceCharacter.value = charId;
  selectedCharacter.value = null;
  selectedSet.value = null;
  await nextTick();
  closeSidebarIfMobile();
}

async function handleSelectSet(payload) {
  const { id: setId, label } = SelectionPayload(payload);
  clearSearchForCategoryChange();
  if (setId == null) {
    selectedSet.value = null;
    selectedReferenceCharacter.value = null;
    await nextTick();
    return;
  }
  if (label) {
    lastSelectedSetLabel.value = label;
  }
  selectedSet.value = setId;
  selectedCharacter.value = null; // Clear character selection
  selectedReferenceCharacter.value = null;
  closeSidebarIfMobile();
}

function handleSearchAllPictures() {
  selectedCharacter.value = ALL_PICTURES_ID;
  selectedSet.value = null;
  selectedReferenceCharacter.value = null;
  lastSelectedCharacterLabel.value = "All Pictures";
}

async function handleUpdateSearchQuery(value) {
  const nextQuery = typeof value === "string" ? value.trim() : "";
  searchInput.value = nextQuery;
  searchQuery.value = nextQuery; // Ensure searchQuery is always a string
  addToSearchHistory(nextQuery);
}

async function handleUpdateSelectedSort({ sort, descending }) {
  selectedSort.value = sort;
  selectedDescending.value = descending;
  closeSidebarIfMobile();
}

function handleUpdateSortOptions(options) {
  sortOptions.value = Array.isArray(options) ? options : [];
}

function handleUpdateStackThreshold(value) {
  stackThreshold.value = value;
}

function handleStackStatsUpdate(payload) {
  const expanded = Number(payload?.expanded ?? 0);
  const total = Number(payload?.total ?? 0);
  expandedStackCount.value = Number.isFinite(expanded)
    ? Math.max(0, expanded)
    : 0;
  totalStackCount.value = Number.isFinite(total) ? Math.max(0, total) : 0;
}

function handleExpandAllStacks() {
  showStacks.value = true;
}

function handleCollapseAllStacks() {
  showStacks.value = false;
}

const selectedSimilarityCharacter = ref(null);
const similarityCharacterOptions = ref([]);
function handleUpdateSimilarityCharacter(val) {
  selectedSimilarityCharacter.value = val;
  refreshGridVersion();
  closeSidebarIfMobile();
}

function handleUpdateSimilarityOptions(options) {
  similarityCharacterOptions.value = Array.isArray(options) ? options : [];
}

function handleUpdateHiddenTags(tags) {
  const nextTags = Array.isArray(tags) ? tags : [];
  if (
    hiddenTags.value.length === nextTags.length &&
    hiddenTags.value.every((tag, index) => tag === nextTags[index])
  ) {
    return;
  }
  hiddenTags.value = nextTags;
}

function handleUpdateApplyTagFilter(value) {
  const nextValue = Boolean(value);
  if (applyTagFilter.value === nextValue) return;
  applyTagFilter.value = nextValue;
}

function handleUpdateDateFormat(value) {
  if (value == null) return;
  const nextValue = String(value);
  if (nextValue === dateFormat.value) return;
  dateFormat.value = nextValue;
}

function handleUpdateThemeMode(value) {
  if (value == null) return;
  themeMode.value = String(value);
}

function handleUpdateSidebarThumbnailSize(value) {
  const nextValue = Number(value);
  if (!Number.isFinite(nextValue)) return;
  sidebarThumbnailSize.value = nextValue;
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
  if (configLoading.value) return;
  configLoading.value = true;
  configApplying.value = true;
  try {
    const res = await apiClient.get("/users/me/config");
    const sortValue = res.data.sort_order ?? res.data.sort;
    if (typeof sortValue === "string" && sortValue) {
      selectedSort.value = sortValue;
    }
    if (typeof res.data.show_stars === "boolean")
      showStars.value = res.data.show_stars;
    if (typeof res.data.show_face_bboxes === "boolean") {
      showFaceBboxes.value = res.data.show_face_bboxes;
    }
    if (typeof res.data.show_format === "boolean") {
      showFormat.value = res.data.show_format;
    }
    if (typeof res.data.show_resolution === "boolean") {
      showResolution.value = res.data.show_resolution;
    }
    if (typeof res.data.show_problem_icon === "boolean") {
      showProblemIcon.value = res.data.show_problem_icon;
    }
    if (typeof res.data.expand_all_stacks === "boolean") {
      showStacks.value = res.data.expand_all_stacks;
    } else if (typeof res.data.show_stacks === "boolean") {
      showStacks.value = res.data.show_stacks;
    }
    if (typeof res.data.date_format === "string" && res.data.date_format) {
      dateFormat.value = res.data.date_format;
    }
    if (typeof res.data.theme_mode === "string" && res.data.theme_mode) {
      themeMode.value = res.data.theme_mode;
    }
    if (typeof res.data.date_format === "string" && res.data.date_format) {
      dateFormat.value = res.data.date_format;
    }
    if (typeof res.data.descending === "boolean") {
      selectedDescending.value = res.data.descending;
    }
    if (typeof res.data.columns === "number") {
      columns.value = res.data.columns;
    }
    if (typeof res.data.sidebar_thumbnail_size === "number") {
      sidebarThumbnailSize.value = res.data.sidebar_thumbnail_size;
    }
    if (res.data.stack_strictness != null) {
      stackThreshold.value = String(res.data.stack_strictness);
    }
    config.sort_order = sortValue || selectedSort.value;
    config.descending = selectedDescending.value;
    config.columns = columns.value;
    config.sidebar_thumbnail_size = sidebarThumbnailSize.value;
    config.show_stars =
      typeof res.data.show_stars === "boolean"
        ? res.data.show_stars
        : showStars.value;
    config.show_face_bboxes =
      typeof res.data.show_face_bboxes === "boolean"
        ? res.data.show_face_bboxes
        : showFaceBboxes.value;
    config.show_format =
      typeof res.data.show_format === "boolean"
        ? res.data.show_format
        : showFormat.value;
    config.show_resolution =
      typeof res.data.show_resolution === "boolean"
        ? res.data.show_resolution
        : showResolution.value;
    config.show_problem_icon =
      typeof res.data.show_problem_icon === "boolean"
        ? res.data.show_problem_icon
        : showProblemIcon.value;
    config.expand_all_stacks =
      typeof res.data.expand_all_stacks === "boolean"
        ? res.data.expand_all_stacks
        : typeof res.data.show_stacks === "boolean"
          ? res.data.show_stacks
          : showStacks.value;
    config.date_format = dateFormat.value;
    config.theme_mode = themeMode.value;
    config.stack_strictness =
      res.data.stack_strictness != null
        ? res.data.stack_strictness
        : config.stack_strictness;
    const similarityValue =
      res.data.similarity_character ?? res.data.selected_similarity_character;
    selectedSimilarityCharacter.value =
      similarityValue ?? selectedSimilarityCharacter.value ?? null;
    hiddenTags.value = Array.isArray(res.data.hidden_tags)
      ? res.data.hidden_tags
      : [];
    applyTagFilter.value = Boolean(res.data.apply_tag_filter);
    config.selectedSimilarityCharacter = selectedSimilarityCharacter.value;
    configSnapshot.value = {
      sort: selectedSort.value || "",
      descending: selectedDescending.value,
      columns: typeof columns.value === "number" ? columns.value : null,
      sidebar_thumbnail_size:
        typeof sidebarThumbnailSize.value === "number"
          ? sidebarThumbnailSize.value
          : null,
      show_stars: showStars.value,
      show_face_bboxes: showFaceBboxes.value,
      show_format: showFormat.value,
      show_resolution: showResolution.value,
      show_problem_icon: showProblemIcon.value,
      expand_all_stacks: showStacks.value,
      date_format: dateFormat.value,
      theme_mode: themeMode.value,
      similarity_character: selectedSimilarityCharacter.value,
      stack_strictness:
        res.data.stack_strictness != null
          ? Number(res.data.stack_strictness)
          : null,
      hidden_tags: hiddenTags.value,
      apply_tag_filter: applyTagFilter.value,
    };
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
  // Only include fields the backend expects and that are not undefined/null/empty
  const patch = {};
  if (selectedSort.value) patch.sort = selectedSort.value;
  patch.descending = selectedDescending.value;
  if (columns.value) patch.columns = columns.value;
  if (sidebarThumbnailSize.value) {
    patch.sidebar_thumbnail_size = sidebarThumbnailSize.value;
  }
  if (typeof showStars.value === "boolean") patch.show_stars = showStars.value;
  if (typeof showFaceBboxes.value === "boolean") {
    patch.show_face_bboxes = showFaceBboxes.value;
  }
  if (typeof showFormat.value === "boolean") {
    patch.show_format = showFormat.value;
  }
  if (typeof showResolution.value === "boolean") {
    patch.show_resolution = showResolution.value;
  }
  if (typeof showProblemIcon.value === "boolean") {
    patch.show_problem_icon = showProblemIcon.value;
  }
  if (typeof showStacks.value === "boolean") {
    patch.expand_all_stacks = showStacks.value;
  }
  if (typeof dateFormat.value === "string" && dateFormat.value) {
    patch.date_format = dateFormat.value;
  }
  if (typeof themeMode.value === "string" && themeMode.value) {
    patch.theme_mode = themeMode.value;
  }
  if (selectedSimilarityCharacter.value != null) {
    patch.similarity_character = selectedSimilarityCharacter.value;
  }
  if (stackThreshold.value != null && stackThreshold.value !== "") {
    const parsed = parseFloat(String(stackThreshold.value));
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
  const keys = ["Home", "End", "PageUp", "PageDown"];
  if (keys.includes(e.key)) {
    const grid = gridContainer.value;
    if (grid && typeof grid.onGlobalKeyPress === "function") {
      grid.onGlobalKeyPress(e.key, e);
    }
  }
}

function resolveThemeName(mode) {
  return mode === "dark" ? "pixlVaultDark" : "pixlVaultLight";
}

async function handleImagesAssignedToCharacter({ characterId, imageIds }) {
  if (selectedCharacter.value !== UNASSIGNED_PICTURES_ID || selectedSet.value) {
    return;
  }
  // Forward to ImageGrid via ref
  if (
    gridContainer.value &&
    typeof gridContainer.value.removeImagesById === "function"
  ) {
    gridContainer.value.removeImagesById(imageIds);
  }
}

function handleImagesMovedToSet({ imageIds }) {
  if (selectedCharacter.value !== UNASSIGNED_PICTURES_ID || selectedSet.value) {
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
  exportSelectedCount.value = Number(counts.selectedCount) || 0;
  exportTotalCount.value = Number(counts.totalCount) || 0;
}

function confirmExportZip() {
  gridContainer.value?.exportCurrentViewToZip({
    exportType: exportType.value,
    captionMode: exportCaptionMode.value,
    includeCharacterName: exportIncludeCharacterName.value,
    resolution: exportResolution.value,
  });
  exportMenuOpen.value = false;
}

// --- Search Overlay ---
const searchOverlayVisible = ref(false);

function openSearchOverlay() {
  searchOverlayVisible.value = true;
}

function closeSearchOverlay() {
  searchOverlayVisible.value = false;
}

function handleClearSearch() {
  searchQuery.value = "";
  searchInput.value = "";
  isSearchHistoryOpen.value = false;
  refreshGridVersion(); // Force the ImageGrid to refresh
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
  if (!query) {
    return;
  }
  const existingIndex = searchHistory.value.findIndex((item) => item === query);
  if (existingIndex !== -1) {
    searchHistory.value.splice(existingIndex, 1);
  }
  searchHistory.value.unshift(query);
  if (searchHistory.value.length > MAX_SEARCH_HISTORY) {
    searchHistory.value = searchHistory.value.slice(0, MAX_SEARCH_HISTORY);
  }
}

function applySearchHistory(query) {
  searchInput.value = query;
  commitSearch();
  isSearchHistoryOpen.value = false;
  nextTick(() => {
    blurSearchInput();
  });
}

function clearSearchHistory() {
  searchHistory.value = [];
  isSearchHistoryOpen.value = false;
}

function commitSearch() {
  const nextQuery =
    typeof searchInput.value === "string" ? searchInput.value.trim() : "";
  if (nextQuery === searchQuery.value) {
    return;
  }
  searchQuery.value = nextQuery;
  addToSearchHistory(nextQuery);
  isSearchHistoryOpen.value = false;
}

function handleResetToAll() {
  selectedCharacter.value = ALL_PICTURES_ID;
  selectedSet.value = null;
  selectedReferenceCharacter.value = null;
  lastSelectedCharacterLabel.value = "All Pictures";
  selectedSort.value = "DATE";
  selectedDescending.value = true;
  selectedSimilarityCharacter.value = null;
  searchQuery.value = "";
  mediaTypeFilter.value = "all";
  refreshGridVersion();
  closeSidebarIfMobile();
}

// --- Watchers ---
watch(searchQuery, (newVal, oldVal) => {
  if (searchInput.value !== newVal) {
    searchInput.value = newVal || "";
  }
  if (!newVal && oldVal) {
    refreshGridVersion();
  }
});

watch([searchInput, searchHistory, isMobile], () => {
  if (isMobile.value) {
    isSearchHistoryOpen.value = false;
    return;
  }
  const needle = (searchInput.value || "").trim();
  if (!needle) {
    isSearchHistoryOpen.value = false;
    return;
  }
  isSearchHistoryOpen.value = filteredSearchHistory.value.length > 0;
});

watch([selectedSort, selectedDescending], () => {
  patchConfigUIOptions();
  refreshGridVersion();
});

watch(hiddenTags, () => {
  refreshGridVersion();
  if (applyTagFilter.value) {
    refreshSidebarDebounced();
  }
});

watch(applyTagFilter, () => {
  refreshGridVersion();
  refreshSidebarDebounced();
});

watch([selectedCharacter, selectedSet, searchQuery], () => {
  sendUpdatesFilters();
});

watch(thumbnailSize, () => {
  patchConfigUIOptions();
  updateMaxColumns();
});

watch(showStars, () => {
  patchConfigUIOptions();
});

watch(
  [showFaceBboxes, showFormat, showResolution, showProblemIcon, showStacks],
  () => {
    patchConfigUIOptions();
  },
);

watch(
  [showFaceBboxes, showFormat, showResolution, showProblemIcon, showStacks],
  ([face, format, resolution, problem, stacks]) => {},
  { immediate: true },
);

watch(selectedSimilarityCharacter, () => {
  patchConfigUIOptions();
});

watch(stackThreshold, () => {
  if (!configLoaded.value) return;
  patchConfigUIOptions();
});

watch(columns, () => {
  if (!configLoaded.value) return;
  patchConfigUIOptions();
});

watch(sidebarThumbnailSize, () => {
  if (!configLoaded.value) return;
  patchConfigUIOptions();
});

watch(dateFormat, () => {
  if (!configLoaded.value) return;
  patchConfigUIOptions();
  refreshGridVersion();
});

watch(
  themeMode,
  (value) => {
    theme.global.name.value = resolveThemeName(value);
    if (!configLoaded.value) return;
    patchConfigUIOptions();
  },
  { immediate: true },
);

watch(exportMenuOpen, async (isOpen) => {
  if (!isOpen) return;
  await nextTick();
  refreshExportCount();
});

// --- Lifecycle ---
onMounted(async () => {
  await fetchConfig();
  updateIsMobile();
  window.addEventListener("resize", updateIsMobile);
  window.addEventListener("keydown", handleGlobalKeydown);
  window.addEventListener("dragover", handleWindowDragOver, true);
  window.addEventListener("drop", handleWindowDrop, true);
  refreshSidebar();
  updateMaxColumns();
  connectUpdatesSocket();
  if (typeof ResizeObserver !== "undefined" && mainAreaRef.value) {
    mainAreaResizeObserver = new ResizeObserver(() => {
      updateMaxColumns();
    });
    mainAreaResizeObserver.observe(mainAreaRef.value);
  }
});

onBeforeUnmount(() => {
  disconnectUpdatesSocket();
  window.removeEventListener("resize", updateIsMobile);
  window.removeEventListener("keydown", handleGlobalKeydown);
  window.removeEventListener("dragover", handleWindowDragOver, true);
  window.removeEventListener("drop", handleWindowDrop, true);
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

defineExpose({ sidebarVisible, mediaTypeFilter });
</script>
<template>
  <v-app>
    <div class="app-viewport">
      <div class="file-manager">
        <div
          class="sidebar-shell"
          :class="{ open: sidebarVisible }"
          v-show="sidebarVisible || !isMobile"
        >
          <SideBar
            ref="sidebarRef"
            :collapsed="!sidebarVisible && !isMobile"
            :selectedCharacter="selectedCharacter"
            :selectedReferenceCharacter="selectedReferenceCharacter"
            :allPicturesId="ALL_PICTURES_ID"
            :unassignedPicturesId="UNASSIGNED_PICTURES_ID"
            :scrapheapPicturesId="SCRAPHEAP_PICTURES_ID"
            :selectedSet="selectedSet"
            :searchQuery="searchQuery"
            :selectedSort="selectedSort"
            :selectedDescending="selectedDescending"
            :backendUrl="BACKEND_URL"
            :selectedSimilarityCharacter="selectedSimilarityCharacter"
            :sidebarThumbnailSize="sidebarThumbnailSize"
            :dateFormat="dateFormat"
            :themeMode="themeMode"
            @update:similarity-options="handleUpdateSimilarityOptions"
            @update:sort-options="handleUpdateSortOptions"
            @update:hidden-tags="handleUpdateHiddenTags"
            @update:apply-tag-filter="handleUpdateApplyTagFilter"
            @update:date-format="handleUpdateDateFormat"
            @update:theme-mode="handleUpdateThemeMode"
            @update:sidebar-thumbnail-size="handleUpdateSidebarThumbnailSize"
            @select-character="handleSelectCharacter"
            @select-reference-pictures="handleSelectReferencePictures"
            @select-set="handleSelectSet"
            @images-assigned-to-character="handleImagesAssignedToCharacter"
            @images-moved="handleImagesMovedToSet"
            @faces-assigned-to-character="handleFacesAssignedToCharacter"
            @toggle-sidebar="sidebarVisible = !sidebarVisible"
            @update:selected-sort="handleUpdateSelectedSort"
            @update:similarity-character="handleUpdateSimilarityCharacter"
            @open-import-dialog="openImportDialog"
            @update:set-error="error = $event"
            @update:set-loading="loading = $event"
          />
        </div>
        <div
          v-if="sidebarVisible && isMobile"
          class="sidebar-backdrop"
          @click="sidebarVisible = false"
        ></div>
        <PhotosImportDialog
          v-model:open="photosDialogOpen"
          @local-import="handleLocalImport"
        />
        <main class="main-area" ref="mainAreaRef">
          <Toolbar
            ref="toolbarRef"
            :isMobile="isMobile"
            :sidebarVisible="sidebarVisible"
            :searchOverlayVisible="searchOverlayVisible"
            :isSearchActive="Boolean(searchQuery && searchQuery.trim())"
            :filteredSearchHistory="filteredSearchHistory"
            :minColumns="minColumns"
            :maxColumns="maxColumns"
            :exportCount="exportCount"
            :exportCaptionOptions="exportCaptionOptions"
            :exportTypeOptions="exportTypeOptions"
            :exportResolutionOptions="exportResolutionOptions"
            :exportTypeLocksCaptions="exportTypeLocksCaptions"
            :sortOptions="sortOptions"
            :selectedSort="selectedSort"
            :selectedDescending="selectedDescending"
            :similarityCharacterOptions="similarityCharacterOptions"
            :selectedSimilarityCharacter="selectedSimilarityCharacter"
            :stackThreshold="stackThreshold"
            :stackExpandedCount="expandedStackCount"
            :stackTotalCount="totalStackCount"
            v-model:searchInput="searchInput"
            v-model:isSearchHistoryOpen="isSearchHistoryOpen"
            v-model:columnsMenuOpen="columnsMenuOpen"
            v-model:overlaysMenuOpen="overlaysMenuOpen"
            v-model:exportMenuOpen="exportMenuOpen"
            v-model:columns="columns"
            v-model:showStars="showStars"
            v-model:showFaceBboxes="showFaceBboxes"
            v-model:showFormat="showFormat"
            v-model:showResolution="showResolution"
            v-model:showProblemIcon="showProblemIcon"
            v-model:showStacks="showStacks"
            v-model:exportType="exportType"
            v-model:exportCaptionMode="exportCaptionMode"
            v-model:exportResolution="exportResolution"
            v-model:exportIncludeCharacterName="exportIncludeCharacterName"
            v-model:mediaTypeFilter="mediaTypeFilter"
            @update:selected-sort="handleUpdateSelectedSort"
            @update:similarity-character="handleUpdateSimilarityCharacter"
            @update:stack-threshold="handleUpdateStackThreshold"
            @open-search-overlay="openSearchOverlay"
            @toggle-sidebar="sidebarVisible = !sidebarVisible"
            @commit-search="commitSearch"
            @clear-search="handleClearSearch"
            @apply-search-history="applySearchHistory"
            @clear-search-history="clearSearchHistory"
            @columns-end="handleColumnsEnd"
            @confirm-export-zip="confirmExportZip"
            @open-settings="openSettingsDialog"
            @expand-all-stacks="handleExpandAllStacks"
            @collapse-all-stacks="handleCollapseAllStacks"
          />
          <div
            :class="['main-content', selectedCharacter ? 'accent-border' : '']"
            style="margin-top: 0; padding-top: 0"
          >
            <ImageGrid
              ref="gridContainer"
              :thumbnailSize="thumbnailSize"
              :sidebarVisible="sidebarVisible"
              :backendUrl="BACKEND_URL"
              :selectedCharacter="selectedCharacter"
              :selectedReferenceCharacter="selectedReferenceCharacter"
              :selectedSet="selectedSet"
              :searchQuery="searchQuery"
              :activeCategoryLabel="activeCategoryLabel"
              :isAllPicturesActive="isAllPicturesActive"
              :selectedSort="selectedSort"
              :selectedDescending="selectedDescending"
              :similarityCharacter="selectedSimilarityCharacter"
              :stackThreshold="stackThreshold"
              :showStars="showStars"
              :gridVersion="gridVersion"
              :wsUpdateKey="wsUpdateKey"
              :wsTagUpdate="wsTagUpdate"
              :wsPluginProgress="wsPluginProgress"
              :mediaTypeFilter="mediaTypeFilter"
              :showFaceBboxes="showFaceBboxes"
              :showFormat="showFormat"
              :showResolution="showResolution"
              :showProblemIcon="showProblemIcon"
              :showStacks="showStacks"
              :dateFormat="dateFormat"
              :hiddenTags="hiddenTags"
              :applyTagFilter="applyTagFilter"
              :allPicturesId="ALL_PICTURES_ID"
              :unassignedPicturesId="UNASSIGNED_PICTURES_ID"
              :scrapheapPicturesId="SCRAPHEAP_PICTURES_ID"
              :columns="columns"
              @clear-search="handleClearSearch"
              @search-all="handleSearchAllPictures"
              @update:selected-sort="handleUpdateSelectedSort"
              @refresh-sidebar="refreshSidebar"
              @reset-to-all="handleResetToAll"
              @update:stack-stats="handleStackStatsUpdate"
            />
          </div>
        </main>
      </div>
      <SearchOverlay
        v-if="searchOverlayVisible"
        :modelValue="searchQuery"
        @search="handleUpdateSearchQuery"
        @close="closeSearchOverlay"
      />
    </div>
  </v-app>
</template>
<style scoped src="./App.css"></style>
