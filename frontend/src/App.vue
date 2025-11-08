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
const selectedReferenceMode = ref(false);
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
  search: true,
});
const dragOverCharacter = ref(null);
const nextCharacterNumber = ref(1);
const editingCharacterId = ref(null);
const editingCharacterName = ref("");

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
const addingTagOverlay = ref(false);
const newTagOverlay = ref("");

// --- Chat Overlay State ---
const chatOpen = ref(false);

// --- Search & Filtering State ---
const searchQuery = ref("");
const showStars = ref(true);
const referenceFilterMode = ref(false);

// --- Config Dialog State ---
const settingsDialog = ref(false);
const config = reactive({
  image_roots: [],
  selected_image_root: "",
  sort: "",
  thumbnail: 256,
  show_stars: true,
  show_only_reference: false,
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
  if (referenceFilterMode.value) {
    return images.value.filter((img) => Number(img.is_reference) === 1);
  }
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
  const refMode = selectedReferenceMode.value;
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
      url = `${BACKEND_URL}/pictures?character_id=&${params.toString()}`;
    } else if (refMode) {
      url = `${BACKEND_URL}/characters/reference_pictures/${encodeURIComponent(
        id
      )}`;
    } else {
      url = `${BACKEND_URL}/pictures?character_id=${encodeURIComponent(
        id
      )}&${params.toString()}`;
    }
    const res = await fetch(url);
    if (!res.ok) throw new Error("Failed to fetch images");
    let baseImages = await res.json();
    if (refMode && baseImages.reference_pictures) {
      baseImages = baseImages.reference_pictures;
      baseImages = baseImages.map((img) => ({ ...img, id: img.picture_id }));
    }
    const newImages = baseImages.map((img) => ({
      ...img,
      score: typeof img.score !== "undefined" ? img.score : null,
      is_reference: Number(img.is_reference) || 0,
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
      `${BACKEND_URL}/category/summary?character_id=null`
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
          `${BACKEND_URL}/category/summary?character_id=${encodeURIComponent(
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
    if (typeof data.show_only_reference === "boolean")
      referenceFilterMode.value = data.show_only_reference;
    config.sort_order = sortValue || selectedSort.value;
    config.thumbnail_size = thumbnailValue || thumbnailSize.value;
    config.show_stars =
      typeof data.show_stars === "boolean" ? data.show_stars : showStars.value;
    config.show_only_reference =
      typeof data.show_only_reference === "boolean"
        ? data.show_only_reference
        : referenceFilterMode.value;
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
    show_only_reference: referenceFilterMode.value,
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

async function removeTagFromOverlayImage(tag) {
  if (!overlayImage.value) return;
  const img = overlayImage.value;
  const newTags = img.tags.filter((t) => t !== tag);
  try {
    const res = await fetch(`${BACKEND_URL}/pictures/${img.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tags: newTags }),
    });
    if (!res.ok) throw new Error("Failed to remove tag");
    img.tags = newTags;
  } catch (e) {
    alert("Failed to remove tag: " + (e.message || e));
  }
}

function startAddTagOverlay() {
  addingTagOverlay.value = true;
  newTagOverlay.value = "";
  nextTick(() => {
    const input = document.querySelector(".tag-add-input");
    if (input) input.focus();
  });
}

function cancelAddTagOverlay() {
  addingTagOverlay.value = false;
  newTagOverlay.value = "";
}

async function confirmAddTagOverlay() {
  if (!overlayImage.value) return;
  const tag = newTagOverlay.value.trim();
  if (!tag) {
    cancelAddTagOverlay();
    return;
  }
  if (overlayImage.value.tags.includes(tag)) {
    cancelAddTagOverlay();
    return;
  }
  const img = overlayImage.value;
  const newTags = [...img.tags, tag];
  try {
    const res = await fetch(`${BACKEND_URL}/pictures/${img.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tags: newTags }),
    });
    if (!res.ok) throw new Error("Failed to add tag");
    img.tags = newTags;
  } catch (e) {
    alert("Failed to add tag: " + (e.message || e));
  }
  cancelAddTagOverlay();
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
      is_reference: Number(img.is_reference) || 0,
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
    const refMode = selectedReferenceMode.value;
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
      url = `${BACKEND_URL}/picture_ids?character_id=&${params.toString()}`;
    } else if (refMode) {
      params.set("is_reference", "1");
      url = `${BACKEND_URL}/picture_ids?character_id=${encodeURIComponent(
        id
      )}&${params.toString()}`;
      const res = await fetch(url);
      if (!res.ok) throw new Error("Failed to fetch picture IDs");
      selectedImageIds.value = await res.json();
      return;
    } else {
      url = `${BACKEND_URL}/picture_ids?character_id=${encodeURIComponent(
        id
      )}&${params.toString()}`;
    }
    const res = await fetch(url);
    if (!res.ok) throw new Error("Failed to fetch picture IDs");
    const ids = await res.json();
    if (referenceFilterMode.value) {
      const fullUrl = `${BACKEND_URL}/pictures?${params.toString()}&is_reference=1${
        id !== ALL_PICTURES_ID ? `&character_id=${encodeURIComponent(id)}` : ""
      }`;
      const fullRes = await fetch(fullUrl);
      if (!fullRes.ok) throw new Error("Failed to fetch pictures");
      const pics = await fullRes.json();
      selectedImageIds.value = pics.map((pic) => pic.id);
    } else {
      selectedImageIds.value = ids;
    }
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
  if (e.key.toLowerCase() === "r" && !e.ctrlKey && !e.metaKey && !e.altKey) {
    if (overlayOpen.value && overlayImage.value) {
      toggleReference(overlayImage.value);
      e.preventDefault();
      return;
    } else if (selectedImageIds.value.length) {
      const lastImg = images.value.find(
        (i) =>
          i.id === selectedImageIds.value[selectedImageIds.value.length - 1]
      );
      if (lastImg) {
        toggleReference(lastImg);
        e.preventDefault();
        return;
      }
    }
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
          `${BACKEND_URL}/pictures/${id}?character_id=${encodeURIComponent(
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
    if (
      selectedCharacter.value !== ALL_PICTURES_ID &&
      selectedCharacter.value !== UNASSIGNED_PICTURES_ID &&
      selectedCharacter.value !== characterId
    ) {
      images.value = images.value.filter((img) => !imageIds.includes(img.id));
      selectedImageIds.value = selectedImageIds.value.filter((id) =>
        images.value.some((img) => img.id === id)
      );
      lastSelectedIndex = null;
    } else {
      const id = selectedCharacter.value;
      let url;
      if (id === ALL_PICTURES_ID) {
        url = `${BACKEND_URL}/pictures?info=true`;
      } else if (id === UNASSIGNED_PICTURES_ID) {
        url = `${BACKEND_URL}/pictures?character_id=&info=true`;
      } else {
        url = `${BACKEND_URL}/pictures?character_id=${encodeURIComponent(
          id
        )}&info=true`;
      }
      const res = await fetch(url);
      if (res.ok) {
        const baseImages = await res.json();
        images.value = baseImages.map((img) => ({
          ...img,
          score: typeof img.score !== "undefined" ? img.score : null,
          is_reference: Number(img.is_reference) || 0,
          _thumbLoaded: false,
        }));
        const newIds = new Set(images.value.map((img) => img.id));
        selectedImageIds.value = selectedImageIds.value.filter((id) =>
          newIds.has(id)
        );
        lastSelectedIndex = null;
        setTimeout(updateColumns, 0);
      }
    }
  } catch (e) {
    alert("Failed to assign character: " + (e.message || e));
  }
}

// --- Character Management ---
function addNewCharacter() {
  let num = nextCharacterNumber.value;
  let name;
  const existingNames = new Set(characters.value.map((c) => c.name));
  do {
    name = `Character ${num}`;
    num++;
  } while (existingNames.has(name));
  nextCharacterNumber.value = num;
  fetch(`${BACKEND_URL}/characters`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, description: "" }),
  })
    .then(async (res) => {
      if (!res.ok) throw new Error("Failed to create character");
      const data = await res.json();
      if (data && data.character && data.character.id) {
        characters.value.push(data.character);
        editingCharacterId.value = data.character.id;
        editingCharacterName.value = data.character.name;
        nextTick(() => {
          const input = document.querySelector(".edit-character-input");
          if (input) {
            input.focus();
            input.select();
          }
        });
        fetchCharacterThumbnail(data.character.id);
      }
    })
    .catch((e) => {
      alert("Failed to create character: " + (e.message || e));
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
        selectedReferenceMode.value = false;
        images.value = [];
        await fetchCharacters();
      })
      .catch((e) => {
        alert("Failed to delete character: " + (e.message || e));
      });
  }
}

// --- Reference Toggles ---
async function toggleReference(img) {
  const selectedIds = selectedImageIds.value;
  const multi = selectedIds.length > 1 && selectedIds.includes(img.id);
  const newVal = Number(img.is_reference) === 1 ? 0 : 1;
  const targets = multi
    ? images.value.filter((i) => selectedIds.includes(i.id))
    : [img];
  try {
    await Promise.all(
      targets.map(async (target) => {
        const res = await fetch(
          `${BACKEND_URL}/pictures/${target.id}?is_reference=${newVal}`,
          { method: "PATCH" }
        );
        if (!res.ok)
          throw new Error(
            `Failed to update reference status for image ${target.id}`
          );
        target.is_reference = newVal;
      })
    );
    if (selectedReferenceMode.value && newVal === 0) {
      images.value = images.value.filter(
        (i) => !targets.some((t) => t.id === i.id)
      );
    }
  } catch (e) {
    alert("Failed to update reference status: " + (e.message || e));
  }
}

// --- Watchers ---
watch([selectedSort, selectedCharacter, selectedReferenceMode], () => {
  if (searchQuery.value && searchQuery.value.trim()) {
    return;
  }
  pageOffset.value = 0;
  hasMoreImages.value = true;
  lastSelectedIndex = null;
  refreshImages();
});

watch([selectedCharacter, selectedReferenceMode], () => {
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

watch(referenceFilterMode, (val) => {
  patchConfigUIOptions({ show_only_reference: val });
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
