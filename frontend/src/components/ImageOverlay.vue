<template>
  <div v-if="open" class="image-overlay" @click.self="emit('close')">
    <div class="overlay-content overlay-grid">
      <button
        class="overlay-close"
        @click="emit('close')"
        aria-label="Close"
        style="position: absolute; top: 12px; right: 18px; z-index: 20"
      >
        &times;
      </button>
      <div
        class="overlay-grid-main"
        style="
          display: grid;
          grid-template-columns: 64px 1fr 64px;
          align-items: center;
          width: 100%;
          height: 100%;
        "
      >
        <div
          style="
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100%;
          "
        >
          <button
            class="overlay-nav overlay-nav-left"
            @click.stop="emit('prev')"
            aria-label="Previous"
          >
            <v-icon>mdi-skip-previous</v-icon>
          </button>
        </div>
        <div class="overlay-img-wrapper">
          <div style="position: relative; display: inline-block">
            <template v-if="image">
              <video
                v-if="isVideo"
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
                @click.stop="emit('set-score', n)"
                >mdi-star</v-icon
              >
            </div>

          </div>
        </div>
        <div
          style="
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100%;
          "
        >
          <button
            class="overlay-nav overlay-nav-right"
            @click.stop="emit('next')"
            aria-label="Next"
          >
            <v-icon>mdi-skip-next</v-icon>
          </button>
        </div>
      </div>
      <div class="overlay-desc">
        {{ image?.description || "No description" }}
      </div>
      <div
        v-if="hasTags"
        class="overlay-tags"
        style="margin-top: 8px; margin-bottom: 0; text-align: center"
      >
        <span
          v-for="tag in image?.tags || []"
          :key="tag"
          class="overlay-tag"
          style="
            display: inline-flex;
            align-items: center;
            background: #eee;
            color: #333;
            border-radius: 16px;
            padding: 4px 16px 4px 14px;
            margin: 2px 2px;
            font-size: 1.15em;
            position: relative;
            min-height: 32px;
          "
        >
          {{ tag }}
          <button
            class="tag-delete-btn"
            @click.stop="emit('remove-tag', tag)"
            title="Remove tag"
            style="
              background: none;
              border: none;
              color: #888;
              font-size: 1.25em;
              margin-left: 10px;
              cursor: pointer;
              display: flex;
              align-items: center;
              justify-content: center;
              height: 24px;
              width: 24px;
              padding: 0;
            "
          >
            ×
          </button>
        </span>
        <button
          v-if="image"
          class="tag-add-btn"
          @click.stop="beginAddTag"
          title="Add tag"
          style="
            display: inline-flex;
            align-items: center;
            justify-content: center;
            background: #e0e0e0;
            color: #333;
            border: none;
            border-radius: 16px;
            font-size: 1.3em;
            margin: 2px 2px;
            height: 32px;
            width: 32px;
            cursor: pointer;
            padding: 0;
            vertical-align: middle;
          "
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
import { computed, nextTick, ref, toRefs, watch } from "vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  image: { type: Object, default: null },
  backendUrl: { type: String, required: true },
  isVideo: { type: Boolean, default: false },
});

const { open, image, backendUrl, isVideo } = toRefs(props);

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
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: rgba(117, 117, 117, 0.9);
  border-radius: 8px;
  box-shadow: 0 2px 16px rgba(0, 0, 0, 0.5);
  padding: 24px 24px 16px 24px;
}

.overlay-grid {
  display: grid;
  grid-template-rows: auto 1fr auto auto;
  grid-template-columns: 1fr;
  width: 90vw;
  min-width: 320px;
  max-width: 95vw;
  max-height: 90vh;
  border-radius: 8px;
  box-shadow: 0 2px 16px rgba(0, 0, 0, 0.5);
  padding: 24px 24px 16px 24px;
  align-items: center;
  justify-items: center;
  position: relative;
  overflow-y: auto;
}

.overlay-grid-main {
  display: grid;
  grid-template-columns: 56px 1fr 56px;
  grid-template-rows: 1fr;
  align-items: center;
  width: 100%;
  height: 100%;
}

.overlay-img-wrapper {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 70vh;
  max-width: 100%;
  min-height: 256px;
}

.overlay-img-container {
  height: 90%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}

.overlay-img {
  max-width: 100%;
  max-height: 70vh;
  min-height: 256px;
  object-fit: contain;
  border-radius: 8px;
  background: #111;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
}

.overlay-video {
  max-width: 100%;
  max-height: 70vh;
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
  max-width: 70vw;
  word-break: break-word;
  font-size: 1.1rem;
}

.overlay-nav {
  position: absolute;
  top: 50%;
  font-size: 2.5rem;
  color: #444;
  background: rgba(255, 255, 255, 0.7);
  max-width: 52px;
  max-height: 52px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  user-select: none;
  z-index: 1200;
}

.overlay-nav-left {
  left: 12px;
}

.overlay-nav-right {
  right: 12px;
}

.overlay-nav:hover {
  background: #fff;
  color: orange;
}
</style>
