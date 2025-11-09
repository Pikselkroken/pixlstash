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

import SideBar from "./components/SideBar.vue";
import ChatWindow from "./components/ChatWindow.vue";
import ImageImporter from "./components/ImageImporter.vue";
import ImageOverlay from "./components/ImageOverlay.vue";
import CharacterEditor from "./components/CharacterEditor.vue";
import { useOverlayActions } from "./composables/useOverlayActions";

// --- Backend Constants & Identifiers ---
const BACKEND_URL = "http://localhost:9537";
const ALL_PICTURES_ID = "__all__";
const UNASSIGNED_PICTURES_ID = "__unassigned__";

// --- Supported Media Extensions ---
const PIL_IMAGE_EXTENSIONS = [
  "jpg",
  "jpeg",
  "png",
  "bmp",
  "gif",
  "tiff",
  "tif",
  "webp",
  "ppm",
  "pgm",
  "pbm",
  "pnm",
  "ico",
  "icns",
  "svg",
  "dds",
  "msp",
  "pcx",
  "xbm",
  "im",
  "fli",
  "flc",
  "eps",
  "psd",
  "pdf",
  "jp2",
  "j2k",
  "jpf",
  "jpx",
  "j2c",
  "jpc",
  "tga",
  "ras",
  "sgi",
  "rgb",
  "rgba",
  "bw",
  "exr",
  "hdr",
  "pic",
  "pict",
  "pct",
  "cur",
  "emf",
  "wmf",
  "heic",
  "heif",
  "avif",
];
const VIDEO_EXTENSIONS = [
  "mp4",
  "avi",
  "mov",
  "webm",
  "mkv",
  "flv",
  "wmv",
  "m4v",
];

// --- Template & Component Refs ---
const gridContainer = ref(null);
const imageImporterRef = ref(null);
const chatWindowRef = ref(null);

// --- Drag-and-Drop State ---
const dragOverlayVisible = ref(false);
const dragOverlayMessage = ref("");
const dragSource = ref(null);

// --- Pagination & Sorting State ---
const sortOptions = ref([]);
const selectedSort = ref("");
const previousSort = ref("");
const pageSize = ref(100);
const pageOffset = ref(0);
const hasMoreImages = ref(true);

// --- Character & Sidebar State ---
const selectedCharacter = ref(ALL_PICTURES_ID);
const characters = ref([]);
const categoryCounts = ref({
  [ALL_PICTURES_ID]: 0,
  [UNASSIGNED_PICTURES_ID]: 0,
});
const characterThumbnails = ref({});
const expandedCharacters = ref({});
const sidebarSections = ref({
  pictures: true,
  people: true,
  sets: true,
  search: true,
});
const dragOverCharacter = ref(null);
const nextCharacterNumber = ref(1);
const editingCharacterId = ref(null);
const editingCharacterName = ref("");

// --- Picture Sets State ---
const pictureSets = ref([]);
const selectedSet = ref(null);

// --- Image Grid State ---
const images = ref([]);
const imagesLoading = ref(false);
const imagesError = ref(null);
const thumbLoaded = reactive({});
const thumbnailSize = ref(256);
const columns = ref(5);
const sidebarVisible = ref(true);
const selectedImageIds = ref([]);
let lastSelectedIndex = null;

// --- Overlay & Tag State ---
const overlayOpen = ref(false);
const overlayImage = ref(null);

// --- Chat Overlay State ---
const chatOpen = ref(false);

// --- Character Editor State ---
const characterEditorOpen = ref(false);
const characterEditorCharacter = ref(null);

// --- Search & Filtering State ---
const searchQuery = ref("");
const showStars = ref(true);

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
});
const openaiModels = ref([]);
const openaiModelFetchError = ref("");
const openaiModelLoading = ref(false);
const newImageRoot = ref("");

// --- Miscellaneous Status Flags ---
const loading = ref(false);
const error = ref(null);

// --- Computed Collections ---
const filteredImages = computed(() => {
  return images.value;
});

const pagedImages = computed(() => filteredImages.value);

const sortedCharacters = computed(() => {
  return [...characters.value]
    .filter((c) => c && typeof c.name === "string" && c.name.trim() !== "")
    .sort((a, b) =>
      a.name.localeCompare(b.name, undefined, { sensitivity: "base" })
    );
});

const selectedCharacterObj = computed(() => {
  if (
    selectedCharacter.value &&
    selectedCharacter.value !== ALL_PICTURES_ID &&
    selectedCharacter.value !== UNASSIGNED_PICTURES_ID
  ) {
    const char =
      characters.value.find((c) => c.id === selectedCharacter.value) || null;
    if (char && typeof char.name === "string" && char.name.length > 0) {
      return {
        ...char,
        name: char.name.charAt(0).toUpperCase() + char.name.slice(1),
      };
    }
    return char;
  }
  return null;
});

// --- Selection Helpers ---
const isImageSelected = (id) =>
  selectedImageIds.value.includes(id) &&
  pagedImages.value.some((img) => img.id === id);

const getSelectionBorderClasses = (idx) => {
  const sorted = pagedImages.value;
  if (!isImageSelected(sorted[idx]?.id)) return "";
  const cols = columns.value;
  const total = sorted.length;
  const row = Math.floor(idx / cols);
  const col = idx % cols;
  const classes = [];
  if (row === 0 || !isImageSelected(sorted[(row - 1) * cols + col]?.id)) {
    classes.push("selected-border-top");
  }
  if (
    row === Math.floor((total - 1) / cols) ||
    !isImageSelected(sorted[(row + 1) * cols + col]?.id)
  ) {
    classes.push("selected-border-bottom");
  }
  if (col === 0 || !isImageSelected(sorted[row * cols + (col - 1)]?.id)) {
    classes.push("selected-border-left");
  }
  if (
    col === cols - 1 ||
    !isImageSelected(sorted[row * cols + (col + 1)]?.id)
  ) {
    classes.push("selected-border-right");
  }
  return classes.join(" ");
};

// --- Text & Display Utilities ---
function formatLikenessScore(score) {
  if (typeof score !== "number") return "";
  return `Likeness: ${(score * 100).toFixed(2)}%`;
}

function extractKeywords(text) {
  const doc = nlp(text);
  const nouns = doc.nouns().out("array");
  const adjectives = doc.adjectives().out("array");
  const keywords = Array.from(new Set([...nouns, ...adjectives]));
  return keywords.join(" ");
}

function getOverlayFormat(overlayImage) {
  if (!overlayImage) return "";
  if (overlayImage.format) return overlayImage.format;
  if (overlayImage.filename) {
    return overlayImage.filename.split(".").pop().toLowerCase();
  }
  if (overlayImage.url) {
    return overlayImage.url.split(".").pop().toLowerCase();
  }
  if (overlayImage.id) {
    return overlayImage.id.split(".").pop().toLowerCase();
  }
  return "png";
}

// --- Media Helpers ---
function isSupportedImageFile(file) {
  const ext = file.name.split(".").pop().toLowerCase();
  return PIL_IMAGE_EXTENSIONS.includes(ext);
}

function isSupportedVideoFile(input) {
  let ext = "";
  if (typeof input === "string") {
    ext = input.toLowerCase();
  } else if (input && input.name) {
    ext = input.name.split(".").pop().toLowerCase();
  }
  return VIDEO_EXTENSIONS.includes(ext);
}

function isSupportedMediaFile(file) {
  return isSupportedImageFile(file) || isSupportedVideoFile(file);
}

function dataTransferHasSupportedMedia(dataTransfer) {
  if (!dataTransfer) return false;
  const items = dataTransfer.items ? Array.from(dataTransfer.items) : [];
  for (let i = 0; i < Math.min(items.length, 10); i++) {
    const item = items[i];
    if (!item || item.kind !== "file") continue;
    const mime = item.type || "";
    if (mime.startsWith("image/") || mime.startsWith("video/")) {
      return true;
    }
    if (!mime && typeof item.getAsFile === "function") {
      const file = item.getAsFile();
      if (file && isSupportedMediaFile(file)) {
        return true;
      }
    }
  }
  if (items.length === 0) {
    const types = dataTransfer.types ? Array.from(dataTransfer.types) : [];
    if (types.includes("Files")) {
      return true;
    }
  }
  return false;
}

// --- Grid Layout Helpers ---
function updateColumns() {
  if (!gridContainer.value) return;
  const containerWidth = gridContainer.value.offsetWidth;
  columns.value = Math.max(
    1,
    Math.floor(containerWidth / (thumbnailSize.value + 32))
  );
}

// --- Sorting & Pagination ---
async function fetchSortOptions() {
  try {
    const res = await fetch(`${BACKEND_URL}/sort_mechanisms`);
    if (!res.ok) throw new Error("Failed to fetch sort mechanisms");
    const options = await res.json();
    sortOptions.value = options.map((opt) => ({
      label: opt.label,
      value: opt.id,
    }));
    if (!selectedSort.value && options.length) {
      selectedSort.value =
        options.find((o) => o.id === "unsorted")?.id || options[0].id;
    }
  } catch (e) {
    sortOptions.value = [
      { label: "Date: Latest First", value: "date_desc" },
      { label: "Date: Oldest First", value: "date_asc" },
      { label: "Score: Highest First", value: "score_desc" },
      { label: "Score: Lowest First", value: "score_asc" },
      { label: "Search Likeness", value: "search_likeness" },
    ];
    if (!selectedSort.value) selectedSort.value = "date_desc";
  }
}

async function refreshImages(append = false) {
  if (!append) {
    images.value = [];
    hasMoreImages.value = true;
    selectedImageIds.value = [];
  }
  imagesError.value = null;
  const id = selectedCharacter.value;
  if (!id) return;
  imagesLoading.value = true;
  try {
    let url;
    const params = new URLSearchParams();
    params.set("info", "true");
    params.set("sort", selectedSort.value || "date_desc");
    params.set("offset", String(pageOffset.value));
    params.set("limit", String(pageSize.value));
    if (id === ALL_PICTURES_ID) {
      url = `${BACKEND_URL}/pictures?${params.toString()}`;
    } else if (id === UNASSIGNED_PICTURES_ID) {
      url = `${BACKEND_URL}/pictures?primary_character_id=&${params.toString()}`;
    } else {
      url = `${BACKEND_URL}/pictures?primary_character_id=${encodeURIComponent(
        id
      )}&${params.toString()}`;
    }
    const res = await fetch(url);
    if (!res.ok) throw new Error("Failed to fetch images");
    let baseImages = await res.json();
    const newImages = baseImages.map((img) => ({
      ...img,
      score: typeof img.score !== "undefined" ? img.score : null,
    }));
    images.value = append ? [...images.value, ...newImages] : newImages;
    hasMoreImages.value = newImages.length === pageSize.value;
    setTimeout(updateColumns, 0);
  } catch (e) {
    imagesError.value = e.message;
  } finally {
    imagesLoading.value = false;
  }
}

// --- Sidebar & Character Data ---
async function fetchSidebarCounts() {
  try {
    const resAll = await fetch(`${BACKEND_URL}/category/summary`);
    if (resAll.ok) {
      const data = await resAll.json();
      categoryCounts.value[ALL_PICTURES_ID] = data.image_count;
    }
  } catch {}
  try {
    const resUnassigned = await fetch(
      `${BACKEND_URL}/category/summary?primary_character_id=null`
    );
    if (resUnassigned.ok) {
      const data = await resUnassigned.json();
      categoryCounts.value[UNASSIGNED_PICTURES_ID] = data.image_count;
    }
  } catch {}
  await Promise.all(
    characters.value.map(async (char) => {
      try {
        const res = await fetch(
          `${BACKEND_URL}/category/summary?primary_character_id=${encodeURIComponent(
            char.id
          )}`
        );
        if (res.ok) {
          const data = await res.json();
          categoryCounts.value[char.id] = data.image_count;
        }
      } catch {}
    })
  );
}

async function fetchCharacters() {
  loading.value = true;
  error.value = null;
  try {
    const res = await fetch(`${BACKEND_URL}/characters`);
    if (!res.ok) throw new Error("Failed to fetch characters");
    const chars = await res.json();
    characters.value = chars;
    for (const char of chars) {
      fetchCharacterThumbnail(char.id);
    }
    await fetchSidebarCounts();
  } catch (e) {
    error.value = e.message;
  } finally {
    loading.value = false;
  }
}

async function fetchCharacterThumbnail(characterId) {
  try {
    const cacheBuster = Date.now();
    const thumbUrl = `${BACKEND_URL}/face_thumbnail/${characterId}?cb=${cacheBuster}`;
    const res = await fetch(thumbUrl);
    if (res.ok && res.headers.get("content-type")?.includes("image/png")) {
      characterThumbnails.value[characterId] = thumbUrl;
    } else {
      characterThumbnails.value[characterId] = null;
    }
  } catch (e) {
    characterThumbnails.value[characterId] = null;
  }
}

function toggleSidebarSection(section) {
  if (!section || !(section in sidebarSections.value)) return;
  sidebarSections.value[section] = !sidebarSections.value[section];
}

// --- Picture Sets ---
async function fetchPictureSets() {
  try {
    const res = await fetch(`${BACKEND_URL}/picture_sets`);
    if (!res.ok) throw new Error("Failed to fetch picture sets");
    pictureSets.value = await res.json();
  } catch (e) {
    console.error("Error fetching picture sets:", e);
  }
}

async function handleSelectSet(setId) {
  selectedSet.value = setId;
  selectedCharacter.value = null; // Clear character selection

  try {
    // Fetch the set with pictures (no ?info=true means we get the pictures)
    const res = await fetch(`${BACKEND_URL}/picture_sets/${setId}`);
    if (!res.ok) throw new Error("Failed to fetch set details");
    const data = await res.json();

    // The response now includes pictures directly
    if (data.pictures && data.pictures.length > 0) {
      images.value = data.pictures.map((img) => ({
        ...img,
        score: typeof img.score !== "undefined" ? img.score : null,
      }));
    } else {
      images.value = [];
    }

    hasMoreImages.value = false;
    await nextTick();
    updateColumns();
  } catch (e) {
    console.error("Error loading set pictures:", e);
    images.value = [];
  }
}

async function handleCreateSet() {
  const name = prompt("Enter set name:");
  if (!name || !name.trim()) return;

  try {
    const res = await fetch(`${BACKEND_URL}/picture_sets`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.trim(), description: "" }),
    });

    if (!res.ok) throw new Error("Failed to create set");
    await fetchPictureSets();
  } catch (e) {
    alert("Failed to create set: " + (e.message || e));
  }
}

async function handleDeleteSet() {
  if (!selectedSet.value) return;

  const setToDelete = pictureSets.value.find((s) => s.id === selectedSet.value);
  if (!setToDelete) return;

  if (!confirm(`Delete picture set "${setToDelete.name}"?`)) return;

  try {
    const res = await fetch(
      `${BACKEND_URL}/picture_sets/${selectedSet.value}`,
      {
        method: "DELETE",
      }
    );

    if (!res.ok) throw new Error("Failed to delete set");

    selectedSet.value = null;
    images.value = [];
    await fetchPictureSets();
  } catch (e) {
    alert("Failed to delete set: " + (e.message || e));
  }
}

async function removeSelectedFromSet() {
  if (!selectedSet.value || selectedImageIds.value.length === 0) return;

  const setToUpdate = pictureSets.value.find((s) => s.id === selectedSet.value);
  if (!setToUpdate) return;

  try {
    // Remove each selected image from the set
    const removePromises = selectedImageIds.value.map(async (picId) => {
      const res = await fetch(
        `${BACKEND_URL}/picture_sets/${selectedSet.value}/pictures/${picId}`,
        { method: "DELETE" }
      );
      if (!res.ok) throw new Error(`Failed to remove image ${picId}`);
    });

    await Promise.all(removePromises);

    // Remove from local images array
    images.value = images.value.filter(
      (img) => !selectedImageIds.value.includes(img.id)
    );
    selectedImageIds.value = [];

    // Refresh the picture sets to update counts
    await fetchPictureSets();

    setTimeout(updateColumns, 0);
  } catch (e) {
    alert("Failed to remove images from set: " + (e.message || e));
    // Refresh the view to ensure consistency
    handleSelectSet(selectedSet.value);
  }
}

async function removeSelectedFromCharacter() {
  if (!selectedCharacter.value || selectedImageIds.value.length === 0) return;
  if (
    selectedCharacter.value === ALL_PICTURES_ID ||
    selectedCharacter.value === UNASSIGNED_PICTURES_ID
  )
    return;

  const character = characters.value.find(
    (c) => c.id === selectedCharacter.value
  );
  if (!character) return;

  try {
    // Update each selected image to clear primary_character_id
    const updatePromises = selectedImageIds.value.map(async (picId) => {
      const res = await fetch(`${BACKEND_URL}/pictures/${picId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ primary_character_id: null }),
      });
      if (!res.ok)
        throw new Error(`Failed to update image ${picId}: ${res.status}`);
    });

    await Promise.all(updatePromises);

    // Remove from local images array
    images.value = images.value.filter(
      (img) => !selectedImageIds.value.includes(img.id)
    );
    selectedImageIds.value = [];

    // Refresh character counts
    await fetchSidebarCounts();

    setTimeout(updateColumns, 0);
  } catch (e) {
    console.error("Error removing images:", e);
    alert("Failed to remove images from character: " + (e.message || e));
    // Refresh the view to ensure consistency
    refreshImages();
  }
}

async function handleDropOnSet({ setId, event }) {
  // Get the dragged image IDs from the drag event
  let draggedIds = [];
  try {
    const data = JSON.parse(event.dataTransfer.getData("application/json"));
    if (data.imageIds && Array.isArray(data.imageIds)) {
      draggedIds = data.imageIds;
    }
  } catch (e) {
    console.error("Could not parse drag data:", e);
    return;
  }

  if (draggedIds.length === 0) {
    console.log("No images found in drag data");
    return;
  }

  const targetSet = pictureSets.value.find((s) => s.id === setId);
  if (!targetSet) return;

  try {
    // Add each image to the set
    const addPromises = draggedIds.map(async (picId) => {
      const res = await fetch(
        `${BACKEND_URL}/picture_sets/${setId}/pictures/${picId}`,
        { method: "POST" }
      );
      // 400 error might mean it's already in the set, which is ok
      if (!res.ok && res.status !== 400) {
        throw new Error(`Failed to add image ${picId}`);
      }
    });

    await Promise.all(addPromises);

    // Refresh the picture sets to update counts
    await fetchPictureSets();

    console.log(
      `Added ${draggedIds.length} image(s) to set "${targetSet.name}"`
    );
  } catch (e) {
    alert("Failed to add images to set: " + (e.message || e));
  }
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
      updateColumns();
    }
    if (typeof data.show_stars === "boolean") showStars.value = data.show_stars;
    config.sort_order = sortValue || selectedSort.value;
    config.thumbnail_size = thumbnailValue || thumbnailSize.value;
    config.show_stars =
      typeof data.show_stars === "boolean" ? data.show_stars : showStars.value;
    config.openai_host = data.openai_host || "localhost";
    config.openai_port = data.openai_port || 8000;
    config.openai_model = data.openai_model || "";
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
  await fetchCharacters();
  await fetchSidebarCounts();
  await refreshImages();
}

async function patchConfigUIOptions(opts = {}) {
  const patch = {
    sort: selectedSort.value,
    thumbnail: thumbnailSize.value,
    show_stars: showStars.value,
    openai_host: config.openai_host,
    openai_port: config.openai_port,
    openai_model: config.openai_model,
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

// --- Image Import & Grid Interaction ---
function handleGridDragEnter(e) {
  if (
    e.relatedTarget &&
    gridContainer.value &&
    gridContainer.value.contains(e.relatedTarget)
  )
    return;
  if (!e.dataTransfer) return;
  const hasSupported = dataTransferHasSupportedMedia(e.dataTransfer);
  if (!hasSupported) return;
  dragOverlayVisible.value = true;
  dragOverlayMessage.value = "Drop files here to import";
  e.preventDefault();
  console.debug("Overlay shown");
}

function handleGridDragOver(e) {
  if (dataTransferHasSupportedMedia(e.dataTransfer)) {
    if (!dragOverlayVisible.value) {
      dragOverlayVisible.value = true;
      dragOverlayMessage.value = "Drop files here to import";
    }
    e.preventDefault();
  }
}

function handleGridDragLeave(e) {
  if (!e.relatedTarget || !e.currentTarget.contains(e.relatedTarget)) {
    dragOverlayVisible.value = false;
  } else {
    console.debug("Drag still inside grid, overlay remains");
  }
}

function handleGridDrop(e) {
  dragOverlayVisible.value = false;
  if (dragSource.value === "grid") {
    dragSource.value = null;
    return;
  }
  if (!e.dataTransfer || !e.dataTransfer.files) return;
  const files = Array.from(e.dataTransfer.files).filter(isSupportedMediaFile);
  console.debug("[IMPORT] Files dropped:", e.dataTransfer.files);
  console.debug("[IMPORT] Supported files after filter:", files);
  if (!files.length) {
    alert("No supported image files found.");
    return;
  }
  dragSource.value = null;
  if (
    !imageImporterRef.value ||
    typeof imageImporterRef.value.startImport !== "function"
  ) {
    console.warn("ImageImporter component is not ready to handle imports.");
    return;
  }
  imageImporterRef.value.startImport(files, {
    selectedCharacterId: selectedCharacter.value,
  });
}

function handleImportFinished() {
  refreshImages();
  fetchSidebarCounts();
}

function handleGridBackgroundClick(e) {
  if (!e.target.closest(".thumbnail-card")) {
    selectedImageIds.value = [];
    lastSelectedIndex = null;
  }
}

function onGridScroll(e) {
  const el = e.target;
  if (!hasMoreImages.value || imagesLoading.value) return;
  if (el.scrollTop + el.clientHeight >= el.scrollHeight - 200) {
    pageOffset.value += pageSize.value;
    refreshImages(true);
  }
}

// --- Overlay & Tag Editing ---
function openOverlay(img) {
  overlayImage.value = img;
  overlayOpen.value = true;
}

function closeOverlay() {
  overlayOpen.value = false;
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

// --- Search ---
async function searchImages(query) {
  const q = (typeof query === "string" ? query : searchQuery.value).trim();
  if (!q) return;
  searchQuery.value = q;
  previousSort.value = selectedSort.value;
  const likenessSort = sortOptions.value.find(
    (opt) =>
      (opt.value && opt.value.toLowerCase().includes("search")) ||
      (opt.label && opt.label.toLowerCase().includes("search"))
  );
  if (likenessSort) {
    selectedSort.value = likenessSort.value;
  }
  imagesLoading.value = true;
  imagesError.value = null;
  try {
    const url = `${BACKEND_URL}/search?query=${encodeURIComponent(
      q
    )}&threshold=0.5&top_n=1000`;
    const res = await fetch(url);
    if (!res.ok) throw new Error("Search failed");
    const baseImages = await res.json();
    images.value = baseImages.map((img) => ({
      ...img,
      score: typeof img.score !== "undefined" ? img.score : null,
    }));
    setTimeout(updateColumns, 0);
  } catch (e) {
    imagesError.value = e.message;
  } finally {
    imagesLoading.value = false;
  }
}

// --- Selection & Keyboard Handling ---
function handleImageSelect(img, idx, event) {
  const sorted = pagedImages.value;
  const id = img.id;
  const isSelected = selectedImageIds.value.includes(id);
  const isCtrl = event.ctrlKey || event.metaKey;
  const isShift = event.shiftKey;
  if (isShift) {
    if (lastSelectedIndex !== null) {
      const start = Math.min(lastSelectedIndex, idx);
      const end = Math.max(lastSelectedIndex, idx);
      const rangeIds = sorted.slice(start, end + 1).map((i) => i.id);
      const newSelection = isCtrl
        ? Array.from(new Set([...selectedImageIds.value, ...rangeIds]))
        : rangeIds;
      selectedImageIds.value = newSelection;
    } else {
      selectedImageIds.value = [id];
    }
    lastSelectedIndex = idx;
  } else if (isCtrl) {
    if (isSelected) {
      selectedImageIds.value = selectedImageIds.value.filter((i) => i !== id);
    } else {
      selectedImageIds.value = [...selectedImageIds.value, id];
    }
    lastSelectedIndex = idx;
  } else {
    selectedImageIds.value = [id];
    lastSelectedIndex = idx;
  }
}

async function selectAllInCurrentView() {
  try {
    const id = selectedCharacter.value;
    if (searchQuery.value && searchQuery.value.trim()) {
      const q = searchQuery.value.trim();
      const url = `${BACKEND_URL}/search?query=${encodeURIComponent(
        q
      )}&threshold=0.5&top_n=10000`;
      const res = await fetch(url);
      if (!res.ok) throw new Error("Failed to fetch search results");
      const results = await res.json();
      selectedImageIds.value = results.map((img) => img.id);
      return;
    }
    let url;
    const params = new URLSearchParams();
    params.set("sort", selectedSort.value || "date_desc");
    if (id === ALL_PICTURES_ID) {
      url = `${BACKEND_URL}/picture_ids?${params.toString()}`;
    } else if (id === UNASSIGNED_PICTURES_ID) {
      url = `${BACKEND_URL}/picture_ids?primary_character_id=&${params.toString()}`;
    } else {
      url = `${BACKEND_URL}/picture_ids?primary_character_id=${encodeURIComponent(
        id
      )}&${params.toString()}`;
    }
    const res = await fetch(url);
    if (!res.ok) throw new Error("Failed to fetch picture IDs");
    const ids = await res.json();
    selectedImageIds.value = ids;
  } catch (e) {
    console.error("Failed to select all images:", e);
    alert("Failed to select all images: " + (e.message || e));
  }
}

function handleOverlayKeydown(e) {
  const tag =
    e.target && e.target.tagName ? e.target.tagName.toLowerCase() : "";
  const isEditable =
    e.target &&
    (e.target.isContentEditable || tag === "input" || tag === "textarea");
  if (isEditable && !(chatOpen.value && e.key === "Escape")) return;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a") {
    e.preventDefault();
    selectAllInCurrentView();
    return;
  }
  if (overlayOpen.value) {
    if (e.key === "ArrowLeft") {
      showPrevImage();
      e.preventDefault();
      return;
    } else if (e.key === "ArrowRight") {
      showNextImage();
      e.preventDefault();
      return;
    } else if (e.key === "Escape") {
      closeOverlay();
      e.preventDefault();
      return;
    }
  }
  if (chatOpen.value && e.key === "Escape") {
    closeChatOverlay();
    e.preventDefault();
    return;
  }
  if (e.key === "Escape" && selectedImageIds.value.length) {
    selectedImageIds.value = [];
    e.preventDefault();
    return;
  }
  if (!images.value.length) return;
  const cols = columns.value;
  let idx = lastSelectedIndex;
  if (idx === null || idx < 0 || idx >= images.value.length) idx = 0;
  let nextIdx = idx;
  if (e.key === "ArrowLeft") {
    if (idx % cols > 0) nextIdx = idx - 1;
    else return;
  } else if (e.key === "ArrowRight") {
    if (idx % cols < cols - 1 && idx + 1 < images.value.length)
      nextIdx = idx + 1;
    else return;
  } else if (e.key === "ArrowUp") {
    if (idx - cols >= 0) nextIdx = idx - cols;
    else return;
  } else if (e.key === "ArrowDown") {
    if (idx + cols < images.value.length) nextIdx = idx + cols;
    else return;
  } else if (e.key === "Delete") {
    if (selectedImageIds.value.length) {
      deleteSelectedImages();
      e.preventDefault();
      return;
    }
  }
  if (/^[1-5]$/.test(e.key)) {
    showStars.value = true;
    if (overlayOpen.value && overlayImage.value) {
      setImageScore(overlayImage.value, Number(e.key));
    } else if (selectedImageIds.value.length) {
      patchScoreForSelection(Number(e.key));
    }
    e.preventDefault();
    return;
  }
  return;
}

function showPrevImage() {
  const sorted = pagedImages.value;
  if (!overlayImage.value || !sorted.length) return;
  const idx = sorted.findIndex((i) => i.id === overlayImage.value.id);
  if (idx === -1) return;
  const prevIdx = (idx - 1 + sorted.length) % sorted.length;
  overlayImage.value = sorted[prevIdx];
}

function showNextImage() {
  const sorted = pagedImages.value;
  if (!overlayImage.value || !sorted.length) return;
  const idx = sorted.findIndex((i) => i.id === overlayImage.value.id);
  if (idx === -1) return;
  const nextIdx = (idx + 1) % sorted.length;
  overlayImage.value = sorted[nextIdx];
}

// --- Image Mutations ---
async function deleteSelectedImages() {
  if (!selectedImageIds.value.length) return;
  const confirmed = confirm(
    `Delete ${selectedImageIds.value.length} selected image(s)? This cannot be undone.`
  );
  if (!confirmed) return;
  for (const id of selectedImageIds.value) {
    try {
      const res = await fetch(`${BACKEND_URL}/pictures/${id}`, {
        method: "DELETE",
      });
      if (!res.ok) throw new Error(`Failed to delete image ${id}`);
    } catch (e) {
      alert(e.message);
    }
  }
  images.value = images.value.filter(
    (img) => !selectedImageIds.value.includes(img.id)
  );
  selectedImageIds.value = [];
  fetchSidebarCounts();
}

async function patchScoreForSelection(score) {
  if (!selectedImageIds.value.length) return;
  for (const id of selectedImageIds.value) {
    try {
      const res = await fetch(`${BACKEND_URL}/pictures/${id}?score=${score}`, {
        method: "PATCH",
      });
      if (!res.ok) throw new Error(`Failed to set score for image ${id}`);
      const result = await res.json();
      const img = images.value.find((i) => i.id === id);
      if (img) img.score = score;
    } catch (e) {
      alert(e.message);
    }
  }
}

async function setImageScore(img, n) {
  const newScore = (img.score || 0) === n ? 0 : n;
  try {
    const res = await fetch(
      `${BACKEND_URL}/pictures/${img.id}?score=${newScore}`,
      { method: "PATCH" }
    );
    if (!res.ok) throw new Error(`Failed to set score for image ${img.id}`);
    if (
      selectedSort.value === "score_desc" ||
      selectedSort.value === "score_asc"
    ) {
      const idx = images.value.findIndex((i) => i.id === img.id);
      if (idx === -1) return;
      img.score = newScore;
      images.value.splice(idx, 1);
      let insertIdx = 0;
      if (selectedSort.value === "score_desc") {
        insertIdx = images.value.findIndex((i) => (i.score || 0) < newScore);
        if (insertIdx === -1) insertIdx = images.value.length;
      } else {
        insertIdx = images.value.findIndex((i) => (i.score || 0) > newScore);
        if (insertIdx === -1) insertIdx = images.value.length;
      }
      images.value.splice(insertIdx, 0, img);
      nextTick(() => {
        const grid = gridContainer.value;
        if (!grid) return;
        const card = grid.querySelectorAll(".image-card")[insertIdx];
        if (card && card.scrollIntoView) {
          card.scrollIntoView({ behavior: "smooth", block: "center" });
        }
      });
    } else {
      img.score = newScore;
    }
  } catch (e) {
    alert(e.message);
  }
}

// --- Character Assignment ---
function handleSelectCharacter(id) {
  selectedCharacter.value = id;
  selectedSet.value = null; // Clear set selection when selecting a character
}

function handleDragOverCharacter(id) {
  dragOverCharacter.value = id;
}

function handleDragLeaveCharacter() {
  dragOverCharacter.value = null;
}

function handleDropOnCharacter(payload) {
  if (!payload || !payload.characterId) return;
  onCharacterDrop(payload.characterId, payload.event);
}

function handleUpdateSearchQuery(value) {
  searchQuery.value = value;
}

function handleUpdateSelectedSort(value) {
  selectedSort.value = value;
}

function onImageDragStart(img, idx, event) {
  const ids =
    selectedImageIds.value.length && isImageSelected(img.id)
      ? selectedImageIds.value
      : [img.id];
  event.dataTransfer.setData(
    "application/json",
    JSON.stringify({ imageIds: ids })
  );
  event.dataTransfer.effectAllowed = "move";
  dragSource.value = "grid";
}

async function onCharacterDrop(characterId, event) {
  let imageIds = [];
  try {
    const data = JSON.parse(event.dataTransfer.getData("application/json"));
    if (data.imageIds && Array.isArray(data.imageIds)) {
      imageIds = data.imageIds;
    }
  } catch (e) {
    alert("Could not determine which images to assign. Please try again.");
    return;
  }
  if (!imageIds.length) {
    alert("No images found in drag data.");
    return;
  }
  const charObj = characters.value.find((c) => c.id === characterId);
  console.log(
    "[DROP] Drop target characterId:",
    characterId,
    "name:",
    charObj ? charObj.name : "(not found)"
  );
  assignImagesToCharacter(imageIds, characterId);
}

async function assignImagesToCharacter(imageIds, characterId) {
  try {
    await Promise.all(
      imageIds.map(async (id) => {
        const res = await fetch(
          `${BACKEND_URL}/pictures/${id}?primary_character_id=${encodeURIComponent(
            characterId
          )}`,
          { method: "PATCH" }
        );
        if (!res.ok)
          throw new Error(`Failed to assign character for image ${id}`);
      })
    );
    await fetchCharacters();
    fetchSidebarCounts();

    // If we're viewing a specific character (not the one being dropped to) or a set, just remove the images locally
    if (
      (selectedCharacter.value !== ALL_PICTURES_ID &&
        selectedCharacter.value !== UNASSIGNED_PICTURES_ID &&
        selectedCharacter.value !== characterId) ||
      selectedSet.value !== null
    ) {
      images.value = images.value.filter((img) => !imageIds.includes(img.id));
      selectedImageIds.value = selectedImageIds.value.filter((id) =>
        images.value.some((img) => img.id === id)
      );
      lastSelectedIndex = null;
      setTimeout(updateColumns, 0);
    } else {
      // Otherwise refresh the view properly with sorting
      refreshImages();
    }
  } catch (e) {
    alert("Failed to assign character: " + (e.message || e));
  }
}

// --- Character Management ---
function addNewCharacter() {
  // Open character editor with empty character to create new one
  let num = nextCharacterNumber.value;
  let name;
  const existingNames = new Set(characters.value.map((c) => c.name));
  do {
    name = `Character ${num}`;
    num++;
  } while (existingNames.has(name));
  nextCharacterNumber.value = num;

  // Open editor with default values
  openCharacterEditor({
    id: null,
    name: name,
    description: "",
    original_prompt: "",
    original_seed: null,
    loras: [],
  });
}

function updateEditingCharacterName(value) {
  editingCharacterName.value = typeof value === "string" ? value : "";
}

function startEditingCharacter(char) {
  editingCharacterId.value = char.id;
  editingCharacterName.value = char.name;
  nextTick(() => {
    const input = document.querySelector(".edit-character-input");
    if (input) {
      input.focus();
      input.select();
    }
  });
}

function saveEditingCharacter(char) {
  const newName = editingCharacterName.value.trim();
  if (newName && newName !== char.name) {
    fetch(`${BACKEND_URL}/characters/${char.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: newName }),
    })
      .then(async (res) => {
        if (!res.ok) throw new Error("Failed to update character");
        const data = await res.json();
        if (data && data.character) {
          char.name = data.character.name;
        }
      })
      .catch((e) => {
        alert("Failed to update character: " + (e.message || e));
      });
  }
  editingCharacterId.value = null;
  editingCharacterName.value = "";
}

function cancelEditingCharacter() {
  editingCharacterId.value = null;
  editingCharacterName.value = "";
}

// --- Character Editor Dialog Functions ---
function openCharacterEditor(char = null) {
  characterEditorCharacter.value = char;
  characterEditorOpen.value = true;
}

function closeCharacterEditor() {
  characterEditorOpen.value = false;
  characterEditorCharacter.value = null;
}

async function saveCharacterFromEditor(charData) {
  try {
    const isNew = !charData.id;
    const method = isNew ? "POST" : "PATCH";
    const url = isNew
      ? `${BACKEND_URL}/characters`
      : `${BACKEND_URL}/characters/${charData.id}`;

    const res = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(charData),
    });

    if (!res.ok) {
      const errorText = await res.text();
      throw new Error(errorText || "Failed to save character");
    }

    const data = await res.json();

    if (isNew && data.character) {
      // New character - add to list
      await fetchCharacters();
      selectedCharacter.value = data.character.id;
    } else if (data.character) {
      // Updated character - refresh the character in the list
      const idx = characters.value.findIndex((c) => c.id === data.character.id);
      if (idx !== -1) {
        characters.value[idx] = data.character;
      }
      await fetchCharacters();
    }

    closeCharacterEditor();
  } catch (e) {
    alert("Failed to save character: " + (e.message || e));
  }
}

function confirmDeleteCharacter() {
  const char = characters.value.find((c) => c.id === selectedCharacter.value);
  if (!char) return;
  if (
    window.confirm(
      `Delete character '${char.name}'? This will unassign all their images.`
    )
  ) {
    fetch(`${BACKEND_URL}/characters/${char.id}`, { method: "DELETE" })
      .then(async (res) => {
        if (!res.ok) throw new Error("Failed to delete character");
        characters.value = characters.value.filter((c) => c.id !== char.id);
        selectedCharacter.value = ALL_PICTURES_ID;
        images.value = [];
        await fetchCharacters();
      })
      .catch((e) => {
        alert("Failed to delete character: " + (e.message || e));
      });
  }
}

const { removeTagFromOverlayImage, addTagToOverlay, handleOverlaySetScore } =
  useOverlayActions({
    overlayImage,
    backendUrl: BACKEND_URL,
    setImageScore,
  });

// --- Watchers ---
watch([selectedSort, selectedCharacter, selectedSet], () => {
  if (searchQuery.value && searchQuery.value.trim()) {
    return;
  }
  // Don't refresh if a set is selected (set handles its own image loading)
  if (selectedSet.value !== null) {
    return;
  }
  pageOffset.value = 0;
  hasMoreImages.value = true;
  lastSelectedIndex = null;
  refreshImages();
});

watch(searchQuery, (newVal, oldVal) => {
  if (!newVal && oldVal) {
    if (previousSort.value && previousSort.value !== selectedSort.value) {
      selectedSort.value = previousSort.value;
    }
    refreshImages();
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
  updateColumns();
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

// --- Lifecycle ---
onMounted(() => {
  fetchConfig();
  fetchSortOptions();
  fetchCharacters();
  fetchPictureSets();
  window.addEventListener("resize", updateColumns);
  window.addEventListener("keydown", handleOverlayKeydown);
  setTimeout(updateColumns, 100);
});

onBeforeUnmount(() => {
  window.removeEventListener("keydown", handleOverlayKeydown);
  window.removeEventListener("resize", updateColumns);
});
</script>
<template src="./App.template.html"></template>
<style scoped src="./App.css"></style>
