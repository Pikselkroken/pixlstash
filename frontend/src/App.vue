<script setup>
import nlp from "compromise";
import {
  nextTick,
  onBeforeUnmount,
  onMounted,
  reactive,
  ref,
  watch,
} from "vue";

import SideBar from "./components/SideBar.vue";
import ChatWindow from "./components/ChatWindow.vue";
import ImageGrid from "./components/ImageGrid.vue";
import CharacterEditor from "./components/CharacterEditor.vue";
import PictureSetEditor from "./components/PictureSetEditor.vue";
import LikenessRows from "./components/LikenessRows.vue";

// --- Backend Constants & Identifiers ---
const BACKEND_URL = "http://localhost:9537";
const ALL_PICTURES_ID = "__all__";
const UNASSIGNED_PICTURES_ID = "__unassigned__";

// --- Template & Component Refs ---
const gridContainer = ref(null);
const selectedImageIds = ref([]);
let lastSelectedIndex = null;
const currentView = ref("grid"); // or 'likeness'
const sidebarRef = ref(null);

const selectedCharacter = ref(ALL_PICTURES_ID);
const selectedSet = ref(null);
const selectedSort = ref("");

// --- Search & Filtering State ---
const searchQuery = ref("");
const showStars = ref(true);

const chatWindowRef = ref(null);

const thumbnailSize = ref(256);
const sidebarVisible = ref(true);

// --- Chat Overlay State ---
const chatOpen = ref(false);

// --- Config Dialog State ---
const settingsDialog = ref(false);
const config = reactive({
  image_roots: [],
  selected_image_root: "",
  sort: "",
  thumbnail: 256,
  show_stars: true,
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

function refreshGrid() {}

function refreshLikeness() {}

async function handleSwitchToLikeness() {
  currentView.value = "likeness";
}

async function handleSwitchToGrid() {
  currentView.value = "grid";
}

async function handleSelectCharacter(charId) {
  selectedCharacter.value = charId;
  selectedSet.value = null; // Clear set selection
  handleSwitchToGrid();
}

async function handleSelectSet(setId) {
  selectedSet.value = setId;
  selectedCharacter.value = null; // Clear character selection
  handleSwitchToGrid();
}

async function handleUpdateSearchQuery(value) {
  searchQuery.value = value;
  handleSwitchToGrid();
}

async function handleUpdateSelectedSort(value) {
  selectedSort.value = value;
  handleSwitchToGrid();
}

// --- Settings & Config ---
async function fetchOpenAIModels() {
  openaiModelLoading.value = true;
  openaiModelFetchError.value = "";
  openaiModels.value = [];
  try {
    const url = `http://${config.openai_host}:${config.openai_port}/v1/models`;
    const res = await fetch(url);
    if (!res.ok) throw new Error("Failed to fetch models");
    const data = await res.json();
    if (Array.isArray(data.data)) {
      openaiModels.value = data.data.map((m) => m.id);
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
    const res = await fetch(`${BACKEND_URL}/config`);
    if (!res.ok) {
      const text = await res.text();
      console.error("Failed to fetch /config:", res.status, text);
      return;
    }
    const data = await res.json();
    config.image_roots = data.image_roots || [];
    config.selected_image_root = data.selected_image_root || "";
    const sortValue = data.sort_order ?? data.sort;
    if (typeof sortValue === "string" && sortValue) {
      selectedSort.value = sortValue;
    }
    const thumbnailValue =
      typeof data.thumbnail_size === "number"
        ? data.thumbnail_size
        : typeof data.thumbnail === "number"
        ? data.thumbnail
        : null;
    if (thumbnailValue !== null) {
      thumbnailSize.value = thumbnailValue;
      await nextTick();
    }
    if (typeof data.show_stars === "boolean") showStars.value = data.show_stars;
    config.sort_order = sortValue || selectedSort.value;
    config.thumbnail_size = thumbnailValue || thumbnailSize.value;
    config.show_stars =
      typeof data.show_stars === "boolean" ? data.show_stars : showStars.value;
    config.openai_host = data.openai_host || "localhost";
    config.openai_port = data.openai_port || 8000;
    config.openai_model = data.openai_model || "";
    config.default_device = data.default_device || "cpu";
  } catch (e) {
    console.error("Error fetching /config:", e);
  }
}

async function addImageRoot() {
  const val = newImageRoot.value.trim();
  if (!val || config.image_roots.includes(val)) return;
  config.image_roots.push(val);
  newImageRoot.value = "";
  await fetch(`${BACKEND_URL}/config`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ image_roots: config.image_roots }),
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
  await fetch(`${BACKEND_URL}/config`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ selected_image_root: config.selected_image_root }),
  });
  await fetchConfig();
  selectedCharacter.value = ALL_PICTURES_ID;
  selectedSet.value = null;
  refreshSidebar();
  if (currentView.value === "grid") {
    refreshGrid();
  } else if (currentView.value === "likeness") {
    refreshLikeness();
  }
}

async function patchConfigUIOptions(opts = {}) {
  const patch = {
    sort: selectedSort.value,
    thumbnail: thumbnailSize.value,
    show_stars: showStars.value,
    openai_host: config.openai_host,
    openai_port: config.openai_port,
    openai_model: config.openai_model,
    default_device: config.default_device,
    ...opts,
  };
  Object.assign(config, patch);
  await fetch(`${BACKEND_URL}/config`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
}

function selectImageRoot(root) {
  if (config.selected_image_root !== root) {
    config.selected_image_root = root;
    updateSelectedRoot();
  }
}

async function saveConfig() {
  await fetch(`/${BACKEND_URL}/config`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      image_roots: config.image_roots,
      selected_image_root: config.selected_image_root,
    }),
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

// --- Chat Overlay ---
function openChatOverlay() {
  chatOpen.value = true;
  nextTick(() => {
    if (chatWindowRef.value) chatWindowRef.value.focusInput();
  });
}

function closeChatOverlay() {
  chatOpen.value = false;
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

// --- Watchers ---
// Scroll to bottom after END loads last page
// (Removed watch on images)

// (Removed watch on selectedSort, selectedCharacter, selectedSet for image loading)

watch(searchQuery, (newVal, oldVal) => {
  if (!newVal && oldVal) {
    if (previousSort.value && previousSort.value !== selectedSort.value) {
      selectedSort.value = previousSort.value;
    }
    refreshGrid();
  }
});

watch(settingsDialog, (val) => {
  if (val) fetchConfig();
});

watch(selectedSort, (val) => {
  patchConfigUIOptions({ sort: val });
});

watch(thumbnailSize, (val) => {
  patchConfigUIOptions({ thumbnail: val });
});

watch(showStars, (val) => {
  patchConfigUIOptions({ show_stars: val });
});

watch(
  () => config.openai_host,
  (val) => {
    patchConfigUIOptions({ openai_host: val });
  }
);
watch(
  () => config.openai_port,
  (val) => {
    patchConfigUIOptions({ openai_port: val });
  }
);
watch(
  () => config.openai_model,
  (val) => {
    patchConfigUIOptions({ openai_model: val });
  }
);
// Watch for default_device changes
watch(
  () => config.default_device,
  (val) => {
    patchConfigUIOptions({ default_device: val });
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

onMounted(() => {
  fetchConfig();
  window.addEventListener("keydown", handleGlobalKeydown);
});

onBeforeUnmount(() => {
  window.removeEventListener("keydown", handleGlobalKeydown);
});

defineExpose({ currentView, sidebarVisible });
</script>
<template src="./App.template.html"></template>
<style scoped src="./App.css"></style>
