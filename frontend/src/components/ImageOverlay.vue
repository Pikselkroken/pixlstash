<template>
  <div v-if="open" class="image-overlay" @click.self="emit('close')">
    <div class="overlay-content overlay-grid">
      <!-- Title Row -->
      <button class="overlay-close" @click="emit('close')" aria-label="Close">
        &times;
      </button>
      <div class="overlay-title-row">
        <div
          class="overlay-title-desc-shell"
          :class="{ editing: isEditingDescription }"
        >
          <textarea
            ref="descriptionEditorRef"
            v-model="descriptionDraft"
            class="overlay-title-desc"
            :readonly="!isEditingDescription"
            @keydown.enter.prevent="isEditingDescription && saveDescription()"
            @blur="isEditingDescription && cancelEditDescription()"
          ></textarea>
        </div>
        <div class="overlay-title-actions">
          <button
            class="title-action-btn"
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
              class="title-action-btn"
              type="button"
              title="Save description"
              :disabled="isSavingDescription"
              @click.stop="saveDescription"
            >
              <v-icon size="18" :class="{ 'mdi-spin': isSavingDescription }">
                {{ isSavingDescription ? "mdi-loading" : "mdi-content-save" }}
              </v-icon>
            </button>
            <button
              class="title-action-btn"
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
            class="title-action-btn"
            type="button"
            title="Edit description"
            :disabled="!image"
            @click.stop="startEditDescription"
          >
            <v-icon size="18">mdi-pencil</v-icon>
          </button>
        </div>
      </div>
      <!-- Image Row -->
      <div
        class="overlay-img-row"
        @touchstart="onTouchStart"
        @touchmove="onTouchMove"
        @touchend="onTouchEnd"
      >
        <div class="overlay-img-wrapper">
          <div style="position: relative; display: inline-block">
            <template v-if="image">
              <video
                v-if="isSupportedVideoFile(getOverlayFormat(image))"
                ref="videoRef"
                :src="getFullImageUrl(image)"
                class="overlay-video"
                controls
                preload="auto"
                playsinline
                style="background: #111"
                @loadedmetadata="updateOverlayDims"
              ></video>
              <img
                v-else
                ref="imgRef"
                :src="getFullImageUrl(image)"
                :alt="image.description || 'Full Image'"
                class="overlay-img"
                @load="updateOverlayDims"
              />
              <!-- Multiple face bbox overlays -->
              <template v-if="showFaceBbox">
                <div
                  v-if="faceBboxes.length === 0"
                  style="
                    position: absolute;
                    left: 8px;
                    top: 8px;
                    color: #ff5252;
                    background: #fff2;
                    z-index: 1001;
                    font-size: 0.95em;
                    padding: 2px 8px;
                    border-radius: 4px;
                  "
                >
                  No face bboxes found
                </div>
                <div
                  v-for="(face, idx) in faceBboxes"
                  :key="idx"
                  class="face-bbox-overlay"
                  :style="{
                    position: 'absolute',
                    border: `1px solid ${faceBoxColor(idx)}`,
                    background: `${faceBoxColor(idx)}22`,
                    left: `${
                      (face.bbox[0] * overlayDims.width) /
                        overlayDims.naturalWidth || 0
                    }px`,
                    top: `${
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
                    pointerEvents: 'auto',
                    zIndex: 1000,
                    display: 'block',
                  }"
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
                    {{ face.character_name || `Face ${idx + 1}` }}
                  </span>
                </div>
              </template>
            </template>
            <div class="star-overlay" v-if="image">
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
            <!-- Toggle buttons -->
            <div
              style="
                position: absolute;
                left: 8px;
                top: 8px;
                z-index: 30;
                display: flex;
                flex-direction: column;
                gap: 4px;
              "
            >
              <button
                @click.stop="toggleFaceBbox"
                style="
                  background: #fff2;
                  color: #ff5252;
                  border: 1px dashed red;
                  border-radius: 4px;
                  padding: 2px 8px;
                  cursor: pointer;
                  font-size: 1.2em;
                  display: flex;
                  align-items: center;
                  justify-content: center;
                  min-width: 32px;
                  min-height: 32px;
                "
              >
                <v-icon size="24" style="color: white">mdi-account</v-icon>
              </button>
            </div>
          </div>
        </div>
      </div>
      <div v-if="swipeHintVisible" class="overlay-swipe-hint">
        <v-icon size="18">mdi-swap-horizontal</v-icon>
        <span>Swipe to navigate</span>
      </div>
      <!-- Navigation Buttons (fixed, outside grid) -->
      <button
        class="overlay-nav overlay-nav-left"
        @click.stop="showPrevImage"
        aria-label="Previous"
      >
        <v-icon>mdi-skip-previous</v-icon>
      </button>
      <button
        class="overlay-nav overlay-nav-right"
        @click.stop="showNextImage"
        aria-label="Next"
      >
        <v-icon>mdi-skip-next</v-icon>
      </button>
      <!-- Tag Row -->
      <div class="overlay-tags-row">
        <span v-for="tag in image?.tags || []" :key="tag" class="overlay-tag">
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
          @blur="cancelAddTag"
          class="tag-add-input"
          style="
            margin-left: 8px;
            font-size: 1.1em;
            border-radius: 8px;
            border: 1px solid #bbb;
            padding: 2px 8px;
            min-width: 80px;
            outline: none;
            background: rgba(
              255,
              255,
              0,
              0.8
            ); /* Bright semi-opaque background */
            color: #000; /* Visible text color */
          "
          placeholder="New tag"
          autofocus
        />
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

// Watch for changes to initialImage and update local image copy
watch(
  () => initialImage.value,
  (newImg) => {
    image.value = newImg ? { ...newImg } : null;
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
const overlayDims = ref({
  width: 1,
  height: 1,
  naturalWidth: 1,
  naturalHeight: 1,
});

function updateOverlayDims() {
  if (imgRef.value) {
    overlayDims.value.width = imgRef.value.clientWidth;
    overlayDims.value.height = imgRef.value.clientHeight;
    overlayDims.value.naturalWidth = imgRef.value.naturalWidth;
    overlayDims.value.naturalHeight = imgRef.value.naturalHeight;
  } else if (videoRef.value) {
    overlayDims.value.width = videoRef.value.clientWidth;
    overlayDims.value.height = videoRef.value.clientHeight;
    overlayDims.value.naturalWidth = videoRef.value.videoWidth;
    overlayDims.value.naturalHeight = videoRef.value.videoHeight;
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
  },
  { immediate: true },
);

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
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  background: rgba(0, 0, 0, 0.2);
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
}

.overlay-content {
  position: relative;
  display: grid;
  grid-template-rows: auto 1fr auto;
  grid-template-columns: 1fr;
  height: 100vh;
  width: 100%;
  height: 100%;
  background: rgba(0, 0, 0, 0.6);
  border-radius: 0px;
  box-shadow: 0 2px 16px rgba(0, 0, 0, 0.5);
  padding: 12px 12px 8px 12px;
  align-items: center;
  justify-items: center;
  overflow-y: auto;
}

.overlay-title-row {
  width: 70%;
  display: flex;
  background-color: #44444488;
  align-items: center;
  justify-content: center;
  position: relative;
  z-index: 2;
}
.overlay-title-desc-shell {
  flex: 1;
  width: 100%;
  padding: 4px 4px;
  padding-right: 100px;
  line-height: 1.1;
  max-height: calc(1.1em * 3 + 12px); /* 3 lines max */
  overflow-y: auto;
  border: 1px rgb(var(--v-theme-border)) dashed;
  border-radius: 4px;
  scrollbar-color: #ff9800 #2b2b2b;
  scrollbar-width: thick;
  scrollbar-gutter: stable both-edges;
}

.overlay-title-desc-shell.editing {
  border: 1px solid orange; /* Change to solid orange border when editing */
}
.overlay-title-desc {
  flex: 1;
  color: #eee;
  width: 100%;
  height: calc(1.1em * 3 + 12px); /* 3 lines max */
  font-size: 1rem;
  text-align: left;
  word-break: break-word;
  position: relative;
  display: block;
  border: none; /* Removed border to avoid double border issue */
  outline: none;
  resize: none;
}
.overlay-title-actions {
  position: absolute;
  top: 6px;
  right: 8px;
  display: flex;
  gap: 8px;
}
.title-action-btn {
  width: 22px;
  height: 22px;
  border-radius: 4px;
  background: transparent;
  border: none;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  padding: 0;
  transition:
    background 0.15s ease,
    transform 0.15s ease;
}
.title-action-btn:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.15);
  transform: translateY(-1px);
}
.title-action-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.overlay-title-desc::-webkit-scrollbar {
  width: 6px;
}
.overlay-title-desc::-webkit-scrollbar-track {
  background: #2b2b2b;
}
.overlay-title-desc::-webkit-scrollbar-thumb {
  background: #ff9800;
}
.overlay-title-desc.has-overflow {
  padding-right: 40px;
}
.overlay-title-desc.has-overflow::before,
.overlay-title-desc.has-overflow::after {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  height: 18px;
  pointer-events: none;
  opacity: 0;
  transition: opacity 0.2s ease;
}
.overlay-close {
  position: absolute;
  top: 8px;
  right: 12px;
  font-size: 2.2rem;
  color: #fff;
  background: transparent;
  border: none;
  cursor: pointer;
  z-index: 10;
  line-height: 1;
  padding: 0 8px;
  transition: color 0.2s;
}
.overlay-close:hover {
  color: #ff5252;
}

.overlay-img-row {
  position: relative;
  width: 100%;
  display: flex;
  align-items: stretch;
  justify-content: center;
  min-height: 256px;
  flex: 1 1 auto;
  height: auto;
  overflow: visible;
  z-index: 1;
}
.overlay-img-wrapper {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  vertical-align: middle;
  width: 100%;
  height: 100%;
  max-width: 100%;
  max-height: 100%;
  min-height: 256px;
  overflow: visible;
}
.overlay-img {
  max-width: 100%;
  max-height: 90vh;
  min-height: 256px;
  object-fit: contain;
  border-radius: 8px;
  background: #111;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
}
.overlay-video {
  max-width: 100%;
  max-height: 80vh;
  min-height: 256px;
  object-fit: cover;
  border-radius: 8px;
  background: #111;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
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
.overlay-nav {
  position: fixed;
  top: 50%;
  transform: translateY(-50%);
  font-size: 3rem;
  color: #eee;
  background: none;
  width: 64px;
  height: 64px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  user-select: none;
  z-index: 1200;
  border: none;
  pointer-events: auto;
}
.overlay-nav-left {
  left: 24px;
}
.overlay-nav-right {
  right: 24px;
}
.overlay-nav:hover {
  color: orange;
}

.overlay-tags-row {
  width: 100%;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: center;
  margin-top: 4px;
  margin-bottom: 0;
  text-align: center;
  vertical-align: middle;
  overflow: scroll;
  min-height: 32px;
  max-height: 72px;
  z-index: 2;
}

.overlay-img-wrapper {
  display: flex;
  align-items: center;
  justify-content: center;
  vertical-align: middle;
  max-width: 100fw;
  max-height: 100fh;
  min-height: 256px;
}

.overlay-img {
  max-width: 100fw;
  max-height: 100fh;
  min-height: 256px;
  object-fit: cover;
}

.overlay-video {
  max-width: 100fw;
  max-height: 100fh;
  min-height: 256px;
  object-fit: cover;
  border-radius: 8px;
  background: #111;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
}

.overlay-close {
  position: absolute;
  top: 8px;
  right: 12px;
  font-size: 2.2rem;
  color: #fff;
  background: transparent;
  border: none;
  cursor: pointer;
  z-index: 10;
  line-height: 1;
  padding: 0 8px;
  transition: color 0.2s;
}

.overlay-close:hover {
  color: #ff5252;
}

.overlay-nav {
  position: absolute;
  top: 50%;
  font-size: 3rem;
  color: #eee;
  background: none;
  max-width: 64px;
  max-height: 64px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  user-select: none;
  z-index: 1200;
}

.overlay-nav-left {
  left: 24px;
}

.overlay-nav-right {
  right: 24px;
}

.overlay-nav:hover {
  border: none;
  color: orange;
}

.overlay-swipe-hint {
  display: none;
}

@media (max-width: 900px) {
  .overlay-swipe-hint {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 6px 10px;
    margin: 6px auto 0;
    background: rgba(0, 0, 0, 0.55);
    color: #fff;
    border-radius: 999px;
    font-size: 0.85rem;
    z-index: 5;
  }
}

@media (max-width: 900px) {
  .overlay-nav {
    display: none;
  }
}
.overlay-tags {
  justify-content: center;
  margin-bottom: 0;
  text-align: center;
  vertical-align: middle;
  overflow: scroll;
}
.overlay-tag {
  display: inline-flex;
  align-items: center;
  vertical-align: middle;
  background-color: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
  border-radius: 6px;
  padding: 2px 6px 2px 6px;
  height: 24px;
  margin: 2px 2px 2px 2px;
  font-size: 0.8em;
  position: relative;
}
.tag-delete-btn {
  background: transparent;
  border: none;
  color: rgb(var(--v-theme-on-primary));
  font-size: 1.2em;
  vertical-align: top;
  margin-left: 8px;
  cursor: pointer;
  line-height: 1;
  padding: 0;
}
.tag-add-btn {
  display: inline-flex;
  align-items: center;
  vertical-align: middle;
  justify-content: center;
  background-color: rgb(var(--v-theme-accent));
  color: rgb(var(--v-theme-on-accent));
  border-radius: 50%;
  width: 32px;
  height: 32px;
  font-size: 1.15em;
  margin: 2px 2px 2px 2px;
  cursor: pointer;
  border: none;
  padding: 0;
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
.face-bbox-overlay {
  box-sizing: border-box;
  pointer-events: none;
  background: rgba(255, 82, 82, 0.15); /* semi-transparent red */
  z-index: 1000 !important;
}
</style>
