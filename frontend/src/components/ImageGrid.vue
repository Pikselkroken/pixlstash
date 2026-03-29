<template>
  <ImageOverlay
    :open="overlayOpen"
    :initialImageId="overlayImageId"
    :initialExpandedStackIds="overlayInitialExpandedStackIds"
    :allImages="allGridImages"
    :backendUrl="props.backendUrl"
    :tagUpdate="props.wsTagUpdate"
    :hiddenTags="props.hiddenTags"
    :applyTagFilter="props.applyTagFilter"
    :dateFormat="props.dateFormat"
    :showStacks="props.showStacks"
    :showProblemIcon="props.showProblemIcon"
    :availablePlugins="availablePlugins"
    :comfyuiProgress="comfyuiProgress"
    :comfyuiProgressPercent="comfyuiProgressPercent"
    :pluginProgress="pluginProgress"
    :pluginProgressPercent="pluginProgressPercent"
    :comfyuiClientId="comfyuiClientId"
    :comfyuiConfigured="props.comfyuiConfigured"
    @close="closeOverlay"
    @apply-score="applyScore"
    @add-tag="addTagToImage"
    @remove-tag="removeTagFromImage"
    @update-description="updateDescriptionForImage"
    @overlay-change="handleOverlayChange"
    @added-to-set="handleOverlayAddedToSet"
    @set-project="handleSetProjectForSelected"
    @comfyui-run="handleComfyuiRun"
    @run-plugin="handlePluginRunRequest"
  />
  <ImageImporter
    ref="imageImporterRef"
    :backendUrl="props.backendUrl"
    :selectedCharacterId="props.selectedCharacter"
    :allPicturesId="props.allPicturesId"
    :unassignedPicturesId="props.unassignedPicturesId"
    @import-started="handleImportStarted"
    @import-finished="handleImagesUploaded"
    @import-cancelled="handleImportCancelled"
    @import-error="handleImportErrored"
  />
  <div :style="wrapperStyle">
    <SelectionBar
      ref="selectionBarRef"
      v-if="showSelectionBar"
      :selectedCount="selectedImageIds.length"
      :selectedExpandedCount="selectedExpandedCount"
      :selectedFaceCount="selectedFaceIds.length"
      :selectedCharacter="String(props.selectedCharacter)"
      :selectedSet="String(props.selectedSet)"
      :selectedGroupName="selectedGroupName"
      :selectedSort="props.selectedSort"
      :allPicturesId="String(props.allPicturesId)"
      :unassignedPicturesId="String(props.unassignedPicturesId)"
      :scrapheapPicturesId="String(props.scrapheapPicturesId)"
      :backend-url="props.backendUrl"
      :selected-image-ids="selectedImageIds"
      :selected-media-support="selectedMediaSupport"
      :comfyui-client-id="comfyuiClientId"
      :comfyui-configured="props.comfyuiConfigured"
      :available-plugins="availablePlugins"
      :show-remove-from-stack="showRemoveFromStack"
      :visible="showSelectionBar"
      @clear-selection="clearSelection"
      @added-to-set="handleOverlayAddedToSet"
      @remove-from-group="removeFromGroup"
      @delete-selected="deleteSelected"
      @set-project="handleSetProjectForSelected"
      @add-to-character="handleAddToCharacter"
      @create-stack="createStackFromSelection"
      @remove-from-stack="removeSelectedFromStack"
      @create-stacks-from-groups="createStacksFromSelectedGroups"
      @run-plugin="handlePluginRunRequest"
      @comfyui-run="handleComfyuiRun"
      @tags-applied="fetchAllGridImages({ force: true })"
    />
    <EmptyScrapHeap
      v-if="showScrapheapBar"
      :visible="showScrapheapBar"
      :disabled="scrapheapEmptyDisabled"
      :restoreDisabled="scrapheapRestoreDisabled"
      @empty-scrapheap="confirmEmptyScrapheap"
      @restore-scrapheap="confirmRestoreScrapheap"
    />
    <div v-if="isSetOverlapView" class="set-overlap-status-bar">
      <div class="set-overlap-status-bar__main">
        <v-icon size="20" class="set-overlap-status-bar__icon"
          >mdi-set-center</v-icon
        >
        <span class="set-overlap-status-bar__text">
          Overlap mode: {{ normalizedSelectedSetIds.length }} sets selected.
          Overlap can be cleared with the Set menu once you've selected images.
        </span>
      </div>
    </div>
    <ProgressOverlay
      :visible="exportProgress.visible"
      :status="exportProgress.status"
      :message="exportProgress.message"
      :percent="exportProgressPercent"
      :count="exportProgress.processed"
      :total="exportProgress.total"
      :abort-label="
        !['completed', 'failed', 'cancelled'].includes(exportProgress.status)
          ? 'Abort'
          : null
      "
      anchor="top"
      @abort="abortExportZip"
    />
    <ComfyUiRunner
      ref="comfyuiRunner"
      :backendUrl="props.backendUrl"
      :overlayOpen="overlayOpen"
      :overlayImageId="overlayImageId"
      :allGridImages="allGridImages"
      :lastFetchedGridImages="lastFetchedGridImages"
      :getPictureStackId="getPictureStackId"
      :selectNewestStackMember="selectNewestStackMember"
      @refresh-grid="onComfyuiRefreshGrid"
      @refresh-sidebar="emit('refresh-sidebar')"
      @update:overlayImageId="
        (id) => {
          overlayImageId.value = id;
        }
      "
    />

    <ProgressOverlay
      :visible="pluginProgress.visible"
      :status="pluginProgress.status"
      :message="pluginProgress.message"
      :percent="pluginProgressPercent"
      :count="pluginProgress.current"
      :total="pluginProgress.total"
      anchor="bottom"
    />

    <ProgressOverlay
      :visible="smartScoreLoadingVisible"
      status="running"
      :message="smartScoreProgressMessage"
      :percent="smartScoreProgressPercent"
      :indeterminate="false"
      anchor="top"
    />

    <div
      class="grid-scroll-wrapper"
      ref="scrollWrapper"
      @scroll="onGridScroll"
      @dragenter.prevent="handleGridDragEnter"
      @dragover.prevent="handleGridDragOver"
      @dragleave.prevent="handleGridDragLeave"
      @drop.capture.prevent="handleGridDrop"
      :style="scrollWrapperStyle"
    >
      <!-- Compact mode sticky current-group label -->
      <div
        v-if="props.compactMode && compactStickyLabel"
        class="compact-sticky-label"
      >
        {{ compactStickyLabel }}
      </div>
      <div v-if="dragOverlayVisible" class="drag-overlay">
        <div class="drag-overlay-message">{{ dragOverlayMessage }}</div>
      </div>
      <div v-if="showEmptyState" class="empty-state">
        <div class="empty-state-card">
          <div class="empty-state-illustration" aria-hidden="true">
            <img
              :src="emptyStateImage"
              :alt="emptyStateAlt"
              :style="emptyStateImageStyle"
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
        :class="['image-grid', { 'compact-mode': props.compactMode }]"
        :style="{
          gridTemplateColumns: `repeat(${props.columns}, minmax(0, ${MAX_THUMBNAIL_SIZE}px))`,
          position: 'relative',
        }"
        ref="gridContainer"
        @click="handleGridBackgroundClick"
      >
        <!-- Top spacer for virtual scroll alignment -->
        <div
          v-if="topSpacerHeight > 0"
          :style="{
            gridColumn: '1 / -1',
            height: `${topSpacerHeight}px`,
          }"
        ></div>
        <div
          v-for="(img, idx) in gridImagesToRender"
          :key="img.id ? `img-${img.id}-${img.idx}` : `placeholder-${img.idx}`"
          :style="getStackCardStyle(img)"
          :class="[
            'image-card',
            {
              'image-card-stack-expanded': isStackExpandedForImage(img),
              'image-card-stack-reorder-target': isStackReorderTarget(img),
              'image-card-stack-reorder-left': isStackReorderTargetSide(
                img,
                'left',
              ),
              'image-card-stack-reorder-right': isStackReorderTargetSide(
                img,
                'right',
              ),
              'stack-hover-active':
                hoveredStackId !== null &&
                getPictureStackId(img) === hoveredStackId,
              'image-card-cursor': img.idx === cursorIdx,
            },
          ]"
          @click="handleImageCardClick(img, img.idx, $event)"
          @mouseenter="handleImageMouseEnter(img)"
          @mouseleave="handleImageMouseLeave(img)"
        >
          <div
            :class="[
              'thumbnail-card',
              { 'thumbnail-card-new': isImageRecentlyAdded(img.id) },
            ]"
            @click.stop="handleThumbnailClick(img, img.idx, $event)"
          >
            <div
              :class="[
                'thumbnail-container',
                { 'thumbnail-container-drag-source': isDragSourceImage(img) },
              ]"
              :ref="(el) => setThumbnailContainerRef(img.id, el)"
              draggable="true"
              @dragstart.capture="handleContainerDragStart(img, $event)"
              @dragend.capture="handleDragEnd"
              @dragover="handleStackReorderDragOver(img, $event)"
              @drop="handleStackReorderDrop(img, $event)"
              @dragleave="handleStackReorderDragLeave(img, $event)"
            >
              <div
                v-if="
                  shouldShowStackBadge(img) &&
                  isThumbnailReady(img.id) &&
                  img.thumbnail
                "
                :class="[
                  'stack-indicator',
                  'thumbnail-badge',
                  'thumbnail-badge--top-left',
                ]"
                :title="stackBadgeTitle(img)"
                @click.stop="toggleStackExpand(img)"
                @mouseenter.stop="prefetchStackMembers(img)"
              >
                <v-icon size="18" :style="getStackBadgeIconStyle(img)"
                  >mdi-layers-outline</v-icon
                >
              </div>
              <div
                v-if="
                  props.showProblemIcon &&
                  hasPenalisedTags(img) &&
                  isThumbnailReady(img.id) &&
                  img.thumbnail
                "
                :class="[
                  'penalised-tag-indicator',
                  'thumbnail-badge',
                  shouldShowStackBadge(img)
                    ? 'thumbnail-badge--top-left-stack'
                    : 'thumbnail-badge--top-left',
                ]"
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
              <template
                v-if="
                  getThumbnailSrc(img) &&
                  isVideo(img) &&
                  getVideoThumbnailSrc(img)
                "
              >
                <video
                  class="thumbnail-img"
                  :src="getVideoThumbnailSrc(img)"
                  :poster="getThumbnailSrc(img)"
                  :ref="
                    (el) => {
                      setVideoRef(img.id, el);
                      setThumbnailRef(img.id, el);
                    }
                  "
                  preload="none"
                  draggable="false"
                  @pointerdown="prepareThumbnailNativeDrag(img, $event)"
                  @pointerup="handleThumbnailPointerRelease($event)"
                  @pointercancel="handleThumbnailPointerRelease($event)"
                  @loadeddata="onThumbnailLoad(img.id, $event)"
                  muted
                  loop
                  playsinline
                  @mouseenter="playVideo(img.id)"
                  @mouseleave="pauseVideo(img.id)"
                ></video>
                <img
                  class="thumbnail-drag-preview"
                  :src="getThumbnailSrc(img)"
                  :ref="(el) => setDragPreviewRef(img.id, el)"
                  alt=""
                />
              </template>
              <template v-else-if="getThumbnailSrc(img)">
                <img
                  :src="getThumbnailSrc(img)"
                  class="thumbnail-img"
                  :ref="(el) => setThumbnailRef(img.id, el)"
                  loading="eager"
                  fetchpriority="high"
                  decoding="async"
                  draggable="true"
                  @pointerdown="prepareThumbnailNativeDrag(img, $event)"
                  @pointerup="handleThumbnailPointerRelease($event)"
                  @pointercancel="handleThumbnailPointerRelease($event)"
                  @dragstart="handleThumbnailNativeDragStart(img, $event)"
                  @dragend="handleDragEnd"
                  @error="handleImageError"
                  @load="onThumbnailLoad(img.id, $event)"
                />
                <img
                  v-if="isVideo(img)"
                  class="thumbnail-drag-preview"
                  :src="getThumbnailSrc(img)"
                  :ref="(el) => setDragPreviewRef(img.id, el)"
                  alt=""
                />
                <!-- Face bounding box overlays: must be rendered after the image for correct stacking -->
                <template v-if="isThumbnailReady(img.id) && img.thumbnail">
                  <div
                    v-for="overlay in getFaceBboxOverlays(img)"
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
              </template>
              <template v-else>
                <div class="thumbnail-placeholder">
                  <v-icon class="thumbnail-placeholder-icon"
                    >mdi-loading</v-icon
                  >
                </div>
              </template>
              <!-- Stack band overlay (top+bottom color stripe for compact mode) -->
              <div
                v-if="getStackBandStyle(img)"
                class="stack-band-overlay"
                :style="getStackBandStyle(img)"
              ></div>
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
            <!-- Compact mode group pill, straddling top edge of this row -->
            <div
              v-if="
                props.compactMode && getCompactGroupLabel(img, idx) !== null
              "
              class="compact-group-label"
            >
              {{ getCompactGroupLabel(img, idx) }}
            </div>
          </div>
          <div v-if="isImageSelected(img.id)" class="selection-overlay"></div>
          <!-- Info row absolutely positioned below thumbnail -->
          <div v-if="!props.compactMode" class="thumbnail-info-row">
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
  extractSupportedImportFilesFromDataTransfer,
  isFileDrag,
  isSupportedImageFile,
  isSupportedVideoFile,
  isVideo,
  getPictureId,
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
import ComfyUiRunner from "./ComfyUiRunner.vue";
import ProgressOverlay from "./ProgressOverlay.vue";
import { apiClient } from "../utils/apiClient";
import {
  applyStackBackgroundAlpha,
  arraysEqualByString,
  faceBoxColor,
  formatUserDate,
  getInfoFont,
  getStackColor,
  getStackColorIndexFromId,
  isRangeOverlap,
  normalizePluginProgressMessage,
  rangeCovers,
  shiftRangesForDelta,
  sleep,
  getStackThreshold,
  toggleScore,
} from "../utils/utils.js";
import {
  dedupeTagList,
  getTagId,
  hasPenalisedTags,
  penalisedTagsTitle,
  getTagList,
  tagMatches,
} from "../utils/tags.js";
import {
  applyStackOrderToList,
  buildStackLeaderMap,
  buildStackReorderedMembers,
  getStackBadgeCount,
  getPictureStackId,
  normalizeStackIdValue,
  selectNewestStackMember,
  shouldShowStackBadge,
  sortStackMembers,
  stackBadgeTitle,
} from "../utils/stack.js";
import { debounce } from "lodash-es";

const emit = defineEmits([
  "open-overlay",
  "refresh-sidebar",
  "clear-search",
  "reset-to-all",
  "search-all",
  "update:selected-sort",
  "update:stack-stats",
]);

// Props
const props = defineProps({
  thumbnailSize: Number,
  sidebarVisible: Boolean,
  backendUrl: String,
  selectedCharacter: { type: [String, Number, null], default: null },
  selectedSet: { type: [Number, String, null], default: null },
  selectedSetIds: { type: Array, default: () => [] },
  searchQuery: String,
  activeCategoryLabel: { type: String, default: "Category" },
  isAllPicturesActive: { type: Boolean, default: false },
  selectedSort: String,
  selectedDescending: Boolean,
  similarityCharacter: { type: [String, Number, null], default: null },
  stackThreshold: { type: [String, Number, null], default: null },
  showStars: Boolean,
  showFaceBboxes: Boolean,
  showFormat: Boolean,
  showResolution: Boolean,
  showProblemIcon: Boolean,
  showStacks: { type: Boolean, default: true },
  compactMode: { type: Boolean, default: false },
  themeMode: { type: String, default: "light" },
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
  wsPluginProgress: {
    type: Object,
    default: () => ({ key: 0, payload: null }),
  },
  mediaTypeFilter: { type: String, default: "all" },
  comfyuiModelFilter: { type: Array, default: () => [] },
  comfyuiLoraFilter: { type: Array, default: () => [] },
  comfyuiConfigured: { type: Boolean, default: false },
  minScoreFilter: { type: Number, default: null },
  columns: { type: Number, required: true },
  hiddenTags: { type: Array, default: () => [] },
  applyTagFilter: { type: Boolean, default: false },
  projectViewMode: { type: String, default: "global" },
  selectedProjectId: { type: Number, default: null },
});

// ============================================================
// CONSTANTS
// ============================================================
const STACKS_SORT_KEY = "PICTURE_STACKS";
const MIN_THUMBNAIL_SIZE = 128;
const MAX_THUMBNAIL_SIZE = 384;
const THUMBNAIL_INFO_ROW_HEIGHT = 24;

const normalizedSelectedSetIds = computed(() => {
  const idsFromProp = Array.isArray(props.selectedSetIds)
    ? props.selectedSetIds
    : [];
  const normalized = idsFromProp
    .map((id) => Number(id))
    .filter((id) => Number.isFinite(id) && id > 0);
  if (normalized.length > 0) {
    return Array.from(new Set(normalized));
  }
  const single = Number(props.selectedSet);
  if (Number.isFinite(single) && single > 0) {
    return [single];
  }
  return [];
});

const hasSetSelection = computed(
  () => normalizedSelectedSetIds.value.length > 0,
);
const isSetOverlapView = computed(
  () => normalizedSelectedSetIds.value.length > 1,
);
const primarySelectedSetId = computed(() =>
  normalizedSelectedSetIds.value.length
    ? normalizedSelectedSetIds.value[0]
    : null,
);

// ============================================================
// THUMBNAIL SYSTEM STATE
// ============================================================
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
const thumbnailAssignedAtMap = reactive({});

const THUMBNAIL_RETRY_DELAY_MS = 10000;
const THUMBNAIL_RETRY_LIMIT = 1;
const thumbnailRetryTimers = new Map();
const thumbnailRetryCounts = reactive({});
const PREFETCHED_FULL_IMAGE_LIMIT = 12;
const fullImagePrefetchControllers = new Map();
const prefetchedFullImageIds = new Set();
const prefetchedFullImageOrder = [];

// ============================================================
// DOM ELEMENT REFS
// ============================================================
const gridContainer = ref(null);
const scrollWrapper = ref(null);
const selectionBarRef = ref(null);

// ============================================================
// GRID DATA STATE
// ============================================================
const allGridImages = ref([]);
const lastFetchedGridImages = ref([]);
// Maps stack_id → { index, row, col } where:
//   index = sequential appearance order of the stack in the grid (drives hue)
//   row   = grid row of the stack's first image (drives lightness)
//   col   = grid column of the stack's first image (drives saturation slightly)
const stackVisualOrderMap = computed(() => {
  const images = allGridImages.value;
  const cols = Math.max(1, props.columns || 1);

  const result = new Map();
  let stackAppearanceIndex = 0;
  for (let i = 0; i < images.length; i++) {
    const sid = getPictureStackId(images[i]);
    if (sid != null && !result.has(sid)) {
      const row = Math.floor(i / cols);
      const col = i % cols;
      result.set(sid, { index: stackAppearanceIndex, row, col });
      stackAppearanceIndex++;
    }
  }
  return result;
});
const expandedStackIds = ref(new Set());
const expandedStackMembers = ref(new Map());
const expandedStackLoading = ref(new Set());
const expandedStackLoadPromises = new Map();
const stackReorderDrag = ref(null);

// ============================================================
// PLUGIN / EXPORT STATE
// ============================================================
const exportProgress = reactive({
  visible: false,
  status: "idle",
  processed: 0,
  total: 0,
  message: "",
  cancelRequested: false,
});

const pluginProgress = reactive({
  visible: false,
  status: "idle",
  current: 0,
  total: 0,
  percent: 0,
  message: "",
  runId: "",
});
let pluginProgressHideTimer = null;

const smartScoreProgress = reactive({
  visible: false,
  percent: 0,
  message: "Calculating smart scores",
});
const SORT_PROGRESS_ESTIMATE_DEFAULT_MS = 2500;
const SORT_PROGRESS_ESTIMATE_SMART_SCORE_MS = 9000;
const SORT_PROGRESS_ESTIMATE_MIN_MS = 900;
const SORT_PROGRESS_ESTIMATE_MAX_MS = 45000;
const SORT_PROGRESS_EWMA_ALPHA = 0.25;
const SORT_PROGRESS_MAX_BEFORE_DONE = 97;
const SORT_PROGRESS_COMPLETION_HOLD_MS = 220;
const SORT_PROGRESS_WARM_RESTART_WINDOW_MS = 1200;
const sortEstimatedDurationMsByKey = reactive({});
let smartScoreProgressTimer = null;
let smartScoreProgressLoadId = 0;
let smartScoreProgressStartedAt = 0;
const smartScoreProgressSortKey = ref("");

const availablePlugins = ref([]);

async function fetchAvailablePlugins() {
  if (!props.backendUrl) {
    availablePlugins.value = [];
    return;
  }
  try {
    const res = await apiClient.get(`${props.backendUrl}/pictures/plugins`);
    const plugins = Array.isArray(res.data?.plugins) ? res.data.plugins : [];
    availablePlugins.value = plugins.filter((plugin) => plugin && plugin.name);
  } catch (err) {
    console.warn("Failed to load image plugins:", err);
    availablePlugins.value = [];
  }
}

function handlePluginRunRequest(payload) {
  const pluginName = String(payload?.pluginName || "").trim();
  const pictureIds = Array.isArray(payload?.pictureIds)
    ? payload.pictureIds
        .map((id) => Number(id))
        .filter((id) => Number.isFinite(id) && id > 0)
    : [];
  const parameters =
    payload?.parameters && typeof payload.parameters === "object"
      ? payload.parameters
      : {};
  if (!pluginName || !pictureIds.length) return;
  // Build per-image captions from stored descriptions in the grid.
  const idSet = new Set(pictureIds);
  const idToDesc = new Map();
  for (const img of allGridImages.value) {
    const id = Number(img?.id);
    if (idSet.has(id)) idToDesc.set(id, img.description || "");
  }
  const captions = pictureIds.map((id) => idToDesc.get(id) ?? "");
  runPluginWithParameters(pluginName, pictureIds, parameters, captions);
}

async function runPluginWithParameters(
  pluginName,
  pictureIds,
  parameters,
  captions,
) {
  if (!pluginName || !Array.isArray(pictureIds) || !pictureIds.length) return;
  try {
    const res = await apiClient.post(
      `${props.backendUrl}/pictures/plugins/${encodeURIComponent(pluginName)}`,
      {
        picture_ids: pictureIds,
        parameters: parameters || {},
        captions: Array.isArray(captions) ? captions : undefined,
      },
    );
    const createdIds = Array.isArray(res.data?.created_picture_ids)
      ? res.data.created_picture_ids
      : [];
    if (createdIds.length) {
      const newIds = createdIds
        .map((id) => getPictureId(id))
        .filter((id) => id != null);
      if (newIds.length) {
        triggerNewImageHighlight(newIds);
        if (overlayOpen.value) {
          overlayImageId.value = newIds[newIds.length - 1];
        }
      }
    }
    preserveScrollOnNextFetch.value = true;
    debouncedFetchAllGridImages();
    if (overlayOpen.value && pictureIds.length) {
      const refreshId =
        createdIds.length > 0
          ? createdIds[createdIds.length - 1]
          : pictureIds[0];
      await refreshGridImage(refreshId, { force: true });
    }
  } catch (err) {
    console.error("Failed to run plugin:", err);
    alert(err?.response?.data?.detail || err?.message || String(err));
  }
}

const pluginProgressPercent = computed(() => {
  const percent = Number(pluginProgress.percent) || 0;
  return Math.min(100, Math.max(0, Math.round(percent)));
});

const smartScoreProgressPercent = computed(() => {
  const percent = Number(smartScoreProgress.percent) || 0;
  return Math.min(100, Math.max(0, Math.round(percent)));
});

const smartScoreProgressMessage = computed(
  () => smartScoreProgress.message || "Calculating smart scores",
);

function getActiveSortKey() {
  if (typeof props.selectedSort !== "string") return "";
  return props.selectedSort.trim().toUpperCase();
}

function getSortProgressLabel(sortKey) {
  const key = String(sortKey || "").toUpperCase();
  if (!key) return "results";
  if (key.includes("SMART_SCORE")) return "smart score";
  if (key.includes("CHARACTER_LIKENESS")) return "character likeness";
  if (key === "TEXT_CONTENT") return "text content";
  if (key === "SCORE") return "score";
  if (key === STACKS_SORT_KEY) return "stacks";
  return key.replace(/_/g, " ").toLowerCase();
}

function getSortEstimateDefaultMs(sortKey) {
  const key = String(sortKey || "").toUpperCase();
  if (key.includes("SMART_SCORE")) return SORT_PROGRESS_ESTIMATE_SMART_SCORE_MS;
  return SORT_PROGRESS_ESTIMATE_DEFAULT_MS;
}

const exportProgressPercent = computed(() => {
  if (!exportProgress.total) return 0;
  const percent = (exportProgress.processed / exportProgress.total) * 100;
  return Math.min(100, Math.max(0, Math.round(percent)));
});

// ============================================================
// RECENTLY ADDED / WS STATE
// ============================================================
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
const pauseGridAutoUpdates = ref(false);
const pendingGridRefreshAfterImport = ref(false);

// ============================================================
// FACE BBOX STATE
// ============================================================
// Key to force face bbox overlay recompute.
const faceOverlayRedrawKey = ref(0);
let gridResizeObserver = null;

function triggerFaceOverlayRedraw() {
  faceOverlayRedrawKey.value++;
}

// ============================================================
// COMFYUI
// ============================================================
const comfyuiRunner = ref(null);

const comfyuiClientId = computed(
  () => comfyuiRunner.value?.clientId?.value ?? null,
);
const comfyuiProgress = computed(
  () =>
    comfyuiRunner.value?.progress ?? {
      visible: false,
      status: "idle",
      percent: 0,
      message: "",
    },
);
const comfyuiProgressPercent = computed(() => {
  const p = comfyuiProgress.value;
  return Math.min(100, Math.max(0, Math.round(Number(p?.percent) || 0)));
});

function handleComfyuiRun(payload) {
  comfyuiRunner.value?.handleComfyuiRun(payload);
}

async function runComfyuiOnGridImages({
  workflowName,
  caption = "",
  seedMode = "random",
  seed = 0,
} = {}) {
  if (!workflowName || !props.backendUrl) return;
  try {
    const payload = {
      workflow_name: workflowName,
      caption: caption || "",
      client_id: comfyuiClientId.value || undefined,
      seed_mode: seedMode,
      seed: seedMode === "fixed" ? seed : undefined,
    };
    const res = await apiClient.post(
      `${props.backendUrl}/comfyui/run_t2i`,
      payload,
    );
    const prompts = Array.isArray(res.data?.prompts) ? res.data.prompts : [];
    handleComfyuiRun({ prompts });
  } catch (err) {
    console.error("ComfyUI T2I run failed:", err);
  }
}

function onComfyuiRefreshGrid({ preserveScroll } = {}) {
  if (preserveScroll) preserveScrollOnNextFetch.value = true;
  debouncedFetchAllGridImages();
}

function getNowMs() {
  return typeof performance !== "undefined" ? performance.now() : Date.now();
}

function clampSmartScoreEstimate(ms) {
  const value = Number(ms);
  if (!Number.isFinite(value)) return SORT_PROGRESS_ESTIMATE_DEFAULT_MS;
  return Math.max(
    SORT_PROGRESS_ESTIMATE_MIN_MS,
    Math.min(SORT_PROGRESS_ESTIMATE_MAX_MS, value),
  );
}

function easeEstimatedProgress(ratio) {
  const x = Math.max(0, Math.min(1, ratio));
  return 1 - Math.pow(1 - x, 1.35);
}

function stopSmartScoreProgressTimer() {
  if (smartScoreProgressTimer) {
    clearInterval(smartScoreProgressTimer);
    smartScoreProgressTimer = null;
  }
}

function setSmartScoreProgressPercent(
  nextPercent,
  { allowReset = false } = {},
) {
  const parsed = Number(nextPercent);
  const clamped = Number.isFinite(parsed)
    ? Math.max(0, Math.min(100, parsed))
    : 0;
  if (allowReset) {
    smartScoreProgress.percent = clamped;
    return;
  }
  // Monotonic guard: progress must never move backwards within a run.
  smartScoreProgress.percent = Math.max(smartScoreProgress.percent, clamped);
}

function startSmartScoreProgress(loadId, sortKey) {
  stopSmartScoreProgressTimer();
  const now = getNowMs();
  const incomingSortKey = String(sortKey || "").toUpperCase();
  const sameSortAsCurrent =
    smartScoreProgress.visible &&
    incomingSortKey &&
    incomingSortKey === smartScoreProgressSortKey.value;
  const rapidRestart =
    sameSortAsCurrent &&
    now - smartScoreProgressStartedAt <= SORT_PROGRESS_WARM_RESTART_WINDOW_MS;

  smartScoreProgressLoadId = Number(loadId) || 0;
  smartScoreProgressSortKey.value = incomingSortKey;
  smartScoreProgress.visible = true;

  const estimateMs = clampSmartScoreEstimate(
    sortEstimatedDurationMsByKey[smartScoreProgressSortKey.value] ??
      getSortEstimateDefaultMs(smartScoreProgressSortKey.value),
  );

  if (rapidRestart) {
    // Keep current progress on quick follow-up fetches so the bar does not
    // visually bounce backwards at startup when multiple refreshes compete.
    const currentRatio = Math.max(
      0,
      Math.min(1, smartScoreProgress.percent / SORT_PROGRESS_MAX_BEFORE_DONE),
    );
    smartScoreProgressStartedAt = now - estimateMs * currentRatio;
  } else {
    smartScoreProgressStartedAt = now;
    setSmartScoreProgressPercent(0, { allowReset: true });
  }

  smartScoreProgress.message =
    props.searchQuery && props.searchQuery.trim()
      ? "Searching"
      : `Sorting by ${getSortProgressLabel(smartScoreProgressSortKey.value)}`;
  smartScoreProgressTimer = setInterval(() => {
    if (!smartScoreProgress.visible) {
      stopSmartScoreProgressTimer();
      return;
    }
    const elapsed = Math.max(0, getNowMs() - smartScoreProgressStartedAt);
    const ratio = elapsed / Math.max(1, estimateMs);
    const smooth = easeEstimatedProgress(ratio);
    const next = Math.min(SORT_PROGRESS_MAX_BEFORE_DONE, smooth * 100);
    setSmartScoreProgressPercent(next);
  }, 120);
}

function completeSmartScoreProgress(loadId, measuredDurationMs, wasSuccessful) {
  if (Number(loadId) !== smartScoreProgressLoadId) return;
  stopSmartScoreProgressTimer();
  if (wasSuccessful) {
    const measured = Number(measuredDurationMs);
    if (Number.isFinite(measured) && measured > 0) {
      const sortKey = String(smartScoreProgressSortKey.value || "");
      const previous = clampSmartScoreEstimate(
        sortEstimatedDurationMsByKey[sortKey] ??
          getSortEstimateDefaultMs(sortKey),
      );
      const nextEstimate =
        (1 - SORT_PROGRESS_EWMA_ALPHA) * previous +
        SORT_PROGRESS_EWMA_ALPHA * clampSmartScoreEstimate(measured);
      sortEstimatedDurationMsByKey[sortKey] =
        clampSmartScoreEstimate(nextEstimate);
      console.debug(
        `[SortProgress] sort=${sortKey || "(none)"} total=${Math.round(measured)}ms estimate=${Math.round(sortEstimatedDurationMsByKey[sortKey] || previous)}ms`,
      );
    }
    setSmartScoreProgressPercent(100);
    smartScoreProgress.message =
      props.searchQuery && props.searchQuery.trim()
        ? "Search complete"
        : `Sorted by ${getSortProgressLabel(smartScoreProgressSortKey.value)}`;
    setTimeout(() => {
      if (Number(loadId) !== smartScoreProgressLoadId) return;
      smartScoreProgress.visible = false;
      setSmartScoreProgressPercent(0, { allowReset: true });
      smartScoreProgress.message = "Calculating smart scores";
      smartScoreProgressSortKey.value = "";
    }, SORT_PROGRESS_COMPLETION_HOLD_MS);
    return;
  }
  smartScoreProgress.visible = false;
  setSmartScoreProgressPercent(0, { allowReset: true });
  smartScoreProgress.message = "Calculating smart scores";
  smartScoreProgressSortKey.value = "";
}

async function maybeRefreshOverlayForComfyui() {
  await comfyuiRunner.value?.maybeRefreshOverlayForComfyui();
}

// ============================================================
// THUMBNAIL INFO TEXT-FITTING
// ============================================================
function buildThumbnailInfoKey(imageId, infoKey) {
  return `${imageId}-${infoKey}`;
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
  window.addEventListener("drop", clearGridDragOverlay, true);
  window.addEventListener("dragend", clearGridDragOverlay, true);
  window.addEventListener("keydown", handleKeyDown);

  fetchAvailablePlugins();
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

watch(
  () => props.backendUrl,
  () => {
    fetchAvailablePlugins();
  },
);

onUnmounted(() => {
  window.removeEventListener("resize", triggerFaceOverlayRedraw);
  window.removeEventListener("drop", clearGridDragOverlay, true);
  window.removeEventListener("dragend", clearGridDragOverlay, true);
  window.removeEventListener("keydown", handleKeyDown);
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
  if (pluginProgressHideTimer) {
    clearTimeout(pluginProgressHideTimer);
    pluginProgressHideTimer = null;
  }
  stopSmartScoreProgressTimer();
  if (wsTagFullRefreshTimer) {
    clearTimeout(wsTagFullRefreshTimer);
    wsTagFullRefreshTimer = null;
  }
});

watch(
  () => props.wsUpdateKey,
  (nextKey) => {
    if (!nextKey || nextKey === lastWsUpdateKey.value) return;
    lastWsUpdateKey.value = nextKey;
    if (pauseGridAutoUpdates.value) {
      pendingGridRefreshAfterImport.value = true;
      return;
    }
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
    if (!pictureIds.length) return;
    if (pauseGridAutoUpdates.value) {
      pendingGridRefreshAfterImport.value = true;
      return;
    }
    // Coalesce all task-driven tag updates into an infrequent full refresh to
    // avoid starving the tagger when a large grid is open.
    scheduleWsTagFullRefresh();
  },
);

watch(
  () => props.wsPluginProgress,
  (wrapped) => {
    if (!wrapped || typeof wrapped !== "object") return;
    const payload = wrapped.payload;
    if (!payload || typeof payload !== "object") return;

    if (pluginProgressHideTimer) {
      clearTimeout(pluginProgressHideTimer);
      pluginProgressHideTimer = null;
    }

    const pluginName = String(payload.plugin || "plugin").toLowerCase();
    if (pluginName === "smart_score") {
      // Smart score overlay is driven by local fetch instrumentation,
      // not websocket events, to avoid jitter/out-of-order updates.
      return;
    }

    pluginProgress.runId = String(payload.run_id || pluginProgress.runId || "");
    pluginProgress.status = String(payload.status || "running");
    pluginProgress.current = Math.max(0, Number(payload.current || 0));
    pluginProgress.total = Math.max(
      pluginProgress.current,
      Number(payload.total || pluginProgress.total || 0),
    );
    const explicitProgress = Number(payload.progress);
    if (Number.isFinite(explicitProgress)) {
      pluginProgress.percent = explicitProgress;
    } else if (pluginProgress.total > 0) {
      pluginProgress.percent =
        (pluginProgress.current / pluginProgress.total) * 100;
    }
    const pluginNameForMessage = String(payload.plugin || "plugin");
    pluginProgress.message = normalizePluginProgressMessage(
      payload.message,
      `${pluginNameForMessage}: ${pluginProgress.status}`,
    );
    pluginProgress.visible = true;

    if (
      pluginProgress.status === "completed" ||
      pluginProgress.status === "failed"
    ) {
      pluginProgressHideTimer = setTimeout(() => {
        pluginProgress.visible = false;
        pluginProgressHideTimer = null;
      }, 1800);
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

// ============================================================
// THUMBNAIL HELPERS
// ============================================================
function onThumbnailLoad(id, event = null) {
  thumbnailLoadedMap[id] = (thumbnailLoadedMap[id] || 0) + 1;
  const assignedAt = Number(thumbnailAssignedAtMap[id]);
  const eventTarget = event?.target || null;
  const src =
    eventTarget?.currentSrc ||
    eventTarget?.src ||
    thumbnailRefs[id]?.currentSrc ||
    thumbnailRefs[id]?.src ||
    null;
  if (Number.isFinite(assignedAt) && assignedAt > 0) {
    delete thumbnailAssignedAtMap[id];
  }
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
    fetchThumbnailsBatch(index, index + 1, {
      reason: "retry-missing-thumbnail",
      triggerId: id,
    });
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

const _makeRefSetter = (map) => (id, el) => {
  if (el) {
    map[id] = el;
  } else {
    delete map[id];
  }
};
const setDragPreviewRef = _makeRefSetter(dragPreviewRefs);
const setThumbnailContainerRef = _makeRefSetter(thumbnailContainerRefs);

function isThumbnailReady(id) {
  return Boolean(id && thumbnailReadyMap[id]);
}

function getThumbnailSrc(img) {
  if (!img) return null;
  return img.thumbnail || null;
}

function getVideoThumbnailSrc(img) {
  if (!img || !isVideo(img)) return null;
  if (!img.id || !img.format) return null;
  // Build a stable URL without the pixel_sha cache-buster so the browser treats
  // this as the same resource as the overlay's videoSrc (which also omits it).
  // Using buildMediaUrl here would add ?v=pixel_sha, causing two concurrent
  // requests to different URLs for the same file — the browser aborts one,
  // leaving the overlay's <video> element stuck loading.
  return `${props.backendUrl}/pictures/${img.id}.${img.format.toLowerCase()}`;
}

// --- Multi-face selection state ---
// ============================================================
// FACE BBOX FUNCTIONS
// ============================================================
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
}

// Track which image is currently hovered
// ============================================================
// HOVER STATE + THUMBNAIL INFO DISPLAY ITEMS
// ============================================================
const hoveredImageIdx = ref(null);
const hoveredStackId = ref(null);

function handleImageMouseEnter(img) {
  hoveredImageIdx.value = img.idx;
  hoveredStackId.value = getPictureStackId(img) ?? null;
}
function handleImageMouseLeave(img) {
  if (hoveredImageIdx.value === img.idx) hoveredImageIdx.value = null;
  if (hoveredStackId.value && getPictureStackId(img) === hoveredStackId.value) {
    hoveredStackId.value = null;
  }
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

  const smartScore = getGridSmartScoreValue(img);
  if (selectedSort.includes("SMART_SCORE") && smartScore !== null) {
    items.push({
      key: "smart_score",
      text: `Smart Score: ${smartScore.toFixed(2)}`,
    });
  }

  if (selectedSort === "TEXT_CONTENT" && typeof img.text_score === "number") {
    items.push({
      key: "text_score",
      text: `Text: ${(img.text_score * 100).toFixed(0)}%`,
    });
  }

  if (
    selectedSort === "TAG_UNCERTAINTY" &&
    typeof img.tag_uncertainty === "number"
  ) {
    items.push({
      key: "tag_uncertainty",
      text: `Tag Uncertainty: ${(img.tag_uncertainty * 100).toFixed(1)}%`,
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
  } else if (selectedSort === "IMPORTED_AT" && img.imported_at) {
    items.push({
      key: "imported_at",
      text: formatUserDate(img.imported_at, props.dateFormat),
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
    if (!props.showStacks) {
      return items;
    }
    const stackIndex =
      typeof img.stackIndex === "number" ? img.stackIndex : img.stack_index;
    items.push({
      key: "stack_index",
      text: `Group ${stackIndex + 1}`,
    });
  }
  return items;
}

function formatCompactDate(dateStr) {
  if (!dateStr) return "";
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) return dateStr;
  const now = new Date();
  const sameYear = d.getFullYear() === now.getFullYear();
  const fmt =
    typeof props.dateFormat === "string" ? props.dateFormat : "locale";
  const y = d.getFullYear();
  const day = d.getDate();
  const MONTHS = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
  ];
  const mon = MONTHS[d.getMonth()];
  switch (fmt) {
    case "eu":
    case "british":
    case "iso":
      return sameYear ? `${day} ${mon}` : `${day} ${mon} ${y}`;
    case "us":
      return sameYear ? `${mon} ${day}` : `${mon} ${day}, ${y}`;
    case "ymd-slash":
    case "ymd-dot":
      return sameYear ? `${mon} ${day}` : `${y} ${mon} ${day}`;
    case "ymd-jp":
      return sameYear
        ? `${d.getMonth() + 1}月${day}日`
        : `${y}年${d.getMonth() + 1}月${day}日`;
    case "locale":
    default:
      return d.toLocaleDateString(
        undefined,
        sameYear
          ? { month: "short", day: "numeric" }
          : { year: "numeric", month: "short", day: "numeric" },
      );
  }
}

function getCompactGroupLabel(img, visualIdx) {
  if (!props.compactMode || !img) return null;
  const isSearchMode = !!(props.searchQuery && props.searchQuery.trim());
  const sort = typeof props.selectedSort === "string" ? props.selectedSort : "";

  function getGroupKey(item) {
    if (!item) return null;
    if (isSearchMode && typeof item.likeness_score === "number")
      return Math.round(item.likeness_score * 20);
    if (sort === "IMPORTED_AT" && item.imported_at)
      return item.imported_at.slice(0, 10);
    if (sort.includes("DATE") && item.created_at)
      return item.created_at.slice(0, 10);
    const smartScore = getGridSmartScoreValue(item);
    if (sort.includes("SMART_SCORE") && smartScore !== null)
      return Math.round(smartScore * 10);
    if (
      sort.includes("CHARACTER_LIKENESS") &&
      typeof item.character_likeness === "number"
    )
      return Math.round(item.character_likeness * 100);
    if (sort === "TEXT_CONTENT" && typeof item.text_score === "number")
      return Math.round(item.text_score * 10);
    if (sort === "TAG_UNCERTAINTY" && typeof item.tag_uncertainty === "number")
      return Math.round(item.tag_uncertainty * 100);
    return null;
  }

  const currentKey = getGroupKey(img);
  if (currentKey === null) return null;

  const prevImg =
    visualIdx > 0 ? gridImagesToRender.value[visualIdx - 1] : null;
  if (visualIdx > 0 && getGroupKey(prevImg) === currentKey) return null;

  if (isSearchMode && typeof img.likeness_score === "number")
    return `≈ ${img.likeness_score.toFixed(2)}`;
  if (sort === "IMPORTED_AT" && img.imported_at)
    return formatCompactDate(img.imported_at);
  if (sort.includes("DATE") && img.created_at)
    return formatCompactDate(img.created_at);
  const smartScore = getGridSmartScoreValue(img);
  if (sort.includes("SMART_SCORE") && smartScore !== null)
    return `★ ${(Math.round(smartScore * 10) / 10).toFixed(1)}`;
  if (
    sort.includes("CHARACTER_LIKENESS") &&
    typeof img.character_likeness === "number"
  )
    return `≈ ${(Math.floor(img.character_likeness * 100) / 100).toFixed(2)}`;
  if (sort === "TEXT_CONTENT" && typeof img.text_score === "number")
    return `${(img.text_score * 100).toFixed(0)}%`;
  if (sort === "TAG_UNCERTAINTY" && typeof img.tag_uncertainty === "number")
    return `⚠ ${(img.tag_uncertainty * 100).toFixed(0)}%`;
  return null;
}

const compactStickyLabel = computed(() => {
  if (!props.compactMode) return null;
  const isSearchMode = !!(props.searchQuery && props.searchQuery.trim());
  const sort = typeof props.selectedSort === "string" ? props.selectedSort : "";

  function getGroupKey(item) {
    if (!item) return null;
    if (isSearchMode && typeof item.likeness_score === "number")
      return Math.round(item.likeness_score * 20);
    if (sort === "IMPORTED_AT" && item.imported_at)
      return item.imported_at.slice(0, 10);
    if (sort.includes("DATE") && item.created_at)
      return item.created_at.slice(0, 10);
    const smartScore = getGridSmartScoreValue(item);
    if (sort.includes("SMART_SCORE") && smartScore !== null)
      return Math.round(smartScore * 10);
    if (
      sort.includes("CHARACTER_LIKENESS") &&
      typeof item.character_likeness === "number"
    )
      return Math.round(item.character_likeness * 100);
    if (sort === "TEXT_CONTENT" && typeof item.text_score === "number")
      return Math.round(item.text_score * 10);
    if (sort === "TAG_UNCERTAINTY" && typeof item.tag_uncertainty === "number")
      return Math.round(item.tag_uncertainty * 100);
    return null;
  }

  const firstVisibleVisualIdx = visibleStart.value - renderStart.value;
  const firstImg = gridImagesToRender.value?.[firstVisibleVisualIdx];
  if (!firstImg) return null;

  // Suppress if this item already has a between-row pill (it's a group boundary)
  const prevImg =
    firstVisibleVisualIdx > 0
      ? gridImagesToRender.value[firstVisibleVisualIdx - 1]
      : null;
  const isGroupBoundary =
    firstVisibleVisualIdx === 0 ||
    getGroupKey(prevImg) !== getGroupKey(firstImg);
  if (isGroupBoundary) return null;

  if (isSearchMode && typeof firstImg.likeness_score === "number")
    return `≈ ${firstImg.likeness_score.toFixed(2)}`;
  if (sort === "IMPORTED_AT" && firstImg.imported_at)
    return formatCompactDate(firstImg.imported_at);
  if (sort.includes("DATE") && firstImg.created_at)
    return formatCompactDate(firstImg.created_at);
  const smartScore = getGridSmartScoreValue(firstImg);
  if (sort.includes("SMART_SCORE") && smartScore !== null)
    return `★ ${(Math.round(smartScore * 10) / 10).toFixed(1)}`;
  if (
    sort.includes("CHARACTER_LIKENESS") &&
    typeof firstImg.character_likeness === "number"
  )
    return `≈ ${(Math.floor(firstImg.character_likeness * 100) / 100).toFixed(2)}`;
  if (sort === "TEXT_CONTENT" && typeof firstImg.text_score === "number")
    return `${(firstImg.text_score * 100).toFixed(0)}%`;
  if (
    sort === "TAG_UNCERTAINTY" &&
    typeof firstImg.tag_uncertainty === "number"
  )
    return `⚠ ${(firstImg.tag_uncertainty * 100).toFixed(0)}%`;
  return null;
});

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

// ============================================================
// SELECTION + DRAG HELPERS
// ============================================================
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

// Video refs for hover play/pause in grid
// ============================================================
// VIDEO
// ============================================================
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
  if (!v) return;
  // Trigger load on-demand only when hovered; do nothing if already loading/playing.
  v.preload = "auto";
  v.play().catch(() => {});
}
function pauseVideo(id) {
  const v = videoRefs[id];
  if (!v) return;
  v.pause();
  v.currentTime = 0;
  // Abort any in-progress network fetch so idle tiles consume no bandwidth.
  v.preload = "none";
  v.load();
}

// ============================================================
// GROUP / SET MEMBERSHIP
// ============================================================
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
      emit("refresh-sidebar");
    });
    return;
  }
}

function handleOverlayAddedToSet(payload) {
  const pictureIds = Array.isArray(payload?.pictureIds)
    ? payload.pictureIds
    : [];
  if (!pictureIds.length) return;
  const changedSetId = Number(payload?.setId);
  const action = String(payload?.action || "added");

  if (
    isSetOverlapView.value &&
    Number.isFinite(changedSetId) &&
    normalizedSelectedSetIds.value.includes(changedSetId)
  ) {
    // In overlap view, removing membership from one selected set means the
    // picture no longer belongs to the intersection and should disappear.
    if (action === "removed") {
      removeImagesById(pictureIds);
      selectedImageIds.value = selectedImageIds.value.filter(
        (id) => !pictureIds.includes(id),
      );
      clearFaceSelection();
      lastSelectedImageId = null;
    }
  } else if (
    hasSetSelection.value &&
    !isSetOverlapView.value &&
    action === "removed" &&
    Number.isFinite(changedSetId) &&
    changedSetId === primarySelectedSetId.value
  ) {
    removeImagesById(pictureIds);
  }

  if (
    props.selectedCharacter === props.unassignedPicturesId &&
    !hasSetSelection.value
  ) {
    removeImagesById(pictureIds);
  }
  emit("refresh-sidebar");
}

function handleAddToCharacter(payload) {
  const pictureIds = Array.isArray(payload?.pictureIds)
    ? payload.pictureIds
    : [];
  if (!pictureIds.length) return;
  if (
    props.selectedCharacter === props.unassignedPicturesId &&
    !hasSetSelection.value
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
      await apiClient.delete(`${backendUrl}/pictures/scrapheap`, {
        data: {
          picture_ids: idsToRemove,
        },
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

async function handleSetProjectForSelected(payload) {
  const explicitPictureIds = Array.isArray(payload?.pictureIds)
    ? payload.pictureIds
    : [];
  const basePictureIds = explicitPictureIds.length
    ? explicitPictureIds
    : selectedImageIds.value;
  const expandStacks = payload?.expandStacks !== false;
  const pictureIds = await resolveProjectSelectionPictureIds(
    basePictureIds,
    expandStacks,
  );
  if (!pictureIds.length) {
    return;
  }

  const nextProjectIdRaw = payload?.projectId ?? null;
  const nextProjectId =
    nextProjectIdRaw === null || nextProjectIdRaw === undefined
      ? null
      : Number(nextProjectIdRaw);
  if (nextProjectId !== null && !Number.isFinite(nextProjectId)) {
    window.alert("Invalid project selected.");
    return;
  }

  const action = String(payload?.action || "set").toLowerCase();

  try {
    if (action === "removed") {
      if (nextProjectId === null) {
        return;
      }
      await apiClient.patch(`${props.backendUrl}/pictures/project`, {
        picture_ids: pictureIds,
        project_id: nextProjectId,
        mode: "remove",
      });
    } else if (action === "added") {
      await apiClient.patch(`${props.backendUrl}/pictures/project`, {
        picture_ids: pictureIds,
        project_id: nextProjectId,
        mode: "add",
      });
    } else {
      await apiClient.patch(`${props.backendUrl}/pictures/project`, {
        picture_ids: pictureIds,
        project_id: nextProjectId,
      });
    }

    preserveScrollOnNextFetch.value = true;
    await fetchAllGridImages({ force: true });
    updateVisibleThumbnails();
    emit("refresh-sidebar");
  } catch (err) {
    const message = err?.response?.data?.detail || err?.message || String(err);
    window.alert(`Failed to update project association: ${message}`);
  }
}

async function resolveProjectSelectionPictureIds(
  pictureIdsInput,
  expandStacks = true,
) {
  const selectedIds = (Array.isArray(pictureIdsInput) ? pictureIdsInput : [])
    .map((id) => Number(id))
    .filter((id) => Number.isFinite(id) && id > 0);
  if (!selectedIds.length) {
    return [];
  }

  const resolved = new Set(selectedIds);
  const fetchedById = new Map(
    (Array.isArray(lastFetchedGridImages.value)
      ? lastFetchedGridImages.value
      : []
    )
      .filter((img) => img && img.id != null)
      .map((img) => [String(img.id), img]),
  );

  const stacksToExpand = new Map();
  if (!expandStacks) {
    return Array.from(resolved).sort((a, b) => a - b);
  }

  for (const pictureId of selectedIds) {
    const fetchedImg = fetchedById.get(String(pictureId));
    if (!fetchedImg) {
      continue;
    }
    const stackId = getPictureStackId(fetchedImg);
    const stackCount = getStackBadgeCount(fetchedImg);
    if (!stackId || !Number.isFinite(stackCount) || stackCount <= 1) {
      continue;
    }
    if (!stacksToExpand.has(stackId)) {
      stacksToExpand.set(stackId, stackCount);
    }
  }

  for (const [stackId, stackCount] of stacksToExpand.entries()) {
    await ensureStackMembersLoaded(stackId, stackCount);
    const loaded = expandedStackMembers.value.get(stackId);
    const loadedImages = Array.isArray(loaded?.images) ? loaded.images : [];
    const fallbackImages = getLocalStackMembers(stackId);
    const source = loadedImages.length ? loadedImages : fallbackImages;
    for (const img of source) {
      const id = Number(img?.id);
      if (Number.isFinite(id) && id > 0) {
        resolved.add(id);
      }
    }
  }

  return Array.from(resolved).sort((a, b) => a - b);
}

// ============================================================
// SELECTION BAR + SCRAPHEAP
// ============================================================
const isScrapheapView = computed(() => {
  const scrapheapId = String(
    props.scrapheapPicturesId || "SCRAPHEAP",
  ).toUpperCase();
  const selected = String(props.selectedCharacter || "").toUpperCase();
  return selected === scrapheapId;
});
const selectedStackId = computed(() => {
  const ids = Array.isArray(selectedImageIds.value)
    ? selectedImageIds.value
    : [];
  if (!ids.length) return null;
  const images = Array.isArray(allGridImages.value) ? allGridImages.value : [];
  if (!images.length) return null;
  const imageById = new Map(
    images
      .filter((img) => img && img.id != null)
      .map((img) => [String(img.id), img]),
  );
  let stackId = null;
  for (const id of ids) {
    const img = imageById.get(String(id));
    const currentStackId = getPictureStackId(img);
    if (!currentStackId) return null;
    if (stackId === null) {
      stackId = currentStackId;
      continue;
    }
    if (stackId !== currentStackId) return null;
  }
  return stackId;
});
const showRemoveFromStack = computed(() => {
  return selectedStackId.value !== null;
});
const selectedMediaSupport = computed(() => {
  const ids = Array.isArray(selectedImageIds.value)
    ? selectedImageIds.value
    : [];
  if (!ids.length) {
    return { hasImages: false, hasVideos: false };
  }

  const images = Array.isArray(allGridImages.value) ? allGridImages.value : [];
  const imageById = new Map(
    images
      .filter((img) => img && img.id != null)
      .map((img) => [String(img.id), img]),
  );

  let hasImages = false;
  let hasVideos = false;
  for (const id of ids) {
    const img = imageById.get(String(id));
    if (!img) {
      hasImages = true;
      continue;
    }
    if (isVideo(img)) {
      hasVideos = true;
    } else {
      hasImages = true;
    }
  }

  return { hasImages, hasVideos };
});
const scrapheapEmptying = ref(false);
const showSelectionBar = computed(() => {
  return selectedImageIds.value.length > 0 || selectedFaceIds.value.length > 0;
});
const selectedExpandedCount = computed(() => {
  const selectedSet = new Set(
    selectedImageIds.value
      .map((id) => Number(id))
      .filter((id) => Number.isFinite(id) && id > 0),
  );
  const visibleIds = new Set(
    allGridImages.value
      .map((img) => Number(img?.id))
      .filter((id) => Number.isFinite(id) && id > 0),
  );
  const isFullVisibleSelection =
    visibleIds.size > 0 &&
    selectedSet.size === visibleIds.size &&
    [...visibleIds].every((id) => selectedSet.has(id));

  const isTopCategorySelection = [
    String(props.allPicturesId),
    String(props.unassignedPicturesId),
  ].includes(String(props.selectedCharacter ?? ""));

  const isSetCategorySelection =
    props.selectedSet !== null &&
    props.selectedSet !== undefined &&
    String(props.selectedSet) !== "";

  const supportsAuthoritativeCategoryCount =
    isTopCategorySelection || isSetCategorySelection;

  const hasNoAdditionalFilters =
    !(props.searchQuery || "").trim() &&
    props.mediaTypeFilter === "all" &&
    (props.comfyuiModelFilter || []).length === 0 &&
    (props.comfyuiLoraFilter || []).length === 0 &&
    props.minScoreFilter == null;

  // Keep the info count aligned with sidebar summary for full category selections.
  if (
    isFullVisibleSelection &&
    supportsAuthoritativeCategoryCount &&
    hasNoAdditionalFilters &&
    totalCurrentCategoryCount.value > 0
  ) {
    return totalCurrentCategoryCount.value;
  }

  const seenStacks = new Set();
  let total = 0;
  for (const img of allGridImages.value) {
    if (!img || !img.id) continue;
    if (!selectedSet.has(Number(img.id))) continue;
    const stackId = getPictureStackId(img);
    const stackCount = Number(img.stack_count ?? img.stackCount ?? 0);
    if (stackId != null && stackCount > 1) {
      if (seenStacks.has(stackId)) continue;
      seenStacks.add(stackId);
      total += stackCount;
      continue;
    }
    total += 1;
  }
  return total;
});
const isSelectionEmpty = computed(() => {
  return !showSelectionBar.value;
});
const showScrapheapBar = computed(() => {
  return isScrapheapView.value && isSelectionEmpty.value;
});
const SCRAPHEAP_BAR_HEIGHT_PX = 36;
const wrapperStyle = { position: "relative", height: "100%" };
const scrollWrapperStyle = computed(() => {
  const offset =
    showSelectionBar.value || showScrapheapBar.value
      ? SCRAPHEAP_BAR_HEIGHT_PX
      : 0;
  return {
    position: "relative",
    paddingTop: `${offset}px`,
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
    await apiClient.delete(`${props.backendUrl}/pictures/scrapheap`);
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

// ============================================================
// IMPORT
// ============================================================
const imageImporterRef = ref(null);
// Handle images-uploaded event from ImageImporter
async function handleImagesUploaded(payload) {
  pauseGridAutoUpdates.value = false;
  pendingGridRefreshAfterImport.value = false;
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
  fetchAllGridImages({ force: true }).then(() => {
    updateVisibleThumbnails();
  });
  emit("refresh-sidebar");
}

function handleImportStarted() {
  pauseGridAutoUpdates.value = true;
  pendingGridRefreshAfterImport.value = false;
}

function runDeferredGridRefreshAfterImport() {
  if (!pendingGridRefreshAfterImport.value) {
    return;
  }
  pendingGridRefreshAfterImport.value = false;
  preserveScrollOnNextFetch.value = true;
  debouncedFetchAllGridImages({ force: true });
  fetchAllPicturesCount();
}

function handleImportCancelled() {
  pauseGridAutoUpdates.value = false;
  runDeferredGridRefreshAfterImport();
}

function handleImportErrored() {
  pauseGridAutoUpdates.value = false;
  runDeferredGridRefreshAfterImport();
}

const debouncedFetchAllGridImages = debounce(fetchAllGridImages, 200);
const lastGridVersionRefreshAt = ref(0);
const WS_TAG_FULL_REFRESH_MIN_INTERVAL_MS = 6000;
const lastWsTagFullRefreshAt = ref(0);
let wsTagFullRefreshTimer = null;

function scheduleWsTagFullRefresh() {
  const now = Date.now();
  const elapsed = now - lastWsTagFullRefreshAt.value;
  if (elapsed >= WS_TAG_FULL_REFRESH_MIN_INTERVAL_MS) {
    lastWsTagFullRefreshAt.value = now;
    preserveScrollOnNextFetch.value = true;
    debouncedFetchAllGridImages({ force: true });
    return;
  }
  if (wsTagFullRefreshTimer) {
    return;
  }
  const waitMs = WS_TAG_FULL_REFRESH_MIN_INTERVAL_MS - elapsed + 25;
  wsTagFullRefreshTimer = setTimeout(
    () => {
      wsTagFullRefreshTimer = null;
      lastWsTagFullRefreshAt.value = Date.now();
      preserveScrollOnNextFetch.value = true;
      debouncedFetchAllGridImages({ force: true });
    },
    Math.max(25, waitMs),
  );
}

watch(
  () => props.gridVersion,
  () => {
    if (pauseGridAutoUpdates.value) {
      pendingGridRefreshAfterImport.value = true;
      return;
    }
    const now = Date.now();
    if (now - lastGridVersionRefreshAt.value < 1200) {
      return;
    }
    lastGridVersionRefreshAt.value = now;
    if (skipNextWsRefresh.value) {
      skipNextWsRefresh.value = false;
      return;
    }
    if (overlayOpen.value) {
      // Overlay is open — quietly refresh in the background without clearing
      // allGridImages, so the overlay doesn't lose focus or state mid-edit.
      debouncedFetchAllGridImages();
      fetchAllPicturesCount();
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
    // Force the refetch to bypass the 1200ms de-dup cache: if the grid was
    // just cleared we must not skip the fetch, otherwise the grid stays blank.
    debouncedFetchAllGridImages({ force: true });
    if (preserveScrollOnNextFetch.value) {
      preserveScrollOnNextFetch.value = false;
    }
    fetchAllPicturesCount();
  },
);

// ============================================================
// VIEWPORT + RENDER
// ============================================================
const VIEW_WINDOW = 100;

const divisibleViewWindow = computed(() => {
  const cols = props.columns;
  return Math.ceil(VIEW_WINDOW / cols) * cols;
});

const initialRender = ref(true);
const renderBuffer = computed(() =>
  initialRender.value ? 0 : divisibleViewWindow.value,
);

// ============================================================
// OVERLAY STATE
// ============================================================
const overlayOpen = ref(false);
const overlayImageId = ref(null);
const overlayInitialExpandedStackIds = ref([]);
// Set to true when a tag mutation was deferred (applyTagFilter=true, overlay
// open). Triggers a filtered grid refetch once the overlay closes.
const pendingTagFilterRefresh = ref(false);

// ============================================================
// DRAG & DROP STATE + SOURCE HELPERS
// ============================================================
const dragOverlayVisible = ref(false);
const dragOverlayMessage = "Drop files here to import";
const dragOverlayDepth = ref(0);
const dragSource = ref(null);
const dragSourceImageIds = ref(new Set());
const stackReorderHoverId = ref(null);
const stackReorderHoverSide = ref(null);

function setDragSourceImageIds(ids) {
  const next = new Set(
    Array.isArray(ids) ? ids.map((id) => String(id)).filter(Boolean) : [],
  );
  dragSourceImageIds.value = next;
}

function clearDragSourceImageIds() {
  dragSourceImageIds.value = new Set();
}

function isDragSourceImage(img) {
  if (!img?.id) return false;
  return dragSourceImageIds.value.has(String(img.id));
}

function setStackReorderHoverId(value) {
  stackReorderHoverId.value = value ? String(value) : null;
}

function setStackReorderHoverSide(value) {
  stackReorderHoverSide.value =
    value === "left" || value === "right" ? value : null;
}

function isStackReorderTarget(img) {
  if (!img?.id) return false;
  return stackReorderHoverId.value === String(img.id);
}

function isStackReorderTargetSide(img, side) {
  if (!isStackReorderTarget(img)) return false;
  return stackReorderHoverSide.value === side;
}

const selectedGroupName = ref("");

async function updateSelectedGroupName() {
  let name = "";
  if (
    props.selectedCharacter &&
    props.selectedCharacter !== `${props.allPicturesId}` &&
    props.selectedCharacter !== `${props.unassignedPicturesId}` &&
    props.selectedCharacter !== `${props.scrapheapPicturesId}`
  ) {
    try {
      const res = await apiClient.get(
        `${props.backendUrl}/characters/${props.selectedCharacter}`,
      );
      const char = res.data;
      name = char.name || "";
    } catch (e) {
      console.error("Character fetch failed:", e);
    }
  } else if (hasSetSelection.value) {
    if (isSetOverlapView.value) {
      selectedGroupName.value = `Set Overlap (${normalizedSelectedSetIds.value.length})`;
      return;
    }
    try {
      const res = await apiClient.get(
        `${props.backendUrl}/picture_sets/${primarySelectedSetId.value}`,
      );
      const set = res.data;
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
    () => props.selectedSet,
    () => props.selectedSetIds,
  ],
  () => {
    updateSelectedGroupName();
  },
  { immediate: true },
);

// ============================================================
// SELECTION STATE
// ============================================================
// Local selection state (mirrors parent prop)
const selectedImageIds = ref([]);
let lastSelectedImageId = null;
const cursorIdx = ref(null);
const isImageSelected = (id) =>
  selectedImageIds.value && selectedImageIds.value.includes(id);

// ============================================================
// OVERLAY FUNCTIONS
// ============================================================
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

async function refreshGridImage(imageId, options = {}) {
  if (!imageId) return;
  const dId = getPictureId(imageId);
  const idx = allGridImages.value.findIndex(
    (img) => getPictureId(img?.id) === dId,
  );
  if (idx === -1) return;
  const latestInfo = await fetchImageInfo(imageId, {
    smartScore: options?.smartScore === true || isSmartScoreSortActive(),
    force: options?.force === true,
  });
  if (latestInfo && !Array.isArray(latestInfo)) {
    const current = allGridImages.value[idx] || {};
    const nextImages = allGridImages.value.slice();
    nextImages[idx] = {
      ...current,
      ...latestInfo,
      idx: current.idx ?? idx,
    };
    allGridImages.value = nextImages;
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

function handleOverlayChange(payload) {
  if (!payload) return;
  const imageId = payload.imageId ?? payload.id ?? payload;
  const fields = payload.fields || {};
  if (fields.stack) {
    preserveScrollOnNextFetch.value = true;
    void fetchAllGridImages();
    return;
  }
  if (!imageId) return;
  if ((fields.tags || fields.smartScore) && isSmartScoreSortActive()) {
    preserveScrollOnNextFetch.value = true;
    debouncedFetchAllGridImages({ force: true });
    return;
  }
  refreshGridImage(imageId);
}

// ============================================================
// STACK MANAGEMENT ACTIONS
// ============================================================
async function createStackFromSelection() {
  const ids = Array.isArray(selectedImageIds.value)
    ? selectedImageIds.value
    : [];
  if (!ids.length) return;
  // Sort by grid display order so the backend keeps the first-in-grid stack.
  const gridImages = Array.isArray(allGridImages.value)
    ? allGridImages.value
    : [];
  const gridIndexById = new Map(
    gridImages.map((img, i) => [String(img?.id), i]),
  );
  const sortedIds = ids.slice().sort((a, b) => {
    const ia = gridIndexById.get(String(a)) ?? Infinity;
    const ib = gridIndexById.get(String(b)) ?? Infinity;
    return ia - ib;
  });
  try {
    await apiClient.post(`${props.backendUrl}/stacks`, {
      picture_ids: sortedIds,
    });
    clearSelection();
    preserveScrollOnNextFetch.value = true;
    debouncedFetchAllGridImages();
  } catch (e) {
    console.error("Failed to create stack from selection:", e);
  }
}

async function removeSelectedFromStack() {
  const stackId = selectedStackId.value;
  const ids = Array.isArray(selectedImageIds.value)
    ? selectedImageIds.value
    : [];
  if (!stackId || !ids.length) return;
  try {
    await apiClient.delete(`${props.backendUrl}/stacks/${stackId}/members`, {
      data: { picture_ids: ids },
    });
    const removed = new Set(ids.map((id) => getPictureId(id)));
    allGridImages.value = allGridImages.value.map((img) => {
      if (!img || !removed.has(getPictureId(img.id))) {
        return img;
      }
      return {
        ...img,
        stack_id: null,
        stackId: null,
        stack_index: null,
        stackIndex: null,
        stack_position: null,
        stackPosition: null,
        stack_count: null,
        stackCount: null,
      };
    });
    const nextMembers = new Map(expandedStackMembers.value);
    const entry = nextMembers.get(stackId);
    if (entry) {
      const nextIds = Array.isArray(entry.ids)
        ? entry.ids.filter((id) => !removed.has(getPictureId(id)))
        : [];
      const nextImages = Array.isArray(entry.images)
        ? entry.images.filter((img) => !removed.has(getPictureId(img?.id)))
        : [];
      if (nextIds.length || nextImages.length) {
        nextMembers.set(stackId, { ids: nextIds, images: nextImages });
      } else {
        nextMembers.delete(stackId);
        const nextExpanded = new Set(expandedStackIds.value);
        if (nextExpanded.delete(stackId)) {
          expandedStackIds.value = nextExpanded;
        }
      }
      expandedStackMembers.value = nextMembers;
    }
    clearSelection();
    preserveScrollOnNextFetch.value = true;
    debouncedFetchAllGridImages();
  } catch (e) {
    console.error("Failed to remove selected images from stack:", e);
  }
}

function getLikenessGroupId(img) {
  if (!img) return null;
  const raw = img.stackIndex ?? img.stack_index ?? null;
  if (raw === null || raw === undefined) return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

async function createStacksFromSelectedGroups() {
  if (props.selectedSort !== STACKS_SORT_KEY) return;
  const ids = Array.isArray(selectedImageIds.value)
    ? selectedImageIds.value
    : [];
  if (!ids.length) return;

  const source = Array.isArray(lastFetchedGridImages.value)
    ? lastFetchedGridImages.value
    : allGridImages.value;
  const images = Array.isArray(source) ? source : [];
  const imageById = new Map(
    images
      .filter((img) => img && img.id != null)
      .map((img) => [String(img.id), img]),
  );

  const groupIds = new Set();
  for (const id of ids) {
    const img = imageById.get(String(id));
    const groupId = getLikenessGroupId(img);
    if (groupId != null) {
      groupIds.add(groupId);
    }
  }

  if (!groupIds.size) return;

  const groupsToStack = [];
  const skippedGroups = [];
  for (const groupId of groupIds) {
    const members = images.filter(
      (img) => getLikenessGroupId(img) === groupId && img?.id != null,
    );
    const memberIds = Array.from(
      new Set(members.map((img) => Number(img.id)).filter(Number.isFinite)),
    );
    if (memberIds.length < 2) continue;
    const membersByStack = new Map();
    for (const member of members) {
      const stackId = getPictureStackId(member);
      if (!stackId) continue;
      if (!membersByStack.has(stackId)) {
        membersByStack.set(stackId, []);
      }
      membersByStack.get(stackId).push(member);
    }
    if (membersByStack.size > 1) {
      skippedGroups.push(groupId);
      continue;
    }
    if (membersByStack.size === 1) {
      const [stackId, stackedMembers] = Array.from(membersByStack.entries())[0];
      const stackedAnchorId = stackedMembers?.[0]?.id;
      const unstackedIds = memberIds.filter(
        (id) => !getPictureStackId(imageById.get(String(id))),
      );
      if (!unstackedIds.length) continue;
      const payloadIds = [stackedAnchorId, ...unstackedIds]
        .filter((id) => id != null)
        .map((id) => Number(id))
        .filter(Number.isFinite);
      if (payloadIds.length < 2) continue;
      groupsToStack.push(payloadIds);
      continue;
    }
    groupsToStack.push(memberIds);
  }

  if (!groupsToStack.length) return;

  try {
    for (const memberIds of groupsToStack) {
      await apiClient.post(`${props.backendUrl}/stacks`, {
        picture_ids: memberIds,
      });
    }
    if (skippedGroups.length) {
      alert(
        `Skipped ${skippedGroups.length} group(s) containing multiple stacks.`,
      );
    }
    clearSelection();
    preserveScrollOnNextFetch.value = true;
    debouncedFetchAllGridImages();
  } catch (e) {
    console.error("Failed to create stacks from groups:", e);
  }
}

async function openOverlay(img) {
  if (!img || !img.id) return;
  overlayInitialExpandedStackIds.value = Array.from(
    expandedStackIds.value || [],
  );
  overlayImageId.value = img.id;
  overlayOpen.value = true;
}

function closeOverlay() {
  overlayOpen.value = false;
  overlayImageId.value = null;
  overlayInitialExpandedStackIds.value = [];
  if (comfyuiRunner.value?.comfyuiPendingOverlayRefresh) {
    comfyuiRunner.value.comfyuiPendingOverlayRefresh.value = false;
  }
  if (pendingTagFilterRefresh.value) {
    pendingTagFilterRefresh.value = false;
    lastFetchSuccess.value = { key: "", at: 0 };
    lastFetchError.value = { key: "", at: 0 };
    debouncedFetchAllGridImages();
  }
}

// ============================================================
// SCORING
// ============================================================
async function setScore(img, n) {
  const newScore = toggleScore(img.score, n);
  applyScore(img, newScore);
}

function isScoreSortActive() {
  return typeof props.selectedSort === "string"
    ? props.selectedSort.toUpperCase() === "SCORE"
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

function getGridSmartScoreValue(img) {
  if (!img) return null;
  const raw =
    typeof img.smartScore === "number"
      ? img.smartScore
      : typeof img.smart_score === "number"
        ? img.smart_score
        : null;
  return Number.isFinite(raw) ? raw : null;
}

function formatGridSmartScoreValue(img) {
  const value = getGridSmartScoreValue(img);
  return value === null ? "" : value.toFixed(2);
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

function _spliceAndReinsert(
  items,
  currentIndex,
  target,
  targetScore,
  getScore,
  descending,
) {
  items.splice(currentIndex, 1);
  let insertIndex = items.findIndex((item) => {
    const score = getScore(item);
    return descending ? score < targetScore : score > targetScore;
  });
  if (insertIndex === -1) insertIndex = items.length;
  if (insertIndex === currentIndex) {
    const updated = allGridImages.value.slice();
    updated[currentIndex] = { ...target, idx: currentIndex };
    allGridImages.value = updated;
    return null;
  }
  items.splice(insertIndex, 0, target);
  for (let i = 0; i < items.length; i += 1) {
    items[i].idx = i;
  }
  allGridImages.value = items;
  invalidateVisibleThumbnailRanges();
  return insertIndex;
}

function repositionImageByScore(imageId, newScore) {
  const items = allGridImages.value.slice();
  const dId = getPictureId(imageId);
  const currentIndex = items.findIndex(
    (item) => getPictureId(item?.id) === dId,
  );
  if (currentIndex === -1) return;

  const target = items[currentIndex];
  target.score = newScore;
  const targetScore = newScore ?? 0;
  const descending = props.selectedDescending === true;
  const insertIndex = _spliceAndReinsert(
    items,
    currentIndex,
    target,
    targetScore,
    (item) => item.score ?? 0,
    descending,
  );
  if (insertIndex !== null) {
    nextTick(() => {
      const grid = gridContainer.value;
      if (!grid) return;
      const card = grid.querySelectorAll(".image-card")[insertIndex];
      if (card && card.scrollIntoView) {
        card.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    });
  }
}

let smartScoreRepositioning = false;

function repositionImageBySmartScore(imageId, smartScore, latestInfo = null) {
  if (smartScoreRepositioning) return;
  smartScoreRepositioning = true;
  try {
    const items = allGridImages.value.slice();
    const currentIndex = items.findIndex((item) => item.id === imageId);
    if (currentIndex === -1) return;

    const targetScore = smartScore ?? 0;
    const target = {
      ...items[currentIndex],
      ...(latestInfo && typeof latestInfo === "object" ? latestInfo : {}),
      smartScore: targetScore,
      thumbnail:
        items[currentIndex]?.thumbnail ?? latestInfo?.thumbnail ?? null,
    };
    const descending = props.selectedDescending === true;
    _spliceAndReinsert(
      items,
      currentIndex,
      target,
      targetScore,
      (item) => item.smartScore ?? 0,
      descending,
    );
  } finally {
    smartScoreRepositioning = false;
  }
}

async function refreshSmartScoreForImage(imageId) {
  if (!imageId || !isSmartScoreSortActive()) return;
  const latestInfo = await fetchImageInfo(imageId, { smartScore: true });
  if (!latestInfo || Array.isArray(latestInfo)) return;

  const idx = allGridImages.value.findIndex((img) => img?.id === imageId);
  if (idx !== -1) {
    const current = allGridImages.value[idx] || {};
    const smartScore =
      typeof latestInfo.smartScore === "number" ? latestInfo.smartScore : null;
    if (current.smartScore === smartScore) {
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
      return;
    }
    if (isSmartScoreSortActive()) {
      preserveScrollOnNextFetch.value = true;
      debouncedFetchAllGridImages();
      return;
    }
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

// ============================================================
// DRAG & DROP — GRID FILE IMPORT
// ============================================================
function handleGridDragEnter(e) {
  if (!e.dataTransfer) return;
  const types = e.dataTransfer.types ? Array.from(e.dataTransfer.types) : [];
  if (!isFileDrag(e.dataTransfer) && types.length > 0) return;
  dragOverlayDepth.value += 1;
  dragOverlayVisible.value = true;
  e.preventDefault();
}

function handleGridDragOver(e) {
  if (!e.dataTransfer) return;
  const types = e.dataTransfer.types ? Array.from(e.dataTransfer.types) : [];
  if (!isFileDrag(e.dataTransfer) && types.length > 0) return;
  if (!dragOverlayVisible.value) {
    dragOverlayVisible.value = true;
  }
  e.preventDefault();
}

function handleGridDragLeave(e) {
  dragOverlayDepth.value = Math.max(0, dragOverlayDepth.value - 1);
  if (dragOverlayDepth.value === 0) {
    dragOverlayVisible.value = false;
  }
}

function clearGridDragOverlay() {
  dragOverlayDepth.value = 0;
  dragOverlayVisible.value = false;
}

async function handleGridDrop(e) {
  clearGridDragOverlay();

  // Ignore drag-and-drop if the source is the grid itself
  if (
    dragSource.value === "grid" ||
    e.dataTransfer.types.includes("application/json")
  ) {
    dragSource.value = null;
    return;
  }

  if (!e.dataTransfer) return;
  const files = await extractSupportedImportFilesFromDataTransfer(
    e.dataTransfer,
  );
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
      projectId: props.selectedProjectId ?? null,
    });
  }
}

// ============================================================
// DRAG & DROP — THUMBNAIL NATIVE
// ============================================================
function buildDragGhostElement(element) {
  if (typeof document === "undefined" || !element) return null;
  const rect = element.getBoundingClientRect?.();
  const width = Math.max(
    1,
    Math.round(rect?.width || element.clientWidth || element.width || 160),
  );
  const height = Math.max(
    1,
    Math.round(rect?.height || element.clientHeight || element.height || 90),
  );
  const computed =
    typeof window !== "undefined" && element instanceof Element
      ? window.getComputedStyle(element)
      : null;
  const radius = computed?.borderRadius || "0px";
  const ghost = document.createElement("div");
  ghost.style.width = `${width}px`;
  ghost.style.height = `${height}px`;
  ghost.style.borderRadius = radius;
  ghost.style.overflow = "hidden";
  ghost.style.backgroundColor = "transparent";
  ghost.style.opacity = "1";
  ghost.style.filter = "none";
  ghost.style.position = "fixed";
  ghost.style.left = "-9999px";
  ghost.style.top = "-9999px";
  ghost.style.pointerEvents = "none";
  ghost.style.zIndex = "9999";

  if (element instanceof HTMLImageElement) {
    const clone = element.cloneNode(true);
    clone.style.width = "100%";
    clone.style.height = "100%";
    clone.style.objectFit = "cover";
    clone.style.borderRadius = "inherit";
    clone.style.opacity = "1";
    clone.style.filter = "none";
    ghost.appendChild(clone);
  } else if (element instanceof HTMLVideoElement) {
    const src = element.currentSrc || element.poster || "";
    ghost.style.background = src
      ? `url("${src}") center / cover no-repeat`
      : "transparent";
  }

  document.body.appendChild(ghost);
  return { ghost, width, height };
}

function setDragImageFromElement(event, element) {
  if (!element || !event?.dataTransfer?.setDragImage) return;
  const ghostData = buildDragGhostElement(element);
  const width = ghostData?.width || element.clientWidth || element.width || 160;
  const height =
    ghostData?.height || element.clientHeight || element.height || 90;
  const dragEl = ghostData?.ghost || element;
  event.dataTransfer.setDragImage(
    dragEl,
    Math.max(1, width / 2),
    Math.max(1, height / 2),
  );
  if (ghostData?.ghost) {
    requestAnimationFrame(() => {
      if (ghostData.ghost?.parentNode) {
        ghostData.ghost.parentNode.removeChild(ghostData.ghost);
      }
    });
  }
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
    setDragSourceImageIds(selectionIds);
    setupMultiExportDrag(event, selectionIds);
    return;
  }
  setDragSourceImageIds([img.id]);
  const target = event?.target;
  if (target instanceof HTMLImageElement) {
    setDragImageFromElement(event, target);
  }
  setDragDataForImageIds(event, [img.id]);
}

function handleDragEnd() {
  dragSource.value = null;
  stackReorderDrag.value = null;
  clearDragSourceImageIds();
  setStackReorderHoverId(null);
  setStackReorderHoverSide(null);
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
    setDragSourceImageIds(selectionIds);
    setupMultiExportDrag(event, selectionIds);
    return;
  }
  setDragSourceImageIds([img.id]);
  const thumbEl = thumbnailRefs[img.id];
  if (thumbEl instanceof HTMLImageElement) {
    setDragImageFromElement(event, thumbEl);
  }
  if (isVideo(img)) {
    const previewEl = dragPreviewRefs[img.id];
    if (previewEl instanceof HTMLImageElement) {
      setDragImageFromElement(event, previewEl);
    }
  }
  setDragDataForImageIds(event, [img.id]);
}

// ============================================================
// KEYBOARD
// ============================================================
function onGlobalKeyPress(key, event) {
  if (scrollWrapper.value) {
    let newScrollTop = scrollWrapper.value.scrollTop;
    const total = allGridImages.value.length;
    const cols = Math.max(1, props.columns || 1);
    const totalRows = Math.ceil(total / cols);
    const totalHeight = totalRows * rowHeight.value;
    const maxScroll = Math.max(
      0,
      totalHeight - scrollWrapper.value.clientHeight,
    );
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

// ============================================================
// GRID FETCH STATE
// ============================================================
const imagesLoading = ref(false);
const imagesError = ref(null);
const totalAllPicturesCount = ref(0);
const totalCurrentCategoryCount = ref(0);
const gridReady = ref(false);
const gridLoadEpoch = ref(0);
const lastFetchKey = ref("");
const lastFetchError = ref({ key: "", at: 0 });
const lastFetchSuccess = ref({ key: "", at: 0 });
const smartScoreLoadingVisible = computed(
  () =>
    !!getActiveSortKey() &&
    smartScoreProgress.visible &&
    !exportProgress.visible,
);

// ============================================================
// GRID FETCH FUNCTIONS
// ============================================================
function buildGridFetchKey() {
  const selectedSetIds = Array.isArray(props.selectedSetIds)
    ? props.selectedSetIds
        .map((id) => Number(id))
        .filter((id) => Number.isFinite(id) && id > 0)
        .sort((a, b) => a - b)
    : [];
  return JSON.stringify({
    selectedCharacter: props.selectedCharacter ?? null,
    selectedSet: props.selectedSet ?? null,
    selectedSetIds,
    isSetOverlapView: selectedSetIds.length > 1,
    projectViewMode: props.projectViewMode ?? "global",
    selectedProjectId: props.selectedProjectId ?? null,
    searchQuery: props.searchQuery ?? "",
    selectedSort: props.selectedSort ?? "",
    selectedDescending: props.selectedDescending ?? null,
    stackThreshold: props.stackThreshold ?? null,
    mediaTypeFilter: props.mediaTypeFilter ?? "all",
    similarityCharacter: props.similarityCharacter ?? null,
    comfyuiModelFilter: props.comfyuiModelFilter ?? [],
    comfyuiLoraFilter: props.comfyuiLoraFilter ?? [],
  });
}

function _appendSelectionParams(params) {
  if (hasSetSelection.value) {
    if (isSetOverlapView.value) {
      for (const setId of normalizedSelectedSetIds.value) {
        params.append("set_ids", String(setId));
      }
      params.append("set_mode", "intersection");
    } else if (primarySelectedSetId.value != null) {
      params.append("set_id", String(primarySelectedSetId.value));
    }
    if (props.projectViewMode === "project") {
      params.append(
        "project_id",
        props.selectedProjectId != null
          ? props.selectedProjectId
          : "UNASSIGNED",
      );
    }
  } else if (
    props.selectedCharacter !== undefined &&
    props.selectedCharacter !== null &&
    props.selectedCharacter !== "" &&
    props.selectedCharacter !== props.allPicturesId
  ) {
    params.append("character_id", props.selectedCharacter);
    if (
      props.selectedCharacter === props.unassignedPicturesId &&
      props.projectViewMode === "project"
    ) {
      params.append(
        "project_id",
        props.selectedProjectId != null
          ? props.selectedProjectId
          : "UNASSIGNED",
      );
    }
  } else if (
    props.selectedCharacter === props.allPicturesId &&
    props.projectViewMode === "project"
  ) {
    params.append(
      "project_id",
      props.selectedProjectId != null ? props.selectedProjectId : "UNASSIGNED",
    );
  }
}

function _appendMediaTypeParams(params) {
  if (props.mediaTypeFilter === "images") {
    for (const ext of PIL_IMAGE_EXTENSIONS) {
      params.append("format", ext.toUpperCase());
    }
  } else if (props.mediaTypeFilter === "videos") {
    for (const ext of VIDEO_EXTENSIONS) {
      params.append("format", ext.toUpperCase());
    }
  }
}

function buildPictureIdsQueryParams() {
  const params = new URLSearchParams();
  _appendSelectionParams(params);
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
      params.append("descending", props.selectedDescending ? "true" : "false");
    } else {
      console.warn(
        "[ImageGrid.vue] selectedDescending is not boolean, skipping param. Type:",
        typeof props.selectedDescending,
      );
    }
  }
  params.append("fields", "grid");
  _appendMediaTypeParams(params);
  (props.comfyuiModelFilter || []).forEach((m) =>
    params.append("comfyui_model", m),
  );
  (props.comfyuiLoraFilter || []).forEach((l) =>
    params.append("comfyui_lora", l),
  );
  if (props.minScoreFilter != null) {
    params.append("min_score", props.minScoreFilter);
  }
  return params.toString();
}

function buildStackQueryParams() {
  const params = new URLSearchParams();
  _appendSelectionParams(params);
  _appendMediaTypeParams(params);
  return params.toString();
}

// ============================================================
// GRID DATA MAPPING
// ============================================================
function collapseStackImages(images) {
  if (!Array.isArray(images) || images.length === 0) return [];
  const counts = new Map();
  for (const img of images) {
    const stackId = getPictureStackId(img);
    if (!stackId) continue;
    counts.set(stackId, (counts.get(stackId) || 0) + 1);
  }
  if (!counts.size) return images;
  const leaders = buildStackLeaderMap(images);
  const seen = new Set();
  const collapsed = [];
  for (const img of images) {
    const stackId = getPictureStackId(img);
    if (!stackId) {
      collapsed.push(img);
      continue;
    }
    const leaderId = leaders.get(stackId);
    if (leaderId && img?.id != null && String(img.id) !== leaderId) {
      continue;
    }
    if (seen.has(stackId)) continue;
    seen.add(stackId);
    const localCount = counts.get(stackId) || 1;
    const serverCount = Number(img?.stack_count ?? img?.stackCount ?? 0);
    const stackCount = Math.max(localCount, serverCount) || 1;
    if (expandedStackIds.value.has(stackId)) {
      const expanded = buildExpandedStackImages(stackId, img, stackCount);
      if (expanded.length) {
        collapsed.push(...expanded);
        continue;
      }
    }
    collapsed.push({
      ...img,
      stackCount,
    });
  }
  return collapsed;
}

function mapGridImages(images) {
  const existingById = new Map(
    allGridImages.value
      .filter((img) => img && img.id != null)
      .map((img) => [getPictureId(img.id), img]),
  );
  const uniqueImages = Array.isArray(images)
    ? (() => {
        const seen = new Set();
        return images.filter((img) => {
          const id = getPictureId(img?.id);
          if (id == null) return true;
          if (seen.has(id)) return false;
          seen.add(id);
          return true;
        });
      })()
    : [];
  return uniqueImages.map((img, i) => {
    return hydrateGridImage(img, i, existingById);
  });
}

function hydrateGridImage(img, idx, existingById) {
  const existing = img?.id ? existingById.get(getPictureId(img.id)) : null;
  return {
    ...img,
    idx,
    thumbnail: existing?.thumbnail ?? null,
    penalised_tags: Array.isArray(existing?.penalised_tags)
      ? existing.penalised_tags
      : [],
    faces: Array.isArray(existing?.faces) ? existing.faces : [],
    hands: Array.isArray(existing?.hands) ? existing.hands : [],
    thumbnail_width: existing?.thumbnail_width ?? img?.thumbnail_width,
    thumbnail_height: existing?.thumbnail_height ?? img?.thumbnail_height,
  };
}

function setGridIndices(items) {
  for (let i = 0; i < items.length; i += 1) {
    items[i].idx = i;
  }
}

function adjustScrollWindowForDelta(changeIndex, delta, totalLength) {
  if (!Number.isFinite(delta) || delta === 0) return;
  if (changeIndex < visibleStart.value) {
    visibleStart.value = Math.max(0, visibleStart.value + delta);
  }
  if (changeIndex < visibleEnd.value) {
    visibleEnd.value = Math.max(0, visibleEnd.value + delta);
  }
  const maxEnd = Math.max(0, totalLength);
  if (visibleEnd.value > maxEnd) visibleEnd.value = maxEnd;
  if (visibleStart.value > visibleEnd.value) {
    visibleStart.value = Math.max(0, visibleEnd.value - 1);
  }
}

function maybeRefreshThumbnailsForRange(start, end) {
  const renderStartValue = renderStart.value;
  const renderEndValue = renderEnd.value;
  if (end <= renderStartValue || start >= renderEndValue) return;
  updateVisibleThumbnails();
}

function fetchThumbnailsForRangeNow(start, end, reason = "manual-now") {
  if (!Number.isFinite(start) || !Number.isFinite(end)) return;
  const safeStart = Math.max(0, Math.floor(start));
  const safeEnd = Math.max(safeStart, Math.floor(end));
  if (safeEnd <= safeStart) return;

  void fetchThumbnailsBatch(safeStart, safeEnd, { reason, force: true });
}

// ============================================================
// STACK — VISUAL
// ============================================================
function getStackCardStyle(img) {
  if (!img) return {};
  if (!isStackExpandedForImage(img)) {
    return {};
  }
  const color = applyStackBackgroundAlpha(getStackCardColor(img));
  if (!color) return {};
  return {
    backgroundColor: color,
    borderRadius: "0px",
    boxShadow: "none",
  };
}

function getStackCardColor(img) {
  if (!img) return null;
  if (typeof img.stackColor === "string" && img.stackColor) {
    return img.stackColor;
  }
  const stackIndex =
    typeof img.stackIndex === "number"
      ? img.stackIndex
      : typeof img.stack_index === "number"
        ? img.stack_index
        : null;
  if (typeof stackIndex === "number") {
    return getStackColor(stackIndex);
  }
  const stackId = getPictureStackId(img);
  if (stackId == null) return null;
  // Use visual order entry: index drives hue, row drives lightness, col drives saturation.
  const visualEntry = stackVisualOrderMap.value.get(stackId);
  if (visualEntry != null) {
    return getStackColor(visualEntry.index, visualEntry.row, visualEntry.col);
  }
  const index = getStackColorIndexFromId(stackId);
  if (index === null) return null;
  return getStackColor(index);
}

function getStackBadgeIconStyle(img) {
  const color = getStackCardColor(img);
  if (!color) return {};
  return {
    color,
  };
}

function getStackBandStyle(img) {
  if (!img || !getPictureStackId(img)) return null;
  if (!isStackExpandedForImage(img)) return null;
  const color = getStackCardColor(img);
  if (!color) return null;
  return {
    borderBottom: `8px solid ${color}`,
  };
}

// ============================================================
// STACK — EXPAND / COLLAPSE
// ============================================================
function getRenderedStackMemberIds(stackId) {
  if (!stackId) return [];
  return allGridImages.value
    .filter((item) => getPictureStackId(item) === stackId && item?.id != null)
    .map((item) => String(item.id));
}

function rebuildGridImagesFromLastFetch() {
  const source = Array.isArray(lastFetchedGridImages.value)
    ? lastFetchedGridImages.value
    : [];
  syncExpandAllStacksFromFetchedImages();
  const collapsed = collapseStackImages(source);
  const newImages = mapGridImages(collapsed);
  allGridImages.value = newImages;
  if (visibleStart.value >= newImages.length) {
    const cols = Math.max(1, props.columns || 1);
    const windowCount = Math.max(cols, divisibleViewWindow.value || cols);
    visibleStart.value = 0;
    visibleEnd.value = Math.min(newImages.length, windowCount);
  } else if (visibleEnd.value > newImages.length) {
    visibleEnd.value = newImages.length;
  }
  invalidateVisibleThumbnailRanges();
  updateVisibleThumbnails();
}

async function refreshExpandedStacksAfterFetch() {
  const expanded = Array.from(expandedStackIds.value || []);
  if (!expanded.length) return;

  // Only eagerly load members for stacks whose leader is within the current
  // render window. Out-of-viewport stacks stay in expandedStackIds (so the
  // badge count renders) but their members are fetched lazily on scroll.
  const fetchStart = Math.max(0, visibleStart.value - renderBuffer.value);
  const fetchEnd = visibleEnd.value + renderBuffer.value;

  const nextExpanded = new Set(expandedStackIds.value);
  for (const stackId of expanded) {
    removeExpandedStackMembers(stackId);
    const headerIndex = allGridImages.value.findIndex(
      (item) => getPictureStackId(item) === stackId,
    );
    if (headerIndex === -1) {
      nextExpanded.delete(stackId);
      continue;
    }
    // Skip loading for stacks outside the visible+buffer window.
    if (headerIndex < fetchStart || headerIndex >= fetchEnd) {
      continue;
    }
    const header = allGridImages.value[headerIndex];
    const fallbackCount = header?.stackCount ?? header?.stack_count ?? null;
    const loaded = await ensureStackMembersLoaded(stackId, fallbackCount);
    if (loaded !== false) {
      const insertedCount = insertExpandedStackMembers(stackId, fallbackCount);
      if (insertedCount <= 0) {
        nextExpanded.delete(stackId);
      }
    } else {
      nextExpanded.delete(stackId);
    }
  }
  if (nextExpanded.size !== expandedStackIds.value.size) {
    expandedStackIds.value = nextExpanded;
  }
}

// Load members for expanded stacks that are now in the visible+buffer window
// but haven't been fetched yet. Called from updateVisibleThumbnails so it
// triggers automatically as the user scrolls.
async function loadExpandedStacksInView() {
  if (!expandedStackIds.value.size) return;
  const start = Math.max(0, visibleStart.value - renderBuffer.value);
  const end = Math.min(
    allGridImages.value.length,
    visibleEnd.value + renderBuffer.value,
  );
  const slice = allGridImages.value.slice(start, end);
  const seen = new Set();
  const pending = [];
  for (const img of slice) {
    const stackId = getPictureStackId(img);
    if (!stackId || seen.has(stackId)) continue;
    seen.add(stackId);
    if (!expandedStackIds.value.has(stackId)) continue;
    const entry = expandedStackMembers.value.get(stackId);
    if (entry && Array.isArray(entry.images) && entry.images.length > 0)
      continue;
    pending.push(stackId);
  }
  if (!pending.length) return;
  for (const stackId of pending) {
    if (!expandedStackIds.value.has(stackId)) continue;
    const headerIndex = allGridImages.value.findIndex(
      (item) => getPictureStackId(item) === stackId,
    );
    if (headerIndex === -1) continue;
    const header = allGridImages.value[headerIndex];
    const fallbackCount = header?.stackCount ?? header?.stack_count ?? null;
    const loaded = await ensureStackMembersLoaded(stackId, fallbackCount);
    if (loaded !== false && expandedStackIds.value.has(stackId)) {
      removeExpandedStackMembers(stackId);
      const insertedCount = insertExpandedStackMembers(stackId, fallbackCount);
      if (insertedCount <= 0) {
        const nextExpanded = new Set(expandedStackIds.value);
        nextExpanded.delete(stackId);
        expandedStackIds.value = nextExpanded;
      }
    }
  }
}

function getLocalStackMembers(stackId) {
  if (!stackId) return [];
  const source = Array.isArray(lastFetchedGridImages.value)
    ? lastFetchedGridImages.value
    : [];
  if (!source.length) return [];
  const members = source.filter((img) => getPictureStackId(img) === stackId);
  const activeSort = String(props.selectedSort || "").toUpperCase();
  const useBackendOrder = !!activeSort && activeSort !== STACKS_SORT_KEY;
  return useBackendOrder ? members : sortStackMembers(members);
}

function cacheExpandedStackMembers(stackId, members) {
  if (!stackId || !Array.isArray(members) || members.length === 0) return false;
  const activeSort = String(props.selectedSort || "").toUpperCase();
  const useBackendOrder = !!activeSort && activeSort !== STACKS_SORT_KEY;
  const sorted = useBackendOrder ? members.slice() : sortStackMembers(members);
  const ordered = sorted
    .filter((img) => img && img.id != null)
    .map((img) =>
      img.stack_id !== undefined || img.stackId !== undefined
        ? img
        : { ...img, stack_id: normalizeStackIdValue(stackId) },
    );
  if (!ordered.length) return false;
  const nextMembers = new Map(expandedStackMembers.value);
  nextMembers.set(stackId, {
    ids: ordered.map((img) => String(img.id)),
    images: ordered,
  });
  expandedStackMembers.value = nextMembers;
  return true;
}

function getExpandedStackCount(stackId, fallbackCount) {
  const entry = expandedStackMembers.value.get(stackId);
  const ids = Array.isArray(entry?.ids) ? entry.ids : [];
  if (ids.length) return ids.length;
  const images = Array.isArray(entry?.images) ? entry.images : [];
  if (images.length) return images.length;
  const fallback = Number(fallbackCount ?? 0);
  return Number.isFinite(fallback) && fallback > 0 ? fallback : 1;
}

function buildExpandedStackImages(stackId, fallbackImg, stackCount) {
  const entry = expandedStackMembers.value.get(stackId);
  const ids = Array.isArray(entry?.ids) ? entry.ids : [];
  const images = Array.isArray(entry?.images) ? entry.images : [];
  const activeSort = String(props.selectedSort || "").toUpperCase();
  const useBackendOrder = !!activeSort && activeSort !== STACKS_SORT_KEY;
  const sourceImages = ids.length
    ? images
    : useBackendOrder
      ? images.slice()
      : sortStackMembers(images);
  const imageById = new Map(
    sourceImages
      .filter((img) => img && img.id != null)
      .map((img) => [String(img.id), img]),
  );
  const ordered = [];
  const seen = new Set();
  const stackValue = normalizeStackIdValue(stackId);
  const addImage = (img) => {
    if (!img || img.id == null) return;
    const key = String(img.id);
    if (seen.has(key)) return;
    seen.add(key);
    const withStack =
      img.stack_id !== undefined || img.stackId !== undefined
        ? img
        : { ...img, stack_id: stackValue };
    ordered.push(withStack);
  };

  if (ids.length) {
    for (const id of ids) {
      addImage(imageById.get(String(id)));
    }
  } else {
    for (const img of sourceImages) {
      addImage(img);
    }
  }

  if (fallbackImg?.id != null && !seen.has(String(fallbackImg.id))) {
    addImage(fallbackImg);
  }

  if (ordered.length) {
    ordered[0] = { ...ordered[0], stackCount };
  }
  return ordered;
}

function insertExpandedStackMembers(stackId, fallbackCount) {
  if (!stackId) return 0;
  const items = allGridImages.value.slice();
  if (!items.length) return 0;
  const headerIndex = items.findIndex(
    (item) => getPictureStackId(item) === stackId,
  );
  if (headerIndex === -1) return 0;
  const header = items[headerIndex];
  const stackCount = getExpandedStackCount(
    stackId,
    fallbackCount ?? header?.stackCount,
  );
  const expanded = buildExpandedStackImages(stackId, header, stackCount);
  if (!expanded.length) return 0;
  const headerId = header?.id != null ? String(header.id) : null;
  const filtered = items.filter((item) => {
    if (getPictureStackId(item) !== stackId) return true;
    if (headerId && item?.id != null) {
      return String(item.id) === headerId;
    }
    return false;
  });
  const filteredHeaderIndex = filtered.findIndex(
    (item) => getPictureStackId(item) === stackId,
  );
  if (filteredHeaderIndex === -1) return 0;
  const existingById = new Map(
    allGridImages.value
      .filter((img) => img && img.id != null)
      .map((img) => [getPictureId(img.id), img]),
  );
  // Keep the existing grid header image as slot-0: it already has the right
  // thumbnail and idx. The backend may return a different first member
  // depending on the active sort (e.g. SCORE ASC returns the lowest-score
  // member first, not the leader shown in the collapsed grid). Spreading header
  // last ensures its id/thumbnail are never overwritten by the backend response.
  const expandedHeader = expanded[0];
  const mergedHeader = hydrateGridImage(
    { ...expandedHeader, ...header, stackCount },
    0,
    existingById,
  );
  const insertItems = expanded
    .filter((img) => img && img.id != null)
    .filter((img) => String(img.id) !== headerId)
    .map((img) => hydrateGridImage(img, 0, existingById));
  const insertIndex = filteredHeaderIndex + 1;
  const before = filtered.slice(0, filteredHeaderIndex);
  const after = filtered.slice(filteredHeaderIndex + 1);
  const result = [...before, mergedHeader, ...insertItems, ...after];
  setGridIndices(result);
  allGridImages.value = result;
  const insertCount = insertItems.length;
  if (insertCount > 0) {
    loadedRanges.value = shiftRangesForDelta(
      loadedRanges.value,
      insertIndex,
      insertCount,
    );
    pendingRanges = shiftRangesForDelta(
      pendingRanges,
      insertIndex,
      insertCount,
    );
    adjustScrollWindowForDelta(insertIndex, insertCount, result.length);
    markVisibleFetchSuppressedForExpand(
      insertIndex,
      insertIndex + insertCount + 1,
    );
    fetchThumbnailsForRangeNow(
      insertIndex,
      insertIndex + insertCount + 1,
      "stack-expand-insert",
    );
  } else {
    maybeRefreshThumbnailsForRange(insertIndex, insertIndex + 1);
  }
  return insertCount;
}

function removeExpandedStackMembers(stackId) {
  if (!stackId) return;
  const items = allGridImages.value.slice();
  if (!items.length) return;
  const headerIndex = items.findIndex(
    (item) => getPictureStackId(item) === stackId,
  );
  if (headerIndex === -1) return;
  let removedCount = 0;
  let keptHeader = false;
  const filtered = items.filter((item) => {
    if (getPictureStackId(item) !== stackId) return true;
    if (!keptHeader) {
      keptHeader = true;
      return true;
    }
    removedCount += 1;
    return false;
  });
  if (filtered.length === items.length) return;
  setGridIndices(filtered);
  allGridImages.value = filtered;
  if (removedCount > 0) {
    const removeStart = headerIndex + 1;
    const removeEnd = headerIndex + 1 + removedCount;
    loadedRanges.value = shiftRangesForDelta(
      loadedRanges.value,
      removeStart,
      -removedCount,
      removeEnd,
    );
    pendingRanges = shiftRangesForDelta(
      pendingRanges,
      removeStart,
      -removedCount,
      removeEnd,
    );
    adjustScrollWindowForDelta(removeStart, -removedCount, filtered.length);
    maybeRefreshThumbnailsForRange(removeStart, removeStart + 1);
  }
}

function isStackExpandedForImage(img) {
  const stackId = getPictureStackId(img);
  if (!stackId) return false;
  return expandedStackIds.value.has(stackId);
}

function collectExpandableStackIds(images) {
  if (!Array.isArray(images) || images.length === 0) return [];
  const counts = new Map();
  for (const img of images) {
    const stackId = getPictureStackId(img);
    if (!stackId) continue;
    counts.set(stackId, (counts.get(stackId) || 0) + 1);
  }

  const expandable = new Set();
  for (const img of images) {
    const stackId = getPictureStackId(img);
    if (!stackId) continue;
    const countFromImage = Number(img?.stackCount ?? img?.stack_count ?? 0);
    const countFromPresence = counts.get(stackId) || 0;
    if (countFromImage > 1 || countFromPresence > 1) {
      expandable.add(stackId);
    }
  }
  return Array.from(expandable);
}

function emitStackStats() {
  const expandable = collectExpandableStackIds(lastFetchedGridImages.value);
  const expandableSet = new Set(expandable);
  let expanded = 0;
  for (const stackId of expandedStackIds.value || []) {
    if (expandableSet.has(stackId)) {
      expanded += 1;
    }
  }
  emit("update:stack-stats", {
    expanded,
    total: expandable.length,
  });
}

function syncExpandAllStacksFromFetchedImages() {
  const autoIds = collectExpandableStackIds(lastFetchedGridImages.value);
  const autoIdSet = new Set(autoIds);
  const currentIds = Array.from(expandedStackIds.value || []);
  const nextIds = new Set(currentIds.filter((id) => autoIdSet.has(id)));
  let changed = false;
  for (const stackId of currentIds) {
    if (!nextIds.has(stackId)) {
      changed = true;
      break;
    }
  }
  if (changed) {
    expandedStackIds.value = nextIds;
  }
}

async function expandAllStacks() {
  const autoIds = collectExpandableStackIds(lastFetchedGridImages.value);
  expandedStackIds.value = new Set(autoIds);
  rebuildGridImagesFromLastFetch();
  await refreshExpandedStacksAfterFetch();
}

async function collapseAllStacks() {
  expandedStackIds.value = new Set();
  rebuildGridImagesFromLastFetch();
  await refreshExpandedStacksAfterFetch();
}

async function ensureStackMembersLoaded(stackId, expectedCount = null) {
  if (!stackId) return false;
  const expected = Number(expectedCount ?? 0);
  const minExpected = Number.isFinite(expected) && expected > 0 ? expected : 0;
  const localMembers = getLocalStackMembers(stackId);
  if (
    localMembers.length &&
    (minExpected <= 0 || localMembers.length >= minExpected)
  ) {
    cacheExpandedStackMembers(stackId, localMembers);
    return true;
  }
  const existing = expandedStackMembers.value.get(stackId);
  if (existing && Array.isArray(existing.images) && existing.images.length) {
    if (minExpected <= 0 || existing.images.length >= minExpected) {
      return true;
    }
  }
  const inFlight = expandedStackLoadPromises.get(stackId);
  if (inFlight) {
    await inFlight;
    const afterWait = expandedStackMembers.value.get(stackId);
    return !!(
      afterWait &&
      Array.isArray(afterWait.images) &&
      afterWait.images.length
    );
  }

  const loadPromise = (async () => {
    const nextLoading = new Set(expandedStackLoading.value);
    nextLoading.add(stackId);
    expandedStackLoading.value = nextLoading;
    try {
      const stackUrl = new URL(
        `${props.backendUrl}/stacks/${stackId}/pictures`,
      );
      stackUrl.searchParams.set("fields", "grid");
      const activeSort = props.selectedSort ?? "";
      const isStackSort = !activeSort || activeSort === STACKS_SORT_KEY;
      if (activeSort) {
        stackUrl.searchParams.set("sort", activeSort);
      }
      if (typeof props.selectedDescending === "boolean") {
        stackUrl.searchParams.set(
          "descending",
          props.selectedDescending ? "true" : "false",
        );
      }
      const picsRes = await apiClient.get(stackUrl.toString());
      const picsData = await picsRes.data;
      const pics = Array.isArray(picsData) ? picsData : [];
      // When a real sort is active the backend already ordered the members;
      // only fall back to client-side stack-order sorting for PICTURE_STACKS
      // or when no sort is selected.
      const sorted = isStackSort ? sortStackMembers(pics) : pics;
      const ordered = sorted
        .filter((img) => img && img.id != null)
        .map((img) =>
          img.stack_id !== undefined || img.stackId !== undefined
            ? img
            : { ...img, stack_id: normalizeStackIdValue(stackId) },
        );
      const pictureIds = ordered.map((img) => String(img.id));
      const nextMembers = new Map(expandedStackMembers.value);
      nextMembers.set(stackId, {
        ids: pictureIds,
        images: ordered,
      });
      expandedStackMembers.value = nextMembers;
      return true;
    } catch (e) {
      console.error("Failed to load stack members:", e);
      return false;
    } finally {
      const cleared = new Set(expandedStackLoading.value);
      cleared.delete(stackId);
      expandedStackLoading.value = cleared;
      expandedStackLoadPromises.delete(stackId);
    }
  })();

  expandedStackLoadPromises.set(stackId, loadPromise);
  return await loadPromise;
}

async function toggleStackExpand(img) {
  const stackId = getPictureStackId(img);
  if (!stackId) return;
  if (expandedStackIds.value.has(stackId)) {
    const nextIds = new Set(expandedStackIds.value);
    nextIds.delete(stackId);
    expandedStackIds.value = nextIds;
    removeExpandedStackMembers(stackId);
    return;
  }
  const nextIds = new Set(expandedStackIds.value);
  nextIds.add(stackId);
  expandedStackIds.value = nextIds;
  const stackCount = getStackBadgeCount(img);
  let insertedCount = 0;
  const localMembers = getLocalStackMembers(stackId);
  if (localMembers.length > 1) {
    cacheExpandedStackMembers(stackId, localMembers);
    insertedCount = insertExpandedStackMembers(stackId, stackCount);
  }

  const loaded = await ensureStackMembersLoaded(stackId, stackCount);
  if (!expandedStackIds.value.has(stackId)) {
    return;
  }
  if (loaded !== false) {
    const renderedIds = getRenderedStackMemberIds(stackId);
    const latestEntry = expandedStackMembers.value.get(stackId);
    const latestIds = Array.isArray(latestEntry?.ids) ? latestEntry.ids : [];
    if (
      insertedCount > 0 &&
      latestIds.length &&
      arraysEqualByString(renderedIds, latestIds)
    ) {
      return;
    }
    removeExpandedStackMembers(stackId);
    insertedCount = insertExpandedStackMembers(stackId, stackCount);
    if (insertedCount <= 0) {
      const resetExpanded = new Set(expandedStackIds.value);
      resetExpanded.delete(stackId);
      expandedStackIds.value = resetExpanded;
      removeExpandedStackMembers(stackId);
    }
    return;
  }

  if (insertedCount <= 0) {
    const resetExpanded = new Set(expandedStackIds.value);
    resetExpanded.delete(stackId);
    expandedStackIds.value = resetExpanded;
    removeExpandedStackMembers(stackId);
  }
}

function prefetchStackMembers(img) {
  const stackId = getPictureStackId(img);
  if (!stackId) return;
  void ensureStackMembersLoaded(stackId, getStackBadgeCount(img));
}

// ============================================================
// STACK — REORDER DRAG
// ============================================================
function getStackReorderCount(stackId, fallbackCount) {
  if (!stackId) return 0;
  const entry = expandedStackMembers.value.get(stackId);
  const ids = Array.isArray(entry?.ids) ? entry.ids : [];
  if (ids.length) return ids.length;
  const images = Array.isArray(entry?.images) ? entry.images : [];
  if (images.length) return images.length;
  const fallback = Number(fallbackCount ?? 0);
  return Number.isFinite(fallback) ? fallback : 0;
}

function getDragImageIdFromEvent(event) {
  const raw = event?.dataTransfer?.getData("application/json");
  if (!raw) return null;
  try {
    const payload = JSON.parse(raw);
    if (payload?.type === "image-ids") {
      const ids = Array.isArray(payload.imageIds) ? payload.imageIds : [];
      if (ids.length === 1) return String(ids[0]);
    }
  } catch (err) {
    return null;
  }
  return null;
}

function buildStackReorderDragState(sourceId) {
  if (!sourceId) return null;
  const source = allGridImages.value.find(
    (item) => item?.id != null && String(item.id) === String(sourceId),
  );
  if (!source) return null;
  const stackId = getPictureStackId(source);
  if (!stackId || !expandedStackIds.value.has(stackId)) return null;
  const count = getStackReorderCount(stackId, getStackBadgeCount(source));
  if (count <= 1) return null;
  return { stackId, imageId: String(sourceId) };
}

function handleStackReorderDragOver(img, event) {
  let drag = stackReorderDrag.value;
  if (!drag) {
    const sourceId = getDragImageIdFromEvent(event);
    drag = buildStackReorderDragState(sourceId);
    if (drag) {
      stackReorderDrag.value = drag;
    }
  }
  if (!drag || !img?.id) return;
  const stackId = getPictureStackId(img);
  if (!stackId || stackId !== drag.stackId) return;
  event.preventDefault();
  event.stopPropagation();
  setStackReorderHoverId(img.id);
  const bounds = event?.currentTarget?.getBoundingClientRect?.();
  if (bounds && Number.isFinite(bounds.left) && Number.isFinite(bounds.width)) {
    const mid = bounds.left + bounds.width / 2;
    const side = event.clientX <= mid ? "left" : "right";
    setStackReorderHoverSide(side);
  }
  if (event?.dataTransfer) {
    event.dataTransfer.dropEffect = "move";
  }
}

function handleStackReorderDragLeave(img, event) {
  if (!stackReorderHoverId.value || !img?.id) return;
  if (String(img.id) !== stackReorderHoverId.value) return;
  const nextTarget = event?.relatedTarget;
  if (nextTarget && event?.currentTarget?.contains?.(nextTarget)) return;
  setStackReorderHoverId(null);
  setStackReorderHoverSide(null);
}

function applyStackOrderLocal(stackId, orderedIds) {
  const items = allGridImages.value.slice();
  const stackItems = items.filter(
    (item) => getPictureStackId(item) === stackId && item?.id != null,
  );
  if (stackItems.length <= 1) return;
  const stackCount = getStackReorderCount(
    stackId,
    getStackBadgeCount(stackItems[0]),
  );
  const orderedMembers = buildStackReorderedMembers(
    stackItems,
    orderedIds,
    stackCount,
  );
  if (!orderedMembers.length) return;
  const nextGrid = applyStackOrderToList(items, stackId, orderedMembers);
  setGridIndices(nextGrid);
  allGridImages.value = nextGrid;

  const nextMembers = new Map(expandedStackMembers.value);
  nextMembers.set(stackId, {
    ids: orderedMembers.map((item) => String(item.id)),
    images: orderedMembers,
  });
  expandedStackMembers.value = nextMembers;

  const nextFetched = applyStackOrderToList(
    Array.isArray(lastFetchedGridImages.value)
      ? lastFetchedGridImages.value.slice()
      : [],
    stackId,
    orderedMembers,
  );
  lastFetchedGridImages.value = nextFetched;
}

async function persistStackOrder(stackId, orderedIds, previousIds) {
  if (!stackId || !orderedIds.length) return;
  try {
    await apiClient.patch(`${props.backendUrl}/stacks/${stackId}/order`, {
      picture_ids: orderedIds.map((id) => Number(id)).filter(Number.isFinite),
    });
  } catch (err) {
    alert(`Failed to save stack order: ${err?.message || err}`);
    if (Array.isArray(previousIds) && previousIds.length) {
      applyStackOrderLocal(stackId, previousIds);
    }
  }
}

function handleStackReorderDrop(img, event) {
  let drag = stackReorderDrag.value;
  stackReorderDrag.value = null;
  const hoverSide = stackReorderHoverSide.value;
  setStackReorderHoverId(null);
  setStackReorderHoverSide(null);
  if (!drag) {
    const sourceId = getDragImageIdFromEvent(event);
    drag = buildStackReorderDragState(sourceId);
  }
  if (!drag || !img?.id) return;
  const stackId = getPictureStackId(img);
  if (!stackId || stackId !== drag.stackId) return;
  event.preventDefault();
  event.stopPropagation();
  const sourceId = String(drag.imageId);
  const targetId = String(img.id);
  if (sourceId === targetId) return;

  const stackItems = allGridImages.value.filter(
    (item) => getPictureStackId(item) === stackId && item?.id != null,
  );
  const currentIds = stackItems.map((item) => String(item.id));
  const fromIndex = currentIds.indexOf(sourceId);
  const toIndex = currentIds.indexOf(targetId);
  if (fromIndex === -1 || toIndex === -1 || fromIndex === toIndex) return;

  const nextIds = currentIds.slice();
  const [moved] = nextIds.splice(fromIndex, 1);
  const targetIndex = nextIds.indexOf(targetId);
  let insertIndex = targetIndex;
  if (hoverSide === "right") {
    insertIndex = targetIndex + 1;
  }
  if (insertIndex < 0) insertIndex = 0;
  if (insertIndex > nextIds.length) insertIndex = nextIds.length;
  nextIds.splice(insertIndex, 0, moved);

  applyStackOrderLocal(stackId, nextIds);
  void persistStackOrder(stackId, nextIds, currentIds);
}

// Fetch total image count for current filters
// ============================================================
// GRID FETCH
// ============================================================
async function fetchAllGridImages(options = {}) {
  const force = options?.force === true;
  const activeSortKey = getActiveSortKey();
  const isSortedFetch = !!activeSortKey;
  let sortedFetchStartedAt = 0;
  // Capture scroll-preservation intent *synchronously* before any await so
  // that it is not affected by the gridVersion watcher clearing it later.
  const fetchStartedWithPreserveScroll = preserveScrollOnNextFetch.value;
  if (
    fetchStartedWithPreserveScroll &&
    pendingScrollTop.value === null &&
    scrollWrapper.value
  ) {
    pendingScrollTop.value = scrollWrapper.value.scrollTop;
  }
  const fetchKey = buildGridFetchKey();
  const now = Date.now();
  if (!force && imagesLoading.value && lastFetchKey.value === fetchKey) {
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
    !force &&
    lastFetchSuccess.value.key === fetchKey &&
    now - lastFetchSuccess.value.at < 1200
  ) {
    return;
  }
  if (
    !force &&
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
  if (isSortedFetch) {
    sortedFetchStartedAt = getNowMs();
    startSmartScoreProgress(loadId, activeSortKey);
  }
  try {
    let images = [];
    const requestId = Date.now();
    fetchAllGridImages.lastRequestId = requestId;
    if (props.selectedSort === STACKS_SORT_KEY) {
      const threshold = getStackThreshold(props.stackThreshold);
      const stackParams = buildStackQueryParams();
      const url = `${
        props.backendUrl
      }/pictures/stacks?threshold=${encodeURIComponent(threshold)}${
        stackParams ? `&${stackParams}` : ""
      }`;
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
    } else if (props.searchQuery && props.searchQuery.trim()) {
      // Use /pictures/search endpoint for text search
      const params = buildPictureIdsQueryParams();
      const url = `${
        props.backendUrl
      }/pictures/search?query=${encodeURIComponent(
        props.searchQuery.trim(),
      )}&threshold=0.1&top_n=10000${params ? `&${params}` : ""}`;
      const res = await apiClient.get(url);
      const data = await res.data;
      images = data;
    } else if (hasSetSelection.value && !isSetOverlapView.value) {
      const params = buildPictureIdsQueryParams();
      const url = `${props.backendUrl}/picture_sets/${primarySelectedSetId.value}${
        params ? `?${params}` : ""
      }`;
      const res = await apiClient.get(url);
      const data = await res.data;
      images = data.pictures || [];
    } else {
      const params = buildPictureIdsQueryParams();
      // Only use allowed parameters: sort, offset, limit, threshold
      const url = `${props.backendUrl}/pictures?offset=0${
        params ? `&${params}` : ""
      }`;
      const res = await apiClient.get(url);
      const data = await res.data;
      images = data;
    }
    if (fetchAllGridImages.lastRequestId !== requestId) {
      return;
    }
    lastFetchedGridImages.value = Array.isArray(images) ? images.slice() : [];
    syncExpandAllStacksFromFetchedImages();
    images = collapseStackImages(images);
    const shouldHighlight = highlightNextFetch.value && hasLoadedOnce.value;
    const nextIdSet = new Set(
      Array.isArray(images)
        ? images.map((img) => getPictureId(img?.id)).filter((id) => id !== null)
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
    const newImages = mapGridImages(images);
    resetThumbnailState();
    allGridImages.value = newImages;
    if (isSetOverlapView.value) {
      totalCurrentCategoryCount.value = newImages.length;
    }
    const cols = props.columns || 1;
    const windowCount = Math.max(cols, divisibleViewWindow.value || cols);
    if (!fetchStartedWithPreserveScroll) {
      // Normal (non-preserve) fetch: jump to top so thumbnails load from index 0.
      visibleStart.value = 0;
      visibleEnd.value = Math.min(newImages.length, windowCount);
    } else {
      // Scroll-preserving fetch: keep visibleStart/End as-is so
      // updateVisibleThumbnails loads the range the user is actually viewing.
      visibleEnd.value = Math.min(visibleEnd.value, newImages.length);
      if (visibleStart.value > visibleEnd.value)
        visibleStart.value = Math.max(0, visibleEnd.value - 1);
    }
    if (initialRender.value) {
      const prefetchEnd = Math.min(
        newImages.length,
        visibleEnd.value + divisibleViewWindow.value,
      );
      fetchThumbnailsBatch(visibleStart.value, prefetchEnd);
    }
    await refreshExpandedStacksAfterFetch();
    await maybeRefreshOverlayForComfyui();
    requestAnimationFrame(() => {
      if (initialRender.value) {
        initialRender.value = false;
        updateVisibleThumbnails();
      }
    });
    lastFetchSuccess.value = { key: fetchKey, at: Date.now() };
    if (isSortedFetch) {
      const elapsedMs = Math.max(0, getNowMs() - sortedFetchStartedAt);
      completeSmartScoreProgress(loadId, elapsedMs, true);
    }
  } catch (e) {
    if (fetchAllGridImages.lastRequestId !== requestId) {
      return;
    }
    imagesError.value = e.message;
    // Don't wipe the grid on a transient error while the overlay is open —
    // the user would see the grid flash empty behind the overlay.
    if (!overlayOpen.value) {
      allGridImages.value = [];
    }
    lastFetchError.value = { key: fetchKey, at: Date.now() };
    if (isSortedFetch) {
      const elapsedMs = Math.max(0, getNowMs() - sortedFetchStartedAt);
      completeSmartScoreProgress(loadId, elapsedMs, false);
    }
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

  try {
    let url = `${props.backendUrl}/characters/${props.allPicturesId}/summary`;
    const selectedCharacter = String(props.selectedCharacter ?? "");
    if (isSetOverlapView.value) {
      totalCurrentCategoryCount.value = Number(allGridImages.value.length) || 0;
      return;
    }
    const selectedSetId = primarySelectedSetId.value;
    if (
      selectedSetId !== null &&
      selectedSetId !== undefined &&
      String(selectedSetId) !== ""
    ) {
      const setRes = await apiClient.get(`${props.backendUrl}/picture_sets`);
      const setList = await setRes.data;
      const selectedSetNumericId = Number(selectedSetId);
      const selectedSet = Array.isArray(setList)
        ? setList.find((item) => {
            const itemId = Number(item?.id);
            if (Number.isFinite(selectedSetNumericId)) {
              return Number.isFinite(itemId) && itemId === selectedSetNumericId;
            }
            return String(item?.id) === String(selectedSetId);
          })
        : null;
      totalCurrentCategoryCount.value = Number(selectedSet?.picture_count) || 0;
      return;
    }
    if (selectedCharacter === String(props.allPicturesId)) {
      if (props.projectViewMode === "project") {
        const pid =
          props.selectedProjectId != null
            ? props.selectedProjectId
            : "UNASSIGNED";
        url = `${props.backendUrl}/projects/${pid}/summary`;
      }
    } else if (selectedCharacter === String(props.unassignedPicturesId)) {
      if (props.projectViewMode === "project") {
        const pid =
          props.selectedProjectId != null
            ? props.selectedProjectId
            : "UNASSIGNED";
        url = `${props.backendUrl}/characters/${props.unassignedPicturesId}/summary?project_id=${pid}`;
      } else {
        url = `${props.backendUrl}/characters/${props.unassignedPicturesId}/summary`;
      }
    } else if (selectedCharacter === String(props.scrapheapPicturesId)) {
      url = `${props.backendUrl}/characters/${props.scrapheapPicturesId}/summary`;
    } else if (
      selectedCharacter &&
      !hasSetSelection.value &&
      selectedCharacter !== String(props.allPicturesId)
    ) {
      url = `${props.backendUrl}/characters/${selectedCharacter}/summary`;
    }

    const scopedRes = await apiClient.get(url);
    const scopedData = await scopedRes.data;
    totalCurrentCategoryCount.value = Number(scopedData.image_count) || 0;
  } catch (e) {
    console.warn("[ImageGrid.vue] Failed to fetch scoped category count:", e);
    totalCurrentCategoryCount.value = 0;
  }
}

function _resetGridState() {
  gridReady.value = false;
  emptyStateDelayPassed.value = false;
  resetThumbnailState();
  allGridImages.value = [];
  lastFetchedGridImages.value = [];
  expandedStackIds.value = new Set();
  expandedStackMembers.value = new Map();
  expandedStackLoading.value = new Set();
  selectedImageIds.value = [];
  lastSelectedImageId = null;
  initialRender.value = true;
}

watch(
  [
    () => props.selectedCharacter,
    () => props.selectedSet,
    () => props.selectedSetIds,
    () => props.projectViewMode,
    () => props.selectedProjectId,
    () => props.searchQuery,
    () => props.selectedSort,
    () => props.stackThreshold,
  ],
  () => {
    _resetGridState();
    updateSelectedGroupName();
    fetchAllPicturesCount();
    debouncedFetchAllGridImages.cancel();
    fetchAllGridImages({ force: true });
  },
);

watch(
  [
    () => props.mediaTypeFilter,
    () => props.comfyuiModelFilter,
    () => props.comfyuiLoraFilter,
    () => props.minScoreFilter,
  ],
  () => {
    _resetGridState();
    visibleStart.value = 0;
    visibleEnd.value = 0;
    fetchAllGridImages({ force: true }).then(() => {
      updateVisibleThumbnails();
    });
  },
);

watch(
  () => props.showStacks,
  async (expandAllStacksEnabled) => {
    if (expandAllStacksEnabled) {
      syncExpandAllStacksFromFetchedImages();
    } else {
      expandedStackIds.value = new Set();
    }
    rebuildGridImagesFromLastFetch();
    await refreshExpandedStacksAfterFetch();
  },
);

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

watch(
  () => props.compactMode,
  () => {
    updateRowHeightFromGrid();
    updateVisibleThumbnails();
  },
);

// ============================================================
// THUMBNAIL TRACKING STATE
// ============================================================
// Track loaded batch ranges to avoid duplicate requests
const loadedRanges = ref([]);
// Debounce timer for scroll-triggered fetches
let thumbFetchTimeout = null;
let pendingRanges = [];
const thumbnailRequestEpoch = ref(0);
const suppressVisibleThumbFetch = ref({
  until: 0,
  start: 0,
  end: 0,
});

function shouldSuppressVisibleWindowFetch(start, end) {
  const now = Date.now();
  const token = suppressVisibleThumbFetch.value;
  if (!token || now > Number(token.until || 0)) return false;
  if (!isRangeOverlap(start, end, token.start, token.end)) return false;

  const visible = allGridImages.value.slice(start, end);
  const missingOutsideSuppressed = visible.some((img, idx) => {
    if (!img || img.thumbnail) return false;
    const globalIndex = start + idx;
    return globalIndex < token.start || globalIndex >= token.end;
  });
  return !missingOutsideSuppressed;
}

function markVisibleFetchSuppressedForExpand(start, end) {
  suppressVisibleThumbFetch.value = {
    until: Date.now() + 350,
    start,
    end,
  };
}

function resetThumbnailState() {
  loadedRanges.value = [];
  pendingRanges = [];
  suppressVisibleThumbFetch.value = {
    until: 0,
    start: 0,
    end: 0,
  };
  if (thumbFetchTimeout) {
    clearTimeout(thumbFetchTimeout);
    thumbFetchTimeout = null;
  }
  thumbnailRequestEpoch.value += 1;
  for (const key of Object.keys(thumbnailLoadedMap)) {
    delete thumbnailLoadedMap[key];
  }
  for (const key of Object.keys(thumbnailAssignedAtMap)) {
    delete thumbnailAssignedAtMap[key];
  }
  for (const timer of thumbnailRetryTimers.values()) {
    clearTimeout(timer);
  }
  thumbnailRetryTimers.clear();
  for (const key of Object.keys(thumbnailRetryCounts)) {
    delete thumbnailRetryCounts[key];
  }
}

// ============================================================
// VIEWPORT STATE
// ============================================================

const visibleStart = ref(0);
const visibleEnd = ref(0);

const rowHeight = ref(
  Math.round(
    Math.min(
      MAX_THUMBNAIL_SIZE,
      Math.max(MIN_THUMBNAIL_SIZE, props.thumbnailSize || MIN_THUMBNAIL_SIZE),
    ) + (props.compactMode ? 0 : THUMBNAIL_INFO_ROW_HEIGHT),
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
    Math.max(1, rawWidth || MIN_THUMBNAIL_SIZE),
  );
}

function updateRowHeightFromGrid() {
  const columnWidth = getGridColumnWidth();
  const infoHeight = props.compactMode ? 0 : THUMBNAIL_INFO_ROW_HEIGHT;
  rowHeight.value = Math.round(columnWidth + infoHeight);
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

watch(
  [() => expandedStackIds.value, () => lastFetchedGridImages.value],
  () => {
    emitStackStats();
  },
  { immediate: true },
);

watch(
  [() => props.showFaceBboxes, () => allGridImages.value.length],
  ([faceEnabled, length], [prevFace, prevLength]) => {
    if (!faceEnabled) return;
    if (length <= 0) return;
    if (faceEnabled === prevFace && length === prevLength) {
      return;
    }
    invalidateVisibleThumbnailRanges();
  },
);

// ============================================================
// MEDIA FILTERING + EMPTY STATE
// ============================================================
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
  if (isSetOverlapView.value) {
    return "No overlap";
  }
  if (isScrapheapView.value) {
    return "No pictures in the scrap heap";
  }
  return totalAllPicturesCount.value > 0
    ? "No pictures match the current filters"
    : "No pictures in the database.";
});

const emptyStateSubtitle = computed(() => {
  if (isSetOverlapView.value) {
    return "The picture sets have no overlap.";
  }
  if (isScrapheapView.value) {
    return "Are all your pictures that good?";
  }
  return totalAllPicturesCount.value > 0
    ? "Try clearing filters, adjusting your search, or switching sets."
    : "Add pictures by dragging them here.";
});

const emptyStateImage = computed(() => {
  return isScrapheapView.value ? "/EmptyTrash.png" : "/Empty.png";
});

const emptyStateAlt = computed(() => {
  return isScrapheapView.value ? "Empty scrap heap" : "No images";
});

const isDarkThemeActive = computed(() => {
  const mode = String(props.themeMode || "light").toLowerCase();
  if (mode === "dark") return true;
  if (mode === "light") return false;
  if (mode === "system") {
    return (
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-color-scheme: dark)").matches
    );
  }
  return false;
});

const emptyStateImageStyle = computed(() => {
  if (!isDarkThemeActive.value) return {};
  return {
    filter: "invert(1) brightness(1.08) contrast(0.92)",
  };
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
// ============================================================
// THUMBNAIL BATCH FETCH
// ============================================================
async function fetchThumbnailsBatch(start, end, meta = {}) {
  if (start === undefined || start === null) {
    start = renderStart.value;
  }
  if (end === undefined || end === null) {
    end = renderEnd.value;
  }

  const requestEpoch = thumbnailRequestEpoch.value;

  if (rangeCovers(pendingRanges, start, end)) {
    return;
  }
  if (!meta?.force && rangeCovers(loadedRanges.value, start, end)) {
    return;
  }
  pendingRanges.push([start, end]);
  // Fetch batch metadata for visible range
  try {
    let images = [];
    let ids = [];
    // Use allGridImages directly regardless of whether we're in a picture-set
    // view. The picture-set endpoint returns a flat leader-only list and
    // doesn't know about expanded stack members; slicing it with absolute
    // indices would overwrite expanded members with wrong images and leave
    // placeholder thumbnails permanently broken.
    images = allGridImages.value.slice(start, end);
    ids = images.map((img) => img.id);
    // Prepare grid image objects
    const gridImages = images.map((img, idx) => ({
      ...img,
      score: img.score ?? 0,
      idx: start + idx, // Ensure idx is global index
      thumbnail: img?.thumbnail ?? null,
      faces: Array.isArray(img?.faces) ? img.faces : [],
      hands: Array.isArray(img?.hands) ? img.hands : [],
      penalised_tags: Array.isArray(img?.penalised_tags)
        ? img.penalised_tags
        : [],
      thumbnail_width: img?.thumbnail_width,
      thumbnail_height: img?.thumbnail_height,
    }));
    // Separate images: those missing a thumbnail need a full fetch; those that
    // already have one still need penalised_tags refreshed (e.g. after a tag
    // removal on a stack-head image whose thumbnail was carried over from the
    // previous grid state and was therefore never re-fetched).
    const missingThumbIds = new Set(
      gridImages
        .filter(
          (img) => img.id !== null && img.id !== undefined && !img.thumbnail,
        )
        .map((img) => String(img.id)),
    );
    ids = Array.from(
      new Set(
        gridImages
          .filter((img) => img.id !== null && img.id !== undefined)
          .map((img) => String(img.id)),
      ),
    );
    const requestedIdPreview = ids.slice(0, 8);
    let overlayNeedsRedraw = false;
    if (ids.length) {
      const thumbRes = await apiClient.post(
        `${props.backendUrl}/pictures/thumbnails`,
        JSON.stringify({ ids }),
      );
      const thumbData = await thumbRes.data;
      if (requestEpoch !== thumbnailRequestEpoch.value) {
        return;
      }
      const requestedIds = new Set(ids);
      for (const gridImg of gridImages) {
        if (!requestedIds.has(String(gridImg.id))) {
          continue;
        }
        const thumbObj = thumbData[String(gridImg.id)];
        // Only overwrite thumbnail URL for images that didn't already have one.
        if (missingThumbIds.has(String(gridImg.id))) {
          const thumbnailUrl =
            thumbObj && thumbObj.thumbnail ? thumbObj.thumbnail : null;
          const previousThumbnail = gridImg.thumbnail || null;
          gridImg.thumbnail = thumbnailUrl
            ? thumbnailUrl.startsWith("http")
              ? thumbnailUrl
              : `${props.backendUrl}${thumbnailUrl}`
            : null;
          if (
            gridImg.id != null &&
            gridImg.thumbnail &&
            gridImg.thumbnail !== previousThumbnail
          ) {
            thumbnailAssignedAtMap[gridImg.id] = performance.now();
          }
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
        // Always refresh penalised_tags regardless of thumbnail cache state.
        gridImg.penalised_tags =
          thumbObj && Array.isArray(thumbObj.penalised_tags)
            ? thumbObj.penalised_tags
            : [];
      }
    }
    // Insert/update images at their correct indices
    if (requestEpoch !== thumbnailRequestEpoch.value) {
      return;
    }
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
  } catch (err) {
    console.error("[BATCH ERROR]", err);
  } finally {
    pendingRanges = pendingRanges.filter(
      ([rangeStart, rangeEnd]) => rangeStart !== start || rangeEnd !== end,
    );
  }
}

// ============================================================
// SCROLL + VIEWPORT UPDATE
// ============================================================
function updateVisibleThumbnails() {
  let start = Math.max(0, visibleStart.value - renderBuffer.value);
  let end = Math.min(
    allGridImages.value.length,
    visibleEnd.value + renderBuffer.value,
  );
  if (shouldSuppressVisibleWindowFetch(start, end)) {
    return;
  }

  // Lazily load members for expanded stacks that have scrolled into view.
  void loadExpandedStacksInView();

  if (rangeCovers(loadedRanges.value, start, end)) return;
  if (rangeCovers(pendingRanges, start, end)) return;

  // Debounce fetches to avoid excessive requests
  if (thumbFetchTimeout) clearTimeout(thumbFetchTimeout);

  const requestEpoch = thumbnailRequestEpoch.value;
  thumbFetchTimeout = setTimeout(async () => {
    if (requestEpoch !== thumbnailRequestEpoch.value) {
      return;
    }
    await fetchThumbnailsBatch(start, end, {
      reason: "visible-window",
    });
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

// ============================================================
// CLICK HANDLERS
// ============================================================
function scrollCursorIntoView(idx) {
  if (!scrollWrapper.value) return;
  const cols = Math.max(1, props.columns || 1);
  const row = Math.floor(idx / cols);
  const itemTop = row * rowHeight.value;
  const itemBottom = itemTop + rowHeight.value;
  const scrollTop = scrollWrapper.value.scrollTop;
  const clientHeight = scrollWrapper.value.clientHeight;
  if (itemTop < scrollTop) {
    scrollWrapper.value.scrollTop = itemTop;
  } else if (itemBottom > scrollTop + clientHeight) {
    scrollWrapper.value.scrollTop = itemBottom - clientHeight;
  }
}

function handleImageCardClick(img, idx, event) {
  if (!img.id) return;
  cursorIdx.value = idx;
  const isCtrl = event.ctrlKey || event.metaKey;
  const isShift = event.shiftKey;
  let newSelection = [];
  const allGrid = allGridImages.value;
  const anchorIndex =
    lastSelectedImageId != null
      ? allGrid.findIndex(
          (item) =>
            getPictureId(item?.id) === getPictureId(lastSelectedImageId),
        )
      : -1;
  if (isCtrl) {
    // Toggle selection
    newSelection = [...selectedImageIds.value];
    if (newSelection.includes(img.id)) {
      newSelection = newSelection.filter((id) => id !== img.id);
    } else {
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
}

function handleThumbnailClick(img, idx, event) {
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
    selectedImageIds.value = [];
    lastSelectedImageId = null;
    cursorIdx.value = null;
  }
}

// ============================================================
// TAGS + METADATA
// ============================================================

// updateColumns removed; columns is now controlled by prop

async function _afterTagMutation(imageId) {
  if (props.applyTagFilter) {
    if (overlayOpen.value) {
      // The overlay is showing live tag state already. Defer the full
      // tag-filtered refetch until the overlay is closed to prevent the
      // grid from unexpectedly going empty in the background.
      pendingTagFilterRefresh.value = true;
      refreshGridImage(imageId);
      return;
    }
    lastFetchSuccess.value = { key: "", at: 0 };
    lastFetchError.value = { key: "", at: 0 };
    await fetchAllGridImages();
    updateVisibleThumbnails();
    return;
  }
  if (isSmartScoreSortActive()) {
    // Smart score values shown in the grid must match the list endpoint's
    // global ranking context. Recompute by refetching the sorted list instead
    // of patching a single card from metadata.
    preserveScrollOnNextFetch.value = true;
    lastFetchSuccess.value = { key: "", at: 0 };
    lastFetchError.value = { key: "", at: 0 };
    await fetchAllGridImages({ force: true });
    updateVisibleThumbnails();
  } else {
    refreshGridImage(imageId);
  }
}

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
      const d = getTagList(gridImg.tags);
      gridImg.tags = d.filter((t) => !tagMatches(t, tag));
    }
    await _afterTagMutation(imageId);
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
    const responseTags = getTagList(response?.data?.tags);
    const gridImg = allGridImages.value.find(
      (img) => img && img.id === imageId,
    );
    if (gridImg) {
      const current = getTagList(gridImg.tags);
      const merged = responseTags.length
        ? responseTags
        : dedupeTagList([...current, { id: null, tag }]);
      gridImg.tags = merged;
    }
    await _afterTagMutation(imageId);
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

// ============================================================
// LIFECYCLE
// ============================================================

// Clear selection on ESC key
function handleKeyDown(event) {
  const isEditableElement = (element) => {
    if (!(element instanceof HTMLElement)) return false;
    if (element.isContentEditable) return true;
    const tagName = element.tagName;
    if (tagName === "INPUT" || tagName === "TEXTAREA" || tagName === "SELECT") {
      return true;
    }
    if (element.getAttribute("role") === "textbox") return true;
    return false;
  };

  const target = event.target;
  if (isEditableElement(target)) {
    return;
  }
  if (
    typeof document !== "undefined" &&
    isEditableElement(document.activeElement)
  ) {
    return;
  }
  if (overlayOpen.value) return; // Ignore if overlay is open
  if (event.key === "Escape") {
    if (showSelectionBar.value) {
      // First ESC clears selection only
      selectedImageIds.value = [];
      lastSelectedImageId = null;
      cursorIdx.value = null;
      clearFaceSelection();
    } else if (props.searchQuery && props.searchQuery.trim()) {
      // No selection active — ESC also clears search
      clearSearchQuery();
    } else {
      selectedImageIds.value = [];
      lastSelectedImageId = null;
      cursorIdx.value = null;
      clearFaceSelection();
    }
  } else if (
    ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)
  ) {
    event.preventDefault();
    const total = allGridImages.value.length;
    if (total === 0) return;
    const cols = Math.max(1, props.columns || 1);
    let newIdx = cursorIdx.value;
    if (newIdx === null) {
      if (selectedImageIds.value.length > 0) {
        const firstSel = allGridImages.value.findIndex(
          (img) => img && selectedImageIds.value.includes(img.id),
        );
        newIdx = firstSel >= 0 ? firstSel : 0;
      } else {
        newIdx = 0;
      }
    } else {
      if (event.key === "ArrowLeft") newIdx = Math.max(0, newIdx - 1);
      else if (event.key === "ArrowRight")
        newIdx = Math.min(total - 1, newIdx + 1);
      else if (event.key === "ArrowUp") newIdx = Math.max(0, newIdx - cols);
      else if (event.key === "ArrowDown")
        newIdx = Math.min(total - 1, newIdx + cols);
    }
    cursorIdx.value = newIdx;
    const cursorImg = allGridImages.value[newIdx];
    if (cursorImg && cursorImg.id) {
      if (event.shiftKey) {
        const anchorIndex =
          lastSelectedImageId != null
            ? allGridImages.value.findIndex(
                (item) =>
                  getPictureId(item?.id) === getPictureId(lastSelectedImageId),
              )
            : newIdx;
        const start = Math.min(anchorIndex, newIdx);
        const end = Math.max(anchorIndex, newIdx);
        selectedImageIds.value = allGridImages.value
          .slice(start, end + 1)
          .map((i) => i.id)
          .filter(Boolean);
      } else if (!event.ctrlKey && !event.metaKey) {
        // Plain arrow: move cursor and select only this image
        selectedImageIds.value = [cursorImg.id];
        lastSelectedImageId = cursorImg.id;
      }
      // Ctrl+Arrow: move cursor without changing selection
    }
    scrollCursorIntoView(newIdx);
  } else if (
    (event.key === "PageDown" || event.key === "PageUp") &&
    event.shiftKey &&
    cursorIdx.value !== null
  ) {
    // Shift+PageDown/Up: extend selection by a viewport's worth of rows
    event.preventDefault();
    const total = allGridImages.value.length;
    if (total === 0) return;
    const cols = Math.max(1, props.columns || 1);
    const rowsPerPage = scrollWrapper.value
      ? Math.max(
          1,
          Math.floor(scrollWrapper.value.clientHeight / rowHeight.value),
        )
      : 5;
    const delta = rowsPerPage * cols;
    const newIdx =
      event.key === "PageDown"
        ? Math.min(total - 1, cursorIdx.value + delta)
        : Math.max(0, cursorIdx.value - delta);
    cursorIdx.value = newIdx;
    const anchorIndex =
      lastSelectedImageId != null
        ? allGridImages.value.findIndex(
            (item) =>
              getPictureId(item?.id) === getPictureId(lastSelectedImageId),
          )
        : newIdx;
    const start = Math.min(anchorIndex, newIdx);
    const end = Math.max(anchorIndex, newIdx);
    selectedImageIds.value = allGridImages.value
      .slice(start, end + 1)
      .map((i) => i.id)
      .filter(Boolean);
    scrollCursorIntoView(newIdx);
  } else if (event.key === " ") {
    // Space: toggle selection at cursor
    if (cursorIdx.value !== null) {
      event.preventDefault();
      const cursorImg = allGridImages.value[cursorIdx.value];
      if (cursorImg && cursorImg.id) {
        const newSelection = [...selectedImageIds.value];
        if (newSelection.includes(cursorImg.id)) {
          selectedImageIds.value = newSelection.filter(
            (id) => id !== cursorImg.id,
          );
        } else {
          newSelection.push(cursorImg.id);
          selectedImageIds.value = newSelection;
          lastSelectedImageId = cursorImg.id;
        }
      }
    }
  } else if (event.key === "Enter") {
    // Enter: open overlay for cursor image
    if (cursorIdx.value !== null) {
      event.preventDefault();
      const cursorImg = allGridImages.value[cursorIdx.value];
      if (cursorImg && cursorImg.id) {
        openOverlay(cursorImg);
      }
    }
  } else if (event.key === "g" || event.key === "G") {
    // Focus the first visible image in the grid
    event.preventDefault();
    const idx = visibleStart.value;
    const img = allGridImages.value[idx];
    if (img && img.id) {
      cursorIdx.value = idx;
      selectedImageIds.value = [img.id];
      lastSelectedImageId = img.id;
    }
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
    (event.key === "t" || event.key === "T") &&
    selectedImageIds.value.length > 0
  ) {
    event.preventDefault();
    selectionBarRef.value?.openTagInput();
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
      updateVisibleThumbnails();
    });
  },
);

// Expose the grid DOM node to parent
defineExpose({
  gridEl: scrollWrapper,
  onGlobalKeyPress,
  updateVisibleThumbnails,
  expandAllStacks,
  collapseAllStacks,
  exportCurrentViewToZip,
  getExportCount,
  removeImagesById,
  clearFaceSelection,
  runComfyuiOnGridImages,
});

// Remove images by ID (for event-driven removal)
function removeImagesById(imageIds) {
  if (!Array.isArray(imageIds) || !imageIds.length) {
    return;
  }
  const dIds = new Set(
    imageIds.map((id) => getPictureId(id)).filter((id) => id !== null),
  );
  const removeId = (img) => dIds.has(getPictureId(img?.id));
  allGridImages.value = allGridImages.value.filter((img) => !removeId(img));
  if (Array.isArray(lastFetchedGridImages.value)) {
    lastFetchedGridImages.value = lastFetchedGridImages.value.filter(
      (img) => !removeId(img),
    );
  }
  const nextMembers = new Map();
  for (const [stackId, entry] of expandedStackMembers.value.entries()) {
    const ids = Array.isArray(entry?.ids) ? entry.ids : [];
    const images = Array.isArray(entry?.images) ? entry.images : [];
    const nextIds = ids.filter((id) => !dIds.has(getPictureId(id)));
    const nextImages = images.filter((img) => !removeId(img));
    if (nextIds.length || nextImages.length) {
      nextMembers.set(stackId, { ids: nextIds, images: nextImages });
    }
  }
  expandedStackMembers.value = nextMembers;
  selectedImageIds.value = selectedImageIds.value.filter(
    (id) => !dIds.has(getPictureId(id)),
  );
  resetThumbnailState();
  rebuildGridImagesFromLastFetch();
  void refreshExpandedStacksAfterFetch();
}

function getExportCount() {
  const selectedCount = selectedImageIds.value.length;
  const totalCount = allGridImages.value.filter((img) => img && img.id).length;
  return { selectedCount, totalCount };
}

// ============================================================
// EXPORT
// ============================================================
async function exportCurrentViewToZip(options = {}) {
  const exportType = options.exportType || "full";
  const captionMode = options.captionMode || "description";
  const includeCharacterName = options.includeCharacterName !== false;
  const useOriginalFileNames = options.useOriginalFileNames === true;
  const resolution = options.resolution || "original";
  let url = `${props.backendUrl}/pictures/export`;
  let params;
  const selectedIds = selectedImageIds.value;
  if (selectedIds && selectedIds.length > 0) {
    const selParams = new URLSearchParams();
    for (const id of selectedIds) {
      selParams.append("id", getPictureId(id));
    }
    params = selParams.toString();
  } else {
    params = buildPictureIdsQueryParams();
  }
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
  if (useOriginalFileNames) {
    extraParams.append("use_original_file_names", "true");
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
    const maxAttempts = 600; // 600 × 1s = 10 minute timeout; suitable for very large collections
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

    let filename = "pixlstash_export.zip";
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

// ============================================================
// SEARCH
// ============================================================
function clearSearchQuery() {
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
  border-radius: 16px;
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

.thumbnail-badge--top-left,
.thumbnail-badge--top-right,
.thumbnail-badge--bottom-left,
.thumbnail-badge--bottom-right {
  position: absolute;
}

.thumbnail-badge--top-left {
  top: 2px;
  left: 2px;
}

.thumbnail-badge--top-right {
  top: 2px;
  right: 2px;
}

.thumbnail-badge--bottom-left {
  left: 2px;
  bottom: 2px;
}

.thumbnail-badge--bottom-right {
  right: 2px;
  bottom: 2px;
}

.thumbnail-badge--bottom-right-raised {
  bottom: 22px;
}

.likeness-group-indicator {
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.face-bbox-label {
  font-size: 0.7em;
  background-color: rgba(var(--v-theme-surface), 0.3);
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
  height: 100%;
  overflow-y: auto;
  overflow-x: hidden;
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
  gap: 4px;
  width: 100%;
  box-sizing: border-box;
  flex: 1 1 0%;
  padding: 2px 4px 2px 4px !important;
  align-content: start;
  justify-content: start;
}
.compact-mode.image-grid {
  padding-top: 10px !important;
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
.image-card-cursor > .thumbnail-card {
  outline: 2px solid rgba(var(--v-theme-primary), 0.9);
  outline-offset: -2px;
}

/* Compact mode: outline is invisible on edge-to-edge images, use inset ::after border instead */
.compact-mode .image-card-cursor > .thumbnail-card {
  outline: none;
}
.compact-mode .image-card-cursor > .thumbnail-card::after {
  content: "";
  position: absolute;
  inset: 0;
  border: 2px solid rgba(var(--v-theme-primary), 1);
  box-shadow: inset 0 0 6px rgba(var(--v-theme-primary), 0.45);
  pointer-events: none;
  z-index: 202;
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

.image-card-stack-reorder-left::after,
.image-card-stack-reorder-right::after {
  content: "";
  position: absolute;
  top: 8px;
  bottom: 32px;
  width: 6px;
  border-radius: 999px;
  background: rgba(var(--v-theme-accent), 0.95);
  box-shadow: 0 2px 8px rgba(var(--v-theme-accent), 0.45);
  pointer-events: none;
  z-index: 6;
}

.image-card-stack-reorder-left::after {
  left: -3px;
}

.image-card-stack-reorder-right::after {
  right: -3px;
}

.selection-overlay {
  position: absolute;
  inset: 0;
  background: rgba(var(--v-theme-info), 0.62);
  pointer-events: none;
  z-index: 2;
}
.thumbnail-info-row {
  margin-top: 2px;
  text-align: center;
  height: 24px;
  min-height: 24px;
  max-height: 24px;
  overflow: hidden;
  background: none;
  width: 100%;
}
.thumbnail-info {
  font-size: 0.88em;
  color: rgba(var(--v-theme-on-background), 0.78);
  text-align: center;
  line-height: 24px;
  display: block;
  width: 100%;
  max-width: 100%;
  padding: 0 8px;
  white-space: nowrap;
  overflow: hidden;
  text-shadow: 0 1px 1px rgba(var(--v-theme-shadow), 0.12);
}
.thumbnail-container {
  width: 100%;
  height: 100%;
  position: relative;
  aspect-ratio: 1 / 1;
}

.thumbnail-container-drag-source .thumbnail-img,
.thumbnail-container-drag-source .thumbnail-placeholder {
  filter: grayscale(1) brightness(0.65);
  opacity: 0.75;
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
.thumbnail-img:hover {
  box-shadow: 1px 1px 2px 2px rgba(var(--v-theme-shadow), 0.3);
  transform: scale(1.03);
  z-index: 2;
}
.stack-hover-active .thumbnail-img {
  box-shadow: 1px 1px 2px 2px rgba(var(--v-theme-shadow), 0.3);
  transform: scale(1.03);
  z-index: 2;
}
.thumbnail-card {
  width: 100%;
  max-width: none;
  min-width: none;
  position: relative;
  padding: 4px;
}

/* Compact mode: no info row gap, no rounded corners, no shadow */
.compact-mode .thumbnail-card {
  padding: 0px 0px 0px 0px;
}
/* Accent border overlay on group-boundary cards (top + left, inset, no layout shift) */
.compact-mode .thumbnail-card:has(.compact-group-label)::before {
  content: "";
  position: absolute;
  inset: 0;
  background:
    linear-gradient(
        to right,
        rgba(var(--v-theme-accent), 0.95),
        rgba(var(--v-theme-accent), 0) 65%
      )
      top / 100% 3px no-repeat,
    linear-gradient(
        to bottom,
        rgba(var(--v-theme-accent), 0.95),
        rgba(var(--v-theme-accent), 0) 65%
      )
      left / 3px 100% no-repeat;
  pointer-events: none;
  z-index: 200;
  transition: transform 0.18s cubic-bezier(0.4, 2, 0.6, 1);
}
.compact-mode .thumbnail-card:has(.compact-group-label):hover::before {
  transform: scale(1.03);
}
.compact-mode .thumbnail-img {
  border-radius: 0;
  box-shadow: none;
}
.compact-mode .thumbnail-img:hover {
  box-shadow: none;
}
.compact-sticky-label,
.compact-group-label {
  transform: translateX(-50%) translateY(-38%);
  background: rgba(var(--v-theme-surface), 0.82);
  color: rgb(var(--v-theme-accent));
  border: 1px solid rgba(var(--v-theme-on-surface), 0.18);
  border-radius: 999px;
  padding: 1px 9px;
  font-size: 0.72em;
  font-weight: 600;
  line-height: 1.6;
  white-space: nowrap;
  max-width: 80%;
  overflow: hidden;
  text-overflow: ellipsis;
  pointer-events: none;
  z-index: 400;
  box-shadow: 0 1px 4px rgba(var(--v-theme-shadow), 0.25);
  backdrop-filter: blur(4px);
}
.compact-sticky-label {
  position: sticky;
  top: 0;
  left: 50%;
  width: fit-content;
  transform: translateX(-50%) translateY(4px);
  margin-bottom: -24px;
}
.compact-group-label {
  position: absolute;
  top: 0;
  left: 30%;
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

.penalised-tag-indicator,
.stack-indicator {
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 30;
  pointer-events: auto;
  padding: 2px;
}

.stack-indicator {
  cursor: pointer;
}

.stack-band-overlay {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 10;
  border-radius: inherit;
}

.thumbnail-badge--top-left-stack {
  position: absolute;
  top: 24px;
  left: 2px;
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

.set-overlap-status-bar {
  position: absolute;
  left: 12px;
  /* Keep clear of global F1 shortcuts FAB in bottom-right corner. */
  right: 72px;
  bottom: 10px;
  z-index: 6;
  margin: 0;
  padding: 10px 14px;
  border-radius: 12px;
  border: 1px solid rgba(var(--v-theme-on-accent), 0.28);
  background: linear-gradient(
    135deg,
    rgba(var(--v-theme-accent), 0.96),
    rgba(var(--v-theme-accent), 0.86)
  );
  color: rgb(var(--v-theme-on-accent));
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 0.015em;
  backdrop-filter: blur(3px);
  box-shadow:
    0 10px 22px rgba(0, 0, 0, 0.28),
    0 0 0 1px rgba(var(--v-theme-on-accent), 0.08) inset;
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 8px;
  pointer-events: none;
}

.set-overlap-status-bar__main {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.set-overlap-status-bar__icon {
  opacity: 0.95;
  flex: 0 0 auto;
}

.set-overlap-status-bar__text {
  line-height: 1.35;
}

@media (max-width: 900px) {
  .set-overlap-status-bar {
    right: 72px;
    font-size: 13px;
    padding: 9px 12px;
    gap: 8px;
  }
}
</style>
