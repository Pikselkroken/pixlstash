<template>
  <div v-if="open" class="image-overlay" @click.self="handleBackdropClick">
    <div
      class="overlay-shell"
      :class="{ 'chrome-hidden': chromeHidden, 'sidebar-open': sidebarOpen }"
      @mousemove="handleUserActivity"
      @mousedown="handleUserActivity"
      @click="handleOverlayClick"
      @wheel.passive="handleUserActivity"
      @touchstart.passive="handleUserActivity"
    >
      <header
        ref="topbarRef"
        class="overlay-topbar"
        :class="{ hidden: chromeHidden }"
      >
        <button class="overlay-close" @click="emit('close')" aria-label="Close">
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
        <div v-for="(face, idx) in faceBboxes" :key="idx">
          <span
            v-if="face.character_name"
            :style="{ color: faceBoxColor(idx) }"
          >
            {{ face.character_name || "Unknown" }}
          </span>
        </div>
        <div class="overlay-top-actions">
          <AddToSetControl
            v-if="image"
            :key="addToSetControlKey"
            :backend-url="backendUrl"
            :picture-ids="[image.id]"
            :class="{ hidden: chromeHidden }"
            @added="handleOverlayAddToSet"
          />
          <StarRatingOverlay
            v-if="image"
            :class="{ hidden: chromeHidden }"
            :score="image?.score || 0"
            icon-size="large"
            @set-score="setScore"
          />
          <button
            class="overlay-icon-btn"
            type="button"
            title="Toggle face/hand bounding boxes"
            aria-label="Toggle face/hand bounding boxes"
            @click.stop="toggleFaceBbox"
            :class="{ hidden: chromeHidden }"
          >
            <v-icon size="20">mdi-account</v-icon>
          </button>

          <button
            class="overlay-icon-btn zoom-btn"
            type="button"
            title="Toggle zoom"
            aria-label="Toggle zoom"
            @click="toggleZoom"
          >
            <v-icon>mdi-magnify</v-icon>
            <span class="zoom-btn-label">{{ zoomHudLabel }}</span>
          </button>
          <button
            class="overlay-icon-btn"
            type="button"
            title="Toggle sidebar"
            aria-label="Toggle sidebar"
            @click="toggleSidebar"
          >
            <v-icon>{{
              sidebarOpen ? "mdi-dock-right" : "mdi-dock-right"
            }}</v-icon>
          </button>
        </div>
      </header>

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
                <video
                  v-if="isSupportedVideoFile(getOverlayFormat(image))"
                  ref="videoRef"
                  :src="getFullImageUrl(image)"
                  class="overlay-video"
                  controls
                  preload="auto"
                  playsinline
                  :draggable="!isZoomed"
                  @dragstart="handleMediaDragStart"
                  @loadedmetadata="updateOverlayDims"
                ></video>
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
                <div
                  v-if="faceBboxes.length === 0 && handBboxes.length === 0"
                  class="face-bbox-empty"
                >
                  No bboxes found
                </div>
                <div
                  v-for="(face, idx) in faceBboxes"
                  :key="`face-${idx}`"
                  :class="[
                    'face-bbox-overlay',
                    'bbox-drop-target',
                    { 'bbox-drop-active': isDragOver('face', face.id) },
                  ]"
                  :style="getOverlayBoxStyle(face.bbox, faceBoxColor(idx))"
                  @dragover.prevent="handleDragOver('face', face.id)"
                  @dragenter.prevent="handleDragOver('face', face.id)"
                  @dragleave="handleDragLeave('face', face.id)"
                  @drop.prevent="handleDropToFace(face)"
                >
                  <span class="face-bbox-label">
                    {{ face.character_name || `Face ${idx + 1}` }}
                  </span>
                </div>
                <div
                  v-for="(hand, idx) in handBboxes"
                  :key="`hand-${idx}`"
                  :class="[
                    'hand-bbox-overlay',
                    'bbox-drop-target',
                    { 'bbox-drop-active': isDragOver('hand', hand.id) },
                  ]"
                  :style="getOverlayBoxStyle(hand.bbox, handBoxColor(idx))"
                  @dragover.prevent="handleDragOver('hand', hand.id)"
                  @dragenter.prevent="handleDragOver('hand', hand.id)"
                  @dragleave="handleDragLeave('hand', hand.id)"
                  @drop.prevent="handleDropToHand(hand)"
                >
                  <span class="hand-bbox-label">
                    {{ handLabel(hand, idx) }}
                  </span>
                </div>
              </template>
            </div>
          </div>

          <button
            class="overlay-nav overlay-nav-left"
            :class="{ hidden: chromeHidden }"
            @click.stop="showPrevImage"
            aria-label="Previous"
          >
            <v-icon>mdi-chevron-left</v-icon>
          </button>
          <button
            class="overlay-nav overlay-nav-right"
            :class="{ hidden: chromeHidden }"
            @click.stop="showNextImage"
            aria-label="Next"
          >
            <v-icon>mdi-chevron-right</v-icon>
          </button>

          <div class="zoom-hud" :class="{ hidden: chromeHidden }">
            {{ zoomHudLabel }}
          </div>

          <div v-if="swipeHintVisible" class="overlay-swipe-hint">
            <v-icon size="18">mdi-swap-horizontal</v-icon>
            <span>Swipe to navigate</span>
          </div>
        </div>

        <div class="overlay-rail" :class="{ hidden: chromeHidden }">
          <div class="filmstrip-list">
            <button
              v-for="item in filmstripWindow"
              :key="
                item.id
                  ? `filmstrip-${item.id}-${item.index}`
                  : `filmstrip-${item.index}`
              "
              class="filmstrip-thumb"
              :class="{ active: item.isActive }"
              @click.stop="selectImageByIndex(item.index)"
              :title="item.description || 'Image'"
            >
              <img
                v-if="getFilmstripThumbSrc(item)"
                :src="getFilmstripThumbSrc(item)"
                :alt="item.description || 'Thumbnail'"
                loading="lazy"
              />
              <div v-else class="filmstrip-thumb-placeholder">
                <v-icon size="22">
                  {{
                    isSupportedVideoFile(getOverlayFormat(item))
                      ? "mdi-video"
                      : "mdi-image"
                  }}
                </v-icon>
              </div>
            </button>
          </div>
        </div>

        <aside
          class="overlay-sidebar"
          :class="{ open: sidebarOpen, hidden: chromeHidden }"
        >
          <div class="sidebar-section">
            <div class="section-header">
              <span>Description</span>
              <span class="section-meta-group">
                <button
                  class="section-meta-btn"
                  type="button"
                  title="Copy description"
                  :disabled="!canCopyDescription"
                  @click.stop="copyDescription"
                >
                  <v-icon size="16">
                    {{
                      descriptionCopyState === "copied"
                        ? "mdi-check-bold"
                        : "mdi-content-copy"
                    }}
                  </v-icon>
                </button>
                <span class="section-meta">
                  {{ descriptionDraft.length }}
                </span>
              </span>
            </div>
            <div class="description-editor">
              <textarea
                ref="descriptionEditorRef"
                v-model="descriptionDraft"
                :readonly="!isEditingDescription"
                @focus="startEditDescription"
                @click="startEditDescription"
                @keydown.enter.prevent="
                  isEditingDescription && !$event.shiftKey && saveDescription()
                "
                @keydown="handleDescriptionEditorKey"
                @blur="isEditingDescription && cancelEditDescription()"
              ></textarea>
              <div class="description-actions">
                <template v-if="isEditingDescription">
                  <button
                    class="overlay-icon-btn"
                    type="button"
                    title="Save description"
                    :disabled="isSavingDescription"
                    @click.stop="saveDescription"
                  >
                    <v-icon
                      size="18"
                      :class="{ 'mdi-spin': isSavingDescription }"
                    >
                      {{
                        isSavingDescription ? "mdi-loading" : "mdi-content-save"
                      }}
                    </v-icon>
                  </button>
                  <button
                    class="overlay-icon-btn"
                    type="button"
                    title="Cancel editing"
                    :disabled="isSavingDescription"
                    @click.stop="cancelEditDescription"
                  >
                    <v-icon size="18">mdi-close</v-icon>
                  </button>
                </template>
              </div>
            </div>
          </div>

          <div class="sidebar-section">
            <div class="section-header">
              <span>Faces</span>
            </div>
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
                      :disabled="!face.id"
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
                          face.character_id != null && !hasCharacterOption(face)
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
          </div>

          <div class="sidebar-section sidebar-section--tags">
            <div class="section-header">
              <span>Tags</span>
              <span class="section-meta-group">
                <button
                  v-if="image"
                  class="section-meta-btn section-meta-btn--danger"
                  type="button"
                  title="Clear tags (Causes the image to be re-tagged automatically)"
                  @click.stop="clearTagsForImage"
                >
                  <v-icon size="16">mdi-refresh</v-icon>
                </button>
                <button
                  v-if="image"
                  class="section-meta-btn"
                  type="button"
                  title="Add tag"
                  @click.stop="beginAddTag"
                >
                  <v-icon size="16">mdi-plus</v-icon>
                </button>
              </span>
            </div>
            <div class="tag-list">
              <div v-if="tagsRefreshing" class="tag-refresh-indicator">
                <v-progress-circular
                  indeterminate
                  size="16"
                  width="2"
                  color="primary"
                />
              </div>
              <div class="tag-section">
                <div class="tag-section-title">All Image Tags</div>
                <div
                  class="tag-drop-zone"
                  :class="{
                    'tag-drop-zone--active': isDragOver('unassigned', null),
                  }"
                  @dragover.prevent="handleDragOver('unassigned', null)"
                  @dragenter.prevent="handleDragOver('unassigned', null)"
                  @dragleave="handleDragLeave('unassigned', null)"
                  @drop.prevent="handleDropToUnassigned"
                >
                  <span
                    v-for="tag in allImageTags"
                    :key="`unassigned-${tag.id ?? tag.tag}`"
                    :class="[
                      'overlay-tag',
                      { 'overlay-tag--penalized': isPenalizedTag(tag) },
                    ]"
                    draggable="true"
                    @dragstart="
                      startTagDrag(tagLabel(tag), 'unassigned', null, $event)
                    "
                    @dragend="clearTagDrag"
                  >
                    {{ tagLabel(tag) }}
                    <button
                      class="tag-delete-btn"
                      @click.stop="removeAllTag(tag)"
                      title="Remove tag"
                    >
                      <v-icon size="12">mdi-close</v-icon>
                    </button>
                  </span>
                  <div v-if="!allImageTags.length" class="tag-drop-placeholder">
                    Drop tags here
                  </div>
                  <input
                    v-if="addingTag"
                    ref="tagInputRef"
                    v-model="newTag"
                    @keydown.enter.prevent="confirmAddTag"
                    @keydown="handleTagBackspace"
                    @blur="cancelAddTag"
                    class="tag-add-input"
                    placeholder="New tag"
                  />
                </div>
              </div>

              <div
                v-for="group in faceTagGroups"
                :key="group.faceKey"
                class="tag-section"
              >
                <div
                  class="tag-section-title tag-section-title-row"
                  :style="{ color: group.color }"
                >
                  <span>{{ group.label }}</span>
                  <button
                    class="tag-section-action"
                    title="Remove face"
                    @click.stop="removeFaceDetection(group.face)"
                  >
                    <v-icon size="14">mdi-delete</v-icon>
                  </button>
                </div>
                <div
                  class="tag-drop-zone"
                  :class="{
                    'tag-drop-zone--active': isDragOver('face', group.face.id),
                  }"
                  @dragover.prevent="handleDragOver('face', group.face.id)"
                  @dragenter.prevent="handleDragOver('face', group.face.id)"
                  @dragleave="handleDragLeave('face', group.face.id)"
                  @drop.prevent="handleDropToFace(group.face)"
                >
                  <span
                    v-for="tag in group.tags"
                    :key="`face-${group.faceKey}-${tag.id ?? tag.tag}`"
                    :class="[
                      'overlay-tag',
                      { 'overlay-tag--penalized': isPenalizedTag(tag) },
                    ]"
                    draggable="true"
                    @dragstart="
                      startTagDrag(tagLabel(tag), 'face', group.face.id, $event)
                    "
                    @dragend="clearTagDrag"
                  >
                    {{ tagLabel(tag) }}
                    <button
                      class="tag-delete-btn"
                      @click.stop="removeTagFromFace(group.face, tag)"
                      title="Remove tag"
                    >
                      <v-icon size="12">mdi-close</v-icon>
                    </button>
                  </span>
                  <div v-if="!group.tags.length" class="tag-drop-placeholder">
                    Drop tags here
                  </div>
                </div>
              </div>

              <div
                v-for="group in handTagGroups"
                :key="group.handKey"
                class="tag-section"
              >
                <div
                  class="tag-section-title tag-section-title-row"
                  :style="{ color: group.color }"
                >
                  <span>{{ group.label }}</span>
                  <button
                    class="tag-section-action"
                    title="Remove hand"
                    @click.stop="removeHandDetection(group.hand)"
                  >
                    <v-icon size="14">mdi-delete</v-icon>
                  </button>
                </div>
                <div
                  class="tag-drop-zone"
                  :class="{
                    'tag-drop-zone--active': isDragOver('hand', group.hand.id),
                  }"
                  @dragover.prevent="handleDragOver('hand', group.hand.id)"
                  @dragenter.prevent="handleDragOver('hand', group.hand.id)"
                  @dragleave="handleDragLeave('hand', group.hand.id)"
                  @drop.prevent="handleDropToHand(group.hand)"
                >
                  <span
                    v-for="tag in group.tags"
                    :key="`hand-${group.handKey}-${tag.id ?? tag.tag}`"
                    :class="[
                      'overlay-tag',
                      { 'overlay-tag--penalized': isPenalizedTag(tag) },
                    ]"
                    draggable="true"
                    @dragstart="
                      startTagDrag(tagLabel(tag), 'hand', group.hand.id, $event)
                    "
                    @dragend="clearTagDrag"
                  >
                    {{ tagLabel(tag) }}
                    <button
                      class="tag-delete-btn"
                      @click.stop="removeTagFromHand(group.hand, tag)"
                      title="Remove tag"
                    >
                      <v-icon size="12">mdi-close</v-icon>
                    </button>
                  </span>
                  <div v-if="!group.tags.length" class="tag-drop-placeholder">
                    Drop tags here
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div class="sidebar-section">
            <div class="section-header">Metadata</div>
            <div
              v-if="
                !metadataEntries.length &&
                !comfyMetadata &&
                !pictureInfoEntries.length
              "
              class="metadata-empty"
            >
              No metadata available
            </div>
            <div v-else class="metadata-list">
              <div v-if="pictureInfoEntries.length" class="metadata-info-card">
                <div class="metadata-info-header">{{ infoHeaderLabel }}</div>
                <div class="metadata-info-grid">
                  <div
                    v-for="entry in pictureInfoEntries"
                    :key="entry.label"
                    class="metadata-info-item"
                  >
                    <div class="metadata-info-label">{{ entry.label }}</div>
                    <div class="metadata-info-value">{{ entry.value }}</div>
                  </div>
                </div>
              </div>
              <div v-if="comfyMetadata" class="metadata-comfy-card">
                <div class="metadata-comfy-header">
                  <span>ComfyUI</span>
                  <span class="metadata-comfy-subtitle">
                    {{ comfyMetadata.summary }}
                  </span>
                </div>
                <details
                  v-if="comfyMetadata.workflow"
                  class="metadata-comfy-details"
                >
                  <summary class="metadata-comfy-summary">
                    <span class="metadata-comfy-summary-left">
                      <span style="font-weight: 500; color: #fff"
                        >Workflow JSON</span
                      >
                    </span>
                    <button
                      class="metadata-comfy-workflow-action"
                      type="button"
                      @click.stop="copyMetadataValue(comfyMetadata.workflow)"
                    >
                      <v-icon size="14">mdi-content-copy</v-icon>
                      Copy
                    </button>
                    <button
                      class="metadata-comfy-workflow-action"
                      type="button"
                      @click.stop="
                        downloadComfyWorkflow(comfyMetadata.workflow)
                      "
                    >
                      <v-icon size="14">mdi-download</v-icon>
                      Download
                    </button>
                  </summary>
                  <textarea
                    class="metadata-comfy-textarea"
                    readonly
                    :value="stringifyMetadata(comfyMetadata.workflow)"
                  ></textarea>
                </details>
              </div>
            </div>
          </div>
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
} from "../utils/media.js";
import { apiClient } from "../utils/apiClient";
import AddToSetControl from "./AddToSetControl.vue";
import StarRatingOverlay from "./StarRatingOverlay.vue";
import { faceBoxColor, handBoxColor, toggleScore } from "../utils/utils.js";
import {
  dedupeTagList,
  getTagId as tagId,
  getTagLabel as tagLabel,
  normalizeTagList,
  tagMatches,
} from "../utils/tags.js";

const props = defineProps({
  open: { type: Boolean, default: false },
  initialImage: { type: Object, default: null },
  allImages: { type: Array, default: () => [] },
  backendUrl: { type: String, required: true },
  tagsRefreshing: { type: Boolean, default: false },
  tagUpdate: { type: Object, default: () => ({}) },
});

const { open, initialImage, allImages, backendUrl, tagsRefreshing, tagUpdate } =
  toRefs(props);

const image = ref(null);
const sidebarOpen = ref(true);
const filmstripOpen = ref(false);
const chromeHidden = ref(false);
const zoomMode = ref("fit");
const zoomSteps = ["fit", 1.5, 2];
const pan = reactive({ x: 0, y: 0 });
const isPanning = ref(false);
const lastPointer = ref({ x: 0, y: 0 });

// Watch for changes to initialImage and update local image copy
watch(
  () => initialImage.value,
  (newImg) => {
    const previousId = image.value?.id ?? null;
    image.value = newImg ? { ...newImg } : null;
    if (newImg?.id !== previousId) {
      zoomMode.value = "fit";
      resetPan();
    }
  },
  { immediate: true },
);

const emit = defineEmits([
  "close",
  "prev",
  "next",
  "apply-score",
  "remove-tag",
  "add-tag",
  "update-description",
  "refresh-image",
  "image-change",
  "tag-refresh-start",
  "added-to-set",
]);

const descriptionRef = ref(null);
const descriptionScrollMeta = reactive({
  hasOverflow: false,
});
const isEditingDescription = ref(false);
const isSavingDescription = ref(false);
const descriptionDraft = ref("");
const descriptionEditorRef = ref(null);
const descriptionCopyState = ref("idle");
const canCopyDescription = computed(() => {
  const source = isEditingDescription.value
    ? descriptionDraft.value
    : image.value?.description;
  return !!(source && source.length);
});
const descriptionTeaser = computed(() => {
  const desc = image.value?.description || "";
  const trimmed = desc.trim();
  if (!trimmed) return "";
  const match = trimmed.match(/[^.!?]+[.!?]?/);
  return match ? match[0].trim() : trimmed;
});
let copyResetTimer = null;

const addingTag = ref(false);
const newTag = ref("");
const tagInputRef = ref(null);
const penalizedTags = ref(new Set());
const penalizedTagsLoading = ref(false);
const lastTagUpdateKey = ref(0);
const addToSetControlKey = ref(0);

watch(open, (value) => {
  if (!value) {
    resetTagInput();
    chromeHidden.value = false;
    addToSetControlKey.value += 1;
  } else {
    fetchCharacters();
    fetchPenalizedTags();
  }
});

async function fetchPenalizedTags() {
  if (penalizedTagsLoading.value) return;
  penalizedTagsLoading.value = true;
  try {
    const res = await apiClient.get("/users/me/config");
    let list = [];
    if (Array.isArray(res.data?.smart_score_penalized_tags)) {
      list = res.data.smart_score_penalized_tags;
    } else if (
      res.data?.smart_score_penalized_tags &&
      typeof res.data.smart_score_penalized_tags === "object"
    ) {
      list = Object.keys(res.data.smart_score_penalized_tags);
    }
    const normalized = list
      .map((tag) =>
        String(tag || "")
          .trim()
          .toLowerCase(),
      )
      .filter(Boolean);
    penalizedTags.value = new Set(normalized);
  } catch (e) {
    penalizedTags.value = new Set();
  } finally {
    penalizedTagsLoading.value = false;
  }
}

function isPenalizedTag(tag) {
  const key = tagLabel(tag).trim().toLowerCase();
  if (!key) return false;
  return penalizedTags.value.has(key);
}

function getFullImageUrl(targetImage = null) {
  const data = targetImage || image.value;
  return buildMediaUrl({ backendUrl: backendUrl.value, image: data });
}

function getFilmstripThumbSrc(target) {
  if (!target) return "";
  if (target.thumbnail) return target.thumbnail;
  if (isSupportedVideoFile(getOverlayFormat(target))) return "";
  return getFullImageUrl(target);
}

watch(image, () => {
  resetTagInput();
  syncDescriptionDraft();
  nextTick(updateDescriptionScrollState);
});

watch(open, (isOpen) => {
  if (isOpen) {
    nextTick(updateDescriptionScrollState);
  } else {
    cancelEditDescription();
    resetCopyState();
  }
});

function resetTagInput() {
  addingTag.value = false;
  newTag.value = "";
}

function syncDescriptionDraft() {
  descriptionDraft.value = image.value?.description || "";
}

function beginAddTag() {
  addingTag.value = true;
  newTag.value = "";
  nextTick(() => {
    if (tagInputRef.value) {
      tagInputRef.value.focus();
      tagInputRef.value.select?.();
    }
  });
}

function cancelAddTag() {
  resetTagInput();
}

function confirmAddTag() {
  const trimmed = newTag.value.trim();
  if (!trimmed) {
    cancelAddTag();
    return;
  }
  const currentTags = normalizeTagList(image.value?.tags);
  if (currentTags.some((tag) => tag.tag === trimmed)) {
    cancelAddTag();
    return;
  }
  emit("add-tag", image.value.id, trimmed);
  if (image.value && Array.isArray(image.value.tags)) {
    const next = dedupeTagList([...currentTags, { id: null, tag: trimmed }]);
    image.value.tags = next;
  }
  resetTagInput();
}

function handleOverlayAddToSet(payload) {
  emit("added-to-set", payload);
}

async function clearTagsForImage() {
  if (!image.value?.id || !backendUrl.value) return;
  try {
    emit("tag-refresh-start", image.value.id);
    await apiClient.post(`${backendUrl.value}/pictures/clear_tags`, {
      picture_ids: [image.value.id],
    });
    if (Array.isArray(image.value.tags)) {
      image.value.tags = [];
    }
    emit("refresh-image", image.value.id);
  } catch (err) {
    alert(`Failed to clear tags: ${err?.message || err}`);
  }
}

function setScore(n) {
  if (!image.value) return;
  image.value.score = toggleScore(image.value.score, n);
  emit("apply-score", image.value, image.value.score);
}

function showPrevImage() {
  const sorted = allImages.value;
  if (!image.value || !sorted.length) return;
  const idx = sorted.findIndex((i) => i.id === image.value.id);
  if (idx === -1) return;
  const prevIdx = (idx - 1 + sorted.length) % sorted.length;
  image.value = sorted[prevIdx];
  emit("image-change", image.value);
}

function selectImageByIndex(idx) {
  if (!Array.isArray(allImages.value)) return;
  const target = allImages.value[idx];
  if (target) {
    image.value = target;
    resetPan();
    emit("image-change", image.value);
  }
}

function showNextImage() {
  const sorted = allImages.value;
  if (!image.value || !sorted.length) return;
  const idx = sorted.findIndex((i) => i.id === image.value.id);
  if (idx === -1) return;
  const nextIdx = (idx + 1) % sorted.length;
  image.value = sorted[nextIdx];
  emit("image-change", image.value);
}

function handleKeydown(e) {
  if (!open.value) return;

  handleUserActivity();

  if (isEditingDescription.value || addingTag.value) {
    // Handle editing-specific keydown behavior
    if (e.key === "Escape") {
      if (isEditingDescription.value) {
        cancelEditDescription(); // Close editing description without saving
      } else if (addingTag.value) {
        cancelAddTag(); // Close tag editing without saving
      }
    }
    return; // Ignore other overlay key presses when editing
  }

  // Regular keydown behavior
  if (e.key === "Escape") {
    emit("close");
  } else if (["ArrowLeft", "Left"].includes(e.key)) {
    showPrevImage();
  } else if (["ArrowRight", "Right"].includes(e.key)) {
    showNextImage();
  } else if (e.key === "z" || e.key === "Z") {
    toggleZoom();
  } else if (e.key === "i" || e.key === "I") {
    toggleSidebar();
  } else if ((e.key === "t" || e.key === "T") && sidebarOpen.value) {
    tagInputRef.value?.focus();
  } else if (["1", "2", "3", "4", "5"].includes(e.key)) {
    const score = parseInt(e.key, 10);
    if (image.value) setScore(score);
  }
}

const showFaceBbox = ref(false);
const isMobile = ref(false);
const MOBILE_BREAKPOINT = 900;
const windowHeight = ref(0);
const overlayMainRef = ref(null);
const touchStart = ref({ x: 0, y: 0, time: 0 });
const touchLatest = ref({ x: 0, y: 0 });
const swipeHintVisible = ref(false);
let swipeHintTimer = null;

function updateViewportMetrics() {
  if (typeof window !== "undefined") {
    isMobile.value = window.innerWidth <= MOBILE_BREAKPOINT;
    windowHeight.value = window.innerHeight || 0;
  }
}

const filmstripStyleVars = computed(() => {
  const targetCount = 7;
  const gap = 8;
  const railPadding = 8;
  const railPaddingTotal = railPadding * 2;
  const overlayMainHeight = overlayMainRef.value?.offsetHeight || 0;
  const fallbackHeight = Math.max(0, windowHeight.value || 0);
  const available = Math.max(0, overlayMainHeight || fallbackHeight);
  const totalGaps = gap * (targetCount - 1);
  const rawSize = (available - railPaddingTotal - totalGaps) / targetCount;
  const computed = Number.isFinite(rawSize) ? Math.floor(rawSize) : 0;
  const thumbSize = computed > 0 ? Math.max(40, computed) : 80;
  const railWidth = thumbSize + 12;
  return {
    "--filmstrip-thumb-size": `${thumbSize}px`,
    "--filmstrip-rail-width": `${railWidth}px`,
    "--filmstrip-available-height": `${available}px`,
    "--filmstrip-gap": `${gap}px`,
    "--filmstrip-padding": `${railPadding}px`,
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
  chromeHidden.value = false;
}

function handleOverlayClick(event) {
  const target = event?.target;
  if (!target || !(target instanceof HTMLElement)) {
    handleUserActivity();
    return;
  }
  if (chromeHidden.value) {
    handleUserActivity();
    return;
  }
  if (Date.now() - chromeRevealTimestamp.value < 250) {
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
  startEditDescription();
}

function toggleZoom() {
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

function onPanEnd() {
  isPanning.value = false;
  if (event?.currentTarget?.releasePointerCapture) {
    event.currentTarget.releasePointerCapture(event.pointerId);
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
  const direction = Math.sign(event.deltaY);
  if (direction === 0) return;
  const currentIndex = zoomSteps.findIndex((step) => step === zoomMode.value);
  if (direction < 0 && currentIndex < zoomSteps.length - 1) {
    zoomMode.value = zoomSteps[currentIndex + 1];
  } else if (direction > 0 && currentIndex > 0) {
    zoomMode.value = zoomSteps[currentIndex - 1];
  }
  if (zoomMode.value === "fit") {
    resetPan();
  }
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

const filmstripWindow = computed(() => {
  const images = Array.isArray(allImages.value) ? allImages.value : [];
  if (!images.length || !image.value) return [];
  const currentIndex = images.findIndex((img) => img.id === image.value.id);
  if (currentIndex === -1) return [];
  const targetCount = Math.min(7, images.length);
  let start = currentIndex - 3;
  let end = currentIndex + 3;
  if (start < 0) {
    end += Math.abs(start);
    start = 0;
  }
  if (end >= images.length) {
    const overshoot = end - (images.length - 1);
    start = Math.max(0, start - overshoot);
    end = images.length - 1;
  }
  while (end - start + 1 < targetCount && end < images.length - 1) {
    end += 1;
  }
  while (end - start + 1 < targetCount && start > 0) {
    start -= 1;
  }
  const indices = [];
  for (let idx = start; idx <= end; idx += 1) {
    indices.push(idx);
  }
  return indices.map((idx) => ({
    ...images[idx],
    index: idx,
    isActive: idx === currentIndex,
  }));
});

function toggleFaceBbox() {
  showFaceBbox.value = !showFaceBbox.value;
  console.log(
    "[ImageOverlay] Toggled showFaceBbox:",
    showFaceBbox.value,
    "faceBboxes:",
    faceBboxes.value,
    "handBboxes:",
    handBboxes.value,
  );
  image.value = image.value ? { ...image.value } : null;
}

const imgRef = ref(null);
const videoRef = ref(null);
const mediaInnerRef = ref(null);
const videoMeta = ref({ duration: null });
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
  const innerEl = mediaInnerRef.value;
  const innerRect = innerEl?.getBoundingClientRect();
  if (imgRef.value) {
    const rect = imgRef.value.getBoundingClientRect();
    overlayDims.value.width = rect.width || imgRef.value.clientWidth;
    overlayDims.value.height = rect.height || imgRef.value.clientHeight;
    overlayDims.value.naturalWidth = imgRef.value.naturalWidth;
    overlayDims.value.naturalHeight = imgRef.value.naturalHeight;
    overlayDims.value.offsetX = innerRect ? rect.left - innerRect.left : 0;
    overlayDims.value.offsetY = innerRect ? rect.top - innerRect.top : 0;
  } else if (videoRef.value) {
    const rect = videoRef.value.getBoundingClientRect();
    overlayDims.value.width = rect.width || videoRef.value.clientWidth;
    overlayDims.value.height = rect.height || videoRef.value.clientHeight;
    overlayDims.value.naturalWidth = videoRef.value.videoWidth;
    overlayDims.value.naturalHeight = videoRef.value.videoHeight;
    overlayDims.value.offsetX = innerRect ? rect.left - innerRect.left : 0;
    overlayDims.value.offsetY = innerRect ? rect.top - innerRect.top : 0;
    const duration = Number.isFinite(videoRef.value.duration)
      ? videoRef.value.duration
      : null;
    videoMeta.value = { duration };
  } else {
    videoMeta.value = { duration: null };
  }
}

watch(image, () => scheduleOverlayDimsUpdate());

onMounted(() => {
  updateViewportMetrics();
  window.addEventListener("resize", updateViewportMetrics);
  window.addEventListener("keydown", handleKeydown);
  window.addEventListener("resize", updateDescriptionScrollState);
  nextTick(updateDescriptionScrollState);
  fetchPenalizedTags();
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
  window.removeEventListener("resize", updateDescriptionScrollState);
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
  resetCopyState();
  clearCharacterThumbnails();
});

watch(open, (isOpen) => {
  if (!isOpen) {
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
  handleUserActivity();
}

function onTouchMove(event) {
  if (!isMobile.value) return;
  const touch = event.touches?.[0];
  if (!touch) return;
  touchLatest.value = { x: touch.clientX, y: touch.clientY };
}

function onTouchEnd() {
  if (!isMobile.value) return;
  const dx = touchLatest.value.x - touchStart.value.x;
  const dy = touchLatest.value.y - touchStart.value.y;
  const absX = Math.abs(dx);
  const absY = Math.abs(dy);
  const elapsed = Date.now() - touchStart.value.time;
  const swipeThreshold = 50;
  const maxVertical = 80;
  const maxTime = 600;

  if (absX >= swipeThreshold && absY <= maxVertical && elapsed <= maxTime) {
    if (dx > 0) {
      showPrevImage();
    } else {
      showNextImage();
    }
  }
}

// Store multiple face bounding boxes (now full face objects)
const faceBboxes = ref([]);
const handBboxes = ref([]);
const faceTagMap = ref({});
const handTagMap = ref({});
const dragState = reactive({
  tag: null,
  sourceType: null,
  sourceId: null,
});
const dragOverTarget = ref({ type: null, id: null });
const overlayThumbnail = ref(null);
const overlayThumbnailDims = ref({ width: 256, height: 256 });
const overlayThumbnailFaceMap = ref({});

const characters = ref([]);
const charactersLoading = ref(false);
const characterThumbnails = ref({});
let characterThumbnailEpoch = 0;
const FACE_THUMB_BASE = 34;
const FACE_THUMB_MIN = 28;
const FACE_THUMB_MAX = 60;
let metadataRequestId = 0;

async function fetchOverlayMetadata(imageId) {
  if (!imageId || !backendUrl.value) return;
  const requestId = (metadataRequestId += 1);
  try {
    const res = await apiClient.get(
      `${backendUrl.value}/pictures/${imageId}/metadata`,
    );
    if (metadataRequestId !== requestId) return;
    if (!image.value || image.value.id !== imageId) return;
    const data = await res.data;
    if (!data || Array.isArray(data)) return;
    const merged = { ...data, ...image.value };
    const dataTags = normalizeTagList(data.tags);
    if (data.tags !== undefined) {
      merged.tags = dedupeTagList(dataTags);
    }
    if (image.value?.description == null) {
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
    syncDescriptionDraft();
  } catch (e) {
    console.error("Failed to fetch overlay metadata:", e);
  }
}

// Fetch face bounding boxes for the current image and set character_name for each face
async function fetchFaceBboxes(imageId) {
  if (!imageId || !backendUrl.value) {
    faceBboxes.value = [];
    return;
  }
  try {
    const res = await apiClient.get(
      `${backendUrl.value}/pictures/${imageId}/faces`,
    );
    const faces = await res.data;
    console.log("Faces: ", faces);
    const faceArray = Array.isArray(faces) ? faces : faces.faces;
    const firstFrameFaces = faceArray.filter(
      (f) =>
        f.frame_index === 0 && Array.isArray(f.bbox) && f.bbox.length === 4,
    );
    // For each face, fetch character name if character_id is present
    await Promise.all(
      firstFrameFaces.map(async (face) => {
        if (face.character_id) {
          try {
            const res = await apiClient.get(
              `${backendUrl.value}/characters/${face.character_id}/name`,
            );
            const data = await res.data;
            face.character_name = data.name || null;
            console.log(
              `Fetched character_name for character_id ${face.character_id}:`,
              face.character_name,
            );
          } catch (e) {
            face.character_name = null;
            console.error(
              `Error fetching character_name for character_id ${face.character_id}:`,
              e,
            );
          }
        } else {
          face.character_name = null;
        }
      }),
    );
    faceBboxes.value = firstFrameFaces;
    await fetchFaceTagsForFaces(firstFrameFaces);
  } catch (e) {
    console.error("Error in fetchFaceBboxes:", e);
    faceBboxes.value = [];
  }
}

async function fetchHandBboxes(imageId) {
  if (!imageId || !backendUrl.value) {
    handBboxes.value = [];
    return;
  }
  try {
    const res = await apiClient.get(
      `${backendUrl.value}/pictures/${imageId}/hands`,
    );
    const hands = await res.data;
    const handArray = Array.isArray(hands) ? hands : hands.hands;
    const firstFrameHands = (handArray || []).filter(
      (h) =>
        h.frame_index === 0 && Array.isArray(h.bbox) && h.bbox.length === 4,
    );
    handBboxes.value = firstFrameHands;
    await fetchHandTagsForHands(firstFrameHands);
  } catch (e) {
    console.error("Error in fetchHandBboxes:", e);
    handBboxes.value = [];
  }
}

async function fetchFaceTagsForFaces(faces) {
  if (!backendUrl.value || !Array.isArray(faces) || !faces.length) {
    faceTagMap.value = {};
    return;
  }
  const entries = await Promise.all(
    faces.map(async (face) => {
      try {
        const res = await apiClient.get(
          `${backendUrl.value}/faces/${face.id}/tags`,
        );
        const payload = await res.data;
        const tags = Array.isArray(payload) ? payload : payload?.tags;
        return [face.id, normalizeTagList(tags)];
      } catch (e) {
        return [face.id, []];
      }
    }),
  );
  const nextMap = {};
  for (const [faceId, tags] of entries) {
    nextMap[faceId] = dedupeTagList(tags);
  }
  faceTagMap.value = nextMap;
}

async function fetchHandTagsForHands(hands) {
  if (!backendUrl.value || !Array.isArray(hands) || !hands.length) {
    handTagMap.value = {};
    return;
  }
  const entries = await Promise.all(
    hands.map(async (hand) => {
      try {
        const res = await apiClient.get(
          `${backendUrl.value}/hands/${hand.id}/tags`,
        );
        const payload = await res.data;
        const tags = Array.isArray(payload) ? payload : payload?.tags;
        return [hand.id, normalizeTagList(tags)];
      } catch (e) {
        return [hand.id, []];
      }
    }),
  );
  const nextMap = {};
  for (const [handId, tags] of entries) {
    nextMap[handId] = dedupeTagList(tags);
  }
  handTagMap.value = nextMap;
}

function ensureTagInImage(tag) {
  if (!image.value) return;
  const label = tagLabel(tag);
  if (!label) return;
  const tags = normalizeTagList(image.value.tags);
  if (!tags.some((entry) => entry.tag === label)) {
    const next = dedupeTagList([...tags, { id: null, tag: label }]);
    image.value = { ...image.value, tags: next };
    emit("add-tag", image.value.id, label);
  }
}

async function assignTagToFace(face, tag) {
  if (!face?.id || !tag || !backendUrl.value) return;
  const label = tagLabel(tag);
  if (!label) return;
  ensureTagInImage(label);
  const res = await apiClient.post(
    `${backendUrl.value}/faces/${face.id}/tags`,
    { tag: label },
  );
  const payload = await res.data;
  const tags = Array.isArray(payload) ? payload : payload?.tags;
  faceTagMap.value = {
    ...faceTagMap.value,
    [face.id]: normalizeTagList(tags),
  };
}

async function removeTagFromFace(face, tag, options = {}) {
  if (!face?.id || !tag || !backendUrl.value) return;
  const key = tagId(tag) ?? tagLabel(tag).trim();
  if (!key) return;
  const res = await apiClient.delete(
    `${backendUrl.value}/faces/${face.id}/tags/${encodeURIComponent(key)}`,
  );
  const payload = await res.data;
  const tags = Array.isArray(payload) ? payload : payload?.tags;
  faceTagMap.value = {
    ...faceTagMap.value,
    [face.id]: normalizeTagList(tags),
  };
  if (!options.skipRefresh && image.value?.id) {
    emit("refresh-image", image.value.id);
  }
}

async function assignTagToHand(hand, tag) {
  if (!hand?.id || !tag || !backendUrl.value) return;
  const label = tagLabel(tag);
  if (!label) return;
  ensureTagInImage(label);
  const res = await apiClient.post(
    `${backendUrl.value}/hands/${hand.id}/tags`,
    { tag: label },
  );
  const payload = await res.data;
  const tags = Array.isArray(payload) ? payload : payload?.tags;
  handTagMap.value = {
    ...handTagMap.value,
    [hand.id]: normalizeTagList(tags),
  };
}

async function removeTagFromHand(hand, tag, options = {}) {
  if (!hand?.id || !tag || !backendUrl.value) return;
  const key = tagId(tag) ?? tagLabel(tag).trim();
  if (!key) return;
  const res = await apiClient.delete(
    `${backendUrl.value}/hands/${hand.id}/tags/${encodeURIComponent(key)}`,
  );
  const payload = await res.data;
  const tags = Array.isArray(payload) ? payload : payload?.tags;
  handTagMap.value = {
    ...handTagMap.value,
    [hand.id]: normalizeTagList(tags),
  };
  if (!options.skipRefresh && image.value?.id) {
    emit("refresh-image", image.value.id);
  }
}

async function removeFaceDetection(face) {
  if (!face || !image.value?.id || !backendUrl.value) return;
  const index = face.face_index ?? face.faceIdx ?? null;
  if (index == null) return;
  try {
    await apiClient.delete(
      `${backendUrl.value}/pictures/${image.value.id}/face/${index}`,
    );
    await fetchFaceBboxes(image.value.id);
    emit("refresh-image", {
      imageId: image.value.id,
      faces: faceBboxes.value,
    });
  } catch (e) {
    alert(`Failed to delete face: ${e?.message || e}`);
  }
}

async function removeHandDetection(hand) {
  if (!hand || !image.value?.id || !backendUrl.value) return;
  const index = hand.hand_index ?? hand.handIdx ?? null;
  if (index == null) return;
  try {
    await apiClient.delete(
      `${backendUrl.value}/pictures/${image.value.id}/hand/${index}`,
    );
    await fetchHandBboxes(image.value.id);
    emit("refresh-image", image.value.id);
  } catch (e) {
    alert(`Failed to delete hand: ${e?.message || e}`);
  }
}

async function fetchOverlayThumbnail(imageId) {
  if (!imageId || !backendUrl.value) {
    overlayThumbnail.value = null;
    overlayThumbnailFaceMap.value = {};
    overlayThumbnailDims.value = { width: 256, height: 256 };
    return;
  }
  try {
    const res = await apiClient.post(
      `${backendUrl.value}/pictures/thumbnails`,
      JSON.stringify({ ids: [String(imageId)] }),
    );
    const data = await res.data;
    const entry = data?.[String(imageId)] || null;
    if (!entry) {
      overlayThumbnail.value = null;
      overlayThumbnailFaceMap.value = {};
      overlayThumbnailDims.value = { width: 256, height: 256 };
      return;
    }
    const thumbnailUrl = entry.thumbnail || null;
    overlayThumbnail.value = thumbnailUrl
      ? thumbnailUrl.startsWith("http")
        ? thumbnailUrl
        : `${backendUrl.value}${thumbnailUrl}`
      : null;
    const width = Number(entry.thumbnail_width);
    const height = Number(entry.thumbnail_height);
    const hasThumbDims =
      Number.isFinite(width) &&
      width > 0 &&
      Number.isFinite(height) &&
      height > 0;
    const thumbWidth = hasThumbDims ? width : 256;
    const thumbHeight = hasThumbDims ? height : 256;
    overlayThumbnailDims.value = {
      width: thumbWidth,
      height: thumbHeight,
    };
    const sourceWidth = Number(
      image.value?.width || overlayDims.value.naturalWidth,
    );
    const sourceHeight = Number(
      image.value?.height || overlayDims.value.naturalHeight,
    );
    const shouldScale = !hasThumbDims && sourceWidth > 0 && sourceHeight > 0;
    const scaleX = shouldScale ? thumbWidth / sourceWidth : 1;
    const scaleY = shouldScale ? thumbHeight / sourceHeight : 1;
    const faceMap = {};
    if (Array.isArray(entry.faces)) {
      entry.faces.forEach((face) => {
        if (face?.id != null && Array.isArray(face.bbox)) {
          const bbox = face.bbox;
          const mapped =
            shouldScale && bbox.length === 4
              ? [
                  Math.round(bbox[0] * scaleX),
                  Math.round(bbox[1] * scaleY),
                  Math.round(bbox[2] * scaleX),
                  Math.round(bbox[3] * scaleY),
                ]
              : bbox;
          faceMap[face.id] = { bbox: mapped };
        }
      });
    }
    overlayThumbnailFaceMap.value = faceMap;
  } catch (e) {
    console.error("Failed to fetch overlay thumbnail:", e);
    overlayThumbnail.value = null;
    overlayThumbnailFaceMap.value = {};
  }
}

async function fetchCharacters(force = false) {
  if (!backendUrl.value || charactersLoading.value) return;
  if (!force && Array.isArray(characters.value) && characters.value.length) {
    return;
  }
  charactersLoading.value = true;
  const requestEpoch = (characterThumbnailEpoch += 1);
  try {
    const res = await apiClient.get(`${backendUrl.value}/characters`);
    const data = await res.data;
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
    if (image.value?.id) {
      emit("refresh-image", {
        imageId: image.value.id,
        faces: faceBboxes.value,
      });
    }
  } catch (e) {
    alert(`Failed to assign character: ${e?.message || e}`);
  }
}

async function unassignFaceCharacter(face) {
  if (!face?.id || !face?.character_id || !backendUrl.value) return;
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
    if (image.value?.id) {
      emit("refresh-image", {
        imageId: image.value.id,
        faces: faceBboxes.value,
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

// Watch for image changes and fetch bboxes
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
      scheduleOverlayDimsUpdate();
      fetchFaceBboxes(newId);
      fetchHandBboxes(newId);
      fetchOverlayMetadata(newId);
      fetchOverlayThumbnail(newId);
    } else {
      faceBboxes.value = [];
      handBboxes.value = [];
      faceTagMap.value = {};
      handTagMap.value = {};
      overlayThumbnail.value = null;
      overlayThumbnailFaceMap.value = {};
      overlayThumbnailDims.value = { width: 256, height: 256 };
    }
    resetPan();
  },
  { immediate: true },
);

watch(
  () => tagsRefreshing.value,
  (isRefreshing, wasRefreshing) => {
    if (!wasRefreshing && isRefreshing) {
      if (image.value && Array.isArray(image.value.tags)) {
        image.value.tags = [];
      }
      faceTagMap.value = {};
      handTagMap.value = {};
      return;
    }
    if (!isRefreshing && wasRefreshing && image.value?.id) {
      fetchFaceTagsForFaces(faceBboxes.value);
      fetchHandTagsForHands(handBboxes.value);
    }
  },
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
    if (faceBboxes.value.length) {
      fetchFaceTagsForFaces(faceBboxes.value);
    }
    if (handBboxes.value.length) {
      fetchHandTagsForHands(handBboxes.value);
    }
  },
);

function handleTagBackspace(event) {
  if (event.key !== "Backspace") return;
  if (newTag.value.trim()) return;
  const tags = normalizeTagList(image.value?.tags);
  if (!tags.length) return;
  removeTag(tags[tags.length - 1]);
}

const metadataEntries = computed(() => {
  const base = normalizeMetadata(image.value?.metadata);
  const entries = Object.entries(stripComfyMetadata(base) || {});
  return entries.map(([key, value]) => ({ key, value }));
});

const faceAssignItems = computed(() => {
  const faces = Array.isArray(faceBboxes.value) ? faceBboxes.value : [];
  return faces.map((face, idx) => ({
    ...face,
    faceIdx: idx,
    faceKey: face?.id ?? `face-${idx}`,
    label: `Face ${idx + 1}`,
  }));
});

const faceTagGroups = computed(() => {
  const faces = Array.isArray(faceBboxes.value) ? faceBboxes.value : [];
  return faces.map((face, idx) => {
    const characterName = face?.character_name;
    const label = characterName ? `${characterName}'s face` : `Face ${idx + 1}`;
    return {
      face,
      faceKey: face?.id ?? `face-${idx}`,
      label,
      tags: faceTagMap.value?.[face.id] || [],
      color: faceBoxColor(idx),
    };
  });
});

const handTagGroups = computed(() => {
  const hands = Array.isArray(handBboxes.value) ? handBboxes.value : [];
  return hands.map((hand, idx) => ({
    hand,
    handKey: hand?.id ?? `hand-${idx}`,
    label: handLabel(hand, idx),
    tags: handTagMap.value?.[hand.id] || [],
    color: handBoxColor(idx),
  }));
});

const imageTags = computed(() => {
  return dedupeTagList(normalizeTagList(image.value?.tags));
});

const faceTags = computed(() => {
  const values = Object.values(faceTagMap.value || {});
  const tags = values.flatMap((list) => normalizeTagList(list));
  return dedupeTagList(tags);
});

const handTags = computed(() => {
  const values = Object.values(handTagMap.value || {});
  const tags = values.flatMap((list) => normalizeTagList(list));
  return dedupeTagList(tags);
});

const allImageTags = computed(() => {
  return dedupeTagList([
    ...imageTags.value,
    ...faceTags.value,
    ...handTags.value,
  ]);
});

function isPictureTag(tag) {
  return imageTags.value.some((entry) => tagMatches(entry, tag));
}

function startTagDrag(tag, sourceType, sourceId, event) {
  dragState.tag = tag;
  dragState.sourceType = sourceType;
  dragState.sourceId = sourceId;
  if (event?.dataTransfer) {
    event.dataTransfer.setData("text/plain", tag);
    event.dataTransfer.effectAllowed = "move";
  }
}

function clearTagDrag() {
  dragState.tag = null;
  dragState.sourceType = null;
  dragState.sourceId = null;
  dragOverTarget.value = { type: null, id: null };
}

function handleDragOver(type, id) {
  dragOverTarget.value = { type, id };
}

function handleDragLeave(type, id) {
  if (dragOverTarget.value?.type === type && dragOverTarget.value?.id === id) {
    dragOverTarget.value = { type: null, id: null };
  }
}

function isDragOver(type, id) {
  return dragOverTarget.value?.type === type && dragOverTarget.value?.id === id;
}

async function handleTagDrop(targetType, targetId) {
  const tag = dragState.tag;
  const sourceType = dragState.sourceType;
  const sourceId = dragState.sourceId;
  if (!tag) return;
  if (targetType === sourceType && targetId === sourceId) {
    clearTagDrag();
    return;
  }

  if (targetType === "unassigned") {
    if (sourceType === "face" && sourceId != null) {
      const face = faceBboxes.value.find((f) => f.id === sourceId);
      if (face) await removeTagFromFace(face, tag);
    }
    if (sourceType === "hand" && sourceId != null) {
      const hand = handBboxes.value.find((h) => h.id === sourceId);
      if (hand) await removeTagFromHand(hand, tag);
    }
    clearTagDrag();
    return;
  }

  if (targetType === "face") {
    const face = faceBboxes.value.find((f) => f.id === targetId);
    if (!face) return;
    await assignTagToFace(face, tag);
    clearTagDrag();
    return;
  }

  if (targetType === "hand") {
    const hand = handBboxes.value.find((h) => h.id === targetId);
    if (!hand) return;
    await assignTagToHand(hand, tag);
    clearTagDrag();
  }
}

async function handleDropToUnassigned() {
  await handleTagDrop("unassigned", null);
}

async function handleDropToFace(face) {
  if (!face?.id) return;
  await handleTagDrop("face", face.id);
}

async function handleDropToHand(hand) {
  if (!hand?.id) return;
  await handleTagDrop("hand", hand.id);
}

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

const infoHeaderLabel = computed(() => {
  const format = image.value ? getOverlayFormat(image.value) : "";
  const isVideo = format ? isSupportedVideoFile(format) : false;
  return isVideo ? "Video information" : "Picture information";
});

const pictureInfoEntries = computed(() => {
  if (!image.value) return [];
  const entries = [];
  const { width, height } = getDisplayDimensions();
  if (width && height) {
    entries.push({ label: "Size", value: `${width}×${height}` });
    const aspect = formatAspectRatio(width, height);
    if (aspect) entries.push({ label: "Aspect", value: aspect });
  }

  const sizeBytes =
    image.value.size_bytes ||
    image.value.sizeBytes ||
    image.value.file_size ||
    image.value.fileSize ||
    image.value.metadata?.size_bytes ||
    image.value.metadata?.file_size ||
    null;
  if (sizeBytes) {
    entries.push({ label: "MB", value: formatMegabytes(sizeBytes) });
  }

  const format = getOverlayFormat(image.value);
  if (format) {
    const isVideo = isSupportedVideoFile(format);
    entries.push({
      label: "Type",
      value: `${isVideo ? "Video" : "Image"} · ${format.toUpperCase()}`,
    });

    if (isVideo) {
      const frameCount =
        image.value.frame_count ||
        image.value.frames ||
        image.value.metadata?.frame_count ||
        image.value.metadata?.frames ||
        null;
      if (frameCount) {
        entries.push({ label: "Frames", value: String(frameCount) });
      }

      const durationSeconds =
        videoMeta.value.duration ||
        image.value.duration ||
        image.value.runtime ||
        image.value.metadata?.duration ||
        image.value.metadata?.runtime ||
        null;
      if (durationSeconds) {
        entries.push({
          label: "Runtime",
          value: formatDuration(durationSeconds),
        });
      }
    }
  }

  return entries;
});

const comfyMetadata = computed(() => {
  const base = normalizeMetadata(image.value?.metadata);
  if (!base || !Object.keys(base).length) return null;

  const png = base.png && typeof base.png === "object" ? base.png : {};
  const workflow = findFirstComfyWorkflow([
    png.workflow,
    png.workflow_json,
    base.workflow,
    base.workflow_json,
    base.comfyui_workflow,
    base.comfyui?.workflow,
    base.comfyui?.workflow_json,
  ]);

  if (!workflow) return null;

  const workflowStats = workflow ? summarizeComfyWorkflow(workflow) : null;

  const summaryParts = [];
  if (workflowStats) {
    summaryParts.push(
      `Workflow · ${workflowStats.nodeCount} nodes` +
        (workflowStats.linkCount !== null
          ? ` · ${workflowStats.linkCount} links`
          : ""),
    );
  }
  return {
    workflow,
    summary: summaryParts.join(" · ") || "Detected ComfyUI metadata",
  };
});

function normalizeMetadata(input) {
  if (!input || typeof input !== "object") return {};
  const output = {};
  Object.entries(input).forEach(([key, value]) => {
    output[key] = parseMetadataValue(value);
  });
  return output;
}

function stripComfyMetadata(input) {
  if (!input || typeof input !== "object") return {};
  const output = {};
  Object.entries(input).forEach(([key, value]) => {
    if (
      key === "workflow" ||
      key === "prompt" ||
      key === "comfyui_workflow" ||
      key === "comfyui_prompt"
    ) {
      return;
    }
    if (key === "png" && value && typeof value === "object") {
      const { workflow, prompt, ...rest } = value;
      if (Object.keys(rest).length) {
        output[key] = rest;
      }
      return;
    }
    if (key === "comfyui" && value && typeof value === "object") {
      const { workflow, prompt, ...rest } = value;
      if (Object.keys(rest).length) {
        output[key] = rest;
      }
      return;
    }
    output[key] = value;
  });
  return output;
}

function parseMetadataValue(value) {
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) return value;
    if (
      (trimmed.startsWith("{") && trimmed.endsWith("}")) ||
      (trimmed.startsWith("[") && trimmed.endsWith("]"))
    ) {
      try {
        return JSON.parse(trimmed);
      } catch (e) {
        return value;
      }
    }
    return value;
  }
  if (Array.isArray(value)) {
    return value.map((item) => parseMetadataValue(item));
  }
  if (value && typeof value === "object") {
    const nested = {};
    Object.entries(value).forEach(([k, v]) => {
      nested[k] = parseMetadataValue(v);
    });
    return nested;
  }
  return value;
}

function findFirstComfyWorkflow(values) {
  for (const value of values) {
    const candidate = normalizeComfyWorkflowCandidate(value);
    if (isComfyWorkflow(candidate)) return candidate;
  }
  return null;
}

function normalizeComfyWorkflowCandidate(value) {
  if (!value) return null;
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (
      (trimmed.startsWith("{") && trimmed.endsWith("}")) ||
      (trimmed.startsWith("[") && trimmed.endsWith("]"))
    ) {
      try {
        return JSON.parse(trimmed);
      } catch (e) {
        return null;
      }
    }
    return null;
  }
  if (value && typeof value === "object") {
    if (value.workflow) {
      return normalizeComfyWorkflowCandidate(value.workflow) || value;
    }
    return value;
  }
  return null;
}

function isComfyWorkflow(value) {
  if (!value || typeof value !== "object") return false;
  const hasNodesArray = Array.isArray(value.nodes);
  const hasLinksArray = Array.isArray(value.links);
  const hasNodeHints =
    typeof value.last_node_id === "number" ||
    typeof value.last_link_id === "number";
  return hasNodesArray || hasLinksArray || hasNodeHints;
}

function summarizeComfyWorkflow(workflow) {
  const nodeCount = Array.isArray(workflow.nodes)
    ? workflow.nodes.length
    : workflow.nodes && typeof workflow.nodes === "object"
      ? Object.keys(workflow.nodes).length
      : 0;
  const linkCount = Array.isArray(workflow.links)
    ? workflow.links.length
    : workflow.links && typeof workflow.links === "object"
      ? Object.keys(workflow.links).length
      : null;
  return { nodeCount, linkCount };
}

function getDisplayDimensions() {
  const w = Number(overlayDims.value.naturalWidth);
  const h = Number(overlayDims.value.naturalHeight);
  if (Number.isFinite(w) && Number.isFinite(h) && w > 1 && h > 1) {
    return { width: Math.round(w), height: Math.round(h) };
  }
  const fallbackW = Number(image.value?.width || 0);
  const fallbackH = Number(image.value?.height || 0);
  return {
    width: fallbackW > 0 ? fallbackW : null,
    height: fallbackH > 0 ? fallbackH : null,
  };
}

function formatAspectRatio(width, height) {
  if (!width || !height) return "";
  const gcd = (a, b) => (b === 0 ? a : gcd(b, a % b));
  const divisor = gcd(width, height);
  const ratioW = Math.round(width / divisor);
  const ratioH = Math.round(height / divisor);
  return `${ratioW}:${ratioH}`;
}

function formatMegabytes(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value <= 0) return "";
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDuration(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value <= 0) return "";
  const total = Math.round(value);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const padded = (num) => String(num).padStart(2, "0");
  if (hours > 0) {
    return `${hours}:${padded(minutes)}:${padded(secs)}`;
  }
  return `${minutes}:${padded(secs)}`;
}

function stringifyMetadata(value) {
  try {
    return JSON.stringify(value, null, 2);
  } catch (e) {
    return String(value);
  }
}

function isPrimitiveValue(value) {
  return (
    value === null ||
    value === undefined ||
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  );
}

async function copyMetadataValue(value) {
  const text = isPrimitiveValue(value)
    ? String(value)
    : stringifyMetadata(value);
  if (!text) return;
  try {
    if (navigator?.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.focus();
      textarea.select();
      document.execCommand("copy");
      document.body.removeChild(textarea);
    }
  } catch (err) {
    console.warn("Failed to copy metadata value:", err);
  }
}

function handLabel(hand, idx) {
  const handIndex = hand?.hand_index;
  if (typeof handIndex === "number" && Number.isFinite(handIndex)) {
    return `Hand ${handIndex + 1}`;
  }
  return `Hand ${idx + 1}`;
}

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

function updateDescriptionScrollState() {
  const el = descriptionRef.value;
  if (!el) {
    descriptionScrollMeta.hasOverflow = false;
    return;
  }

  descriptionScrollMeta.hasOverflow = false; // Disable overflow logic
}

function startEditDescription() {
  if (!image.value) return;
  syncDescriptionDraft();
  isEditingDescription.value = true;
  nextTick(() => {
    if (descriptionEditorRef.value) {
      descriptionEditorRef.value.focus();
    }
  });
}

function cancelEditDescription() {
  isEditingDescription.value = false;
  isSavingDescription.value = false;
  syncDescriptionDraft();
  nextTick(updateDescriptionScrollState);
}

async function saveDescription() {
  if (!image.value || isSavingDescription.value) return;
  isSavingDescription.value = true;
  const newDescription = descriptionDraft.value.trim();
  const payload = { description: newDescription || null };
  try {
    const res = await apiClient.patch(
      `${backendUrl.value}/pictures/${image.value.id}`,
      payload,
    );
    image.value = { ...image.value, description: newDescription };
    if (Array.isArray(allImages.value)) {
      const idx = allImages.value.findIndex(
        (img) => img && img.id === image.value.id,
      );
      if (idx !== -1) {
        allImages.value[idx] = {
          ...allImages.value[idx],
          description: newDescription,
        };
      }
    }
    emit("update-description", image.value.id, newDescription);
    isEditingDescription.value = false;
    nextTick(updateDescriptionScrollState);
  } catch (err) {
    alert(`Failed to update description: ${err?.message || err}`);
  } finally {
    isSavingDescription.value = false;
  }
}

async function copyDescription() {
  const text = isEditingDescription.value
    ? descriptionDraft.value
    : image.value?.description;
  if (!text) return;
  try {
    if (navigator?.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.focus();
      textarea.select();
      document.execCommand("copy");
      document.body.removeChild(textarea);
    }
    descriptionCopyState.value = "copied";
    if (copyResetTimer) {
      clearTimeout(copyResetTimer);
    }
    copyResetTimer = window.setTimeout(() => {
      resetCopyState();
    }, 2000);
  } catch (err) {
    alert(`Unable to copy description: ${err?.message || err}`);
  }
}

function resetCopyState() {
  if (copyResetTimer) {
    clearTimeout(copyResetTimer);
    copyResetTimer = null;
  }
  descriptionCopyState.value = "idle";
}

function handleDescriptionEditorKey(event) {
  if (event.key === "Escape") {
    event.preventDefault();
    cancelEditDescription();
    return;
  }
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    event.preventDefault();
    saveDescription();
  }
}

async function removeAllTag(tag) {
  if (!tag) return;
  const label = tagLabel(tag);
  if (!label) return;
  let didUpdate = false;
  const imageMatch = imageTags.value.find((entry) => entry.tag === label);
  if (imageMatch && imageMatch.id != null) {
    if (image.value && Array.isArray(image.value.tags)) {
      const current = normalizeTagList(image.value.tags);
      image.value.tags = current.filter((entry) => entry.tag !== label);
    }
    didUpdate = true;
  } else if (image.value && Array.isArray(image.value.tags)) {
    const current = normalizeTagList(image.value.tags);
    const next = current.filter((entry) => entry.tag !== label);
    if (next.length !== current.length) {
      image.value.tags = next;
      didUpdate = true;
    }
  }

  const faces = Array.isArray(faceBboxes.value) ? faceBboxes.value : [];
  for (const face of faces) {
    const tags = normalizeTagList(faceTagMap.value?.[face.id]);
    const nextTags = tags.filter((entry) => entry.tag !== label);
    if (nextTags.length !== tags.length) {
      faceTagMap.value = {
        ...faceTagMap.value,
        [face.id]: nextTags,
      };
      didUpdate = true;
    }
  }

  const hands = Array.isArray(handBboxes.value) ? handBboxes.value : [];
  for (const hand of hands) {
    const tags = normalizeTagList(handTagMap.value?.[hand.id]);
    const nextTags = tags.filter((entry) => entry.tag !== label);
    if (nextTags.length !== tags.length) {
      handTagMap.value = {
        ...handTagMap.value,
        [hand.id]: nextTags,
      };
      didUpdate = true;
    }
  }

  if (image.value?.id && backendUrl.value) {
    try {
      await apiClient.post(
        `${backendUrl.value}/pictures/${image.value.id}/tags/remove_all`,
        { tag: label },
      );
    } catch (err) {
      console.warn("Failed to remove tag everywhere:", err);
    }
  }

  if (didUpdate && image.value?.id) {
    emit("refresh-image", image.value.id);
  }
}

function removeTag(tag) {
  if (!image.value || !Array.isArray(image.value.tags)) return;
  if (tagId(tag) == null) {
    console.warn("Tag id is required to remove a picture tag.", tag);
    return;
  }
  const current = normalizeTagList(image.value.tags);
  const label = tagLabel(tag);
  if (!label) return;
  const next = current.filter((entry) => entry.tag !== label);
  image.value.tags = next;
  emit("remove-tag", image.value.id, tag); // Notify parent component
}

function downloadComfyWorkflow(workflow) {
  if (!workflow) return;
  const payload = stringifyMetadata(workflow);
  const blob = new Blob([payload], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "comfyui_workflow.json";
  document.body.appendChild(a);
  a.click();
  setTimeout(() => {
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }, 100);
}
</script>

<style scoped>
.image-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.92);
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
  --topbar-height: 56px;
  position: relative;
}

.overlay-shell.sidebar-open {
  --sidebar-width: 0px;
}

.overlay-topbar {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
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

.overlay-close {
  border: none;
  background: rgba(var(--v-theme-primary), 0.7);
  color: #fff;
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

.overlay-title-text {
  font-weight: 600;
  font-size: 1rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.overlay-desc-teaser {
  border: none;
  background: transparent;
  color: rgba(255, 255, 255, 0.7);
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

.zoom-btn-label {
  min-width: 48px;
  text-align: left;
}

.zoom-btn-label {
  font-size: 0.8rem;
  font-weight: 600;
  line-height: 1;
}

.overlay-main {
  flex: 1;
  display: grid;
  grid-template-columns: 1fr;
  height: 100%;
  min-height: 0;
  position: relative;
}

.overlay-character-tag {
  border: 1px solid transparent;
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
  border-radius: 12px;
  background: #111;
  box-shadow: 0 12px 30px rgba(0, 0, 0, 0.45);
}

.overlay-nav {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  width: 44px;
  height: 44px;
  border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.2);
  background: rgba(0, 0, 0, 0.35);
  color: #fff;
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
  left: 16px;
}

.overlay-nav-right {
  right: 16px;
}

.zoom-hud {
  position: absolute;
  bottom: 16px;
  right: 16px;
  padding: 4px 10px;
  border-radius: 999px;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  font-size: 0.75rem;
  transition: opacity 0.2s ease;
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
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  border-radius: 999px;
  font-size: 0.85rem;
  z-index: 4;
}

.overlay-rail {
  position: absolute;
  top: var(--topbar-height);
  left: 0;
  bottom: 0;
  width: var(--filmstrip-rail-width, var(--rail-open-width));
  background: rgba(var(--v-theme-dark-surface), 0.9);
  border-left: 1px solid rgba(255, 255, 255, 0.08);
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: var(--filmstrip-padding, 8px) 6px;
  transition: opacity 0.2s ease;
  overflow: hidden;
  height: calc(100% - var(--topbar-height));
  z-index: 3;
}

.overlay-rail.hidden {
  opacity: 0;
  pointer-events: none;
}

.filmstrip-list {
  display: flex;
  flex-direction: column;
  gap: var(--filmstrip-gap, 8px);
  overflow-y: auto;
  width: var(--filmstrip-thumb-size, 100%);
  align-items: center;
  overflow-x: hidden;
  align-self: center;
  padding-right: 4px;
  height: 100%;
}

.filmstrip-thumb {
  border: none;
  padding: 0;
  background: transparent;
  cursor: pointer;
  border-radius: 8px;
  overflow: hidden;
  border: 2px solid transparent;
  width: var(--filmstrip-thumb-size, 100%);
  height: var(--filmstrip-thumb-size, auto);
  max-width: 100%;
  aspect-ratio: 1 / 1;
}

.filmstrip-thumb.active {
  border-color: rgba(255, 183, 77, 0.9);
  box-shadow: 0 0 0 2px rgba(255, 183, 77, 0.35);
}

.filmstrip-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.filmstrip-thumb-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(255, 255, 255, 0.08);
  color: rgba(255, 255, 255, 0.85);
}

.overlay-sidebar {
  position: absolute;
  top: var(--topbar-height);
  right: 0;
  bottom: 0;
  width: var(--sidebar-width);
  background: rgba(var(--v-theme-dark-surface), 0.9);
  color: rgb(var(--v-theme-on-dark-surface));
  transition: width 0.2s ease;
  overflow: hidden;
  padding: 0;
  height: calc(100% - var(--topbar-height));
  z-index: 4;
}

.overlay-sidebar.open {
  width: 320px;
  padding: 16px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.overlay-sidebar.hidden {
  opacity: 0;
  pointer-events: none;
}

.sidebar-section {
  margin-bottom: 20px;
}

.sidebar-section--tags {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 600;
  margin-bottom: 8px;
  color: #fff;
}

.section-meta-group {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.section-meta-btn {
  border: none;
  background: transparent;
  color: rgba(255, 255, 255, 0.7);
  padding: 2px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}

.section-meta-btn--danger {
  background: rgb(var(--v-theme-error));
  color: rgb(var(--v-theme-on-error));
  border-radius: 6px;
  padding: 2px 6px;
}

.section-meta-btn:disabled {
  cursor: default;
  opacity: 0.5;
}

.section-meta {
  font-size: 0.75rem;
  color: rgba(255, 255, 255, 0.6);
}

.description-editor textarea {
  width: 100%;
  min-height: 120px;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.2);
  background: rgba(0, 0, 0, 0.35);
  color: #fff;
  padding: 6px;
  resize: vertical;
}

.description-actions {
  margin-top: 6px;
  display: flex;
  gap: 8px;
}

.tag-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-right: 4px;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.tag-refresh-indicator {
  display: inline-flex;
  align-items: center;
  padding: 2px 4px;
  margin-right: 4px;
}

.overlay-tag {
  background: rgba(255, 255, 255, 0.1);
  color: #fff;
  border-radius: 6px;
  padding: 1px 2px 1px 6px;
  font-size: 0.72rem;
  line-height: 1.2;
  margin-bottom: 0px;
  margin-right: 0px;
  margin-left: 0px;
  justify-content: center;
  vertical-align: middle;
  cursor: pointer;
}

.overlay-tag--penalized {
  color: rgb(var(--v-theme-error));
  border: 1px solid rgba(var(--v-theme-error), 0.6);
  background: rgba(var(--v-theme-error), 0.15);
}

.tag-delete-btn {
  margin: 0px;
  padding: 2px;
  background: transparent;
  border: none;
  color: rgb(var(--v-theme-primary));
  cursor: pointer;
  font-size: 0.8em;
  line-height: 1;
  vertical-align: middle;
}

.tag-delete-btn:hover {
  color: rgb(var(--v-theme-accent));
}

.tag-add-input {
  background: rgba(0, 0, 0, 0.4);
  border: 1px solid rgba(255, 255, 255, 0.2);
  color: #fff;
  border-radius: 999px;
  padding: 1px 6px;
  font-size: 0.7rem;
}

.face-assign-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px;
  margin-top: 6px;
}

.face-assign-card {
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 6px;
  padding: 4px;
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
  gap: 4px;
  min-width: 0;
  flex: 1;
}

.face-assign-label {
  font-size: 0.78rem;
  color: rgba(255, 255, 255, 0.9);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.face-assign-select {
  width: 100%;
  background: rgba(0, 0, 0, 0.45);
  border: 1px solid rgba(255, 255, 255, 0.15);
  color: #fff;
  border-radius: 8px;
  padding: 2px 6px;
  font-size: 0.75rem;
  height: 26px;
}

.face-assign-select:disabled {
  opacity: 0.6;
}

.face-assign-empty {
  font-size: 0.8rem;
  color: rgba(255, 255, 255, 0.6);
  padding: 4px 6px;
}

.metadata-empty {
  font-size: 0.85rem;
  color: rgba(255, 255, 255, 0.6);
}

.metadata-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.metadata-info-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.06);
}

.metadata-info-header {
  font-size: 0.84rem;
  font-weight: 600;
  color: rgba(255, 255, 255, 0.85);
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

.metadata-info-label {
  font-size: 0.7rem;
  color: rgba(255, 255, 255, 0.6);
}

.metadata-info-value {
  font-size: 0.8rem;
  color: #fff;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.metadata-comfy-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 10px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.06);
}

.metadata-comfy-header {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-weight: 600;
  font-size: 0.74rem;
  color: rgba(255, 255, 255, 0.85);
}

.metadata-comfy-subtitle {
  font-size: 0.78rem;
  font-weight: 500;
  color: rgba(255, 255, 255, 0.65);
}

.metadata-comfy-details {
  background: rgba(0, 0, 0, 0.25);
  border-radius: 8px;
  padding: 8px 10px;
}

.metadata-comfy-details summary {
  cursor: pointer;
  font-size: 0.78rem;
  color: rgba(255, 255, 255, 0.75);
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
  color: rgba(255, 255, 255, 0.75);
  font-size: 0.72rem;
  padding: 2px 2px;
  border-radius: 4px;
  cursor: pointer;
}

.metadata-comfy-workflow-action:hover {
  background: rgba(255, 255, 255, 0.12);
  color: #fff;
}

.metadata-comfy-textarea {
  margin-top: 8px;
  width: 100%;
  max-width: 100%;
  box-sizing: border-box;
  min-height: 160px;
  max-height: 280px;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.15);
  background: rgba(0, 0, 0, 0.35);
  color: #fff;
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

.metadata-row {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 8px;
  align-items: start;
  background: rgba(255, 255, 255, 0.05);
  padding: 8px;
  border-radius: 8px;
}

.metadata-key {
  font-weight: 600;
  font-size: 0.8rem;
  color: rgba(255, 255, 255, 0.7);
}

.metadata-value {
  font-size: 0.8rem;
  color: #fff;
  word-break: break-word;
}

.metadata-value pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
}

.metadata-copy {
  border: none;
  background: transparent;
  color: rgba(255, 255, 255, 0.7);
  cursor: pointer;
  justify-self: end;
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

.hand-bbox-overlay {
  box-sizing: border-box;
  position: absolute;
  pointer-events: none;
  z-index: 1000 !important;
}

.bbox-drop-target {
  pointer-events: auto;
}

.bbox-drop-target:hover {
  box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.4);
}

.bbox-drop-active {
  box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.75);
}

.tag-section {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 6px;
}

.tag-section-title {
  font-size: 0.68rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: rgba(255, 255, 255, 0.6);
}

.tag-section-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
}

.tag-section-action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 0;
  background: transparent;
  color: inherit;
  padding: 0;
  cursor: pointer;
  opacity: 0.8;
}

.tag-section-action:hover {
  opacity: 1;
}

.tag-drop-zone {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  padding: 4px;
  border-radius: 8px;
  border: 1px dashed rgba(255, 255, 255, 0.2);
  min-height: 26px;
  max-height: 154px;
  overflow-y: auto;
}

.tag-list > .tag-section:first-of-type .tag-drop-zone {
  max-height: 242px;
}

.tag-drop-zone--active {
  border-color: rgba(255, 255, 255, 0.6);
  background: rgba(255, 255, 255, 0.08);
}

.tag-drop-placeholder {
  font-size: 0.68rem;
  color: rgba(255, 255, 255, 0.45);
}

.hand-bbox-label {
  position: absolute;
  left: 0;
  top: 0;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  font-size: 0.75rem;
  padding: 1px 4px;
  border-bottom-right-radius: 6px;
}

@media (max-width: 720px) {
  .overlay-main {
    grid-template-columns: 1fr;
  }

  .overlay-rail {
    position: absolute;
    top: auto;
    bottom: 0;
    left: 0;
    right: 0;
    width: 100%;
    height: 100px;
    flex-direction: row;
    justify-content: flex-start;
    padding: 6px 10px;
  }

  .overlay-rail.open {
    width: 100%;
  }

  .filmstrip-list {
    flex-direction: row;
    overflow-x: auto;
    overflow-y: hidden;
    width: auto;
  }

  .filmstrip-thumb {
    width: 80px;
    flex: 0 0 auto;
  }

  .filmstrip-thumb img {
    height: 100%;
    width: 100%;
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
