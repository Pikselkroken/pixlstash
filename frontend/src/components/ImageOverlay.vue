<template>
  <div v-if="open" class="image-overlay" @click.self="emit('close')">
    <div class="overlay-content overlay-grid">
      <!-- Title Row -->
      <button class="overlay-close" @click="emit('close')" aria-label="Close">
        &times;
      </button>
      <div class="overlay-title-row">
        <span class="overlay-title-desc">{{
          image?.description || "No description"
        }}</span>
      </div>
      <!-- Image Row -->
      <div class="overlay-img-row">
        <div class="overlay-img-wrapper">
          <div style="position: relative; display: inline-block">
            <template v-if="image">
              <video
                v-if="isSupportedVideoFile(getOverlayFormat(image))"
                :src="`${backendUrl}/pictures/${image.id}`"
                class="overlay-video"
                controls
                preload="auto"
                playsinline
                style="background: #111"
              ></video>
              <img
                v-else
                ref="imgRef"
                :src="`${backendUrl}/pictures/${image.id}`"
                :alt="image.description || 'Full Image'"
                class="overlay-img"
                @load="updateOverlayDims"
              />
              <!-- Face bbox overlay -->
              <template v-if="showFaceBbox && parsedFaceBbox">
                <div
                  class="face-bbox-overlay"
                  :style="{
                    position: 'absolute',
                    border: '2px solid #ff5252',
                    background: 'rgba(255, 82, 82, 0.15)',
                    left: `${(parsedFaceBbox[0] * overlayDims.width / overlayDims.naturalWidth) || 0}px`,
                    top: `${(parsedFaceBbox[1] * overlayDims.height / overlayDims.naturalHeight) || 0}px`,
                    width: `${((parsedFaceBbox[2] - parsedFaceBbox[0]) * overlayDims.width / overlayDims.naturalWidth) || 0}px`,
                    height: `${((parsedFaceBbox[3] - parsedFaceBbox[1]) * overlayDims.height / overlayDims.naturalHeight) || 0}px`,
                    pointerEvents: 'auto',
                    zIndex: 1000,
                    display: 'block',
                  }"
                ></div>
              </template>
              <!-- Facial features overlay -->
              <template v-if="showFacialFeatures && parsedFacialFeatures && parsedFacialFeatures.length">
                <div v-for="pt in transformedFacialFeatures" :key="pt.idx"
                  class="facial-feature-point"
                  :style="{
                    position: 'absolute',
                    left: `${pt.px || 0}px`,
                    top: `${pt.py || 0}px`,
                    width: '6px',
                    height: '6px',
                    background: pt.inRange ? '#42a5f5' : '#ff5252',
                    borderRadius: '50%',
                    zIndex: 21,
                    pointerEvents: 'none',
                    border: pt.inRange ? 'none' : '2px solid #fff',
                  }"
                  :title="`idx=${pt.idx} norm=(${pt.norm[0]},${pt.norm[1]}) inRange=${pt.inRange}`"
                ></div>
              </template>
              <!-- No overlay if features not available -->
              <template v-else></template>
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
            <div style="position: absolute; left: 8px; top: 8px; z-index: 30; display: flex; flex-direction: column; gap: 4px;">
              <button @click.stop="toggleFaceBbox" style="background: #fff2; color: #ff5252; border: none; border-radius: 4px; padding: 2px 8px; cursor: pointer; font-size: 0.95em;">BBox</button>
              <button @click.stop="toggleFacialFeatures" style="background: #fff2; color: #42a5f5; border: none; border-radius: 4px; padding: 2px 8px; cursor: pointer; font-size: 0.95em;">Features</button>
            </div>
          </div>
        </div>
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
      <div v-if="hasTags" class="overlay-tags-row">
        <span v-for="tag in image?.tags || []" :key="tag" class="overlay-tag">
          {{ tag }}
          <button
            class="tag-delete-btn"
            @click.stop="emit('remove-tag', tag)"
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
          "
          placeholder="New tag"
          autofocus
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref, computed, nextTick, toRefs, watch } from "vue";
import { isSupportedVideoFile, getOverlayFormat } from "../utils/media.js";

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
  { immediate: true }
);

const emit = defineEmits([
  "close",
  "prev",
  "next",
  "apply-score",
  "remove-tag",
  "add-tag",
]);

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

watch(image, () => {
  resetTagInput();
});

function resetTagInput() {
  addingTag.value = false;
  newTag.value = "";
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
  emit("add-tag", trimmed);
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
const showFacialFeatures = ref(false);

function toggleFaceBbox() {
  showFaceBbox.value = !showFaceBbox.value;
  // Instrumentation
  console.log('[ImageOverlay] Toggled showFaceBbox:', showFaceBbox.value, image.value?.face_bbox);
  image.value = image.value ? { ...image.value } : null;
}
function toggleFacialFeatures() {
  showFacialFeatures.value = !showFacialFeatures.value;
  // Instrumentation
  console.log('[ImageOverlay] Toggled showFacialFeatures:', showFacialFeatures.value, image.value?.facial_features);
  image.value = image.value ? { ...image.value } : null;
}

const imgRef = ref(null);
const overlayDims = ref({ width: 1, height: 1, naturalWidth: 1, naturalHeight: 1 });

function updateOverlayDims() {
  if (imgRef.value) {
    overlayDims.value.width = imgRef.value.clientWidth;
    overlayDims.value.height = imgRef.value.clientHeight;
    overlayDims.value.naturalWidth = imgRef.value.naturalWidth;
    overlayDims.value.naturalHeight = imgRef.value.naturalHeight;
    console.log('[ImageOverlay] updateOverlayDims', {
      width: overlayDims.value.width,
      height: overlayDims.value.height,
      naturalWidth: overlayDims.value.naturalWidth,
      naturalHeight: overlayDims.value.naturalHeight,
    });
  }
}

watch(image, () => nextTick(updateOverlayDims));

onMounted(() => {
  window.addEventListener("keydown", handleKeydown);
});
onUnmounted(() => {
  window.removeEventListener("keydown", handleKeydown);
});

const parsedFaceBbox = computed(() => {
  if (!image.value || !image.value.face_bbox) return null;
  if (Array.isArray(image.value.face_bbox) && image.value.face_bbox.length === 4) {
    return image.value.face_bbox;
  }
  // Try to parse if it's a string
  if (typeof image.value.face_bbox === 'string') {
    try {
      const arr = JSON.parse(image.value.face_bbox);
      if (Array.isArray(arr) && arr.length === 4) return arr;
    } catch (e) {}
  }
  return null;
});

const parsedFacialFeatures = computed(() => {
  if (!image.value || !image.value.facial_features) return null;
  // If already an array of arrays (points)
  if (Array.isArray(image.value.facial_features) && Array.isArray(image.value.facial_features[0])) {
    console.log('[ImageOverlay] facial_features is array of points', image.value.facial_features);
    return image.value.facial_features;
  }
  // Try to parse if it's a string
  if (typeof image.value.facial_features === 'string') {
    try {
      const arr = JSON.parse(image.value.facial_features);
      if (Array.isArray(arr) && Array.isArray(arr[0])) {
        console.log('[ImageOverlay] facial_features parsed from string', arr);
        return arr;
      }
      console.log('[ImageOverlay] facial_features string parsed but not array of points', arr);
    } catch (e) {
      console.log('[ImageOverlay] facial_features string parse error', e, image.value.facial_features);
    }
  }
  console.log('[ImageOverlay] facial_features not usable', image.value.facial_features);
  return null;
});

// Transform facial feature points from normalized face_bbox coordinates to overlay pixel coordinates
const transformedFacialFeatures = computed(() => {
  const features = parsedFacialFeatures.value;
  const { width, height, naturalWidth, naturalHeight } = overlayDims.value;
  const bbox = parsedFaceBbox.value;
  if (!features || !naturalWidth || !naturalHeight || !width || !height || !bbox) return [];
  // bbox: [x0, y0, x1, y1] in natural image coordinates
  const [x0, y0, x1, y1] = bbox;
  const bboxW = x1 - x0;
  const bboxH = y1 - y0;
  // Detect range: if any x or y < 0, assume [-1,1], else [0,1]
  const isMinusOneToOne = features.some(pt => pt[0] < 0 || pt[1] < 0);
  return features.map(([x, y], idx) => {
    let fx, fy, inRange = true;
    if (isMinusOneToOne) {
      inRange = x >= -1 && x <= 1 && y >= -1 && y <= 1;
      fx = x0 + ((x + 1) / 2) * bboxW;
      fy = y0 + ((y + 1) / 2) * bboxH;
    } else {
      inRange = x >= 0 && x <= 1 && y >= 0 && y <= 1;
      fx = x0 + x * bboxW;
      fy = y0 + y * bboxH;
    }
    const px = fx * (width / naturalWidth);
    const py = fy * (height / naturalHeight);
    // Console debug
    console.log(`[FacialFeature] idx=${idx} norm=(${x},${y}) inRange=${inRange} mapped=(${px},${py})`);
    return { px, py, inRange, norm: [x, y], idx };
  });
});
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
  background: rgba(0, 0, 0, 0.8);
  border-radius: 0px;
  box-shadow: 0 2px 16px rgba(0, 0, 0, 0.5);
  padding: 12px 12px 8px 12px;
  align-items: center;
  justify-items: center;
  overflow-y: auto;
}

.overlay-title-row {
  width: 90%;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  min-height: 32px;
  max-height: 72px;
  z-index: 2;
}
.overlay-title-desc {
  flex: 1;
  color: #eee;
  font-size: 1.25rem;
  text-align: center;
  word-break: break-word;
  padding-right: 48px;
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
  max-height: 80vh;
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

.overlay-desc {
  color: #eee;
  margin-top: 12px;
  text-align: center;
  max-width: 90vw;
  word-break: break-word;
  font-size: 1.25rem;
  overflow: auto;
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
  background-color: #2581a2;
  color: #ffffff;
  border-radius: 16px;
  padding: 4px 12px 4px 12px;
  height: 32px;
  margin: 2px 2px 2px 2px;
  font-size: 1.15em;
  position: relative;
}
.tag-delete-btn {
  background: transparent;
  border: none;
  color: #fff;
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
  background-color: #4caf50;
  color: #ffffff;
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
.facial-feature-point {
  pointer-events: none;
}
</style>
