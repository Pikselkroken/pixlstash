<script setup>
import {
  computed,
  ref,
  onMounted,
  watch,
  onBeforeUnmount,
  nextTick,
} from "vue";
import { VTextField } from "vuetify/components";
import SearchBar from "./components/SearchBar.vue";
import unknownPerson from "./assets/unknown-person.png"; // Import for unknown character icon

// Drag-and-drop overlay state (for image grid only)
const dragOverlayVisible = ref(false);
const dragOverlayMessage = ref("");

// Import progress modal state
const importInProgress = ref(false);
const importProgress = ref(0);
const importTotal = ref(0);
const importError = ref(null);
const importPhase = ref(''); // 'hashing', 'checking', 'uploading', 'done', 'error'
const importPhaseMessage = computed(() => {
  switch (importPhase.value) {
    case 'hashing': return 'Hashing files...';
    case 'checking': return 'Checking for duplicates...';
    case 'uploading': return 'Uploading images...';
    case 'done': return 'Import complete!';
    case 'duplicates': return 'All files are duplicates.';
    case 'cancelled': return 'Import cancelled.';
    case 'error': return 'Import failed.';
    default: return '';
  }
});
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
function isSupportedImageFile(file) {
  const ext = file.name.split(".").pop().toLowerCase();
  return PIL_IMAGE_EXTENSIONS.includes(ext);
}

async function hashFile(file) {
  // SHA-256 sampled hash: whole file if <=128KB, else 8 evenly spaced 8192-byte blocks
  const CHUNK_SIZE = 8192;
  const N = 8;
  const WHOLE_FILE_THRESHOLD = 128 * 1024; // 128KB
  if (file.size <= WHOLE_FILE_THRESHOLD) {
    const buf = await file.arrayBuffer();
    const hashBuffer = await crypto.subtle.digest('SHA-256', buf);
    return Array.from(new Uint8Array(hashBuffer)).map(b => b.toString(16).padStart(2, '0')).join('');
  }
  // For larger files, sample N evenly spaced blocks
  const offsets = Array.from({length: N}, (_, i) => Math.floor(i * (file.size - CHUNK_SIZE) / (N - 1)));
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
  const hashBuffer = await crypto.subtle.digest('SHA-256', all);
  return Array.from(new Uint8Array(hashBuffer)).map(b => b.toString(16).padStart(2, '0')).join('');
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
    const res = await fetch(`${BACKEND_URL}/pictures/sort_mechanisms`);
    if (!res.ok) throw new Error("Failed to fetch sort mechanisms");
    const options = await res.json();
    sortOptions.value = options.map((opt) => ({
      label: opt.label,
      value: opt.id,
    }));
    // Set default sort if not set
    if (!selectedSort.value && options.length) {
      selectedSort.value =
        options.find((o) => o.id === "date_desc")?.id || options[0].id;
    }
  } catch (e) {
    sortOptions.value = [
      { label: "Date: Latest First", value: "date_desc" },
      { label: "Date: Oldest First", value: "date_asc" },
      { label: "Score: Highest First", value: "score_desc" },
      { label: "Score: Lowest First", value: "score_asc" },
    ];
    if (!selectedSort.value) selectedSort.value = "date_desc";
  }
}

const selectedCharacter = ref(ALL_PICTURES_ID);
const selectedReferenceMode = ref(false);

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
  if (e.relatedTarget && gridContainer.value && gridContainer.value.contains(e.relatedTarget)) return;
  if (!e.dataTransfer || !e.dataTransfer.items) return;
  // Only check the first 20 items for image type, break immediately if found
  const items = Array.from(e.dataTransfer.items);
  let hasImageType = false;
  for (let i = 0; i < Math.min(items.length, 20); i++) {
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

const cancelImport = ref(false);
function handleCancelImport() {
  cancelImport.value = true;
}

function handleGridDrop(e) {
  dragOverlayVisible.value = false;
  if (!e.dataTransfer || !e.dataTransfer.files) return;
  const files = Array.from(e.dataTransfer.files).filter(isSupportedImageFile);
  console.debug('[IMPORT] Files dropped:', e.dataTransfer.files);
  console.debug('[IMPORT] Supported files after filter:', files);
  if (!files.length) {
    alert("No supported image files found.");
    return;
  }
  cancelImport.value = false;
  importInProgress.value = true;
  importProgress.value = 0;
  importError.value = null;
  importPhase.value = 'hashing';
  (async () => {
    // Step 1: Compute hashes for all files in parallel (with concurrency limit)
    importTotal.value = files.length;
    let hashProgress = 0;
    let fileHashes = [];
    const CONCURRENCY = 6;
    // Helper to run promises with concurrency limit
    async function mapWithConcurrencyLimit(items, fn, concurrency) {
      const results = new Array(items.length);
      let nextIndex = 0;
      let active = 0;
      return new Promise((resolve, reject) => {
        function runNext() {
          if (nextIndex >= items.length && active === 0) {
            resolve(results);
            return;
          }
          while (active < concurrency && nextIndex < items.length) {
            const idx = nextIndex++;
            active++;
            fn(items[idx], idx)
              .then((result) => {
                results[idx] = result;
                active--;
                runNext();
              })
              .catch((err) => {
                reject(err);
              });
          }
        }
        runNext();
      });
    }
    try {
      fileHashes = await mapWithConcurrencyLimit(
        files,
        async (file, idx) => {
          if (cancelImport.value) throw new Error('cancelled');
          const hash = await hashFile(file);
          hashProgress++;
          importProgress.value = hashProgress;
          await nextTick();
          return { file, hash };
        },
        CONCURRENCY
      );
      console.debug('[IMPORT] fileHashes after hashing:', fileHashes);
    } catch (err) {
      importInProgress.value = false;
      if (err.message === 'cancelled') {
        importPhase.value = 'cancelled';
        importError.value = 'Import cancelled.';
      } else {
        importPhase.value = 'error';
        importError.value = "Failed to hash files.";
      }
      setTimeout(() => { importInProgress.value = false; }, 1500);
      return;
    }
    // Step 2: Batch check with backend for existing hashes
    importPhase.value = 'checking';
    let existing = [];
    try {
      const hashesToSend = fileHashes.map(fh => fh.hash);
      console.debug('[IMPORT] Sending hashes to /check_hashes:', hashesToSend);
      const res = await fetch(`${BACKEND_URL}/check_hashes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(hashesToSend),
      });
      if (res.ok) {
        const data = await res.json();
        console.debug('[IMPORT] /check_hashes response:', data);
        existing = data.existing || [];
      } else {
        throw new Error("Failed to check for duplicates");
      }
    } catch (err) {
      importPhase.value = 'error';
      importInProgress.value = false;
      importError.value = "Failed to check for duplicates.";
      setTimeout(() => { importInProgress.value = false; }, 1500);
      return;
    }
    // Step 3: Filter out duplicates
    const newFiles = fileHashes.filter(fh => !existing.includes(fh.hash)).map(fh => fh.file);
    importTotal.value = newFiles.length;
    importProgress.value = 0;
    if (newFiles.length === 0) {
      importPhase.value = 'duplicates';
      importError.value = "All files are duplicates.";
      setTimeout(() => { importInProgress.value = false; }, 2000);
      return;
    }
    // Show found X new images
    importPhase.value = 'uploading';
    importError.value = `Found ${newFiles.length} new image(s).`;
    let completed = 0;
    const uploadFile = async (file) => {
      const formData = new FormData();
      formData.append("image", file);
      if (
        selectedCharacter.value &&
        selectedCharacter.value !== ALL_PICTURES_ID &&
        selectedCharacter.value !== UNASSIGNED_PICTURES_ID
      ) {
        formData.append("character_id", selectedCharacter.value);
      }
      try {
        const res = await fetch(`${BACKEND_URL}/pictures`, {
          method: "POST",
          body: formData,
        });
        if (!res.ok) throw new Error("Upload failed");
        await res.json();
        completed++;
        importProgress.value = completed;
        await nextTick();
      } catch (err) {
        importPhase.value = 'error';
        importError.value = err.message || String(err);
        throw err;
      }
    };
    try {
      for (const file of newFiles) {
        if (cancelImport.value) {
          importPhase.value = 'cancelled';
          importError.value = "Import cancelled by user.";
          setTimeout(() => { importInProgress.value = false; }, 1500);
          return;
        }
        await uploadFile(file);
      }
      importPhase.value = 'done';
      importError.value = `Imported ${newFiles.length} image(s).`;
      setTimeout(() => { importInProgress.value = false; }, 1500);
      refreshImages();
      fetchSidebarCounts();
    } catch (e) {
      importPhase.value = 'error';
      importInProgress.value = false;
      alert("One or more uploads failed: " + (e.message || e));
    }
  })();
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
    const url = `${BACKEND_URL}/pictures/search?query=${encodeURIComponent(
      q
    )}&threshold=0.3&top_n=1000`;
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

// Fetch score for an image if missing (called on thumbnail load)
async function fetchScoreIfMissing(img) {
  if (typeof img.score === "undefined" || img.score === null) {
    try {
      const res = await fetch(`${BACKEND_URL}/pictures/${img.id}`);
      if (res.ok) {
        const data = await res.json();
        if ("score" in data) {
          // Ensure reactivity
          Object.assign(img, { score: data.score });
        }
      }
    } catch (e) {}
  }
}

const isImageSelected = (id) => selectedImageIds.value.includes(id);

// Logic to determine if a selected image is on the outer edge of a selection group (use pagedImages)
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
    const resUnassigned = await fetch(`${BACKEND_URL}/category/summary?character_id=null`);
    if (resUnassigned.ok) {
      const data = await resUnassigned.json();
      categoryCounts.value[UNASSIGNED_PICTURES_ID] = data.image_count;
    }
  } catch {}
  // Each character
  await Promise.all(
    characters.value.map(async (char) => {
      try {
        const res = await fetch(`${BACKEND_URL}/category/summary?character_id=${encodeURIComponent(char.id)}`);
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
  return [...characters.value].sort((a, b) => {
    if (!a.name && !b.name) return 0;
    if (!a.name) return 1;
    if (!b.name) return -1;
    return a.name.localeCompare(b.name, undefined, { sensitivity: "base" });
  });
});
const characterThumbnails = ref({}); // { [characterId]: thumbnailUrl }
const loading = ref(false);
const error = ref(null);

// Reference filter for toolbar (local only, no backend refresh)
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
});
const images = ref([]);
const imagesLoading = ref(false);
const imagesError = ref(null);

const BACKEND_URL = "http://localhost:9537";

// Thumbnail size slider state
const thumbnailSizes = [128, 192, 256];
const thumbnailLabels = ["Small", "Medium", "Large"];
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
    // Test if the endpoint returns an image (status 200 and content-type image/png)
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

onMounted(() => {
  // Always select All Pictures at startup
  selectedCharacter.value = ALL_PICTURES_ID;
  selectedReferenceMode.value = false;
  fetchSortOptions();
  fetchCharacters().then(() => {
    // After loading characters, ensure All Pictures is still selected
    selectedCharacter.value = ALL_PICTURES_ID;
    selectedReferenceMode.value = false;
    // Explicitly trigger image loading if already on All Pictures
    if (
      selectedCharacter.value === ALL_PICTURES_ID &&
      !selectedReferenceMode.value
    ) {
      // This mimics the watcher logic
      images.value = [];
      imagesError.value = null;
      selectedImageIds.value = [];
      imagesLoading.value = true;
      let url = `${BACKEND_URL}/pictures?info=true`;
      fetch(url)
        .then((res) => {
          if (!res.ok) throw new Error("Failed to fetch images");
          return res.json();
        })
        .then((baseImages) => {
          images.value = baseImages.map((img) => ({
            ...img,
            score: typeof img.score !== "undefined" ? img.score : null,
            is_reference: Number(img.is_reference) || 0,
          }));
          setTimeout(updateColumns, 0);
        })
        .catch((e) => {
          imagesError.value = e.message;
        })
        .finally(() => {
          imagesLoading.value = false;
        });
    }
  });
  window.addEventListener("resize", updateColumns);
  watch(thumbnailSize, updateColumns);
  setTimeout(updateColumns, 100); // Initial update after mount
});

watch([selectedCharacter, selectedReferenceMode], async ([id, refMode]) => {
  refreshImages();
});

function handleOverlayKeydown(e) {
  // Don't trigger shortcuts if focus is in a text field
  const tag =
    e.target && e.target.tagName ? e.target.tagName.toLowerCase() : "";
  const isEditable =
    e.target &&
    (e.target.isContentEditable || tag === "input" || tag === "textarea");
  if (isEditable) return;
  // Ctrl+A: select all images in grid
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
    } else if (selectedImageIds.value.length) {
      // Use the last selected image as the reference for toggle value
      const lastImg = images.value.find(
        (i) =>
          i.id === selectedImageIds.value[selectedImageIds.value.length - 1]
      );
      if (lastImg) toggleReference(lastImg);
    } else if (images.value.length) {
      // If nothing selected, toggle the first image
      toggleReference(images.value[0]);
    }
    e.preventDefault();
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
  // Score shortcuts 1-5 (overlay: set score for overlayImage, grid: set for selection)
  if (/^[1-5]$/.test(e.key)) {
    showStars.value = true;
    if (overlayOpen.value && overlayImage.value) {
      setImageScore(overlayImage.value, Number(e.key));
    } else if (selectedImageIds.value.length) {
      patchScoreForSelection(Number(e.key));
    }
    e.preventDefault();
    return;
  } else {
    return;
  }
  const isCtrl = e.ctrlKey || e.metaKey;
  const isShift = e.shiftKey;
  if (isShift && lastSelectedIndex !== null) {
    // Range select
    const start = Math.min(lastSelectedIndex, nextIdx);
    const end = Math.max(lastSelectedIndex, nextIdx);
    const rangeIds = images.value.slice(start, end + 1).map((i) => i.id);
    const newSelection = isCtrl
      ? Array.from(new Set([...selectedImageIds.value, ...rangeIds]))
      : rangeIds;
    selectedImageIds.value = newSelection;
  } else if (isCtrl) {
    // Toggle selection of nextIdx
    const id = images.value[nextIdx].id;
    if (selectedImageIds.value.includes(id)) {
      selectedImageIds.value = selectedImageIds.value.filter((i) => i !== id);
    } else {
      selectedImageIds.value = [...selectedImageIds.value, id];
    }
    lastSelectedIndex = nextIdx;
  } else {
    // Single select
    selectedImageIds.value = [images.value[nextIdx].id];
    lastSelectedIndex = nextIdx;
  }
  e.preventDefault();
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
    if (selectedSort.value === "score_desc" || selectedSort.value === "score_asc") {
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
        const card = grid.querySelectorAll('.image-card')[insertIdx];
        if (card && card.scrollIntoView) {
          card.scrollIntoView({ behavior: 'smooth', block: 'center' });
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

const showStars = ref(true);

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
  const charObj = characters.value.find(c => c.id === characterId);
  console.log("[DROP] Drop target characterId:", characterId, "name:", charObj ? charObj.name : "(not found)");
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
    // Remove reassigned images from the current grid if not viewing All Pictures or Unassigned
    if (
      selectedCharacter.value !== ALL_PICTURES_ID &&
      selectedCharacter.value !== UNASSIGNED_PICTURES_ID &&
      selectedCharacter.value !== characterId
    ) {
      images.value = images.value.filter(img => !imageIds.includes(img.id));
      // Also remove these IDs from selection
      selectedImageIds.value = selectedImageIds.value.filter(id => images.value.some(img => img.id === id));
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
        }));
        // Remove any selected IDs not in the new images
        const newIds = new Set(images.value.map(img => img.id));
        selectedImageIds.value = selectedImageIds.value.filter(id => newIds.has(id));
        lastSelectedIndex = null;
        setTimeout(updateColumns, 0);
      }
    }
  } catch (e) {
    alert("Failed to assign character: " + (e.message || e));
  }
}

// Assign images as reference images for a character (set is_reference=true and character_id)
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
        }));
        // Remove any selected IDs not in the new images
        const newIds = new Set(images.value.map(img => img.id));
        selectedImageIds.value = selectedImageIds.value.filter(id => newIds.has(id));
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
</script>

<template>
  <v-app>
    <!-- Import Progress Modal (fixed, outside app-viewport) -->
    <div v-if="importInProgress" class="import-progress-modal">
      <div class="import-progress-content">
        <div class="import-progress-title">{{ importPhaseMessage }}</div>
        <div class="import-progress-bar-bg">
          <div class="import-progress-bar" :style="{ width: ((importTotal ? (importProgress / importTotal) : 0) * 100) + '%' }"></div>
        </div>
        <div class="import-progress-label">
          <template v-if="importPhase === 'hashing'">
            Hashing {{ importProgress }} / {{ importTotal }}
          </template>
          <template v-else-if="importPhase === 'checking'">
            Checking for duplicates...
          </template>
          <template v-else-if="importPhase === 'uploading'">
            Uploading {{ importProgress }} / {{ importTotal }}
          </template>
          <template v-else-if="importPhase === 'done'">
            Import complete!
          </template>
          <template v-else-if="importPhase === 'duplicates'">
            All files are duplicates.
          </template>
          <template v-else-if="importPhase === 'cancelled'">
            Import cancelled.
          </template>
          <template v-else-if="importPhase === 'error'">
            Import failed.
          </template>
          <span v-if="importError" class="import-progress-error">{{ importError }}</span>
        </div>
        <button class="cancel-button" @click="handleCancelImport" v-if="importPhase !== 'done' && importPhase !== 'duplicates' && importPhase !== 'cancelled' && importPhase !== 'error'">Cancel</button>
      </div>
    </div>
    <div class="app-viewport">
      <div class="top-toolbar">
        <v-btn
          icon
          @click="sidebarVisible = !sidebarVisible"
          title="Toggle sidebar"
          class="sidebar-toggle-btn"
          style="margin-right: 16px"
        >
          <v-icon>{{ sidebarVisible ? "mdi-menu-open" : "mdi-menu" }}</v-icon>
        </v-btn>
        <SearchBar
          v-model="searchQuery"
          placeholder="Search images..."
          style="min-width: 400px; max-width: 800px; margin-right: 16px"
          @search="searchImages"
        />
        <div class="toolbar-actions">
          <!-- Sorting dropdown -->
          <v-select
            v-model="selectedSort"
            :items="sortOptions"
            item-title="label"
            item-value="value"
            label="Sort by"
            dense
            hide-details
            style="min-width: 200px; max-width: 300px; margin-right: 8px"
          />

          <v-icon style="display: flex; align-items: center; height: 100%"
            >mdi-image-size-select-small</v-icon
          >
          <v-slider
            v-model="thumbnailSize"
            :min="128"
            :max="256"
            :step="32"
            :tick-labels="thumbnailLabels"
            class="slider"
            hide-details
            style="
              min-width: 256px;
              max-width: 320px;
              vertical-align: middle;
              margin-top: 4px;
              margin-bottom: 4px;
              margin-left: 8px;
              margin-right: 16px;
            "
          />
          <v-icon
            style="
              display: flex;
              align-items: center;
              height: 100%;
              margin-right: 16px;
            "
            >mdi-image-size-select-large</v-icon
          >
          <v-btn
            icon
            :color="referenceFilterMode ? 'orange darken-2' : 'grey'"
            @click="referenceFilterMode = !referenceFilterMode"
            title="Show only reference images"
            style="margin-right: 2px"
          >
            <v-icon>{{
              referenceFilterMode ? "mdi-trophy" : "mdi-trophy-outline"
            }}</v-icon>
          </v-btn>
          <v-btn
            icon
            :color="showStars ? 'amber darken-2' : 'grey'"
            @click="showStars = !showStars"
            title="Toggle star ratings"
            style="margin-left: 2px; margin-right: 2px"
          >
            <v-icon>{{ showStars ? "mdi-star" : "mdi-star-outline" }}</v-icon>
          </v-btn>
          <v-btn
            icon
            color="red darken-2"
            :disabled="!selectedImageIds.length"
            @click="deleteSelectedImages"
            title="Delete selected images"
            style="margin-left: 2px; margin-right: 2px"
          >
            <v-icon>mdi-trash-can-outline</v-icon>
          </v-btn>
        </div>
      </div>
      <div class="file-manager">
        <aside v-if="sidebarVisible" class="sidebar">
          <div
            class="sidebar-section-header"
            @click="sidebarSections.pictures = !sidebarSections.pictures"
          >
            <v-icon small style="margin-right: 8px">{{
              sidebarSections.pictures
                ? "mdi-chevron-down"
                : "mdi-chevron-right"
            }}</v-icon>
            Pictures
          </div>
          <transition name="fade">
            <div v-show="sidebarSections.pictures">
              <div
                :class="[
                  'sidebar-list-item',
                  { active: selectedCharacter === ALL_PICTURES_ID },
                ]"
                @click="selectedCharacter = ALL_PICTURES_ID"
              >
                <span class="sidebar-list-icon">
                  <v-icon size="44">mdi-image-multiple</v-icon>
                </span>
                <span class="sidebar-list-label">All Pictures</span>
                <span class="sidebar-list-count">{{ categoryCounts[ALL_PICTURES_ID] ?? '' }}</span>
              </div>
              <div
                :class="[
                  'sidebar-list-item',
                  { active: selectedCharacter === UNASSIGNED_PICTURES_ID },
                ]"
                @click="selectedCharacter = UNASSIGNED_PICTURES_ID"
              >
                <span class="sidebar-list-icon">
                  <v-icon size="44">mdi-help-circle-outline</v-icon>
                </span>
                <span class="sidebar-list-label">Unassigned Pictures</span>
                <span class="sidebar-list-count">{{ categoryCounts[UNASSIGNED_PICTURES_ID] ?? '' }}</span>
              </div>
            </div>
          </transition>
          <div
            class="sidebar-section-header"
            @click="sidebarSections.people = !sidebarSections.people"
          >
            <v-icon small style="margin-right: 8px">{{
              sidebarSections.people ? "mdi-chevron-down" : "mdi-chevron-right"
            }}</v-icon>
            <span style="flex: 1 1 auto"></span>
            <span
              style="
                display: grid;
                grid-template-columns: 32px 32px;
                gap: 0px;
                align-items: center;
                min-width: 64px;
              "
            >
              <v-icon
                v-if="
                  selectedCharacter &&
                  selectedCharacter !== ALL_PICTURES_ID &&
                  selectedCharacter !== UNASSIGNED_PICTURES_ID
                "
                class="delete-character-inline"
                color="white"
                style="cursor: pointer; justify-self: end"
                @click.stop="confirmDeleteCharacter"
                title="Delete selected character"
                >mdi-trash-can-outline
              </v-icon>
              <v-icon
                class="add-character-inline"
                @click.stop="addNewCharacter"
                title="Add character"
                style="justify-self: end"
                >mdi-plus</v-icon
              >
            </span>
          </div>
          <transition name="fade">
            <div v-show="sidebarSections.people">
              <div v-if="error" class="sidebar-error">{{ error }}</div>
              <div
                v-for="char in sortedCharacters"
                :key="char.id"
                class="sidebar-character-group"
              >
                <div
                  :class="[
                    'sidebar-list-item',
                    {
                      active: selectedCharacter === char.id,
                      droppable: dragOverCharacter === char.id,
                    },
                  ]"
                  @click="selectedCharacter = char.id"
                  @dragover.prevent="dragOverCharacter = char.id"
                  @dragleave="dragOverCharacter = null"
                  @drop.prevent="onCharacterDrop(char.id, $event)"
                >
                  <span class="sidebar-list-icon">
                    <img
                      :src="
                        characterThumbnails[char.id]
                          ? characterThumbnails[char.id]
                          : unknownPerson
                      "
                      alt=""
                      class="sidebar-character-thumb"
                    />
                  </span>
                  <span class="sidebar-list-label">
                    <template v-if="editingCharacterId === char.id">
                      <input
                        v-model="editingCharacterName"
                        class="edit-character-input"
                        @keydown.enter="saveEditingCharacter(char)"
                        @keydown.esc="cancelEditingCharacter"
                        @blur="saveEditingCharacter(char)"
                        ref="editInput"
                        style="
                          width: 90%;
                          font-size: 1em;
                          background: #fff;
                          color: #222;
                          border-radius: 4px;
                          border: 1px solid #bbb;
                          padding: 2px 6px;
                          outline: none;
                        "
                      />
                    </template>
                    <template v-else>
                      <span @dblclick.stop="startEditingCharacter(char)">
                        {{
                          char.name.charAt(0).toUpperCase() + char.name.slice(1)
                        }}
                      </span>
                    </template>
                  </span>
                  <span class="sidebar-list-count">{{ categoryCounts[char.id] ?? '' }}</span>
                </div>
              </div>
              <div v-if="loading" class="sidebar-loading">Loading...</div>
            </div>
          </transition>
        </aside>
        <main class="main-area" :class="{ 'full-width': !sidebarVisible }">
          <div
            :class="['main-content', selectedCharacter ? 'accent-border' : '']"
          >
            <template v-if="selectedCharacter">
              <div
                class="image-grid"
                :style="{ gridTemplateColumns: `repeat(${columns}, 1fr)` }"
                ref="gridContainer"
                style="position: relative"
                @dragenter.prevent="handleGridDragEnter"
                @dragover.prevent="handleGridDragOver"
                @dragleave.prevent="handleGridDragLeave"
                @drop.prevent="handleGridDrop"
                @scroll="onGridScroll"
              >
                <div
                  v-if="images.length === 0 && !imagesLoading && !imagesError"
                  class="empty-state"
                >
                  No images found for this character.
                </div>
                <div v-if="imagesLoading" class="empty-state">
                  Loading images...
                </div>
                <div v-if="imagesError" class="empty-state">
                  {{ imagesError }}
                </div>
                <div v-if="dragOverlayVisible" class="drag-overlay-grid">
                  <span>{{ dragOverlayMessage }}</span>
                </div>
                <div
                  v-for="(img, idx) in pagedImages"
                  :key="img.id"
                  class="image-card"
                  :class="[
                    isImageSelected(img.id) ? 'selected' : '',
                    getSelectionBorderClasses(idx),
                  ]"
                  @click="handleImageSelect(img, idx, $event)"
                  :draggable="isImageSelected(img.id)"
                  @dragstart="onImageDragStart(img, idx, $event)"
                >
                  <v-card>
                    <div class="star-overlay" v-if="showStars">
                      <v-icon
                        v-for="n in 5"
                        :key="n"
                        small
                        :color="
                          n <= (img.score || 0) ? 'amber' : 'grey lighten-1'
                        "
                        style="cursor: pointer"
                        @click.stop="setImageScore(img, n)"
                        >mdi-star</v-icon
                      >
                    </div>
                    <v-img
                      :src="`${BACKEND_URL}/thumbnails/${img.id}`"
                      :height="thumbnailSize"
                      :width="thumbnailSize"
                      @click.stop="
                        (e) => {
                          if (e.ctrlKey || e.metaKey || e.shiftKey) {
                            handleImageSelect(img, idx, e);
                          } else {
                            openOverlay(img);
                          }
                        }
                      "
                      @load="fetchScoreIfMissing(img)"
                      style="cursor: pointer"
                    />
                    <!-- Trophy icon for reference toggle -->
                    <v-btn
                      icon
                      size="small"
                      class="reference-trophy-btn"
                      :color="img.is_reference ? 'orange darken-2' : 'grey'"
                      @click.stop="toggleReference(img)"
                      title="Toggle reference picture"
                    >
                      <v-icon color="white">mdi-trophy</v-icon>
                    </v-btn>
                    <!-- Show date under thumbnail if sorting by date -->
                    <div
                      v-if="
                        selectedSort === 'date_desc' ||
                        selectedSort === 'date_asc'
                      "
                      class="thumbnail-date"
                    >
                      {{ new Date(img.created_at).toLocaleString() }}
                    </div>
                  </v-card>
                </div>
                <!-- Full image overlay -->
                <div
                  v-if="overlayOpen"
                  class="image-overlay"
                  @click.self="closeOverlay"
                >
                  <div class="overlay-content">
                    <button
                      class="overlay-close"
                      @click="closeOverlay"
                      aria-label="Close"
                    >
                      &times;
                    </button>
                    <div class="overlay-flex-row">
                      <button
                        class="overlay-nav overlay-nav-left"
                        @click.stop="showPrevImage"
                        aria-label="Previous"
                      >
                        &#8592;
                      </button>
                      <div class="overlay-img-container">
                        <div
                          class="overlay-star-row"
                          v-if="overlayImage"
                          style="
                            display: flex;
                            justify-content: center;
                            align-items: center;
                            margin-bottom: 12px;
                          "
                        >
                          <v-icon
                            v-for="n in 5"
                            :key="n"
                            large
                            :color="
                              n <= (overlayImage.score || 0)
                                ? 'amber'
                                : 'grey lighten-1'
                            "
                            style="cursor: pointer"
                            @click.stop="setImageScore(overlayImage, n)"
                            >mdi-star</v-icon
                          >
                        </div>
                        <div style="position: relative; display: inline-block">
                          <img
                            v-if="overlayImage"
                            :src="`${BACKEND_URL}/pictures/${overlayImage.id}`"
                            :alt="overlayImage.description || 'Full Image'"
                            class="overlay-img"
                          />
                          <v-btn
                            icon
                            size="small"
                            class="reference-trophy-btn overlay-trophy-btn"
                            :color="
                              overlayImage.is_reference
                                ? 'orange darken-2'
                                : 'grey'
                            "
                            @click.stop="toggleReference(overlayImage)"
                            title="Toggle reference picture"
                            style="
                              position: absolute;
                              bottom: 8px;
                              right: 8px;
                              z-index: 10;
                              background: rgba(0, 0, 0, 0.3);
                            "
                          >
                            <v-icon color="white">mdi-trophy</v-icon>
                          </v-btn>
                          <div
                            v-if="
                              overlayImage &&
                              overlayImage.tags &&
                              overlayImage.tags.length
                            "
                            class="overlay-tags"
                            style="
                              margin-top: 8px;
                              margin-bottom: 0;
                              text-align: center;
                            "
                          >
                            <span
                              v-for="tag in overlayImage.tags"
                              :key="tag"
                              class="overlay-tag"
                              style="
                                display: inline-flex;
                                align-items: center;
                                background: #eee;
                                color: #333;
                                border-radius: 16px;
                                padding: 4px 16px 4px 14px;
                                margin: 2px 2px;
                                font-size: 1.15em;
                                position: relative;
                                min-height: 32px;
                              "
                            >
                              {{ tag }}
                              <button
                                class="tag-delete-btn"
                                @click.stop="removeTagFromOverlayImage(tag)"
                                title="Remove tag"
                                style="
                                  background: none;
                                  border: none;
                                  color: #888;
                                  font-size: 1.25em;
                                  margin-left: 10px;
                                  cursor: pointer;
                                  display: flex;
                                  align-items: center;
                                  justify-content: center;
                                  height: 24px;
                                  width: 24px;
                                  padding: 0;
                                "
                              >
                                ×
                              </button>
                            </span>
                            <!-- Add + button at the end for adding tags -->
                            <button
                              class="tag-add-btn"
                              @click.stop="startAddTagOverlay()"
                              title="Add tag"
                              style="
                                display: inline-flex;
                                align-items: center;
                                justify-content: center;
                                background: #e0e0e0;
                                color: #333;
                                border: none;
                                border-radius: 16px;
                                font-size: 1.3em;
                                margin: 2px 2px;
                                height: 32px;
                                width: 32px;
                                cursor: pointer;
                                padding: 0;
                                vertical-align: middle;
                              "
                            >
                              +
                            </button>
                            <!-- Input for adding a tag, shown only when adding -->
                            <input
                              v-if="addingTagOverlay"
                              v-model="newTagOverlay"
                              @keydown.enter="confirmAddTagOverlay"
                              @blur="cancelAddTagOverlay"
                              class="tag-add-input"
                              style="
                                margin-left: 8px;
                                font-size: 1.1em;
                                border-radius: 8px;
                                border: 1px solid #bbb;
                                padding: 2px 8px;
                                min-width: 80px;
                                outline: none;
                              "
                              placeholder="New tag"
                              autofocus
                            />
                          </div>
                        </div>
                        <div class="overlay-desc">
                          {{ overlayImage?.description }}
                        </div>
                      </div>
                      <button
                        class="overlay-nav overlay-nav-right"
                        @click.stop="showNextImage"
                        aria-label="Next"
                      >
                        &#8594;
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </template>
            <template v-else>
              <div class="empty-state">Select a character to view images.</div>
            </template>
          </div>
        </main>
      </div>
    </div>
  </v-app>
</template>

<style scoped>
.app-viewport {
  position: fixed;
  inset: 0;
  width: 100vw;
  height: 100vh;
  overflow: hidden;
  z-index: 0;
}

.drag-overlay-grid {
  position: absolute;
  inset: 0;
  background: rgba(32, 32, 32, 0.25);
  color: #fff8e1;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 2.2rem;
  font-weight: 700;
  z-index: 1000;
  pointer-events: none;
  user-select: none;
  letter-spacing: 0.04em;
  text-shadow: 0 2px 8px #000a;
}

body {
  margin: 0;
  padding: 0;
}

.image-grid {
  display: grid;
  gap: 0;
  width: 100%;
  height: 100%;
  min-height: 64px;
  flex: 1 1 0%;
  padding: 4px 12px 4px 4px; /* Extra right padding for scrollbar */
  overflow-y: auto;
  background: #ddd;
  align-content: start;
  justify-content: start;
}
.image-card {
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  padding: 0;
  margin: 0;
  transition: box-shadow 0.2s, border 0.2s;
  position: relative;
  z-index: 0; /* Ensure stacking context */
  border: 3px solid transparent;
}

.reference-trophy-btn {
  position: absolute !important;
  right: 8px;
  bottom: 8px;
  z-index: 12;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  background: transparent;
}
.image-card.selected {
  z-index: 2;
  position: relative;
  border: 3px solid rgba(25, 118, 210, 0.32);
}
.selected-border-top {
  border-top-color: #1976d2 !important;
}
.selected-border-bottom {
  border-bottom-color: #1976d2 !important;
}
.selected-border-left {
  border-left-color: #1976d2 !important;
}
.selected-border-right {
  border-right-color: #1976d2 !important;
}
.image-card.selected::after {
  content: "";
  position: absolute;
  inset: 0;
  background: rgba(25, 118, 210, 0.32);
  border-radius: 0;
  pointer-events: none;
  z-index: 1; /* Lower than border */
}
.v-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  box-shadow: none;
  background: transparent;
  width: 100%;
  max-width: 256px;
  min-width: 128px;
  padding: 0;
  margin: 0;
}
.v-img {
  display: block;
  margin: 0 auto;
  box-sizing: border-box;
  padding: 0;
}
.v-card-title {
  width: 100%;
  max-width: 256px;
  min-height: 2.5em;
  font-size: 1rem;
  text-align: center;
  white-space: normal;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  word-break: break-word;
  margin: 0 auto 2px auto;
  padding: 2px 4px 0 4px;
}
/* Original simple file manager layout */
.file-manager {
  display: flex;
  flex-direction: row;
  width: 100vw;
  height: 100vh;
  min-height: 0;
  inset: 0;
  min-width: 0;
  background: #ccc;
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}
.sidebar {
  width: 280px;
  background: #506168ff;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  min-height: 100vh;
  box-sizing: border-box;
}
.sidebar-section-header {
  position: relative;
  font-size: 1.2rem;
  font-weight: 800;
  padding: 2px 2px 2px 2px;
  margin-bottom: 2px;
  margin-top: 0px;
  border-radius: 0px;
  box-shadow: 0 1px 1px rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  cursor: pointer;
  user-select: none;
  background: #7f95aa;
  color: #fff;
  transition: background 0.2s, color 0.2s;
}
/* Fade transition for collapsible sections */
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
.sidebar-list-item,
.sidebar-list-item.active {
  display: flex;
  align-items: center;
  min-height: 56px;
  padding: 8px 16px;
  cursor: pointer;
  border-radius: 0px;
  margin-bottom: 0px;
  font-size: 1em;
  font-weight: 500;
  background: transparent;
  color: #fff;
  transition: background 0.18s, color 0.18s;
  width: 100%;
}
.sidebar-list-item.active {
  background: #f0f0f055;
  color: #fff;
  border-right: 0;
  position: relative;
}

.sidebar-list-item.active::after {
  content: "";
  position: absolute;
  top: 0;
  right: 0;
  width: 20px;
  height: 100%;
  background: linear-gradient(
    to right,
    rgba(255, 165, 0, 0) 0%,
    rgba(255, 165, 0, 1) 100%
  );
  pointer-events: none;
  z-index: 2;
}

.sidebar-list-item:hover {
  background: #6c7a8a;
  color: #fff;
}

.sidebar-list-icon {
  display: flex;
  align-items: center;
  margin-right: 12px;
  width: 44px;
  min-width: 44px;
  justify-content: center;
}
.sidebar-list-label {
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  text-align: left;
}
.sidebar-character-thumb {
  width: 44px;
  height: 44px;
  object-fit: cover;
  border-radius: 6px;
  box-shadow: 0 0px 0px #bbb;
}
.sidebar-trophy-btn {
  margin-left: 4px;
}
.main-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: #eee;
  min-width: 0;
  min-height: 100vh;
  box-sizing: border-box;
  padding: 0;
  margin: 0;
  transition: width 0.2s;
}
.main-area.full-width {
  width: 100vw;
}
.sidebar-toggle-btn {
  min-width: 40px;
  min-height: 40px;
  margin-left: -8px;
}
.main-content {
  flex: 1 1 0%;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  justify-content: flex-start;
  padding: 0;
  border-left: 4px solid orange;
  transition: border-color 0.2s;
  min-height: 0;
  height: 100%;
}

.empty-state {
  color: #aaa;
  font-size: 1.2rem;
  margin-top: 32px;
  text-align: center;
}
.thumbnail-slider {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  width: 100%;
  margin-bottom: 32px;
  min-height: 48px;
}
.slider {
  flex: 1;
  margin: 0 8px;
  min-width: 120px;
  max-width: 220px;
}
.thumbnail-slider {
  margin-bottom: 4px;
  min-height: 32px;
}
.slider {
  margin: 0 2px;
  min-width: 80px;
  max-width: 180px;
}
/* Overlay modal for full image view */
.image-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  background: rgba(0, 0, 0, 0.85);
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
}
.overlay-content {
  position: relative;
  width: 80vw;
  height: 80vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: #222;
  border-radius: 8px;
  box-shadow: 0 2px 16px rgba(0, 0, 0, 0.5);
  padding: 24px 24px 16px 24px;
}
.overlay-flex-row {
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
}
.overlay-img-container {
  height: 90%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}
.overlay-img {
  max-width: 100%;
  max-height: 70vh;
  object-fit: contain;
  border-radius: 4px;
  background: #111;
  box-shadow: 0 1px 8px rgba(0, 0, 0, 0.4);
}
.overlay-close {
  position: absolute;
  top: 8px;
  right: 12px;
  font-size: 2.2rem;
  color: #fff;
  background: transparent;
  border: none;
  cursor: pointer;
  z-index: 10;
  line-height: 1;
  padding: 0 8px;
  transition: color 0.2s;
}
.overlay-close:hover {
  color: #ff5252;
}
.overlay-desc {
  color: #eee;
  margin-top: 12px;
  text-align: center;
  max-width: 70vw;
  word-break: break-word;
  font-size: 1.1rem;
}
/* Overlay navigation buttons */
.overlay-nav {
  position: absolute;
  top: 50%;
  font-size: 2.5rem;
  color: #000;
  background: #bbb;
  width: 52px;
  height: 52px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  user-select: none;
}

.overlay-nav-left {
  left: 12px;
}

.overlay-nav-right {
  right: 12px;
}

.overlay-nav:hover {
  background: #fff;
  color: #000;
}
.overlay-nav {
  z-index: 1200;
}
.top-toolbar {
  width: 100%;
  background: #cdcdcdff;
  min-height: 48px;
  display: flex;
  align-items: center;
  padding: 0 24px;
  border-bottom: 2px solid #888;
  margin-bottom: 0;
  z-index: 2;
  position: relative;
}
.toolbar-actions {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  margin-left: auto;
  margin-right: 0px;
  padding-right: 2px;
}
.star-overlay {
  position: absolute;
  top: 5px;
  right: 10px;
  transform: translateX(-25%);
  display: flex;
  flex-direction: row;
  z-index: 10;
  background: rgba(255, 255, 255, 0.85);
  border-radius: 6px;
  padding: 1px 4px 1px 2px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.08);
  font-size: 0.85em;
}
.star-overlay .v-icon {
  font-size: 16px !important;
  width: 16px;
  height: 16px;
}
.image-card {
  position: relative;
}
.v-card {
  position: relative;
  overflow: visible;
}
.v-img {
  display: block;
  position: relative;
  z-index: 1;
}
.add-character-btn {
  position: absolute;
  right: 8px;
  top: 50%;
  transform: translateY(-50%);
  z-index: 2;
}
.add-character-inline {
  color: #fff;
  font-size: 1.3em;
  cursor: pointer;
  vertical-align: middle;
  background: none !important;
  border: none;
  box-shadow: none;
  padding: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  position: absolute;
  right: 2px;
  top: 50%;
  transform: translateY(-50%);
  margin-left: 0;
}
.add-character-inline:hover {
  color: #ffe082;
}
.edit-character-input {
  font-size: 1em;
  background: #fff;
  color: #222;
  border-radius: 4px;
  border: 1px solid #bbb;
  padding: 2px 6px;
  outline: none;
  width: 90%;
  margin-left: 0;
}
/* Make disabled buttons more faded */
.v-btn.v-btn--disabled,
button[disabled] {
  opacity: 0.35 !important;
  filter: grayscale(30%);
  pointer-events: none;
}

.thumbnail-date {
  font-size: 0.85em;
  color: #666;
  margin-top: 2px;
  text-align: center;
  word-break: break-all;
}
.sidebar-list-count {
  font-size: 0.92em;
  color: #b0b8c9;
  min-width: 2.5em;
  text-align: right;
  margin-left: 8px;
  margin-right: 8px;
  font-weight: 400;
  opacity: 0.85;
  letter-spacing: 0.01em;
  align-self: center;
  display: inline-block;
}

/* Import Progress Modal Styles */
.import-progress-modal {
  position: fixed !important;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  background: rgba(32, 32, 32, 0.65) !important;
  z-index: 99999 !important;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: all;
}
.import-progress-content {
  background: #222;
  color: #fff8e1;
  padding: 32px 48px;
  border-radius: 16px;
  box-shadow: 0 4px 32px #000a;
  min-width: 320px;
  display: flex;
  flex-direction: column;
  align-items: center;
}
.import-progress-title {
  font-size: 1.5rem;
  font-weight: 700;
  margin-bottom: 24px;
}
.import-progress-bar-bg {
  width: 100%;
  height: 18px;
  background: #444;
  border-radius: 9px;
  overflow: hidden;
  margin-bottom: 16px;
}
.import-progress-bar {
  height: 100%;
  background: linear-gradient(90deg, #ff9800 0%, #ffc107 100%);
  border-radius: 9px 0 0 9px;
  transition: width 0.2s;
}
.import-progress-label {
  font-size: 1.1rem;
  margin-top: 8px;
}
.import-progress-error {
  color: #ff5252;
  margin-left: 12px;
}
</style>
