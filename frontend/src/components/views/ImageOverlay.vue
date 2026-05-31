<template>
  <div v-if="open" class="image-overlay" @click.self="handleBackdropClick">
    <div
      class="overlay-shell"
      :class="{ 'chrome-hidden': chromeHidden, 'sidebar-open': sidebarOpen }"
      @mousemove="handleMouseActivity"
      @mousedown="handleMouseActivity"
      @click="handleOverlayClick"
      @wheel.passive="handleWheelActivity"
    >
      <header
        ref="topbarRef"
        class="overlay-topbar"
        :class="{ hidden: chromeHidden }"
      >
        <button
          class="overlay-close"
          @click="emit('close')"
          aria-label="Close (ESC)"
          title="Close (ESC)"
        >
          <v-icon size="18">mdi-close</v-icon>
          <span>Close</span>
        </button>
        <div class="overlay-title">
          <button
            class="overlay-desc-teaser"
            type="button"
            :disabled="!image"
            @click="openSidebarFromTeaser"
          >
            {{ descriptionTeaser || "Add a description" }}
          </button>
        </div>
        <div
          class="overlay-character-names"
          v-for="(face, idx) in faceBboxes"
          :key="idx"
        >
          <span
            v-if="face.character_name"
            :style="{ color: faceBoxColor(idx) }"
          >
            {{ face.character_name || "Unknown" }}
          </span>
        </div>
        <div class="overlay-top-actions">
          <v-menu
            v-if="!isReadOnly"
            v-model="pluginMenuOpen"
            :close-on-content-click="false"
            location-strategy="connected"
            location="bottom end"
            origin="top end"
            transition="scale-transition"
          >
            <template #activator="{ props }">
              <button
                v-bind="props"
                class="overlay-icon-btn overlay-comfy-activator"
                type="button"
                title="Filters"
                aria-label="Filters"
                :class="{
                  hidden: chromeHidden,
                  'overlay-icon-btn--active': pluginMenuOpen,
                }"
              >
                <v-icon size="20">mdi-tune-variant</v-icon>
                <span class="overlay-comfy-activator-label">Filters</span>
              </button>
            </template>
            <div class="overlay-comfy-panel">
              <div class="overlay-comfy-header">Filters</div>
              <div class="overlay-comfy-body">
                <div
                  v-if="!overlayPluginOptions.length"
                  class="overlay-comfy-warning"
                >
                  No filters available.
                </div>
                <template v-else>
                  <label class="overlay-comfy-field-label">Filters</label>
                  <select
                    v-model="overlaySelectedPluginName"
                    class="overlay-comfy-select"
                  >
                    <option
                      v-for="plugin in overlayPluginOptions"
                      :key="plugin.name"
                      :value="plugin.name"
                    >
                      {{ plugin.display_name || plugin.name }}
                    </option>
                  </select>
                  <PluginParametersUI
                    v-model="overlayPluginParameters"
                    :plugin="activeOverlayPluginSchema"
                    :show-description="true"
                    tone="dark"
                    input-class="overlay-comfy-select"
                    label-class="overlay-comfy-field-label"
                  />
                  <div class="overlay-comfy-actions">
                    <button
                      class="overlay-comfy-run"
                      type="button"
                      :disabled="!image || !overlaySelectedPluginName"
                      @click.stop="runOverlayPlugin"
                    >
                      <v-icon size="16">mdi-play</v-icon>
                      <span>Run</span>
                    </button>
                  </div>
                </template>
              </div>
            </div>
          </v-menu>
          <div v-if="comfyuiConfigured" class="overlay-menu-anchor">
            <button
              class="overlay-icon-btn overlay-comfy-activator"
              type="button"
              title="Run ComfyUI I2I"
              aria-label="Run ComfyUI I2I"
              :class="{
                hidden: chromeHidden,
                'overlay-icon-btn--active': comfyuiMenuOpen,
              }"
            >
              <v-icon size="20">mdi-robot</v-icon>
              <span class="overlay-comfy-activator-label">I2I</span>
            </button>
            <v-menu
              v-model="comfyuiMenuOpen"
              activator="parent"
              :close-on-content-click="false"
              location-strategy="connected"
              location="bottom end"
              origin="top end"
              transition="scale-transition"
            >
              <div class="overlay-comfy-panel">
                <div class="overlay-comfy-header">ComfyUI I2I</div>
                <div v-if="comfyuiWorkflowLoading" class="overlay-comfy-status">
                  Loading workflows...
                </div>
                <div v-else class="overlay-comfy-body">
                  <div v-if="comfyuiWorkflowError" class="overlay-comfy-error">
                    {{ comfyuiWorkflowError }}
                  </div>
                  <div
                    v-if="!validComfyWorkflows.length"
                    class="overlay-comfy-warning"
                  >
                    No valid workflows found. Workflows need a
                    {{ imagePlaceholderLabel }} placeholder.
                  </div>
                  <label class="overlay-comfy-field-label">Workflow</label>
                  <select
                    v-model="comfyuiSelectedWorkflow"
                    class="overlay-comfy-select"
                    :disabled="!validComfyWorkflows.length"
                  >
                    <option
                      v-for="workflow in validComfyWorkflows"
                      :key="workflow.name"
                      :value="workflow.name"
                    >
                      {{ workflow.display_name || workflow.name }}
                    </option>
                  </select>
                  <div
                    v-if="invalidComfyWorkflows.length"
                    class="overlay-comfy-note"
                  >
                    {{ invalidComfyWorkflows.length }} workflow(s) missing
                    required placeholders.
                  </div>
                  <template v-if="showComfyuiCaptionInput">
                    <label class="overlay-comfy-field-label">Caption</label>
                    <div class="overlay-comfy-textarea-wrap">
                      <div
                        v-if="showComfyuiCaptionHelp"
                        class="overlay-comfy-help"
                      >
                        Add edit caption here
                      </div>
                      <textarea
                        v-model="comfyuiCaption"
                        class="overlay-comfy-textarea"
                        rows="6"
                        @input="comfyuiCaptionTouched = true"
                        @focus="comfyuiCaptionFocused = true"
                        @blur="comfyuiCaptionFocused = false"
                      ></textarea>
                    </div>
                  </template>
                  <div class="overlay-comfy-actions">
                    <button
                      class="overlay-comfy-run"
                      type="button"
                      :disabled="!canRunComfyWorkflow"
                      @click.stop="runComfyWorkflow"
                    >
                      <v-icon
                        size="16"
                        :class="{ 'mdi-spin': comfyuiRunLoading }"
                      >
                        {{ comfyuiRunLoading ? "mdi-loading" : "mdi-play" }}
                      </v-icon>
                      <span>{{ comfyuiRunLoading ? "Running" : "Run" }}</span>
                    </button>
                  </div>
                  <div v-if="comfyuiRunError" class="overlay-comfy-error">
                    {{ comfyuiRunError }}
                  </div>
                  <div v-if="comfyuiRunSuccess" class="overlay-comfy-success">
                    {{ comfyuiRunSuccess }}
                  </div>
                </div>
              </div>
            </v-menu>
          </div>
          <AddToEntityControl
            v-if="image && !isReadOnly"
            type="set"
            ref="addToSetControlRef"
            :key="addToSetControlKey"
            :backend-url="backendUrl"
            :picture-ids="[image.id]"
            :include-deleted-members="true"
            :force-dark="true"
            :disabled="!!stackGroupingLockReason"
            :title="stackGroupingLockReason || undefined"
            :class="{ hidden: chromeHidden }"
            @added="(payload) => emit('added-to-set', payload)"
          />
          <AddToEntityControl
            v-if="image && !isReadOnly"
            type="project"
            :backend-url="backendUrl"
            :picture-ids="[image.id]"
            :include-deleted-members="true"
            :expand-stacks="false"
            :force-dark="true"
            :disabled="!!stackGroupingLockReason"
            :title="stackGroupingLockReason || undefined"
            :class="{ hidden: chromeHidden }"
            @selected="(payload) => emit('set-project', payload)"
          />
          <StarRatingOverlay
            v-if="image && !isMobile"
            :class="{ hidden: chromeHidden }"
            :score="isReadOnly ? guestScore || 0 : image?.score || 0"
            icon-size="large"
            @set-score="setScore"
          />
          <v-menu
            v-if="image && isMobile"
            v-model="starMenuOpen"
            location="bottom end"
            origin="top end"
            transition="scale-transition"
          >
            <template #activator="{ props: menuProps }">
              <button
                v-bind="menuProps"
                class="overlay-icon-btn overlay-star-mobile-btn"
                type="button"
                title="Set rating (1–5)"
                aria-label="Set rating"
                :class="{ hidden: chromeHidden }"
              >
                <v-icon size="18" color="rgba(var(--v-theme-accent))"
                  >mdi-star</v-icon
                >
                <span class="overlay-star-mobile-label">{{
                  isReadOnly ? guestScore || 0 : image?.score || 0
                }}</span>
              </button>
            </template>
            <div class="overlay-star-menu">
              <button
                v-for="n in [0, 1, 2, 3, 4, 5]"
                :key="n"
                class="overlay-star-menu-item"
                :class="{
                  'overlay-star-menu-item--active':
                    (isReadOnly ? guestScore || 0 : image?.score || 0) === n,
                }"
                type="button"
                @click.stop="setScore(n)"
              >
                <span class="overlay-star-menu-stars">
                  <v-icon
                    v-for="s in 5"
                    :key="s"
                    size="16"
                    :color="
                      s <= n
                        ? 'rgba(var(--v-theme-accent))'
                        : 'rgba(255,255,255,0.2)'
                    "
                    >mdi-star</v-icon
                  >
                </span>
                <span class="overlay-star-menu-label">{{
                  n === 0 ? "No rating" : n
                }}</span>
              </button>
            </div>
          </v-menu>
          <button
            class="overlay-icon-btn"
            type="button"
            title="Toggle face bounding boxes"
            aria-label="Toggle face bounding boxes"
            @click.stop="toggleFaceBbox"
            :class="{
              hidden: chromeHidden,
              'overlay-icon-btn--active': showFaceBbox,
            }"
          >
            <v-icon size="20">mdi-face-recognition</v-icon>
          </button>
          <button
            v-if="!isMobile && !isReadOnly"
            class="overlay-icon-btn"
            type="button"
            title="Draw face bounding box"
            aria-label="Draw face bounding box"
            @click.stop="beginDrawMode('face')"
            :class="{
              hidden: chromeHidden,
              'overlay-icon-btn--active': drawMode === 'face',
            }"
          >
            <v-icon size="20">mdi-account-plus</v-icon>
          </button>

          <button
            class="overlay-icon-btn zoom-btn"
            type="button"
            title="Toggle zoom (Z)"
            aria-label="Toggle zoom (Z)"
            @click="toggleZoom"
          >
            <v-icon>mdi-magnify</v-icon>
          </button>
          <button
            class="overlay-icon-btn overlay-topbar-sidebar-toggle"
            type="button"
            title="Toggle sidebar (S)"
            aria-label="Toggle sidebar (S)"
            @click="toggleSidebar"
          >
            <v-icon>{{
              sidebarOpen ? "mdi-arrow-collapse-right" : "mdi-arrow-expand-left"
            }}</v-icon>
          </button>
        </div>
      </header>

      <div
        v-if="comfyuiProgress && comfyuiProgress.visible"
        class="overlay-progress overlay-progress--comfyui"
        :class="{
          'overlay-progress--error': comfyuiProgress.status === 'failed',
        }"
      >
        <div class="overlay-progress-title">
          {{ comfyuiProgress.message }}
        </div>
        <div class="overlay-progress-bar">
          <div
            class="overlay-progress-fill"
            :style="{ width: `${comfyuiProgressPercent}%` }"
          ></div>
        </div>
      </div>

      <div
        v-if="pluginProgress && pluginProgress.visible"
        class="overlay-progress overlay-progress--plugin"
        :class="{
          'overlay-progress--error': pluginProgress.status === 'failed',
        }"
      >
        <div class="overlay-progress-title">
          {{ pluginProgress.message }}
        </div>
        <div class="overlay-progress-bar">
          <div
            class="overlay-progress-fill"
            :style="{ width: `${pluginProgressPercent}%` }"
          ></div>
        </div>
        <div class="overlay-progress-meta">
          {{ pluginProgress.current || 0 }} / {{ pluginProgress.total || 0 }}
        </div>
      </div>

      <div
        ref="overlayMainRef"
        class="overlay-main"
        :style="filmstripStyleVars"
      >
        <div
          class="overlay-canvas"
          @touchstart="onTouchStart"
          @touchmove="onTouchMove"
          @touchend="onTouchEnd"
          @dblclick="toggleZoom"
          @wheel.prevent="onWheelZoom"
        >
          <div
            class="overlay-media"
            :style="mediaTransformStyle"
            :class="{ panning: isPanning }"
            @pointerdown="onPanStart"
            @pointermove="onPanMove"
            @pointerup="onPanEnd"
            @pointercancel="onPanEnd"
            @pointerleave="onPanEnd"
          >
            <div ref="mediaInnerRef" class="overlay-media-inner">
              <template v-if="image">
                <template v-if="isSupportedVideoFile(getOverlayFormat(image))">
                  <video
                    v-if="!videoError"
                    ref="videoRef"
                    :src="videoSrc"
                    class="overlay-video"
                    controls
                    preload="metadata"
                    playsinline
                    :draggable="!isZoomed"
                    @dragstart="handleMediaDragStart"
                    @loadedmetadata="updateOverlayDims"
                    @error="handleVideoError"
                  ></video>
                  <div v-else class="overlay-video-error">
                    <v-icon size="48" color="grey-lighten-1"
                      >mdi-video-off-outline</v-icon
                    >
                    <p class="overlay-video-error-msg">
                      Your browser cannot play this video format ({{
                        getOverlayFormat(image).toUpperCase()
                      }}).
                    </p>
                    <a
                      :href="videoSrc"
                      download
                      class="overlay-video-download-btn"
                    >
                      <v-icon size="18">mdi-download</v-icon>
                      Download video
                    </a>
                  </div>
                </template>
                <img
                  v-else
                  ref="imgRef"
                  :src="getFullImageUrl(image)"
                  :alt="image.description || 'Full Image'"
                  class="overlay-img"
                  :draggable="!isZoomed"
                  @dragstart="handleMediaDragStart"
                  @load="updateOverlayDims"
                />
              </template>
              <template v-if="showFaceBbox && overlayReady">
                <div v-if="faceBboxes.length === 0" class="face-bbox-empty">
                  No bboxes found
                </div>
                <div
                  v-for="(face, idx) in faceBboxes"
                  :key="`face-${idx}`"
                  class="face-bbox-overlay"
                  :style="getOverlayBoxStyle(face.bbox, faceBoxColor(idx))"
                >
                  <span class="face-bbox-label">
                    {{ face.character_name || `Face ${idx + 1}` }}
                  </span>
                </div>
              </template>
            </div>
          </div>

          <div
            v-if="drawMode"
            class="overlay-draw-layer"
            @pointerdown.prevent="onDrawStart"
            @pointermove.prevent="onDrawMove"
            @pointerup.prevent="onDrawEnd"
            @pointercancel.prevent="onDrawCancel"
            @pointerleave.prevent="onDrawCancel"
          >
            <div class="overlay-draw-hint">
              <span>
                Draw a bounding box to create the {{ drawModeLabel }}
              </span>
              <button
                class="overlay-draw-cancel"
                type="button"
                @click.stop="clearDrawMode"
              >
                Cancel
              </button>
            </div>
            <div
              v-if="drawRectStyle"
              class="overlay-draw-rect"
              :style="drawRectStyle"
            ></div>
          </div>

          <button
            class="overlay-nav overlay-nav-left"
            :class="{ hidden: chromeHidden }"
            @click.stop="showPrevImage"
            @dblclick.stop
            aria-label="Previous (←)"
            title="Previous (←)"
          >
            <v-icon>mdi-chevron-left</v-icon>
          </button>
          <button
            class="overlay-nav overlay-nav-right"
            :class="{ hidden: chromeHidden }"
            @click.stop="showNextImage"
            @dblclick.stop
            aria-label="Next (→)"
            title="Next (→)"
          >
            <v-icon>mdi-chevron-right</v-icon>
          </button>

          <div
            class="zoom-hud"
            :class="{ hidden: chromeHidden || zoomMode === 'fit' }"
          >
            {{ zoomHudLabel }}
          </div>

          <div v-if="swipeHintVisible" class="overlay-swipe-hint">
            <v-icon size="18">mdi-swap-horizontal</v-icon>
            <span>Swipe to navigate</span>
          </div>
          <div v-if="chromeHidden" class="overlay-chrome-hint">
            <span>Click or <kbd>Space</kbd> to show controls</span>
          </div>
        </div>

        <OverlayFilmstrip
          ref="filmstripRef"
          :items="filmstripCanvasWindow"
          :canvas-style="filmstripCanvasStyle"
          :hidden="chromeHidden"
          @select="selectImageByIndex"
          @toggle-expand="toggleFilmstripStackExpand"
          @prefetch="prefetchFilmstripStackMembers"
          @navigate="onFilmstripNavigate"
        />

        <aside
          class="overlay-sidebar"
          :class="{ open: sidebarOpen, hidden: chromeHidden }"
        >
          <OverlayDescriptionPanel
            ref="descriptionPanelRef"
            :image="image"
            :backend-url="backendUrl"
            @update-description="handleDescriptionUpdate"
          />

          <div class="sidebar-section sidebar-section--faces">
            <div
              class="section-header section-header--collapsible"
              @click="facesCollapsed = !facesCollapsed"
            >
              <span>Faces</span>
              <v-icon size="16" style="opacity: 0.6">{{
                facesCollapsed ? "mdi-chevron-right" : "mdi-chevron-down"
              }}</v-icon>
            </div>
            <template v-if="!facesCollapsed">
              <div v-if="faceAssignItems.length" class="face-assign-grid">
                <div
                  v-for="face in faceAssignItems"
                  :key="face.faceKey"
                  class="face-assign-card"
                >
                  <div class="face-assign-row">
                    <div class="face-assign-thumb">
                      <div
                        class="face-assign-crop"
                        :style="getFaceThumbStyle(face, face.faceIdx)"
                      ></div>
                    </div>
                    <div class="face-assign-meta">
                      <div
                        class="face-assign-label"
                        :style="{ color: faceBoxColor(face.faceIdx) }"
                      >
                        {{ face.label }}
                      </div>
                      <select
                        class="face-assign-select"
                        :disabled="!face.id || isReadOnly"
                        :value="
                          face.character_id != null
                            ? String(face.character_id)
                            : ''
                        "
                        @change="handleFaceAssignChange(face, $event)"
                      >
                        <option value="">Unassigned</option>
                        <option
                          v-if="
                            face.character_id != null &&
                            !hasCharacterOption(face)
                          "
                          :value="String(face.character_id)"
                        >
                          {{
                            face.character_name ||
                            `Character ${face.character_id}`
                          }}
                        </option>
                        <option
                          v-for="char in sortedCharacters"
                          :key="char.id"
                          :value="String(char.id)"
                        >
                          {{ char.displayName }}
                        </option>
                      </select>
                    </div>
                  </div>
                </div>
              </div>
              <div v-else class="face-assign-empty">No faces detected</div>
            </template>
          </div>

          <OverlayTagsPanel
            ref="tagsPanelRef"
            :image="image"
            :backend-url="backendUrl"
            :hidden-tags="hiddenTags"
            :apply-tag-filter="applyTagFilter"
            @update-tags="handleTagsUpdate"
            @overlay-change="(payload) => emit('overlay-change', payload)"
            @add-tag="(imageId, tag) => emit('add-tag', imageId, tag)"
            @request-metadata-refresh="fetchOverlayMetadata"
          />

          <OverlayMetadataPanel
            :image="image"
            :comfy-metadata="comfyMetadata"
            :date-format="dateFormat"
            :backend-url="backendUrl"
            :video-duration="videoMeta.duration"
          />
        </aside>
      </div>
    </div>
  </div>
</template>

<script setup>
import {
  onMounted,
  onUnmounted,
  ref,
  reactive,
  computed,
  nextTick,
  toRefs,
  watch,
} from "vue";
import {
  isSupportedVideoFile,
  getOverlayFormat,
  buildMediaUrl,
} from "../../utils/media.js";
import { apiClient, appendShareToken, isReadOnly } from "../../utils/apiClient";
import { copyText } from "../../utils/clipboard";
import AddToEntityControl from "../widgets/AddToEntityControl.vue";
import OverlayDescriptionPanel from "./OverlayDescriptionPanel.vue";
import OverlayFilmstrip from "./OverlayFilmstrip.vue";
import OverlayMetadataPanel from "./OverlayMetadataPanel.vue";
import OverlayTagsPanel from "./OverlayTagsPanel.vue";
import PluginParametersUI from "../widgets/PluginParametersUI.vue";
import StarRatingOverlay from "../widgets/StarRatingOverlay.vue";
import {
  faceBoxColor,
  formatUserDate,
  getStackColor,
  toggleScore,
} from "../../utils/utils.js";
import { dedupeTagList, getTagList } from "../../utils/tags.js";

const props = defineProps({
  open: { type: Boolean, default: false },
  initialImageId: { type: [String, Number, null], default: null },
  initialExpandedStackIds: { type: Array, default: () => [] },
  allImages: { type: Array, default: () => [] },
  backendUrl: { type: String, required: true },
  tagUpdate: { type: Object, default: () => ({}) },
  descriptionUpdate: { type: Object, default: () => ({}) },
  hiddenTags: { type: Array, default: () => [] },
  applyTagFilter: { type: Boolean, default: false },
  dateFormat: { type: String, default: "locale" },
  showStacks: { type: Boolean, default: true },
  showProblemIcon: { type: Boolean, default: true },
  availablePlugins: { type: Array, default: () => [] },
  comfyuiProgress: { type: Object, default: null },
  comfyuiProgressPercent: { type: Number, default: 0 },
  pluginProgress: { type: Object, default: null },
  pluginProgressPercent: { type: Number, default: 0 },
  comfyuiClientId: { type: String, default: "" },
  comfyuiConfigured: { type: Boolean, default: false },
  guestScore: { type: Number, default: 0 },
});

const {
  open,
  initialImageId,
  initialExpandedStackIds,
  allImages,
  backendUrl,
  tagUpdate,
  descriptionUpdate,
  hiddenTags,
  applyTagFilter,
  showStacks,
  showProblemIcon,
  availablePlugins,
  comfyuiProgress,
  comfyuiProgressPercent,
  pluginProgress,
  pluginProgressPercent,
  comfyuiClientId,
  comfyuiConfigured,
  guestScore,
} = toRefs(props);

const image = ref(null);
// Grouping (project/set) membership is stack-atomic: a single stack member shown
// in the overlay cannot have its membership changed individually — the user must
// unstack first. Whole-stack edits happen from the collapsed grid tile.
const stackGroupingLockReason = computed(() => {
  if (!image.value) return null;
  const count = Number(image.value.stackCount ?? image.value.stack_count ?? 0);
  return count > 1
    ? "Unstack first to change the project or set of an individual stack picture."
    : null;
});
const addToSetControlRef = ref(null);
const tagsPanelRef = ref(null);
const isAddingTag = computed(() => tagsPanelRef.value?.addingTag ?? false);

function handleTagsUpdate(newTagsArray) {
  if (image.value) {
    image.value = { ...image.value, tags: newTagsArray };
  }
}
const sidebarOpen = ref(true);
const chromeHidden = ref(false);
const chromeRevealTimestamp = ref(0);
const zoomMode = ref("fit");
const zoomSteps = ["fit", 1.5, 2];
const pan = reactive({ x: 0, y: 0 });
const isPanning = ref(false);
const lastPointer = ref({ x: 0, y: 0 });
const overlayExpandedStackIds = ref(new Set());
const overlayExpandedStackMembers = ref(new Map());
const overlayExpandedStackLoading = ref(new Set());
const overlayStackSignatures = ref(new Map());
const overlayStackReloadToken = ref(0);

const allImageById = computed(() => {
  const map = new Map();
  const list = Array.isArray(allImages.value) ? allImages.value : [];
  for (const item of list) {
    if (!item || item.id == null) continue;
    map.set(String(item.id), item);
  }
  return map;
});

const allImagesByStackId = computed(() => {
  const map = new Map();
  const list = Array.isArray(allImages.value) ? allImages.value : [];
  for (const item of list) {
    const stackId = getOverlayStackId(item);
    if (!stackId || item?.id == null) continue;
    if (!map.has(stackId)) {
      map.set(stackId, []);
    }
    map.get(stackId).push(item);
  }
  for (const [stackId, members] of map.entries()) {
    map.set(stackId, sortOverlayStackMembers(members));
  }
  return map;
});

const allImageLeaderByStackId = computed(() => {
  const leaders = new Map();
  for (const [stackId, members] of allImagesByStackId.value.entries()) {
    const leader = members[0];
    if (leader?.id != null) {
      leaders.set(stackId, String(leader.id));
    }
  }
  return leaders;
});

function resetOverlayStackState() {
  overlayExpandedStackIds.value = new Set();
  overlayExpandedStackMembers.value = new Map();
  overlayExpandedStackLoading.value = new Set();
  overlayStackSignatures.value = new Map();
  overlayStackReloadToken.value += 1;
}

function applyInitialExpandedStackState() {
  const raw = Array.isArray(initialExpandedStackIds.value)
    ? initialExpandedStackIds.value
    : [];
  const next = new Set(
    raw
      .map((id) => (id === null || id === undefined ? "" : String(id)))
      .filter(Boolean),
  );
  overlayExpandedStackIds.value = next;
}

function setOverlayImageById(nextId) {
  if (nextId == null || nextId === "") {
    image.value = null;
    return;
  }
  const nextIdKey = String(nextId);
  const currentId = image.value?.id;
  const isSameImage =
    currentId !== null &&
    currentId !== undefined &&
    String(currentId) === nextIdKey;
  const targetFromAll = allImageById.value.get(nextIdKey);
  const target = targetFromAll
    ? targetFromAll
    : filmstripImageById.value.get(nextIdKey);
  if (target) {
    const existingTags = getTagList(image.value?.tags);
    const targetTags = getTagList(target.tags);
    const existingDescription = image.value?.description;
    const existingSmartScore = image.value?.smartScore;
    image.value = {
      ...target,
      // Preserve the existing description when re-setting the same image from filmstrip
      // data, which may only carry partial fields (no description). The full description
      // is loaded separately by fetchOverlayMetadata and must not be overwritten here.
      ...(isSameImage && existingDescription != null
        ? { description: existingDescription }
        : {}),
      // Preserve smartScore fetched by fetchOverlayMetadata — grid images don't carry it.
      ...(isSameImage && existingSmartScore != null
        ? { smartScore: existingSmartScore }
        : {}),
      tags: dedupeTagList(
        isSameImage ? (existingTags.length ? existingTags : targetTags) : [],
      ),
    };
  } else {
    if (!image.value) {
      image.value = { id: nextId, tags: [] };
    }
    return;
  }
  // Tag state reset is handled by OverlayTagsPanel's image-id watcher.
}

const emit = defineEmits([
  "close",
  "prev",
  "next",
  "apply-score",
  "set-guest-score",
  "remove-tag",
  "add-tag",
  "update-description",
  "overlay-change",
  "added-to-set",
  "set-project",
  "comfyui-run",
  "run-plugin",
]);

const descriptionPanelRef = ref(null);
const isDescriptionEditing = computed(
  () => descriptionPanelRef.value?.isEditingDescription ?? false,
);
const overlayCopyState = ref("idle");
const imagePlaceholderLabel = "{{image_path}}";
const captionPlaceholderLabel = "{{caption}}";
const canCopyOverlay = computed(() => !!image.value);
const descriptionTeaser = computed(() => {
  const desc = image.value?.description || "";
  const trimmed = desc.trim();
  if (!trimmed) return "";
  const match = trimmed.match(/[^.!?]+[.!?]?/);
  return match ? match[0].trim() : trimmed;
});

const lastTagUpdateKey = ref(0);
const lastDescriptionUpdateKey = ref(0);
const addToSetControlKey = ref(0);
const comfyuiMenuOpen = ref(false);
const pluginMenuOpen = ref(false);
const starMenuOpen = ref(false);
let menuWasOpenOnPointerDown = false;
const comfyuiWorkflows = ref([]);
const comfyuiWorkflowLoading = ref(false);
const comfyuiWorkflowError = ref("");
const comfyuiSelectedWorkflow = ref("");
const comfyuiCaption = ref("");
const comfyuiCaptionTouched = ref(false);
const comfyuiCaptionFocused = ref(false);
const comfyuiRunLoading = ref(false);
const comfyuiRunError = ref("");
const comfyuiRunSuccess = ref("");
const overlaySelectedPluginName = ref("");
const overlayPluginParameters = ref({});
const overlaySelectionMedia = computed(() => {
  const format = image.value ? getOverlayFormat(image.value) : "";
  const hasVideos = format ? isSupportedVideoFile(format) : false;
  return {
    hasImages: !hasVideos,
    hasVideos,
  };
});

const overlayPluginOptions = computed(() => {
  if (!Array.isArray(availablePlugins.value)) return [];
  const hasImages = overlaySelectionMedia.value.hasImages;
  const hasVideos = overlaySelectionMedia.value.hasVideos;
  return availablePlugins.value.filter((plugin) => {
    if (!plugin || !plugin.name) return false;
    const supportsImages = plugin.supports_images !== false;
    const supportsVideos = plugin.supports_videos === true;
    if (hasImages && !supportsImages) return false;
    if (hasVideos && !supportsVideos) return false;
    return true;
  });
});

const activeOverlayPluginSchema = computed(() => {
  if (!overlaySelectedPluginName.value) return null;
  return (
    overlayPluginOptions.value.find(
      (plugin) =>
        String(plugin.name) === String(overlaySelectedPluginName.value),
    ) || null
  );
});

const COMFYUI_PROMPT_STORAGE_PREFIX = "pixlstash:comfyuiPrompt:";

function getComfyuiPromptStorageKey() {
  if (typeof window === "undefined") return "";
  const workflow = String(comfyuiSelectedWorkflow.value || "default");
  return `${COMFYUI_PROMPT_STORAGE_PREFIX}${workflow}`;
}

function loadComfyuiPromptFromSession() {
  if (typeof window === "undefined") return null;
  if (!showComfyuiCaptionInput.value) return null;
  const key = getComfyuiPromptStorageKey();
  if (!key) return null;
  return window.sessionStorage?.getItem(key);
}

function persistComfyuiPromptToSession() {
  if (typeof window === "undefined") return;
  if (!showComfyuiCaptionInput.value) return;
  const key = getComfyuiPromptStorageKey();
  if (!key) return;
  const value = comfyuiCaption.value || "";
  window.sessionStorage?.setItem(key, value);
}

const validComfyWorkflows = computed(() =>
  (comfyuiWorkflows.value || []).filter(
    (workflow) => workflow?.valid && workflow?.workflow_type === "i2i",
  ),
);
const invalidComfyWorkflows = computed(() =>
  (comfyuiWorkflows.value || []).filter(
    (workflow) => !workflow?.valid && workflow?.workflow_type === "i2i",
  ),
);
const selectedComfyWorkflow = computed(() =>
  (comfyuiWorkflows.value || []).find(
    (workflow) => workflow?.name === comfyuiSelectedWorkflow.value,
  ),
);
const selectedComfyUsesCaption = computed(() => {
  const missing = Array.isArray(
    selectedComfyWorkflow.value?.missing_placeholders,
  )
    ? selectedComfyWorkflow.value.missing_placeholders
    : [];
  return !missing.includes(captionPlaceholderLabel);
});
const showComfyuiCaptionInput = computed(() => selectedComfyUsesCaption.value);
const canRunComfyWorkflow = computed(() => {
  return (
    !!image.value?.id &&
    !!comfyuiSelectedWorkflow.value &&
    !comfyuiRunLoading.value
  );
});
const showComfyuiCaptionHelp = computed(() => {
  return (
    showComfyuiCaptionInput.value &&
    !comfyuiCaptionFocused.value &&
    !comfyuiCaption.value
  );
});

watch(open, (value) => {
  if (!value) {
    resetOverlayStackState();
    pluginMenuOpen.value = false;
    comfyuiMenuOpen.value = false;
    chromeHidden.value = false;
    chromeRevealTimestamp.value = 0;
    addToSetControlKey.value += 1;
    zoomMode.value = "fit";
    resetPan();
    resetComfyState();
  } else {
    resetOverlayStackState();
    applyInitialExpandedStackState();
    pluginMenuOpen.value = false;
    comfyuiMenuOpen.value = false;
    chromeRevealTimestamp.value = Date.now();
    const stored = loadComfyuiPromptFromSession();
    if (stored != null) {
      comfyuiCaption.value = stored;
      comfyuiCaptionTouched.value = Boolean(stored);
    }
    fetchCharacters();
    fetchComfyWorkflows();
  }
});

watch(validComfyWorkflows, (workflows) => {
  const list = Array.isArray(workflows) ? workflows : [];
  if (!list.length) {
    comfyuiSelectedWorkflow.value = "";
    return;
  }
  const hasSelection = list.some(
    (workflow) => workflow?.name === comfyuiSelectedWorkflow.value,
  );
  if (!hasSelection) {
    comfyuiSelectedWorkflow.value = list[0].name;
  }
});

watch([comfyuiSelectedWorkflow, selectedComfyUsesCaption], () => {
  if (!selectedComfyUsesCaption.value) {
    comfyuiCaption.value = "";
    comfyuiCaptionTouched.value = false;
    comfyuiCaptionFocused.value = false;
    return;
  }
  const stored = loadComfyuiPromptFromSession();
  if (stored != null) {
    comfyuiCaption.value = stored;
    comfyuiCaptionTouched.value = Boolean(stored);
    comfyuiCaptionFocused.value = false;
  } else if (!comfyuiCaptionTouched.value) {
    comfyuiCaption.value = "";
  }
});

watch(comfyuiCaption, () => {
  persistComfyuiPromptToSession();
});

watch(comfyuiMenuOpen, (value) => {
  if (value) {
    comfyuiRunError.value = "";
    comfyuiRunSuccess.value = "";
    comfyuiCaptionFocused.value = false;
  }
});

watch(
  overlayPluginOptions,
  (plugins) => {
    if (!Array.isArray(plugins) || !plugins.length) {
      overlaySelectedPluginName.value = "";
      return;
    }
    if (!overlaySelectedPluginName.value) {
      overlaySelectedPluginName.value = String(plugins[0].name);
      return;
    }
    const exists = plugins.some(
      (plugin) =>
        String(plugin.name) === String(overlaySelectedPluginName.value),
    );
    if (!exists) {
      overlaySelectedPluginName.value = String(plugins[0].name);
    }
  },
  { immediate: true },
);

watch(overlaySelectedPluginName, () => {
  overlayPluginParameters.value = {};
});

watch(pluginMenuOpen, (isOpen) => {
  if (!isOpen) return;
  if (!overlaySelectedPluginName.value && overlayPluginOptions.value.length) {
    overlaySelectedPluginName.value = String(
      overlayPluginOptions.value[0].name,
    );
  }
  overlayPluginParameters.value = {};
});

async function fetchComfyWorkflows() {
  if (comfyuiWorkflowLoading.value) return;
  comfyuiWorkflowLoading.value = true;
  comfyuiWorkflowError.value = "";
  try {
    const res = await apiClient.get("/comfyui/workflows");
    const workflows = res.data?.workflows;
    comfyuiWorkflows.value = Array.isArray(workflows) ? workflows : [];
  } catch (err) {
    comfyuiWorkflowError.value =
      err?.response?.data?.detail || err?.message || String(err);
    comfyuiWorkflows.value = [];
  } finally {
    comfyuiWorkflowLoading.value = false;
  }
}

async function runComfyWorkflow() {
  if (!canRunComfyWorkflow.value) return;
  comfyuiRunLoading.value = true;
  comfyuiRunError.value = "";
  comfyuiRunSuccess.value = "";
  try {
    const payload = {
      picture_id: image.value.id,
      workflow_name: comfyuiSelectedWorkflow.value,
      caption: comfyuiCaption.value || "",
      client_id: comfyuiClientId.value || undefined,
    };
    const res = await apiClient.post(
      `${backendUrl.value}/comfyui/run_i2i`,
      payload,
    );
    const promptCount = Array.isArray(res.data?.prompts)
      ? res.data.prompts.length
      : 0;
    emit("comfyui-run", {
      prompts: Array.isArray(res.data?.prompts) ? res.data.prompts : [],
      pictureId: payload.picture_id ?? null,
    });
    comfyuiRunSuccess.value = promptCount
      ? `Queued ${promptCount} run(s) in ComfyUI.`
      : "Queued in ComfyUI.";
  } catch (err) {
    comfyuiRunError.value =
      err?.response?.data?.detail || err?.message || String(err);
  } finally {
    comfyuiRunLoading.value = false;
  }
}

function runOverlayPlugin() {
  if (!image.value?.id || !overlaySelectedPluginName.value) return;
  emit("run-plugin", {
    pluginName: overlaySelectedPluginName.value,
    pictureIds: [image.value.id],
    parameters: overlayPluginParameters.value || {},
  });
  pluginMenuOpen.value = false;
}

function getFullImageUrl(targetImage = null) {
  const data = targetImage || image.value;
  return appendShareToken(
    buildMediaUrl({ backendUrl: backendUrl.value, image: data }),
  );
}

function getOverlayImageList() {
  const expanded = filmstripImages.value;
  if (Array.isArray(expanded) && expanded.length) return expanded;
  return Array.isArray(allImages.value) ? allImages.value : [];
}

const filmstripImageById = computed(() => {
  const map = new Map();
  const list = getOverlayImageList();
  for (const item of list) {
    if (!item || item.id == null) continue;
    map.set(String(item.id), item);
  }
  return map;
});

const filmstripIndexById = computed(() => {
  const map = new Map();
  const list = filmstripImages.value;
  for (let idx = 0; idx < list.length; idx += 1) {
    const item = list[idx];
    if (!item || item.id == null) continue;
    map.set(String(item.id), idx);
  }
  return map;
});

function isImageInFilmstrip(targetId) {
  if (!targetId) return false;
  const list = filmstripImages.value;
  if (!Array.isArray(list) || !list.length) return false;
  return list.some((item) => String(item?.id) === String(targetId));
}

async function ensureOverlayFilmstripForImage() {
  const targetId = image.value?.id ?? null;
  if (!targetId) return;
  const stackId = getOverlayStackId(image.value);
  if (!stackId) return;
  const shouldExpand = !isImageInFilmstrip(targetId) && !!stackId;
  if (!shouldExpand) return;
  const stackCount = getOverlayStackCount(image.value);
  if (stackCount <= 1) return;
  if (!overlayExpandedStackIds.value.has(stackId)) {
    const nextIds = new Set(overlayExpandedStackIds.value);
    nextIds.add(stackId);
    overlayExpandedStackIds.value = nextIds;
  }
  await ensureOverlayStackMembersLoaded(stackId, image.value);
}

function getOverlayStackId(img) {
  const stackId = img?.stack_id ?? img?.stackId ?? null;
  if (stackId === null || stackId === undefined) return null;
  return String(stackId);
}

function getOverlayStackPositionValue(img) {
  if (!img) return null;
  const raw = img.stack_position ?? img.stackPosition ?? null;
  if (raw === null || raw === undefined) return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

function getOverlayStackSmartScoreValue(img) {
  const raw = img?.smartScore ?? img?.smart_score ?? null;
  if (raw === null || raw === undefined) return 0;
  const value = Number(raw);
  return Number.isFinite(value) ? value : 0;
}

function getOverlayStackCreatedAtTs(img) {
  if (!img?.created_at) return 0;
  const ts = new Date(img.created_at).getTime();
  return Number.isFinite(ts) ? ts : 0;
}

function compareOverlayStackOrder(a, b) {
  const posA = getOverlayStackPositionValue(a);
  const posB = getOverlayStackPositionValue(b);
  if (posA !== null || posB !== null) {
    if (posA === null) return 1;
    if (posB === null) return -1;
    if (posA !== posB) return posA - posB;
  }
  const scoreA = Number(a?.score ?? 0);
  const scoreB = Number(b?.score ?? 0);
  if (scoreA !== scoreB) return scoreB - scoreA;
  const smartA = getOverlayStackSmartScoreValue(a);
  const smartB = getOverlayStackSmartScoreValue(b);
  if (smartA !== smartB) return smartB - smartA;
  const dateA = getOverlayStackCreatedAtTs(a);
  const dateB = getOverlayStackCreatedAtTs(b);
  if (dateA !== dateB) return dateB - dateA;
  const idA = Number(a?.id ?? 0);
  const idB = Number(b?.id ?? 0);
  return idA - idB;
}

function sortOverlayStackMembers(members) {
  if (!Array.isArray(members)) return [];
  return members.slice().sort(compareOverlayStackOrder);
}

function buildOverlayStackLeaderMap(images) {
  const byStack = new Map();
  for (const img of images) {
    const stackId = getOverlayStackId(img);
    if (!stackId || img?.id == null) continue;
    if (!byStack.has(stackId)) {
      byStack.set(stackId, []);
    }
    byStack.get(stackId).push(img);
  }
  const leaders = new Map();
  for (const [stackId, members] of byStack.entries()) {
    const ordered = sortOverlayStackMembers(members);
    const leader = ordered[0];
    if (leader?.id != null) {
      leaders.set(stackId, String(leader.id));
    }
  }
  return leaders;
}

function getOverlayLocalStackMembers(stackId) {
  if (!stackId) return [];
  const members = allImagesByStackId.value.get(stackId);
  return Array.isArray(members) ? members : [];
}

function getOverlayStackSignature(stackId) {
  if (!stackId) return "";
  const members = getOverlayLocalStackMembers(stackId);
  if (!members.length) return "";
  const parts = members.map((img) => {
    const id = img?.id != null ? String(img.id) : "";
    const pos = getOverlayStackPositionValue(img);
    return `${id}:${pos === null ? "x" : String(pos)}`;
  });
  return `${members.length}|${parts.join(",")}`;
}

function normalizeOverlayStackMembersForStack(stackId, members) {
  if (!stackId || !Array.isArray(members) || !members.length) return [];
  const normalized = [];
  for (const member of members) {
    if (!member || member.id == null) continue;
    const id = String(member.id);
    const latest = allImageById.value.get(id) || member;
    if (getOverlayStackId(latest) !== stackId) continue;
    normalized.push(latest);
  }
  return sortOverlayStackMembers(normalized);
}

const overlayStackCounts = computed(() => {
  const counts = new Map();
  const list = Array.isArray(allImages.value) ? allImages.value : [];
  for (const img of list) {
    const stackId = getOverlayStackId(img);
    if (!stackId) continue;
    counts.set(stackId, (counts.get(stackId) || 0) + 1);
  }
  return counts;
});

function getOverlayStackCount(item) {
  const count = Number(item?.stackCount ?? item?.stack_count ?? 0);
  if (Number.isFinite(count) && count > 0) return count;
  const stackId = getOverlayStackId(item);
  if (!stackId) return 0;
  const expanded = overlayExpandedStackMembers.value.get(stackId);
  const ids = Array.isArray(expanded?.ids) ? expanded.ids : [];
  if (ids.length) return ids.length;
  return overlayStackCounts.value.get(stackId) || 0;
}

function getStackColorIndexFromId(stackId) {
  if (stackId === null || stackId === undefined) return null;
  const numeric = Number(stackId);
  if (Number.isFinite(numeric)) return numeric;
  const raw = String(stackId);
  let hash = 0;
  for (let i = 0; i < raw.length; i += 1) {
    hash = (hash * 31 + raw.charCodeAt(i)) % 2147483647;
  }
  return hash || null;
}

function getOverlayStackColor(item) {
  if (!item) return null;
  if (typeof item.stackColor === "string" && item.stackColor) {
    return item.stackColor;
  }
  const stackIndex =
    typeof item.stackIndex === "number"
      ? item.stackIndex
      : typeof item.stack_index === "number"
        ? item.stack_index
        : null;
  if (typeof stackIndex === "number") {
    return getStackColor(stackIndex);
  }
  const stackId = getOverlayStackId(item);
  const index = getStackColorIndexFromId(stackId);
  if (index === null) return null;
  return getStackColor(index);
}

function applyOverlayStackBackgroundAlpha(color) {
  if (!color || typeof color !== "string") return color;
  const trimmed = color.trim();
  if (!trimmed) return color;
  if (trimmed.startsWith("hsla(") || trimmed.startsWith("rgba(")) {
    return trimmed;
  }
  if (trimmed.startsWith("hsl(")) {
    const inner = trimmed.slice(4, -1).trim();
    if (inner.includes(",")) {
      return `hsla(${inner}, 0.6)`;
    }
    return `hsl(${inner} / 0.6)`;
  }
  if (trimmed.startsWith("rgb(")) {
    const inner = trimmed.slice(4, -1).trim();
    if (inner.includes(",")) {
      return `rgba(${inner}, 0.6)`;
    }
    return `rgb(${inner} / 0.6)`;
  }
  return trimmed;
}

function buildOverlayExpandedStackImages(stackId, fallbackItem, stackCount) {
  const entry = overlayExpandedStackMembers.value.get(stackId);
  const images = Array.isArray(entry?.images) ? entry.images : [];
  const normalizedCached = normalizeOverlayStackMembersForStack(
    stackId,
    images,
  );
  const localMembers = getOverlayLocalStackMembers(stackId);
  const imageById = new Map();
  for (const img of normalizedCached) {
    if (!img || img.id == null) continue;
    imageById.set(String(img.id), img);
  }
  for (const img of localMembers) {
    if (!img || img.id == null) continue;
    imageById.set(String(img.id), img);
  }
  const ordered = [];
  const seen = new Set();
  const addImage = (img) => {
    if (!img || img.id == null) return;
    const key = String(img.id);
    if (seen.has(key)) return;
    seen.add(key);
    ordered.push(img);
  };

  const orderedIds = sortOverlayStackMembers(
    Array.from(imageById.values()),
  ).map((img) => String(img.id));
  for (const id of orderedIds) {
    addImage(imageById.get(String(id)));
  }

  if (fallbackItem?.id != null) {
    addImage(fallbackItem);
  }

  if (ordered.length) {
    ordered[0] = { ...ordered[0], stackCount };
  }
  return ordered;
}

function collapseOverlayStackImages(images) {
  if (!Array.isArray(images) || images.length === 0) return [];
  const counts = new Map();
  for (const img of images) {
    const stackId = getOverlayStackId(img);
    if (!stackId) continue;
    counts.set(stackId, (counts.get(stackId) || 0) + 1);
  }
  if (!counts.size) return images;
  const leaders = allImageLeaderByStackId.value;
  const seen = new Set();
  const collapsed = [];
  for (const img of images) {
    const stackId = getOverlayStackId(img);
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
    const stackCount = getOverlayStackCount(img) || counts.get(stackId) || 1;
    if (overlayExpandedStackIds.value.has(stackId)) {
      const expanded = buildOverlayExpandedStackImages(
        stackId,
        img,
        stackCount,
      );
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

async function ensureOverlayStackMembersLoaded(
  stackId,
  referenceItem = null,
  options = {},
) {
  if (!stackId) return false;
  const forceReload = options?.force === true;
  const localMembers = getOverlayLocalStackMembers(stackId);
  const expectedCount = Number(
    referenceItem?.stackCount ?? referenceItem?.stack_count ?? 0,
  );
  if (!forceReload && localMembers.length > 1) {
    const orderedLocal = sortOverlayStackMembers(localMembers);
    if (!Number.isFinite(expectedCount) || expectedCount <= 0) {
      const ids = orderedLocal
        .filter((img) => img && img.id != null)
        .map((img) => String(img.id));
      const nextMembers = new Map(overlayExpandedStackMembers.value);
      nextMembers.set(stackId, { ids, images: orderedLocal });
      overlayExpandedStackMembers.value = nextMembers;
      return true;
    }
    if (orderedLocal.length >= expectedCount) {
      const ids = orderedLocal
        .filter((img) => img && img.id != null)
        .map((img) => String(img.id));
      const nextMembers = new Map(overlayExpandedStackMembers.value);
      nextMembers.set(stackId, { ids, images: orderedLocal });
      overlayExpandedStackMembers.value = nextMembers;
      return true;
    }
  }
  const existing = overlayExpandedStackMembers.value.get(stackId);
  if (
    !forceReload &&
    existing &&
    Array.isArray(existing.images) &&
    existing.images.length
  ) {
    if (
      !Number.isFinite(expectedCount) ||
      expectedCount <= 0 ||
      existing.images.length >= expectedCount
    ) {
      return true;
    }
  }
  if (overlayExpandedStackLoading.value.has(stackId)) return false;
  if (!backendUrl.value) return false;
  const nextLoading = new Set(overlayExpandedStackLoading.value);
  nextLoading.add(stackId);
  overlayExpandedStackLoading.value = nextLoading;
  try {
    const res = await apiClient.get(
      `${backendUrl.value}/stacks/${stackId}/pictures?fields=grid`,
    );
    const data = res.data;
    const images = Array.isArray(data) ? data : [];
    const ordered = normalizeOverlayStackMembersForStack(stackId, images);
    const ids = ordered
      .filter((img) => img && img.id != null)
      .map((img) => String(img.id));
    const nextMembers = new Map(overlayExpandedStackMembers.value);
    nextMembers.set(stackId, { ids, images: ordered });
    overlayExpandedStackMembers.value = nextMembers;
    return true;
  } catch (e) {
    console.error("Failed to load overlay stack members:", e);
    return false;
  } finally {
    const cleared = new Set(overlayExpandedStackLoading.value);
    cleared.delete(stackId);
    overlayExpandedStackLoading.value = cleared;
  }
}

function getOverlayStackLeaderId(stackId) {
  if (!stackId) return null;
  const localMembers = getOverlayLocalStackMembers(stackId);
  if (localMembers.length && localMembers[0]?.id != null) {
    return String(localMembers[0].id);
  }
  const cached = overlayExpandedStackMembers.value.get(stackId);
  const cachedIds = Array.isArray(cached?.ids) ? cached.ids : [];
  if (cachedIds.length) {
    return String(cachedIds[0]);
  }
  const cachedImages = Array.isArray(cached?.images) ? cached.images : [];
  const orderedCached = sortOverlayStackMembers(cachedImages);
  if (orderedCached.length && orderedCached[0]?.id != null) {
    return String(orderedCached[0].id);
  }
  return null;
}

async function toggleFilmstripStackExpand(item) {
  const stackId = getOverlayStackId(item);
  if (!stackId) return;
  if (overlayExpandedStackIds.value.has(stackId)) {
    const currentStackId = getOverlayStackId(image.value);
    if (currentStackId === stackId && image.value?.id != null) {
      // Always navigate to the leader when collapsing, regardless of showStacks.
      // If the current image is a non-leader it will no longer be visible in the
      // collapsed filmstrip; staying on it would cause ensureOverlayFilmstripForImage
      // to immediately re-expand the stack on the next allImages poll or metadata fetch.
      const leaderId = getOverlayStackLeaderId(stackId);
      if (leaderId && leaderId !== String(image.value.id)) {
        setOverlayImageById(leaderId);
      }
    }
    const nextIds = new Set(overlayExpandedStackIds.value);
    nextIds.delete(stackId);
    overlayExpandedStackIds.value = nextIds;
    return;
  }
  const nextIds = new Set(overlayExpandedStackIds.value);
  nextIds.add(stackId);
  overlayExpandedStackIds.value = nextIds;
  await ensureOverlayStackMembersLoaded(stackId, item);
}

function prefetchFilmstripStackMembers(item) {
  const stackId = getOverlayStackId(item);
  if (!stackId) return;
  void ensureOverlayStackMembersLoaded(stackId, item);
}

const filmstripImages = computed(() => {
  const images = Array.isArray(allImages.value) ? allImages.value : [];
  return collapseOverlayStackImages(images);
});

watch(
  () => initialImageId.value,
  (newId) => {
    setOverlayImageById(newId);
    void ensureOverlayFilmstripForImage();
  },
  { immediate: true },
);

const pendingAllImagesUpdate = ref(false);

async function applyAllImagesUpdate() {
  const currentId = image.value?.id ?? initialImageId.value;
  if (currentId != null && currentId !== "") {
    setOverlayImageById(currentId);
  }
  void ensureOverlayFilmstripForImage();

  const stackId = getOverlayStackId(image.value);
  if (!stackId) return;
  const nextSignature = getOverlayStackSignature(stackId);
  if (!nextSignature) return;

  const previousSignature = overlayStackSignatures.value.get(stackId) || "";
  const nextSignatures = new Map(overlayStackSignatures.value);
  nextSignatures.set(stackId, nextSignature);
  overlayStackSignatures.value = nextSignatures;

  if (previousSignature === nextSignature) return;

  if (previousSignature) {
    emit("overlay-change", {
      imageId: image.value?.id ?? null,
      fields: { stack: true },
      stackId,
    });
  }

  const nextMembers = new Map(overlayExpandedStackMembers.value);
  nextMembers.delete(stackId);
  overlayExpandedStackMembers.value = nextMembers;

  const reloadToken = overlayStackReloadToken.value + 1;
  overlayStackReloadToken.value = reloadToken;

  await ensureOverlayStackMembersLoaded(stackId, image.value, {
    force: true,
  });
  if (overlayStackReloadToken.value !== reloadToken) return;

  // Only navigate to the new stack leader when the stack structure genuinely
  // changed while we were already watching it (previousSignature is truthy).
  // When previousSignature is "" this is just the first time we record the
  // signature; navigating away from the user's deliberate non-leader selection
  // here would be wrong (and is the root cause of the tag-change nav bug).
  if (!previousSignature) return;

  const refreshed = overlayExpandedStackMembers.value.get(stackId);
  const ids = Array.isArray(refreshed?.ids) ? refreshed.ids : [];
  const localMembers = getOverlayLocalStackMembers(stackId);
  const topId =
    ids.length > 0
      ? ids[0]
      : localMembers[0]?.id != null
        ? String(localMembers[0].id)
        : null;
  if (topId && String(image.value?.id ?? "") !== String(topId)) {
    setOverlayImageById(topId);
  }
}

watch(
  () => allImages.value,
  async () => {
    // Don't disturb the DOM while the user is actively typing — the reactive
    // update to image.value causes a DOM patch that can blur the focused input.
    // Set a flag so we apply the update as soon as editing finishes.
    if (isAddingTag.value || isDescriptionEditing.value) {
      pendingAllImagesUpdate.value = true;
      return;
    }
    await applyAllImagesUpdate();
  },
);

// Flush any deferred allImages update as soon as the user finishes editing.
watch(
  () => isAddingTag.value || isDescriptionEditing.value,
  async (isEditing) => {
    if (!isEditing && pendingAllImagesUpdate.value) {
      pendingAllImagesUpdate.value = false;
      await applyAllImagesUpdate();
    }
  },
);

watch(showStacks, (value) => {
  if (value) {
    // Ensure the current image is visible; if it's a non-leader stack member
    // that isn't in the filmstrip, ensureOverlayFilmstripForImage will expand
    // just that stack as needed.
    void ensureOverlayFilmstripForImage();
  } else {
    overlayExpandedStackIds.value = new Set();
  }
});

watch(image, (newImage, oldImage) => {
  if (newImage?.id === oldImage?.id) return;
  comfyuiCaptionTouched.value = false;
  comfyuiCaption.value = "";
  resetOverlayCopyState();
});

watch(open, (isOpen) => {
  if (!isOpen) {
    descriptionPanelRef.value?.cancelEditDescription();
    descriptionPanelRef.value?.resetCopyState();
    resetOverlayCopyState();
  }
});

function resetComfyState() {
  comfyuiMenuOpen.value = false;
  comfyuiRunLoading.value = false;
  comfyuiRunError.value = "";
  comfyuiRunSuccess.value = "";
  comfyuiCaptionTouched.value = false;
  comfyuiCaption.value = "";
}

function setScore(n) {
  if (!image.value) return;
  if (isReadOnly.value) {
    emit("set-guest-score", image.value, n);
    return;
  }
  image.value.score = toggleScore(image.value.score, n);
  emit("apply-score", image.value, image.value.score);
}

function showPrevImage() {
  return navigateOverlayImage(-1);
}

function navigateOverlayImage(direction, options = {}) {
  const sorted = filmstripImages.value;
  const allowWrap = options?.wrap !== false;
  if (!image.value || !sorted.length) return;
  const idx = filmstripIndexById.value.get(String(image.value.id)) ?? -1;
  if (idx === -1) return;
  let nextIdx = idx + direction;
  if (allowWrap) {
    nextIdx = (nextIdx + sorted.length) % sorted.length;
  } else {
    nextIdx = Math.min(sorted.length - 1, Math.max(0, nextIdx));
    if (nextIdx === idx) {
      return false;
    }
  }
  setOverlayImageById(sorted[nextIdx]?.id ?? null);
  return true;
}

function selectImageByIndex(idx) {
  const list = filmstripImages.value;
  if (!Array.isArray(list)) return;
  const target = list[idx];
  if (target) {
    setOverlayImageById(target.id ?? null);
  }
}

function onFilmstripNavigate(direction) {
  if (isMobile.value) return;
  handleUserActivity();
  const moved = navigateOverlayImage(direction, { wrap: false });
  if (moved === false) {
    filmstripRef.value?.resetWheel?.();
  }
}

function showNextImage() {
  return navigateOverlayImage(1);
}

function handleKeydown(e) {
  if (!open.value) return;
  // Prevent other window keydown listeners (e.g. ImageGrid) from seeing this
  // event while the overlay is open. ImageOverlay mounts before ImageGrid so
  // its handler runs first; without this, ImageGrid would process the same
  // keypress (e.g. Escape) after the overlay has already handled it.
  e.stopImmediatePropagation();

  // When chrome is hidden, only Space and Escape reveal it — other keys still
  // navigate/act but don't bring the chrome back.
  if (chromeHidden.value) {
    if (e.key === " " || e.key === "Spacebar") {
      e.preventDefault();
      handleUserActivity();
      return;
    }
    // Let navigation keys work silently without revealing chrome
    if (
      [
        "ArrowLeft",
        "Left",
        "ArrowRight",
        "Right",
        "ArrowUp",
        "Up",
        "ArrowDown",
        "Down",
      ].includes(e.key)
    ) {
      if (["ArrowLeft", "Left", "ArrowUp", "Up"].includes(e.key))
        showPrevImage();
      else showNextImage();
      return;
    }
    // Escape reveals chrome (and closes draw mode if active)
    if (e.key === "Escape") {
      handleUserActivity();
      return;
    }
    // T reveals chrome, opens the sidebar, and starts tag entry
    if (e.key === "t" || e.key === "T") {
      e.preventDefault();
      handleUserActivity();
      if (!isReadOnly.value) {
        sidebarOpen.value = true;
        nextTick(() => tagsPanelRef.value?.beginAddTag());
      }
      return;
    }
    // All other keys: ignore while chrome is hidden
    return;
  }

  handleUserActivity();

  if (comfyuiCaptionFocused.value) {
    if (e.key === "Escape") {
      if (comfyuiMenuOpen.value) {
        comfyuiMenuOpen.value = false;
      }
      e.preventDefault();
    }
    return;
  }

  if (isDescriptionEditing.value || isAddingTag.value) {
    if (e.key === "Escape") {
      if (isDescriptionEditing.value) {
        descriptionPanelRef.value?.cancelEditDescription();
      } else if (isAddingTag.value) {
        tagsPanelRef.value?.cancelAddTag();
      }
    }
    return;
  }

  // Block shortcuts when any other editable element (e.g. plugin parameter inputs) has focus.
  // Still allow ESC to close the plugin/comfyui menu if open.
  const target = e.target;
  const isEditable =
    target instanceof HTMLElement &&
    (target.isContentEditable ||
      ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) ||
      target.getAttribute("role") === "textbox");
  if (isEditable) {
    if (e.key === "Escape") {
      if (pluginMenuOpen.value) {
        pluginMenuOpen.value = false;
        e.preventDefault();
      } else if (comfyuiMenuOpen.value) {
        comfyuiMenuOpen.value = false;
        e.preventDefault();
      } else if (target.tagName === "SELECT") {
        // Close the select dropdown on ESC, since it doesn't do that by default.
        target.blur();
      } else {
        return;
      }
    } else {
      return;
    }
  }

  if ((e.ctrlKey || e.metaKey) && (e.key === "c" || e.key === "C")) {
    const isEditable =
      target &&
      (target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        target.isContentEditable);
    if (!isEditable) {
      e.preventDefault();
      copyOverlayImage();
    }
    return;
  }

  if (e.key === "Escape") {
    if (drawMode.value) {
      clearDrawMode();
    } else if (pluginMenuOpen.value) {
      pluginMenuOpen.value = false;
    } else if (comfyuiMenuOpen.value) {
      comfyuiMenuOpen.value = false;
    } else {
      emit("close");
    }
  } else if (["ArrowLeft", "Left", "ArrowUp", "Up"].includes(e.key)) {
    showPrevImage();
  } else if (["ArrowRight", "Right", "ArrowDown", "Down"].includes(e.key)) {
    showNextImage();
  } else if (e.key === "z" || e.key === "Z") {
    toggleZoom();
  } else if (e.key === " " || e.key === "Spacebar") {
    e.preventDefault();
    if (!chromeHidden.value) {
      chromeHidden.value = true;
    }
  } else if (e.key === "s" || e.key === "S") {
    toggleSidebar();
  } else if (e.key === "a" || e.key === "A") {
    if (addToSetControlRef.value?.lastUsedSet?.id) {
      e.preventDefault();
      addToSetControlRef.value.addToLastSet();
    }
  } else if ((e.key === "t" || e.key === "T") && sidebarOpen.value) {
    if (!isReadOnly.value) {
      e.preventDefault();
      tagsPanelRef.value?.beginAddTag();
    }
  } else if (["1", "2", "3", "4", "5"].includes(e.key)) {
    if (!isReadOnly.value) {
      const score = parseInt(e.key, 10);
      if (image.value) setScore(score);
    }
  }
}

const showFaceBbox = ref(false);
const isMobile = ref(false);
const MOBILE_BREAKPOINT = 900;
const FILMSTRIP_VISIBLE_COUNT = 7;
const FILMSTRIP_BUFFER_COUNT = 3;
const FILMSTRIP_GAP = 0;
const FILMSTRIP_RAIL_PADDING = 8;
const ZOOM_WHEEL_THRESHOLD = 40;
const ZOOM_WHEEL_SENSITIVITY = 0.25;
// Approximate pixels per line for wheel events reported in DOM_DELTA_LINE units.
const WHEEL_LINE_HEIGHT_PX = 16;
const windowHeight = ref(0);
const overlayMainRef = ref(null);
const filmstripRef = ref(null);
const touchStart = ref({ x: 0, y: 0, time: 0 });
const touchLatest = ref({ x: 0, y: 0 });
const swipeHintVisible = ref(false);
let swipeHintTimer = null;
let touchTapConsumed = false;
let lastTouchEndTime = 0;
let zoomWheelAccumulator = 0;

function updateViewportMetrics() {
  if (typeof window !== "undefined") {
    isMobile.value = window.innerWidth <= MOBILE_BREAKPOINT;
    windowHeight.value = window.innerHeight || 0;
  }
}

const filmstripThumbSizePx = computed(() => {
  const targetCount = FILMSTRIP_VISIBLE_COUNT;
  const railPaddingTotal = FILMSTRIP_RAIL_PADDING * 2;
  const railHeight = filmstripRef.value?.railEl?.offsetHeight || 0;
  const overlayMainHeight = overlayMainRef.value?.offsetHeight || 0;
  const fallbackHeight = Math.max(0, windowHeight.value || 0);
  const availableRaw = Math.max(0, overlayMainHeight || fallbackHeight);
  const available = railHeight > 0 ? railHeight : Math.max(0, availableRaw);
  const usable = Math.max(0, available - railPaddingTotal);
  const totalGaps = FILMSTRIP_GAP * (targetCount - 1);
  const rawSize = (usable - totalGaps) / targetCount;
  const computed = Number.isFinite(rawSize) ? Math.floor(rawSize) : 0;
  return computed > 0 ? Math.max(36, computed - 8) : 80;
});

const filmstripStyleVars = computed(() => {
  const railPaddingTotal = FILMSTRIP_RAIL_PADDING * 2;
  const thumbSize = filmstripThumbSizePx.value;
  const railHeight = filmstripRef.value?.railEl?.offsetHeight || 0;
  const overlayMainHeight = overlayMainRef.value?.offsetHeight || 0;
  const fallbackHeight = Math.max(0, windowHeight.value || 0);
  const availableRaw = Math.max(0, overlayMainHeight || fallbackHeight);
  const available = railHeight > 0 ? railHeight : Math.max(0, availableRaw);
  const railWidth = thumbSize + 12;
  return {
    "--filmstrip-thumb-size": `${thumbSize}px`,
    "--filmstrip-rail-width": `${railWidth}px`,
    "--filmstrip-available-height": `${available}px`,
    "--filmstrip-gap": `${FILMSTRIP_GAP}px`,
    "--filmstrip-padding": `${FILMSTRIP_RAIL_PADDING}px`,
    "--filmstrip-padding-total": `${railPaddingTotal}px`,
  };
});

function showSwipeHint() {
  if (!isMobile.value) return;
  swipeHintVisible.value = true;
  if (swipeHintTimer) {
    clearTimeout(swipeHintTimer);
  }
  swipeHintTimer = window.setTimeout(() => {
    swipeHintVisible.value = false;
  }, 3000);
}

function handleBackdropClick() {
  emit("close");
}

function handleUserActivity() {
  if (chromeHidden.value) {
    chromeRevealTimestamp.value = Date.now();
  }

  chromeHidden.value = false;
}

function handleMouseActivity() {
  if (chromeHidden.value) return; // mouse movement never reveals chrome
  if (Date.now() - lastTouchEndTime < 600) return;
  handleUserActivity();
}

function handleWheelActivity() {
  // Scroll always reveals chrome (deliberate interaction)
  handleUserActivity();
}

function handleOverlayPointerDown() {
  menuWasOpenOnPointerDown = !!(
    document.querySelector(".v-overlay--active") ||
    document.querySelector(".add-to-set.open") ||
    document.querySelector(".add-to-project.open")
  );
}

function handleOverlayClick(event) {
  if (touchTapConsumed) {
    touchTapConsumed = false;
    return;
  }
  const target = event?.target;
  if (!target || !(target instanceof HTMLElement)) {
    handleUserActivity();
    return;
  }
  if (chromeHidden.value) {
    handleUserActivity();
    return;
  }
  if (Date.now() - chromeRevealTimestamp.value < 500) {
    return;
  }
  if (menuWasOpenOnPointerDown) {
    menuWasOpenOnPointerDown = false;
    return;
  }
  const interactiveSelector =
    "button, a, input, select, textarea, label, summary, details";
  const interactiveContainerSelector =
    ".overlay-sidebar, .overlay-rail, .overlay-nav";
  if (
    target.closest(interactiveSelector) ||
    target.closest(interactiveContainerSelector)
  ) {
    handleUserActivity();
    return;
  }
  chromeHidden.value = true;
}

function toggleSidebar() {
  sidebarOpen.value = !sidebarOpen.value;
  if (sidebarOpen.value) {
    chromeHidden.value = false;
  } else {
    handleUserActivity();
  }
}

function openSidebarFromTeaser() {
  if (!image.value) return;
  sidebarOpen.value = true;
  chromeHidden.value = false;
  descriptionPanelRef.value?.startEditDescription();
}

function toggleZoom(event = null) {
  const target = event?.target;
  if (target instanceof HTMLElement && target.closest(".overlay-nav")) {
    return;
  }
  const currentIndex = zoomSteps.findIndex((step) => step === zoomMode.value);
  const nextIndex = (currentIndex + 1) % zoomSteps.length;
  zoomMode.value = zoomSteps[nextIndex];
  if (zoomMode.value === "fit") {
    resetPan();
  }
}

function resetPan() {
  pan.x = 0;
  pan.y = 0;
}

function onPanStart(event) {
  if (drawMode.value) return;
  if (!isZoomed.value) return;
  event.preventDefault();
  isPanning.value = true;
  lastPointer.value = { x: event.clientX, y: event.clientY };
  if (event.currentTarget?.setPointerCapture) {
    event.currentTarget.setPointerCapture(event.pointerId);
  }
}

function onPanMove(event) {
  if (!isPanning.value || !isZoomed.value) return;
  const dx = event.clientX - lastPointer.value.x;
  const dy = event.clientY - lastPointer.value.y;
  pan.x += dx;
  pan.y += dy;
  lastPointer.value = { x: event.clientX, y: event.clientY };
}

function onPanEnd(event) {
  isPanning.value = false;
  try {
    event?.currentTarget?.releasePointerCapture(event.pointerId);
  } catch (_) {
    /* pointer already released */
  }
}

function handleMediaDragStart(event) {
  if (isZoomed.value) {
    event.preventDefault();
  }
}

function onWheelZoom(event) {
  if (!open.value) return;
  handleUserActivity();
  const deltaY = normalizeZoomWheelDelta(event);
  if (!Number.isFinite(deltaY) || deltaY === 0) return;
  zoomWheelAccumulator += deltaY;
  if (Math.abs(zoomWheelAccumulator) < ZOOM_WHEEL_THRESHOLD) {
    return;
  }
  const direction = Math.sign(zoomWheelAccumulator);
  zoomWheelAccumulator -= direction * ZOOM_WHEEL_THRESHOLD;
  const currentIndex = zoomSteps.findIndex((step) => step === zoomMode.value);
  if (direction < 0 && currentIndex < zoomSteps.length - 1) {
    zoomMode.value = zoomSteps[currentIndex + 1];
  } else if (direction > 0 && currentIndex > 0) {
    zoomMode.value = zoomSteps[currentIndex - 1];
  } else {
    zoomWheelAccumulator = 0;
  }
  if (zoomMode.value === "fit") {
    resetPan();
  }
}

function normalizeZoomWheelDelta(event) {
  if (!event) return 0;
  const raw = Number(event.deltaY ?? 0);
  if (!Number.isFinite(raw) || raw === 0) return 0;
  const scaled = raw * ZOOM_WHEEL_SENSITIVITY;
  if (event.deltaMode === 1) {
    return scaled * WHEEL_LINE_HEIGHT_PX;
  }
  if (event.deltaMode === 2) {
    const pagePx = Number(windowHeight.value) || 800;
    return scaled * pagePx;
  }
  return scaled;
}

const mediaTransformStyle = computed(() => {
  const scale = zoomScale.value;
  return {
    transform: `translate(${pan.x}px, ${pan.y}px) scale(${scale})`,
  };
});

const zoomScale = computed(() => {
  if (zoomMode.value === "fit") return 1;
  const renderedWidth = overlayDims.value.width || 1;
  const renderedHeight = overlayDims.value.height || 1;
  const naturalWidth = overlayDims.value.naturalWidth || renderedWidth;
  const naturalHeight = overlayDims.value.naturalHeight || renderedHeight;
  const baseScale = Math.min(
    naturalWidth / renderedWidth,
    naturalHeight / renderedHeight,
  );
  return baseScale * Number(zoomMode.value);
});

const isZoomed = computed(() => zoomScale.value > 1.01);

const zoomHudLabel = computed(() => {
  if (zoomMode.value === "fit") return "Fit";
  return `${Math.round(Number(zoomMode.value) * 100)}%`;
});

const filmstripCanvasData = computed(() => {
  const images = filmstripImages.value;
  if (!images.length || !image.value) {
    return { items: [], topBufferSlots: 0 };
  }
  const currentIndex = filmstripIndexById.value.get(String(image.value.id));
  const safeCurrentIndex =
    Number.isFinite(currentIndex) && currentIndex >= 0 ? currentIndex : -1;
  if (safeCurrentIndex === -1) {
    return { items: [], topBufferSlots: 0 };
  }

  const visibleCount = Math.min(
    isMobile.value ? 5 : FILMSTRIP_VISIBLE_COUNT,
    images.length,
  );
  let visibleStart = safeCurrentIndex - Math.floor(visibleCount / 2);
  let visibleEnd = visibleStart + visibleCount - 1;
  if (visibleStart < 0) {
    visibleEnd += Math.abs(visibleStart);
    visibleStart = 0;
  }
  if (visibleEnd >= images.length) {
    const overshoot = visibleEnd - (images.length - 1);
    visibleStart = Math.max(0, visibleStart - overshoot);
    visibleEnd = images.length - 1;
  }

  const bufferCount = isMobile.value ? 0 : FILMSTRIP_BUFFER_COUNT;
  const canvasCount = Math.min(visibleCount + bufferCount * 2, images.length);
  let canvasStart = Math.max(0, visibleStart - bufferCount);
  let canvasEnd = Math.min(images.length - 1, visibleEnd + bufferCount);
  while (canvasEnd - canvasStart + 1 < canvasCount && canvasStart > 0) {
    canvasStart -= 1;
  }
  while (
    canvasEnd - canvasStart + 1 < canvasCount &&
    canvasEnd < images.length - 1
  ) {
    canvasEnd += 1;
  }

  const topBufferSlots = Math.max(0, visibleStart - canvasStart);
  const indices = [];
  for (let idx = canvasStart; idx <= canvasEnd; idx += 1) {
    indices.push(idx);
  }
  const items = indices.map((idx) => {
    const item = images[idx];
    const stackId = getOverlayStackId(item);
    const prevItem = idx > 0 ? images[idx - 1] : null;
    const prevStackId = getOverlayStackId(prevItem);
    const isStackExpanded = stackId
      ? overlayExpandedStackIds.value.has(stackId)
      : false;
    const isStackJoined =
      isStackExpanded && !!stackId && stackId === prevStackId;

    // Pre-compute all display fields for OverlayFilmstrip
    const thumbSrc = (() => {
      if (item.thumbnail) {
        const thumbnail = String(item.thumbnail);
        if (!thumbnail) return "";
        if (thumbnail.startsWith("http")) return appendShareToken(thumbnail);
        if (thumbnail.startsWith("/")) {
          return appendShareToken(
            backendUrl.value ? `${backendUrl.value}${thumbnail}` : thumbnail,
          );
        }
        return appendShareToken(thumbnail);
      }
      if (item.id != null && backendUrl.value) {
        return appendShareToken(
          `${backendUrl.value}/pictures/thumbnails/${item.id}.webp`,
        );
      }
      return "";
    })();
    const stackColor = getOverlayStackColor(item);
    const stackTileStyle = (() => {
      if (!isStackExpanded) return {};
      const color = applyOverlayStackBackgroundAlpha(stackColor);
      return color ? { "--filmstrip-stack-bg": color } : {};
    })();

    return {
      ...item,
      index: idx,
      isActive: idx === safeCurrentIndex,
      isStackJoined,
      thumbSrc,
      isVideo: isSupportedVideoFile(getOverlayFormat(item)),
      stackBadgeVisible: getOverlayStackCount(item) > 1,
      isStackLead: stackId !== prevStackId,
      stackBadgeTitle: (() => {
        const count = getOverlayStackCount(item);
        return count <= 1 ? "" : `Stack of ${count} images`;
      })(),
      problemBadgeVisible:
        showProblemIcon.value &&
        !!thumbSrc &&
        Array.isArray(item?.penalised_tags) &&
        item.penalised_tags.length > 0,
      problemTitle: (() => {
        const tags = Array.isArray(item?.penalised_tags)
          ? item.penalised_tags
          : [];
        return tags.length ? `Penalised tags: ${tags.join(", ")}` : "";
      })(),
      stackIconStyle: stackColor ? { color: stackColor } : {},
      stackTileStyle,
    };
  });

  return { items, topBufferSlots };
});

const filmstripCanvasWindow = computed(() => filmstripCanvasData.value.items);

const filmstripCanvasStyle = computed(() => {
  if (isMobile.value) return {};
  const slot = filmstripThumbSizePx.value + FILMSTRIP_GAP;
  const offset = Math.max(0, filmstripCanvasData.value.topBufferSlots) * slot;
  return {
    transform: `translateY(-${offset}px)`,
  };
});

function toggleFaceBbox() {
  showFaceBbox.value = !showFaceBbox.value;
}

const drawMode = ref(null);
const drawState = ref({
  active: false,
  startX: 0,
  startY: 0,
  currentX: 0,
  currentY: 0,
});
const drawSubmitInFlight = ref(false);

const drawModeLabel = computed(() => {
  if (drawMode.value === "face") return "face";
  return "";
});

function beginDrawMode(mode) {
  if (!mode) return;
  showFaceBbox.value = true;
  drawMode.value = mode;
  drawState.value = {
    active: false,
    startX: 0,
    startY: 0,
    currentX: 0,
    currentY: 0,
  };
}

function clearDrawMode() {
  drawMode.value = null;
  drawSubmitInFlight.value = false;
  drawState.value = {
    active: false,
    startX: 0,
    startY: 0,
    currentX: 0,
    currentY: 0,
  };
}

const imgRef = ref(null);
const videoRef = ref(null);
const mediaInnerRef = ref(null);
const videoMeta = ref({ duration: null });
const videoError = ref(null); // set to MediaError when browser can't play the video
// Derive the video src from id + format only — deliberately excludes pixel_sha
// so the URL stays stable across metadata merges (which would otherwise abort
// the in-progress download). Returns '' while format is not yet known, which
// also keeps the <video> element from being created prematurely via v-if.
const videoSrc = computed(() => {
  const id = image.value?.id;
  const fmt = image.value?.format;
  if (!id || !fmt || !isSupportedVideoFile(`file.${fmt}`)) return "";
  return appendShareToken(
    `${backendUrl.value}/pictures/${id}.${fmt.toLowerCase()}`,
  );
});
const overlayDims = ref({
  width: 1,
  height: 1,
  naturalWidth: 1,
  naturalHeight: 1,
  offsetX: 0,
  offsetY: 0,
});
const overlayReady = computed(() => {
  const dims = overlayDims.value;
  return (
    Number.isFinite(dims.width) &&
    Number.isFinite(dims.height) &&
    Number.isFinite(dims.naturalWidth) &&
    Number.isFinite(dims.naturalHeight) &&
    dims.width > 1 &&
    dims.height > 1 &&
    dims.naturalWidth > 1 &&
    dims.naturalHeight > 1
  );
});

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function getDrawPoint(event) {
  if (!overlayReady.value) return null;
  const innerEl = mediaInnerRef.value;
  if (!innerEl) return null;
  const rect = innerEl.getBoundingClientRect();
  const dims = overlayDims.value;
  const localX = event.clientX - rect.left - (dims.offsetX || 0);
  const localY = event.clientY - rect.top - (dims.offsetY || 0);
  const clampedX = clamp(localX, 0, dims.width);
  const clampedY = clamp(localY, 0, dims.height);
  const imgX = (clampedX * dims.naturalWidth) / dims.width;
  const imgY = (clampedY * dims.naturalHeight) / dims.height;
  return {
    x: clamp(imgX, 0, dims.naturalWidth),
    y: clamp(imgY, 0, dims.naturalHeight),
  };
}

const drawRectStyle = computed(() => {
  if (!drawMode.value) return null;
  const state = drawState.value;
  if (!state.active) return null;
  const dims = overlayDims.value;
  const x1 = Math.min(state.startX, state.currentX);
  const y1 = Math.min(state.startY, state.currentY);
  const x2 = Math.max(state.startX, state.currentX);
  const y2 = Math.max(state.startY, state.currentY);
  const left = (dims.offsetX || 0) + (x1 * dims.width) / dims.naturalWidth;
  const top = (dims.offsetY || 0) + (y1 * dims.height) / dims.naturalHeight;
  const width = ((x2 - x1) * dims.width) / dims.naturalWidth;
  const height = ((y2 - y1) * dims.height) / dims.naturalHeight;
  return {
    left: `${left || 0}px`,
    top: `${top || 0}px`,
    width: `${width || 0}px`,
    height: `${height || 0}px`,
  };
});

function onDrawStart(event) {
  if (!drawMode.value) return;
  const point = getDrawPoint(event);
  if (!point) return;
  drawState.value = {
    active: true,
    startX: point.x,
    startY: point.y,
    currentX: point.x,
    currentY: point.y,
  };
  if (event.currentTarget?.setPointerCapture) {
    event.currentTarget.setPointerCapture(event.pointerId);
  }
}

function onDrawMove(event) {
  if (!drawMode.value || !drawState.value.active) return;
  const point = getDrawPoint(event);
  if (!point) return;
  drawState.value = {
    ...drawState.value,
    currentX: point.x,
    currentY: point.y,
  };
}

async function onDrawEnd(event) {
  if (!drawMode.value || !drawState.value.active) return;
  if (drawSubmitInFlight.value) return;
  drawSubmitInFlight.value = true;
  const state = drawState.value;
  const x1 = Math.min(state.startX, state.currentX);
  const y1 = Math.min(state.startY, state.currentY);
  const x2 = Math.max(state.startX, state.currentX);
  const y2 = Math.max(state.startY, state.currentY);
  drawState.value = { ...drawState.value, active: false };
  try {
    event?.currentTarget?.releasePointerCapture(event.pointerId);
  } catch (_) {
    /* pointer already released */
  }
  if (Math.abs(x2 - x1) < 5 || Math.abs(y2 - y1) < 5) {
    clearDrawMode();
    return;
  }
  if (!image.value?.id || !backendUrl.value) {
    clearDrawMode();
    return;
  }
  const capturedImageId = image.value.id;
  const capturedBackendUrl = backendUrl.value;
  const payload = { bbox: [x1, y1, x2, y2], frame_index: 0 };
  try {
    if (drawMode.value === "face") {
      await apiClient.post(
        `${capturedBackendUrl}/pictures/${capturedImageId}/face`,
        payload,
      );
      await fetchFaceBboxes(capturedImageId);
      emit("overlay-change", {
        imageId: capturedImageId,
        fields: { faces: true },
      });
    }
  } catch (e) {
    alert(`Failed to create ${drawModeLabel.value} box: ${e?.message || e}`);
  } finally {
    clearDrawMode();
  }
}

function onDrawCancel(event) {
  try {
    event?.currentTarget?.releasePointerCapture(event.pointerId);
  } catch (_) {
    /* pointer already released */
  }
  clearDrawMode();
}
let overlayResizeObserver = null;
let mediaResizeObserver = null;

function scheduleOverlayDimsUpdate() {
  nextTick(() => {
    requestAnimationFrame(() => {
      requestAnimationFrame(updateOverlayDims);
    });
  });
}

function updateOverlayDims() {
  if (imgRef.value) {
    const rect = imgRef.value.getBoundingClientRect();
    const width = imgRef.value.clientWidth || rect.width || 1;
    const height = imgRef.value.clientHeight || rect.height || 1;
    overlayDims.value.width = width;
    overlayDims.value.height = height;
    overlayDims.value.naturalWidth = imgRef.value.naturalWidth;
    overlayDims.value.naturalHeight = imgRef.value.naturalHeight;
    overlayDims.value.offsetX = imgRef.value.offsetLeft || 0;
    overlayDims.value.offsetY = imgRef.value.offsetTop || 0;
  } else if (videoRef.value) {
    const rect = videoRef.value.getBoundingClientRect();
    const width = videoRef.value.clientWidth || rect.width || 1;
    const height = videoRef.value.clientHeight || rect.height || 1;
    overlayDims.value.width = width;
    overlayDims.value.height = height;
    overlayDims.value.naturalWidth = videoRef.value.videoWidth;
    overlayDims.value.naturalHeight = videoRef.value.videoHeight;
    overlayDims.value.offsetX = videoRef.value.offsetLeft || 0;
    overlayDims.value.offsetY = videoRef.value.offsetTop || 0;
    const duration = Number.isFinite(videoRef.value.duration)
      ? videoRef.value.duration
      : null;
    videoMeta.value = { duration };
  } else {
    videoMeta.value = { duration: null };
  }
}

watch(image, () => scheduleOverlayDimsUpdate());

function handleVideoError(event) {
  const err = event.target?.error;
  const code = err?.code ?? -1;
  const msg = err?.message ?? String(event);
  // MEDIA_ERR_SRC_NOT_SUPPORTED (code 4) is the most common — browser doesn't
  // support the container/codec (e.g. Firefox + .mov/ProRes).
  console.warn(`Video load error (code ${code}): ${msg}`);
  videoError.value = err ?? { code, message: msg };
}

onMounted(() => {
  updateViewportMetrics();
  window.addEventListener("resize", updateViewportMetrics);
  window.addEventListener("keydown", handleKeydown);
  window.addEventListener("pointerdown", handleOverlayPointerDown, true);
  if (typeof ResizeObserver !== "undefined" && overlayMainRef.value) {
    overlayResizeObserver = new ResizeObserver(() => {
      scheduleOverlayDimsUpdate();
    });
    overlayResizeObserver.observe(overlayMainRef.value);
  }
  if (typeof ResizeObserver !== "undefined" && mediaInnerRef.value) {
    mediaResizeObserver = new ResizeObserver(() => {
      scheduleOverlayDimsUpdate();
    });
    mediaResizeObserver.observe(mediaInnerRef.value);
  }
});
onUnmounted(() => {
  window.removeEventListener("resize", updateViewportMetrics);
  window.removeEventListener("keydown", handleKeydown);
  window.removeEventListener("pointerdown", handleOverlayPointerDown, true);
  if (overlayResizeObserver) {
    overlayResizeObserver.disconnect();
    overlayResizeObserver = null;
  }
  if (mediaResizeObserver) {
    mediaResizeObserver.disconnect();
    mediaResizeObserver = null;
  }
  if (swipeHintTimer) {
    clearTimeout(swipeHintTimer);
    swipeHintTimer = null;
  }
  resetOverlayCopyState();
  clearCharacterThumbnails();
});

watch(open, (isOpen) => {
  if (!isOpen) {
    zoomWheelAccumulator = 0;
    swipeHintVisible.value = false;
    if (swipeHintTimer) {
      clearTimeout(swipeHintTimer);
      swipeHintTimer = null;
    }
    return;
  }
  showSwipeHint();
  handleUserActivity();
});

function onTouchStart(event) {
  if (!isMobile.value) return;
  const touch = event.touches?.[0];
  if (!touch) return;
  touchStart.value = {
    x: touch.clientX,
    y: touch.clientY,
    time: Date.now(),
  };
  touchLatest.value = { x: touch.clientX, y: touch.clientY };
}

function onTouchMove(event) {
  if (!isMobile.value) return;
  const touch = event.touches?.[0];
  if (!touch) return;
  touchLatest.value = { x: touch.clientX, y: touch.clientY };
  handleUserActivity();
}

function onTouchEnd(event) {
  if (!isMobile.value) return;
  const dx = touchLatest.value.x - touchStart.value.x;
  const dy = touchLatest.value.y - touchStart.value.y;
  const absX = Math.abs(dx);
  const absY = Math.abs(dy);
  const elapsed = Date.now() - touchStart.value.time;
  const swipeThreshold = 50;
  const maxVertical = 80;
  const maxTime = 600;
  const tapThreshold = 10;

  if (absX >= swipeThreshold && absY <= maxVertical && elapsed <= maxTime) {
    if (dx > 0) {
      showPrevImage();
    } else {
      showNextImage();
    }
    return;
  }

  // Tap: toggle chrome visibility
  if (absX < tapThreshold && absY < tapThreshold) {
    lastTouchEndTime = Date.now();
    touchTapConsumed = true;
    const target = event?.changedTouches?.[0]
      ? document.elementFromPoint(
          event.changedTouches[0].clientX,
          event.changedTouches[0].clientY,
        )
      : null;
    const interactiveSelector =
      "button, a, input, select, textarea, label, summary, details";
    const interactiveContainerSelector =
      ".overlay-sidebar, .overlay-rail, .overlay-nav";
    if (
      target &&
      (target.closest(interactiveSelector) ||
        target.closest(interactiveContainerSelector))
    ) {
      handleUserActivity();
      return;
    }
    if (chromeHidden.value) {
      handleUserActivity();
    } else {
      chromeHidden.value = true;
    }
  }
}

const faceBboxes = ref([]);
const characters = ref([]);
const charactersLoading = ref(false);
const characterThumbnails = ref({});
let characterThumbnailEpoch = 0;
const FACE_THUMB_BASE = 34;
const FACE_THUMB_MIN = 28;
const FACE_THUMB_MAX = 60;
let metadataRequestId = 0;
let faceBboxesRequestId = 0;
let comfyWorkflowRequestId = 0;

function dedupeDetections(items) {
  if (!Array.isArray(items)) return [];
  const seen = new Set();
  const result = [];
  for (const item of items) {
    const id = item?.id;
    const bbox = Array.isArray(item?.bbox) ? item.bbox.join(",") : "";
    const frame = item?.frame_index ?? "";
    const key = id != null ? `id:${id}` : `bbox:${frame}:${bbox}`;
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(item);
  }
  return result;
}

async function fetchComfyWorkflow(imageId) {
  if (!imageId || !backendUrl.value) return;
  const requestId = (comfyWorkflowRequestId += 1);
  const requestedImageId = imageId;
  try {
    const res = await apiClient.get(
      `${backendUrl.value}/comfyui/pictures/${imageId}/workflow`,
    );
    if (comfyWorkflowRequestId !== requestId) return;
    if (!image.value || image.value.id !== requestedImageId) return;
    const data = res.data;
    comfyMetadata.value = data
      ? {
          workflow: data.workflow,
          isApiFormat: data.is_api_format,
          summary: data.summary,
          positive_prompt: data.positive_prompt || null,
          models: data.models || [],
          loras: data.loras || [],
        }
      : null;
  } catch (e) {
    // 404 is expected when no ComfyUI workflow is embedded
    if (e?.response?.status !== 404) {
      console.error("Failed to fetch ComfyUI workflow:", e);
    }
    if (comfyWorkflowRequestId !== requestId) return;
    comfyMetadata.value = null;
  }
}

async function fetchOverlayMetadata(imageId) {
  if (!imageId || !backendUrl.value) return;
  const requestId = (metadataRequestId += 1);
  try {
    const res = await apiClient.get(
      `${backendUrl.value}/pictures/${imageId}/metadata?smart_score=true`,
    );
    if (metadataRequestId !== requestId) return;
    if (!image.value || image.value.id !== imageId) return;
    const data = res.data;
    if (!data || Array.isArray(data)) return;
    const merged = { ...data, ...image.value };
    const existingSmartScore =
      typeof image.value?.smartScore === "number"
        ? image.value.smartScore
        : typeof image.value?.smart_score === "number"
          ? image.value.smart_score
          : null;
    if (Object.prototype.hasOwnProperty.call(data, "smartScore")) {
      // Keep Smart Score consistent with the grid/list payload when already
      // present there; metadata endpoint currently computes a different
      // single-candidate value for some images.
      merged.smartScore =
        existingSmartScore !== null ? existingSmartScore : data.smartScore;
    }
    const dataTags = getTagList(data.tags);
    if (data.tags !== undefined) {
      merged.tags = dedupeTagList(dataTags);
    }
    if (
      image.value?.description == null ||
      image.value.description.startsWith("__description::")
    ) {
      merged.description = data.description ?? null;
    }
    const currentMeta = image.value?.metadata;
    const dataMeta = data.metadata ?? {};
    if (currentMeta == null || Object.keys(currentMeta).length === 0) {
      merged.metadata = dataMeta;
    } else if (Object.keys(dataMeta).length) {
      merged.metadata = { ...currentMeta, ...dataMeta };
    }
    image.value = merged;
    void ensureOverlayFilmstripForImage();
    void tagsPanelRef.value?.refetchPredictions(imageId);
  } catch (e) {
    console.error("Failed to fetch overlay metadata:", e);
  }
}

async function fetchFaceBboxes(imageId) {
  if (!imageId || !backendUrl.value) {
    faceBboxes.value = [];
    return;
  }
  const requestId = (faceBboxesRequestId += 1);
  const requestedImageId = imageId;
  try {
    const res = await apiClient.get(
      `${backendUrl.value}/pictures/${imageId}/faces`,
    );
    const faces = res.data;
    if (faceBboxesRequestId !== requestId) return;
    if (!image.value || image.value.id !== requestedImageId) return;
    const faceArray = Array.isArray(faces) ? faces : faces.faces;
    const firstFrameFaces = dedupeDetections(faceArray).filter(
      (f) =>
        f.frame_index === 0 && Array.isArray(f.bbox) && f.bbox.length === 4,
    );
    if (faceBboxesRequestId !== requestId) return;
    if (!image.value || image.value.id !== requestedImageId) return;
    faceBboxes.value = firstFrameFaces;
    // Fetch character names asynchronously to avoid delaying tag loading
    Promise.all(
      firstFrameFaces.map(async (face) => {
        if (face.character_id) {
          try {
            const res = await apiClient.get(
              `${backendUrl.value}/characters/${face.character_id}/name`,
            );
            const data = res.data;
            face.character_name = data.name || null;
          } catch (e) {
            face.character_name = null;
          }
        } else {
          face.character_name = null;
        }
      }),
    ).then(() => {
      if (faceBboxesRequestId !== requestId) return;
      if (!image.value || image.value.id !== requestedImageId) return;
      faceBboxes.value = [...firstFrameFaces];
    });
  } catch (e) {
    console.error("Error in fetchFaceBboxes:", e);
    faceBboxes.value = [];
  }
}

async function fetchCharacters() {
  if (!backendUrl.value || charactersLoading.value) return;
  charactersLoading.value = true;
  const requestEpoch = (characterThumbnailEpoch += 1);
  try {
    const res = await apiClient.get(`${backendUrl.value}/characters`);
    const data = res.data;
    const list = Array.isArray(data) ? data : [];
    characters.value = list;
    await Promise.all(
      list.map(async (char) => fetchCharacterThumbnail(char?.id, requestEpoch)),
    );
  } catch (e) {
    console.error("Failed to fetch characters:", e);
    characters.value = [];
  } finally {
    if (requestEpoch === characterThumbnailEpoch) {
      charactersLoading.value = false;
    }
  }
}

async function fetchCharacterThumbnail(characterId, requestEpoch) {
  if (!characterId || !backendUrl.value) return;
  try {
    const cacheBuster = Date.now();
    const res = await apiClient.get(
      `${backendUrl.value}/characters/${characterId}/thumbnail?cb=${cacheBuster}`,
      { responseType: "blob" },
    );
    if (requestEpoch !== characterThumbnailEpoch) return;
    const blobUrl = URL.createObjectURL(res.data);
    const existing = characterThumbnails.value[characterId];
    if (existing) {
      URL.revokeObjectURL(existing);
    }
    characterThumbnails.value = {
      ...characterThumbnails.value,
      [characterId]: blobUrl,
    };
  } catch (e) {
    console.error(`Failed to fetch thumbnail for character ${characterId}:`, e);
    if (requestEpoch !== characterThumbnailEpoch) return;
    characterThumbnails.value = {
      ...characterThumbnails.value,
      [characterId]: null,
    };
  }
}

function clearCharacterThumbnails() {
  Object.values(characterThumbnails.value).forEach((url) => {
    if (url) {
      URL.revokeObjectURL(url);
    }
  });
  characterThumbnails.value = {};
}

function getFaceThumbStyle(face, idx) {
  const color = faceBoxColor(idx);
  const base = {
    borderColor: color,
  };
  const bbox = Array.isArray(face?.bbox) ? face.bbox : null;
  const sourceWidth = Number(
    image.value?.width || overlayDims.value.naturalWidth || 0,
  );
  const sourceHeight = Number(
    image.value?.height || overlayDims.value.naturalHeight || 0,
  );
  const sourceUrl = getFullImageUrl(image.value);
  if (!sourceUrl || !bbox || bbox.length !== 4) {
    return {
      ...base,
      width: `${FACE_THUMB_BASE}px`,
      height: `${FACE_THUMB_BASE}px`,
    };
  }
  const [x1, y1, x2, y2] = bbox;
  const faceW = Math.max(1, x2 - x1);
  const faceH = Math.max(1, y2 - y1);
  const imageW = sourceWidth || overlayDims.value.naturalWidth || 1;
  const imageH = sourceHeight || overlayDims.value.naturalHeight || 1;
  const maxDim = Math.max(faceW, faceH);
  const targetMax = Math.min(
    FACE_THUMB_MAX,
    Math.max(FACE_THUMB_MIN, FACE_THUMB_BASE),
  );
  const scale = targetMax / maxDim;
  const targetW = Math.max(1, Math.round(faceW * scale));
  const targetH = Math.max(1, Math.round(faceH * scale));
  const bgWidth = Math.round(imageW * scale);
  const bgHeight = Math.round(imageH * scale);
  const bgPosX = Math.round(-x1 * scale);
  const bgPosY = Math.round(-y1 * scale);
  return {
    ...base,
    width: `${targetW}px`,
    height: `${targetH}px`,
    backgroundImage: `url(${sourceUrl})`,
    backgroundSize: `${bgWidth}px ${bgHeight}px`,
    backgroundPosition: `${bgPosX}px ${bgPosY}px`,
  };
}

async function assignFaceToCharacter(face, character) {
  if (!face?.id || !character?.id || !backendUrl.value) return;
  // Store imageId before the await in case image.value changes or is nulled during the async operation
  const capturedImageId = image.value?.id ?? null;
  try {
    await apiClient.post(
      `${backendUrl.value}/characters/${character.id}/faces`,
      { face_ids: [face.id] },
    );
    if (Array.isArray(faceBboxes.value)) {
      faceBboxes.value = faceBboxes.value.map((entry) => {
        if (entry?.id === face.id) {
          return {
            ...entry,
            character_id: character.id,
            character_name: character.displayName || character.name || null,
          };
        }
        return entry;
      });
    }
    if (capturedImageId) {
      emit("overlay-change", {
        imageId: capturedImageId,
        fields: { faces: true },
      });
    }
  } catch (e) {
    alert(`Failed to assign character: ${e?.message || e}`);
  }
}

async function unassignFaceCharacter(face) {
  if (!face?.id || !face?.character_id || !backendUrl.value) return;
  // Store imageId before the await in case image.value changes or is nulled during the async operation
  const capturedImageId = image.value?.id ?? null;
  try {
    await apiClient.delete(
      `${backendUrl.value}/characters/${face.character_id}/faces`,
      { data: { face_ids: [face.id] } },
    );
    if (Array.isArray(faceBboxes.value)) {
      faceBboxes.value = faceBboxes.value.map((entry) => {
        if (entry?.id === face.id) {
          return { ...entry, character_id: null, character_name: null };
        }
        return entry;
      });
    }
    if (capturedImageId) {
      emit("overlay-change", {
        imageId: capturedImageId,
        fields: { faces: true },
      });
    }
  } catch (e) {
    alert(`Failed to unassign character: ${e?.message || e}`);
  }
}

function handleFaceAssignChange(face, event) {
  const rawValue = event?.target?.value ?? "";
  const nextId = rawValue === "" ? null : rawValue;
  if (!nextId) {
    unassignFaceCharacter(face);
    return;
  }
  const character = sortedCharacters.value.find(
    (char) => String(char.id) === String(nextId),
  );
  if (character) {
    assignFaceToCharacter(face, character);
  }
}

function hasCharacterOption(face) {
  if (!face?.character_id) return false;
  return sortedCharacters.value.some(
    (char) => String(char.id) === String(face.character_id),
  );
}

// Keep preload Image objects alive so the browser doesn't discard the
// in-flight requests. Replaced on every navigation.
let _preloadImages = [];

function preloadAdjacentImages() {
  const images = filmstripImages.value;
  if (!images.length || !image.value) return;
  const idx = filmstripIndexById.value.get(String(image.value.id)) ?? -1;
  if (idx === -1) return;
  const candidates = [];
  if (idx + 1 < images.length) candidates.push(images[idx + 1]);
  if (idx - 1 >= 0) candidates.push(images[idx - 1]);
  _preloadImages = candidates.flatMap((img) => {
    // Only preload still images — video files are large; let the browser
    // fetch them on demand rather than pre-requesting gigabytes of video.
    if (isSupportedVideoFile(getOverlayFormat(img))) return [];
    const url = appendShareToken(
      buildMediaUrl({ backendUrl: backendUrl.value, image: img }),
    );
    if (!url) return [];
    const probe = new Image();
    probe.src = url;
    return [probe];
  });
}

const comfyMetadata = ref(null);
const facesCollapsed = ref(false);

watch(
  () => image.value?.id,
  (newId) => {
    if (newId) {
      overlayDims.value = {
        width: 1,
        height: 1,
        naturalWidth: 1,
        naturalHeight: 1,
        offsetX: 0,
        offsetY: 0,
      };
      videoError.value = null;
      scheduleOverlayDimsUpdate();
      fetchFaceBboxes(newId);
      fetchOverlayMetadata(newId);
      fetchComfyWorkflow(newId);
      preloadAdjacentImages();
    } else {
      faceBboxes.value = [];
      videoError.value = null;
      comfyMetadata.value = null;
    }
  },
  { immediate: true },
);

watch(
  () => tagUpdate.value,
  (payload) => {
    if (!payload || typeof payload !== "object") return;
    const nextKey = payload.key || 0;
    if (!nextKey || nextKey === lastTagUpdateKey.value) return;
    lastTagUpdateKey.value = nextKey;
    if (!open.value || !image.value?.id) return;
    const pictureIds = Array.isArray(payload.pictureIds)
      ? payload.pictureIds.map((id) => String(id))
      : [];
    const currentId = String(image.value.id);
    if (pictureIds.length && !pictureIds.includes(currentId)) return;
    fetchOverlayMetadata(image.value.id);
  },
);

watch(
  () => descriptionUpdate.value,
  (payload) => {
    if (!payload || typeof payload !== "object") return;
    const nextKey = payload.key || 0;
    if (!nextKey || nextKey === lastDescriptionUpdateKey.value) return;
    lastDescriptionUpdateKey.value = nextKey;
    if (!open.value || !image.value?.id) return;
    const pictureIds = Array.isArray(payload.pictureIds)
      ? payload.pictureIds.map((id) => String(id))
      : [];
    const currentId = String(image.value.id);
    if (pictureIds.length && !pictureIds.includes(currentId)) return;
    fetchOverlayMetadata(image.value.id);
  },
);

const faceAssignItems = computed(() => {
  const faces = Array.isArray(faceBboxes.value) ? faceBboxes.value : [];
  return faces.map((face, idx) => ({
    ...face,
    faceIdx: idx,
    faceKey: face?.id ?? `face-${idx}`,
    label: `Face ${idx + 1}`,
  }));
});

const sortedCharacters = computed(() => {
  const list = Array.isArray(characters.value) ? characters.value : [];
  return [...list]
    .filter((char) => char && typeof char.name === "string")
    .sort((a, b) =>
      a.name.localeCompare(b.name, undefined, { sensitivity: "base" }),
    )
    .map((char) => ({
      ...char,
      displayName: char.name.charAt(0).toUpperCase() + char.name.slice(1),
    }));
});

function isValidOverlayBBox(bbox) {
  return Array.isArray(bbox) && bbox.length === 4;
}

function getOverlayBoxStyle(bbox, color) {
  if (!overlayReady.value || !isValidOverlayBBox(bbox)) {
    return { display: "none" };
  }
  const dims = overlayDims.value;
  const x1 = bbox[0];
  const y1 = bbox[1];
  const x2 = bbox[2];
  const y2 = bbox[3];
  const left = (dims.offsetX || 0) + (x1 * dims.width) / dims.naturalWidth;
  const top = (dims.offsetY || 0) + (y1 * dims.height) / dims.naturalHeight;
  const width = ((x2 - x1) * dims.width) / dims.naturalWidth;
  const height = ((y2 - y1) * dims.height) / dims.naturalHeight;
  return {
    border: `1px solid ${color}`,
    background: `${color}22`,
    left: `${left || 0}px`,
    top: `${top || 0}px`,
    width: `${width || 0}px`,
    height: `${height || 0}px`,
  };
}

function handleDescriptionUpdate(imageId, newDescription) {
  if (image.value && image.value.id === imageId) {
    image.value = { ...image.value, description: newDescription };
  }
  if (Array.isArray(allImages.value)) {
    const idx = allImages.value.findIndex((img) => img && img.id === imageId);
    if (idx !== -1) {
      allImages.value[idx] = {
        ...allImages.value[idx],
        description: newDescription,
      };
    }
  }
  emit("update-description", imageId, newDescription);
}

async function copyOverlayImage() {
  if (!image.value) return;
  const url = getFullImageUrl(image.value);
  let copied = false;
  try {
    if (isSupportedVideoFile(getOverlayFormat(image.value))) {
      copied = await copyVideoFrameToClipboard();
    } else {
      copied = await copyImageElementToClipboard();
    }
    if (!copied) {
      copied = await copyImageByFetch(url);
    }
    if (!copied) {
      await copyTextToClipboard(url);
    }
    overlayCopyState.value = "copied";
    if (overlayCopyResetTimer) {
      clearTimeout(overlayCopyResetTimer);
    }
    overlayCopyResetTimer = window.setTimeout(() => {
      resetOverlayCopyState();
    }, 2000);
  } catch (err) {
    try {
      await copyTextToClipboard(url);
      overlayCopyState.value = "copied";
    } catch (fallbackErr) {
      console.warn("Failed to copy overlay image:", fallbackErr || err);
    }
  }
}

async function copyImageElementToClipboard() {
  const imgEl = imgRef.value;
  if (!imgEl || !imgEl.complete) return false;
  const canvas = document.createElement("canvas");
  canvas.width = imgEl.naturalWidth || imgEl.width;
  canvas.height = imgEl.naturalHeight || imgEl.height;
  if (!canvas.width || !canvas.height) return false;
  const ctx = canvas.getContext("2d");
  if (!ctx) return false;
  try {
    ctx.drawImage(imgEl, 0, 0);
    const blob = await canvasToBlob(canvas, "image/png");
    if (!blob) return false;
    return await copyBlobToClipboard(blob);
  } catch (err) {
    return false;
  }
}

async function copyVideoFrameToClipboard() {
  const videoEl = videoRef.value;
  if (!videoEl || videoEl.readyState < 2) return false;
  const width = videoEl.videoWidth || videoEl.clientWidth;
  const height = videoEl.videoHeight || videoEl.clientHeight;
  if (!width || !height) return false;
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) return false;
  try {
    ctx.drawImage(videoEl, 0, 0, width, height);
    const blob = await canvasToBlob(canvas, "image/png");
    if (!blob) return false;
    return await copyBlobToClipboard(blob);
  } catch (err) {
    return false;
  }
}

async function copyImageByFetch(url) {
  if (!navigator?.clipboard?.write || !window.ClipboardItem) return false;
  try {
    const response = await fetch(url, { credentials: "include" });
    if (!response.ok) return false;
    const blob = await response.blob();
    if (!blob) return false;
    return await copyBlobToClipboard(blob);
  } catch (err) {
    return false;
  }
}

async function copyBlobToClipboard(blob) {
  if (!navigator?.clipboard?.write || !window.ClipboardItem) return false;
  const mime = blob.type || "image/png";
  const item = new ClipboardItem({ [mime]: blob });
  await navigator.clipboard.write([item]);
  return true;
}

function canvasToBlob(canvas, type) {
  return new Promise((resolve) => {
    if (!canvas?.toBlob) {
      resolve(null);
      return;
    }
    canvas.toBlob(
      (blob) => {
        resolve(blob || null);
      },
      type,
      0.95,
    );
  });
}

async function copyTextToClipboard(text) {
  const ok = await copyText(text);
  if (!ok) throw new Error("Copy failed");
}

let overlayCopyResetTimer = null;
function resetOverlayCopyState() {
  if (overlayCopyResetTimer) {
    clearTimeout(overlayCopyResetTimer);
    overlayCopyResetTimer = null;
  }
  overlayCopyState.value = "idle";
}
</script>

<style scoped>
.image-overlay {
  position: fixed;
  inset: 0;
  background: rgba(var(--v-theme-scrim), 0.92);
  z-index: 1000;
}

.overlay-shell {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  --rail-width: 52px;
  --rail-open-width: 170px;
  --sidebar-width: 0px;
  --topbar-height: 40px;
  position: relative;
}

.overlay-shell.sidebar-open {
  --sidebar-width: 320px;
}

.overlay-topbar {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 4px 10px;
  min-height: var(--topbar-height);
  background: rgba(var(--v-theme-dark-surface), 0.9);
  color: rgb(var(--v-theme-on-dark-surface));
  transition: opacity 0.2s ease;
  z-index: 5;
}

.overlay-topbar.hidden {
  opacity: 0;
  pointer-events: none;
}

.overlay-progress {
  position: absolute;
  right: calc(16px + var(--sidebar-width));
  z-index: 6;
  background: rgba(var(--v-theme-dark-surface), 0.75);
  color: rgb(var(--v-theme-on-dark-surface));
  padding: 8px 10px;
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(var(--v-theme-shadow), 0.25);
  backdrop-filter: blur(6px);
}

.overlay-progress--comfyui {
  bottom: 64px;
  min-width: 180px;
}

.overlay-progress--plugin {
  bottom: 128px;
  min-width: 220px;
}

.overlay-progress--error {
  background: rgba(var(--v-theme-error), 0.95);
}

.overlay-progress-title {
  font-size: 0.8em;
  margin-bottom: 6px;
  white-space: pre-line;
}

.overlay-progress-bar {
  width: 100%;
  height: 6px;
  background: rgba(var(--v-theme-on-dark-surface), 0.2);
  border-radius: 999px;
  overflow: hidden;
}

.overlay-progress-fill {
  height: 100%;
  background: rgb(var(--v-theme-accent));
  width: 0;
  transition: width 0.2s ease;
}

.overlay-progress-meta {
  margin-top: 6px;
  font-size: 0.78em;
  opacity: 0.85;
}

.overlay-close {
  border: none;
  background: rgba(var(--v-theme-primary), 0.7);
  color: rgb(var(--v-theme-on-primary));
  padding: 6px 14px;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  cursor: pointer;
  font-size: 1em;
  font-weight: 600;
}
.overlay-close:hover {
  background: rgba(var(--v-theme-accent), 0.85);
}

.overlay-title {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  flex: 1;
}

.overlay-desc-teaser {
  border: none;
  background: transparent;
  color: rgba(var(--v-theme-on-dark-surface), 0.7);
  text-align: left;
  font-size: 0.9rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  cursor: pointer;
  padding: 0;
}

.overlay-desc-teaser:disabled {
  cursor: default;
  opacity: 0.5;
}

.overlay-top-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.overlay-top-actions .star-overlay {
  position: static;
  top: auto;
  right: auto;
  z-index: auto;
  padding: 6px 14px;
  height: 32px;
  background: none;
  border-radius: 4px;
}

.star-overlay:hover {
  background: rgba(var(--v-theme-primary), 0.6);
}

.overlay-icon-btn {
  border: none;
  background: none;
  color: rgb(var(--v-theme-on-dark-surface));
  height: 32px;
  padding: 6px 14px;
  min-width: 32px;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  font-size: 1em;
}
.overlay-icon-btn:hover {
  background: rgba(var(--v-theme-primary), 0.6);
}

.overlay-icon-btn--active {
  background: rgba(var(--v-theme-primary), 0.25);
  color: rgb(var(--v-theme-primary));
}

.overlay-star-mobile-btn {
  display: flex;
  align-items: center;
  gap: 4px;
}

.overlay-star-mobile-label {
  font-size: 0.85rem;
  font-weight: 700;
  color: rgb(var(--v-theme-on-dark-surface));
  min-width: 10px;
}

.overlay-star-menu {
  background: rgba(var(--v-theme-dark-surface), 0.97);
  border-radius: 10px;
  padding: 4px;
  display: flex;
  flex-direction: column;
  min-width: 160px;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.5);
}

.overlay-star-menu-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border: none;
  background: transparent;
  border-radius: 6px;
  cursor: pointer;
  color: rgb(var(--v-theme-on-dark-surface));
  width: 100%;
  text-align: left;
}

.overlay-star-menu-item:hover {
  background: rgba(255, 255, 255, 0.08);
}

.overlay-star-menu-item--active {
  background: rgba(var(--v-theme-accent), 0.15);
}

.overlay-star-menu-stars {
  display: flex;
  gap: 2px;
}

.overlay-star-menu-label {
  font-size: 0.82rem;
  color: rgba(var(--v-theme-on-dark-surface), 0.8);
}

.overlay-comfy-activator {
  gap: 6px;
}

.overlay-comfy-activator-label {
  font-size: 0.78rem;
  font-weight: 600;
}

.overlay-comfy-panel {
  padding: 12px;
  min-width: 420px;
  background: rgba(var(--v-theme-dark-surface), 0.96);
  color: rgb(var(--v-theme-on-dark-surface));
  border-radius: 10px;
  box-shadow: 2px 2px 12px rgba(0, 0, 0, 0.4);
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.overlay-comfy-header {
  font-size: 0.85rem;
  font-weight: 600;
}

.overlay-comfy-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.overlay-comfy-field-label {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}

.overlay-comfy-select,
.overlay-comfy-textarea {
  width: 100%;
  background: rgba(var(--v-theme-shadow), 0.45);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  color: rgb(var(--v-theme-on-dark-surface));
  border-radius: 8px;
  padding: 6px 8px;
  font-size: 0.8rem;
}

.overlay-comfy-textarea-wrap {
  position: relative;
}

.overlay-comfy-help {
  position: absolute;
  left: 12px;
  top: 10px;
  font-size: 0.78rem;
  color: rgba(var(--v-theme-on-dark-surface), 0.5);
  pointer-events: none;
}

.overlay-comfy-textarea {
  resize: vertical;
  min-height: 220px;
}

.overlay-comfy-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.overlay-comfy-run {
  border: none;
  background: rgba(var(--v-theme-primary), 0.8);
  color: rgb(var(--v-theme-on-primary));
  padding: 6px 12px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  font-size: 0.8rem;
  font-weight: 600;
}

.overlay-comfy-run:disabled {
  opacity: 0.6;
  cursor: default;
}

.overlay-comfy-warning,
.overlay-comfy-error,
.overlay-comfy-success {
  border-radius: 8px;
  padding: 6px 8px;
  font-size: 0.78rem;
}

.overlay-comfy-warning {
  background: rgba(var(--v-theme-warning), 0.2);
  color: rgb(var(--v-theme-on-warning));
}

.overlay-comfy-note {
  font-size: 0.74rem;
  color: rgba(var(--v-theme-on-dark-surface), 0.65);
}

.overlay-comfy-status {
  font-size: 0.8rem;
  color: rgba(var(--v-theme-on-dark-surface), 0.75);
}

.overlay-comfy-error {
  background: rgba(var(--v-theme-error), 0.2);
  color: rgb(var(--v-theme-on-error));
}

.overlay-comfy-success {
  background: rgba(var(--v-theme-primary), 0.18);
  color: rgb(var(--v-theme-on-dark-surface));
}

.zoom-btn {
  width: auto;
  min-width: 84px;
  padding: 6px 14px;
  gap: 6px;
  justify-content: flex-start;
}

.zoom-btn .v-icon {
  flex: 0 0 18px;
}

.overlay-main {
  flex: 1;
  display: grid;
  grid-template-columns: 1fr;
  height: 100%;
  min-height: 0;
  position: relative;
}

.overlay-canvas {
  position: relative;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  min-height: 0;
  user-select: none;
}

.overlay-media {
  position: relative;
  width: 100%;
  height: 100%;
  max-width: 100%;
  max-height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  transform-origin: center;
  transition: transform 0.15s ease;
  cursor: grab;
}

.overlay-media-inner {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  max-width: 100%;
  max-height: 100%;
  z-index: 1;
}

.overlay-media.panning {
  transition: none;
  cursor: grabbing;
}

.overlay-img,
.overlay-video {
  max-width: 100%;
  max-height: 100%;
  width: auto;
  height: auto;
  object-fit: contain;
  background: rgb(var(--v-theme-dark-surface));
  box-shadow: 0 12px 30px rgba(var(--v-theme-shadow), 0.45);
  position: relative;
  z-index: 1;
}

.overlay-img {
  border-radius: 0;
}

.overlay-video {
  border-radius: 12px;
}

.overlay-video-error {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  padding: 40px;
  border-radius: 12px;
  background: rgba(var(--v-theme-dark-surface), 0.8);
  color: rgb(var(--v-theme-on-dark-surface));
  text-align: center;
  min-width: 280px;
}

.overlay-video-error-msg {
  margin: 0;
  font-size: 0.9rem;
  opacity: 0.75;
  max-width: 300px;
}

.overlay-video-download-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 20px;
  border-radius: 6px;
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
  text-decoration: none;
  font-size: 0.875rem;
  font-weight: 500;
}

.overlay-video-download-btn:hover {
  opacity: 0.85;
}

.overlay-nav {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  width: 44px;
  height: 44px;
  border-radius: 999px;
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.2);
  background: rgba(var(--v-theme-shadow), 0.35);
  color: rgb(var(--v-theme-on-dark-surface));
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: opacity 0.2s ease;
  z-index: 4;
}

.overlay-nav.hidden {
  opacity: 0;
  pointer-events: none;
}

.overlay-nav-left {
  left: calc(16px + var(--filmstrip-rail-width));
}

.overlay-nav-right {
  right: calc(16px + var(--sidebar-width));
}

.zoom-hud {
  position: absolute;
  bottom: 16px;
  left: calc(16px + var(--filmstrip-rail-width, 0px));
  padding: 4px 10px;
  border-radius: 999px;
  background: rgba(var(--v-theme-shadow), 0.55);
  color: rgb(var(--v-theme-on-dark-surface));
  font-size: 0.75rem;
  transition:
    opacity 0.2s ease,
    left 0.2s ease;
  z-index: 4;
}

.zoom-hud.hidden {
  opacity: 0;
  pointer-events: none;
}

.overlay-swipe-hint {
  position: absolute;
  bottom: 16px;
  left: 50%;
  transform: translateX(-50%);
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  background: rgba(var(--v-theme-shadow), 0.55);
  color: rgb(var(--v-theme-on-dark-surface));
  border-radius: 999px;
  font-size: 0.85rem;
  z-index: 4;
}
.overlay-chrome-hint {
  position: absolute;
  bottom: 12px;
  left: 50%;
  transform: translateX(-50%);
  padding: 3px 10px;
  background: rgba(0, 0, 0, 0.35);
  color: rgba(255, 255, 255, 0.55);
  border-radius: 999px;
  font-size: 0.72rem;
  pointer-events: none;
  z-index: 4;
  white-space: nowrap;
}
.overlay-chrome-hint kbd {
  font-family: inherit;
  font-size: inherit;
  background: rgba(255, 255, 255, 0.15);
  border-radius: 3px;
  padding: 0 4px;
}

.overlay-sidebar {
  position: absolute;
  top: var(--topbar-height);
  right: 0;
  bottom: 0;
  width: var(--sidebar-width);
  background: rgba(var(--v-theme-dark-surface), 0.6);
  color: rgb(var(--v-theme-on-dark-surface));
  transition: width 0.2s ease;
  overflow: hidden;
  padding: 0;
  height: calc(100% - var(--topbar-height));
  z-index: 4;
}

.overlay-sidebar.open {
  width: 320px;
  padding: 10px 12px 44px;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.overlay-sidebar.hidden {
  opacity: 0;
  pointer-events: none;
}

.sidebar-section {
  margin-bottom: 6px;
}

.sidebar-section--description {
  flex: 1 1 114px;
  display: flex;
  flex-direction: column;
  min-height: 114px;
  overflow: visible;
}

.sidebar-section--description.sidebar-section--collapsed {
  flex: 0 0 auto;
  min-height: 0;
  overflow: hidden;
}

.sidebar-section--tags {
  min-height: 104px;
  display: flex;
  flex-direction: column;
}

.sidebar-section--tags.sidebar-section--collapsed {
  min-height: 0;
}

.section-header--collapsible {
  cursor: pointer;
  user-select: none;
}

.section-header--collapsible:hover {
  opacity: 0.85;
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 0.78rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  margin-bottom: 4px;
  padding: 2px 0;
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}

.rejected-threshold-label {
  margin-left: 6px;
  font-size: 0.64rem;
  opacity: 0.5;
  font-weight: 400;
  letter-spacing: 0.01em;
}

.section-header-title-with-meta {
  display: inline-flex;
  align-items: baseline;
}

.section-meta {
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}

.description-editor {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.description-editor textarea {
  flex: 1;
  width: 100%;
  min-height: 56px;
  border-radius: 8px;
  font-size: 0.85rem;
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.2);
  background: rgba(var(--v-theme-shadow), 0.35);
  color: rgb(var(--v-theme-on-dark-surface));
  padding: 6px;
  resize: vertical;
}

.description-actions {
  margin-top: 6px;
  display: flex;
  gap: 8px;
}

.face-assign-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 4px;
  margin-top: 4px;
  max-height: 210px;
  overflow-y: auto;
  padding-right: 2px;
}

.face-assign-card {
  background: rgba(var(--v-theme-on-dark-surface), 0.06);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.12);
  border-radius: 6px;
  padding: 3px;
}

.face-assign-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.face-assign-thumb {
  border-radius: 2px;
  flex: 0 0 auto;
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
}

.face-assign-crop {
  border-radius: 2px;
  border: 1px solid transparent;
  background-repeat: no-repeat;
  background-position: center;
  background-size: cover;
  margin: 0 auto;
}

.face-assign-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}

.face-assign-label {
  font-size: 0.78rem;
  color: rgba(var(--v-theme-on-dark-surface), 0.9);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.face-assign-select {
  width: 100%;
  background: rgba(var(--v-theme-shadow), 0.45);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.15);
  color: rgb(var(--v-theme-on-dark-surface));
  border-radius: 8px;
  padding: 1px 4px;
  font-size: 0.75rem;
  height: 22px;
}

.face-assign-select:disabled {
  opacity: 0.6;
}

.face-assign-empty {
  font-size: 0.8rem;
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
  padding: 4px 6px;
}

.metadata-empty {
  font-size: 0.85rem;
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}

.metadata-tabbox {
  display: flex;
  flex-direction: column;
  border-radius: 10px;
  background: rgba(var(--v-theme-on-dark-surface), 0.06);
  overflow: hidden;
}

.metadata-tab-strip {
  display: flex;
  border-bottom: 1px solid rgba(var(--v-theme-on-dark-surface), 0.1);
}

.metadata-tab-btn {
  flex: 1;
  padding: 6px 8px;
  font-size: 0.78rem;
  font-weight: 600;
  border: none;
  background: transparent;
  color: rgba(var(--v-theme-on-dark-surface), 0.5);
  cursor: pointer;
  transition:
    color 0.15s,
    background 0.15s;
  text-align: center;
  white-space: nowrap;
}

.metadata-tab-btn:first-child {
  border-radius: 10px 0 0 0;
}

.metadata-tab-btn:last-child {
  border-radius: 0 10px 0 0;
}

.metadata-tab-btn:only-child {
  border-radius: 10px 10px 0 0;
}

.metadata-tab-btn.active {
  color: rgb(var(--v-theme-on-dark-surface));
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
}

.metadata-tab-btn:hover:not(.active) {
  color: rgba(var(--v-theme-on-dark-surface), 0.8);
  background: rgba(var(--v-theme-on-dark-surface), 0.04);
}

.metadata-tab-panel {
  padding: 10px;
}

.metadata-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.metadata-info-card,
.metadata-comfy-card {
  display: flex;
  flex-direction: column;
  padding: 10px;
  border-radius: 10px;
  background: rgba(var(--v-theme-on-dark-surface), 0.06);
}

.metadata-info-card {
  gap: 8px;
}

.metadata-info-header {
  font-size: 0.84rem;
  font-weight: 600;
  color: rgba(var(--v-theme-on-dark-surface), 0.85);
}

.metadata-info-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 12px;
}

.metadata-info-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.metadata-info-item--full-width {
  grid-column: 1 / -1;
}

.metadata-info-item--clickable {
  cursor: pointer;
}

.metadata-info-item--clickable:hover .metadata-info-value {
  text-decoration: underline;
}

.metadata-info-label {
  font-size: 0.7rem;
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}

.metadata-info-value {
  font-size: 0.8rem;
  color: rgb(var(--v-theme-on-dark-surface));
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.metadata-comfy-card {
  gap: 10px;
}

.metadata-comfy-header {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-weight: 600;
  font-size: 0.74rem;
  color: rgba(var(--v-theme-on-dark-surface), 0.85);
}

.metadata-comfy-subtitle {
  font-size: 0.78rem;
  font-weight: 500;
  color: rgba(var(--v-theme-on-dark-surface), 0.65);
}

.metadata-comfy-details {
  background: rgba(var(--v-theme-shadow), 0.25);
  border-radius: 8px;
  padding: 8px 10px;
}

.metadata-comfy-details summary {
  cursor: pointer;
  font-size: 0.78rem;
  color: rgba(var(--v-theme-on-dark-surface), 0.75);
}

.metadata-comfy-details summary::-webkit-details-marker {
  display: none;
}

.metadata-comfy-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.metadata-comfy-summary-left {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.metadata-comfy-workflow-action {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  border: none;
  background: transparent;
  color: rgba(var(--v-theme-on-dark-surface), 0.75);
  font-size: 0.72rem;
  padding: 2px 2px;
  border-radius: 4px;
  cursor: pointer;
}

.metadata-comfy-workflow-action:hover {
  background: rgba(var(--v-theme-on-dark-surface), 0.12);
  color: rgb(var(--v-theme-on-dark-surface));
}

.metadata-comfy-textarea {
  width: 100%;
  max-width: 100%;
  box-sizing: border-box;
  min-height: 160px;
  max-height: 280px;
  border-radius: 8px;
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.15);
  background: rgba(var(--v-theme-shadow), 0.35);
  color: rgb(var(--v-theme-on-dark-surface));
  font-size: 0.74rem;
  line-height: 1.4;
  padding: 8px;
  resize: vertical;
  overflow: auto;
  white-space: pre;
  word-break: normal;
}

.metadata-comfy-details:not([open]) .metadata-comfy-textarea {
  display: none;
}

.metadata-comfy-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.metadata-comfy-field-label {
  font-size: 0.7rem;
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}

.metadata-comfy-field-group {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.metadata-comfy-prompt-block {
  display: flex;
  flex-direction: column;
}

.metadata-comfy-prompt {
  min-height: 70px;
  max-height: 160px;
  white-space: pre-wrap;
  word-break: break-word;
}

.metadata-comfy-chips-block {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.metadata-comfy-chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}

.metadata-comfy-chip {
  display: inline-block;
  font-size: 0.7rem;
  background: rgba(var(--v-theme-on-dark-surface), 0.1);
  color: rgba(var(--v-theme-on-dark-surface), 0.85);
  border-radius: 4px;
  padding: 4px 6px;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.face-bbox-empty {
  position: absolute;
  left: 8px;
  top: 8px;
  color: #ff5252;
  background: rgba(255, 255, 255, 0.12);
  z-index: 1001;
  font-size: 0.9em;
  padding: 2px 8px;
  border-radius: 4px;
}

.face-bbox-label {
  position: absolute;
  left: 0;
  top: 0;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  font-size: 0.75rem;
  padding: 1px 4px;
  border-bottom-right-radius: 6px;
}

.face-bbox-overlay {
  box-sizing: border-box;
  position: absolute;
  pointer-events: none;
  z-index: 1000 !important;
}

.overlay-draw-layer {
  position: absolute;
  inset: 0;
  pointer-events: auto;
  z-index: 5000;
  cursor: crosshair;
}

.overlay-draw-rect {
  position: absolute;
  border: 2px dashed rgba(255, 255, 255, 0.8);
  background: rgba(255, 255, 255, 0.12);
  box-sizing: border-box;
  z-index: 2001;
}

.overlay-draw-hint {
  position: absolute;
  left: 50%;
  top: 72px;
  transform: translateX(-50%);
  padding: 8px 14px;
  border-radius: 999px;
  background: rgba(0, 0, 0, 0.85);
  color: #fff;
  font-size: 0.9rem;
  font-weight: 600;
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.35);
  pointer-events: none;
  z-index: 2002;
  display: inline-flex;
  align-items: center;
  gap: 10px;
}

.overlay-draw-cancel {
  pointer-events: auto;
  border: 0;
  background: rgb(var(--v-theme-error));
  color: #fff;
  font-weight: 600;
  font-size: 0.85rem;
  padding: 4px 10px;
  border-radius: 999px;
  cursor: pointer;
  box-shadow: 0 4px 10px rgba(0, 0, 0, 0.25);
}

.overlay-draw-cancel:hover {
  filter: brightness(0.95);
}

@media (max-width: 900px) {
  .overlay-sidebar {
    display: none !important;
  }

  .overlay-topbar-sidebar-toggle {
    display: none !important;
  }

  .overlay-close span {
    display: none;
  }

  .overlay-comfy-activator-label {
    display: none;
  }

  .overlay-title {
    display: none;
  }

  .overlay-character-names {
    display: none;
  }

  :deep(.add-to-set-label) {
    display: none;
  }

  .zoom-btn {
    min-width: 32px;
    width: 32px;
    padding: 6px;
  }
}

@media (max-width: 720px) {
  .overlay-shell.sidebar-open {
    --sidebar-width: 78%;
  }

  .overlay-main {
    grid-template-columns: 1fr;
  }

  .overlay-sidebar {
    position: absolute;
    top: var(--topbar-height);
    right: 0;
    height: calc(100% - var(--topbar-height));
    width: 0;
  }

  .overlay-sidebar.open {
    width: 78%;
  }

  .overlay-nav {
    display: none;
  }
}
</style>
