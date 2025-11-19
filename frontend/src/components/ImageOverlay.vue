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
                :src="`${backendUrl}/pictures/${image.id}`"
                :alt="image.description || 'Full Image'"
                class="overlay-img"
              />
            </template>
            <div class="star-overlay" v-if="image">
              <v-icon
                v-for="n in 5"
                :key="n"
                large
                :color="n <= (image?.score || 0) ? 'orange' : 'grey darken-2'"
                style="cursor: pointer"
                @click.stop="emit('set-score', image, n)"
                >mdi-star</v-icon
              >
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
import { onMounted, onUnmounted } from "vue";
import { computed, nextTick, ref, toRefs, watch } from "vue";
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
  "set-score",
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
    if (image.value) emit("set-score", image.value, score);
  }
}

onMounted(() => {
  window.addEventListener("keydown", handleKeydown);
});
onUnmounted(() => {
  window.removeEventListener("keydown", handleKeydown);
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
</style>
