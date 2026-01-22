<template>
  <div v-if="open" class="image-overlay" @click.self="handleBackdropClick">
    <div
      class="overlay-shell"
      :class="{ 'chrome-hidden': chromeHidden, 'sidebar-open': sidebarOpen }"
      @mousemove="handleUserActivity"
      @mousedown="handleUserActivity"
      @wheel.passive="handleUserActivity"
      @touchstart.passive="handleUserActivity"
    >
      <header class="overlay-topbar" :class="{ hidden: chromeHidden }">
        <button class="overlay-close" @click="emit('close')" aria-label="Close">
          <v-icon>mdi-close</v-icon>
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
        <div class="overlay-top-actions">
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

      <div class="overlay-main">
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
              <template v-if="showFaceBbox">
                <div v-if="faceBboxes.length === 0" class="face-bbox-empty">
                  No face bboxes found
                </div>
                <div
                  v-for="(face, idx) in faceBboxes"
                  :key="idx"
                  class="face-bbox-overlay"
                  :style="{
                    border: `1px solid ${faceBoxColor(idx)}`,
                    background: `${faceBoxColor(idx)}22`,
                    left: `${
                      (overlayDims.offsetX || 0) +
                        (face.bbox[0] * overlayDims.width) /
                          overlayDims.naturalWidth || 0
                    }px`,
                    top: `${
                      (overlayDims.offsetY || 0) +
                        (face.bbox[1] * overlayDims.height) /
                          overlayDims.naturalHeight || 0
                    }px`,
                    width: `${
                      ((face.bbox[2] - face.bbox[0]) * overlayDims.width) /
                        overlayDims.naturalWidth || 0
                    }px`,
                    height: `${
                      ((face.bbox[3] - face.bbox[1]) * overlayDims.height) /
                        overlayDims.naturalHeight || 0
                    }px`,
                  }"
                >
                  <span class="face-bbox-label">
                    {{ face.character_name || `Face ${idx + 1}` }}
                  </span>
                </div>
              </template>
            </div>
          </div>

          <div
            class="star-overlay"
            v-if="image"
            :class="{ hidden: chromeHidden }"
          >
            <v-icon
              v-for="n in 5"
              :key="n"
              large
              :color="n <= (image?.score || 0) ? 'orange' : 'grey darken-2'"
              style="cursor: pointer"
              @click.stop="setScore(n)"
              >mdi-star</v-icon
            >
          </div>

          <button
            class="face-bbox-toggle"
            type="button"
            title="Toggle face bounding boxes"
            aria-label="Toggle face bounding boxes"
            @click.stop="toggleFaceBbox"
            :class="{ hidden: chromeHidden }"
          >
            <v-icon size="20">mdi-account</v-icon>
          </button>

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
              :key="item.id"
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

        <aside class="overlay-sidebar" :class="{ open: sidebarOpen }">
          <div class="sidebar-section">
            <div class="section-header">
              <span>Description</span>
              <span class="section-meta">
                {{ descriptionDraft.length }}
              </span>
            </div>
            <div class="description-editor">
              <textarea
                ref="descriptionEditorRef"
                v-model="descriptionDraft"
                :readonly="!isEditingDescription"
                @keydown.enter.prevent="
                  isEditingDescription && saveDescription()
                "
                @keydown="handleDescriptionEditorKey"
                @blur="isEditingDescription && cancelEditDescription()"
              ></textarea>
              <div class="description-actions">
                <button
                  class="overlay-icon-btn"
                  type="button"
                  title="Copy description"
                  :disabled="!canCopyDescription"
                  @click.stop="copyDescription"
                >
                  <v-icon size="18">
                    {{
                      descriptionCopyState === "copied"
                        ? "mdi-check-bold"
                        : "mdi-content-copy"
                    }}
                  </v-icon>
                </button>
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
                <button
                  v-else
                  class="overlay-icon-btn"
                  type="button"
                  title="Edit description"
                  :disabled="!image"
                  @click.stop="startEditDescription"
                >
                  <v-icon size="18">mdi-pencil</v-icon>
                </button>
              </div>
            </div>
          </div>

          <div class="sidebar-section">
            <div class="section-header">Tags</div>
            <div class="tag-list">
              <span
                v-for="tag in image?.tags || []"
                :key="tag"
                class="overlay-tag"
              >
                {{ tag }}
                <button
                  class="tag-delete-btn"
                  @click.stop="removeTag(tag)"
                  title="Remove tag"
                >
                  ×
                </button>
              </span>
              <button
                v-if="image"
                class="tag-add-btn"
                @click.stop="beginAddTag"
                title="Add tag"
              >
                +
              </button>
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

          <div class="sidebar-section">
            <div class="section-header">Metadata</div>
            <div v-if="!metadataEntries.length" class="metadata-empty">
              No metadata available
            </div>
            <div v-else class="metadata-list">
              <div
                v-for="entry in metadataEntries"
                :key="entry.key"
                class="metadata-row"
              >
                <div class="metadata-key">{{ entry.key }}</div>
                <div class="metadata-value">
                  <span v-if="isPrimitiveValue(entry.value)">{{
                    entry.value
                  }}</span>
                  <pre v-else>{{ stringifyMetadata(entry.value) }}</pre>
                </div>
                <button
                  class="metadata-copy"
                  type="button"
                  @click.stop="copyMetadataValue(entry.value)"
                  aria-label="Copy metadata value"
                  title="Copy"
                >
                  <v-icon size="16">mdi-content-copy</v-icon>
                </button>
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
import { isSupportedVideoFile, getOverlayFormat } from "../utils/media.js";
import { apiClient } from "../utils/apiClient";

const props = defineProps({
  open: { type: Boolean, default: false },
  initialImage: { type: Object, default: null },
  allImages: { type: Array, default: () => [] },
  backendUrl: { type: String, required: true },
});

const { open, initialImage, allImages, backendUrl } = toRefs(props);

const image = ref(null);
const sidebarOpen = ref(false);
const filmstripOpen = ref(false);
const chromeHidden = ref(false);
const zoomMode = ref("fit");
const zoomSteps = ["fit", 1, 1.5, 2];
const pan = reactive({ x: 0, y: 0 });
const isPanning = ref(false);
const lastPointer = ref({ x: 0, y: 0 });
const idleTimeoutMs = 1400;
let chromeIdleTimer = null;

// Watch for changes to initialImage and update local image copy
watch(
  () => initialImage.value,
  (newImg) => {
    image.value = newImg ? { ...newImg } : null;
    zoomMode.value = "fit";
    resetPan();
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

const hasTags = computed(() => {
  return !!(
    image.value &&
    Array.isArray(image.value.tags) &&
    image.value.tags.length
  );
});

watch(open, (value) => {
  if (!value) {
    resetTagInput();
    chromeHidden.value = false;
    if (chromeIdleTimer) {
      clearTimeout(chromeIdleTimer);
      chromeIdleTimer = null;
    }
  }
});

function normalizePictureFormat(target) {
  if (!target || !target.format) return "";
  return String(target.format).trim().toLowerCase();
}

function getFullImageUrl(targetImage = null) {
  const data = targetImage || image.value;
  if (!data || !data.id) return "";
  const ext = normalizePictureFormat(data);
  const suffix = ext ? `.${ext}` : "";
  const cacheBuster = data.pixel_sha ? `?v=${data.pixel_sha}` : "";
  return `${backendUrl.value}/pictures/${data.id}${suffix}${cacheBuster}`;
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
  if (
    image.value &&
    Array.isArray(image.value.tags) &&
    image.value.tags.includes(trimmed)
  ) {
    cancelAddTag();
    return;
  }
  emit("add-tag", image.value.id, trimmed);
  if (image.value && Array.isArray(image.value.tags)) {
    image.value.tags.push(trimmed);
    image.value.tags.sort(); // Ensure tags remain sorted
  }
  resetTagInput();
}

function setScore(n) {
  if (!image.value) return;
  image.value.score = (image.value.score || 0) === n ? 0 : n;
  emit("apply-score", image.value, image.value.score);
}

function showPrevImage() {
  const sorted = allImages.value;
  if (!image.value || !sorted.length) return;
  const idx = sorted.findIndex((i) => i.id === image.value.id);
  if (idx === -1) return;
  const prevIdx = (idx - 1 + sorted.length) % sorted.length;
  image.value = sorted[prevIdx];
}

function selectImageByIndex(idx) {
  if (!Array.isArray(allImages.value)) return;
  const target = allImages.value[idx];
  if (target) {
    image.value = target;
    resetPan();
  }
}

function showNextImage() {
  const sorted = allImages.value;
  if (!image.value || !sorted.length) return;
  const idx = sorted.findIndex((i) => i.id === image.value.id);
  if (idx === -1) return;
  const nextIdx = (idx + 1) % sorted.length;
  image.value = sorted[nextIdx];
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
    if (sidebarOpen.value) {
      sidebarOpen.value = false;
      return;
    }
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
const touchStart = ref({ x: 0, y: 0, time: 0 });
const touchLatest = ref({ x: 0, y: 0 });
const swipeHintVisible = ref(false);
let swipeHintTimer = null;

function updateIsMobile() {
  if (typeof window !== "undefined") {
    isMobile.value = window.innerWidth <= MOBILE_BREAKPOINT;
  }
}

function showSwipeHint() {
  if (!isMobile.value) return;
  swipeHintVisible.value = true;
  if (swipeHintTimer) {
    clearTimeout(swipeHintTimer);
  }
  swipeHintTimer = window.setTimeout(() => {
    swipeHintVisible.value = false;
  }, 2000);
}

function handleBackdropClick() {
  emit("close");
}

function handleUserActivity() {
  chromeHidden.value = false;
  if (sidebarOpen.value) return;
  if (chromeIdleTimer) {
    clearTimeout(chromeIdleTimer);
  }
  chromeIdleTimer = window.setTimeout(() => {
    chromeHidden.value = true;
  }, idleTimeoutMs);
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

function toggleFilmstrip() {
  filmstripOpen.value = !filmstripOpen.value;
}

function openFilmstrip() {
  if (!isMobile.value) filmstripOpen.value = true;
}

function closeFilmstrip() {
  if (!isMobile.value) filmstripOpen.value = false;
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
  const indices = [];
  for (let offset = -2; offset <= 2; offset += 1) {
    const idx = currentIndex + offset;
    if (idx < 0 || idx >= images.length) continue;
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
  );
  image.value = image.value ? { ...image.value } : null;
}

const imgRef = ref(null);
const videoRef = ref(null);
const mediaInnerRef = ref(null);
const overlayDims = ref({
  width: 1,
  height: 1,
  naturalWidth: 1,
  naturalHeight: 1,
  offsetX: 0,
  offsetY: 0,
});

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
  }
}

watch(image, () => nextTick(updateOverlayDims));

onMounted(() => {
  updateIsMobile();
  window.addEventListener("resize", updateIsMobile);
  window.addEventListener("keydown", handleKeydown);
  window.addEventListener("resize", updateDescriptionScrollState);
  nextTick(updateDescriptionScrollState);
});
onUnmounted(() => {
  window.removeEventListener("resize", updateIsMobile);
  window.removeEventListener("keydown", handleKeydown);
  window.removeEventListener("resize", updateDescriptionScrollState);
  if (swipeHintTimer) {
    clearTimeout(swipeHintTimer);
    swipeHintTimer = null;
  }
  if (chromeIdleTimer) {
    clearTimeout(chromeIdleTimer);
    chromeIdleTimer = null;
  }
  resetCopyState();
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
        console.log("Processing face:", face);
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
  } catch (e) {
    console.error("Error in fetchFaceBboxes:", e);
    faceBboxes.value = [];
  }
}

// Watch for image changes and fetch bboxes
watch(
  () => image.value?.id,
  (newId) => {
    if (newId) fetchFaceBboxes(newId);
    else faceBboxes.value = [];
    resetPan();
  },
  { immediate: true },
);

function handleTagBackspace(event) {
  if (event.key !== "Backspace") return;
  if (newTag.value.trim()) return;
  const tags = image.value?.tags || [];
  if (!tags.length) return;
  removeTag(tags[tags.length - 1]);
}

const metadataEntries = computed(() => {
  const base = normalizeMetadata(image.value?.metadata);
  const entries = Object.entries(base || {});
  return entries.map(([key, value]) => ({ key, value }));
});

function normalizeMetadata(input) {
  if (!input || typeof input !== "object") return {};
  const output = {};
  Object.entries(input).forEach(([key, value]) => {
    output[key] = parseMetadataValue(value);
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

// Add this helper below your script setup imports
function faceBoxColor(idx) {
  // Pick from a palette, cycle if more faces than colors
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

function updateDescriptionScrollState() {
  const el = descriptionRef.value;
  if (!el) {
    descriptionScrollMeta.hasOverflow = false;
    return;
  }

  descriptionScrollMeta.hasOverflow = false; // Disable overflow logic
}

function handleDescriptionScroll() {
  updateDescriptionScrollState();
}

const descriptionScrollClasses = computed(() => {
  return {
    "has-overflow": descriptionScrollMeta.hasOverflow,
  };
});

function startEditDescription() {
  if (!image.value) return;
  syncDescriptionDraft();
  isEditingDescription.value = true;
  nextTick(() => {
    if (descriptionEditorRef.value) {
      descriptionEditorRef.value.focus();
      descriptionEditorRef.value.select?.();
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

function selectAllText() {
  const input = descriptionEditorRef.value;
  if (input) {
    input.select();
  }
}

function removeTag(tag) {
  if (!image.value || !Array.isArray(image.value.tags)) return;
  const index = image.value.tags.indexOf(tag);
  if (index !== -1) {
    image.value.tags.splice(index, 1);
    emit("remove-tag", image.value.id, tag); // Notify parent component
  }
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
}

.overlay-shell.sidebar-open {
  --sidebar-width: 320px;
}

.overlay-topbar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  background: rgba(0, 0, 0, 0.45);
  backdrop-filter: blur(6px);
  color: #fff;
  transition: opacity 0.2s ease;
  z-index: 5;
}

.overlay-topbar.hidden {
  opacity: 0;
  pointer-events: none;
}

.overlay-close {
  border: none;
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
  width: 36px;
  height: 36px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
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

.overlay-icon-btn {
  border: none;
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
  width: 32px;
  height: 32px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}

.zoom-btn {
  width: auto;
  min-width: 84px;
  padding: 0 10px;
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
  grid-template-columns: 1fr auto auto;
  height: 100%;
  min-height: 0;
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

.star-overlay {
  position: absolute;
  top: 12px;
  right: 12px;
  z-index: 3;
  display: flex;
  gap: 2px;
  padding: 4px 6px;
  background: rgba(255, 255, 255, 0.75);
  border-radius: 6px;
}

.star-overlay.hidden {
  opacity: 0;
  pointer-events: none;
}

.face-bbox-toggle {
  position: absolute;
  top: 12px;
  left: 12px;
  border: none;
  width: 34px;
  height: 34px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.15);
  color: #fff;
  cursor: pointer;
  z-index: 3;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.face-bbox-toggle.hidden {
  opacity: 0;
  pointer-events: none;
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
  width: var(--rail-open-width);
  background: rgba(12, 12, 12, 0.6);
  border-left: 1px solid rgba(255, 255, 255, 0.08);
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 8px 6px;
  transition: opacity 0.2s ease;
  overflow: hidden;
}

.overlay-rail.hidden {
  opacity: 0;
  pointer-events: none;
}

.filmstrip-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  overflow-y: auto;
  width: 100%;
  padding-right: 4px;
}

.filmstrip-thumb {
  border: none;
  padding: 0;
  background: transparent;
  cursor: pointer;
  border-radius: 8px;
  overflow: hidden;
  border: 2px solid transparent;
  width: 100%;
  aspect-ratio: 1 / 1;
}

.filmstrip-thumb.active {
  border-color: rgba(255, 183, 77, 0.95);
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
  color: rgba(255, 255, 255, 0.65);
}

.overlay-sidebar {
  width: var(--sidebar-width);
  background: rgba(18, 18, 18, 0.92);
  color: #fff;
  transition: width 0.2s ease;
  overflow: hidden;
  padding: 0;
}

.overlay-sidebar.open {
  padding: 16px;
}

.sidebar-section {
  margin-bottom: 20px;
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 600;
  margin-bottom: 8px;
  color: #fff;
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
  padding: 8px;
  resize: vertical;
}

.description-actions {
  margin-top: 8px;
  display: flex;
  gap: 8px;
}

.tag-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.overlay-tag {
  display: inline-flex;
  align-items: center;
  background: rgba(255, 255, 255, 0.1);
  color: #fff;
  border-radius: 999px;
  padding: 2px 10px;
  font-size: 0.8rem;
}

.tag-delete-btn {
  margin-left: 6px;
  background: transparent;
  border: none;
  color: inherit;
  cursor: pointer;
}

.tag-add-btn {
  width: 28px;
  height: 28px;
  border-radius: 999px;
  border: none;
  background: rgba(255, 255, 255, 0.2);
  color: #fff;
  cursor: pointer;
}

.tag-add-input {
  background: rgba(0, 0, 0, 0.4);
  border: 1px solid rgba(255, 255, 255, 0.2);
  color: #fff;
  border-radius: 999px;
  padding: 4px 10px;
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

@media (max-width: 720px) {
  .overlay-main {
    grid-template-columns: 1fr;
  }

  .overlay-rail {
    position: absolute;
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
    top: 0;
    right: 0;
    height: 100%;
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
