<script setup>
import nlp from "compromise";
import { marked } from "marked";
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  reactive,
  ref,
  watch,
} from "vue";
import { VTextField } from "vuetify/components";

import SearchBar from "./components/SearchBar.vue";
import unknownPerson from "./assets/unknown-person.png"; // Import for unknown character icon

const BACKEND_URL = "http://localhost:9537";

// Drag-and-drop overlay state (for image grid only)
const dragOverlayVisible = ref(false);
const dragOverlayMessage = ref("");
// Track drag source for grid
const dragSource = ref(null);

const gridContainer = ref(null); // already used for grid

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
function isSupportedImageFile(file) {
  const ext = file.name.split(".").pop().toLowerCase();
  return PIL_IMAGE_EXTENSIONS.includes(ext);
}

// Format likeness score as percentage with 2 decimals function
function formatLikenessScore(score) {
  if (typeof score !== "number") return "";
  return `Likeness: ${(score * 100).toFixed(2)}%`;
}

function extractKeywords(text) {
  const doc = nlp(text);
  // Get all noun and adjective phrases as keywords
  const nouns = doc.nouns().out("array");
  const adjectives = doc.adjectives().out("array");
  // Combine and deduplicate
  const keywords = Array.from(new Set([...nouns, ...adjectives]));
  return keywords.join(" ");
}

// Extracts the format/extension for overlayImage robustly function
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

// Accepts either a file object (with .name) or a string extension
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

async function hashFile(file) {
  // SHA-256 sampled hash: whole file if <=128KB, else 8 evenly spaced 8192-byte
  // blocks
  const CHUNK_SIZE = 8192;
  const N = 8;
  const WHOLE_FILE_THRESHOLD = 128 * 1024; // 128KB
  if (file.size <= WHOLE_FILE_THRESHOLD) {
    const buf = await file.arrayBuffer();
    const hashBuffer = await crypto.subtle.digest("SHA-256", buf);
    return Array.from(new Uint8Array(hashBuffer))
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");
  }
  // For larger files, sample N evenly spaced blocks
  const offsets = Array.from({ length: N }, (_, i) =>
    Math.floor((i * (file.size - CHUNK_SIZE)) / (N - 1))
  );
  const chunks = [];
  for (const offset of offsets) {
    const blob = file.slice(offset, offset + CHUNK_SIZE);
    const buf = await blob.arrayBuffer();
    chunks.push(new Uint8Array(buf));
  }
  let totalLen = chunks.reduce((sum, arr) => sum + arr.length, 0);
  let all = new Uint8Array(totalLen);
  let pos = 0;
  for (const arr of chunks) {
    all.set(arr, pos);
    pos += arr.length;
  }
  const hashBuffer = await crypto.subtle.digest("SHA-256", all);
  return Array.from(new Uint8Array(hashBuffer))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

// Sorting and pagination state
const sortOptions = ref([]);
const selectedSort = ref("");
const previousSort = ref(""); // Track previous sort for search restore
const pageSize = ref(100);
const pageOffset = ref(0);
const hasMoreImages = ref(true);

// Fetch sort mechanisms from backend
async function fetchSortOptions() {
  try {
    const res = await fetch(`${BACKEND_URL}/sort_mechanisms`);
    if (!res.ok) throw new Error("Failed to fetch sort mechanisms");
    const options = await res.json();
    sortOptions.value = options.map((opt) => ({
      label: opt.label,
      value: opt.id,
    }));
    // Set default sort if not set
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

const selectedCharacter = ref(ALL_PICTURES_ID);
const selectedReferenceMode = ref(false);

// Track thumbnail load state globally by image ID
const thumbLoaded = reactive({});

// Fetch images for the current character and mode, with pagination and sorting
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
      // Reference mode: fallback to old endpoint for now (no paging)
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
    if (append) {
      images.value = [...images.value, ...newImages];
    } else {
      images.value = newImages;
    }
    hasMoreImages.value = newImages.length === pageSize.value;
    setTimeout(updateColumns, 0);
  } catch (e) {
    imagesError.value = e.message;
  } finally {
    imagesLoading.value = false;
  }
}

// Watch for sort or character changes
watch([selectedSort, selectedCharacter, selectedReferenceMode], () => {
  pageOffset.value = 0;
  hasMoreImages.value = true;
  lastSelectedIndex = null;
  refreshImages();
});

function handleGridDragEnter(e) {
  // Only trigger if entering from outside the image-grid (not between children)
  // If relatedTarget is inside the grid, ignore (moving within grid children).
  if (
    e.relatedTarget &&
    gridContainer.value &&
    gridContainer.value.contains(e.relatedTarget)
  )
    return;
  if (!e.dataTransfer || !e.dataTransfer.items) return;
  // Only check the first 5 items for image type, break immediately if found
  const items = Array.from(e.dataTransfer.items);
  let hasImageType = false;
  for (let i = 0; i < Math.min(items.length, 5); i++) {
    const item = items[i];
    if (item.kind === "file" && item.type.startsWith("image/")) {
      hasImageType = true;
      break;
    }
  }
  // Timing end
  if (hasImageType) {
    dragOverlayVisible.value = true;
    dragOverlayMessage.value = "Drop files here to import";
    e.preventDefault();
    console.debug("Overlay shown");
  } else {
    dragOverlayVisible.value = false;
  }
}

function handleGridDragOver(e) {
  if (dragOverlayVisible.value) e.preventDefault();
}
function handleGridDragLeave(e) {
  // Only hide overlay if leaving the .image-grid entirely
  if (!e.relatedTarget || !e.currentTarget.contains(e.relatedTarget)) {
    dragOverlayVisible.value = false;
  } else {
    console.debug("Drag still inside grid, overlay remains");
  }
}

// Import progress modal state
const importInProgress = ref(false);
const importProgress = ref(0);
const importTotal = ref(0);
const importError = ref(null);
const importPhase = ref(""); // 'hashing', 'checking', 'uploading', 'done', 'error'
const importPhaseMessage = computed(() => {
  switch (importPhase.value) {
    case "uploading":
      return "Uploading images...";
    case "done":
      return "Import complete!";
    case "duplicates":
      return "All files are duplicates.";
    case "cancelled":
      return "Import cancelled.";
    case "error":
      return "Import failed.";
    default:
      return "";
  }
});

const cancelImport = ref(false);
function handleCancelImport() {
  cancelImport.value = true;
}

function handleGridDrop(e) {
  dragOverlayVisible.value = false;
  // Prevent importing if this is an internal drag (from our own grid)
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
  cancelImport.value = false;
  importInProgress.value = true;
  importProgress.value = 0;
  importError.value = null;
  importPhase.value = "uploading";
  dragSource.value = null;
  (async () => {
    importTotal.value = files.length;
    importProgress.value = 0;
    let completed = 0;
    let importedCount = 0;
    importError.value = null;
    const BATCH_SIZE = 100;
    const TIMEOUT_MS = 5000; // 5 seconds
    const MAX_RETRIES = 3;
    try {
      for (let i = 0; i < files.length; i += BATCH_SIZE) {
        const batch = files.slice(i, i + BATCH_SIZE);
        const formData = new FormData();
        batch.forEach((file) => {
          formData.append("file", file);
        });
        if (
          selectedCharacter.value &&
          selectedCharacter.value !== ALL_PICTURES_ID &&
          selectedCharacter.value !== UNASSIGNED_PICTURES_ID
        ) {
          formData.append("character_id", selectedCharacter.value);
        }
        let res = null;
        let lastError = null;
        for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
          const controller = new AbortController();
          const timeout = setTimeout(() => controller.abort(), TIMEOUT_MS);
          try {
            res = await fetch(`${BACKEND_URL}/pictures`, {
              method: "POST",
              body: formData,
              signal: controller.signal,
            });
            clearTimeout(timeout);
            if (res.ok) {
              break;
            } else {
              lastError = new Error(`Upload failed with status ${res.status}`);
            }
          } catch (err) {
            clearTimeout(timeout);
            if (err.name === "AbortError") {
              lastError = new Error("Upload timed out");
              console.warn(
                `[IMPORT] Batch ${
                  i / BATCH_SIZE + 1
                } timed out (attempt ${attempt})`
              );
            } else {
              lastError = err;
              console.warn(
                `[IMPORT] Batch ${
                  i / BATCH_SIZE + 1
                } failed (attempt ${attempt}):`,
                err
              );
            }
          }
          if (attempt < MAX_RETRIES) {
            await new Promise((resolve) => setTimeout(resolve, 1000)); // wait 1s before retry
          }
        }
        if (!res || !res.ok) {
          importPhase.value = "error";
          importError.value = lastError ? lastError.message : "Upload failed.";
          importInProgress.value = false;
          return;
        }
        const result = await res.json();
        if (result && Array.isArray(result.results)) {
          completed += result.results.length;
          importProgress.value = completed;
          importedCount += result.results.filter(
            (r) => r.status === "success"
          ).length;
          await nextTick();
        }
      }
      if (importedCount === 0) {
        importPhase.value = "duplicates";
        importError.value = "All files are duplicates.";
      } else {
        importPhase.value = "done";
        importError.value = `Imported ${importedCount} image${
          importedCount !== 1 ? "s" : ""
        }.`;
      }
      setTimeout(() => {
        importInProgress.value = false;
      }, 1500);
      refreshImages();
      fetchSidebarCounts();
    } catch (e) {
      importPhase.value = "error";
      importInProgress.value = false;
      alert("All uploads failed: " + (e.message || e));
    }
  })();
}

// Clear selection if clicking on empty space in the image grid
function handleGridBackgroundClick(e) {
  // If the click is NOT inside an image-card, clear selection
  if (!e.target.closest(".thumbnail-card")) {
    selectedImageIds.value = [];
    lastSelectedIndex = null;
  }
}

// Infinite scroll: load more images as user scrolls near bottom
function onGridScroll(e) {
  const el = e.target;
  if (!hasMoreImages.value || imagesLoading.value) return;
  if (el.scrollTop + el.clientHeight >= el.scrollHeight - 200) {
    // Near bottom
    pageOffset.value += pageSize.value;
    refreshImages(true);
  }
}

// Use backend-driven images, no local sorting
const pagedImages = computed(() => images.value);

// Remove a tag from the overlay image and PATCH the backend
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

// State for adding a tag in the overlay
const addingTagOverlay = ref(false);
const newTagOverlay = ref("");

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
  } // Prevent duplicate tags

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

// Selection state for file manager
const selectedImageIds = ref([]);
let lastSelectedIndex = null;

// Sidebar visibility state
const sidebarVisible = ref(true);

// Overlay state for full image view
const overlayOpen = ref(false);
const overlayImage = ref(null);

// Trophy button color: dark blue when not selected, orange when selected
const trophyButtonColor = (charId) =>
  selectedCharacter.value === charId && selectedReferenceMode.value
    ? "orange"
    : "#29405a"; // darker blue than sidebar

function openOverlay(img) {
  overlayImage.value = img;
  overlayOpen.value = true;
}

function closeOverlay() {
  overlayOpen.value = false;
}

const chatOpen = ref(false);
function openChatOverlay() {
  chatOpen.value = true;
  nextTick(() => {
    if (chatInputField.value) chatInputField.value.focus();
  });
}

function closeChatOverlay() {
  chatOpen.value = false;
}

// Scroll chat to bottom utility
function scrollChatToBottom() {
  nextTick(() => {
    if (chatMessagesContainer.value) {
      chatMessagesContainer.value.scrollTop =
        chatMessagesContainer.value.scrollHeight;
    }
  });
}

// Search bar state and logic
const searchQuery = ref(""); // Used for actual search
async function searchImages(query) {
  // Only update searchQuery and trigger search if input is non-empty
  const q = (typeof query === "string" ? query : searchQuery.value).trim();
  if (!q) return;
  searchQuery.value = q;
  // Save previous sort before switching to likeness sort
  previousSort.value = selectedSort.value;
  // Switch sorting to 'Sort by Search Likeness' if available
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
  // Watch for clearing of searchQuery to restore previous sort and refresh view
  watch(searchQuery, (newVal, oldVal) => {
    if (!newVal && oldVal) {
      // Restore previous sort if available
      if (previousSort.value && previousSort.value !== selectedSort.value) {
        selectedSort.value = previousSort.value;
      }
      // Refresh images for current character and sort
      refreshImages();
    }
  });
}

function handleImageSelect(img, idx, event) {
  // Use pagedImages for all index-based selection
  const sorted = pagedImages.value;
  const id = img.id;
  const isSelected = selectedImageIds.value.includes(id);
  const isCtrl = event.ctrlKey || event.metaKey;
  const isShift = event.shiftKey;

  if (isShift) {
    if (lastSelectedIndex !== null) {
      // Range select in pagedImages
      const start = Math.min(lastSelectedIndex, idx);
      const end = Math.max(lastSelectedIndex, idx);
      const rangeIds = sorted.slice(start, end + 1).map((i) => i.id);
      const newSelection = isCtrl
        ? Array.from(new Set([...selectedImageIds.value, ...rangeIds]))
        : rangeIds;
      selectedImageIds.value = newSelection;
    } else {
      // No previous selection, just select the clicked image
      selectedImageIds.value = [id];
    }
    lastSelectedIndex = idx;
  } else if (isCtrl) {
    // Toggle selection
    if (isSelected) {
      selectedImageIds.value = selectedImageIds.value.filter((i) => i !== id);
    } else {
      selectedImageIds.value = [...selectedImageIds.value, id];
    }
    lastSelectedIndex = idx;
  } else {
    // Single select
    selectedImageIds.value = [id];
    lastSelectedIndex = idx;
  }
}

// Only visually mark as selected if the image is both in selectedImageIds and
// visible in pagedImages
const isImageSelected = (id) =>
  selectedImageIds.value.includes(id) &&
  pagedImages.value.some((img) => img.id === id);

// Logic to determine if a selected image is on the outer edge of a selection
// group (use pagedImages)
const getSelectionBorderClasses = (idx) => {
  const sorted = pagedImages.value;
  if (!isImageSelected(sorted[idx]?.id)) return "";
  const cols = columns.value;
  const total = sorted.length;
  const row = Math.floor(idx / cols);
  const col = idx % cols;
  let classes = [];
  // Check neighbors: top, right, bottom, left
  // Top
  if (row === 0 || !isImageSelected(sorted[(row - 1) * cols + col]?.id)) {
    classes.push("selected-border-top");
  }
  // Bottom
  if (
    row === Math.floor((total - 1) / cols) ||
    !isImageSelected(sorted[(row + 1) * cols + col]?.id)
  ) {
    classes.push("selected-border-bottom");
  }
  // Left
  if (col === 0 || !isImageSelected(sorted[row * cols + (col - 1)]?.id)) {
    classes.push("selected-border-left");
  }
  // Right
  if (
    col === cols - 1 ||
    !isImageSelected(sorted[row * cols + (col + 1)]?.id)
  ) {
    classes.push("selected-border-right");
  }
  return classes.join(" ");
};

// Handle drop on Reference Images child
function onReferenceDrop(characterId, event) {
  dragOverCharacter.value = null;
  try {
    const data = JSON.parse(event.dataTransfer.getData("application/json"));
    if (!data.imageIds || !Array.isArray(data.imageIds)) return;
    assignImagesAsReference(data.imageIds, characterId);
  } catch (e) {}
}

const ALL_PICTURES_ID = "__all__";
const UNASSIGNED_PICTURES_ID = "__unassigned__";
const characters = ref([]);
// Store image counts for each category (all, unassigned, characterId)
const categoryCounts = ref({
  [ALL_PICTURES_ID]: 0,
  [UNASSIGNED_PICTURES_ID]: 0,
  // characterId: count
});

// Fetch and update image counts for all sidebar categories
async function fetchSidebarCounts() {
  // All Pictures
  try {
    const resAll = await fetch(`${BACKEND_URL}/category/summary`);
    if (resAll.ok) {
      const data = await resAll.json();
      categoryCounts.value[ALL_PICTURES_ID] = data.image_count;
    }
  } catch {}
  // Unassigned Pictures
  try {
    const resUnassigned = await fetch(
      `${BACKEND_URL}/category/summary?character_id=null`
    );
    if (resUnassigned.ok) {
      const data = await resUnassigned.json();
      categoryCounts.value[UNASSIGNED_PICTURES_ID] = data.image_count;
    }
  } catch {}
  // Each character
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
// Computed: characters sorted alphabetically by name (case-insensitive)
const sortedCharacters = computed(() => {
  return [...characters.value]
    .filter((c) => c && typeof c.name === "string" && c.name.trim() !== "")
    .sort((a, b) => {
      return a.name.localeCompare(b.name, undefined, { sensitivity: "base" });
    });
});
const characterThumbnails = ref({}); // { [characterId]: thumbnailUrl }
const loading = ref(false);
const error = ref(null);

// Reference filter for toolbar (local only, no backend refresh)
const showStars = ref(true);
const referenceFilterMode = ref(false);
const filteredImages = computed(() => {
  if (referenceFilterMode.value) {
    return images.value.filter((img) => Number(img.is_reference) === 1);
  }
  return images.value;
});
const expandedCharacters = ref({}); // { [characterId]: true/false }
// Collapsible sidebar sections
const sidebarSections = ref({
  pictures: true,
  people: true,
  search: true,
});

const images = ref([]);
const imagesLoading = ref(false);
const imagesError = ref(null);

// Thumbnail size slider state
const thumbnailSize = ref(256);

// Responsive columns
const columns = ref(5);

function updateColumns() {
  if (!gridContainer.value) return;
  const containerWidth = gridContainer.value.offsetWidth;
  columns.value = Math.max(
    1,
    Math.floor(containerWidth / (thumbnailSize.value + 32))
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
    // For each character, fetch their first image's thumbnail (if any)
    for (const char of chars) {
      fetchCharacterThumbnail(char.id);
    }
    // After loading characters, fetch sidebar counts
    await fetchSidebarCounts();
  } catch (e) {
    error.value = e.message;
  } finally {
    loading.value = false;
  }
}

async function fetchCharacterThumbnail(characterId) {
  try {
    // Add cache-busting query param to ensure fresh thumbnail
    const cacheBuster = Date.now();
    const thumbUrl = `${BACKEND_URL}/face_thumbnail/${characterId}?cb=${cacheBuster}`;
    // Test if the endpoint returns an image (status 200 and content-type
    // image/png)
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

// Toggle reference status for a picture (multi-select aware)
async function toggleReference(img) {
  // If multiple images are selected and this image is among them, apply to all
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
    // If in reference mode, reload images so the grid updates immediately
    if (selectedReferenceMode.value && newVal === 0) {
      images.value = images.value.filter(
        (i) => !targets.some((t) => t.id === i.id)
      );
    }
  } catch (e) {
    alert("Failed to update reference status: " + (e.message || e));
  }
}

const settingsDialog = ref(false);
watch(settingsDialog, (val) => {
  if (val) fetchConfig();
});
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

async function fetchOpenAIModels() {
  openaiModelLoading.value = true;
  openaiModelFetchError.value = "";
  openaiModels.value = [];
  try {
    const url = `http://${config.openai_host}:${config.openai_port}/v1/models`;
    const res = await fetch(url);
    if (!res.ok) throw new Error("Failed to fetch models");
    const data = await res.json();
    // OpenAI API returns { data: [ { id: ... }, ... ] }
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
    // UI options
    if (data.sort) selectedSort.value = data.sort_order;
    if (data.thumbnail) thumbnailSize.value = data.thumbnail_size;
    if (typeof data.show_stars === "boolean") showStars.value = data.show_stars;
    if (typeof data.show_only_reference === "boolean")
      referenceFilterMode.value = data.show_only_reference;
    // Also update config for PATCHing
    config.sort_order = data.sort || selectedSort.value;
    config.thumbnail_size = data.thumbnail || thumbnailSize.value;
    config.show_stars =
      typeof data.show_stars === "boolean" ? data.show_stars : showStars.value;
    config.show_only_reference =
      typeof data.show_only_reference === "boolean"
        ? data.show_only_reference
        : referenceFilterMode.value;
    // OpenAI settings
    config.openai_host = data.openai_host || "localhost";
    config.openai_port = data.openai_port || 8000;
    config.openai_model = data.openai_model || "";
    if (!res.ok) {
      const text = await res.text();
      console.error("Failed to fetch /config:", res.status, text);
      return;
    }
  } catch (e) {
    console.error("Error fetching /config:", e);
  }
}

// Settings dialog: image roots add/remove/select logic
const newImageRoot = ref("");
async function addImageRoot() {
  const val = newImageRoot.value.trim();
  if (!val || config.image_roots.includes(val)) return;
  config.image_roots.push(val);
  newImageRoot.value = "";
  // PATCH only image_roots
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
    // If removed root was selected, pick first remaining
    if (config.selected_image_root === root) {
      config.selected_image_root = config.image_roots[0] || "";
    }
    saveConfig();
  }
}

async function updateSelectedRoot() {
  // PATCH only selected_image_root
  await fetch(`${BACKEND_URL}/config`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ selected_image_root: config.selected_image_root }),
  });
  // Refresh grid and sidebar after vault change
  await fetchConfig();
  await fetchCharacters();
  await fetchSidebarCounts();
  await refreshImages();
}

// --- UI option PATCH logic ---
async function patchConfigUIOptions(opts = {}) {
  // Merge with config
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
  // Save config to backend (POST /config)
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

// Fetch config and sync UI options on mount
onMounted(() => {
  fetchConfig();
});

onMounted(() => {
  // Always select All Pictures at startup
  selectedCharacter.value = ALL_PICTURES_ID;
  selectedReferenceMode.value = false;
  fetchSortOptions();
  fetchCharacters();
  window.addEventListener("resize", updateColumns);
  watch(thumbnailSize, updateColumns);
  setTimeout(updateColumns, 100); // Initial update after mount
});

// Watch and PATCH UI config options when changed
watch(selectedSort, (val) => {
  patchConfigUIOptions({ sort: val });
});
watch(thumbnailSize, (val) => {
  patchConfigUIOptions({ thumbnail: val });
});
watch(showStars, (val) => {
  patchConfigUIOptions({ show_stars: val });
});

watch(referenceFilterMode, (val) => {
  patchConfigUIOptions({ show_only_reference: val });
});

// Watch OpenAI config fields and PATCH when changed
// Helper: refresh models after patching host/port
async function patchHostAndRefresh(val, key) {
  await patchConfigUIOptions({ [key]: val });
  fetchOpenAIModels();
}

// Patch and refresh models when host/port change (on blur or enter)
function onHostBlurOrEnter(e) {
  patchHostAndRefresh(config.openai_host, "openai_host");
}
function onPortBlurOrEnter(e) {
  patchHostAndRefresh(config.openai_port, "openai_port");
}

// Still patch on change for persistence
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

watch([selectedCharacter, selectedReferenceMode], async ([id, refMode]) => {
  refreshImages();
});

function handleOverlayKeydown(e) {
  // Don't trigger most shortcuts if focus is in a text field, but allow Escape
  // for chat overlay
  const tag =
    e.target && e.target.tagName ? e.target.tagName.toLowerCase() : "";
  const isEditable =
    e.target &&
    (e.target.isContentEditable || tag === "input" || tag === "textarea");
  if (isEditable && !(chatOpen.value && e.key === "Escape")) return;
  // Ctrl+A: select all images in grid view (fetch all, not just paged)
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a") {
    if (images.value.length) {
      selectedImageIds.value = images.value.map((img) => img.id);
      e.preventDefault();
    }
    return;
  }
  // R: toggle reference for overlay image or selection
  if (e.key.toLowerCase() === "r" && !e.ctrlKey && !e.metaKey && !e.altKey) {
    if (overlayOpen.value && overlayImage.value) {
      toggleReference(overlayImage.value);
      e.preventDefault();
      return;
    } else if (selectedImageIds.value.length) {
      // Use the last selected image as the reference for toggle value
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
    // Do nothing if nothing is selected and overlay is not open
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
  // Grid navigation and selection
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
  // Score shortcuts 1-5 (overlay: set score for overlayImage, grid: set for
  // selection)
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

onMounted(() => {
  fetchCharacters();
  window.addEventListener("resize", updateColumns);
  watch(thumbnailSize, updateColumns);
  setTimeout(updateColumns, 100); // Initial update after mount
  window.addEventListener("keydown", handleOverlayKeydown);
});

onBeforeUnmount(() => {
  window.removeEventListener("keydown", handleOverlayKeydown);
});
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

// Delete functionality
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
  // Remove deleted images from UI
  images.value = images.value.filter(
    (img) => !selectedImageIds.value.includes(img.id)
  );
  selectedImageIds.value = [];
  fetchSidebarCounts();
}

// Patch score for selected images
async function patchScoreForSelection(score) {
  if (!selectedImageIds.value.length) return;
  for (const id of selectedImageIds.value) {
    try {
      const res = await fetch(`${BACKEND_URL}/pictures/${id}?score=${score}`, {
        method: "PATCH",
      });
      if (!res.ok) throw new Error(`Failed to set score for image ${id}`);
      // Update local image score
      const result = await res.json();
      const img = images.value.find((i) => i.id === id);
      if (img) img.score = score;
    } catch (e) {
      alert(e.message);
    }
  }
}

// Set score for a single image (click on star)
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
      // Remove image from current position
      const idx = images.value.findIndex((i) => i.id === img.id);
      if (idx === -1) return;
      img.score = newScore;
      images.value.splice(idx, 1);
      // Find new index based on sort order
      let insertIdx = 0;
      if (selectedSort.value === "score_desc") {
        insertIdx = images.value.findIndex((i) => (i.score || 0) < newScore);
        if (insertIdx === -1) insertIdx = images.value.length;
      } else {
        insertIdx = images.value.findIndex((i) => (i.score || 0) > newScore);
        if (insertIdx === -1) insertIdx = images.value.length;
      }
      images.value.splice(insertIdx, 0, img);
      // Scroll to new position
      nextTick(() => {
        const grid = gridContainer.value;
        if (!grid) return;
        const card = grid.querySelectorAll(".image-card")[insertIdx];
        if (card && card.scrollIntoView) {
          card.scrollIntoView({ behavior: "smooth", block: "center" });
        }
      });
    } else {
      // Not sorting by score, just update the score in place
      img.score = newScore;
    }
  } catch (e) {
    alert(e.message);
  }
}

// Drag and drop logic for assigning images to characters
const dragOverCharacter = ref(null);
function onImageDragStart(img, idx, event) {
  // Only allow dragging if this image is selected
  let ids =
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
function onCharacterDragOver(charId) {
  dragOverCharacter.value = charId;
}
function onCharacterDragLeave(charId) {
  if (dragOverCharacter.value === charId) dragOverCharacter.value = null;
}

// Handle drop on character in sidebar to set character_id for selected images
async function onCharacterDrop(characterId, event) {
  dragOverCharacter.value = null;
  let imageIds = [];
  // Always use drag event data for image IDs
  try {
    const data = JSON.parse(event.dataTransfer.getData("application/json"));
    if (data.imageIds && Array.isArray(data.imageIds)) {
      imageIds = data.imageIds;
    }
  } catch (e) {
    // If drag data is missing or malformed, abort
    alert("Could not determine which images to assign. Please try again.");
    return;
  }
  if (!imageIds.length) {
    alert("No images found in drag data.");
    return;
  }
  // Log drop target and character id
  const charObj = characters.value.find((c) => c.id === characterId);
  console.log(
    "[DROP] Drop target characterId:",
    characterId,
    "name:",
    charObj ? charObj.name : "(not found)"
  );
  // Always use the characterId from the drop target
  assignImagesToCharacter(imageIds, characterId);
}

// Assign images to a character by PATCHing their character_id
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
    // Remove reassigned images from the current grid if not viewing All
    // Pictures or Unassigned
    if (
      selectedCharacter.value !== ALL_PICTURES_ID &&
      selectedCharacter.value !== UNASSIGNED_PICTURES_ID &&
      selectedCharacter.value !== characterId
    ) {
      images.value = images.value.filter((img) => !imageIds.includes(img.id));
      // Also remove these IDs from selection
      selectedImageIds.value = selectedImageIds.value.filter((id) =>
        images.value.some((img) => img.id === id)
      );
      lastSelectedIndex = null;
    } else {
      // For All Pictures or Unassigned, refresh the grid as before
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
        // Remove any selected IDs not in the new images
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

// Assign images as reference images for a character (set is_reference=true and
// character_id)
async function assignImagesAsReference(imageIds, characterId) {
  try {
    await Promise.all(
      imageIds.map(async (id) => {
        // Fetch image to check if it already has the character
        let needsChar = true;
        try {
          const res = await fetch(`${BACKEND_URL}/pictures/${id}`);
          if (res.ok) {
            const data = await res.json();
            if (data.character_id === characterId) needsChar = false;
          }
        } catch (e) {}
        // Always set is_reference=true, and set character_id if needed
        let url = `${BACKEND_URL}/pictures/${id}?is_reference=1`;
        if (needsChar)
          url += `&character_id=${encodeURIComponent(characterId)}`;
        const res2 = await fetch(url, { method: "PATCH" });
        if (!res2.ok)
          throw new Error(`Failed to set reference for image ${id}`);
      })
    );
    await fetchCharacters();
    fetchSidebarCounts();
    // Refresh images if needed
    if (
      selectedCharacter.value === characterId ||
      selectedCharacter.value === ALL_PICTURES_ID ||
      selectedCharacter.value === UNASSIGNED_PICTURES_ID
    ) {
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
        // Remove any selected IDs not in the new images
        const newIds = new Set(images.value.map((img) => img.id));
        selectedImageIds.value = selectedImageIds.value.filter((id) =>
          newIds.has(id)
        );
        lastSelectedIndex = null;
        setTimeout(updateColumns, 0);
      }
    }
  } catch (e) {
    alert("Failed to set reference: " + (e.message || e));
  }
}

// Add a ref to track the next character number
const nextCharacterNumber = ref(1);

function addNewCharacter() {
  // Find the next available number
  let num = nextCharacterNumber.value;
  let name;
  const existingNames = new Set(characters.value.map((c) => c.name));
  do {
    name = `Character ${num}`;
    num++;
  } while (existingNames.has(name));
  nextCharacterNumber.value = num;
  // POST to backend
  fetch(`${BACKEND_URL}/characters`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, description: "" }),
  })
    .then(async (res) => {
      if (!res.ok) throw new Error("Failed to create character");
      const data = await res.json();
      if (data && data.character && data.character.id) {
        // Add to local list
        characters.value.push(data.character);
        // Optionally, start editing the new character name
        editingCharacterId.value = data.character.id;
        editingCharacterName.value = data.character.name;
        nextTick(() => {
          const input = document.querySelector(".edit-character-input");
          if (input) {
            input.focus();
            input.select();
          }
        });
        // Optionally, fetch thumbnail
        fetchCharacterThumbnail(data.character.id);
      }
    })
    .catch((e) => {
      alert("Failed to create character: " + (e.message || e));
    });
}

// Inline edit state for character names
const editingCharacterId = ref(null);
const editingCharacterName = ref("");

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
    // PATCH backend
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

// Confirm and delete character
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
        // Remove from local list
        characters.value = characters.value.filter((c) => c.id !== char.id);
        // Reset selection
        selectedCharacter.value = ALL_PICTURES_ID;
        selectedReferenceMode.value = false;
        // Optionally, refresh images
        images.value = [];
        await fetchCharacters();
      })
      .catch((e) => {
        alert("Failed to delete character: " + (e.message || e));
      });
  }
}

// Chat state
// Add optional pictureUrl to assistant messages
const chatMessages = ref([]); // {role: 'user'|'assistant', content: string, pictureUrl?: string}
const chatInput = ref("");
const chatLoading = ref(false);
const chatMessagesContainer = ref(null);
const chatInputField = ref(null);

function renderMarkdown(text) {
  return marked.parse(text || "");
}

// Computed: Get the selected character object (if any)
const selectedCharacterObj = computed(() => {
  if (
    selectedCharacter.value &&
    selectedCharacter.value !== ALL_PICTURES_ID &&
    selectedCharacter.value !== UNASSIGNED_PICTURES_ID
  ) {
    const char =
      characters.value.find((c) => c.id === selectedCharacter.value) || null;
    if (char && typeof char.name === "string" && char.name.length > 0) {
      // Capitalize first letter only
      return {
        ...char,
        name: char.name.charAt(0).toUpperCase() + char.name.slice(1),
      };
    }
    return char;
  }
  return null;
});

async function sendChatMessageAndFocus() {
  const input = chatInput.value.trim();
  if (!input || chatLoading.value) return;

  let system_message =
    "You should always respond as the character you are playing. Stay in character and don't break it. Let me speak for myself. Do not repeat yourself.";

  if (chatMessages.value.length === 0) {
    // First message, set character context
    if (selectedCharacterObj.value && selectedCharacterObj.value.name) {
      system_message += ` You are now assuming the role of the character named '${selectedCharacterObj.value.name}'.`;
      if (
        selectedCharacterObj.value.description &&
        selectedCharacterObj.value.description.trim().length > 0
      ) {
        system_message += ` Here is some information about you: ${selectedCharacterObj.value.description.trim()}`;
      }
    } else {
      system_message +=
        " You are now assuming the role of a generic character without a specific name or background.";
    }
    chatMessages.value.push(
      { role: "user", content: input },
      { role: "system", content: system_message }
    );
  } else {
    chatMessages.value.push({ role: "user", content: input });
  }
  chatInput.value = "";
  chatLoading.value = true;
  await nextTick();
  scrollChatToBottom();
  try {
    const url = `http://${config.openai_host}:${config.openai_port}/v1/chat/completions`;
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model: config.openai_model || "gpt-3.5-turbo",
        messages: chatMessages.value.map((m) => ({
          role: m.role,
          content: m.content,
        })),
        stream: false,
      }),
    });
    if (!res.ok) throw new Error("OpenAI server error");
    const data = await res.json();
    const reply = data.choices?.[0]?.message?.content || "(No response)";
    // Add the AI response first
    chatMessages.value.push({ role: "assistant", content: reply });
    await nextTick();
    scrollChatToBottom();

    // After AI responds, trigger /search with character name + last user input
    // + last AI response
    let lastUser = null;
    for (let i = chatMessages.value.length - 2; i >= 0; i--) {
      if (chatMessages.value[i].role === "user") {
        lastUser = chatMessages.value[i].content;
        break;
      }
    }
    let searchQuery = extractKeywords(reply);
    if (lastUser) {
      searchQuery = lastUser + " " + searchQuery;
    }
    if (selectedCharacterObj.value && selectedCharacterObj.value.name) {
      searchQuery = selectedCharacterObj.value.name + " " + searchQuery;
    }
    try {
      const searchRes = await fetch(
        `${BACKEND_URL}/search?query=${encodeURIComponent(searchQuery)}`
      );
      if (searchRes.ok) {
        const searchData = await searchRes.json();
        if (searchData && Array.isArray(searchData) && searchData.length > 0) {
          // Weighted random selection by likeness_score
          const totalScore = searchData.reduce(
            (sum, pic) => sum + (pic.likeness_score || 0),
            0
          );
          let r = Math.random() * totalScore;
          let chosen = searchData[0];
          for (const pic of searchData) {
            r -= pic.likeness_score || 0;
            if (r <= 0) {
              chosen = pic;
              break;
            }
          }
          // Compose the image URL (assuming /pictures/:id)
          const imageUrl = `${BACKEND_URL}/pictures/${chosen.id}`;
          // Add the picture URL to the last assistant message
          const lastMsg = chatMessages.value
            .slice()
            .reverse()
            .find((m) => m.role === "assistant" && !m.pictureUrl);
          if (lastMsg) {
            lastMsg.pictureUrl = imageUrl;
          }
        }
      }
    } catch (e) {
      // Ignore search errors
    }
  } catch (e) {
    chatMessages.value.push({
      role: "assistant",
      content: "Error: " + (e.message || e),
    });
  } finally {
    chatLoading.value = false;
    await nextTick();
    if (chatInputField.value) {
      chatInputField.value.focus();
    }
  }
}
</script>
<template src="./App.template.html"></template>
<style scoped src="./App.css"></style>
