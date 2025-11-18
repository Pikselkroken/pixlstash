<template>
  <ImageOverlay
    :open="overlayOpen"
    :initialImage="overlayImage"
    :allImages="allGridImages"
    :backendUrl="props.backendUrl"
    @close="closeOverlay"
    @set-score="setScore"
  />
  <ImageImporter
    ref="imageImporterRef"
    :backendUrl="props.backendUrl"
    :selectedCharacterId="props.selectedCharacter"
    :allPicturesId="'__all__'"
    :unassignedPicturesId="'__unassigned__'"
    @import-finished="handleImagesUploaded"
  />
  <div
    class="image-grid"
    :style="{
      gridTemplateColumns: `repeat(${columns}, 1fr)`,
      position: 'relative',
    }"
    ref="gridContainer"
    @dragenter.prevent="handleGridDragEnter"
    @dragover.prevent="handleGridDragOver"
    @dragleave.prevent="handleGridDragLeave"
    @drop.prevent="handleGridDrop"
    @scroll="onGridScroll"
    @click="handleGridBackgroundClick"
  >
    <div v-if="dragOverlayVisible" class="drag-overlay">
      <div class="drag-overlay-message">{{ dragOverlayMessage }}</div>
    </div>
    <div
      v-for="(img, idx) in allGridImages"
      :key="img.id || idx"
      class="image-card"
      :draggable="isImageSelected(img.id)"
      @dragstart="onImageDragStart(img, idx, $event)"
      @click="handleImageCardClick(img, idx, $event)"
    >
      <v-card
        class="thumbnail-card"
        @click.stop="handleThumbnailClick(img, idx, $event)"
      >
        <div class="thumbnail-container">
          <template v-if="img.thumbnail">
            <img :src="img.thumbnail" class="thumbnail-img"/>
            <div
              class="thumbnail-index-overlay"
              :style="{
                position: 'absolute',
                top: '6px',
                left: '10px',
                color: 'red',
                fontWeight: 'bold',
                fontSize: '1.2em',
                textShadow: '0 0 2px #fff',
                zIndex: 20,
              }"
            >
              {{ idx}}
            </div>
          </template>
          <template v-else>
            <div
              class="thumbnail-placeholder"
              :style="{
                width: '100%',
                height: '100%',
                background: '#e0e0e0',
                borderRadius: '8px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: '1.5em',
                color: '#aaa',
                position: 'absolute',
                top: 0,
                left: 0,
              }"
            >
              <span> Image #{{ String(idx + 1).padStart(5, "0") }} </span>
            </div>
          </template>
          <!-- Score overlay -->
          <div v-if="props.showStars" class="star-overlay">
            <v-icon
              v-for="n in 5"
              :key="n"
              large
              :color="n <= (img.score || 0) ? 'orange' : 'grey darken-2'"
              style="cursor: pointer"
              @click.stop="setScore(img, n)"
              >mdi-star</v-icon
            >
          </div>
        </div>
      </v-card>
      <div v-if="isImageSelected(img.id)" class="selection-overlay"></div>
    </div>
  </div>
</template>

<script setup>
// Number of images before/after viewport to load thumbnails for
import { computed, onMounted, ref, watch, nextTick, onUnmounted } from "vue";
import {
  isSupportedMediaFile,
  dataTransferHasSupportedMedia,
  isSupportedVideoFile,
  getOverlayFormat,
} from "../utils/media.js";
import ImageImporter from "./ImageImporter.vue";
import ImageOverlay from "./ImageOverlay.vue";
import { useOverlayActions } from "../utils/useOverlayActions";

const emit = defineEmits(["open-overlay", "select-image", "clear-selection"]);

const imageImporterRef = ref(null);
// Handle images-uploaded event from ImageImporter
async function handleImagesUploaded(newIds) {
  await fetchTotalImageCount();
  // Do NOT clear thumbnails; keep existing ones
  // Reset loadedRanges so new thumbnails can be fetched
  loadedRanges.value = [];
  // Recalculate visible indices and fetch thumbnails for visible images
  nextTick(() => {
    if (gridContainer.value) {
      onGridScroll({ target: gridContainer.value });
    }
  });
}
// Props
const props = defineProps({
  thumbnailSize: Number,
  sidebarVisible: Boolean,
  backendUrl: String,
  selectedCharacter: { type: [String, Number, null], default: null },
  selectedSet: { type: [String, Number, null], default: null },
  searchQuery: String,
  selectedSort: String,
  showStars: Boolean,
});

const LAZY_THUMB_WINDOW = 40;

// Image overlay
const overlayOpen = ref(false);
const overlayImage = ref(null);

// Drag-and-drop overlay state
const dragOverlayVisible = ref(false);
const dragOverlayMessage = ref("");
const dragSource = ref(null);

// --- Multi-selection state ---
// Local selection state (mirrors parent prop)
const selectedImageIds = ref([]);
let lastSelectedIndex = null;

// --- Overlay ---
async function fetchImageInfo(imageId) {
  try {
    const res = await fetch(
      `${props.backendUrl}/pictures/${imageId}?info=true`
    );
    if (!res.ok) throw new Error("Failed to fetch tags");
    return await res.json();
  } catch (e) {
    console.error("Tag fetch failed:", e);
    return [];
  }
}

async function openOverlay(img) {
  if (img && img.id) {
    const latestInfo = await fetchImageInfo(img.id);
    // Merge all fields from latestInfo into img
    Object.assign(img, latestInfo);
  }
  overlayImage.value = { ...img };
  overlayOpen.value = true;
}

function closeOverlay() {
  overlayOpen.value = false;
}

async function setScore(img, n) {
  const newScore = (img.score || 0) === n ? 0 : n;
  const imageId = img.id || (overlayImage.value && overlayImage.value.id);
  if (!imageId) {
    alert("Failed to set score: image id is missing.");
    return;
  }
  try {
    console.debug("PATCH /pictures/", imageId, "?score=", newScore);
    const res = await fetch(
      `${props.backendUrl}/pictures/${imageId}?score=${newScore}`,
      { method: "PATCH" }
    );
    if (!res.ok) throw new Error(`Failed to set score for image ${imageId}`);
    // Fetch latest info after score update
    const latestInfo = await fetchImageInfo(imageId);
    if (overlayImage.value && overlayImage.value.id === imageId) {
      overlayImage.value = { ...overlayImage.value, ...latestInfo };
    }
    // ...existing code for sorting and updating images array...
    if (
      props.selectedSort.value === "score_desc" ||
      props.selectedSort.value === "score_asc"
    ) {
      const idx = images.value.findIndex((i) => i.id === imageId);
      if (idx === -1) return;
      img.score = newScore;
      images.value.splice(idx, 1);
      let insertIdx = 0;
      if (props.selectedSort.value === "score_desc") {
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

async function fetchCharacter(id) {
  try {
    const res = await fetch(`${props.backendUrl}/characters/${id}`);
    if (!res.ok) throw new Error("Failed to fetch character");
    const char = await res.json();
    return char;
  } catch (e) {
    console.error("Character fetch failed:", e);
  }
  return null;
}

// Drag-and-drop overlay handlers
async function handleGridDragEnter(e) {
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

  const itemCount = e.dataTransfer.items.length;
  if (
    props.selectedCharacter &&
    props.selectedCharacter !== "__all__" &&
    props.selectedCharacter !== "__unassigned__"
  ) {
    const character = await fetchCharacter(props.selectedCharacter);
    const characterName = character ? "for " + character.name : "";
    dragOverlayMessage.value = `Drop files here to import ${itemCount} file(s) ${characterName}`;
  } else {
    dragOverlayMessage.value = `Drop files here to import ${itemCount} file(s)`;
  }
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
  // Trigger import directly in ImageGrid
  if (imageImporterRef.value && files.length) {
    imageImporterRef.value.startImport(files, {
      backendUrl: props.backendUrl,
      selectedCharacterId: props.selectedCharacter,
      allPicturesId: "__all__",
      unassignedPicturesId: "__unassigned__",
    });
  }
}

// Method to handle global key presses from App.vue
function onGlobalKeyPress(key, event) {
  if (gridContainer.value) {
    if (key === "Home") {
      gridContainer.value.scrollTop = 0;
      onGridScroll({ target: gridContainer.value });
    } else if (key === "End") {
      gridContainer.value.scrollTop = gridContainer.value.scrollHeight;
      onGridScroll({ target: gridContainer.value });
    } else if (key === "PageUp") {
      gridContainer.value.scrollTop -= gridContainer.value.clientHeight;
      onGridScroll({ target: gridContainer.value });
    } else if (key === "PageDown") {
      gridContainer.value.scrollTop += gridContainer.value.clientHeight;
      onGridScroll({ target: gridContainer.value });
    }
  }
}

// Local state for all image IDs
// Total image count for paging and 'End' key
const totalImageCount = ref(0);
const imagesLoading = ref(false);
const imagesError = ref(null);

function buildPictureIdsQueryParams() {
  const params = new URLSearchParams();
  if (props.selectedCharacter && props.selectedCharacter !== "__all__") {
    if (props.selectedCharacter === "__unassigned__") {
      params.append("primary_character_id", "");
    } else {
      params.append("primary_character_id", props.selectedCharacter);
    }
  }

  if (props.searchQuery && props.searchQuery.trim()) {
    params.append("query", props.searchQuery.trim());
  }
  if (props.selectedSort && props.selectedSort.trim()) {
    params.append("sort", props.selectedSort.trim());
  }
  return params.toString();
}

// Fetch total image count for current filters
async function fetchTotalImageCount() {
  imagesLoading.value = true;
  imagesError.value = null;
  try {
    const params = buildPictureIdsQueryParams();
    const url = `${props.backendUrl}/pictures?count=true&${params}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error("Failed to fetch image count");
    const data = await res.json();
    totalImageCount.value = data.count || 0;
    console.debug(
      "[IMAGE COUNT] Total images for current filters:",
      totalImageCount.value
    );
  } catch (e) {
    imagesError.value = e.message;
    totalImageCount.value = 0;
  } finally {
    imagesLoading.value = false;
  }
}

onMounted(() => {
  fetchTotalImageCount();
});

watch(
  [
    () => props.selectedCharacter,
    () => props.selectedSet,
    () => props.searchQuery,
    () => props.selectedSort,
  ],
  () => {
    // Reset loaded ranges and thumbnails when filters change
    loadedRanges.value = [];
    allGridImages.value = [];
    fetchTotalImageCount().then(() => {
      nextTick(() => {
        if (gridContainer.value) {
          onGridScroll({ target: gridContainer.value });
        }
      });
    });
  }
);

// Track loaded batch ranges to avoid duplicate requests
const loadedRanges = ref([]);
// Debounce timer for scroll-triggered fetches
let thumbFetchTimeout = null;

// Track which indices are visible in the grid
const visibleStart = ref(0);
const visibleEnd = ref(0);

// Compute grid images (id, idx, thumbnail)
const allGridImages = ref([]);

// Batch fetch metadata (including thumbnail) for visible range
async function fetchThumbnailsBatch(start, end) {
  start = Math.max(0, start);
  end = Math.max(start, end);

  console.debug(`[BATCH REQUEST] start=${start}, end=${end}, loadedRanges=${JSON.stringify(loadedRanges.value)}`);
  // Check if this batch range is already loaded
  for (const range of loadedRanges.value) {
    if (start >= range[0] && end <= range[1]) {
      return; // Already loaded
    }
  }
  // Fetch batch metadata for visible range
  try {
    const params = buildPictureIdsQueryParams();
    const url = `${props.backendUrl}/pictures?info=true&offset=${start}&limit=${end-start}&${params}`;
    console.debug(`[BATCH FETCH] Requesting: ${url}`);
    const res = await fetch(url);
    if (res.ok) {
      const images = await res.json();
      console.debug(`[BATCH RESPONSE] Received ${images.length} images:`, images.map(img => img.id));
      // Prepare grid image objects
      const gridImages = images.map((img, idx) => ({
        ...img,
        idx,
        thumbnail: null,
      }));
      // Now fetch thumbnails for these IDs
      const ids = images.map(img => img.id);
      if (ids.length) {
        const thumbRes = await fetch(`${props.backendUrl}/thumbnails`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ids })
        });
        if (thumbRes.ok) {
          const thumbData = await thumbRes.json();
          for (const gridImg of gridImages) {
            gridImg.thumbnail = thumbData[gridImg.id]
              ? `data:image/png;base64,${thumbData[gridImg.id]}`
              : null;
          }
        } else {
          for (const gridImg of gridImages) {
            gridImg.thumbnail = null;
          }
        }
      }
      // Ensure allGridImages.value is sized to totalImageCount
      if (allGridImages.value.length < totalImageCount.value) {
        for (let i = allGridImages.value.length; i < totalImageCount.value; i++) {
          allGridImages.value[i] = { id: null, thumbnail: null, idx: i };
        }
      }
      // Insert/update images at their correct indices
      for (let i = 0; i < gridImages.length; i++) {
        const img = gridImages[i];
        allGridImages.value[start + i] = img;
      }
      loadedRanges.value.push([start, end]);
      // Log current visible IDs
      const startVis = Math.max(0, visibleStart.value);
      const endVis = Math.min(allGridImages.value.length, visibleEnd.value);
      const visibleIds = allGridImages.value.slice(startVis, endVis).map(img => img.id);
    }
  } catch (err) {
    console.error('[BATCH ERROR]', err);
  }
}

function updateVisibleThumbnails() {
  let start = Math.max(0, visibleStart.value - LAZY_THUMB_WINDOW);
  let end = Math.min(totalImageCount.value, visibleEnd.value + LAZY_THUMB_WINDOW);
  console.log("Fetch range: ", start, "to", end);

  // Debounce fetches to avoid excessive requests
  if (thumbFetchTimeout) clearTimeout(thumbFetchTimeout);
  thumbFetchTimeout = setTimeout(() => {
    fetchThumbnailsBatch(start, end);
  }, 80);
}

// Update visible indices on scroll
function onGridScroll(e) {
  nextTick(() => {
  const gridEl = gridContainer.value;
  const gridRect = gridEl.getBoundingClientRect();
  const cards = gridEl.querySelectorAll('.image-card');
  let visibleIndices = [];
  cards.forEach((card, idx) => {
    const rect = card.getBoundingClientRect();
    if (rect.bottom > gridRect.top && rect.top < gridRect.bottom) {
      visibleIndices.push(idx);
    }
  });
  if (visibleIndices.length) {
    visibleStart.value = Math.min(...visibleIndices);
    visibleEnd.value = Math.max(...visibleIndices) + 1;
  } else {
    visibleStart.value = 0;
    visibleEnd.value = 0;
  }
  console.debug("Visible indices:", visibleStart.value, visibleEnd.value);

  updateVisibleThumbnails();
});
}

watch(totalImageCount, () => {
  nextTick(() => {
    if (gridContainer.value) {
      onGridScroll({ target: gridContainer.value });
    }
  });
});

// Internal columns state
const columns = ref(1);

// Selection logic
const isImageSelected = (id) =>
  selectedImageIds.value && selectedImageIds.value.includes(id);

// Event handlers: these should emit events or call parent-provided functions
const onImageDragStart = (img, idx, event) => {
  if (selectedImageIds.value && selectedImageIds.value.includes(img.id)) {
    event.dataTransfer.setData(
      "application/json",
      JSON.stringify({ imageIds: selectedImageIds.value })
    );
  } else {
    event.dataTransfer.setData(
      "application/json",
      JSON.stringify({ imageIds: [img.id] })
    );
  }
  event.dataTransfer.effectAllowed = "move";
};

function handleImageCardClick(img, idx, event) {
  if (!img.id) return;
  const isCtrl = event.ctrlKey || event.metaKey;
  const isShift = event.shiftKey;
  let newSelection = [...selectedImageIds.value];
  if (isCtrl) {
    // Toggle selection
    if (newSelection.includes(img.id)) {
      console.debug("Deselecting image ID:", img.id);
      newSelection = newSelection.filter((id) => id !== img.id);
    } else {
      console.debug("Selecting image ID:", img.id);
      newSelection.push(img.id);
    }
    lastSelectedIndex = idx;
  } else if (isShift && lastSelectedIndex !== null) {
    // Range select
    const start = Math.min(lastSelectedIndex, idx);
    const end = Math.max(lastSelectedIndex, idx);
    const idsInRange = allGridImages.value
      .slice(start, end + 1)
      .map((i) => i.id)
      .filter(Boolean);
    newSelection = Array.from(new Set([...newSelection, ...idsInRange]));
  } else {
    // Single select
    newSelection = [img.id];
    lastSelectedIndex = idx;
  }
  selectedImageIds.value = newSelection;
  console.log("New selection:", newSelection);
  emit("select-image", newSelection);
}

function handleThumbnailClick(img, idx, event) {
  console.debug("Thumbnail clicked. Id=", img.id, "Idx=", idx, "event=", event);
  if (!img.id) return;
  const isCtrl = event.ctrlKey || event.metaKey;
  const isShift = event.shiftKey;
  if (isCtrl || isShift) {
    return handleImageCardClick(img, idx, event);
  }
  openOverlay(img);
}

// Clear selection when clicking grid background
function handleGridBackgroundClick(e) {
  if (!e.target.closest(".image-card")) {
    console.log("Clearing selection");
    selectedImageIds.value = [];
    lastSelectedIndex = null;
    emit("clear-selection");
  }
}

// --- Text & Display Utilities ---
function formatLikenessScore(score) {
  if (typeof score !== "number") return "";
  return `Likeness: ${(score * 100).toFixed(2)}%`;
}

const gridContainer = ref(null);

function updateColumns() {
  nextTick(() => {
    const el = gridContainer.value?.$el || gridContainer.value;
    if (!el) return;
    const containerWidth = el.offsetWidth;
    columns.value = Math.max(
      1,
      Math.floor(containerWidth / (props.thumbnailSize + 32))
    );
  });
}

onMounted(() => {
  updateColumns();
  window.addEventListener("resize", updateColumns);
});

watch(
  () => props.thumbnailSize,
  () => {
    updateColumns();
  }
);

onUnmounted(() => {
  window.removeEventListener("resize", updateColumns);
});

// Expose the grid DOM node to parent
defineExpose({ gridEl: gridContainer, onGlobalKeyPress });

onMounted(() => {
  if (gridContainer.value) {
    gridContainer.value.addEventListener("scroll", onGridScroll);
  }
});

watch(
  () => allGridImages.value.length,
  (len) => {
    if (len > 0 && gridContainer.value) {      
      nextTick(() => {
        onGridScroll({ target: gridContainer.value });
      });
    }
  }
);


</script>
<style scoped>
.drag-overlay {
  position: absolute;
  inset: 0;
  background: rgba(255, 166, 0, 0.2);
  z-index: 9999;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: all;
  border: 8px solid #ffa600; /* thick orange border */
  border-radius: 16px; /* rounded corners */
  box-sizing: border-box;
  transition: border-color 0.2s, background 0.2s;
  color: #ffffff;
  font-size: 3em;
  font-weight: bold;
}
.image-grid {
  display: grid;
  gap: 0;
  width: 100%;
  box-sizing: border-box;
  flex: 1 1 0%;
  min-height: 0;
  overflow-y: auto;
  padding: 2px 2px 2px 2px !important;
  overflow: auto;
  scrollbar-width: 16px !important;
  scrollbar-color: orange #ddd;
  align-content: start;
  justify-content: start;
}
.image-grid::-webkit-scrollbar {
  width: 8px;
}
.image-grid::-webkit-scrollbar-thumb {
  background: orange;
  border-radius: 8px;
}
.image-grid::-webkit-scrollbar-track {
  background: #ddd;
}
.image-card {
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  padding: 0px;
  margin: 0;
  transition: box-shadow 0.2s, border 0.2s;
  position: relative;
  z-index: 0; /* Ensure stacking context */
  border: 0px solid transparent;
}
.selection-overlay {
  position: absolute;
  inset: 0;
  background: rgba(25, 118, 210, 0.62); /* semi-transparent blue */
  pointer-events: none;
  z-index: 2;
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
  padding: 4px;
  margin: 0;
}
.star-overlay {
  position: absolute;
  top: 8px;
  right: 8px;
  z-index: 12;
  display: flex;
  flex-direction: row;
  background: rgba(255, 255, 255, 0.7);
  border-radius: 4px;
  box-shadow: none;
  font-size: 0.85em;
  margin: 4px 4px 4px 4px;
}
.star-overlay:hover {
  background: rgba(255, 255, 255, 1);
}
.star-overlay .v-icon {
  font-size: 20px !important;
  width: 20px;
  height: 20px;
}
.image-card {
  position: relative;
}
.thumbnail-info {
  font-size: 0.85em;
  color: #666;
  margin-top: 2px;
  text-align: center;
  word-break: break-all;
}
.thumbnail-container {
  width: 100%;
  position: relative;
  display: block;
  aspect-ratio: 1 / 1;
}
.thumbnail-img {
  width: 100%;
  height: 100%;
  aspect-ratio: 1 / 1;
  object-fit: cover;
  display: block;
  border-radius: 8px;
  position: absolute;
  top: 0;
  left: 0;
  transition: transform 0.18s cubic-bezier(0.4, 2, 0.6, 1), box-shadow 0.18s;
}
/* Spinner for thumbnail loading */
.thumbnail-loading {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: 40px;
  height: 40px;
  border: 4px solid #eee;
  border-top: 4px solid #1976d2;
  border-radius: 50%;
  animation: spin 1s linear infinite;
  z-index: 10;
}

@keyframes spin {
  0% {
    transform: translate(-50%, -50%) rotate(0deg);
  }
  100% {
    transform: translate(-50%, -50%) rotate(360deg);
  }
}
.thumbnail-container:hover .thumbnail-img,
.thumbnail-container:focus-within .thumbnail-img {
  transform: scale(1.02);
  box-shadow: 0 4px 24px 0 rgba(25, 118, 210, 0.2),
    0 1.5px 6px 0 rgba(0, 0, 0, 0.3);
  z-index: 2;
  transition: transform 0.18s cubic-bezier(0.4, 2, 0.6, 1), box-shadow 0.18s;
}

.thumbnail-card {
  width: 100%;
  height: 100%;
  max-width: none;
  min-width: none;
  position: relative;
}
/* Overlay for image index on thumbnail */
.thumbnail-index-overlay {
  pointer-events: none;
}
</style>
