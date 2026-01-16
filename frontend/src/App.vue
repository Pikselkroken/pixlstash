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
import SearchBar from "./components/SearchBar.vue";

const likenessRowsRef = ref(null);

const BACKEND_URL = API_BASE_URL;
const ALL_PICTURES_ID = "ALL";
const UNASSIGNED_PICTURES_ID = "UNASSIGNED";

// --- Template & Component Refs ---
const gridContainer = ref(null);
const selectedImageIds = ref([]);
let lastSelectedIndex = null;
const currentView = ref("grid"); // or 'likeness'
const sidebarRef = ref(null);

const selectedCharacter = ref(ALL_PICTURES_ID);
const selectedSet = ref(null);
const selectedSort = ref("");
const selectedDescending = ref(false);

// --- Search & Filtering State ---
const searchQuery = ref("");
const showStars = ref(true);
const showFaceBboxes = ref(false);

const chatWindowRef = ref(null);

const thumbnailSize = ref(256);
const sidebarVisible = ref(true);

// --- Media Type Filter State ---
const mediaTypeFilter = ref("all"); // 'all', 'images', 'videos'

// --- Chat Overlay State ---
const chatOpen = ref(false);

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
const settingsDialog = ref(false);
const config = reactive({
  image_roots: [],
  selected_image_root: "",
  sort: "",
  thumbnail: 256,
  show_stars: true,
  likeness_threshold: 0.97,
  openai_host: "localhost",
  openai_port: 8000,
  openai_model: "",
  default_device: "cpu", // Add default_device to config
});
const openaiModels = ref([]);
const openaiModelFetchError = ref("");
const openaiModelLoading = ref(false);
const newImageRoot = ref("");

const loading = ref(false);
const error = ref(null);

function refreshSidebar() {
  sidebarRef.value?.refreshSidebar();
}

function handleSwitchToLikeness() {
  console.log("[App.vue] handleSwitchToLikeness called");
  currentView.value = "likeness";
  nextTick(() => {
    console.log("[App.vue] Calling likenessRowsRef.refreshLikeness()");
    likenessRowsRef.value?.refreshLikeness();
  });
}

async function handleSwitchToGrid() {
  currentView.value = "grid";
}

async function handleSelectCharacter(charId) {
  console.log("[App.vue] handleSelectCharacter called with charId:", charId);
  selectedCharacter.value = charId;
  selectedSet.value = null; // Clear set selection
  searchQuery.value = ""; // Clear search query
  await nextTick(); // Ensure reactivity propagates the change
  console.log("[App.vue] searchQuery cleared:", searchQuery.value);
  handleSwitchToGrid();
}

async function handleSelectSet(setId) {
  selectedSet.value = setId;
  selectedCharacter.value = null; // Clear character selection
  searchQuery.value = ""; // Clear search query
  handleSwitchToGrid();
}

async function handleUpdateSearchQuery(value) {
  searchQuery.value = typeof value === "string" ? value : ""; // Ensure searchQuery is always a string
  handleSwitchToGrid();
}

async function handleUpdateSelectedSort({ sort, descending }) {
  selectedSort.value = sort;
  selectedDescending.value = descending;
  handleSwitchToGrid();
}

const selectedSimilarityCharacter = ref(null);
function handleUpdateSimilarityCharacter(val) {
  selectedSimilarityCharacter.value = val;
  refreshGridVersion();
}

// --- Settings & Config ---
async function fetchOpenAIModels() {
  openaiModelLoading.value = true;
  openaiModelFetchError.value = "";
  openaiModels.value = [];
  try {
    const res = await apiClient.get("/openai/models");
    console.log("Fetched OpenAI models:", res);
    if (Array.isArray(res.data)) {
      openaiModels.value = res.data.map((m) => m.id);
    } else {
      openaiModelFetchError.value = "No models found.";
    }
  } catch (e) {
    openaiModelFetchError.value = "Failed to fetch models: " + (e.message || e);
  } finally {
    openaiModelLoading.value = false;
  }
}

async function fetchConfig() {
  try {
    const res = await apiClient.get("/config");
    console.log("Fetched config:", res);
    config.image_roots = res.data.image_roots || [];
    config.selected_image_root = res.data.selected_image_root || "";
    if (typeof res.data.likeness_threshold === "number") {
      config.likeness_threshold = res.data.likeness_threshold;
    }
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
    config.thumbnail_size = thumbnailValue || thumbnailSize.value;
    config.show_stars =
      typeof res.data.show_stars === "boolean"
        ? res.data.show_stars
        : showStars.value;
    config.openai_host = res.data.openai_host || "localhost";
    config.openai_port = res.data.openai_port || 8000;
    config.openai_model = res.data.openai_model || "";
    config.default_device = res.data.default_device || "cpu";
  } catch (e) {
    console.error("Failed to fetch /config:", e);
  }
}

async function addImageRoot() {
  const val = newImageRoot.value.trim();
  if (!val || config.image_roots.includes(val)) return;
  config.image_roots.push(val);
  newImageRoot.value = "";
  await apiClient.patch("/config", {
    image_roots: config.image_roots,
  });
}

function removeImageRoot(root) {
  if (config.image_roots.length <= 1) return;
  const idx = config.image_roots.indexOf(root);
  if (idx !== -1) {
    config.image_roots.splice(idx, 1);
    if (config.selected_image_root === root) {
      config.selected_image_root = config.image_roots[0] || "";
    }
    saveConfig();
  }
}

async function updateSelectedRoot() {
  await apiClient.patch("/config", {
    selected_image_root: config.selected_image_root,
  });
  await fetchConfig();
  selectedCharacter.value = ALL_PICTURES_ID;
  selectedSet.value = null;
  refreshSidebar();
  if (currentView.value === "grid") {
    refreshGridVersion();
  } else if (currentView.value === "likeness") {
    likenessRowsRef.value?.refreshLikeness();
  }
}

async function patchConfigUIOptions() {
  // Only include fields the backend expects and that are not undefined/null/empty
  const patch = {};
  if (selectedSort.value) patch.sort = selectedSort.value;
  patch.descending = selectedDescending.value;
  if (thumbnailSize.value) patch.thumbnail = thumbnailSize.value;
  if (typeof showStars.value === "boolean") patch.show_stars = showStars.value;
  if (typeof config.likeness_threshold === "number")
    patch.likeness_threshold = config.likeness_threshold;
  if (config.openai_host) patch.openai_host = config.openai_host;
  if (config.openai_port) patch.openai_port = config.openai_port;
  if (config.openai_model) patch.openai_model = config.openai_model;
  if (config.default_device) patch.default_device = config.default_device;
  // Only send image_roots and selected_image_root if present
  if (Array.isArray(config.image_roots) && config.image_roots.length > 0)
    patch.image_roots = config.image_roots;
  if (config.selected_image_root)
    patch.selected_image_root = config.selected_image_root;
  console.log("PATCH /config payload:", patch);
  try {
    const response = await apiClient.patch("/config", patch);

    const updatedConfig = await response.data;
    console.log("PATCH /config response:", updatedConfig);
  } catch (e) {
    console.error("Error patching /config:", e);
  }
}

function selectImageRoot(root) {
  if (config.selected_image_root !== root) {
    config.selected_image_root = root;
    updateSelectedRoot();
  }
}

async function saveConfig() {
  await apiClient.patch("/config", {
    image_roots: config.image_roots,
    selected_image_root: config.selected_image_root,
  });
}

function openSettingsDialog() {
  console.debug("Opening settings dialog");
  fetchConfig().then(() => {
    fetchOpenAIModels();
  });
  settingsDialog.value = true;
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
  if (currentView.value !== "grid") {
    console.warn("Export to zip is only available in grid view.");
    return;
  }
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
// Scroll to bottom after END loads last page
// (Removed watch on images)

// (Removed watch on selectedSort, selectedCharacter, selectedSet for image loading)

watch(searchQuery, (newVal, oldVal) => {
  if (!newVal && oldVal) {
    refreshGridVersion();
  }
});

watch(currentView, (val) => {
  if (val === "likeness") {
    console.log(
      "[App.vue] currentView watcher: switched to likeness, refreshing..."
    );
    nextTick(() => {
      console.log(
        "[App.vue] currentView watcher: calling likenessRowsRef.refreshLikeness()"
      );
      likenessRowsRef.value?.refreshLikeness();
    });
  }
});

watch(settingsDialog, (val) => {
  if (val) fetchConfig();
});

watch([selectedSort, selectedDescending], () => {
  patchConfigUIOptions();
  refreshGridVersion();
});

watch(thumbnailSize, () => {
  patchConfigUIOptions();
});

watch(
  () => config.likeness_threshold,
  () => {
    patchConfigUIOptions();
  }
);

watch(showStars, () => {
  patchConfigUIOptions();
});

// Watch all AI chat config fields together and PATCH all at once
watch(
  () => [config.openai_host, config.openai_port, config.openai_model],
  ([host, port, model], [oldHost, oldPort, oldModel]) => {
    if (host !== oldHost || port !== oldPort || model !== oldModel) {
      patchConfigUIOptions();
    }
  }
);
// Watch for default_device changes
watch(
  () => config.default_device,
  () => {
    patchConfigUIOptions();
  }
);

// Watch for vault change and update view
watch(
  () => config.selected_image_root,
  (val, oldVal) => {
    if (val !== oldVal) {
      refreshSidebar();
    }
  }
);

// --- Lifecycle ---
onBeforeMount(() => {
  fetchConfig();
});
onMounted(() => {
  window.addEventListener("keydown", handleGlobalKeydown);
});

onBeforeUnmount(() => {
  window.removeEventListener("keydown", handleGlobalKeydown);
});

defineExpose({ currentView, sidebarVisible, mediaTypeFilter });
</script>
<template src="./App.template.html"></template>
<style scoped src="./App.css"></style>
