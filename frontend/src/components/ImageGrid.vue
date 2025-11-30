<template>
  <ImageOverlay
    :open="overlayOpen"
    :initialImage="overlayImage"
    :allImages="allGridImages"
    :backendUrl="props.backendUrl"
    @close="closeOverlay"
    @apply-score="applyScore"
  />
  <ImageImporter
    ref="imageImporterRef"
    :backendUrl="props.backendUrl"
    :selectedCharacterId="props.selectedCharacter"
    :allPicturesId="'__all__'"
    :unassignedPicturesId="'__unassigned__'"
    @import-finished="handleImagesUploaded"
  />
  <div style="position: relative">
    <SelectionBar
      v-if="selectedImageIds.length > 0"
      :selectedCount="selectedImageIds.length"
      :selectedCharacter="String(props.selectedCharacter)"
      :selectedSet="String(props.selectedSet)"
      :selectedGroupName="selectedGroupName"
      :visible="selectedImageIds.length > 0"
      @clear-selection="clearSelection"
      @remove-from-group="removeFromGroup"
      @delete-selected="deleteSelected"
      style="position: absolute; top: 0; left: 0; width: 100%; z-index: 100"
    />

    <div
      class="grid-scroll-wrapper"
      ref="scrollWrapper"
      @scroll="onGridScroll"
      style="position: relative"
    >
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
        @click="handleGridBackgroundClick"
      >
        <!-- Top spacer for virtual scroll alignment -->
        <div
          v-if="topSpacerHeight > 0"
          :style="{
            gridColumn: '1 / -1',
            height: `${topSpacerHeight}px`,
            border: '0px solid blue',
          }"
        ></div>
        <!-- Drag overlay -->
        <div v-if="dragOverlayVisible" class="drag-overlay">
          <div class="drag-overlay-message">{{ dragOverlayMessage }}</div>
        </div>
        <div
          v-for="img in gridImagesToRender"
          :key="img.id ? `img-${img.id}` : `placeholder-${img.idx}`"
          class="image-card"
          :draggable="isImageSelected(img.id)"
          @dragstart="onImageDragStart(img, img.idx, $event)"
          @click="handleImageCardClick(img, img.idx, $event)"
          @mouseenter="img._showRes = true"
          @mouseleave="img._showRes = false"
        >
          <v-card
            class="thumbnail-card"
            @click.stop="handleThumbnailClick(img, img.idx, $event)"
          >
            <div class="thumbnail-container">
              <!-- Movie icon overlay for videos -->
              <div
                v-if="isVideo(img)"
                class="movie-icon-overlay"
                :style="{
                  position: 'absolute',
                  bottom: '8px',
                  left: '10px',
                  background: 'rgba(0, 0, 0, 0.6)', // semi-transparent orange
                  color: '#ff9800', // orange for border/outline
                  padding: '2px 5px',
                  borderRadius: '4px',
                  fontSize: '1.0em',
                  zIndex: 30,
                  display: 'flex',
                  alignItems: 'center',
                  pointerEvents: 'none',
                }"
              >
                <svg
                  width="18"
                  height="18"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="#ff9800"
                  stroke-width="2"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  style="display: inline-block; vertical-align: middle"
                >
                  <rect x="2" y="4" width="20" height="16" rx="2" ry="2" />
                  <polygon points="10 9 16 12 10 15 10 9" fill="#ff9800" />
                </svg>
              </div>
              <!-- Resolution hover overlay -->
              <div
                v-if="img._showRes && img.width && img.height"
                class="resolution-hover-overlay"
                :style="{
                  position: 'absolute',
                  bottom: '8px',
                  right: '10px',
                  background: 'rgba(0,0,0,0.5)',
                  color: '#fff',
                  padding: '2px 8px',
                  borderRadius: '6px',
                  fontSize: '0.8em',
                  zIndex: 30,
                  pointerEvents: 'none',
                }"
              >
                {{ img.width }}×{{ img.height }}
              </div>
              <template v-if="img.thumbnail && isVideo(img)">
                <video
                  class="thumbnail-img"
                  :src="`${props.backendUrl}/pictures/${img.id}`"
                  :ref="(el) => setVideoRef(img.id, el)"
                  muted
                  loop
                  playsinline
                  @mouseenter="playVideo(img.id)"
                  @mouseleave="pauseVideo(img.id)"
                  style="
                    object-fit: cover;
                    width: 100%;
                    height: 100%;
                    border-radius: 8px;
                  "
                ></video>
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
                  {{ img.idx }}
                </div>
              </template>
              <template v-else-if="img.thumbnail">
                <img :src="img.thumbnail" class="thumbnail-img" />
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
                  {{ img.idx }}
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
                  <span> Image #{{ String(img.idx).padStart(5, "0") }} </span>
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
                        <!-- Info row absolutely positioned below thumbnail -->
              <div
                class="thumbnail-info-row"
              >
                <div
                  v-if="
                    props.selectedSort === 'search_likeness' &&
                    img.likeness_score !== undefined
                  "
                  class="likeness-score"
                >
                  Likeness: {{ img.likeness_score.toFixed(2) }}
                </div>
                <div
                  v-else-if="
                    props.selectedSort.includes('created_at') && img.created_at
                  "
                  class="thumbnail-info"
                >
                  {{ formatIsoDate(img.created_at) }}
                </div>
              </div>

        </div>
        <!-- Bottom spacer -->
        <div
          v-if="bottomSpacerHeight > 0"
          :style="{
            gridColumn: '1 / -1',
            height: `${bottomSpacerHeight}px`,
            border: '0px solid green',
          }"
        ></div>
      </div>
    </div>
  </div>
</template>

<script setup>
// Number of images before/after viewport to load thumbnails for
// Format date to ISO (YYYY-MM-DD HH:mm:ss)
function formatIsoDate(dateStr) {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) return dateStr;
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
import { computed, onMounted, ref, watch, nextTick, onUnmounted } from "vue";
import {
  isSupportedMediaFile,
  isSupportedImageFile,
  dataTransferHasSupportedMedia,
  isSupportedVideoFile,
  getOverlayFormat,
} from "../utils/media.js";
import ImageImporter from "./ImageImporter.vue";
import ImageOverlay from "./ImageOverlay.vue";
import SelectionBar from "./SelectionBar.vue";
import { useOverlayActions } from "../utils/useOverlayActions";

const emit = defineEmits(["open-overlay", "refresh-sidebar"]);

function clearSelection() {
  selectedImageIds.value = [];
}

// Video refs for hover play/pause in grid
const videoRefs = {};
function setVideoRef(id, el) {
  if (el) {
    videoRefs[id] = el;
  } else {
    delete videoRefs[id];
  }
}
function playVideo(id) {
  const v = videoRefs[id];
  if (v) v.play();
}
function pauseVideo(id) {
  const v = videoRefs[id];
  if (v) {
    v.pause();
    v.currentTime = 0;
  }
}
function isVideo(img) {
  if (!img) return false;
  let name = "";
  if (img.filename) {
    name = img.filename;
  } else if (img.id) {
    name = img.id;
  }
  return isSupportedVideoFile(name);
}

function removeFromGroup() {
  if (!selectedImageIds.value.length) return;
  const backendUrl = props.backendUrl;
  // Remove from character
  if (
    props.selectedCharacter &&
    props.selectedCharacter !== "__all__" &&
    props.selectedCharacter !== "__unassigned__"
  ) {
    Promise.all(
      selectedImageIds.value.map((id) =>
        fetch(`${backendUrl}/pictures/${id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ primary_character_id: null }),
        })
          .then((res) => {
            if (!res.ok)
              throw new Error(`Failed to unassign character for image ${id}`);
          })
          .catch((err) => {
            alert(
              `Error unassigning character for image ${id}: ${err.message}`
            );
          })
      )
    ).then(() => {
      // Remove affected images from grid immediately
      allGridImages.value = allGridImages.value.filter(
        (img) => !selectedImageIds.value.includes(img.id)
      );
      selectedImageIds.value = [];
      lastSelectedIndex = null;
      fetchTotalImageCount().then(() => {
        loadedRanges.value = [];
        updateVisibleThumbnails();
        emit("refresh-sidebar");
      });
      // Ensure thumbnails are refetched for new visible range
      updateVisibleThumbnails();
    });
    return;
  }
  // Remove from set
  if (
    props.selectedSet &&
    props.selectedSet !== "__all__" &&
    props.selectedSet !== "__unassigned__"
  ) {
    Promise.all(
      selectedImageIds.value.map((id) =>
        fetch(
          `${backendUrl}/picture_sets/${props.selectedSet}/pictures/${id}`,
          {
            method: "DELETE",
          }
        )
          .then((res) => {
            if (!res.ok)
              throw new Error(`Failed to remove image ${id} from set`);
          })
          .catch((err) => {
            alert(`Error removing image ${id} from set: ${err.message}`);
          })
      )
    ).then(async () => {
      // Remove affected images from grid immediately
      allGridImages.value = allGridImages.value.filter(
        (img) => !selectedImageIds.value.includes(img.id)
      );
      selectedImageIds.value = [];
      lastSelectedIndex = null;
      await fetchTotalImageCount();
      loadedRanges.value = [];
      updateVisibleThumbnails();
      // Ensure sidebar counts are refreshed after drag-out
      if (
        typeof window !== "undefined" &&
        window.app &&
        window.app.fetchPictureSets
      ) {
        await window.app.fetchPictureSets();
      } else {
        // Fallback: emit refresh-sidebar to parent
        emit("refresh-sidebar");
      }
    });
    return;
  }
}

function deleteSelected() {
  if (!selectedImageIds.value.length) return;
  if (
    !confirm(
      `Delete ${selectedImageIds.value.length} selected image(s)? This cannot be undone.`
    )
  )
    return;
  const backendUrl = props.backendUrl;
  Promise.all(
    selectedImageIds.value.map((id) =>
      fetch(`${backendUrl}/pictures/${id}`, { method: "DELETE" })
        .then((res) => {
          if (!res.ok) throw new Error(`Failed to delete image ${id}`);
        })
        .catch((err) => {
          alert(`Error deleting image ${id}: ${err.message}`);
        })
    )
  ).then(() => {
    // Remove deleted images from grid and clear selection
    allGridImages.value = allGridImages.value.filter(
      (img) => !selectedImageIds.value.includes(img.id)
    );
    selectedImageIds.value = [];
    lastSelectedIndex = null;
    updateVisibleThumbnails();
    emit("refresh-sidebar");
  });
}

const imageImporterRef = ref(null);
// Handle images-uploaded event from ImageImporter
async function handleImagesUploaded(newIds) {
  await fetchTotalImageCount();
  // Do NOT clear thumbnails; keep existing ones
  // Reset loadedRanges so new thumbnails can be fetched
  loadedRanges.value = [];
}
// Props
const props = defineProps({
  thumbnailSize: Number,
  sidebarVisible: Boolean,
  backendUrl: String,
  selectedCharacter: { type: [String, Number, null], default: null },
  selectedSet: { type: [Number, String, null], default: null },
  searchQuery: String,
  selectedSort: String,
  showStars: Boolean,
  gridVersion: { type: Number, default: 0 },
  mediaTypeFilter: { type: String, default: "all" },
});

watch(
  () => props.gridVersion,
  () => {
    // Full grid data refresh
    console.log("Grid version changed, refreshing all thumbnails");
    loadedRanges.value = [];
    allGridImages.value = [];
    selectedImageIds.value = [];
    lastSelectedIndex = null;
    fetchTotalImageCount().then(() => {
      updateVisibleThumbnails();
    });
  }
);

const VIEW_WINDOW = 100;

const divisibleViewWindow = computed(() => {
  const cols = columns.value;
  return Math.ceil(VIEW_WINDOW / cols) * cols;
});

const isLoadingThumbnails = ref(false);
const hasMoreImages = ref(true);

// Image overlay
const overlayOpen = ref(false);
const overlayImage = ref(null);

// Drag-and-drop overlay state
const dragOverlayVisible = ref(false);
const dragOverlayMessage = ref("");
const dragSource = ref(null);

const selectedGroupName = ref("");

async function updateSelectedGroupName() {
  let name = "";
  if (
    props.selectedCharacter &&
    props.selectedCharacter !== "__all__" &&
    props.selectedCharacter !== "__unassigned__"
  ) {
    try {
      const res = await fetch(
        `${props.backendUrl}/characters/${props.selectedCharacter}`
      );
      if (res.ok) {
        const char = await res.json();
        name = char.name || "";
      }
    } catch (e) {
      console.error("Character fetch failed:", e);
    }
  } else if (
    props.selectedSet &&
    props.selectedSet !== "__all__" &&
    props.selectedSet !== "__unassigned__"
  ) {
    try {
      const res = await fetch(`${props.backendUrl}/sets/${props.selectedSet}`);
      if (res.ok) {
        const set = await res.json();
        name = set.name || "";
      }
    } catch (e) {
      console.error("Set fetch failed:", e);
    }
  }
  selectedGroupName.value = name;
}

watch(
  [() => props.selectedCharacter, () => props.selectedSet],
  () => {
    updateSelectedGroupName();
  },
  { immediate: true }
);

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
  applyScore(img, newScore);
}

async function applyScore(img, newScore) {
  console.debug("Applying score:", newScore);
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
    const gridImg = allGridImages.value.find((i) => i.id === imageId);
    if (gridImg) {
      gridImg.score = newScore;
    }

    // Overlay image is not refreshed here; grid stays in sync
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
    emit("refresh-sidebar");
  } catch (e) {
    alert(e.message);
  }
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
    const groupLabel = selectedGroupName.value
      ? "for " + selectedGroupName.value
      : "";
    dragOverlayMessage.value = `Drop files here to import ${itemCount} file(s) ${groupLabel}`;
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
  if (scrollWrapper.value) {
    let newScrollTop = scrollWrapper.value.scrollTop;
    const maxScroll =
      scrollWrapper.value.scrollHeight - scrollWrapper.value.clientHeight;
    if (key === "Home") {
      newScrollTop = 0;
    } else if (key === "End") {
      newScrollTop = maxScroll;
    } else if (key === "PageUp") {
      newScrollTop = Math.max(
        0,
        newScrollTop - scrollWrapper.value.clientHeight
      );
    } else if (key === "PageDown") {
      newScrollTop = Math.min(
        maxScroll,
        newScrollTop + scrollWrapper.value.clientHeight
      );
    }
    // Only update if changed
    if (scrollWrapper.value.scrollTop !== newScrollTop) {
      scrollWrapper.value.scrollTop = newScrollTop;
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
  // If a set is selected, filter by set
  if (
    props.selectedSet &&
    props.selectedSet !== "__all__" &&
    props.selectedSet !== "__unassigned__"
  ) {
    params.append("set_id", props.selectedSet);
  } else if (props.selectedCharacter && props.selectedCharacter !== "__all__") {
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
  // Add format filter for backend media type filtering
  if (props.mediaTypeFilter === "images") {
    params.append("format", "PNG");
  } else if (props.mediaTypeFilter === "videos") {
    params.append("format", "MP4");
  }
  return params.toString();
}

// Fetch total image count for current filters
async function fetchTotalImageCount() {
  imagesLoading.value = true;
  imagesError.value = null;
  try {
    let images = [];
    // If a set is selected, use /picture_sets/{id}
    if (
      props.selectedSet &&
      props.selectedSet !== "__all__" &&
      props.selectedSet !== "__unassigned__"
    ) {
      const url = `${props.backendUrl}/picture_sets/${props.selectedSet}`;
      const res = await fetch(url);
      if (!res.ok) throw new Error("Failed to fetch images for set");
      const data = await res.json();
      images = data.pictures || [];
    } else if (props.searchQuery && props.searchQuery.trim()) {
      // Use /search endpoint for text search
      const url = `${props.backendUrl}/search?query=${encodeURIComponent(
        props.searchQuery.trim()
      )}&top_n=10000`;
      const res = await fetch(url);
      if (!res.ok) throw new Error("Failed to fetch search results");
      images = await res.json();
    } else {
      const params = buildPictureIdsQueryParams();
      const url = `${props.backendUrl}/pictures?info=true&offset=0&limit=10000&${params}`;
      const res = await fetch(url);
      if (!res.ok) throw new Error("Failed to fetch image info for all images");
      images = await res.json();
    }
    totalImageCount.value = images.length;
    console.debug(
      "[IMAGE COUNT] Total images for current filters:",
      totalImageCount.value
    );
    allGridImages.value = images.map((img, i) => ({
      ...img,
      idx: i,
      thumbnail: null,
    }));
  } catch (e) {
    imagesError.value = e.message;
    totalImageCount.value = 0;
    allGridImages.value = [];
  } finally {
    imagesLoading.value = false;
  }
}

onMounted(() => {
  fetchTotalImageCount().then(() => {
    updateVisibleThumbnails();
  });
});

watch(
  [
    () => props.selectedCharacter,
    () => props.selectedSet,
    () => props.searchQuery,
    () => props.selectedSort,
    () => props.mediaTypeFilter,
  ],
  () => {
    // Reset loaded ranges and thumbnails when filters change
    loadedRanges.value = [];
    allGridImages.value = [];
    selectedImageIds.value = [];
    lastSelectedIndex = null;
    updateSelectedGroupName();
    fetchTotalImageCount().then(() => {
      updateVisibleThumbnails();
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

const rowHeight = ref(props.thumbnailSize + 24);

const renderStart = computed(() => {
  const cols = columns.value;
  let start = Math.max(0, visibleStart.value - divisibleViewWindow.value);
  return start;
});

const renderEnd = computed(() => {
  const cols = columns.value;
  let end = Math.min(
    allGridImages.value.length,
    visibleEnd.value + divisibleViewWindow.value
  );
  return end;
});

const topSpacerHeight = computed(() => {
  const cols = columns.value;
  const rowsAbove = Math.floor(renderStart.value / cols);
  const height = rowsAbove > 0 ? rowsAbove * rowHeight.value : 1;
  console.log("topSpacerHeight:", height);
  return height;
});

const bottomSpacerHeight = computed(() => {
  const cols = columns.value;
  const lastRenderedRow = Math.floor((renderEnd.value - 1) / cols) + 1;
  const totalRows = Math.ceil(totalImageCount.value / cols);
  const rowsBelow = totalRows - lastRenderedRow;
  const height = rowsBelow > 0 ? rowsBelow * rowHeight.value : 0;
  return height;
});

// Compute grid images (id, idx, thumbnail)
const allGridImages = ref([]);

const gridImagesToRender = computed(() => {
  // Only render a window of placeholders/images for performance
  if (allGridImages.value.length < totalImageCount.value) {
    for (let i = allGridImages.value.length; i < totalImageCount.value; i++) {
      allGridImages.value[i] = { id: null, thumbnail: null, idx: i };
    }
  }
  console.log("Filtering images for mediaTypeFilter:", props.mediaTypeFilter);
  // Accept both 'all' and 'both' as showing all media
  let filtered = allGridImages.value;
  if (props.mediaTypeFilter === "images") {
    filtered = filtered.filter((img) => {
      if (!img) return false;
      const name = img.filename || img.name || img.id || "";
      const format = (img.format || "").toLowerCase();
      return isSupportedImageFile(name) || isSupportedImageFile(format);
    });
  } else if (props.mediaTypeFilter === "videos") {
    filtered = filtered.filter((img) => {
      if (!img) return false;
      const name = img.filename || img.name || img.id || "";
      const format = (img.format || "").toLowerCase();
      return isSupportedVideoFile(name) || isSupportedVideoFile(format);
    });
  } // else 'all' or 'both' shows everything
  return filtered.slice(renderStart.value, renderEnd.value);
});

// Batch fetch metadata (including thumbnail) for visible range
async function fetchThumbnailsBatch(start, end) {
  start = renderStart.value;
  end = renderEnd.value;

  console.debug(
    `[BATCH REQUEST] start=${start}, end=${end}, loadedRanges=${JSON.stringify(
      loadedRanges.value
    )}`
  );
  // Check if this batch range is already loaded
  for (const range of loadedRanges.value) {
    if (start >= range[0] && end <= range[1]) {
      return; // Already loaded
    }
  }
  // Fetch batch metadata for visible range
  try {
    let images = [];
    let ids = [];
    // If a set is selected, use /picture_sets/{id}
    if (
      props.selectedSet &&
      props.selectedSet !== "__all__" &&
      props.selectedSet !== "__unassigned__"
    ) {
      const url = `${props.backendUrl}/picture_sets/${props.selectedSet}`;
      const res = await fetch(url);
      if (res.ok) {
        const data = await res.json();
        images = data.pictures ? data.pictures.slice(start, end) : [];
        ids = images.map((img) => img.id);
      }
    } else {
      // Only fetch if we don't already have metadata for this range
      images = allGridImages.value.slice(start, end);
      ids = images.map((img) => img.id);
      // If not enough images, fetch missing ones
      if (images.length < end - start) {
        const params = buildPictureIdsQueryParams();
        const url = `${props.backendUrl}/pictures?offset=${start}&limit=${
          end - start
        }&${params}`;
        const res = await fetch(url);
        if (res.ok) {
          const fetched = await res.json();
          // Fill in missing slots
          for (let i = 0; i < fetched.length; i++) {
            allGridImages.value[start + i] = {
              ...fetched[i],
              idx: start + i,
              thumbnail: null,
            };
          }
          images = allGridImages.value.slice(start, end);
          ids = images.map((img) => img.id);
        }
      }
    }
    console.debug(
      `[BATCH RESPONSE] Received ${images.length} images:`,
      images.map((img) => img.id)
    );
    // Prepare grid image objects
    const gridImages = images.map((img, idx) => ({
      ...img,
      score: img.score ?? 0,
      idx: start + idx, // Ensure idx is global index
      thumbnail: null,
    }));
    // Now fetch thumbnails for these IDs
    if (ids.length) {
      const thumbRes = await fetch(`${props.backendUrl}/thumbnails`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids }),
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
      img.idx = start + i; // Redundant but explicit for safety
      allGridImages.value[start + i] = img;
    }
    loadedRanges.value.push([start, end]);
  } catch (err) {
    console.error("[BATCH ERROR]", err);
  }
}

function updateVisibleThumbnails() {
  let start = Math.max(0, visibleStart.value - divisibleViewWindow.value);
  let end = Math.min(
    totalImageCount.value,
    visibleEnd.value + divisibleViewWindow.value
  );
  console.log(
    "Fetch range: ",
    start,
    "to",
    end,
    "Visible:",
    visibleStart.value,
    visibleEnd.value,
    "Total:",
    totalImageCount.value
  );

  // Debounce fetches to avoid excessive requests
  if (thumbFetchTimeout) clearTimeout(thumbFetchTimeout);

  thumbFetchTimeout = setTimeout(async () => {
    await fetchThumbnailsBatch(start, end);
  }, 80);
}

function onGridScroll(e) {
  // Debounce scroll handler to prevent runaway feedback
  if (!window._scrollDebounceTimeout) window._scrollDebounceTimeout = null;
  if (window._scrollDebounceTimeout)
    clearTimeout(window._scrollDebounceTimeout);
  window._scrollDebounceTimeout = setTimeout(() => {
    const el = scrollWrapper.value;
    if (!el) return;
    let cardHeight = rowHeight.value;
    const scrollTop = el.scrollTop;
    const cols = columns.value;
    // First visible row (may be partially visible)
    const firstVisibleRow = scrollTop / cardHeight;
    // Last visible row (may be partially visible)
    const lastVisibleRow = (scrollTop + el.clientHeight - 1) / cardHeight;

    const newVisibleStart = Math.floor(firstVisibleRow) * cols;
    const newVisibleEnd = Math.ceil(lastVisibleRow) * cols;

    // Only update if changed
    if (
      visibleStart.value !== newVisibleStart ||
      visibleEnd.value !== newVisibleEnd
    ) {
      visibleStart.value = newVisibleStart;
      visibleEnd.value = newVisibleEnd;
      console.debug(
        "[SCROLL] visibleStart:",
        visibleStart.value,
        "visibleEnd:",
        visibleEnd.value,
        "Client Height: ",
        el.clientHeight
      );
      // Only trigger buffer expansion/fetch if user is near buffer end
      // Always fetch thumbnails for the current visible window
      updateVisibleThumbnails();
    }
  }, 50);
}

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
  let newSelection = [];
  if (isCtrl) {
    // Toggle selection
    newSelection = [...selectedImageIds.value];
    if (newSelection.includes(img.id)) {
      console.debug("Deselecting image ID:", img.id);
      newSelection = newSelection.filter((id) => id !== img.id);
    } else {
      console.debug("Selecting image ID:", img.id);
      newSelection.push(img.id);
    }
    lastSelectedIndex = idx;
  } else if (isShift && lastSelectedIndex !== null) {
    // Range select: select only the contiguous range between anchor and clicked item
    const start = Math.min(lastSelectedIndex, idx);
    const end = Math.max(lastSelectedIndex, idx);
    newSelection = allGridImages.value
      .slice(start, end + 1)
      .map((i) => i.id)
      .filter(Boolean);
    // Do NOT merge with previous selection; replace it
  } else {
    // Single select
    newSelection = [img.id];
    lastSelectedIndex = idx;
  }
  selectedImageIds.value = newSelection;
  console.log("New selection:", newSelection);
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
  }
}

// --- Text & Display Utilities ---
function formatLikenessScore(score) {
  if (typeof score !== "number") return "";
  return `Likeness: ${(score * 100).toFixed(2)}%`;
}

const gridContainer = ref(null);
const scrollWrapper = ref(null);

function updateColumns() {
  nextTick(() => {
    function measureRowHeight(retries = 0) {
      const firstCard = gridContainer.value?.querySelector(".image-card");
      if (firstCard) {
        const rect = firstCard.getBoundingClientRect();
        rowHeight.value = rect.height;
      } else if (retries < 5) {
        setTimeout(() => measureRowHeight(retries + 1), 60);
      }
    }
    measureRowHeight();

    const el = scrollWrapper.value?.$el || scrollWrapper.value;
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
  window.addEventListener("keydown", handleKeyDown);
});

// Clear selection on ESC key
function handleKeyDown(event) {
  if (event.key === "Escape") {
    selectedImageIds.value = [];
    lastSelectedIndex = null;
  } else if (event.key === "Delete" || event.key === "Backspace") {
    if (selectedImageIds.value.length > 0) {
      deleteSelected();
    }
  } else if ((event.ctrlKey || event.metaKey) && event.key === "a") {
    event.preventDefault();
    // Instrumentation: log allGridImages and selection
    console.log("[CTRL+A] allGridImages length:", allGridImages.value.length);
    const ids = allGridImages.value.map((img) => img && img.id);
    const validIds = ids.filter((id) => !!id);
    const placeholderCount = ids.length - validIds.length;
    console.log("[CTRL+A] valid image IDs count:", validIds.length);
    console.log("[CTRL+A] placeholder count:", placeholderCount);
    console.log("[CTRL+A] allGridImages IDs:", ids);
    // Select all images with valid IDs from allGridImages (not just visible)
    const allIds = allGridImages.value
      .filter((img) => img && img.id)
      .map((img) => img.id);
    selectedImageIds.value = Array.from(allIds);
    console.log("[CTRL+A] selectedImageIds:", selectedImageIds.value);
    lastSelectedIndex = null;
  }
}

watch(
  () => props.thumbnailSize,
  () => {
    updateColumns();
    // Recalculate visibleStart and visibleEnd after columns/rowHeight update
    nextTick(() => {
      const el = scrollWrapper.value;
      if (!el) return;
      let cardHeight = rowHeight.value;
      const scrollTop = el.scrollTop;
      const cols = columns.value;
      // First visible row (may be partially visible)
      const firstVisibleRow = scrollTop / cardHeight;
      // Last visible row (may be partially visible)
      const lastVisibleRow = (scrollTop + el.clientHeight - 1) / cardHeight;
      const newVisibleStart = Math.floor(firstVisibleRow) * cols;
      const newVisibleEnd = Math.ceil(lastVisibleRow) * cols;
      visibleStart.value = newVisibleStart;
      visibleEnd.value = newVisibleEnd;
      updateVisibleThumbnails();
    });
  }
);

onUnmounted(() => {
  window.removeEventListener("resize", updateColumns);
  window.removeEventListener("keydown", handleKeyDown);
});

// Expose the grid DOM node to parent
defineExpose({
  gridEl: scrollWrapper,
  onGlobalKeyPress,
  updateVisibleThumbnails,
  exportCurrentViewToZip,
});

// --- Export to Zip ---
async function exportCurrentViewToZip() {
  // Build query params for current view
  let url = `${props.backendUrl}/export/zip`;
  const params = buildPictureIdsQueryParams();
  if (params) {
    url += `?${params}`;
  }
  // If viewing a set, send set_id as param
  if (
    props.selectedSet &&
    props.selectedSet !== "__all__" &&
    props.selectedSet !== "__unassigned__"
  ) {
    url += params
      ? `&set_id=${props.selectedSet}`
      : `?set_id=${props.selectedSet}`;
  }
  try {
    const res = await fetch(url, { method: "GET" });
    if (!res.ok) throw new Error("Failed to export zip");
    const blob = await res.blob();
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "pixlvault_export.zip";
    document.body.appendChild(link);
    link.click();
    setTimeout(() => {
      URL.revokeObjectURL(link.href);
      document.body.removeChild(link);
    }, 2000);
  } catch (e) {
    alert("Export failed: " + (e.message || e));
  }
}
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
.grid-scroll-wrapper {
  height: 100vh; /* or calc(100vh - headerHeight) if you have a header */
  overflow-y: auto;
  width: 100%;
  padding-right: 0px;
  scrollbar-color: orange #ddd;
  border: 0px solid red;
}
.image-grid {
  height: 100%;
  display: grid;
  gap: 0;
  width: 100%;
  box-sizing: border-box;
  flex: 1 1 0%;
  padding: 0px 2px 2px 2px !important;
  align-content: start;
  justify-content: start;
}
.grid-scroll-wrapper::-webkit-scrollbar {
  width: 8px;
}
.grid-scroll-wrapper::-webkit-scrollbar-thumb {
  background: orange;
  border-radius: 8px;
}
.grid-scroll-wrapper::-webkit-scrollbar-track {
  background: #ddd;
}
.image-card {
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  width: 100%;
  margin-bottom: 2.2em;
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
.thumbnail-info-row {
  margin-top: 2px;
  text-align: center;
  min-height: 1.2em;
  background: none;
  
}
.thumbnail-info {
  font-size: 1.0em;
  color: #222;
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
  max-width: none;
  min-width: none;
  position: relative;
}
/* Overlay for image index on thumbnail */
.thumbnail-index-overlay {
  pointer-events: none;
}
</style>
