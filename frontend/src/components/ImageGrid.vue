<template>
  <ImageOverlay
    :open="overlayOpen"
    :initialImage="overlayImage"
    :allImages="allGridImages"
    :backendUrl="props.backendUrl"
    @close="closeOverlay"
    @apply-score="applyScore"
    @add-tag="addTagToImage"
    @remove-tag="removeTagFromImage"
  />
  <ImageImporter
    ref="imageImporterRef"
    :backendUrl="props.backendUrl"
    :selectedCharacterId="props.selectedCharacter"
    :allPicturesId="props.allPicturesId"
    :unassignedPicturesId="props.unassignedPicturesId"
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
      v-if="exportProgress.visible"
      class="export-progress"
      :class="{ 'export-progress-error': exportProgress.status === 'failed' }"
    >
      <div class="export-progress-title">
        {{ exportProgress.message }}
      </div>
      <div class="export-progress-bar">
        <div
          class="export-progress-fill"
          :style="{ width: `${exportProgressPercent}%` }"
        ></div>
      </div>
      <div class="export-progress-meta">
        {{ exportProgress.processed }} / {{ exportProgress.total }}
      </div>
    </div>

    <div
      class="grid-scroll-wrapper"
      ref="scrollWrapper"
      @scroll="onGridScroll"
      style="position: relative"
    >
      <div v-if="showEmptyState" class="empty-state">
        <div class="empty-state-card">
          <div class="empty-state-illustration" aria-hidden="true">
            <svg
              width="160"
              height="120"
              viewBox="0 0 160 120"
              fill="none"
              xmlns="http://www.w3.org/2000/svg"
            >
              <rect
                x="18"
                y="16"
                width="86"
                height="64"
                rx="10"
                stroke="currentColor"
                stroke-width="3"
                opacity="0.4"
              />
              <rect
                x="34"
                y="28"
                width="86"
                height="64"
                rx="10"
                stroke="currentColor"
                stroke-width="3"
                opacity="0.6"
              />
              <rect
                x="50"
                y="40"
                width="86"
                height="64"
                rx="10"
                stroke="currentColor"
                stroke-width="3"
              />
              <circle
                cx="96"
                cy="70"
                r="8"
                stroke="currentColor"
                stroke-width="3"
              />
              <path
                d="M60 86 L76 70 L88 82 L104 66 L122 86"
                stroke="currentColor"
                stroke-width="3"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
          </div>
          <div class="empty-state-title">
            {{ emptyStateTitle }}
          </div>
          <div class="empty-state-subtitle">
            {{ emptyStateSubtitle }}
          </div>
          <v-btn
            v-if="canShowAllPicturesButton"
            class="empty-state-action"
            color="primary"
            variant="elevated"
            @click.stop="handleEmptyStateReset"
          >
            Show All Pictures
          </v-btn>
        </div>
      </div>
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
          v-for="(img, idx) in gridImagesToRender"
          :key="img.id ? `img-${img.id}` : `placeholder-${img.idx}`"
          :style="getStackCardStyle(img)"
          class="image-card"
          @click="handleImageCardClick(img, img.idx, $event)"
          @mouseenter="handleImageMouseEnter(img)"
          @mouseleave="handleImageMouseLeave(img)"
        >
          <v-card
            class="thumbnail-card"
            @click.stop="handleThumbnailClick(img, img.idx, $event)"
          >
            <div
              class="thumbnail-container"
              :ref="(el) => setThumbnailContainerRef(img.id, el)"
              draggable="true"
              @dragstart.capture="handleContainerDragStart(img, $event)"
              @dragend.capture="handleContainerDragEnd(img, $event)"
            >
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
                  :src="getImageDownloadUrl(img)"
                  :ref="(el) => setVideoRef(img.id, el)"
                  draggable="false"
                  @pointerdown="prepareThumbnailNativeDrag(img, $event)"
                  @pointerup="handleThumbnailPointerRelease($event)"
                  @pointercancel="handleThumbnailPointerRelease($event)"
                  @load="
                    () => {
                      setThumbnailRef(img.id, el);
                      onThumbnailLoad(img.id);
                    }
                  "
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
                <img
                  v-if="img.thumbnail"
                  class="thumbnail-drag-preview"
                  :src="img.thumbnail"
                  :ref="(el) => setDragPreviewRef(img.id, el)"
                  alt=""
                />
                <div
                  class="thumbnail-index-overlay"
                  @pointerdown="prepareThumbnailNativeDrag(img, $event)"
                  @pointerup="handleThumbnailPointerRelease($event)"
                  @pointercancel="handleThumbnailPointerRelease($event)"
                  :style="{
                    position: 'absolute',
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
                <div
                  v-if="img.id"
                  class="thumbnail-id-overlay"
                  :style="{
                    position: 'absolute',
                    left: '10px',
                    bottom: '6px',
                    color: '#fff',
                    background: 'rgba(0, 0, 0, 0.6)',
                    fontSize: '0.72em',
                    padding: '2px 6px',
                    borderRadius: '6px',
                    zIndex: 20,
                    maxWidth: '90%',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }"
                >
                  {{ img.id }}
                </div>
              </template>
              <template v-else-if="img.thumbnail">
                <img
                  :src="img.thumbnail"
                  class="thumbnail-img"
                  :ref="(el) => setThumbnailRef(img.id, el)"
                  draggable="true"
                  @pointerdown="prepareThumbnailNativeDrag(img, $event)"
                  @pointerup="handleThumbnailPointerRelease($event)"
                  @pointercancel="handleThumbnailPointerRelease($event)"
                  @dragstart="handleThumbnailNativeDragStart(img, $event)"
                  @dragend="handleThumbnailNativeDragEnd($event)"
                  @error="handleImageError"
                  @load="
                    () => {
                      setThumbnailRef(img.id, el);
                      onThumbnailLoad(img.id);
                    }
                  "
                />
                <!-- Face bounding box overlays: must be rendered after the image for correct stacking -->
                <template
                  v-if="thumbnailRefs[img.id] && thumbnailLoadedMap[img.id]"
                >
                  <div
                    v-for="overlay in getFaceBboxOverlays(img).value"
                    :key="overlay.idx + '-' + thumbnailLoadedMap[img.id]"
                    class="face-bbox-overlay"
                    :style="overlay.style"
                    draggable="true"
                    @dragstart="
                      (e) => {
                        e.stopPropagation();
                        onFaceBboxDragStart(e, img, overlay.idx);
                      }
                    "
                  >
                    <span
                      style="
                        position: absolute;
                        left: 0;
                        top: 0;
                        background: #222c;
                        color: #fff;
                        font-size: 0.8em;
                        padding: 1px 4px;
                        border-bottom-right-radius: 6px;
                      "
                    >
                      {{
                        img.faces &&
                        img.faces[overlay.idx] &&
                        img.faces[overlay.idx].character_name
                          ? img.faces[overlay.idx].character_name
                          : `Face ${overlay.idx + 1}`
                      }}
                    </span>
                  </div>
                </template>
                <div
                  v-if="img.format && img.format !== 'unknown'"
                  class="thumbnail-id-overlay"
                  :style="{
                    position: 'absolute',
                    left: '10px',
                    bottom: '6px',
                    color: '#fff',
                    background: 'rgba(0, 0, 0, 0.4)',
                    fontSize: '0.72em',
                    padding: '2px 4px',
                    borderRadius: '4px',
                    zIndex: 20,
                    maxWidth: '90%',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }"
                >
                  {{ img.format.toUpperCase() }}
                </div>
              </template>
              <template v-else>
                <div class="thumbnail-placeholder">
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
          <div class="thumbnail-info-row">
            <div
              v-if="
                typeof props.selectedSort === 'string' &&
                props.selectedSort.includes('CHARACTER_LIKENESS') &&
                img.character_likeness !== undefined
              "
              class="likeness-score"
            >
              Likeness: {{ img.character_likeness.toFixed(2) }}
            </div>
            <div
              v-if="
                typeof props.searchQuery && img.likeness_score !== undefined
              "
              class="likeness-score"
            >
              Search likeness: {{ img.likeness_score.toFixed(2) }}
            </div>
            <div
              v-else-if="
                typeof props.selectedSort === 'string' &&
                props.selectedSort.includes('DATE') &&
                img.created_at
              "
              class="thumbnail-info"
            >
              {{ formatIsoDate(img.created_at) }}
            </div>
            <div
              v-else-if="
                props.selectedSort === STACKS_SORT_KEY &&
                (typeof img.stackIndex === 'number' ||
                  typeof img.stack_index === 'number')
              "
              class="thumbnail-info"
            >
              Stack
              {{
                typeof img.stackIndex === "number"
                  ? img.stackIndex + 1
                  : img.stack_index + 1
              }}
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

    <!-- Search Result Bar -->
    <div
      v-if="props.searchQuery && props.searchQuery.length > 0"
      class="search-result-bar"
    >
      <span> Search result found {{ allGridImages.length }} items </span>
      <v-btn color="primary" @click="clearSearchQuery">Clear</v-btn>
    </div>
  </div>
</template>

<script setup>
import {
  computed,
  onMounted,
  reactive,
  ref,
  watch,
  nextTick,
  onUnmounted,
} from "vue";
import {
  isSupportedMediaFile,
  isSupportedImageFile,
  dataTransferHasSupportedMedia,
  isSupportedVideoFile,
  getOverlayFormat,
  PIL_IMAGE_EXTENSIONS,
  VIDEO_EXTENSIONS,
} from "../utils/media.js";
import ImageImporter from "./ImageImporter.vue";
import ImageOverlay from "./ImageOverlay.vue";
import SelectionBar from "./SelectionBar.vue";
import { useSearchOverlay } from "../utils/useSearchOverlay";
import { apiClient } from "../utils/apiClient";
import { debounce, update } from "lodash-es";

const emit = defineEmits([
  "open-overlay",
  "refresh-sidebar",
  "clear-search",
  "reset-to-all",
]);

// Props
const props = defineProps({
  thumbnailSize: Number,
  sidebarVisible: Boolean,
  backendUrl: String,
  selectedCharacter: { type: [String, Number, null], default: null },
  selectedSet: { type: [Number, String, null], default: null },
  searchQuery: String,
  selectedSort: String,
  selectedDescending: Boolean,
  similarityCharacter: { type: [String, Number, null], default: null },
  stackThreshold: { type: [String, Number, null], default: null },
  showStars: Boolean,
  showFaceBboxes: Boolean,
  allPicturesId: String,
  unassignedPicturesId: String,
  gridVersion: { type: Number, default: 0 },
  mediaTypeFilter: { type: String, default: "all" },
});
const STACKS_SORT_KEY = "PICTURE_STACKS";
const STACK_COLOR_STEP = 47;
// Store refs for each thumbnail image
const thumbnailRefs = reactive({});
const thumbnailContainerRefs = reactive({});
const dragPreviewRefs = reactive({});
const thumbnailLoadedMap = reactive({});
const PREFETCHED_FULL_IMAGE_LIMIT = 12;
const fullImagePrefetchControllers = new Map();
const prefetchedFullImageIds = new Set();
const prefetchedFullImageOrder = [];
const multiZipState = reactive({
  key: null,
  status: "idle",
  blob: null,
  filename: null,
  error: null,
});
let multiZipAbortController = null;

const exportProgress = reactive({
  visible: false,
  status: "idle",
  processed: 0,
  total: 0,
  message: "",
});

const exportProgressPercent = computed(() => {
  if (!exportProgress.total) return 0;
  const percent = (exportProgress.processed / exportProgress.total) * 100;
  return Math.min(100, Math.max(0, Math.round(percent)));
});

// Key to force face bbox overlay recompute
const faceOverlayRedrawKey = ref(0);

function triggerFaceOverlayRedraw() {
  faceOverlayRedrawKey.value++;
}

onMounted(() => {
  console.log(
    "[ImageGrid.vue] Mounted with selectedDescending:",
    props.selectedDescending,
  );
  console.log(
    "[ImageGrid.vue] Initial gridImagesToRender:",
    gridImagesToRender.value,
  );
  console.log("[ImageGrid.vue] Initial allGridImages:", allGridImages.value);
  window.addEventListener("resize", triggerFaceOverlayRedraw);
  fetchAllPicturesCount();
});

onUnmounted(() => {
  window.removeEventListener("resize", triggerFaceOverlayRedraw);
  fullImagePrefetchControllers.clear();
  prefetchedFullImageIds.clear();
  prefetchedFullImageOrder.length = 0;
  resetMultiSelectionZip();
  if (emptyStateDelayTimer) {
    clearTimeout(emptyStateDelayTimer);
    emptyStateDelayTimer = null;
  }
});

function onThumbnailLoad(id) {
  thumbnailLoadedMap[id] = (thumbnailLoadedMap[id] || 0) + 1;
}

function setThumbnailRef(id, el) {
  if (el) {
    thumbnailRefs[id] = el;
  } else {
    delete thumbnailRefs[id];
    delete thumbnailLoadedMap[id];
  }
}

function setDragPreviewRef(id, el) {
  if (el) {
    dragPreviewRefs[id] = el;
  } else {
    delete dragPreviewRefs[id];
  }
}

function setThumbnailContainerRef(id, el) {
  if (el) {
    thumbnailContainerRefs[id] = el;
  } else {
    delete thumbnailContainerRefs[id];
  }
}

// --- Multi-face selection state ---
const selectedFaceIds = ref([]); // Array of { imageId, faceIdx, faceId }

function isFaceSelected(imageId, faceIdx) {
  return selectedFaceIds.value.some(
    (f) => f.imageId === imageId && f.faceIdx === faceIdx,
  );
}

function toggleFaceSelection(imageId, faceIdx, faceId) {
  const idx = selectedFaceIds.value.findIndex(
    (f) => f.imageId === imageId && f.faceIdx === faceIdx,
  );
  if (idx !== -1) {
    selectedFaceIds.value.splice(idx, 1);
  } else {
    selectedFaceIds.value.push({ imageId, faceIdx, faceId });
  }
}

function clearFaceSelection() {
  selectedFaceIds.value = [];
}

function onFaceBboxDragStart(event, img, faceIdx) {
  // If this face is selected, drag all selected faces; else, drag just this one
  console.log(`Dragging face bbox: imageId=${img.id}, faceIdx=${faceIdx}`);
  let facesToDrag = [];
  if (isFaceSelected(img.id, faceIdx) && selectedFaceIds.value.length > 0) {
    facesToDrag = selectedFaceIds.value.map((f) => ({
      imageId: f.imageId,
      faceIdx: f.faceIdx,
      faceId: f.faceId,
    }));
  } else {
    const face = img.faces[faceIdx];
    facesToDrag = [{ imageId: img.id, faceIdx, faceId: face.id }];
  }

  // Ensure that additional data types are preserved in the dataTransfer object
  const existingData = {};
  for (const type of event.dataTransfer.types) {
    existingData[type] = event.dataTransfer.getData(type);
  }

  // Set the application/json data
  const dragDataStr = JSON.stringify({
    type: "face-bbox",
    faceIds: facesToDrag.map((f) => f.faceId),
    imageIds: Array.from(new Set(facesToDrag.map((f) => f.imageId))),
    faces: facesToDrag,
  });
  console.log("[DRAG] onFaceBboxDragStart dragData:", dragDataStr);
  event.dataTransfer.setData("application/json", dragDataStr);

  // Restore other data types
  for (const [type, data] of Object.entries(existingData)) {
    if (type !== "application/json") {
      event.dataTransfer.setData(type, data);
    }
  }

  event.dataTransfer.effectAllowed = "move";
}

// Helper to calculate face bbox overlay style using object-fit: cover logic
function getFaceBboxStyle(bbox, idx, img, el) {
  if (!el) return { display: "none" };
  const container = el.parentElement;
  if (!container) return { display: "none" };
  const containerWidth = container.clientWidth;
  const containerHeight = container.clientHeight;
  const naturalWidth = img.width || 1;
  const naturalHeight = img.height || 1;
  // Calculate scale and offset for object-fit: cover
  const scale = Math.max(
    containerWidth / naturalWidth,
    containerHeight / naturalHeight,
  );
  const displayWidth = naturalWidth * scale;
  const displayHeight = naturalHeight * scale;
  const offsetX = (containerWidth - displayWidth) / 2;
  const offsetY = (containerHeight - displayHeight) / 2;
  // Transform bbox
  const left = offsetX + bbox[0] * scale;
  const top = offsetY + bbox[1] * scale;
  const width = (bbox[2] - bbox[0]) * scale;
  const height = (bbox[3] - bbox[1]) * scale;
  return {
    position: "absolute",
    border: `1.5px solid ${faceBoxColor(idx)}`,
    background: `${faceBoxColor(idx)}22`,
    left: `${left}px`,
    top: `${top}px`,
    width: `${width}px`,
    height: `${height}px`,
    pointerEvents: "auto",
    zIndex: 100,
    display: "block",
  };
}

function getFaceBboxOverlays(img) {
  return computed(() => {
    void thumbnailLoadedMap[img.id];
    void thumbnailRefs[img.id];
    void faceOverlayRedrawKey.value; // depend on redraw key
    if (
      !props.showFaceBboxes ||
      !img.faces ||
      !img.faces.length ||
      !img.width ||
      !img.height
    ) {
      return [];
    }
    const el = thumbnailRefs[img.id];
    if (!el) return [];
    const firstFrameFaces = img.faces.filter((f) => f.frame_index === 0);
    return firstFrameFaces.map((face, fidx) => ({
      style: getFaceBboxStyle(face.bbox, fidx, img, el),
      idx: fidx,
    }));
  });
}

// Helper for face bbox color palette (copied from ImageOverlay.vue)
function faceBoxColor(idx) {
  const palette = [
    "#ff5252", // red
    "#40c4ff", // blue
    "#ffd740", // yellow
    "#69f0ae", // green
    "#d500f9", // purple
    "#ffab40", // orange
    "#00e676", // teal
    "#ff4081", // pink
    "#8d6e63", // brown
    "#7c4dff", // indigo
  ];
  return palette[idx % palette.length];
}

// Track which image is currently hovered
const hoveredImageIdx = ref(null);

function handleImageMouseEnter(img) {
  img._showRes = true;
  prefetchFullImage(img);
  hoveredImageIdx.value = img.idx;
}
function handleImageMouseLeave(img) {
  img._showRes = false;
  if (hoveredImageIdx.value === img.idx) hoveredImageIdx.value = null;
}

// Number of images before/after viewport to load thumbnails for
// Format date to ISO (YYYY-MM-DD HH:mm:ss)
function formatIsoDate(dateStr) {
  if (!dateStr) return "";
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) return dateStr;
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(
    d.getHours(),
  )}:${pad(d.getMinutes())}`;
}

function getImageFormatExtension(img) {
  if (!img || !img.format) return "";
  return String(img.format).trim().toLowerCase();
}

function appendExtIfMissing(name, ext) {
  if (!name) return null;
  if (!ext) return name;
  const normalized = ext.toLowerCase();
  const lowerName = name.toLowerCase();
  if (lowerName.endsWith(`.${normalized}`)) return name;
  if (/\.[a-z0-9]+$/i.test(lowerName)) return name;
  return `${name}.${normalized}`;
}

function getImageFilename(img) {
  if (!img) return "image";
  const ext = getImageFormatExtension(img);
  const original = appendExtIfMissing(img.original_filename, ext);
  const fromFilename = appendExtIfMissing(img.filename, ext);
  const fallbackBase = img.id ? String(img.id) : "image";
  const fallback = ext ? `${fallbackBase}.${ext}` : `${fallbackBase}.jpg`;
  return original || fromFilename || fallback;
}

function getImageDownloadUrl(img) {
  if (!img || !img.id) return "";
  const ext = getImageFormatExtension(img);
  const suffix = ext ? `.${ext}` : "";
  const cacheBuster = img.pixel_sha ? `?v=${img.pixel_sha}` : "";
  return `${props.backendUrl}/pictures/${img.id}${suffix}${cacheBuster}`;
}

function prefetchFullImage(img) {
  if (!img || !img.id) return;
  if (isVideo(img)) return;
  const id = img.id;
  if (prefetchedFullImageIds.has(id) || fullImagePrefetchControllers.has(id)) {
    return;
  }
  const url = getImageDownloadUrl(img);
  if (!url) return;
  const preloader = new Image();
  fullImagePrefetchControllers.set(id, preloader);
  preloader.onload = () => {
    fullImagePrefetchControllers.delete(id);
    prefetchedFullImageIds.add(id);
    prefetchedFullImageOrder.push(id);
    while (prefetchedFullImageOrder.length > PREFETCHED_FULL_IMAGE_LIMIT) {
      const oldest = prefetchedFullImageOrder.shift();
      if (oldest !== undefined) {
        prefetchedFullImageIds.delete(oldest);
      }
    }
  };
  preloader.onerror = () => {
    fullImagePrefetchControllers.delete(id);
  };
  preloader.decoding = "async";
  preloader.loading = "eager";
  preloader.src = url;
}

function getDragSelectionIds(img) {
  if (
    img &&
    selectedImageIds.value &&
    selectedImageIds.value.length > 1 &&
    selectedImageIds.value.includes(img.id)
  ) {
    return selectedImageIds.value.slice();
  }
  return img && img.id ? [img.id] : [];
}

function sanitizeFilenameSegment(name) {
  if (!name || typeof name !== "string") return "PixlVault";
  const cleaned = name.trim().replace(/[^a-z0-9_-]+/gi, "_");
  return cleaned || "PixlVault";
}

function buildSelectionZipFilename(count) {
  const base = sanitizeFilenameSegment(selectedGroupName.value || "PixlVault");
  return `${base}-${String(count).padStart(2, "0")}-images.zip`;
}

function buildExportUrlForIds(ids) {
  if (!Array.isArray(ids) || !ids.length) return "";
  const params = new URLSearchParams();
  ids.forEach((id) => {
    if (id !== undefined && id !== null) {
      params.append("id", id);
    }
  });
  return `${props.backendUrl}/pictures/export?${params.toString()}`;
}

function handleImageError(event) {
  const imgEl = event?.target;
  if (imgEl instanceof HTMLImageElement) {
    const src = imgEl.src || "";
    if (src.endsWith(".mp4") || src.endsWith(".webm") || src.endsWith(".mov")) {
      return;
    }
    if (imgEl.dataset.errorLogged === "1") {
      return;
    }
    imgEl.dataset.errorLogged = "1";
    console.error("[ImageGrid.vue] Image load error for:", src);
  }
  const src = imgEl?.src || "";
  if (!src) {
    return;
  }
  console.error("[ImageGrid] Image load error for", src);
}

function setupMultiExportDrag(event, ids) {
  if (!event?.dataTransfer || !Array.isArray(ids) || ids.length < 2) return;

  try {
    const dragData = {
      type: "image-ids",
      imageIds: ids,
    };
    event.dataTransfer.setData("application/json", JSON.stringify(dragData));
    console.debug("[DRAG] Multi-selection drag data set:", dragData);
  } catch (err) {
    console.error("[ERROR] Failed to set drag data:", err);
  }
}

function getSelectionSignature(ids) {
  if (!Array.isArray(ids) || !ids.length) return null;
  return ids
    .slice()
    .map((id) => String(id))
    .sort()
    .join(",");
}

function primeMultiSelectionZip(ids) {
  const signature = getSelectionSignature(ids);
  if (!signature) return;
  if (
    multiZipState.key === signature &&
    (multiZipState.status === "pending" || multiZipState.status === "ready")
  ) {
    return;
  }
  resetMultiSelectionZip();
  multiZipState.key = signature;
  multiZipState.status = "pending";
  multiZipState.filename = buildSelectionZipFilename(ids.length);
  const url = buildExportUrlForIds(ids);
  if (!url) {
    multiZipState.status = "error";
    multiZipState.error = "Missing export URL";
    return;
  }
  const controller = new AbortController();
  multiZipAbortController = controller;
  apiClient
    .get(url, { signal: controller.signal })
    .then((res) => res.data.blob()) // Access the blob from res.data
    .then((blob) => {
      if (multiZipState.key !== signature) return;
      multiZipState.status = "ready";
      multiZipState.blob = blob;
    })
    .catch((err) => {
      if (controller.signal.aborted) return;
      multiZipState.status = "error";
      multiZipState.error = err?.message || "Export failed";
    });
}

function resetMultiSelectionZip() {
  if (multiZipAbortController) {
    multiZipAbortController.abort();
    multiZipAbortController = null;
  }
  multiZipState.key = null;
  multiZipState.status = "idle";
  multiZipState.blob = null;
  multiZipState.filename = null;
  multiZipState.error = null;
}

function attachMultiSelectionZipToDrag(event, ids) {
  if (!event?.dataTransfer) return false;
  const signature = getSelectionSignature(ids);
  if (
    !signature ||
    signature !== multiZipState.key ||
    multiZipState.status !== "ready" ||
    !multiZipState.blob
  ) {
    return false;
  }
  try {
    const file = new File(
      [multiZipState.blob],
      multiZipState.filename || buildSelectionZipFilename(ids.length),
      { type: "application/zip" },
    );
    event.dataTransfer.items.add(file);
    event.dataTransfer.effectAllowed = "copy";
    event.dataTransfer.dropEffect = "copy";
    return true;
  } catch (err) {
    console.debug("[DRAG] Unable to attach zip file", err);
    return false;
  }
}

function prepareThumbnailNativeDrag(img, event) {
  if (!img || !event) return;
  const selectionIds = getDragSelectionIds(img);
  if (selectionIds.length > 1) return;
  prefetchFullImage(img);
  if (event.pointerType === "mouse" && event.button !== 0) return;
}

function handleThumbnailPointerRelease(event) {
  if (dragSource.value === "grid") return;
}

function debugLogDataTransfer(label, dataTransfer) {
  if (!dataTransfer) {
    console.debug(`[DRAG] ${label}: no dataTransfer available`);
    return;
  }
  const types = Array.from(dataTransfer.types || []);
  console.debug(`[DRAG] ${label}: types`, types);

  const itemsSummary = dataTransfer.items
    ? Array.from(dataTransfer.items).map((item, idx) => ({
        idx,
        kind: item.kind,
        type: item.type,
      }))
    : [];
  if (itemsSummary.length) {
    console.debug(`[DRAG] ${label}: items`, itemsSummary);
  }

  const interestingTypes = new Set([
    "text/uri-list",
    "text/html",
    "text/plain",
    "public.url",
    "public.url-name",
    "com.apple.pasteboard.promised-file-url",
    "com.apple.pasteboard.promised-file-content-type",
    "com.apple.pasteboard.promised-file-extension",
  ]);

  for (const type of types) {
    if (typeof type !== "string") continue;
    const shouldRead = type.startsWith("text/") || interestingTypes.has(type);
    if (!shouldRead) continue;
    try {
      const value = dataTransfer.getData(type) || "";
      const truncated = value.length > 200 ? `${value.slice(0, 200)}…` : value;
      console.debug(`[DRAG] ${label}: payload (${type})`, truncated);
    } catch (err) {
      console.debug(`[DRAG] ${label}: unable to read ${type}`, err);
    }
  }
}

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
    props.selectedCharacter !== props.allPicturesId &&
    props.selectedCharacter !== props.unassignedPicturesId
  ) {
    apiClient
      .delete(`${backendUrl}/characters/${props.selectedCharacter}/faces`, {
        data: { picture_ids: selectedImageIds.value },
      })
      .catch((err) => {
        alert(`Error removing images from character: ${err.message}`);
      })
      .finally(() => {
        // Remove affected images from grid immediately
        allGridImages.value = allGridImages.value.filter(
          (img) => !selectedImageIds.value.includes(img.id),
        );
        selectedImageIds.value = [];
        lastSelectedIndex = null;
        fetchAllGridImages().then(() => {
          loadedRanges.value = [];
          updateVisibleThumbnails();
          emit("refresh-sidebar");
        });
        updateVisibleThumbnails();
      });
    return;
  }
  // Remove from set
  if (
    props.selectedSet &&
    props.selectedSet !== props.allPicturesId &&
    props.selectedSet !== props.unassignedPicturesId
  ) {
    Promise.all(
      selectedImageIds.value.map((id) =>
        apiClient
          .delete(
            `${backendUrl}/picture_sets/${props.selectedSet}/members/${id}`,
          )

          .catch((err) => {
            alert(`Error removing image ${id} from set: ${err.message}`);
          }),
      ),
    ).then(async () => {
      // Remove affected images from grid immediately
      allGridImages.value = allGridImages.value.filter(
        (img) => !selectedImageIds.value.includes(img.id),
      );
      selectedImageIds.value = [];
      lastSelectedIndex = null;
      await fetchAllGridImages();
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
      `Delete ${selectedImageIds.value.length} selected image(s)? This cannot be undone.`,
    )
  )
    return;
  const backendUrl = props.backendUrl;
  Promise.all(
    selectedImageIds.value.map((id) =>
      apiClient.delete(`${backendUrl}/pictures/${id}`).catch((err) => {
        alert(`Error deleting image ${id}: ${err.message}`);
      }),
    ),
  ).then(() => {
    // Remove deleted images from grid and clear selection
    allGridImages.value = allGridImages.value.filter(
      (img) => !selectedImageIds.value.includes(img.id),
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
  loadedRanges.value = [];
  allGridImages.value = [];
  selectedImageIds.value = [];
  lastSelectedIndex = null;
  fetchAllGridImages().then(() => {
    updateVisibleThumbnails();
  });
  emit("refresh-sidebar");
}

// Adjust debounce timing to 200ms for better responsiveness
const debouncedFetchAllGridImages = debounce(fetchAllGridImages, 200);

// Debounced version of fetchAllGridImages
watch(
  () => props.gridVersion,
  () => {
    console.log(
      "[ImageGrid.vue] Grid version changed, refreshing all thumbnails.",
    );
    loadedRanges.value = [];
    allGridImages.value = [];
    selectedImageIds.value = [];
    lastSelectedIndex = null;
    debouncedFetchAllGridImages();
    fetchAllPicturesCount();
  },
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
  console.log(
    "Updating selected group name: ",
    props.selectedCharacter,
    props.selectedSet,
    props.allPicturesId,
  );
  if (
    props.selectedCharacter &&
    props.selectedCharacter !== `${props.allPicturesId}` &&
    props.selectedCharacter !== `${props.unassignedPicturesId}`
  ) {
    try {
      const res = await apiClient.get(
        `${props.backendUrl}/characters/${props.selectedCharacter}`,
      );
      const char = await res.data;
      name = char.name || "";
    } catch (e) {
      console.error("Character fetch failed:", e);
    }
  } else if (
    props.selectedSet &&
    props.selectedSet !== `${props.allPicturesId}` &&
    props.selectedSet !== `${props.unassignedPicturesId}`
  ) {
    try {
      const res = await apiClient.get(
        `${props.backendUrl}/picture_sets/${props.selectedSet}`,
      );
      const set = await res.data;
      name = set.name || "";
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
  { immediate: true },
);

// --- Multi-selection state ---
// Local selection state (mirrors parent prop)
const selectedImageIds = ref([]);
let lastSelectedIndex = null;

watch(
  selectedImageIds,
  (ids) => {
    if (ids.length > 1) {
      primeMultiSelectionZip(ids);
    } else {
      resetMultiSelectionZip();
    }
  },
  { deep: false },
);

// --- Overlay ---
async function fetchImageInfo(imageId) {
  try {
    const res = await apiClient.get(
      `${props.backendUrl}/pictures/${imageId}/metadata`,
    );
    const data = await res.data;
    return data;
  } catch (e) {
    console.error("Tag fetch failed:", e);
    return [];
  }
}

async function openOverlay(img) {
  if (!img || !img.id) return;
  const requestedId = img.id;
  overlayImage.value = { ...img };
  overlayOpen.value = true;

  const latestInfo = await fetchImageInfo(requestedId);
  if (!latestInfo || Array.isArray(latestInfo)) return;
  if (!overlayImage.value || overlayImage.value.id !== requestedId) return;
  overlayImage.value = { ...overlayImage.value, ...latestInfo };
}

function closeOverlay() {
  overlayOpen.value = false;
}

async function setScore(img, n) {
  const newScore = (img.score || 0) === n ? 0 : n;
  applyScore(img, newScore);
}

function isScoreSortActive() {
  return typeof props.selectedSort === "string"
    ? props.selectedSort.toUpperCase() === "SCORE"
    : false;
}

function invalidateVisibleThumbnailRanges() {
  const start = Math.max(0, visibleStart.value - divisibleViewWindow.value);
  const end = Math.min(
    allGridImages.value.length,
    visibleEnd.value + divisibleViewWindow.value,
  );
  loadedRanges.value = loadedRanges.value.filter(
    ([rangeStart, rangeEnd]) => rangeEnd <= start || rangeStart >= end,
  );
  updateVisibleThumbnails();
}

function repositionImageByScore(imageId, newScore) {
  const items = allGridImages.value.slice();
  const currentIndex = items.findIndex((item) => item.id === imageId);
  if (currentIndex === -1) return;

  const target = items[currentIndex];
  target.score = newScore;
  items.splice(currentIndex, 1);

  const targetScore = newScore ?? 0;
  const descending = props.selectedDescending === true;
  let insertIndex = items.findIndex((item) => {
    const score = item.score ?? 0;
    return descending ? score < targetScore : score > targetScore;
  });
  if (insertIndex === -1) insertIndex = items.length;
  items.splice(insertIndex, 0, target);

  for (let i = 0; i < items.length; i += 1) {
    items[i].idx = i;
  }

  allGridImages.value = items;
  invalidateVisibleThumbnailRanges();
  nextTick(() => {
    const grid = gridContainer.value;
    if (!grid) return;
    const card = grid.querySelectorAll(".image-card")[insertIndex];
    if (card && card.scrollIntoView) {
      card.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  });
}

async function applyScore(img, newScore) {
  console.debug("Applying score:", newScore);
  const imageId = img.id || (overlayImage.value && overlayImage.value.id);
  if (!imageId) {
    alert("Failed to set score: image id is missing.");
    return;
  }
  try {
    console.debug(
      "PATCH /pictures/",
      imageId,
      " body: { score:",
      newScore,
      "}",
    );
    const res = await apiClient.patch(
      `${props.backendUrl}/pictures/${imageId}`,
      {
        score: newScore,
      },
    );

    // Update score in allGridImages
    const gridImg = allGridImages.value.find((i) => i.id === imageId);
    if (gridImg) {
      gridImg.score = newScore;
    }
    // Update overlay image if open and matches
    if (
      overlayOpen.value &&
      overlayImage.value &&
      overlayImage.value.id === imageId
    ) {
      overlayImage.value = { ...overlayImage.value, score: newScore };
    }

    if (isScoreSortActive()) {
      repositionImageByScore(imageId, newScore);
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

  // Log dataTransfer contents for debugging
  console.debug("[DEBUG] dataTransfer types:", e.dataTransfer.types);
  console.debug("[DEBUG] dataTransfer items:", e.dataTransfer.items);

  // Focus on standard dataTransfer types for inspection
  const standardTypesToInspect = ["text/uri-list", "text/html", "text/plain"];
  for (const type of standardTypesToInspect) {
    if (e.dataTransfer.types.includes(type)) {
      const data = e.dataTransfer.getData(type);
      console.debug(`[DEBUG] dataTransfer content for ${type}:`, data);
    }
  }

  const hasSupported = dataTransferHasSupportedMedia(e.dataTransfer);
  if (!hasSupported) return;
  dragOverlayVisible.value = true;

  const itemCount = e.dataTransfer.items.length;
  if (
    props.selectedCharacter &&
    props.selectedCharacter !== props.allPicturesId &&
    props.selectedCharacter !== props.unassignedPicturesId
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
  debugLogDataTransfer("drop", e.dataTransfer);

  // Ignore drag-and-drop if the source is the grid itself
  if (
    dragSource.value === "grid" ||
    e.dataTransfer.types.includes("application/json")
  ) {
    console.debug("Drag-and-drop within the grid ignored.");
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
      allPicturesId: "ALL",
      unassignedPicturesId: "UNASSIGNED",
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
        newScrollTop - scrollWrapper.value.clientHeight,
      );
    } else if (key === "PageDown") {
      newScrollTop = Math.min(
        maxScroll,
        newScrollTop + scrollWrapper.value.clientHeight,
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
const imagesLoading = ref(false);
const imagesError = ref(null);
const totalAllPicturesCount = ref(0);

function normalizeStackThreshold(value) {
  if (value === null || value === undefined || value === "") return 0.94;
  const parsed = parseFloat(value);
  if (!Number.isFinite(parsed) || parsed <= 0) return 0.94;
  return Math.max(0.9, Math.min(0.98, parsed));
}

function getStackColor(stackIndex) {
  const hue = (stackIndex * STACK_COLOR_STEP) % 360;
  return `hsl(${hue} 70% 55%)`;
}

function getStackCardStyle(img) {
  if (!img) return {};
  const rawIndex =
    typeof img.stackIndex === "number"
      ? img.stackIndex
      : typeof img.stack_index === "number"
        ? img.stack_index
        : null;
  if (rawIndex === null) return {};
  const color = img.stackColor || getStackColor(rawIndex);
  return {
    backgroundColor: color,
    padding: "6px",
    borderRadius: "0px",
    boxShadow: "none",
    border: "1px dashed black",
  };
}

function buildPictureIdsQueryParams() {
  const params = new URLSearchParams();
  // If a set is selected, filter by set
  if (
    props.selectedSet &&
    props.selectedSet !== props.allPicturesId &&
    props.selectedSet !== props.unassignedPicturesId
  ) {
    params.append("set_id", props.selectedSet);
  } else if (
    props.selectedCharacter !== undefined &&
    props.selectedCharacter !== null &&
    props.selectedCharacter !== "" &&
    props.selectedCharacter !== props.allPicturesId
  ) {
    params.append("character_id", props.selectedCharacter);
  }

  if (
    props.selectedSort === "CHARACTER_LIKENESS" &&
    props.similarityCharacter
  ) {
    params.append("reference_character_id", props.similarityCharacter);
  }

  if (props.searchQuery && props.searchQuery.trim()) {
    params.append("query", props.searchQuery.trim());
  }
  if (props.selectedSort && props.selectedSort.trim()) {
    params.append("sort", props.selectedSort.trim());
  }
  if (typeof props.selectedDescending === "boolean") {
    console.log(
      "[ImageGrid.vue] Constructing query with descending:",
      props.selectedDescending,
    );
    params.append("descending", props.selectedDescending ? "true" : "false");
  } else {
    console.warn(
      "[ImageGrid.vue] selectedDescending is not boolean, skipping param. Type:",
      typeof props.selectedDescending,
    );
  }
  // Add format filter for backend media type filtering
  if (props.mediaTypeFilter === "images") {
    console.log(
      "[ImageGrid.vue] Building query params for image formats only",
      PIL_IMAGE_EXTENSIONS,
    );
    for (const ext of PIL_IMAGE_EXTENSIONS) {
      params.append("format", ext.toUpperCase());
    }
  } else if (props.mediaTypeFilter === "videos") {
    for (const ext of VIDEO_EXTENSIONS) {
      params.append("format", ext.toUpperCase());
    }
  }
  return params.toString();
}

function buildStackQueryParams() {
  const params = new URLSearchParams();
  if (
    props.selectedSet &&
    props.selectedSet !== props.allPicturesId &&
    props.selectedSet !== props.unassignedPicturesId
  ) {
    params.append("set_id", props.selectedSet);
  } else if (
    props.selectedCharacter !== undefined &&
    props.selectedCharacter !== null &&
    props.selectedCharacter !== "" &&
    props.selectedCharacter !== props.allPicturesId
  ) {
    params.append("character_id", props.selectedCharacter);
  }

  if (props.mediaTypeFilter === "images") {
    for (const ext of PIL_IMAGE_EXTENSIONS) {
      params.append("format", ext.toUpperCase());
    }
  } else if (props.mediaTypeFilter === "videos") {
    for (const ext of VIDEO_EXTENSIONS) {
      params.append("format", ext.toUpperCase());
    }
  }

  return params.toString();
}

// Fetch total image count for current filters
async function fetchAllGridImages() {
  console.log("[ImageGrid.vue] fetchAllGridImages called.");
  imagesLoading.value = true;
  imagesError.value = null;
  try {
    let images = [];
    const requestId = Date.now();
    fetchAllGridImages.lastRequestId = requestId;
    if (props.selectedSort === STACKS_SORT_KEY) {
      const threshold = normalizeStackThreshold(props.stackThreshold);
      const stackParams = buildStackQueryParams();
      const url = `${props.backendUrl}/pictures/stacks?threshold=${encodeURIComponent(
        threshold,
      )}${stackParams ? `&${stackParams}` : ""}`;
      const res = await apiClient.get(url);
      const data = await res.data;
      if (fetchAllGridImages.lastRequestId !== requestId) return;
      const stackImages = Array.isArray(data) ? data : [];
      images = stackImages.map((img) => {
        const stackIndex =
          typeof img.stack_index === "number"
            ? img.stack_index
            : typeof img.stackIndex === "number"
              ? img.stackIndex
              : null;
        return {
          ...img,
          stackIndex,
          stackColor:
            typeof stackIndex === "number" ? getStackColor(stackIndex) : null,
        };
      });
    } else if (
      props.selectedSet &&
      props.selectedSet !== props.allPicturesId &&
      props.selectedSet !== props.unassignedPicturesId
    ) {
      const url = `${props.backendUrl}/picture_sets/${props.selectedSet}`;
      const res = await apiClient.get(url);
      const data = await res.data;
      images = data.pictures || [];
    } else if (props.searchQuery && props.searchQuery.trim()) {
      // Use /pictures/search endpoint for text search
      const params = buildPictureIdsQueryParams();
      const url = `${
        props.backendUrl
      }/pictures/search?query=${encodeURIComponent(
        props.searchQuery.trim(),
      )}&top_n=10000${params ? `&${params}` : ""}`;
      const res = await apiClient.get(url);
      const data = await res.data;
      images = data;
    } else {
      const params = buildPictureIdsQueryParams();
      // Only use allowed parameters: sort, offset, limit, threshold
      const url = `${props.backendUrl}/pictures?offset=0&limit=10000${
        params ? `&${params}` : ""
      }`;
      const res = await apiClient.get(url);
      const data = await res.data;
      images = data;
    }
    const newImages = images.map((img, i) => ({
      ...img,
      idx: i,
      thumbnail: null,
    }));
    console.log("Updating allGridImages with fetched images:", newImages);
    allGridImages.value = newImages;
    const cols = columns.value || 1;
    const windowCount = Math.max(cols, divisibleViewWindow.value || cols);
    visibleStart.value = 0;
    visibleEnd.value = Math.min(newImages.length, windowCount);
  } catch (e) {
    imagesError.value = e.message;
    allGridImages.value = [];
  } finally {
    imagesLoading.value = false;
  }
  updateVisibleThumbnails();
}

async function fetchAllPicturesCount() {
  try {
    const res = await apiClient.get(
      `${props.backendUrl}/characters/${props.allPicturesId}/summary`,
    );
    const data = await res.data;
    totalAllPicturesCount.value = Number(data.image_count) || 0;
  } catch (e) {
    console.warn("[ImageGrid.vue] Failed to fetch all pictures count:", e);
  }
}

// Update watchers to use the debounced function
watch(
  [
    () => props.selectedCharacter,
    () => props.selectedSet,
    () => props.searchQuery,
    () => props.selectedSort,
    () => props.stackThreshold,
  ],
  () => {
    console.log(
      "[ImageGrid.vue] Filters changed. Resetting state and fetching total image count.",
    );
    loadedRanges.value = [];
    allGridImages.value = [];
    selectedImageIds.value = [];
    lastSelectedIndex = null;
    updateSelectedGroupName();
    debouncedFetchAllGridImages();
  },
);

watch(
  () => props.gridVersion,
  () => {
    console.log(
      "[ImageGrid.vue] Grid version changed, refreshing all thumbnails.",
    );
    loadedRanges.value = [];
    allGridImages.value = [];
    selectedImageIds.value = [];
    lastSelectedIndex = null;
    debouncedFetchAllGridImages();
  },
);

watch([() => props.mediaTypeFilter], () => {
  console.log(
    "[ImageGrid.vue] Media Type filters changed. Resetting state and fetching total image count.",
  );
  // Reset loaded ranges, thumbnails, pagination, and fetch new count/images for filter
  loadedRanges.value = [];
  selectedImageIds.value = [];
  lastSelectedIndex = null;
  visibleStart.value = 0;
  visibleEnd.value = 0;
  allGridImages.value = [];
  fetchAllGridImages().then(() => {
    updateVisibleThumbnails();
  });
});

// Track loaded batch ranges to avoid duplicate requests
const loadedRanges = ref([]);
// Debounce timer for scroll-triggered fetches
let thumbFetchTimeout = null;

// Track which indices are visible in the grid

const visibleStart = ref(0);
const visibleEnd = ref(0);

const rowHeight = ref(props.thumbnailSize + 24);

// Internal columns state
const columns = ref(1);

const renderStart = computed(() => {
  const cols = columns.value;
  let start = Math.max(0, visibleStart.value - divisibleViewWindow.value);
  return start;
});

const renderEnd = computed(() => {
  const cols = columns.value;
  let end = Math.min(
    allGridImages.value.length,
    visibleEnd.value + divisibleViewWindow.value,
  );
  return end;
});

const topSpacerHeight = computed(() => {
  const cols = columns.value;
  const rowsAbove = Math.floor(renderStart.value / cols);
  const height = rowsAbove > 0 ? rowsAbove * rowHeight.value : 1;
  return height;
});

const bottomSpacerHeight = computed(() => {
  const cols = columns.value;
  const lastRenderedRow = Math.floor((renderEnd.value - 1) / cols) + 1;
  const totalRows = Math.ceil(allGridImages.value.length / cols);
  const rowsBelow = totalRows - lastRenderedRow;
  const height = rowsBelow > 0 ? rowsBelow * rowHeight.value : 0;
  return height;
});

// Compute grid images (id, idx, thumbnail)
const allGridImages = ref([]);

function filterImagesByMediaType(images) {
  let filtered = images;
  if (props.mediaTypeFilter === "images") {
    filtered = filtered.filter((img) => {
      if (!img) return false;
      const candidates = [img.filename, img.name, img.id, img.format]
        .filter(Boolean)
        .map((v) => (typeof v === "string" ? v : ""));
      return candidates.some((val) => isSupportedImageFile(val));
    });
  } else if (props.mediaTypeFilter === "videos") {
    filtered = filtered.filter((img) => {
      if (!img) return false;
      const candidates = [img.filename, img.name, img.id, img.format]
        .filter(Boolean)
        .map((v) => (typeof v === "string" ? v : ""));
      return candidates.some((val) => isSupportedVideoFile(val));
    });
  }
  return filtered;
}

const filteredGridCount = computed(() => {
  if (!allGridImages.value) return 0;
  return filterImagesByMediaType(allGridImages.value).length;
});

const EMPTY_STATE_DELAY_MS = 350;
const emptyStateDelayPassed = ref(false);
let emptyStateDelayTimer = null;

const showEmptyState = computed(() => {
  return (
    !imagesLoading.value &&
    filteredGridCount.value === 0 &&
    emptyStateDelayPassed.value
  );
});

const canShowAllPicturesButton = computed(() => {
  return totalAllPicturesCount.value > 0;
});

const emptyStateTitle = computed(() => {
  return totalAllPicturesCount.value > 0
    ? "No pictures match the current filters"
    : "No pictures in the database.";
});

const emptyStateSubtitle = computed(() => {
  return totalAllPicturesCount.value > 0
    ? "Try clearing filters, adjusting your search, or switching sets."
    : "Add pictures by dragging them here.";
});

watch([imagesLoading, filteredGridCount], ([loading, count]) => {
  if (emptyStateDelayTimer) {
    clearTimeout(emptyStateDelayTimer);
    emptyStateDelayTimer = null;
  }

  if (loading || count > 0) {
    emptyStateDelayPassed.value = false;
    return;
  }

  emptyStateDelayPassed.value = false;
  emptyStateDelayTimer = setTimeout(() => {
    if (!imagesLoading.value && filteredGridCount.value === 0) {
      emptyStateDelayPassed.value = true;
    }
  }, EMPTY_STATE_DELAY_MS);
});

watch(allGridImages, (newVal, oldVal) => {
  console.log("[ImageGrid.vue] allGridImages updated:", {
    oldLength: oldVal.length,
    newLength: newVal.length,
    oldValue: oldVal,
    newValue: newVal,
  });
});

const gridImagesToRender = computed(() => {
  if (!allGridImages.value) {
    console.warn("allGridImages is undefined");
    return [];
  }

  const filtered = filterImagesByMediaType(allGridImages.value);
  return filtered.slice(renderStart.value, renderEnd.value);
});

watch(gridImagesToRender, (newVal, oldVal) => {
  console.log("[ImageGrid.vue] gridImagesToRender updated:", {
    oldLength: oldVal.length,
    newLength: newVal.length,
    oldValue: oldVal,
    newValue: newVal,
  });
});

// Batch fetch metadata (including thumbnail) for visible range
async function fetchThumbnailsBatch(start, end) {
  start = renderStart.value;
  end = renderEnd.value;

  /* console.debug(
    `[BATCH REQUEST] start=${start}, end=${end}, loadedRanges=${JSON.stringify(
      loadedRanges.value
    )}`
  ); */
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
      props.selectedSet !== props.allPicturesId &&
      props.selectedSet !== props.unassignedPicturesId
    ) {
      const url = `${props.backendUrl}/picture_sets/${props.selectedSet}`;
      const res = await apiClient.get(url);
      const data = await res.data;
      images = data.pictures ? data.pictures.slice(start, end) : [];
      ids = images.map((img) => img.id);
    } else {
      // Only fetch if we don't already have metadata for this range
      images = allGridImages.value.slice(start, end);
      ids = images.map((img) => img.id);
    }
    /* console.debug(
      `[BATCH RESPONSE] Received ${images.length} images:`,
      images.map((img) => img.id)
    ); */
    // Prepare grid image objects
    const gridImages = images.map((img, idx) => ({
      ...img,
      score: img.score ?? 0,
      idx: start + idx, // Ensure idx is global index
      thumbnail: null,
    }));
    // Now fetch thumbnails for these IDs
    ids = ids.filter((id) => id !== null && id !== undefined);
    if (ids.length) {
      ids = Array.from(new Set(ids.map((id) => String(id))));
      const thumbRes = await apiClient.post(
        `${props.backendUrl}/pictures/thumbnails`,
        JSON.stringify({ ids }),
      );
      const thumbData = await thumbRes.data;
      for (const gridImg of gridImages) {
        const thumbObj = thumbData[String(gridImg.id)];
        gridImg.thumbnail =
          thumbObj && thumbObj.thumbnail
            ? `data:image/png;base64,${thumbObj.thumbnail}`
            : null;
        gridImg.faces =
          thumbObj && Array.isArray(thumbObj.faces) ? thumbObj.faces : [];
      }
    }
    // Insert/update images at their correct indices
    console.log("Updating allGridImages with thumbnails");
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
    allGridImages.value.length,
    visibleEnd.value + divisibleViewWindow.value,
  );
  console.log("[ImageGrid.vue] Updating visible thumbnails:", {
    start,
    end,
    visibleStart: visibleStart.value,
    visibleEnd: visibleEnd.value,
    divisibleViewWindow: divisibleViewWindow.value,
    allGridImagesLength: allGridImages.value.length,
  });

  // Debounce fetches to avoid excessive requests
  if (thumbFetchTimeout) clearTimeout(thumbFetchTimeout);

  thumbFetchTimeout = setTimeout(async () => {
    console.log("[ImageGrid.vue] Fetching thumbnails batch:", { start, end });
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
        el.clientHeight,
      );
      // Only trigger buffer expansion/fetch if user is near buffer end
      // Always fetch thumbnails for the current visible window
      updateVisibleThumbnails();
    }
  }, 50);
}

// Selection logic
const isImageSelected = (id) =>
  selectedImageIds.value && selectedImageIds.value.includes(id);

function handleThumbnailNativeDragStart(img, event) {
  dragSource.value = "grid";
  const selectionIds = getDragSelectionIds(img);
  if (selectionIds.length > 1) {
    setupMultiExportDrag(event, selectionIds);
    return;
  }
  const target = event?.target;
  if (target instanceof HTMLImageElement && event?.dataTransfer?.setDragImage) {
    const width = target.naturalWidth || target.width || 160;
    const height = target.naturalHeight || target.height || 90;
    event.dataTransfer.setDragImage(
      target,
      Math.max(1, width / 2),
      Math.max(1, height / 2),
    );
  }
  event.dataTransfer.setData(
    "application/json",
    JSON.stringify({
      type: "image-ids",
      imageIds: [img.id],
    }),
  );
}

function handleThumbnailNativeDragEnd(event) {
  dragSource.value = null;
}

function handleVideoDragStart(img, event) {
  if (!img) return;
  dragSource.value = "grid";
  const selectionIds = getDragSelectionIds(img);
  if (selectionIds.length > 1) {
    setupMultiExportDrag(event, selectionIds);
    return;
  }
  event.dataTransfer.setData(
    "application/json",
    JSON.stringify({
      type: "image-ids",
      imageIds: [img.id],
    }),
  );
}

function handleVideoDragEnd() {
  dragSource.value = null;
}

function handleContainerDragStart(img, event) {
  if (!img || !event?.dataTransfer) return;
  const existing = event.dataTransfer.getData("application/json");
  if (existing) return;
  dragSource.value = "grid";
  const selectionIds = getDragSelectionIds(img);
  if (selectionIds.length > 1) {
    setupMultiExportDrag(event, selectionIds);
    return;
  }
  const thumbEl = thumbnailRefs[img.id];
  if (!isVideo(img) && thumbEl instanceof HTMLImageElement) {
    const width = thumbEl.naturalWidth || thumbEl.width || 160;
    const height = thumbEl.naturalHeight || thumbEl.height || 90;
    if (event.dataTransfer?.setDragImage) {
      event.dataTransfer.setDragImage(
        thumbEl,
        Math.max(1, width / 2),
        Math.max(1, height / 2),
      );
    }
  }
  if (isVideo(img)) {
    const previewEl = dragPreviewRefs[img.id];
    if (previewEl && event.dataTransfer?.setDragImage) {
      const width = previewEl.naturalWidth || previewEl.width || 160;
      const height = previewEl.naturalHeight || previewEl.height || 90;
      event.dataTransfer.setDragImage(
        previewEl,
        Math.max(1, width / 2),
        Math.max(1, height / 2),
      );
    }
  }
  event.dataTransfer.setData(
    "application/json",
    JSON.stringify({
      type: "image-ids",
      imageIds: [img.id],
    }),
  );
}

function handleContainerDragEnd(img, event) {
  if (dragSource.value !== "grid") return;
  dragSource.value = null;
}

// Event handlers: these should emit events or call parent-provided functions
// Legacy drag logic replaced with native media dragging for Finder compatibility

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
  } else if (isShift && lastSelectedIndex === null) {
    newSelection = [img.id];
    lastSelectedIndex = idx;
  } else {
    return;
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
  event.stopPropagation();
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
    const isMobile = typeof window !== "undefined" && window.innerWidth <= 900;
    const isLandscape =
      typeof window !== "undefined" &&
      window.matchMedia &&
      window.matchMedia("(orientation: landscape)").matches;

    if (isMobile) {
      columns.value = isLandscape ? 4 : 2;
      return;
    }

    columns.value = Math.max(
      1,
      Math.floor(containerWidth / (props.thumbnailSize + 32)),
    );
  });
}

async function removeTagFromImage(imageId, tag) {
  if (!imageId) {
    console.error("Image ID is required to remove a tag.");
    return;
  }

  apiClient
    .delete(`${props.backendUrl}/pictures/${imageId}/tags/${tag}`)

    .catch((error) => {
      console.error("Error removing tag:", error);
    });
}

async function addTagToImage(imageId, tag) {
  try {
    const response = await apiClient.post(
      `${props.backendUrl}/pictures/${imageId}/tags`,
      {
        tag: tag,
      },
    );
    console.log(`Tag '${tag}' added to image ${imageId}`);
  } catch (error) {
    console.error("Error adding tag:", error);
  }
}

onMounted(() => {
  updateColumns();
  window.addEventListener("resize", updateColumns);
  window.addEventListener("keydown", handleKeyDown);
});

// Clear selection on ESC key
function handleKeyDown(event) {
  if (overlayOpen.value) return; // Ignore if overlay is open
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
  } else if (
    hoveredImageIdx.value !== null &&
    selectedImageIds.value.length === 0 &&
    !overlayOpen.value &&
    /^[1-5]$|^0$/.test(event.key)
  ) {
    // Number key pressed, set score for hovered image
    const idx = hoveredImageIdx.value;
    const img = allGridImages.value[idx];
    if (img && img.id) {
      let score = parseInt(event.key, 10);
      if (score === 0) score = 5;
      setScore(img, score);
      event.preventDefault();
    }
  }
}

watch(
  () => props.thumbnailSize,
  () => {
    // Recalculate visibleStart and visibleEnd after columns/rowHeight update
    nextTick(() => {
      updateColumns();
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
  },
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
  removeImagesById,
});

// Remove images by ID (for event-driven removal)
function removeImagesById(imageIds) {
  if (!Array.isArray(imageIds) || !imageIds.length) {
    console.log("No image IDs provided for removal.");
    return;
  }
  console.log("Removing images by ID:", imageIds);
  allGridImages.value = allGridImages.value.filter(
    (img) => !imageIds.includes(img.id),
  );
  selectedImageIds.value = selectedImageIds.value.filter(
    (id) => !imageIds.includes(id),
  );
  loadedRanges.value = [];
  updateVisibleThumbnails();
}

// --- Export to Zip ---
function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function exportCurrentViewToZip(options = {}) {
  const captionMode = options.captionMode || "description";
  const includeCharacterName = options.includeCharacterName !== false;
  let url = `${props.backendUrl}/pictures/export`;
  const params = buildPictureIdsQueryParams();
  const extraParams = new URLSearchParams();
  if (captionMode) {
    extraParams.append("caption_mode", captionMode);
  }
  if (includeCharacterName) {
    extraParams.append("include_character_name", "true");
  }
  const extraParamString = extraParams.toString();
  if (params) {
    url += `?${params}`;
    if (extraParamString) {
      url += `&${extraParamString}`;
    }
  } else if (extraParamString) {
    url += `?${extraParamString}`;
  }

  try {
    exportProgress.visible = true;
    exportProgress.status = "starting";
    exportProgress.processed = 0;
    exportProgress.total = 0;
    exportProgress.message = "Preparing export...";

    const startRes = await apiClient.get(url);
    const taskId = startRes?.data?.task_id;
    if (!taskId) {
      throw new Error("Missing task_id from export response.");
    }

    let downloadUrl = null;
    const maxAttempts = 600;
    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
      const statusRes = await apiClient.get(
        `${props.backendUrl}/pictures/export/status`,
        { params: { task_id: taskId } },
      );
      const status = statusRes?.data?.status;
      exportProgress.status = status || "in_progress";
      exportProgress.processed = statusRes?.data?.processed || 0;
      exportProgress.total = statusRes?.data?.total || 0;
      exportProgress.message =
        status === "completed"
          ? "Finalizing download..."
          : "Exporting images...";
      if (status === "completed") {
        downloadUrl = statusRes?.data?.download_url;
        break;
      }
      if (status === "failed") {
        throw new Error("Export failed on server.");
      }
      await sleep(1000);
    }

    if (!downloadUrl) {
      throw new Error("Export timed out waiting for ZIP.");
    }

    const fileRes = await apiClient.get(`${props.backendUrl}${downloadUrl}`, {
      responseType: "blob",
    });

    let filename = "pixlvault_export.zip";
    const disposition = fileRes.headers["content-disposition"];
    if (disposition) {
      const match = disposition.match(/filename="?([^";]+)"?/);
      if (match) filename = match[1];
    }

    const link = document.createElement("a");
    link.href = URL.createObjectURL(fileRes.data);
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    setTimeout(() => {
      URL.revokeObjectURL(link.href);
      document.body.removeChild(link);
      exportProgress.visible = false;
      exportProgress.status = "idle";
      exportProgress.message = "";
    }, 2000);
  } catch (e) {
    exportProgress.status = "failed";
    exportProgress.message = "Export failed";
    alert("Export failed: " + (e.message || e));
    setTimeout(() => {
      exportProgress.visible = false;
      exportProgress.status = "idle";
      exportProgress.message = "";
    }, 4000);
  }
}

// Search functionality
const searchQuery = ref(props.searchQuery);
const { visible, openSearchOverlay, closeSearchOverlay } = useSearchOverlay();

function handleSearch(query) {
  console.log("Search triggered with query:", query);
  searchQuery.value = query;
  props.searchQuery = query;
  fetchAllGridImages().then(() => {
    updateVisibleThumbnails();
  });
}

onMounted(() => {
  console.log("ImageGrid mounted. Initial search query:", searchQuery.value);
});

watch(searchQuery, (newQuery) => {
  console.log("Search query updated:", newQuery);
});

// Function to clear searchQuery
function clearSearchQuery() {
  console.log("[ImageGrid.vue] clearSearchQuery called");
  emit("clear-search", "");
}

function handleEmptyStateReset() {
  emit("reset-to-all");
}
</script>

<style scoped>
.drag-overlay {
  position: fixed;
  inset: 0;
  background: rgba(var(--v-theme-accent), 0.2);
  z-index: 9999;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: all;
  border: 8px solid rgb(var(--v-theme-accent));
  border-radius: 16px; /* rounded corners */
  box-sizing: border-box;
  transition:
    border-color 0.2s,
    background 0.2s;
  color: #ffffff;
  font-size: 3em;
  font-weight: bold;
}

.export-progress {
  position: absolute;
  top: 10px;
  right: 12px;
  z-index: 120;
  background: rgba(20, 20, 20, 0.9);
  color: #fff;
  padding: 10px 12px;
  border-radius: 8px;
  min-width: 220px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.3);
}

.export-progress-error {
  background: rgba(140, 20, 20, 0.95);
}

.export-progress-title {
  font-size: 0.9em;
  margin-bottom: 8px;
}

.export-progress-bar {
  width: 100%;
  height: 8px;
  background: rgba(255, 255, 255, 0.15);
  border-radius: 6px;
  overflow: hidden;
}

.export-progress-fill {
  height: 100%;
  background: rgb(var(--v-theme-accent));
  width: 0;
  transition: width 0.3s ease;
}

.export-progress-meta {
  margin-top: 6px;
  font-size: 0.8em;
  opacity: 0.85;
}
.face-bbox-overlay span {
  white-space: pre-line;
  word-break: break-word;
  overflow-wrap: anywhere;
  max-width: 90px;
  display: block;
  line-height: 1.1;
  text-overflow: ellipsis;
  overflow: hidden;
  max-height: 1.1em;
}
.grid-scroll-wrapper {
  height: calc(100vh - 60px);
  overflow-y: auto;
  width: 100%;
  padding-right: 0px;
  scrollbar-color: rgb(var(--v-theme-accent)) rgb(var(--v-theme-on-accent));
}
.empty-state {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: auto;
  z-index: 5;
}
.empty-state-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 26px 30px;
  border-radius: 18px;
  border: 1px dashed rgba(0, 0, 0, 0.25);
  background: rgba(255, 255, 255, 0.72);
  color: rgb(var(--v-theme-on-background));
  text-align: center;
  max-width: 420px;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.08);
  pointer-events: auto;
}
.empty-state-illustration {
  color: rgba(0, 0, 0, 0.45);
}
.empty-state-title {
  font-size: 1.2em;
  font-weight: 600;
}
.empty-state-subtitle {
  font-size: 0.95em;
  opacity: 0.8;
}
.empty-state-action {
  margin-top: 6px;
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
  background: rgb(var(--v-theme-accent));
  border-radius: 8px;
}
.grid-scroll-wrapper::-webkit-scrollbar-track {
  background: rgb(var(--v-theme-on-accent));
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
  transition:
    box-shadow 0.2s,
    border 0.2s;
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
  font-size: 1.1em;
  color: rgb(var(--v-theme-on-background));
  text-align: center;
  word-break: break-all;
  text-shadow: 1px 1px 1px rgba(0, 0, 0, 0.2);
}
.thumbnail-container {
  width: 100%;
  height: 100%;
  position: relative;
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
  z-index: 1;
  box-shadow: 2px 2px 4px rgba(0, 0, 0, 0.4);
  transition:
    transform 0.18s cubic-bezier(0.4, 2, 0.6, 1),
    box-shadow 0.18s;
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
.thumbnail-container:hover .thumbnail-img,
.thumbnail-container:focus-within .thumbnail-img {
  box-shadow: none;
  transform: scale(1.02);
  z-index: 2;
  transition:
    transform 0.18s cubic-bezier(0.4, 2, 0.6, 1),
    box-shadow 0.18s;
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

.thumbnail-drag-preview {
  position: fixed;
  width: 160px;
  height: auto;
  opacity: 0.01;
  pointer-events: none;
  left: -9999px;
  top: -9999px;
  object-fit: cover;
  border-radius: 8px;
}

/* Add a button to trigger the search overlay */
.search-button {
  position: fixed;
  bottom: 20px;
  right: 20px;
  background: #007bff;
  color: white;
  border: none;
  border-radius: 50%;
  width: 50px;
  height: 50px;
  display: flex;
  justify-content: center;
  align-items: center;
  cursor: pointer;
  box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
}

.clear-search-btn {
  position: absolute;
  bottom: 16px;
  left: 16px;
  z-index: 1000;
  background-color: red !important; /* Temporary debug styling */
  color: white;
  border: 2px solid black;
}

.search-result-bar {
  position: absolute;
  bottom: 0;
  left: 0;
  width: 100%;
  z-index: 1000;
  background-color: #f5f5f5;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px;
  box-shadow: 0 -2px 4px rgba(0, 0, 0, 0.1);
}
.thumbnail-placeholder {
  width: 100%;
  height: 100%;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1.5em;
  position: absolute;
  top: 0;
  left: 0;
  color: rgb(var(--v-theme-on-background));
}
</style>
