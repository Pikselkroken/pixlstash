<script setup>
import nlp from "compromise";
import {
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
const selectedDescending = ref(false);

// --- Search & Filtering State ---
const searchQuery = ref("");
const showStars = ref(true);
const showFaceBboxes = ref(false);

const thumbnailSize = ref(256);
const sidebarVisible = ref(true);

// --- Media Type Filter State ---
const mediaTypeFilter = ref("all"); // 'all', 'images', 'videos'

const gridVersion = ref(0);

function refreshGridVersion() {
  gridVersion.value++;
}

// --- Export Dialog State ---
const exportDialog = ref(false);
const exportCaptionMode = ref("description");
const exportIncludeCharacterName = ref(true);
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

async function handleSelectCharacter(charId) {
  console.log("[App.vue] handleSelectCharacter called with charId:", charId);
  selectedCharacter.value = charId;
  selectedSet.value = null; // Clear set selection
  searchQuery.value = ""; // Clear search query
  await nextTick(); // Ensure reactivity propagates the change
  console.log("[App.vue] searchQuery cleared:", searchQuery.value);
}

async function handleSelectSet(setId) {
  selectedSet.value = setId;
  selectedCharacter.value = null; // Clear character selection
  searchQuery.value = ""; // Clear search query
}

async function handleUpdateSearchQuery(value) {
  searchQuery.value = typeof value === "string" ? value : ""; // Ensure searchQuery is always a string
}

async function handleUpdateSelectedSort({ sort, descending }) {
  selectedSort.value = sort;
  selectedDescending.value = descending;
}

const selectedSimilarityCharacter = ref(null);
function handleUpdateSimilarityCharacter(val) {
  selectedSimilarityCharacter.value = val;
  refreshGridVersion();
}

async function fetchConfig() {
  try {
    const res = await apiClient.get("/config");
    console.log("Fetched config:", res);
    const sortValue = res.data.sort_order ?? res.data.sort;
    if (typeof sortValue === "string" && sortValue) {
      selectedSort.value = sortValue;
    }
    const thumbnailValue =
      typeof res.data.thumbnail_size === "number"
        ? res.data.thumbnail_size
        : typeof res.data.thumbnail === "number"
          ? res.data.thumbnail
          : null;
    if (thumbnailValue !== null) {
      thumbnailSize.value = thumbnailValue;
      await nextTick();
    }
    if (typeof res.data.show_stars === "boolean")
      showStars.value = res.data.show_stars;
    config.sort_order = sortValue || selectedSort.value;
    config.descending = res.data.descending ?? selectedDescending.value;
    config.thumbnail_size = thumbnailValue || thumbnailSize.value;
    config.show_stars =
      typeof res.data.show_stars === "boolean"
        ? res.data.show_stars
        : showStars.value;
    config.selectedSimilarityCharacter =
      res.data.selected_similarity_character ||
      selectedSimilarityCharacter.value;
  } catch (e) {
    console.error("Failed to fetch /config:", e);
  }
}

async function patchConfigUIOptions() {
  // Only include fields the backend expects and that are not undefined/null/empty
  const patch = {};
  if (selectedSort.value) patch.sort = selectedSort.value;
  patch.descending = selectedDescending.value;
  if (thumbnailSize.value) patch.thumbnail = thumbnailSize.value;
  if (typeof showStars.value === "boolean") patch.show_stars = showStars.value;
  if (config.selectedSimilarityCharacter)
    patch.selected_similarity_character = config.selectedSimilarityCharacter;

  console.log("PATCH /config payload:", patch);
  try {
    const response = await apiClient.patch("/config", patch);

    const updatedConfig = await response.data;
    console.log("PATCH /config response:", updatedConfig);
  } catch (e) {
    console.error("Error patching /config:", e);
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

function handleImagesUploaded() {
  // Called when images are imported
  refreshGridVersion(); // Force grid and thumbnails to refresh
  refreshSidebar(); // Optionally refresh sidebar counts
}

// --- Export to Zip ---
function handleExportZip() {
  exportDialog.value = true;
}

function cancelExportZip() {
  exportDialog.value = false;
}

function confirmExportZip() {
  console.log("Exporting current view to zip...");
  gridContainer.value?.exportCurrentViewToZip({
    captionMode: exportCaptionMode.value,
    includeCharacterName: exportIncludeCharacterName.value,
  });
  exportDialog.value = false;
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
  console.log("[App.vue] searchQuery cleared:", searchQuery.value);
  refreshGridVersion(); // Force the ImageGrid to refresh
}

// --- Watchers ---
watch(searchQuery, (newVal, oldVal) => {
  if (!newVal && oldVal) {
    refreshGridVersion();
  }
});

watch([selectedSort, selectedDescending], () => {
  patchConfigUIOptions();
  refreshGridVersion();
});

watch(thumbnailSize, () => {
  patchConfigUIOptions();
});

watch(showStars, () => {
  patchConfigUIOptions();
});

watch(selectedSimilarityCharacter, () => {
  patchConfigUIOptions();
});

// --- Lifecycle ---
onBeforeMount(() => {
  fetchConfig();
});
onMounted(() => {
  window.addEventListener("keydown", handleGlobalKeydown);
  refreshSidebar();
});

onBeforeUnmount(() => {
  window.removeEventListener("keydown", handleGlobalKeydown);
});

defineExpose({ sidebarVisible, mediaTypeFilter });
</script>
<template src="./App.template.html"></template>
<style scoped src="./App.css"></style>
