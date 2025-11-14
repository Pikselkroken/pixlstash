<template>
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
    @click="handleGridBackgroundClick"
  >
    <div
      v-if="allGridImages.length === 0 && !props.imagesLoading && !props.imagesError"
      class="empty-state"
    >
      No images found for this character.
    </div>
    <div v-if="props.imagesError" class="empty-state">
      {{ props.imagesError }}
    </div>
    <div v-if="dragOverlayVisible" class="drag-overlay-grid">
      <span>{{ dragOverlayMessage }}</span>
    </div>
    <div
      v-for="(img, idx) in allGridImages"
      :key="img.id || idx"
      class="image-card"
      :class="[
        isImageSelected(img.id) ? 'selected' : '',
        getSelectionBorderClasses(idx),
      ]"
      :draggable="isImageSelected(img.id)"
      @dragstart="onImageDragStart(img, idx, $event)"
      @click="handleGridBackgroundClick"
    >
      <v-card class="thumbnail-card">
        <div class="thumbnail-container">
          <template v-if="img.thumbnail">
            <img :src="img.thumbnail" class="thumbnail-img" />
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
              <span>
                Image #{{ String(idx + 1).padStart(5, '0') }}
              </span>
            </div>
          </template>
        </div>
      </v-card>
    </div>
  </div>
</template>
<script setup>
// Number of images before/after viewport to load thumbnails for
const LAZY_THUMB_WINDOW = 40;
import {
  defineEmits,
  computed,
  onMounted,
  ref,
  watch,
  nextTick,
  onUnmounted,
} from "vue";
const emit = defineEmits([
  "open-overlay",
  "select-image",
  "clear-selection",
]);


// Props
const props = defineProps({
  images: Array,
  imagesLoading: Boolean,
  imagesError: String,
  thumbLoaded: Object,
  thumbnailSize: Number,
  sidebarVisible: Boolean,
  selectedImageIds: Array,
  showStars: Boolean,
  selectedSort: String,
  dragOverlayVisible: Boolean,
  dragOverlayMessage: String,
  BACKEND_URL: String,
  selectedCharacter: String,
  selectedSet: [String, Number, null],
  searchQuery: String,
  selectedCharacterObj: Object,
  config: Object,
  extractKeywords: Function,
});

// Method to handle global key presses from App.vue
function onGlobalKeyPress(key, event) {
  if (gridContainer.value) {
    if (key === 'Home') {
      gridContainer.value.scrollTop = 0;
      onGridScroll({ target: gridContainer.value });
    } else if (key === 'End') {
      gridContainer.value.scrollTop = gridContainer.value.scrollHeight;
      onGridScroll({ target: gridContainer.value });
    } else if (key === 'PageUp') {
      gridContainer.value.scrollTop -= gridContainer.value.clientHeight;
      onGridScroll({ target: gridContainer.value });
    } else if (key === 'PageDown') {
      gridContainer.value.scrollTop += gridContainer.value.clientHeight;
      onGridScroll({ target: gridContainer.value });
    }
  }
}


// Local state for all image IDs
const allImageIds = ref([]);
const imagesLoading = ref(false);
const imagesError = ref(null);

function buildPictureIdsQueryParams() {
  const params = new URLSearchParams();
  if (props.selectedCharacter && props.selectedCharacter !== '__all__') {
    if (props.selectedCharacter === '__unassigned__') {
      params.append('primary_character_id', '');
    } else {
      params.append('primary_character_id', props.selectedCharacter);
    }
  }
  if (props.selectedSet !== null && typeof props.selectedSet !== 'undefined') {
    params.append('set_id', props.selectedSet);
  }
  if (props.searchQuery && props.searchQuery.trim()) {
    params.append('query', props.searchQuery.trim());
  }
  if (props.selectedSort && props.selectedSort.trim()) {
    params.append('sort', props.selectedSort.trim());
  }
  return params.toString();
}

async function fetchAllPictureIds() {
  imagesLoading.value = true;
  imagesError.value = null;
  try {
    const url = `${props.BACKEND_URL}/picture_ids?${buildPictureIdsQueryParams()}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error("Failed to fetch picture ids");
    const ids = await res.json();
    allImageIds.value = ids;
  } catch (e) {
    imagesError.value = e.message;
    allImageIds.value = [];
  } finally {
    imagesLoading.value = false;
  }
}

onMounted(() => {
  fetchAllPictureIds();
});

watch([
  () => props.selectedCharacter,
  () => props.selectedSet,
  () => props.searchQuery,
  () => props.selectedSort
], () => {
  // Reset loaded ranges and thumbnails when filters change
  thumbnails.value = {};
  loadedRanges.value = [];
  fetchAllPictureIds();
});


// Thumbnails state: id -> thumbnail data (or null if not loaded)
const thumbnails = ref({});
// Track loaded batch ranges to avoid duplicate requests
const loadedRanges = ref([]);
// Debounce timer for scroll-triggered fetches
let thumbFetchTimeout = null;

// Track which indices are visible in the grid
const visibleStart = ref(0);
const visibleEnd = ref(0);

// Compute grid images (id, idx, thumbnail)
const allGridImages = computed(() => {
  return allImageIds.value.map((id, idx) => ({
    id,
    idx,
    thumbnail: thumbnails.value[id] || null,
  }));
});


// Batch fetch metadata (including thumbnail) for visible range
async function fetchThumbnailsBatch(start, end) {
  // Check if this batch range is already loaded
  for (const range of loadedRanges.value) {
    if (start >= range[0] && end <= range[1]) {
      return; // Already loaded
    }
  }
  // Only fetch for IDs not already loaded
  const idsToFetch = [];
  for (let i = start; i < end; i++) {
    const id = allImageIds.value[i];
    if (id && thumbnails.value[id] === undefined) {
      idsToFetch.push(id);
    }
  }
  if (!idsToFetch.length) {
    loadedRanges.value.push([start, end]);
    return;
  }
  // Use /pictures?info=true&offset=...&limit=...
  const offset = start;
  const limit = end - start;
  const params = new URLSearchParams();
  params.append('info', 'true');
  params.append('offset', offset);
  params.append('limit', limit);
  // Add filters
  if (props.selectedCharacter && props.selectedCharacter !== '__all__') {
    if (props.selectedCharacter === '__unassigned__') {
      params.append('primary_character_id', '');
    } else {
      params.append('primary_character_id', props.selectedCharacter);
    }
  }
  if (props.selectedSet !== null && typeof props.selectedSet !== 'undefined') {
    params.append('set_id', props.selectedSet);
  }
  if (props.searchQuery && props.searchQuery.trim()) {
    params.append('query', props.searchQuery.trim());
  }
  if (props.selectedSort && props.selectedSort.trim()) {
    params.append('sort', props.selectedSort.trim());
  }
  try {
    const url = `${props.BACKEND_URL}/pictures?${params.toString()}`;
    const res = await fetch(url);
    if (res.ok) {
      const images = await res.json();
      for (const img of images) {
        if (img.id && img.thumbnail) {
          thumbnails.value[img.id] = `${props.BACKEND_URL}/thumbnails/${img.id}`;
        } else if (img.id) {
          thumbnails.value[img.id] = null;
        }
      }
      loadedRanges.value.push([start, end]);
    }
  } catch {
    // Ignore errors for now
  }
}

function updateVisibleThumbnails() {
  let midPoint = Math.min(Math.max(0, Math.floor((visibleStart.value + visibleEnd.value) / 2)), allImageIds.value.length );

  let start = midPoint - LAZY_THUMB_WINDOW
  let end = midPoint + LAZY_THUMB_WINDOW;
  
  // Debounce fetches to avoid excessive requests
  if (thumbFetchTimeout) clearTimeout(thumbFetchTimeout);
  thumbFetchTimeout = setTimeout(() => {
    fetchThumbnailsBatch(start, end);
  }, 80);
}

// Update visible indices on scroll
function onGridScroll(e) {
  const el = e.target;
  if (!el) return;
    // Measure actual row height from the DOM
    let cardHeight = props.thumbnailSize + 24;
    const firstCard = gridContainer.value?.querySelector('.image-card');
    if (firstCard) {
      const rect = firstCard.getBoundingClientRect();
      cardHeight = rect.height;
    }
  const scrollTop = el.scrollTop;
  const gridHeight = el.clientHeight;
  const firstVisibleRow = Math.floor(scrollTop / cardHeight);
  const rowsVisible = Math.ceil(gridHeight / cardHeight);
  const cols = columns.value;
  const totalImages = allImageIds.value.length;
  visibleStart.value = firstVisibleRow * cols;
  visibleEnd.value =visibleStart.value + rowsVisible * cols;
  updateVisibleThumbnails();
}

onMounted(() => {
  nextTick(() => {
    // Initial visible range
    if (gridContainer.value) {
      onGridScroll({ target: gridContainer.value });
    }
  });
});

watch(allImageIds, () => {
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
  props.selectedImageIds && props.selectedImageIds.includes(id);

const getSelectionBorderClasses = (idx) => {
  const sorted = allGridImages.value;
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

// Event handlers: these should emit events or call parent-provided functions
const onImageDragStart = (img, idx, event) => {
  if (props.selectedImageIds && props.selectedImageIds.includes(img.id)) {
    event.dataTransfer.setData(
      "application/json",
      JSON.stringify({ imageIds: props.selectedImageIds })
    );
  } else {
    event.dataTransfer.setData(
      "application/json",
      JSON.stringify({ imageIds: [img.id] })
    );
  }
  event.dataTransfer.effectAllowed = "move";
};

const handleGridBackgroundClick = (e) => {
  if (!e.target.closest(".thumbnail-card")) {
    emit("clear-selection");
  }
};

const handleImageSelect = (img, idx, event) => {
  emit("select-image", img, idx, event);
};

const openOverlay = (img) => {
  emit("open-overlay", img);
};

const setImageScore = (img, n) => {
  emit("set-image-score", img, n);
};

const formatLikenessScore = (score) =>
  score !== undefined ? score.toFixed(2) : "";

const isSupportedVideoFile = (format) => {
  if (!format) return false;
  const videoExts = ["mp4", "avi", "mov", "webm", "mkv", "flv", "wmv", "m4v"];
  return videoExts.includes(format.toLowerCase());
};

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

watch(
  () => props.images,
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

</script>
<style scoped>
.image-grid {
  display: grid;
  gap: 0;
  width: 100%;
  box-sizing: border-box;
  flex: 1 1 0%;
  min-height: 0;
  overflow-y: auto;
  padding: 0 0px 0 0px !important; /* Extra right padding for visible scrollbar */
  overflow: auto;
  scrollbar-width: 16px !important;
  scrollbar-color: orange #ddd;
  align-content: start;
  justify-content: start;
  padding-bottom: 24px !important;
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
  padding: 0;
  margin: 0;
  transition: box-shadow 0.2s, border 0.2s;
  position: relative;
  z-index: 0; /* Ensure stacking context */
  border: 3px solid transparent;
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
.v-card {
  position: relative;
  overflow: visible;
  max-width: none;
  min-width: none;
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
</style>
