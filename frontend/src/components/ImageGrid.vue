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
          v-for="(img, idx) in gridImagesToRender"
          :key="img.id ? `img-${img.id}` : `placeholder-${img.idx}`"
          class="image-card"
          :draggable="isImageSelected(img.id)"
          @dragstart="onImageDragStart(img, img.idx, $event)"
          @pointerdown="primeImageDragPayload(img)"
          @click="handleImageCardClick(img, img.idx, $event)"
          @mouseenter="handleImageMouseEnter(img)"
          @mouseleave="handleImageMouseLeave(img)"
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
                <img
                  :src="img.thumbnail"
                  class="thumbnail-img"
                  :ref="(el) => setThumbnailRef(img.id, el)"
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
              v-else-if="
                typeof props.selectedSort === 'string' &&
                props.selectedSort.includes('DATE') &&
                img.created_at
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

    <!-- Search Result Bar -->
    <div
      v-if="props.searchQuery && props.searchQuery.length > 0"
      class="search-result-bar"
      style="position: absolute; bottom: 64px; left: 0; width: 100%; z-index: 1000; background-color: #f5f5f5; display: flex; align-items: center; justify-content: space-between; padding: 8px 16px; box-shadow: 0 -2px 4px rgba(0, 0, 0, 0.1);"
    >
      <span>
        Search result found {{ allGridImages.length}} items
      </span>
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

const emit = defineEmits(["open-overlay", "refresh-sidebar", "clear-search"]);

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
  showStars: Boolean,
  showFaceBboxes: Boolean,
  allPicturesId: String,
  unassignedPicturesId: String,
  gridVersion: { type: Number, default: 0 },
  mediaTypeFilter: { type: String, default: "all" },
});
// Store refs for each thumbnail image
const thumbnailRefs = reactive({});
const thumbnailLoadedMap = reactive({});

// Key to force face bbox overlay recompute
const faceOverlayRedrawKey = ref(0);

function triggerFaceOverlayRedraw() {
  faceOverlayRedrawKey.value++;
}

onMounted(() => {
  window.addEventListener("resize", triggerFaceOverlayRedraw);
  console.log(
    "[ImageGrid.vue] Mounted with selectedDescending:",
    props.selectedDescending
  );
  console.log("[ImageGrid.vue] Clear Search button should be visible.");
});
onUnmounted(() => {
  window.removeEventListener("resize", triggerFaceOverlayRedraw);
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

// --- Multi-face selection state ---
const selectedFaceIds = ref([]); // Array of { imageId, faceIdx, faceId }

function isFaceSelected(imageId, faceIdx) {
  return selectedFaceIds.value.some(
    (f) => f.imageId === imageId && f.faceIdx === faceIdx
  );
}

function toggleFaceSelection(imageId, faceIdx, faceId) {
  const idx = selectedFaceIds.value.findIndex(
    (f) => f.imageId === imageId && f.faceIdx === faceIdx
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
  const dragDataStr = JSON.stringify({
    type: "face-bbox",
    faceIds: facesToDrag.map((f) => f.faceId),
    imageIds: Array.from(new Set(facesToDrag.map((f) => f.imageId))),
    faces: facesToDrag,
  });
  console.log("[DRAG] onFaceBboxDragStart dragData:", dragDataStr);
  event.dataTransfer.setData("application/json", dragDataStr);
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
    containerHeight / naturalHeight
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
    for (const face of firstFrameFaces) {
      console.debug("Face bbox:", face.bbox, "Character:", face.character_id);
    }
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
  hoveredImageIdx.value = img.idx;
  prefetchFullImageBlob(img);
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
    d.getHours()
  )}:${pad(d.getMinutes())}`;
}

const EXTENSION_MIME_MAP = {
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  png: "image/png",
  webp: "image/webp",
  gif: "image/gif",
  bmp: "image/bmp",
  tif: "image/tiff",
  tiff: "image/tiff",
  heic: "image/heic",
  heif: "image/heif",
  mp4: "video/mp4",
  mov: "video/quicktime",
};

function inferMimeType(img, filename = "") {
  if (img && img.mime_type) return img.mime_type;
  const name = filename.toLowerCase();
  const ext = name.includes(".") ? name.split(".").pop() : "";
  return (ext && EXTENSION_MIME_MAP[ext]) || "application/octet-stream";
}

function getImageFilename(img) {
  if (!img) return "image";
  return (
    img.original_filename ||
    img.filename ||
    (img.id ? `${img.id}.jpg` : "image")
  );
}

function getImageDownloadUrl(img) {
  if (!img) return "";
  const identifier = img.id || img.filename;
  if (!identifier) return "";
  return `${props.backendUrl}/pictures/${identifier}`;
}

const dragBinaryCache = new Map();
const dragPrefetchPromises = new Map();

function getImageCacheKey(img) {
  if (!img) return null;
  return img.id || img.filename || img.original_filename || null;
}

function dataUrlToBlob(dataUrl, fallbackType = "application/octet-stream") {
  if (!dataUrl || !dataUrl.startsWith("data:")) return null;
  const parts = dataUrl.split(",");
  if (parts.length < 2) return null;
  const header = parts[0];
  const base64Data = parts[1];
  const mimeMatch = header.match(/data:(.*?);base64/);
  const type = mimeMatch ? mimeMatch[1] : fallbackType;
  const byteString = atob(base64Data);
  const len = byteString.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i += 1) {
    bytes[i] = byteString.charCodeAt(i);
  }
  return new Blob([bytes], { type: type || fallbackType });
}

function arrayBufferToBase64(buffer) {
  let binary = "";
  const bytes = new Uint8Array(buffer);
  const chunkSize = 0x8000;
  for (let i = 0; i < bytes.length; i += chunkSize) {
    const subArray = bytes.subarray(i, i + chunkSize);
    binary += String.fromCharCode.apply(null, subArray);
  }
  return btoa(binary);
}

function bufferToDataUrl(buffer, mimeType) {
  const base64 = arrayBufferToBase64(buffer);
  return `data:${mimeType};base64,${base64}`;
}

function createBinaryEntry(buffer, mimeType, cacheKey) {
  const resolvedType = mimeType || "application/octet-stream";
  const blob = new Blob([buffer], { type: resolvedType });
  const dataUrl = bufferToDataUrl(buffer, resolvedType);
  const entry = { blob, dataUrl, mimeType: resolvedType };
  if (cacheKey) {
    dragBinaryCache.set(cacheKey, entry);
  }
  return entry;
}

function getCachedBinaryEntry(img) {
  const key = getImageCacheKey(img);
  if (!key) return null;
  return dragBinaryCache.get(key) || null;
}

function prefetchFullImageBlob(img) {
  const key = getImageCacheKey(img);
  const fileUrl = getImageDownloadUrl(img);
  if (!key || !fileUrl) return;
  if (dragBinaryCache.has(key) || dragPrefetchPromises.has(key)) return;

  const promise = fetch(fileUrl, { cache: "no-store" })
    .then(async (response) => {
      if (!response.ok) {
        throw new Error(`Prefetch failed with status ${response.status}`);
      }
      const buffer = await response.arrayBuffer();
      const headerType = response.headers.get("Content-Type") || inferMimeType(img);
      const entry = createBinaryEntry(buffer, headerType, key);
      dragPrefetchPromises.delete(key);
      return entry;
    })
    .catch((err) => {
      dragPrefetchPromises.delete(key);
      console.warn("[DRAG] Prefetch failed", err);
      throw err;
    });

  dragPrefetchPromises.set(key, promise);
}

function fetchBinarySync(fileUrl, mimeType, cacheKey) {
  if (!fileUrl) return null;
  try {
    const xhr = new XMLHttpRequest();
    xhr.open("GET", fileUrl, false);
    xhr.responseType = "arraybuffer";
    xhr.send();
    if (xhr.status >= 200 && xhr.status < 300) {
      const buffer = xhr.response;
      const type = xhr.getResponseHeader("Content-Type") || mimeType;
      return createBinaryEntry(
        buffer,
        type || "application/octet-stream",
        cacheKey
      );
    }
    console.warn("[DRAG] Synchronous fetch failed", xhr.status, fileUrl);
  } catch (err) {
    console.error("[DRAG] Synchronous fetch error", err);
  }
  return null;
}

function ensureBinaryEntry(img, fileUrl, mimeType) {
  const cacheKey = getImageCacheKey(img);
  let entry = cacheKey ? dragBinaryCache.get(cacheKey) : null;
  if (entry) return entry;

  entry = fetchBinarySync(fileUrl, mimeType, cacheKey);
  if (entry) return entry;

  if (img?.thumbnail) {
    const thumbBlob = dataUrlToBlob(img.thumbnail, mimeType || "image/png");
    if (thumbBlob) {
      entry = { blob: thumbBlob, dataUrl: img.thumbnail, mimeType: thumbBlob.type };
      if (cacheKey) dragBinaryCache.set(cacheKey, entry);
    }
  }
  return entry || null;
}

function attachFullFileToDrag(event, img, fileUrl, filename, mimeType, entry) {
  if (!event?.dataTransfer?.items) return null;

  const resolvedEntry = entry || ensureBinaryEntry(img, fileUrl, mimeType);
  if (!resolvedEntry) {
    console.warn("[DRAG] No binary data available for drag payload");
    return null;
  }

  try {
    const resolvedType =
      resolvedEntry.mimeType || mimeType || resolvedEntry.blob.type || "application/octet-stream";
    const file = new File([resolvedEntry.blob], filename, { type: resolvedType });
    event.dataTransfer.items.add(file);
    console.debug("[DRAG] Added binary payload to dataTransfer.items", {
      name: filename,
      type: resolvedType,
      size: file.size,
    });
    return resolvedEntry;
  } catch (err) {
    console.error("[DRAG] Unable to attach binary file to drag payload", err);
    return resolvedEntry;
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

function primeImageDragPayload(img) {
  prefetchFullImageBlob(img);
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
    fetch(`${backendUrl}/characters/${props.selectedCharacter}/faces`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ picture_ids: selectedImageIds.value }),
    })
      .then((res) => {
        if (!res.ok) throw new Error(`Failed to remove images from character`);
      })
      .catch((err) => {
        alert(`Error removing images from character: ${err.message}`);
      })
      .finally(() => {
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
        fetch(`${backendUrl}/picture_sets/${props.selectedSet}/members/${id}`, {
          method: "DELETE",
        })
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
  loadedRanges.value = [];
  allGridImages.value = [];
  selectedImageIds.value = [];
  lastSelectedIndex = null;
  await fetchTotalImageCount();
  updateVisibleThumbnails();
}

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
  console.log(
    "Updating selected group name: ",
    props.selectedCharacter,
    props.selectedSet,
    props.allPicturesId
  );
  if (
    props.selectedCharacter &&
    props.selectedCharacter !== `${props.allPicturesId}` &&
    props.selectedCharacter !== `${props.unassignedPicturesId}`
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
    props.selectedSet !== `${props.allPicturesId}` &&
    props.selectedSet !== `${props.unassignedPicturesId}`
  ) {
    try {
      const res = await fetch(
        `${props.backendUrl}/picture_sets/${props.selectedSet}`
      );
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
    const res = await fetch(`${props.backendUrl}/pictures/${imageId}/metadata`);
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
    console.debug(
      "PATCH /pictures/",
      imageId,
      " body: { score:",
      newScore,
      "}"
    );
    const res = await fetch(`${props.backendUrl}/pictures/${imageId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ score: newScore }),
    });
    if (!res.ok) throw new Error(`Failed to set score for image ${imageId}`);

    // Update score in allGridImages
    const gridImg = allGridImages.value.find((i) => i.id === imageId);
    if (gridImg) {
      gridImg.score = newScore;
    }
    // Update score in images array if present
    const idx = loadedRanges.value.findIndex((i) => i.id === imageId);
    if (idx !== -1) {
      loadedRanges.value[idx].score = newScore;
    }
    // Update overlay image if open and matches
    if (
      overlayOpen.value &&
      overlayImage.value &&
      overlayImage.value.id === imageId
    ) {
      overlayImage.value = { ...overlayImage.value, score: newScore };
    }

    // If sorting by score, re-sort
    if (
      props.selectedSort.value === "score_desc" ||
      props.selectedSort.value === "score_asc"
    ) {
      // Resort images array
      loadedRanges.value.sort((a, b) => {
        const sa = a.score || 0;
        const sb = b.score || 0;
        return props.selectedSort.value === "score_desc" ? sb - sa : sa - sb;
      });
      nextTick(() => {
        const grid = gridContainer.value;
        if (!grid) return;
        const newIdx = loadedRanges.value.findIndex((i) => i.id === imageId);
        const card = grid.querySelectorAll(".image-card")[newIdx];
        if (card && card.scrollIntoView) {
          card.scrollIntoView({ behavior: "smooth", block: "center" });
        }
      });
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
  const standardTypesToInspect = [
    "text/uri-list",
    "text/html",
    "text/plain",
  ];
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

  // Ignore drag-and-drop if the source is the grid itself
  if (dragSource.value === "grid" || e.dataTransfer.types.includes("application/json")) {
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
const imagesLoading = ref(false);
const imagesError = ref(null);

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
      props.selectedDescending
    );
    params.append("descending", props.selectedDescending ? "true" : "false");
  } else {
    console.warn(
      "[ImageGrid.vue] selectedDescending is not boolean, skipping param. Type:",
      typeof props.selectedDescending
    );
  }
  // Add format filter for backend media type filtering
  if (props.mediaTypeFilter === "images") {
    console.log(
      "[ImageGrid.vue] Building query params for image formats only", PIL_IMAGE_EXTENSIONS
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

// Fetch total image count for current filters
async function fetchTotalImageCount() {
  imagesLoading.value = true;
  imagesError.value = null;
  try {
    let images = [];
    // If a set is selected, use /picture_sets/{id}
    if (
      props.selectedSet &&
      props.selectedSet !== props.allPicturesId &&
      props.selectedSet !== props.unassignedPicturesId
    ) {
      const url = `${props.backendUrl}/picture_sets/${props.selectedSet}`;
      const res = await fetch(url);
      if (!res.ok) throw new Error("Failed to fetch images for set");
      const data = await res.json();
      images = data.pictures || [];
    } else if (props.searchQuery && props.searchQuery.trim()) {
      // Use /pictures/search endpoint for text search
      const params = buildPictureIdsQueryParams();
      const url = `${
        props.backendUrl
      }/pictures/search?query=${encodeURIComponent(
        props.searchQuery.trim()
      )}&top_n=10000${params ? `&${params}` : ""}`;
      const res = await fetch(url);
      if (!res.ok) throw new Error("Failed to fetch search results");
      images = await res.json();
    } else {
      const params = buildPictureIdsQueryParams();
      // Only use allowed parameters: sort, offset, limit, threshold
      const url = `${props.backendUrl}/pictures?offset=0&limit=10000${
        params ? `&${params}` : ""
      }`;
      const res = await fetch(url);
      if (!res.ok) throw new Error("Failed to fetch image info for all images");
      images = await res.json();
    }
    allGridImages.value = images.map((img, i) => ({
      ...img,
      idx: i,
      thumbnail: null,
    }));
  } catch (e) {
    imagesError.value = e.message;
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

watch([() => props.mediaTypeFilter], () => {
  // Reset loaded ranges, thumbnails, pagination, and fetch new count/images for filter
  loadedRanges.value = [];
  selectedImageIds.value = [];
  lastSelectedIndex = null;
  visibleStart.value = 0;
  visibleEnd.value = 0;
  allGridImages.value = [];
  fetchTotalImageCount().then(() => {
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

watch(allGridImages, (newVal, oldVal) => {
  console.log("allGridImages updated:", newVal);
});

const gridImagesToRender = computed(() => {
  if (!allGridImages.value) {
    console.warn("allGridImages is undefined");
    return [];
  }

  // Only render a window of placeholders/images for performance
  if (allGridImages.value.length < allGridImages.value.length) {
    for (
      let i = allGridImages.value.length;
      i < allGridImages.value.length;
      i++
    ) {
      allGridImages.value[i] = { id: null, thumbnail: null, idx: i };
    }
  }

  let filtered = allGridImages.value;
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
  return filtered.slice(renderStart.value, renderEnd.value);
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
        // Only use allowed parameters: sort, offset, limit, threshold
        const url = `${props.backendUrl}/pictures?offset=${start}&limit=${
          end - start
        }${params ? `&${params}` : ""}`;
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
    if (ids.length) {
      const thumbRes = await fetch(`${props.backendUrl}/pictures/thumbnails`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids }),
      });
      if (thumbRes.ok) {
        const thumbData = await thumbRes.json();
        for (const gridImg of gridImages) {
          const thumbObj = thumbData[gridImg.id];
          gridImg.thumbnail =
            thumbObj && thumbObj.thumbnail
              ? `data:image/png;base64,${thumbObj.thumbnail}`
              : null;
          gridImg.faces =
            thumbObj && Array.isArray(thumbObj.faces) ? thumbObj.faces : [];
        }
      } else {
        for (const gridImg of gridImages) {
          gridImg.thumbnail = null;
          gridImg.faces = [];
        }
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
    allGridImages.value.length,
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
      allGridImages.value.length
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
  if (!event || !event.dataTransfer || !img) return;

  const fileUrl = getImageDownloadUrl(img);
  if (!fileUrl) {
    console.warn("[DRAG] Unable to resolve file URL for image", img);
    return;
  }

  dragSource.value = "grid";

  const filename = getImageFilename(img);
  const mimeType = inferMimeType(img, filename);
  const binaryEntry = ensureBinaryEntry(img, fileUrl, mimeType);

  try {
    event.dataTransfer.clearData();
  } catch (err) {
    console.debug("[DRAG] clearData not supported:", err);
  }

  event.dataTransfer.effectAllowed = "copy";

  const dragImg = event.currentTarget?.querySelector(".thumbnail-img");
  if (dragImg) {
    const rect = dragImg.getBoundingClientRect();
    event.dataTransfer.setDragImage(dragImg, rect.width / 2, rect.height / 2);
  }

  console.debug(
    "[DRAG] dataTransfer types after setData",
    Array.from(event.dataTransfer.types || [])
  );
  const payload = attachFullFileToDrag(
    event,
    img,
    fileUrl,
    filename,
    mimeType,
    binaryEntry
  );
  if (!payload) {
    console.warn("[DRAG] Unable to attach binary payload, aborting drag");
    event.preventDefault();
    return;
  }
  console.debug(
    "[DRAG] dataTransfer items after file attach",
    event.dataTransfer.items
      ? Array.from(event.dataTransfer.items).map((item) => ({
          kind: item.kind,
          type: item.type,
        }))
      : []
  );

  console.debug("[DRAG] Payload prepared", {
    fileUrl,
    filename,
    mimeType,
    downloadDescriptor,
  });
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
    (img) => !imageIds.includes(img.id)
  );
  selectedImageIds.value = selectedImageIds.value.filter(
    (id) => !imageIds.includes(id)
  );
  updateVisibleThumbnails();
}

// --- Export to Zip ---
async function exportCurrentViewToZip() {
  // Build query params for current view
  let url = `${props.backendUrl}/pictures/export`;
  const params = buildPictureIdsQueryParams();
  if (params) {
    url += `?${params}`;
  }
  try {
    const res = await fetch(url, { method: "GET" });
    if (!res.ok) throw new Error("Failed to export zip");
    const blob = await res.blob();
    // Extract filename from Content-Disposition header
    let filename = "pixlvault_export.zip";
    const disposition = res.headers.get("Content-Disposition");
    if (disposition) {
      const match = disposition.match(/filename=\"?([^\";]+)\"?/);
      if (match) filename = match[1];
    }
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = filename;
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

// Search functionality
const searchQuery = ref(props.searchQuery);
const { visible, openSearchOverlay, closeSearchOverlay } = useSearchOverlay();

function handleSearch(query) {
  console.log("Search triggered with query:", query);
  searchQuery.value = query;
  props.searchQuery = query;
  fetchTotalImageCount(); // Refresh the grid based on the new search query
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
  font-size: 1em;
  color: #222;
  text-align: center;
  word-break: break-all;
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
</style>
