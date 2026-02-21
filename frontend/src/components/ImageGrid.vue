<template>
  <ImageOverlay
    :open="overlayOpen"
    :initialImageId="overlayImageId"
    :allImages="allGridImages"
    :backendUrl="props.backendUrl"
    :tagUpdate="props.wsTagUpdate"
    :hiddenTags="props.hiddenTags"
    :applyTagFilter="props.applyTagFilter"
    :dateFormat="props.dateFormat"
    @close="closeOverlay"
    @apply-score="applyScore"
    @add-tag="addTagToImage"
    @remove-tag="removeTagFromImage"
    @update-description="updateDescriptionForImage"
    @overlay-change="handleOverlayChange"
    @added-to-set="handleOverlayAddedToSet"
  />
  <ImageImporter
    ref="imageImporterRef"
    :backendUrl="props.backendUrl"
    :selectedCharacterId="props.selectedCharacter"
    :allPicturesId="props.allPicturesId"
    :unassignedPicturesId="props.unassignedPicturesId"
    @import-finished="handleImagesUploaded"
  />
  <div :style="wrapperStyle">
    <SelectionBar
      v-if="showSelectionBar"
      :selectedCount="selectedImageIds.length"
      :selectedFaceCount="selectedFaceIds.length"
      :selectedCharacter="String(props.selectedCharacter)"
      :selectedSet="String(props.selectedSet)"
      :selectedGroupName="selectedGroupName"
      :allPicturesId="String(props.allPicturesId)"
      :unassignedPicturesId="String(props.unassignedPicturesId)"
      :scrapheapPicturesId="String(props.scrapheapPicturesId)"
      :backend-url="props.backendUrl"
      :selected-image-ids="selectedImageIds"
      :visible="showSelectionBar"
      @clear-selection="clearSelection"
      @refresh-tags="refreshTagsForSelection"
      @added-to-set="handleSelectionAddedToSet"
      @remove-from-group="removeFromGroup"
      @delete-selected="deleteSelected"
      @add-to-character="handleAddToCharacter"
    />
    <EmptyScrapHeap
      v-if="showScrapheapBar"
      :visible="showScrapheapBar"
      :disabled="scrapheapEmptyDisabled"
      :restoreDisabled="scrapheapRestoreDisabled"
      @empty-scrapheap="confirmEmptyScrapheap"
      @restore-scrapheap="confirmRestoreScrapheap"
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
      <button
        v-if="
          exportProgress.status !== 'completed' &&
          exportProgress.status !== 'failed' &&
          exportProgress.status !== 'cancelled'
        "
        class="export-progress-abort"
        type="button"
        @click="abortExportZip"
      >
        Abort
      </button>
    </div>

    <div
      class="grid-scroll-wrapper"
      ref="scrollWrapper"
      @scroll="onGridScroll"
      :style="scrollWrapperStyle"
    >
      <!-- Drag overlay (visible viewport of grid) -->
      <div v-if="dragOverlayVisible" class="drag-overlay">
        <div class="drag-overlay-message">{{ dragOverlayMessage }}</div>
      </div>
      <div v-if="showEmptyState" class="empty-state">
        <div class="empty-state-card">
          <div class="empty-state-illustration" aria-hidden="true">
            <img
              :src="emptyStateImage"
              :alt="emptyStateAlt"
              style="width: 90%"
            />
          </div>
          <div class="empty-state-title">
            {{ emptyStateTitle }}
          </div>
          <div class="empty-state-subtitle">
            {{ emptyStateSubtitle }}
          </div>
          <v-btn
            v-if="canShowAllPicturesButton"
            class="empty-state-action app-btn-base"
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
          gridTemplateColumns: `repeat(${props.columns}, minmax(${MIN_THUMBNAIL_SIZE}px, ${MAX_THUMBNAIL_SIZE}px))`,
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
        <div
          v-for="(img, idx) in gridImagesToRender"
          :key="img.id ? `img-${img.id}-${img.idx}` : `placeholder-${img.idx}`"
          :style="getStackCardStyle(img)"
          class="image-card"
          @click="handleImageCardClick(img, img.idx, $event)"
          @mouseenter="handleImageMouseEnter(img)"
          @mouseleave="handleImageMouseLeave(img)"
        >
          <v-card
            :class="[
              'thumbnail-card',
              { 'thumbnail-card-new': isImageRecentlyAdded(img.id) },
            ]"
            @click.stop="handleThumbnailClick(img, img.idx, $event)"
          >
            <div
              class="thumbnail-container"
              :ref="(el) => setThumbnailContainerRef(img.id, el)"
              draggable="true"
              @dragstart.capture="handleContainerDragStart(img, $event)"
              @dragend.capture="handleContainerDragEnd(img, $event)"
            >
              <div
                v-if="
                  props.showProblemIcon &&
                  hasPenalisedTags(img) &&
                  isThumbnailReady(img.id) &&
                  img.thumbnail
                "
                class="penalised-tag-indicator thumbnail-badge thumbnail-badge--top-left"
                :title="penalisedTagsTitle(img)"
              >
                <v-icon size="18" color="error"
                  >mdi-emoticon-sad-outline</v-icon
                >
              </div>
              <!-- Resolution overlay -->
              <div
                v-if="
                  props.showResolution &&
                  img.width &&
                  img.height &&
                  isThumbnailReady(img.id) &&
                  img.thumbnail
                "
                class="resolution-hover-overlay thumbnail-badge thumbnail-badge--bottom-right"
              >
                {{ img.width }}×{{ img.height }}
              </div>
              <template v-if="img.thumbnail && isVideo(img)">
                <video
                  class="thumbnail-img"
                  :src="
                    buildMediaUrl({ backendUrl: props.backendUrl, image: img })
                  "
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
                <template v-if="isThumbnailReady(img.id) && img.thumbnail">
                  <div
                    v-for="overlay in getFaceBboxOverlays(img).value"
                    :key="
                      overlay.faceId +
                      '-' +
                      img.id +
                      '-' +
                      (img.thumbnail ? 1 : 0)
                    "
                    class="face-bbox-overlay"
                    :style="overlay.style"
                    draggable="true"
                    @pointerdown.stop
                    @mousedown.stop
                    @click.stop="
                      toggleFaceSelection(
                        img.id,
                        overlay.faceIdx,
                        overlay.faceId,
                      )
                    "
                    @dragstart="
                      (e) => {
                        e.stopPropagation();
                        onFaceBboxDragStart(
                          e,
                          img,
                          overlay.faceIdx,
                          overlay.faceId,
                        );
                      }
                    "
                  >
                    <div
                      :style="{ color: overlay.color }"
                      class="face-bbox-label"
                    >
                      {{ overlay.face.character_name }}
                    </div>
                  </div>
                </template>
                <div
                  v-if="
                    props.showFormat &&
                    img.format &&
                    img.format !== 'unknown' &&
                    isThumbnailReady(img.id) &&
                    img.thumbnail
                  "
                  class="thumbnail-id-overlay thumbnail-badge thumbnail-badge--bottom-left"
                >
                  {{ img.format.toUpperCase() }}
                </div>
                <template
                  v-if="
                    props.showHandBboxes &&
                    isThumbnailReady(img.id) &&
                    img.thumbnail
                  "
                >
                  <div
                    v-for="overlay in getHandBboxOverlays(img).value"
                    :key="
                      overlay.handKey +
                      '-' +
                      img.id +
                      '-' +
                      (img.thumbnail ? 1 : 0)
                    "
                    class="hand-bbox-overlay"
                    :style="overlay.style"
                  ></div>
                </template>
              </template>
              <template v-else>
                <div class="thumbnail-placeholder">
                  <v-icon class="thumbnail-placeholder-icon"
                    >mdi-loading</v-icon
                  >
                </div>
              </template>
              <!-- Score overlay -->
              <StarRatingOverlay
                v-if="
                  props.showStars && isThumbnailReady(img.id) && img.thumbnail
                "
                class="thumbnail-badge thumbnail-badge--top-right"
                :score="img.score || 0"
                :icon-size="16"
                :compact="true"
                @set-score="setScore(img, $event)"
              />
            </div>
          </v-card>
          <div v-if="isImageSelected(img.id)" class="selection-overlay"></div>
          <!-- Info row absolutely positioned below thumbnail -->
          <div class="thumbnail-info-row">
            <div
              v-for="info in getThumbnailInfoItems(img)"
              :key="`${info.key}-${img.id}`"
              class="thumbnail-info"
              :ref="
                (el) => setThumbnailInfoRef(img.id, info.key, info.text, el)
              "
              :title="getThumbnailInfoTitle(img.id, info.key)"
              @mouseenter="handleThumbnailInfoMouseEnter(img.id, info.key)"
            >
              {{ getThumbnailInfoDisplayText(img.id, info.key, info.text) }}
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
    <SearchResultBar
      v-if="props.searchQuery && props.searchQuery.length > 0"
      :images-loading="imagesLoading"
      :count="allGridImages.length"
      :category-label="props.activeCategoryLabel"
      :is-all-pictures-active="props.isAllPicturesActive"
      @search-all="emit('search-all')"
      @clear="clearSearchQuery"
    />
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
  isSupportedImportFile,
  isSupportedImageFile,
  isSupportedVideoFile,
  MediaFormat,
  PictureId,
  buildMediaUrl,
  PIL_IMAGE_EXTENSIONS,
  VIDEO_EXTENSIONS,
} from "../utils/media.js";
import ImageImporter from "./ImageImporter.vue";
import ImageOverlay from "./ImageOverlay.vue";
import EmptyScrapHeap from "./EmptyScrapHeap.vue";
import SelectionBar from "./SelectionBar.vue";
import SearchResultBar from "./SearchResultBar.vue";
import StarRatingOverlay from "./StarRatingOverlay.vue";
import { apiClient } from "../utils/apiClient";
import {
  faceBoxColor,
  formatUserDate,
  getStackColor,
  handBoxColor,
  StackThreshold,
  toggleScore,
} from "../utils/utils.js";
import { dedupeTagList, getTagId, TagList, tagMatches } from "../utils/tags.js";
import { debounce } from "lodash-es";

const emit = defineEmits([
  "open-overlay",
  "refresh-sidebar",
  "clear-search",
  "reset-to-all",
  "search-all",
  "update:selected-sort",
]);

// Props
const props = defineProps({
  thumbnailSize: Number,
  sidebarVisible: Boolean,
  backendUrl: String,
  selectedCharacter: { type: [String, Number, null], default: null },
  selectedReferenceCharacter: { type: [String, Number, null], default: null },
  selectedSet: { type: [Number, String, null], default: null },
  searchQuery: String,
  activeCategoryLabel: { type: String, default: "Category" },
  isAllPicturesActive: { type: Boolean, default: false },
  selectedSort: String,
  selectedDescending: Boolean,
  similarityCharacter: { type: [String, Number, null], default: null },
  stackThreshold: { type: [String, Number, null], default: null },
  showStars: Boolean,
  showFaceBboxes: Boolean,
  showHandBboxes: Boolean,
  showFormat: Boolean,
  showResolution: Boolean,
  showProblemIcon: Boolean,
  dateFormat: { type: String, default: "locale" },
  allPicturesId: String,
  unassignedPicturesId: String,
  scrapheapPicturesId: String,
  gridVersion: { type: Number, default: 0 },
  wsUpdateKey: { type: Number, default: 0 },
  wsTagUpdate: {
    type: Object,
    default: () => ({ key: 0, pictureIds: [] }),
  },
  mediaTypeFilter: { type: String, default: "all" },
  columns: { type: Number, required: true },
  hiddenTags: { type: Array, default: () => [] },
  applyTagFilter: { type: Boolean, default: false },
});
const STACKS_SORT_KEY = "PICTURE_STACKS";
const STACK_COLOR_STEP = 47;
const MIN_THUMBNAIL_SIZE = 128;
const MAX_THUMBNAIL_SIZE = 384;
const THUMBNAIL_INFO_ROW_HEIGHT = 24;
// Store refs for each thumbnail image (non-reactive to avoid render feedback loops)
const thumbnailRefs = {};
const thumbnailContainerRefs = {};
const dragPreviewRefs = {};
const thumbnailInfoRefs = {};
const thumbnailInfoTitleMap = reactive({});
const thumbnailInfoDisplayMap = reactive({});
const thumbnailInfoFullMap = reactive({});
const textMeasureCanvas =
  typeof document !== "undefined" ? document.createElement("canvas") : null;
const textMeasureContext = textMeasureCanvas
  ? textMeasureCanvas.getContext("2d")
  : null;
const thumbnailLoadedMap = reactive({});
const thumbnailReadyMap = reactive({});
const THUMBNAIL_RETRY_DELAY_MS = 10000;
const THUMBNAIL_RETRY_LIMIT = 1;
const thumbnailRetryTimers = new Map();
const thumbnailRetryCounts = reactive({});
const PREFETCHED_FULL_IMAGE_LIMIT = 12;
const fullImagePrefetchControllers = new Map();
const prefetchedFullImageIds = new Set();
const prefetchedFullImageOrder = [];

const exportProgress = reactive({
  visible: false,
  status: "idle",
  processed: 0,
  total: 0,
  message: "",
  cancelRequested: false,
});

const exportProgressPercent = computed(() => {
  if (!exportProgress.total) return 0;
  const percent = (exportProgress.processed / exportProgress.total) * 100;
  return Math.min(100, Math.max(0, Math.round(percent)));
});

const recentlyAddedIds = ref({});
const recentlyAddedTimers = new Map();
const previousImageIds = new Set();
const hasLoadedOnce = ref(false);
const highlightNextFetch = ref(false);
const lastWsUpdateKey = ref(0);
const lastWsTagUpdateKey = ref(0);
const preserveScrollOnNextFetch = ref(false);
const pendingScrollTop = ref(null);
const skipNextWsRefresh = ref(false);

// Key to force face bbox overlay recompute.
const faceOverlayRedrawKey = ref(0);
let gridResizeObserver = null;

function triggerFaceOverlayRedraw() {
  faceOverlayRedrawKey.value++;
}

function buildThumbnailInfoKey(imageId, infoKey) {
  return `${imageId}-${infoKey}`;
}

function getInfoFont(el) {
  if (typeof window === "undefined" || !el) return null;
  const style = window.getComputedStyle(el);
  return `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
}

function measureTextWidth(text, el) {
  if (!textMeasureContext || !el) return 0;
  const font = getInfoFont(el);
  if (font) {
    textMeasureContext.font = font;
  }
  return textMeasureContext.measureText(text).width;
}

function truncateTextToFit(fullText, el) {
  if (!fullText || !el) return "";
  const maxWidth = el.clientWidth || 0;
  if (!maxWidth) return fullText;
  if (measureTextWidth(fullText, el) <= maxWidth) return fullText;
  const words = fullText.split(/\s+/).filter(Boolean);
  if (!words.length) return "";
  let current = "";
  for (const word of words) {
    const next = current ? `${current} ${word}` : word;
    if (measureTextWidth(next, el) <= maxWidth) {
      current = next;
    } else {
      break;
    }
  }
  return current || words[0] || "";
}

function updateThumbnailInfoDisplay(key, fullText, el) {
  if (!el) return;
  const truncated = truncateTextToFit(fullText, el);
  if (truncated && truncated !== fullText) {
    thumbnailInfoDisplayMap[key] = truncated;
    thumbnailInfoTitleMap[key] = fullText;
  } else {
    thumbnailInfoDisplayMap[key] = fullText || "";
    if (thumbnailInfoTitleMap[key]) {
      delete thumbnailInfoTitleMap[key];
    }
  }
}

function setThumbnailInfoRef(imageId, infoKey, fullText, el) {
  const key = buildThumbnailInfoKey(imageId, infoKey);
  if (el) {
    thumbnailInfoRefs[key] = el;
    thumbnailInfoFullMap[key] = fullText || "";
    updateThumbnailInfoDisplay(key, fullText || "", el);
  } else {
    delete thumbnailInfoRefs[key];
    delete thumbnailInfoFullMap[key];
    delete thumbnailInfoDisplayMap[key];
    if (thumbnailInfoTitleMap[key]) {
      delete thumbnailInfoTitleMap[key];
    }
  }
}

function getThumbnailInfoTitle(imageId, infoKey) {
  const key = buildThumbnailInfoKey(imageId, infoKey);
  return thumbnailInfoTitleMap[key] || "";
}

function getThumbnailInfoDisplayText(imageId, infoKey, fallbackText) {
  const key = buildThumbnailInfoKey(imageId, infoKey);
  return thumbnailInfoDisplayMap[key] ?? fallbackText ?? "";
}

function handleThumbnailInfoMouseEnter(imageId, infoKey) {
  const key = buildThumbnailInfoKey(imageId, infoKey);
  const el = thumbnailInfoRefs[key];
  if (!el) return;
  updateThumbnailInfoDisplay(key, thumbnailInfoFullMap[key] || "", el);
}

function refreshAllThumbnailInfoDisplays() {
  for (const key of Object.keys(thumbnailInfoRefs)) {
    const el = thumbnailInfoRefs[key];
    const fullText = thumbnailInfoFullMap[key];
    if (!el || fullText == null) continue;
    updateThumbnailInfoDisplay(key, fullText, el);
  }
}

let initialFetchTimer = null;

onMounted(() => {
  window.addEventListener("resize", triggerFaceOverlayRedraw);
  fetchAllPicturesCount();
  const mountFetchKey = buildGridFetchKey();
  if (!hasLoadedOnce.value && !imagesLoading.value) {
    if (
      !Array.isArray(allGridImages.value) ||
      allGridImages.value.length === 0
    ) {
      if (initialFetchTimer) {
        clearTimeout(initialFetchTimer);
      }
      initialFetchTimer = setTimeout(() => {
        initialFetchTimer = null;
        const currentKey = buildGridFetchKey();
        if (currentKey !== mountFetchKey) {
          return;
        }
        if (hasLoadedOnce.value || imagesLoading.value) {
          return;
        }
        if (
          !Array.isArray(allGridImages.value) ||
          allGridImages.value.length === 0
        ) {
          fetchAllGridImages().then(() => {
            updateVisibleThumbnails();
          });
        }
      }, 80);
    }
  }
  nextTick(() => {
    updateRowHeightFromGrid();
    if (typeof ResizeObserver !== "undefined" && gridContainer.value) {
      gridResizeObserver = new ResizeObserver(() => {
        updateRowHeightFromGrid();
      });
      gridResizeObserver.observe(gridContainer.value);
    }
  });
});

onUnmounted(() => {
  window.removeEventListener("resize", triggerFaceOverlayRedraw);
  if (gridResizeObserver) {
    gridResizeObserver.disconnect();
    gridResizeObserver = null;
  }
  if (initialFetchTimer) {
    clearTimeout(initialFetchTimer);
    initialFetchTimer = null;
  }
  fullImagePrefetchControllers.clear();
  prefetchedFullImageIds.clear();
  prefetchedFullImageOrder.length = 0;
  if (emptyStateDelayTimer) {
    clearTimeout(emptyStateDelayTimer);
    emptyStateDelayTimer = null;
  }
  for (const timer of recentlyAddedTimers.values()) {
    clearTimeout(timer);
  }
  recentlyAddedTimers.clear();
  recentlyAddedIds.value = {};
});

watch(
  () => props.wsUpdateKey,
  (nextKey) => {
    if (!nextKey || nextKey === lastWsUpdateKey.value) return;
    lastWsUpdateKey.value = nextKey;
    const scrollTop = scrollWrapper.value?.scrollTop ?? 0;
    const threshold = rowHeight.value * 0.5;
    if (scrollTop > threshold) {
      skipNextWsRefresh.value = true;
      preserveScrollOnNextFetch.value = false;
      return;
    }
    highlightNextFetch.value = true;
    preserveScrollOnNextFetch.value = true;
  },
);

watch(
  () => props.wsTagUpdate,
  (payload) => {
    if (!payload || typeof payload !== "object") return;
    const nextKey = payload.key || 0;
    if (!nextKey || nextKey === lastWsTagUpdateKey.value) return;
    lastWsTagUpdateKey.value = nextKey;
    const pictureIds = Array.isArray(payload.pictureIds)
      ? payload.pictureIds
      : [];
    const dPayloadIds = pictureIds
      .map((id) => PictureId(id))
      .filter((id) => id != null);
    for (const id of dPayloadIds) {
      refreshGridImage(id);
    }
  },
);

function triggerNewImageHighlight(ids) {
  ids.forEach((id) => {
    if (!id) return;
    if (recentlyAddedTimers.has(id)) {
      clearTimeout(recentlyAddedTimers.get(id));
      recentlyAddedTimers.delete(id);
    }
    recentlyAddedIds.value[id] = true;
    const timeout = setTimeout(() => {
      recentlyAddedIds.value[id] = false;
      recentlyAddedTimers.delete(id);
    }, 2200);
    recentlyAddedTimers.set(id, timeout);
  });
}

function isImageRecentlyAdded(id) {
  return Boolean(id && recentlyAddedIds.value[id]);
}

function onThumbnailLoad(id) {
  thumbnailLoadedMap[id] = (thumbnailLoadedMap[id] || 0) + 1;
  clearThumbnailRetry(id);
}

function clearThumbnailRetry(id) {
  if (!id) return;
  const timer = thumbnailRetryTimers.get(id);
  if (timer) {
    clearTimeout(timer);
  }
  thumbnailRetryTimers.delete(id);
}

function scheduleThumbnailRetry(id, index, requestEpoch) {
  if (!id || index == null) return;
  if ((thumbnailRetryCounts[id] || 0) >= THUMBNAIL_RETRY_LIMIT) return;
  if (thumbnailRetryTimers.has(id)) return;
  const timer = setTimeout(() => {
    thumbnailRetryTimers.delete(id);
    if (requestEpoch !== thumbnailRequestEpoch.value) return;
    const current = allGridImages.value[index];
    if (!current || current.id !== id) return;
    if (current.thumbnail) return;
    thumbnailRetryCounts[id] = (thumbnailRetryCounts[id] || 0) + 1;
    invalidateThumbnailIndex(index);
    fetchThumbnailsBatch(index, index + 1);
  }, THUMBNAIL_RETRY_DELAY_MS);
  thumbnailRetryTimers.set(id, timer);
}

function setThumbnailRef(id, el) {
  if (el) {
    thumbnailRefs[id] = el;
    if (!thumbnailReadyMap[id]) {
      thumbnailReadyMap[id] = true;
    }
  } else {
    delete thumbnailRefs[id];
    if (thumbnailReadyMap[id]) {
      delete thumbnailReadyMap[id];
    }
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

function isThumbnailReady(id) {
  return Boolean(id && thumbnailReadyMap[id]);
}

function getThumbnailLoadedKey(id) {
  return thumbnailLoadedMap[id] || 0;
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

function onFaceBboxDragStart(event, img, faceIdx, faceId) {
  // If this face is selected, drag all selected faces; else, drag just this one
  let facesToDrag = [];
  if (isFaceSelected(img.id, faceIdx) && selectedFaceIds.value.length > 0) {
    facesToDrag = selectedFaceIds.value.map((f) => ({
      imageId: f.imageId,
      faceIdx: f.faceIdx,
      faceId: f.faceId,
    }));
  } else {
    const resolvedFaceId = faceId ?? (img.faces && img.faces[faceIdx]?.id);
    if (!resolvedFaceId) {
      return;
    }
    facesToDrag = [{ imageId: img.id, faceIdx, faceId: resolvedFaceId }];
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
function getFaceBboxStyle(bbox, idx, img, el, isSelected) {
  if (!el) return { display: "none" };
  const container = el.parentElement;
  if (!container) return { display: "none" };
  const containerWidth = container.clientWidth;
  const containerHeight = container.clientHeight;
  const naturalWidth = img.thumbnail_width || img.width || 1;
  const naturalHeight = img.thumbnail_height || img.height || 1;
  // Calculate scale and offset for object-fit: cover
  const scale = Math.max(
    containerWidth / naturalWidth,
    containerHeight / naturalHeight,
  );
  const displayWidth = naturalWidth * scale;
  const offsetX = (containerWidth - displayWidth) / 2;
  const offsetY = 0;
  // Transform bbox
  const left = offsetX + bbox[0] * scale;
  const top = offsetY + bbox[1] * scale;
  const width = (bbox[2] - bbox[0]) * scale;
  const height = (bbox[3] - bbox[1]) * scale;
  const borderColor = faceBoxColor(idx);
  return {
    position: "absolute",
    border: `${isSelected ? 3 : 1.5}px solid ${borderColor}`,
    background: `${borderColor}${isSelected ? "44" : "22"}`,
    "--face-frame-color": `${borderColor}${isSelected ? "cc" : "aa"}`,
    left: `${left}px`,
    top: `${top}px`,
    width: `${width}px`,
    height: `${height}px`,
    pointerEvents: "auto",
    zIndex: isSelected ? 60 : 40,
    display: "block",
  };
}

function getFaceBboxOverlays(img) {
  return computed(() => {
    void faceOverlayRedrawKey.value; // depend on redraw key
    void selectedFaceIds.value;
    void thumbnailReadyMap[img.id];
    if (
      !props.showFaceBboxes ||
      !img.faces ||
      !img.faces.length ||
      !(img.thumbnail_width || img.width) ||
      !(img.thumbnail_height || img.height)
    ) {
      return [];
    }
    const el = thumbnailRefs[img.id];
    if (!el) return [];
    const firstFrameFaces = img.faces
      .map((face, faceIdx) => ({ face, faceIdx }))
      .filter((entry) => entry.face.frame_index === 0);
    return firstFrameFaces.map((entry, colorIdx) => ({
      style: getFaceBboxStyle(
        entry.face.bbox,
        colorIdx,
        img,
        el,
        isFaceSelected(img.id, entry.faceIdx),
      ),
      faceIdx: entry.faceIdx,
      faceId: entry.face.id,
      face: entry.face,
      colorIdx,
    }));
  });
}

function getHandBboxStyle(bbox, idx, img, containerEl) {
  if (!bbox || bbox.length !== 4 || !containerEl) return null;
  const containerWidth = containerEl.clientWidth;
  const containerHeight = containerEl.clientHeight;
  const naturalWidth = img.thumbnail_width || img.width || 1;
  const naturalHeight = img.thumbnail_height || img.height || 1;
  const scale = Math.max(
    containerWidth / naturalWidth,
    containerHeight / naturalHeight,
  );
  const displayWidth = naturalWidth * scale;
  const offsetX = (containerWidth - displayWidth) / 2;
  const offsetY = 0;
  const left = offsetX + bbox[0] * scale;
  const top = offsetY + bbox[1] * scale;
  const width = (bbox[2] - bbox[0]) * scale;
  const height = (bbox[3] - bbox[1]) * scale;

  const borderColor = handBoxColor(idx);
  return {
    position: "absolute",
    border: `1.5px dashed ${borderColor}`,
    background: `${borderColor}22`,
    "--face-frame-color": `${borderColor}77`,
    left: `${left}px`,
    top: `${top}px`,
    width: `${width}px`,
    height: `${height}px`,
  };
}

function getHandBboxOverlays(img) {
  return computed(() => {
    void faceOverlayRedrawKey.value;
    void getThumbnailLoadedKey(img.id);
    if (
      !props.showHandBboxes ||
      !img.hands ||
      !img.hands.length ||
      !(img.thumbnail_width || img.width) ||
      !(img.thumbnail_height || img.height)
    ) {
      return [];
    }
    const el = thumbnailRefs[img.id];
    if (!el) return [];
    const firstFrameHands = img.hands
      .map((hand, handIdx) => ({ hand, handIdx }))
      .filter(
        (entry) =>
          entry.hand.frame_index === 0 || entry.hand.frame_index == null,
      );
    if (firstFrameHands.length === 0) {
      console.debug("[hand-bbox] No first-frame hands", {
        imageId: img.id,
        hands: img.hands?.map((hand) => ({
          id: hand?.id,
          frame_index: hand?.frame_index,
          bbox: hand?.bbox,
        })),
      });
    }
    const overlays = firstFrameHands
      .map((entry, idx) => ({
        style: getHandBboxStyle(entry.hand.bbox, idx, img, el),
        handKey: entry.hand.id ?? `hand-${entry.handIdx}`,
        label: `Hand ${entry.handIdx + 1}`,
      }))
      .filter((entry) => entry.style != null);
    if (!overlays.length && firstFrameHands.length) {
      console.debug("[hand-bbox] Styles filtered", {
        imageId: img.id,
        hands: firstFrameHands.map((entry) => ({
          id: entry.hand?.id,
          frame_index: entry.hand?.frame_index,
          bbox: entry.hand?.bbox,
        })),
        thumbnail: {
          width: img.thumbnail_width,
          height: img.thumbnail_height,
        },
      });
    }
    return overlays;
  });
}

// Track which image is currently hovered
const hoveredImageIdx = ref(null);

function handleImageMouseEnter(img) {
  prefetchFullImage(img);
  hoveredImageIdx.value = img.idx;
}
function handleImageMouseLeave(img) {
  if (hoveredImageIdx.value === img.idx) hoveredImageIdx.value = null;
}

// Number of images before/after viewport to load thumbnails for
// Format date to ISO (YYYY-MM-DD HH:mm:ss)
function getThumbnailInfoItems(img) {
  if (!img) return [];
  const items = [];
  const selectedSort =
    typeof props.selectedSort === "string" ? props.selectedSort : "";

  if (
    selectedSort.includes("CHARACTER_LIKENESS") &&
    img.character_likeness !== undefined
  ) {
    items.push({
      key: "character_likeness",
      text: `Likeness: ${img.character_likeness.toFixed(2)}`,
    });
  }

  if (
    selectedSort.includes("SMART_SCORE") &&
    typeof img.smartScore === "number"
  ) {
    items.push({
      key: "smart_score",
      text: `Smart Score: ${img.smartScore.toFixed(2)}`,
    });
  }

  if (
    typeof props.searchQuery === "string" &&
    img.likeness_score !== undefined
  ) {
    items.push({
      key: "search_likeness",
      text: `Search likeness: ${img.likeness_score.toFixed(2)}`,
    });
  } else if (selectedSort.includes("DATE") && img.created_at) {
    items.push({
      key: "created_at",
      text: formatUserDate(img.created_at, props.dateFormat),
    });
  } else if (
    selectedSort === STACKS_SORT_KEY &&
    (typeof img.stackIndex === "number" || typeof img.stack_index === "number")
  ) {
    const stackIndex =
      typeof img.stackIndex === "number" ? img.stackIndex : img.stack_index;
    items.push({
      key: "stack_index",
      text: `Stack ${stackIndex + 1}`,
    });
  }
  return items;
}

function prefetchFullImage(img) {
  if (!img || !img.id) return;
  if (isVideo(img)) return;
  const id = img.id;
  if (prefetchedFullImageIds.has(id) || fullImagePrefetchControllers.has(id)) {
    return;
  }
  const url = buildMediaUrl({ backendUrl: props.backendUrl, image: img });
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

function clearSelection() {
  selectedImageIds.value = [];
  clearFaceSelection();
  lastSelectedImageId = null;
}

async function refreshTagsForSelection() {
  if (!selectedImageIds.value.length) return;
  const ids = selectedImageIds.value.slice();
  const dIds = new Set(ids.map((id) => PictureId(id)));
  try {
    await apiClient.post(`${props.backendUrl}/pictures/clear_tags`, {
      picture_ids: ids,
    });
    allGridImages.value = allGridImages.value.map((img) => {
      if (!img || !dIds.has(PictureId(img.id))) {
        return img;
      }
      return { ...img, tags: [] };
    });
    for (const id of ids) {
      refreshGridImage(id);
    }
  } catch (err) {
    alert(`Failed to refresh tags: ${err?.message || err}`);
  }
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
  const format = MediaFormat(img);
  if (format) {
    return isSupportedVideoFile(`file.${format}`);
  }
  return isSupportedVideoFile(img.id || "");
}

function removeFromGroup() {
  if (!selectedImageIds.value.length && !selectedFaceIds.value.length) return;
  const backendUrl = props.backendUrl;
  const faceIds = selectedFaceIds.value
    .map((entry) => entry.faceId)
    .filter((id) => id !== undefined && id !== null);
  const pictureIds = selectedImageIds.value.slice();
  if (isScrapheapView.value) {
    if (!pictureIds.length) {
      clearFaceSelection();
      return;
    }
    apiClient
      .post(`${backendUrl}/pictures/scrapheap/restore`, {
        picture_ids: pictureIds,
      })
      .catch((err) => {
        alert(`Error restoring images: ${err.message}`);
      })
      .finally(() => {
        allGridImages.value = allGridImages.value.filter(
          (img) => !pictureIds.includes(img.id),
        );
        selectedImageIds.value = [];
        clearFaceSelection();
        lastSelectedImageId = null;
        fetchAllGridImages().then(() => {
          loadedRanges.value = [];
          updateVisibleThumbnails();
          emit("refresh-sidebar");
        });
        updateVisibleThumbnails();
      });
    return;
  }
  // Remove from character
  if (
    props.selectedCharacter &&
    props.selectedCharacter !== props.allPicturesId &&
    props.selectedCharacter !== props.unassignedPicturesId
  ) {
    const requests = [];
    if (pictureIds.length) {
      requests.push(
        apiClient.delete(
          `${backendUrl}/characters/${props.selectedCharacter}/faces`,
          {
            data: { picture_ids: pictureIds },
          },
        ),
      );
    }
    if (faceIds.length) {
      requests.push(
        apiClient.delete(
          `${backendUrl}/characters/${props.selectedCharacter}/faces`,
          {
            data: { face_ids: faceIds },
          },
        ),
      );
    }
    if (!requests.length) return;
    Promise.all(requests)
      .catch((err) => {
        alert(`Error removing faces from character: ${err.message}`);
      })
      .finally(() => {
        if (pictureIds.length) {
          // Remove affected images from grid immediately
          allGridImages.value = allGridImages.value.filter(
            (img) => !pictureIds.includes(img.id),
          );
        }
        selectedImageIds.value = [];
        clearFaceSelection();
        lastSelectedImageId = null;
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
    if (!pictureIds.length) {
      clearFaceSelection();
      return;
    }
    Promise.all(
      pictureIds.map((id) =>
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
        (img) => !pictureIds.includes(img.id),
      );
      selectedImageIds.value = [];
      clearFaceSelection();
      lastSelectedImageId = null;
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

function handleOverlayAddedToSet(payload) {
  const pictureIds = Array.isArray(payload?.pictureIds)
    ? payload.pictureIds
    : [];
  if (!pictureIds.length) return;
  if (
    props.selectedCharacter === props.unassignedPicturesId &&
    !props.selectedSet
  ) {
    removeImagesById(pictureIds);
  }
  emit("refresh-sidebar");
}

function handleSelectionAddedToSet(payload) {
  handleOverlayAddedToSet(payload);
}

function handleAddToCharacter(payload) {
  const pictureIds = Array.isArray(payload?.pictureIds)
    ? payload.pictureIds
    : [];
  if (!pictureIds.length) return;
  if (
    props.selectedCharacter === props.unassignedPicturesId &&
    !props.selectedSet
  ) {
    removeImagesById(pictureIds);
    selectedImageIds.value = [];
    clearFaceSelection();
    lastSelectedImageId = null;
    updateVisibleThumbnails();
  }
  emit("refresh-sidebar");
}

async function deleteSelected() {
  if (!selectedImageIds.value.length) return;
  const isScrapheapSelection = isScrapheapView.value;
  const idsToRemove = selectedImageIds.value.slice();
  if (isScrapheapSelection) {
    if (
      !confirm(
        `Permanently delete ${selectedImageIds.value.length} selected image(s)?`,
      )
    ) {
      return;
    }
  }
  const backendUrl = props.backendUrl;
  try {
    if (isScrapheapSelection) {
      await apiClient.post(`${backendUrl}/pictures/scrapheap/delete`, {
        picture_ids: idsToRemove,
      });
    } else {
      await Promise.all(
        idsToRemove.map((id) =>
          apiClient.delete(`${backendUrl}/pictures/${id}`).catch((err) => {
            alert(`Error deleting image ${id}: ${err.message}`);
          }),
        ),
      );
    }
    removeImagesById(idsToRemove);
    selectedImageIds.value = [];
    lastSelectedImageId = null;
    if (isScrapheapSelection) {
      updateVisibleThumbnails();
    }
    emit("refresh-sidebar");
  } catch (err) {
    alert(`Error deleting images: ${err?.message || err}`);
  }
}

const isScrapheapView = computed(() => {
  const scrapheapId = String(
    props.scrapheapPicturesId || "SCRAPHEAP",
  ).toUpperCase();
  const selected = String(props.selectedCharacter || "").toUpperCase();
  return selected === scrapheapId;
});
const scrapheapEmptying = ref(false);
const showSelectionBar = computed(() => {
  return selectedImageIds.value.length > 0 || selectedFaceIds.value.length > 0;
});
const isSelectionEmpty = computed(() => {
  return !showSelectionBar.value;
});
const showScrapheapBar = computed(() => {
  return isScrapheapView.value && isSelectionEmpty.value;
});
const SCRAPHEAP_BAR_HEIGHT_PX = 48;
const wrapperStyle = computed(() => {
  return {
    position: "relative",
  };
});
const scrollWrapperStyle = computed(() => {
  const offset =
    showSelectionBar.value || showScrapheapBar.value
      ? SCRAPHEAP_BAR_HEIGHT_PX
      : 0;
  return {
    position: "relative",
    paddingTop: `${offset}px`,
    height: "calc(100vh - 60px)",
  };
});
const scrapheapEmptyDisabled = computed(() => {
  return (
    scrapheapEmptying.value ||
    imagesLoading.value ||
    filteredGridCount.value === 0
  );
});
const scrapheapRestoring = ref(false);
const scrapheapRestoreDisabled = computed(() => {
  return (
    scrapheapRestoring.value ||
    imagesLoading.value ||
    filteredGridCount.value === 0
  );
});

async function confirmEmptyScrapheap() {
  if (scrapheapEmptyDisabled.value) return;
  const confirmed = confirm(
    "Empty scrapheap? This will permanently delete all pictures inside.",
  );
  if (!confirmed) return;
  scrapheapEmptying.value = true;
  try {
    await apiClient.post(`${props.backendUrl}/pictures/scrapheap/empty`);
    allGridImages.value = [];
    selectedImageIds.value = [];
    selectedFaceIds.value = [];
    lastSelectedImageId = null;
    updateVisibleThumbnails();
    emit("refresh-sidebar");
    fetchAllGridImages().then(() => {
      updateVisibleThumbnails();
    });
  } catch (e) {
    alert("Failed to empty scrapheap.");
  } finally {
    scrapheapEmptying.value = false;
  }
}

async function confirmRestoreScrapheap() {
  if (scrapheapRestoreDisabled.value) return;
  const confirmed = confirm(
    "Restore all scrapheap pictures? This will make them visible again.",
  );
  if (!confirmed) return;
  scrapheapRestoring.value = true;
  try {
    await apiClient.post(`${props.backendUrl}/pictures/scrapheap/restore`);
    allGridImages.value = [];
    selectedImageIds.value = [];
    selectedFaceIds.value = [];
    lastSelectedImageId = null;
    updateVisibleThumbnails();
    emit("refresh-sidebar");
    fetchAllGridImages().then(() => {
      updateVisibleThumbnails();
    });
  } catch (e) {
    alert("Failed to restore scrapheap.");
  } finally {
    scrapheapRestoring.value = false;
  }
}

const imageImporterRef = ref(null);
// Handle images-uploaded event from ImageImporter
async function handleImagesUploaded(payload) {
  const results = Array.isArray(payload?.results) ? payload.results : [];
  const pictureIds = Array.from(
    new Set(
      results
        .map((entry) => entry?.picture_id)
        .filter((id) => id !== null && id !== undefined),
    ),
  );
  if (pictureIds.length) {
    try {
      const selectedSetId = props.selectedSet;
      const selectedCharacterId = props.selectedCharacter;
      const selectedCharacterKey = String(selectedCharacterId ?? "");
      const skipCharacter = [
        String(props.allPicturesId),
        String(props.unassignedPicturesId),
        String(props.scrapheapPicturesId),
      ].includes(selectedCharacterKey);
      if (selectedSetId != null && selectedSetId !== "") {
        await Promise.all(
          pictureIds.map((id) =>
            apiClient.post(
              `${props.backendUrl}/picture_sets/${selectedSetId}/members/${id}`,
            ),
          ),
        );
      } else if (!skipCharacter && selectedCharacterId != null) {
        await apiClient.post(
          `${props.backendUrl}/characters/${selectedCharacterId}/faces`,
          { picture_ids: pictureIds },
        );
      }
    } catch (e) {
      console.error("Failed to associate imported pictures:", e);
    }
  }
  resetThumbnailState();
  allGridImages.value = [];
  selectedImageIds.value = [];
  lastSelectedImageId = null;
  fetchAllGridImages().then(() => {
    updateVisibleThumbnails();
  });
  emit("refresh-sidebar");
}

// Adjust debounce timing to 200ms for better responsiveness
const debouncedFetchAllGridImages = debounce(fetchAllGridImages, 200);
const lastGridVersionRefreshAt = ref(0);

// Debounced version of fetchAllGridImages
watch(
  () => props.gridVersion,
  () => {
    const now = Date.now();
    if (now - lastGridVersionRefreshAt.value < 1200) {
      return;
    }
    lastGridVersionRefreshAt.value = now;
    if (skipNextWsRefresh.value) {
      skipNextWsRefresh.value = false;
      return;
    }
    gridReady.value = false;
    emptyStateDelayPassed.value = false;
    if (preserveScrollOnNextFetch.value && scrollWrapper.value) {
      pendingScrollTop.value = scrollWrapper.value.scrollTop;
    } else {
      pendingScrollTop.value = null;
    }
    resetThumbnailState();
    if (!preserveScrollOnNextFetch.value) {
      allGridImages.value = [];
      selectedImageIds.value = [];
      lastSelectedImageId = null;
    }
    debouncedFetchAllGridImages();
    if (preserveScrollOnNextFetch.value) {
      preserveScrollOnNextFetch.value = false;
    }
    fetchAllPicturesCount();
  },
);

const VIEW_WINDOW = 100;

const divisibleViewWindow = computed(() => {
  const cols = props.columns;
  return Math.ceil(VIEW_WINDOW / cols) * cols;
});

const initialRender = ref(true);
const renderBuffer = computed(() =>
  initialRender.value ? 0 : divisibleViewWindow.value,
);

const isLoadingThumbnails = ref(false);
const hasMoreImages = ref(true);

// Image overlay
const overlayOpen = ref(false);
const overlayImageId = ref(null);

// Drag-and-drop overlay state
const dragOverlayVisible = ref(false);
const dragOverlayMessage = ref("Drop files here to import");
const dragSource = ref(null);

const selectedGroupName = ref("");

async function updateSelectedGroupName() {
  let name = "";
  if (
    props.selectedReferenceCharacter &&
    props.selectedReferenceCharacter !== `${props.allPicturesId}` &&
    props.selectedReferenceCharacter !== `${props.unassignedPicturesId}`
  ) {
    try {
      const res = await apiClient.get(
        `${props.backendUrl}/characters/${props.selectedReferenceCharacter}`,
      );
      const char = await res.data;
      name = char?.name
        ? `${char.name} - Reference Pictures`
        : "Reference Pictures";
    } catch (e) {
      console.error("Character fetch failed:", e);
    }
  } else if (
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
  } else if (props.selectedSet) {
    try {
      const res = await apiClient.get(
        `${props.backendUrl}/picture_sets/${props.selectedSet}`,
      );
      const set = await res.data;
      name = set.set.name || "";
    } catch (e) {
      console.error("Set fetch failed:", e);
    }
  }
  selectedGroupName.value = name;
}

watch(
  [
    () => props.selectedCharacter,
    () => props.selectedReferenceCharacter,
    () => props.selectedSet,
  ],
  () => {
    updateSelectedGroupName();
  },
  { immediate: true },
);

// --- Multi-selection state ---
// Local selection state (mirrors parent prop)
const selectedImageIds = ref([]);
let lastSelectedImageId = null;

// --- Overlay ---
async function fetchImageInfo(imageId, options = {}) {
  try {
    const params = new URLSearchParams();
    if (options.smartScore) {
      params.set("smart_score", "true");
    }
    if (options.force) {
      params.set("cb", String(Date.now()));
    }
    const query = params.toString();
    const url = query
      ? `${props.backendUrl}/pictures/${imageId}/metadata?${query}`
      : `${props.backendUrl}/pictures/${imageId}/metadata`;
    const res = await apiClient.get(url);
    const data = await res.data;
    return data;
  } catch (e) {
    console.error("Tag fetch failed:", e);
    return [];
  }
}

function invalidateThumbnailIndex(index) {
  loadedRanges.value = loadedRanges.value.filter(
    ([rangeStart, rangeEnd]) => index < rangeStart || index >= rangeEnd,
  );
}

async function refreshGridImage(imageId) {
  if (!imageId) return;
  const dId = PictureId(imageId);
  const idx = allGridImages.value.findIndex(
    (img) => PictureId(img?.id) === dId,
  );
  if (idx === -1) return;
  const latestInfo = await fetchImageInfo(imageId, {
    smartScore:
      isSmartScoreSortActive() || props.selectedSort === STACKS_SORT_KEY,
  });
  if (latestInfo && !Array.isArray(latestInfo)) {
    const current = allGridImages.value[idx] || {};
    allGridImages.value[idx] = {
      ...current,
      ...latestInfo,
      idx: current.idx ?? idx,
    };
  }
  if (props.selectedSort === STACKS_SORT_KEY) {
    const stackIndex = getStackIndexFromItem(allGridImages.value[idx]);
    if (typeof stackIndex === "number") {
      reorderStackByScore(stackIndex);
    }
  }
  invalidateThumbnailIndex(idx);
  fetchThumbnailsBatch(idx, idx + 1);
}

function getStackIndexFromItem(item) {
  if (!item) return null;
  if (typeof item.stackIndex === "number") return item.stackIndex;
  if (typeof item.stack_index === "number") return item.stack_index;
  return null;
}

function reorderStackByScore(stackIndex) {
  const items = allGridImages.value.slice();
  const stackItems = items.filter(
    (item) => getStackIndexFromItem(item) === stackIndex,
  );
  if (stackItems.length <= 1) return;
  stackItems.sort((a, b) => {
    const scoreA = a?.score ?? 0;
    const scoreB = b?.score ?? 0;
    if (scoreA !== scoreB) return scoreB - scoreA;
    const smartA = a?.smartScore ?? 0;
    const smartB = b?.smartScore ?? 0;
    if (smartA !== smartB) return smartB - smartA;
    return (a?.id ?? 0) - (b?.id ?? 0);
  });
  const result = [];
  let inserted = false;
  for (const item of items) {
    const idx = getStackIndexFromItem(item);
    if (idx === stackIndex) {
      if (inserted) continue;
      result.push(...stackItems);
      inserted = true;
      continue;
    }
    result.push(item);
  }
  for (let i = 0; i < result.length; i += 1) {
    result[i].idx = i;
  }
  allGridImages.value = result;
  invalidateVisibleThumbnailRanges();
}

function addImageToGrid(imageData) {
  if (!imageData?.id) return null;
  const items = allGridImages.value.slice();
  const dId = PictureId(imageData.id);
  const existingIndex = items.findIndex((img) => PictureId(img?.id) === dId);
  if (existingIndex !== -1) {
    const current = items[existingIndex] || {};
    items[existingIndex] = {
      ...current,
      ...imageData,
      idx: current.idx ?? existingIndex,
      thumbnail: current.thumbnail ?? imageData.thumbnail ?? null,
    };
    allGridImages.value = items;
    invalidateThumbnailIndex(existingIndex);
    fetchThumbnailsBatch(existingIndex, existingIndex + 1);
    return existingIndex;
  }
  const newIndex = items.length;
  items.push({
    ...imageData,
    idx: newIndex,
    thumbnail: imageData.thumbnail ?? null,
  });
  for (let i = 0; i < items.length; i += 1) {
    items[i].idx = i;
  }
  allGridImages.value = items;
  invalidateThumbnailIndex(newIndex);
  fetchThumbnailsBatch(newIndex, newIndex + 1);
  return newIndex;
}

async function fetchCharacterLikenessForImage(imageId) {
  if (!imageId || !props.similarityCharacter) return null;
  const params = new URLSearchParams();
  params.set("reference_character_id", String(props.similarityCharacter));
  if (props.selectedCharacter != null) {
    params.set("character_id", String(props.selectedCharacter));
  }
  try {
    const res = await apiClient.get(
      `${props.backendUrl}/pictures/${imageId}/character_likeness?${params.toString()}`,
    );
    return res.data;
  } catch (e) {
    console.error("Failed to fetch character likeness for image:", e);
    return null;
  }
}

function handleOverlayChange(payload) {
  if (!payload) return;
  const imageId = payload.imageId ?? payload.id ?? payload;
  if (!imageId) return;
  const fields = payload.fields || {};
  if ((fields.tags || fields.smartScore) && isSmartScoreSortActive()) {
    refreshGridImage(imageId);
    preserveScrollOnNextFetch.value = true;
    debouncedFetchAllGridImages();
    return;
  }
  refreshGridImage(imageId);
}

async function openOverlay(img) {
  if (!img || !img.id) return;
  overlayImageId.value = img.id;
  overlayOpen.value = true;
}

function closeOverlay() {
  overlayOpen.value = false;
  overlayImageId.value = null;
}

async function setScore(img, n) {
  const newScore = toggleScore(img.score, n);
  applyScore(img, newScore);
}

function isScoreSortActive() {
  return typeof props.selectedSort === "string"
    ? props.selectedSort.toUpperCase() === "SCORE"
    : false;
}

function isDateSortActive() {
  return typeof props.selectedSort === "string"
    ? props.selectedSort.toUpperCase() === "DATE"
    : false;
}

function isCharacterLikenessSortActive() {
  return typeof props.selectedSort === "string"
    ? props.selectedSort.toUpperCase() === "CHARACTER_LIKENESS"
    : false;
}

function isSmartScoreSortActive() {
  return typeof props.selectedSort === "string"
    ? props.selectedSort.toUpperCase().includes("SMART_SCORE")
    : false;
}

function invalidateVisibleThumbnailRanges() {
  const start = Math.max(0, visibleStart.value - renderBuffer.value);
  const end = Math.min(
    allGridImages.value.length,
    visibleEnd.value + renderBuffer.value,
  );
  loadedRanges.value = loadedRanges.value.filter(
    ([rangeStart, rangeEnd]) => rangeEnd <= start || rangeStart >= end,
  );
  updateVisibleThumbnails();
}

function repositionImageByScore(imageId, newScore) {
  const items = allGridImages.value.slice();
  const dId = PictureId(imageId);
  const currentIndex = items.findIndex((item) => PictureId(item?.id) === dId);
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
  if (insertIndex === currentIndex) {
    const updated = allGridImages.value.slice();
    updated[currentIndex] = { ...target, idx: currentIndex };
    allGridImages.value = updated;
    return;
  }
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

function repositionImageByDate(imageId, createdAt) {
  const items = allGridImages.value.slice();
  const currentIndex = items.findIndex((item) => item.id === imageId);
  if (currentIndex === -1) return;

  const target = items[currentIndex];
  const targetTime =
    new Date(createdAt || target.created_at || 0).getTime() || 0;
  items.splice(currentIndex, 1);

  const descending = props.selectedDescending === true;
  let insertIndex = items.findIndex((item) => {
    const itemTime = new Date(item.created_at || 0).getTime() || 0;
    return descending ? itemTime < targetTime : itemTime > targetTime;
  });
  if (insertIndex === -1) insertIndex = items.length;
  items.splice(insertIndex, 0, target);

  for (let i = 0; i < items.length; i += 1) {
    items[i].idx = i;
  }

  allGridImages.value = items;
  invalidateVisibleThumbnailRanges();
}

function repositionImageByLikeness(imageId) {
  const items = allGridImages.value.slice();
  const currentIndex = items.findIndex((item) => item.id === imageId);
  if (currentIndex === -1) return;

  const target = items[currentIndex];
  const targetScore =
    target.character_likeness ?? target.likeness_score ?? target.score ?? 0;
  items.splice(currentIndex, 1);

  const descending = props.selectedDescending === true;
  let insertIndex = items.findIndex((item) => {
    const score =
      item.character_likeness ?? item.likeness_score ?? item.score ?? 0;
    return descending ? score < targetScore : score > targetScore;
  });
  if (insertIndex === -1) insertIndex = items.length;
  items.splice(insertIndex, 0, target);

  for (let i = 0; i < items.length; i += 1) {
    items[i].idx = i;
  }

  allGridImages.value = items;
  invalidateVisibleThumbnailRanges();
}

let smartScoreRepositioning = false;

function repositionImageBySmartScore(imageId, smartScore, latestInfo = null) {
  if (smartScoreRepositioning) {
    console.debug("[SmartScore] Reposition skipped (lock active):", imageId);
    return;
  }
  smartScoreRepositioning = true;
  try {
    const items = allGridImages.value.slice();
    const currentIndex = items.findIndex((item) => item.id === imageId);
    if (currentIndex === -1) {
      console.debug("[SmartScore] Reposition skipped (not in grid):", imageId);
      return;
    }

    const targetScore = smartScore ?? 0;
    const target = {
      ...items[currentIndex],
      ...(latestInfo && typeof latestInfo === "object" ? latestInfo : {}),
      smartScore: targetScore,
      thumbnail:
        items[currentIndex]?.thumbnail ?? latestInfo?.thumbnail ?? null,
    };
    items.splice(currentIndex, 1);

    const descending = props.selectedDescending === true;
    let insertIndex = items.findIndex((item) => {
      const score = item.smartScore ?? 0;
      return descending ? score < targetScore : score > targetScore;
    });
    if (insertIndex === -1) insertIndex = items.length;
    console.debug("[SmartScore] Reposition", {
      imageId,
      currentIndex,
      insertIndex,
      targetScore,
      descending,
    });
    if (insertIndex === currentIndex) {
      const updated = allGridImages.value.slice();
      updated[currentIndex] = { ...target, idx: currentIndex };
      allGridImages.value = updated;
      return;
    }
    items.splice(insertIndex, 0, target);

    for (let i = 0; i < items.length; i += 1) {
      items[i].idx = i;
    }

    allGridImages.value = items;
    invalidateVisibleThumbnailRanges();
  } finally {
    smartScoreRepositioning = false;
  }
}

async function refreshSmartScoreForImage(imageId) {
  if (!imageId || !isSmartScoreSortActive()) return;
  console.debug("[SmartScore] Refresh requested", {
    imageId,
    sort: props.selectedSort,
  });
  const latestInfo = await fetchImageInfo(imageId, { smartScore: true });
  if (!latestInfo || Array.isArray(latestInfo)) return;

  const idx = allGridImages.value.findIndex((img) => img?.id === imageId);
  if (idx !== -1) {
    const current = allGridImages.value[idx] || {};
    const smartScore =
      typeof latestInfo.smartScore === "number" ? latestInfo.smartScore : null;
    console.debug("[SmartScore] Refresh result", {
      imageId,
      smartScore,
    });
    if (current.smartScore === smartScore) {
      console.debug("[SmartScore] No score change; skipping reposition", {
        imageId,
      });
      return;
    }
    await nextTick();
    await new Promise((resolve) => requestAnimationFrame(resolve));
    repositionImageBySmartScore(imageId, smartScore ?? 0, latestInfo);
  }
}

async function applyScoresByEntries(entries, options = {}) {
  const { updateSort = true, emitRefreshSidebar = true } = options;
  if (!Array.isArray(entries) || !entries.length) return;

  const chunkSize = 50;
  for (let i = 0; i < entries.length; i += chunkSize) {
    const chunk = entries.slice(i, i + chunkSize);
    await Promise.all(
      chunk.map(([id, score]) =>
        apiClient.patch(`${props.backendUrl}/pictures/${id}`, {
          score,
        }),
      ),
    );
  }

  const scoreMap = new Map(
    entries.map(([id, score]) => [String(id), Number(score)]),
  );

  let updatedImages = allGridImages.value.map((img) => {
    if (!img || img.id == null) return img;
    const key = String(img.id);
    if (!scoreMap.has(key)) return img;
    return { ...img, score: scoreMap.get(key) };
  });

  if (updateSort && isScoreSortActive()) {
    const descending = props.selectedDescending === true;
    updatedImages = updatedImages
      .slice()
      .sort((a, b) => {
        const aScore = a?.score ?? 0;
        const bScore = b?.score ?? 0;
        if (aScore === bScore) {
          const aIdx = a?.idx ?? 0;
          const bIdx = b?.idx ?? 0;
          return aIdx - bIdx;
        }
        return descending ? bScore - aScore : aScore - bScore;
      })
      .map((img, idx) => (img ? { ...img, idx } : img));
    allGridImages.value = updatedImages;
    invalidateVisibleThumbnailRanges();
  } else {
    allGridImages.value = updatedImages;
  }

  if (updateSort && isCharacterLikenessSortActive()) {
    preserveScrollOnNextFetch.value = true;
    debouncedFetchAllGridImages();
  }

  if (updateSort && isSmartScoreSortActive()) {
    preserveScrollOnNextFetch.value = true;
    debouncedFetchAllGridImages();
  }

  if (emitRefreshSidebar) {
    emit("refresh-sidebar");
  }
}

async function applyScore(img, newScore) {
  console.debug("Applying score:", newScore);
  const imageId = img?.id;
  if (!imageId) {
    alert("Failed to set score: image id is missing.");
    return;
  }
  try {
    await applyScoresByEntries([[String(imageId), newScore]], {
      updateSort: false,
      emitRefreshSidebar: false,
    });

    if (isScoreSortActive()) {
      repositionImageByScore(imageId, newScore);
    }
    if (isCharacterLikenessSortActive()) {
      preserveScrollOnNextFetch.value = true;
      debouncedFetchAllGridImages();
      emit("refresh-sidebar");
      return;
    }
    if (isSmartScoreSortActive()) {
      preserveScrollOnNextFetch.value = true;
      debouncedFetchAllGridImages();
      emit("refresh-sidebar");
      return;
    }
    emit("refresh-sidebar");
  } catch (e) {
    alert(e.message);
  }
}

async function applyScoresForSelection(imageIds, targetScore) {
  const ids = Array.isArray(imageIds) ? imageIds.filter(Boolean) : [];
  if (!ids.length) return;
  if (!Number.isFinite(targetScore)) return;

  const gridById = new Map(
    allGridImages.value
      .filter((img) => img && img.id != null)
      .map((img) => [String(img.id), img]),
  );

  const entries = [];
  for (const id of ids) {
    const key = String(id);
    const img = gridById.get(key);
    if (!img) continue;
    const current = Number(img.score || 0);
    const nextScore = toggleScore(current, targetScore);
    entries.push([key, nextScore]);
  }

  if (!entries.length) return;

  await applyScoresByEntries(entries, {
    updateSort: true,
    emitRefreshSidebar: true,
  });
}

// Drag-and-drop overlay handlers
function isFileDrag(dataTransfer) {
  if (!dataTransfer) return false;
  const types = dataTransfer.types ? Array.from(dataTransfer.types) : [];
  return types.includes("Files") || types.includes("application/x-moz-file");
}

function handleGridDragEnter(e) {
  if (!e.dataTransfer) return;
  const types = e.dataTransfer.types ? Array.from(e.dataTransfer.types) : [];
  if (!isFileDrag(e.dataTransfer) && types.length > 0) return;
  dragOverlayVisible.value = true;
  dragOverlayMessage.value = "Drop files here to import";
  e.preventDefault();
}

function handleGridDragOver(e) {
  if (!e.dataTransfer) return;
  const types = e.dataTransfer.types ? Array.from(e.dataTransfer.types) : [];
  if (!isFileDrag(e.dataTransfer) && types.length > 0) return;
  if (!dragOverlayVisible.value) {
    dragOverlayVisible.value = true;
    dragOverlayMessage.value = "Drop files here to import";
  }
  e.preventDefault();
}

function handleGridDragLeave(e) {
  if (!e.relatedTarget || !e.currentTarget.contains(e.relatedTarget)) {
    dragOverlayVisible.value = false;
  }
}

function handleGridDrop(e) {
  dragOverlayVisible.value = false;

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
  const files = Array.from(e.dataTransfer.files).filter(isSupportedImportFile);
  console.debug("[IMPORT] Files dropped:", e.dataTransfer.files);
  console.debug("[IMPORT] Supported files after filter:", files);
  if (!files.length) {
    alert("No supported files found.");
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
    const maxScroll = (() => {
      const total = allGridImages.value.length;
      const cols = Math.max(1, props.columns || 1);
      const totalRows = Math.ceil(total / cols);
      const totalHeight = totalRows * rowHeight.value;
      return Math.max(0, totalHeight - scrollWrapper.value.clientHeight);
    })();
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
const gridReady = ref(false);
const gridLoadEpoch = ref(0);
const lastFetchKey = ref("");
const lastFetchError = ref({ key: "", at: 0 });
const lastFetchSuccess = ref({ key: "", at: 0 });

function buildGridFetchKey() {
  return JSON.stringify({
    selectedCharacter: props.selectedCharacter ?? null,
    selectedReferenceCharacter: props.selectedReferenceCharacter ?? null,
    selectedSet: props.selectedSet ?? null,
    searchQuery: props.searchQuery ?? "",
    selectedSort: props.selectedSort ?? "",
    selectedDescending: props.selectedDescending ?? null,
    stackThreshold: props.stackThreshold ?? null,
    mediaTypeFilter: props.mediaTypeFilter ?? "all",
    similarityCharacter: props.similarityCharacter ?? null,
  });
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
  const color = img.stackColor || getStackColor(rawIndex, STACK_COLOR_STEP);
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
  } else {
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
  }
  params.append("fields", "grid");
  // Add format filter for backend media type filtering
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
  const fetchKey = buildGridFetchKey();
  const now = Date.now();
  if (imagesLoading.value && lastFetchKey.value === fetchKey) {
    const lastActivity = Math.max(
      lastFetchSuccess.value.at || 0,
      lastFetchError.value.at || 0,
    );
    if (now - lastActivity < 2500) {
      return;
    }
    imagesLoading.value = false;
  }
  if (
    lastFetchSuccess.value.key === fetchKey &&
    now - lastFetchSuccess.value.at < 1200
  ) {
    return;
  }
  if (
    lastFetchError.value.key === fetchKey &&
    now - lastFetchError.value.at < 2500
  ) {
    return;
  }
  lastFetchKey.value = fetchKey;
  const loadId = (gridLoadEpoch.value += 1);
  gridReady.value = false;
  imagesLoading.value = true;
  imagesError.value = null;
  try {
    const fetchStart = performance.now();
    let images = [];
    const requestId = Date.now();
    fetchAllGridImages.lastRequestId = requestId;
    if (
      props.selectedReferenceCharacter &&
      props.selectedReferenceCharacter !== props.allPicturesId &&
      props.selectedReferenceCharacter !== props.unassignedPicturesId
    ) {
      const refRequestStart = performance.now();
      const refRes = await apiClient.get(
        `${props.backendUrl}/characters/${props.selectedReferenceCharacter}/reference_pictures`,
      );
      const refRequestEnd = performance.now();
      const refData = await refRes.data;
      const refParseEnd = performance.now();
      if (fetchAllGridImages.lastRequestId !== requestId) return;
      const referenceIds = Array.isArray(refData?.reference_picture_ids)
        ? refData.reference_picture_ids
        : [];
      console.log("[ImageGrid.vue] /characters/reference_pictures timing", {
        count: referenceIds.length,
        requestMs: (refRequestEnd - refRequestStart).toFixed(1),
        totalMs: (refParseEnd - refRequestStart).toFixed(1),
      });

      if (referenceIds.length) {
        const params = new URLSearchParams();
        referenceIds.forEach((id) => params.append("id", String(id)));
        if (props.mediaTypeFilter === "images") {
          for (const ext of PIL_IMAGE_EXTENSIONS) {
            params.append("format", ext.toUpperCase());
          }
        } else if (props.mediaTypeFilter === "videos") {
          for (const ext of VIDEO_EXTENSIONS) {
            params.append("format", ext.toUpperCase());
          }
        }
        const picsUrl = `${props.backendUrl}/pictures?${params.toString()}`;
        const picsRequestStart = performance.now();
        const picsRes = await apiClient.get(picsUrl);
        const picsRequestEnd = performance.now();
        const picsData = await picsRes.data;
        const picsParseEnd = performance.now();
        if (fetchAllGridImages.lastRequestId !== requestId) return;
        const picList = Array.isArray(picsData) ? picsData : [];
        const picsById = new Map(
          picList.map((img) => [PictureId(img?.id), img]),
        );
        images = referenceIds
          .map((id) => picsById.get(PictureId(id)))
          .filter(Boolean);
        console.log("[ImageGrid.vue] /pictures by reference ids timing", {
          count: images.length,
          requestMs: (picsRequestEnd - picsRequestStart).toFixed(1),
          totalMs: (picsParseEnd - picsRequestStart).toFixed(1),
        });
      }
    } else if (props.selectedSort === STACKS_SORT_KEY) {
      const threshold = StackThreshold(props.stackThreshold);
      const stackParams = buildStackQueryParams();
      const url = `${props.backendUrl}/pictures/stacks?threshold=${encodeURIComponent(
        threshold,
      )}${stackParams ? `&${stackParams}` : ""}`;
      const requestStart = performance.now();
      const res = await apiClient.get(url);
      const requestEnd = performance.now();
      const data = await res.data;
      const parseEnd = performance.now();
      if (fetchAllGridImages.lastRequestId !== requestId) return;
      const stackImages = Array.isArray(data) ? data : [];
      console.log("[ImageGrid.vue] /pictures/stacks timing", {
        count: stackImages.length,
        requestMs: (requestEnd - requestStart).toFixed(1),
        totalMs: (parseEnd - requestStart).toFixed(1),
      });
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
            typeof stackIndex === "number"
              ? getStackColor(stackIndex, STACK_COLOR_STEP)
              : null,
        };
      });
    } else if (props.searchQuery && props.searchQuery.trim()) {
      // Use /pictures/search endpoint for text search
      const params = buildPictureIdsQueryParams();
      const url = `${
        props.backendUrl
      }/pictures/search?query=${encodeURIComponent(
        props.searchQuery.trim(),
      )}&threshold=0.1&top_n=10000${params ? `&${params}` : ""}`;
      const requestStart = performance.now();
      const res = await apiClient.get(url);
      const requestEnd = performance.now();
      const data = await res.data;
      const parseEnd = performance.now();
      images = data;
      console.log("[ImageGrid.vue] /pictures/search timing", {
        count: Array.isArray(images) ? images.length : 0,
        requestMs: (requestEnd - requestStart).toFixed(1),
        totalMs: (parseEnd - requestStart).toFixed(1),
      });
    } else if (
      props.selectedSet &&
      props.selectedSet !== props.allPicturesId &&
      props.selectedSet !== props.unassignedPicturesId
    ) {
      const params = buildPictureIdsQueryParams();
      const url = `${props.backendUrl}/picture_sets/${props.selectedSet}${
        params ? `?${params}` : ""
      }`;
      const requestStart = performance.now();
      const res = await apiClient.get(url);
      const requestEnd = performance.now();
      const data = await res.data;
      const parseEnd = performance.now();
      images = data.pictures || [];
      console.log("[ImageGrid.vue] /picture_sets timing", {
        count: images.length,
        requestMs: (requestEnd - requestStart).toFixed(1),
        totalMs: (parseEnd - requestStart).toFixed(1),
      });
    } else {
      const params = buildPictureIdsQueryParams();
      // Only use allowed parameters: sort, offset, limit, threshold
      const url = `${props.backendUrl}/pictures?offset=0&limit=10000${
        params ? `&${params}` : ""
      }`;
      const requestStart = performance.now();
      const res = await apiClient.get(url);
      const requestEnd = performance.now();
      const data = await res.data;
      const parseEnd = performance.now();
      images = data;
      console.log("[ImageGrid.vue] /pictures timing", {
        count: Array.isArray(images) ? images.length : 0,
        requestMs: (requestEnd - requestStart).toFixed(1),
        totalMs: (parseEnd - requestStart).toFixed(1),
      });
    }
    const shouldHighlight = highlightNextFetch.value && hasLoadedOnce.value;
    const nextIdSet = new Set(
      Array.isArray(images)
        ? images.map((img) => PictureId(img?.id)).filter((id) => id !== null)
        : [],
    );
    if (shouldHighlight) {
      const newIds = [];
      nextIdSet.forEach((id) => {
        if (!previousImageIds.has(id)) {
          newIds.push(id);
        }
      });
      if (newIds.length) {
        triggerNewImageHighlight(newIds);
      }
    }
    previousImageIds.clear();
    nextIdSet.forEach((id) => previousImageIds.add(id));
    highlightNextFetch.value = false;
    hasLoadedOnce.value = true;
    const mapStart = performance.now();
    const existingById = new Map(
      allGridImages.value
        .filter((img) => img && img.id != null)
        .map((img) => [PictureId(img.id), img]),
    );
    const uniqueImages = Array.isArray(images)
      ? (() => {
          const seen = new Set();
          return images.filter((img) => {
            const id = PictureId(img?.id);
            if (id == null) return true;
            if (seen.has(id)) return false;
            seen.add(id);
            return true;
          });
        })()
      : [];
    const newImages = uniqueImages.map((img, i) => {
      const existing = img?.id ? existingById.get(PictureId(img.id)) : null;
      return {
        ...img,
        idx: i,
        thumbnail: existing?.thumbnail ?? null,
        penalised_tags: Array.isArray(existing?.penalised_tags)
          ? existing.penalised_tags
          : [],
        faces: Array.isArray(existing?.faces) ? existing.faces : [],
        hands: Array.isArray(existing?.hands) ? existing.hands : [],
        thumbnail_width: existing?.thumbnail_width ?? img?.thumbnail_width,
        thumbnail_height: existing?.thumbnail_height ?? img?.thumbnail_height,
      };
    });
    const mapEnd = performance.now();
    console.log("Updating allGridImages with fetched images:", {
      count: newImages.length,
    });
    allGridImages.value = newImages;
    const assignEnd = performance.now();
    const cols = props.columns || 1;
    const windowCount = Math.max(cols, divisibleViewWindow.value || cols);
    visibleStart.value = 0;
    visibleEnd.value = Math.min(newImages.length, windowCount);
    if (initialRender.value) {
      const prefetchEnd = Math.min(
        newImages.length,
        visibleEnd.value + divisibleViewWindow.value,
      );
      fetchThumbnailsBatch(visibleStart.value, prefetchEnd);
    }
    const rangeEnd = performance.now();
    const fetchEnd = performance.now();
    console.log("[ImageGrid.vue] fetchAllGridImages total timing", {
      totalMs: (fetchEnd - fetchStart).toFixed(1),
      count: newImages.length,
      mapMs: (mapEnd - mapStart).toFixed(1),
      assignMs: (assignEnd - mapEnd).toFixed(1),
      rangeMs: (rangeEnd - assignEnd).toFixed(1),
    });
    requestAnimationFrame(() => {
      const rafEnd = performance.now();
      console.log("[ImageGrid.vue] post-assign frame timing", {
        rafMs: (rafEnd - assignEnd).toFixed(1),
      });
      if (initialRender.value) {
        initialRender.value = false;
        updateVisibleThumbnails();
      }
    });
    lastFetchSuccess.value = { key: fetchKey, at: Date.now() };
  } catch (e) {
    imagesError.value = e.message;
    allGridImages.value = [];
    lastFetchError.value = { key: fetchKey, at: Date.now() };
  } finally {
    if (loadId === gridLoadEpoch.value) {
      imagesLoading.value = false;
      gridReady.value = true;
    }
  }
  if (!initialRender.value) {
    updateVisibleThumbnails();
  }
  if (pendingScrollTop.value !== null && scrollWrapper.value) {
    const targetTop = pendingScrollTop.value;
    pendingScrollTop.value = null;
    nextTick(() => {
      if (!scrollWrapper.value) return;
      const maxScroll =
        scrollWrapper.value.scrollHeight - scrollWrapper.value.clientHeight;
      const clamped = Math.max(0, Math.min(targetTop, maxScroll));
      scrollWrapper.value.scrollTop = clamped;
      updateVisibleThumbnails();
    });
  }
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
    () => props.selectedReferenceCharacter,
    () => props.selectedSet,
    () => props.searchQuery,
    () => props.selectedSort,
    () => props.stackThreshold,
  ],
  () => {
    console.log(
      "[ImageGrid.vue] Filters changed. Resetting state and fetching total image count.",
    );
    gridReady.value = false;
    emptyStateDelayPassed.value = false;
    resetThumbnailState();
    allGridImages.value = [];
    selectedImageIds.value = [];
    lastSelectedImageId = null;
    initialRender.value = true;
    updateSelectedGroupName();
    debouncedFetchAllGridImages();
  },
);

watch([() => props.mediaTypeFilter], () => {
  console.log(
    "[ImageGrid.vue] Media Type filters changed. Resetting state and fetching total image count.",
  );
  gridReady.value = false;
  emptyStateDelayPassed.value = false;
  // Reset loaded ranges, thumbnails, pagination, and fetch new count/images for filter
  resetThumbnailState();
  selectedImageIds.value = [];
  lastSelectedImageId = null;
  visibleStart.value = 0;
  visibleEnd.value = 0;
  allGridImages.value = [];
  initialRender.value = true;
  fetchAllGridImages().then(() => {
    updateVisibleThumbnails();
  });
});

watch(
  () => props.columns,
  async () => {
    updateRowHeightFromGrid();
    updateVisibleThumbnails();
    await nextTick();
    triggerFaceOverlayRedraw();
    requestAnimationFrame(() => {
      triggerFaceOverlayRedraw();
    });
  },
);

// Track loaded batch ranges to avoid duplicate requests
const loadedRanges = ref([]);
// Debounce timer for scroll-triggered fetches
let thumbFetchTimeout = null;
let pendingRanges = [];
const thumbnailRequestEpoch = ref(0);

function resetThumbnailState() {
  loadedRanges.value = [];
  pendingRanges = [];
  if (thumbFetchTimeout) {
    clearTimeout(thumbFetchTimeout);
    thumbFetchTimeout = null;
  }
  thumbnailRequestEpoch.value += 1;
  for (const key of Object.keys(thumbnailLoadedMap)) {
    delete thumbnailLoadedMap[key];
  }
  for (const timer of thumbnailRetryTimers.values()) {
    clearTimeout(timer);
  }
  thumbnailRetryTimers.clear();
  for (const key of Object.keys(thumbnailRetryCounts)) {
    delete thumbnailRetryCounts[key];
  }
}

function rangeCovers(ranges, start, end) {
  return ranges.some(
    ([rangeStart, rangeEnd]) => start >= rangeStart && end <= rangeEnd,
  );
}

// Track which indices are visible in the grid

const visibleStart = ref(0);
const visibleEnd = ref(0);

const rowHeight = ref(
  Math.round(
    Math.min(
      MAX_THUMBNAIL_SIZE,
      Math.max(MIN_THUMBNAIL_SIZE, props.thumbnailSize || MIN_THUMBNAIL_SIZE),
    ) + THUMBNAIL_INFO_ROW_HEIGHT,
  ),
);

function getGridColumnWidth() {
  const cols = Math.max(1, props.columns || 1);
  const gridWidth =
    gridContainer.value?.clientWidth ?? scrollWrapper.value?.clientWidth ?? 0;
  if (!gridWidth) {
    return Math.min(
      MAX_THUMBNAIL_SIZE,
      Math.max(MIN_THUMBNAIL_SIZE, props.thumbnailSize || MIN_THUMBNAIL_SIZE),
    );
  }
  const availableWidth = Math.max(0, gridWidth - 4);
  const rawWidth = availableWidth / cols;
  return Math.min(
    MAX_THUMBNAIL_SIZE,
    Math.max(MIN_THUMBNAIL_SIZE, rawWidth || MIN_THUMBNAIL_SIZE),
  );
}

function updateRowHeightFromGrid() {
  const columnWidth = getGridColumnWidth();
  rowHeight.value = Math.round(columnWidth + THUMBNAIL_INFO_ROW_HEIGHT);
  refreshAllThumbnailInfoDisplays();
}

// columns is now controlled by prop

const renderStart = computed(() => {
  const cols = props.columns;
  let start = Math.max(0, visibleStart.value - renderBuffer.value);
  return start;
});

const renderEnd = computed(() => {
  const cols = props.columns;
  let end = Math.min(
    allGridImages.value.length,
    visibleEnd.value + renderBuffer.value,
  );
  return end;
});

const topSpacerHeight = computed(() => {
  const cols = props.columns;
  const rowsAbove = Math.floor(renderStart.value / cols);
  const height = rowsAbove > 0 ? rowsAbove * rowHeight.value : 1;
  return height;
});

const bottomSpacerHeight = computed(() => {
  const cols = props.columns;
  const lastRenderedRow = Math.floor((renderEnd.value - 1) / cols) + 1;
  const totalRows = Math.ceil(allGridImages.value.length / cols);
  const rowsBelow = totalRows - lastRenderedRow;
  const height = rowsBelow > 0 ? rowsBelow * rowHeight.value : 0;
  return height;
});

// Compute grid images (id, idx, thumbnail)
const allGridImages = ref([]);

watch(
  [
    () => props.showFaceBboxes,
    () => props.showHandBboxes,
    () => allGridImages.value.length,
  ],
  ([faceEnabled, handEnabled, length], [prevFace, prevHand, prevLength]) => {
    if (!faceEnabled && !handEnabled) return;
    if (length <= 0) return;
    if (
      faceEnabled === prevFace &&
      handEnabled === prevHand &&
      length === prevLength
    ) {
      return;
    }
    invalidateVisibleThumbnailRanges();
  },
);

function filterImagesByMediaType(images) {
  let filtered = images;
  if (props.mediaTypeFilter === "images") {
    filtered = filtered.filter((img) => {
      if (!img) return false;
      const candidates = [img.name, img.id, img.format]
        .filter(Boolean)
        .map((v) => (typeof v === "string" ? v : ""));
      return candidates.some((val) => isSupportedImageFile(val));
    });
  } else if (props.mediaTypeFilter === "videos") {
    filtered = filtered.filter((img) => {
      if (!img) return false;
      const candidates = [img.name, img.id, img.format]
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
    gridReady.value &&
    !imagesLoading.value &&
    filteredGridCount.value === 0 &&
    emptyStateDelayPassed.value
  );
});

const canShowAllPicturesButton = computed(() => {
  return totalAllPicturesCount.value > 0;
});

const emptyStateTitle = computed(() => {
  if (isScrapheapView.value) {
    return "No pictures in the scrap heap";
  }
  return totalAllPicturesCount.value > 0
    ? "No pictures match the current filters"
    : "No pictures in the database.";
});

const emptyStateSubtitle = computed(() => {
  if (isScrapheapView.value) {
    return "Are all your pictures that good?";
  }
  return totalAllPicturesCount.value > 0
    ? "Try clearing filters, adjusting your search, or switching sets."
    : "Add pictures by dragging them here.";
});

const emptyStateImage = computed(() => {
  return isScrapheapView.value ? "/src/EmptyTrash.png" : "/src/Empty.png";
});

const emptyStateAlt = computed(() => {
  return isScrapheapView.value ? "Empty scrap heap" : "No images";
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

const gridImagesToRender = computed(() => {
  if (!allGridImages.value) {
    console.warn("allGridImages is undefined");
    return [];
  }

  const filtered = filterImagesByMediaType(allGridImages.value);
  return filtered.slice(renderStart.value, renderEnd.value);
});

// Batch fetch metadata (including thumbnail) for visible range
async function fetchThumbnailsBatch(start, end) {
  if (start === undefined || start === null) {
    start = renderStart.value;
  }
  if (end === undefined || end === null) {
    end = renderEnd.value;
  }

  const requestEpoch = thumbnailRequestEpoch.value;

  if (rangeCovers(loadedRanges.value, start, end)) return;
  if (rangeCovers(pendingRanges, start, end)) return;
  pendingRanges.push([start, end]);
  // Fetch batch metadata for visible range
  try {
    const batchStart = performance.now();
    let images = [];
    let ids = [];
    // If a set is selected, use /picture_sets/{id}
    if (
      props.selectedSet &&
      props.selectedSet !== props.allPicturesId &&
      props.selectedSet !== props.unassignedPicturesId &&
      !(props.searchQuery && props.searchQuery.trim())
    ) {
      const params = buildPictureIdsQueryParams();
      const url = `${props.backendUrl}/picture_sets/${props.selectedSet}${
        params ? `?${params}` : ""
      }`;
      const requestStart = performance.now();
      const res = await apiClient.get(url);
      const requestEnd = performance.now();
      const data = await res.data;
      const parseEnd = performance.now();
      images = data.pictures ? data.pictures.slice(start, end) : [];
      console.log("[ImageGrid.vue] /picture_sets batch timing", {
        count: images.length,
        requestMs: (requestEnd - requestStart).toFixed(1),
        totalMs: (parseEnd - requestStart).toFixed(1),
      });
      ids = images.map((img) => img.id);
    } else {
      // Only fetch if we don't already have metadata for this range
      images = allGridImages.value.slice(start, end);
      ids = images.map((img) => img.id);
    }
    // Prepare grid image objects
    const gridImages = images.map((img, idx) => ({
      ...img,
      score: img.score ?? 0,
      idx: start + idx, // Ensure idx is global index
      thumbnail: null,
    }));
    // Now fetch thumbnails for these IDs
    ids = ids.filter((id) => id !== null && id !== undefined);
    let overlayNeedsRedraw = false;
    if (ids.length) {
      ids = Array.from(new Set(ids.map((id) => String(id))));
      const thumbRequestStart = performance.now();
      const thumbRes = await apiClient.post(
        `${props.backendUrl}/pictures/thumbnails`,
        JSON.stringify({ ids }),
      );
      const thumbRequestEnd = performance.now();
      const thumbData = await thumbRes.data;
      const thumbParseEnd = performance.now();
      console.log("[ImageGrid.vue] /pictures/thumbnails timing", {
        count: ids.length,
        requestMs: (thumbRequestEnd - thumbRequestStart).toFixed(1),
        totalMs: (thumbParseEnd - thumbRequestStart).toFixed(1),
      });
      if (requestEpoch !== thumbnailRequestEpoch.value) {
        return;
      }
      for (const gridImg of gridImages) {
        const thumbObj = thumbData[String(gridImg.id)];
        const thumbnailUrl =
          thumbObj && thumbObj.thumbnail ? thumbObj.thumbnail : null;
        gridImg.thumbnail = thumbnailUrl
          ? thumbnailUrl.startsWith("http")
            ? thumbnailUrl
            : `${props.backendUrl}${thumbnailUrl}`
          : null;
        if (gridImg.id != null && thumbObj && thumbObj.thumbnail) {
          thumbnailLoadedMap[gridImg.id] =
            (thumbnailLoadedMap[gridImg.id] || 0) + 1;
        }
        gridImg.faces =
          thumbObj && Array.isArray(thumbObj.faces) ? thumbObj.faces : [];
        gridImg.hands =
          thumbObj && Array.isArray(thumbObj.hands) ? thumbObj.hands : [];
        if (props.showFaceBboxes && gridImg.faces.length) {
          overlayNeedsRedraw = true;
        }
        if (props.showHandBboxes && gridImg.hands.length) {
          overlayNeedsRedraw = true;
        }
        gridImg.penalised_tags =
          thumbObj && Array.isArray(thumbObj.penalised_tags)
            ? thumbObj.penalised_tags
            : [];
        if (thumbObj) {
          const thumbWidth = Number(thumbObj.thumbnail_width);
          const thumbHeight = Number(thumbObj.thumbnail_height);
          if (!Number.isNaN(thumbWidth) && thumbWidth > 0) {
            gridImg.thumbnail_width = thumbWidth;
          }
          if (!Number.isNaN(thumbHeight) && thumbHeight > 0) {
            gridImg.thumbnail_height = thumbHeight;
          }
        }
      }
    }
    // Insert/update images at their correct indices
    if (requestEpoch !== thumbnailRequestEpoch.value) {
      return;
    }
    console.log("Updating allGridImages with thumbnails");
    for (let i = 0; i < gridImages.length; i++) {
      const img = gridImages[i];
      img.idx = start + i; // Redundant but explicit for safety
      allGridImages.value[start + i] = img;
      if (img.thumbnail) {
        clearThumbnailRetry(img.id);
      } else {
        scheduleThumbnailRetry(img.id, start + i, requestEpoch);
      }
    }
    loadedRanges.value.push([start, end]);
    if (overlayNeedsRedraw) {
      triggerFaceOverlayRedraw();
    }
    const batchEnd = performance.now();
    console.log("[ImageGrid.vue] fetchThumbnailsBatch total timing", {
      count: gridImages.length,
      totalMs: (batchEnd - batchStart).toFixed(1),
    });
  } catch (err) {
    console.error("[BATCH ERROR]", err);
  } finally {
    pendingRanges = pendingRanges.filter(
      ([rangeStart, rangeEnd]) => rangeStart !== start || rangeEnd !== end,
    );
  }
}

function updateVisibleThumbnails() {
  let start = Math.max(0, visibleStart.value - renderBuffer.value);
  let end = Math.min(
    allGridImages.value.length,
    visibleEnd.value + renderBuffer.value,
  );
  if (rangeCovers(loadedRanges.value, start, end)) return;
  if (rangeCovers(pendingRanges, start, end)) return;
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

  const requestEpoch = thumbnailRequestEpoch.value;
  thumbFetchTimeout = setTimeout(async () => {
    if (requestEpoch !== thumbnailRequestEpoch.value) {
      return;
    }
    console.log("[ImageGrid.vue] Fetching thumbnails batch:", { start, end });
    await fetchThumbnailsBatch(start, end);
  }, 80);
}

function hasPenalisedTags(img) {
  return Array.isArray(img?.penalised_tags) && img.penalised_tags.length > 0;
}

function penalisedTagsTitle(img) {
  const tags = Array.isArray(img?.penalised_tags) ? img.penalised_tags : [];
  if (!tags.length) return "";
  return `Penalised tags: ${tags.join(", ")}`;
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
    const cols = props.columns;
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
      // Only trigger buffer expansion/fetch if user is near buffer end
      // Always fetch thumbnails for the current visible window
      updateVisibleThumbnails();
    }
  }, 50);
}

// Selection logic
const isImageSelected = (id) =>
  selectedImageIds.value && selectedImageIds.value.includes(id);

function setDragImageFromElement(event, element) {
  if (!element || !event?.dataTransfer?.setDragImage) return;
  const width = element.naturalWidth || element.width || 160;
  const height = element.naturalHeight || element.height || 90;
  event.dataTransfer.setDragImage(
    element,
    Math.max(1, width / 2),
    Math.max(1, height / 2),
  );
}

function setDragDataForImageIds(event, imageIds) {
  if (!event?.dataTransfer) return;
  event.dataTransfer.setData(
    "application/json",
    JSON.stringify({
      type: "image-ids",
      imageIds,
    }),
  );
}

function handleThumbnailNativeDragStart(img, event) {
  dragSource.value = "grid";
  const selectionIds = getDragSelectionIds(img);
  if (selectionIds.length > 1) {
    setupMultiExportDrag(event, selectionIds);
    return;
  }
  const target = event?.target;
  if (target instanceof HTMLImageElement) {
    setDragImageFromElement(event, target);
  }
  setDragDataForImageIds(event, [img.id]);
}

function handleThumbnailNativeDragEnd(event) {
  dragSource.value = null;
}

function handleContainerDragStart(img, event) {
  if (!img || !event?.dataTransfer) return;
  if (event.target && event.target.closest?.(".face-bbox-overlay")) {
    return;
  }
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
    setDragImageFromElement(event, thumbEl);
  }
  if (isVideo(img)) {
    const previewEl = dragPreviewRefs[img.id];
    setDragImageFromElement(event, previewEl);
  }
  setDragDataForImageIds(event, [img.id]);
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
  const allGrid = allGridImages.value;
  const anchorIndex =
    lastSelectedImageId != null
      ? allGrid.findIndex(
          (item) => PictureId(item?.id) === PictureId(lastSelectedImageId),
        )
      : -1;
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
    lastSelectedImageId = img.id;
  } else if (isShift && anchorIndex >= 0) {
    // Range select: select only the contiguous range between anchor and clicked item
    const start = Math.min(anchorIndex, idx);
    const end = Math.max(anchorIndex, idx);
    newSelection = allGrid
      .slice(start, end + 1)
      .map((i) => i.id)
      .filter(Boolean);
    // Do NOT merge with previous selection; replace it
  } else if (isShift && anchorIndex < 0) {
    newSelection = [img.id];
    lastSelectedImageId = img.id;
  } else {
    // Single click (no ctrl/shift): select only this image
    newSelection = [img.id];
    lastSelectedImageId = img.id;
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
    lastSelectedImageId = null;
  }
}

// --- Text & Display Utilities ---

const gridContainer = ref(null);
const scrollWrapper = ref(null);

// updateColumns removed; columns is now controlled by prop

async function removeTagFromImage(imageId, tag) {
  if (!imageId) {
    console.error("Image ID is required to remove a tag.");
    return;
  }

  try {
    const tagId = getTagId(tag);
    if (tagId == null) {
      console.warn("Tag id is required to remove a tag.", tag);
      return;
    }
    const tagKey = String(tagId);
    await apiClient.delete(
      `${props.backendUrl}/pictures/${imageId}/tags/${tagKey}`,
    );
    const gridImg = allGridImages.value.find(
      (img) => img && img.id === imageId,
    );
    if (gridImg && Array.isArray(gridImg.tags)) {
      const d = TagList(gridImg.tags);
      gridImg.tags = d.filter((t) => !tagMatches(t, tag));
    }
    if (isSmartScoreSortActive()) {
      await refreshSmartScoreForImage(imageId);
    } else {
      refreshGridImage(imageId);
    }
  } catch (error) {
    console.error("Error removing tag:", error);
  }
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
    const responseTags = TagList(response?.data?.tags);
    const gridImg = allGridImages.value.find(
      (img) => img && img.id === imageId,
    );
    if (gridImg) {
      const current = TagList(gridImg.tags);
      const merged = responseTags.length
        ? responseTags
        : dedupeTagList([...current, { id: null, tag }]);
      gridImg.tags = merged;
    }
    if (isSmartScoreSortActive()) {
      await refreshSmartScoreForImage(imageId);
    } else {
      refreshGridImage(imageId);
    }
  } catch (error) {
    console.error("Error adding tag:", error);
  }
}

function updateDescriptionForImage(imageId, description) {
  const gridImg = allGridImages.value.find((img) => img && img.id === imageId);
  if (gridImg) {
    gridImg.description = description;
  }
  refreshGridImage(imageId);
}

onMounted(() => {
  window.addEventListener("keydown", handleKeyDown);
});

// Clear selection on ESC key
function handleKeyDown(event) {
  if (overlayOpen.value) return; // Ignore if overlay is open
  if (event.key === "Escape") {
    selectedImageIds.value = [];
    lastSelectedImageId = null;
    clearFaceSelection();
  } else if (event.key === "Delete" || event.key === "Backspace") {
    if (selectedImageIds.value.length > 0) {
      deleteSelected();
    }
  } else if ((event.ctrlKey || event.metaKey) && event.key === "a") {
    event.preventDefault();
    // Instrumentation: log allGridImages and selection
    const ids = allGridImages.value.map((img) => img && img.id);
    const validIds = ids.filter((id) => !!id);
    const placeholderCount = ids.length - validIds.length;
    // Select all images with valid IDs from allGridImages (not just visible)
    const allIds = allGridImages.value
      .filter((img) => img && img.id)
      .map((img) => img.id);
    selectedImageIds.value = Array.from(allIds);
    lastSelectedImageId = null;
  } else if (
    (hoveredImageIdx.value !== null || selectedImageIds.value.length > 0) &&
    !overlayOpen.value &&
    /^[1-5]$|^0$/.test(event.key)
  ) {
    // Number key pressed, set score for hovered image
    if (selectedImageIds.value.length > 0) {
      const score = parseInt(event.key, 10);
      const ids = selectedImageIds.value.slice();
      applyScoresForSelection(ids, score);
      event.preventDefault();
      return;
    }
    const idx = hoveredImageIdx.value;
    const img = allGridImages.value[idx];
    if (img && img.id) {
      let score = parseInt(event.key, 10);
      setScore(img, score);
      event.preventDefault();
    }
  }
}

watch(
  () => props.thumbnailSize,
  () => {
    // Recalculate visibleStart and visibleEnd after rowHeight update
    nextTick(() => {
      updateRowHeightFromGrid();
      const el = scrollWrapper.value;
      if (!el) return;
      let cardHeight = rowHeight.value;
      const scrollTop = el.scrollTop;
      const cols = props.columns;
      // First visible row (may be partially visible)
      const firstVisibleRow = scrollTop / cardHeight;
      // Last visible row (may be partially visible)
      const lastVisibleRow = (scrollTop + el.clientHeight - 1) / cardHeight;
      const newVisibleStart = Math.floor(firstVisibleRow) * cols;
      const newVisibleEnd = Math.ceil(lastVisibleRow) * cols;
      visibleStart.value = newVisibleStart;
      visibleEnd.value = newVisibleEnd;
      updateVisibleThumbnails(); // test
    });
  },
);

onUnmounted(() => {
  window.removeEventListener("keydown", handleKeyDown);
});

// Expose the grid DOM node to parent
defineExpose({
  gridEl: scrollWrapper,
  onGlobalKeyPress,
  updateVisibleThumbnails,
  exportCurrentViewToZip,
  getExportCount,
  removeImagesById,
  clearFaceSelection,
});

function reindexGridImages() {
  const items = allGridImages.value.slice();
  for (let i = 0; i < items.length; i += 1) {
    const current = items[i];
    if (!current) continue;
    if (current.idx !== i) {
      items[i] = { ...current, idx: i };
    }
  }
  allGridImages.value = items;
}

// Remove images by ID (for event-driven removal)
function removeImagesById(imageIds) {
  if (!Array.isArray(imageIds) || !imageIds.length) {
    console.log("No image IDs provided for removal.");
    return;
  }
  console.log("Removing images by ID:", imageIds);
  const dIds = new Set(
    imageIds.map((id) => PictureId(id)).filter((id) => id !== null),
  );
  allGridImages.value = allGridImages.value.filter(
    (img) => !dIds.has(PictureId(img?.id)),
  );
  selectedImageIds.value = selectedImageIds.value.filter(
    (id) => !dIds.has(PictureId(id)),
  );
  reindexGridImages();
  resetThumbnailState();
  updateVisibleThumbnails();
}

function getExportCount() {
  const selectedCount = selectedImageIds.value.length;
  const totalCount = allGridImages.value.filter((img) => img && img.id).length;
  return { selectedCount, totalCount };
}

// --- Export to Zip ---
function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function exportCurrentViewToZip(options = {}) {
  const exportType = options.exportType || "full";
  const captionMode = options.captionMode || "description";
  const includeCharacterName = options.includeCharacterName !== false;
  const resolution = options.resolution || "original";
  let url = `${props.backendUrl}/pictures/export`;
  const params = buildPictureIdsQueryParams();
  const extraParams = new URLSearchParams();
  if (exportType) {
    extraParams.append("export_type", exportType);
  }
  if (captionMode) {
    extraParams.append("caption_mode", captionMode);
  }
  if (includeCharacterName) {
    extraParams.append("include_character_name", "true");
  }
  if (resolution) {
    extraParams.append("resolution", resolution);
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
    exportProgress.cancelRequested = false;

    const startRes = await apiClient.get(url);
    const taskId = startRes?.data?.task_id;
    if (!taskId) {
      throw new Error("Missing task_id from export response.");
    }

    let downloadUrl = null;
    const maxAttempts = 600;
    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
      if (exportProgress.cancelRequested) {
        exportProgress.status = "cancelled";
        exportProgress.message = "Export cancelled.";
        exportProgress.visible = false;
        return;
      }
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

    if (exportProgress.cancelRequested) {
      exportProgress.status = "cancelled";
      exportProgress.message = "Export cancelled.";
      exportProgress.visible = false;
      return;
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

function abortExportZip() {
  if (!exportProgress.visible) return;
  exportProgress.cancelRequested = true;
}

// Search functionality
const searchQuery = ref(props.searchQuery);

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
  gridReady.value = false;
  emptyStateDelayPassed.value = false;
  emit("reset-to-all");
}
</script>

<style scoped>
.drag-overlay {
  position: sticky;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background: rgba(var(--v-theme-accent), 0.2);
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none;
  border: 8px solid rgb(var(--v-theme-accent));
  border-radius: 16px; /* rounded corners */
  box-sizing: border-box;
  transition:
    border-color 0.2s,
    background 0.2s;
  color: rgb(var(--v-theme-on-accent));
  font-size: 3em;
  font-weight: bold;
}

.drag-overlay-message {
  padding: 6px 14px;
  background: rgba(var(--v-theme-shadow), 0.35);
  border-radius: 12px;
}

.export-progress {
  position: absolute;
  top: 10px;
  right: 12px;
  z-index: 120;
  background: rgba(var(--v-theme-dark-surface), 0.9);
  color: rgb(var(--v-theme-on-dark-surface));
  padding: 10px 12px;
  border-radius: 8px;
  min-width: 220px;
  box-shadow: 0 4px 14px rgba(var(--v-theme-shadow), 0.3);
}

.export-progress-error {
  background: rgba(var(--v-theme-error), 0.95);
}

.export-progress-title {
  font-size: 0.9em;
  margin-bottom: 8px;
}

.export-progress-bar {
  width: 100%;
  height: 8px;
  background: rgba(var(--v-theme-on-dark-surface), 0.15);
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

.export-progress-abort {
  margin-top: 10px;
  width: 100%;
  background: rgb(var(--v-theme-error));
  color: rgb(var(--v-theme-on-error));
  border: none;
  border-radius: 6px;
  padding: 6px 10px;
  font-size: 0.85em;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s ease;
}

.export-progress-abort:hover {
  background: rgba(var(--v-theme-error), 0.85);
}

.thumbnail-badge {
  background: rgba(var(--v-theme-dark-surface), 0.65);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.3);
  border-radius: 6px;
  color: rgb(var(--v-theme-on-dark-surface));
  box-shadow: 0 2px 6px rgba(var(--v-theme-shadow), 0.3);
  font-size: 0.8em;
  padding: 2px 4px;
  z-index: 20;
  max-width: 90%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.thumbnail-badge--top-left {
  position: absolute;
  top: 2px;
  left: 2px;
}

.thumbnail-badge--top-right {
  position: absolute;
  top: 2px;
  right: 2px;
}

.thumbnail-badge--bottom-left {
  position: absolute;
  left: 2px;
  bottom: 2px;
}

.thumbnail-badge--bottom-right {
  position: absolute;
  right: 2px;
  bottom: 2px;
}
.face-bbox-label {
  font-size: 0.7em;
  background-color: rgba(var(--v-theme-shadow), 0.3);
  color: rgb(var(--v-theme-on-surface));
  text-overflow: ellipsis;
  overflow-y: hidden;
  overflow-x: hidden;
  white-space: nowrap;
}

.hand-bbox-overlay {
  box-sizing: border-box;
  position: absolute;
  pointer-events: none;
  border: 2px dashed rgb(var(--v-theme-tertiary));
  display: block;
  z-index: 30;
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
  border: 1px dashed rgba(var(--v-theme-border), 0.5);
  background: rgba(var(--v-theme-panel), 0.72);
  color: rgb(var(--v-theme-on-background));
  text-align: center;
  max-width: 420px;
  box-shadow: 0 10px 30px rgba(var(--v-theme-shadow), 0.08);
  pointer-events: auto;
}
.empty-state-illustration {
  color: rgba(var(--v-theme-on-panel), 0.45);
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
  background: rgba(var(--v-theme-shadow), 0.15);
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
  background: rgba(var(--v-theme-info), 0.62);
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
.thumbnail-info-row {
  margin-top: 0;
  text-align: center;
  height: 24px;
  min-height: 24px;
  max-height: 24px;
  overflow: hidden;
  background: none;
  width: 100%;
}
.thumbnail-info {
  font-size: 0.95em;
  color: rgb(var(--v-theme-on-background));
  text-align: center;
  line-height: 24px;
  display: block;
  width: 100%;
  max-width: 100%;
  padding: 0 8px;
  white-space: nowrap;
  overflow: hidden;
  text-shadow: 1px 1px 1px rgba(var(--v-theme-shadow), 0.2);
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
  object-position: top center;
  display: block;
  border-radius: 8px;
  position: absolute;
  top: 0;
  left: 0;
  z-index: 1;
  box-shadow: 1px 2px 3px 3px rgba(var(--v-theme-shadow), 0.3);
  transition:
    transform 0.18s cubic-bezier(0.4, 2, 0.6, 1),
    box-shadow 0.18s;
}
.thumbnail-container:hover .thumbnail-img,
.thumbnail-container:focus-within .thumbnail-img {
  box-shadow: 2px 4px 12px rgba(var(--v-theme-shadow), 0.6);
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
  padding: 8px;
}

.thumbnail-card-new {
  animation: gridNewPulse 2.2s ease-out;
  box-shadow: 0 0 0 rgba(var(--v-theme-accent), 0);
}

@keyframes gridNewPulse {
  0% {
    transform: translateZ(0) scale(1);
    box-shadow: 0 0 0 rgba(var(--v-theme-accent), 0);
  }
  35% {
    transform: translateZ(0) scale(1.015);
    box-shadow:
      0 0 10px rgba(var(--v-theme-accent), 0.5),
      0 0 18px rgba(var(--v-theme-accent), 0.25);
  }
  100% {
    transform: translateZ(0) scale(1);
    box-shadow: 0 0 0 rgba(var(--v-theme-accent), 0);
  }
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

.penalised-tag-indicator {
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 30;
  pointer-events: auto;
  padding: 2px;
}

.thumbnail-placeholder {
  width: 100%;
  height: 100%;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  position: absolute;
  top: 0;
  left: 0;
  color: rgb(var(--v-theme-on-background));
}

.thumbnail-placeholder-icon {
  font-size: 28px;
  opacity: 0.7;
  animation: thumbnailPlaceholderSpin 1.1s linear infinite;
}

@keyframes thumbnailPlaceholderSpin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}
</style>
