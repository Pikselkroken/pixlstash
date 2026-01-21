<script setup>
import nlp from "compromise";
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onBeforeMount,
  onMounted,
  reactive,
  ref,
  watch,
} from "vue";
import { apiClient, API_BASE_URL } from "./utils/apiClient";

import SideBar from "./components/SideBar.vue";
import ImageGrid from "./components/ImageGrid.vue";
import SearchOverlay from "./components/SearchOverlay.vue";

const BACKEND_URL = API_BASE_URL;
const ALL_PICTURES_ID = "ALL";
const UNASSIGNED_PICTURES_ID = "UNASSIGNED";

// --- Template & Component Refs ---
const gridContainer = ref(null);
const selectedImageIds = ref([]);
let lastSelectedIndex = null;
const sidebarRef = ref(null);

const selectedCharacter = ref(ALL_PICTURES_ID);
const selectedSet = ref(null);
const selectedSort = ref("");
const selectedDescending = ref(true);
const stackThreshold = ref(null);

// --- Search & Filtering State ---
const searchQuery = ref("");
const searchInput = ref("");
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

const thumbnailSize = ref(256);
const columns = ref(4); // Default columns
const MIN_THUMBNAIL_SIZE = 128;
const MAX_THUMBNAIL_SIZE = 320;
const MIN_COLUMNS = 2;
const MAX_COLUMNS = 10;
const minColumns = ref(4);
const maxColumns = ref(10);
const mainAreaRef = ref(null);
let mainAreaResizeObserver = null;
const sidebarVisible = ref(true);
const isMobile = ref(false);
const MOBILE_BREAKPOINT = 900;

// --- Media Type Filter State ---
const mediaTypeFilter = ref("all"); // 'all', 'images', 'videos'

const gridVersion = ref(0);
const columnsMenuOpen = ref(false);
const configLoaded = ref(false);
const COLUMNS_MENU_CLOSE_DELAY_MS = 300;
let columnsMenuCloseTimeout = null;

function refreshGridVersion() {
  gridVersion.value++;
}

// --- Export Menu State ---
const exportMenuOpen = ref(false);
const exportCaptionMode = ref("description");
const exportIncludeCharacterName = ref(true);
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

// --- Config Dialog State ---
const config = reactive({
  sort: "",
  thumbnail: 256,
  show_stars: true,
});

const loading = ref(false);
const error = ref(null);

function refreshSidebar() {
  sidebarRef.value?.refreshSidebar();
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

async function handleSelectCharacter(charId) {
  console.log("[App.vue] handleSelectCharacter called with charId:", charId);
  if (charId == null) {
    selectedCharacter.value = null;
    await nextTick();
    return;
  }
  selectedCharacter.value = charId;
  selectedSet.value = null; // Clear set selection
  searchQuery.value = ""; // Clear search query
  await nextTick(); // Ensure reactivity propagates the change
  console.log("[App.vue] searchQuery cleared:", searchQuery.value);
  closeSidebarIfMobile();
}

async function handleSelectSet(setId) {
  if (setId == null) {
    selectedSet.value = null;
    await nextTick();
    return;
  }
  selectedSet.value = setId;
  selectedCharacter.value = null; // Clear character selection
  searchQuery.value = ""; // Clear search query
  closeSidebarIfMobile();
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

function handleUpdateStackThreshold(value) {
  stackThreshold.value = value;
}

const selectedSimilarityCharacter = ref(null);
function handleUpdateSimilarityCharacter(val) {
  selectedSimilarityCharacter.value = val;
  refreshGridVersion();
  closeSidebarIfMobile();
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
  try {
    const res = await apiClient.get("/users/me/config");
    console.log("Fetched config:", res);
    const sortValue = res.data.sort_order ?? res.data.sort;
    if (typeof sortValue === "string" && sortValue) {
      selectedSort.value = sortValue;
    }
    if (typeof res.data.show_stars === "boolean")
      showStars.value = res.data.show_stars;
    if (typeof res.data.descending === "boolean") {
      selectedDescending.value = res.data.descending;
    }
    if (typeof res.data.columns === "number") {
      columns.value = res.data.columns;
    }
    config.sort_order = sortValue || selectedSort.value;
    config.descending = selectedDescending.value;
    config.columns = columns.value;
    config.show_stars =
      typeof res.data.show_stars === "boolean"
        ? res.data.show_stars
        : showStars.value;
    const similarityValue =
      res.data.similarity_character ?? res.data.selected_similarity_character;
    selectedSimilarityCharacter.value =
      similarityValue ?? selectedSimilarityCharacter.value ?? null;
    config.selectedSimilarityCharacter = selectedSimilarityCharacter.value;
    configLoaded.value = true;
  } catch (e) {
    console.error("Failed to fetch /users/me/config:", e);
  }
}

async function patchConfigUIOptions() {
  // Only include fields the backend expects and that are not undefined/null/empty
  const patch = {};
  if (selectedSort.value) patch.sort = selectedSort.value;
  patch.descending = selectedDescending.value;
  if (columns.value) patch.columns = columns.value;
  if (typeof showStars.value === "boolean") patch.show_stars = showStars.value;
  if (selectedSimilarityCharacter.value != null) {
    patch.similarity_character = selectedSimilarityCharacter.value;
  }

  console.log("PATCH /users/me/config payload:", patch);
  try {
    const response = await apiClient.patch("/users/me/config", patch);

    const updatedConfig = await response.data;
    console.log("PATCH /users/me/config response:", updatedConfig);
  } catch (e) {
    console.error("Error patching /users/me/config:", e);
  }
}

function handleGridBackgroundClick(e) {
  if (!e.target.closest(".thumbnail-card")) {
    selectedImageIds.value = [];
    lastSelectedIndex = null;
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

async function handleImagesAssignedToCharacter({ characterId, imageIds }) {
  // Forward to ImageGrid via ref
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

function handleImagesUploaded() {
  // Called when images are imported
  refreshGridVersion(); // Force grid and thumbnails to refresh
  refreshSidebar(); // Optionally refresh sidebar counts
}

function cancelExportZip() {
  exportMenuOpen.value = false;
}

function confirmExportZip() {
  console.log("Exporting current view to zip...");
  gridContainer.value?.exportCurrentViewToZip({
    captionMode: exportCaptionMode.value,
    includeCharacterName: exportIncludeCharacterName.value,
  });
  exportMenuOpen.value = false;
}

// --- Search Overlay ---
const searchOverlayVisible = ref(false);

function openSearchOverlay() {
  searchOverlayVisible.value = true;
  console.log("Search overlay visibility toggled:", searchOverlayVisible.value);
}

function closeSearchOverlay() {
  searchOverlayVisible.value = false;
  console.log("Search overlay closed");
}

function handleClearSearch() {
  console.log("[App.vue] handleClearSearch called");
  searchQuery.value = "";
  searchInput.value = "";
  isSearchHistoryOpen.value = false;
  console.log("[App.vue] searchQuery cleared:", searchQuery.value);
  refreshGridVersion(); // Force the ImageGrid to refresh
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

watch(thumbnailSize, () => {
  patchConfigUIOptions();
  updateMaxColumns();
});

watch(showStars, () => {
  patchConfigUIOptions();
});

watch(selectedSimilarityCharacter, () => {
  patchConfigUIOptions();
});

watch(columns, () => {
  if (!configLoaded.value) return;
  patchConfigUIOptions();
});

watch(exportMenuOpen, async (isOpen) => {
  if (!isOpen) return;
  await nextTick();
  refreshExportCount();
});

// --- Lifecycle ---
onBeforeMount(() => {
  fetchConfig();
});
onMounted(() => {
  updateIsMobile();
  window.addEventListener("resize", updateIsMobile);
  window.addEventListener("keydown", handleGlobalKeydown);
  refreshSidebar();
  updateMaxColumns();
  if (typeof ResizeObserver !== "undefined" && mainAreaRef.value) {
    mainAreaResizeObserver = new ResizeObserver(() => {
      updateMaxColumns();
    });
    mainAreaResizeObserver.observe(mainAreaRef.value);
  }
});

onBeforeUnmount(() => {
  window.removeEventListener("resize", updateIsMobile);
  window.removeEventListener("keydown", handleGlobalKeydown);
  if (mainAreaResizeObserver) {
    mainAreaResizeObserver.disconnect();
    mainAreaResizeObserver = null;
  }
  if (columnsMenuCloseTimeout) {
    clearTimeout(columnsMenuCloseTimeout);
    columnsMenuCloseTimeout = null;
  }
});

defineExpose({ sidebarVisible, mediaTypeFilter });
</script>
<template src="./App.template.html"></template>
<style scoped src="./App.css"></style>
