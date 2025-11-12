<template>
  <div
    class="image-grid"
    :style="{ gridTemplateColumns: `repeat(${columns}, 1fr)` }"
    ref="gridContainer"
    style="position: relative"
    @dragenter.prevent="handleGridDragEnter"
    @dragover.prevent="handleGridDragOver"
    @dragleave.prevent="handleGridDragLeave"
    @drop.prevent="handleGridDrop"
    @scroll="onGridScroll"
    @click="handleGridBackgroundClick"
  >
    <div v-if="images.length === 0 && !imagesLoading && !imagesError" class="empty-state">
      No images found for this character.
    </div>
    <div v-if="imagesError" class="empty-state">
      {{ imagesError }}
    </div>
    <div v-if="dragOverlayVisible" class="drag-overlay-grid">
      <span>{{ dragOverlayMessage }}</span>
    </div>
    <div
      v-for="(img, idx) in pagedImages"
      :key="img.id"
      class="image-card"
      :class="[isImageSelected(img.id) ? 'selected' : '', getSelectionBorderClasses(idx)]"
      :draggable="isImageSelected(img.id)"
      @dragstart="onImageDragStart(img, idx, $event)"
      @click="handleGridBackgroundClick"
    >
      <v-card class="thumbnail-card">
        <div class="thumbnail-container">
          <div class="star-overlay" v-if="showStars && thumbLoaded[img.id]">
            <v-icon
              v-for="n in 5"
              :key="n"
              small
              :color="n <= (img.score || 0) ? 'orange' : 'grey darken-2'"
              style="cursor: pointer"
              @click.stop="setImageScore(img, n)"
              >mdi-star</v-icon
            >
          </div>
          <template v-if="(img.format && isSupportedVideoFile(img.format)) || (!img.format && isSupportedVideoFile((img.id || '').split('.').pop()))">
            <img
              :src="`${BACKEND_URL}/thumbnails/${img.id}`"
              class="thumbnail-img video-thumb"
              @load="thumbLoaded[img.id] = true"
              @error="thumbLoaded[img.id] = true"
              @click.stop="(e) => { if (e.ctrlKey || e.metaKey || e.shiftKey) { handleImageSelect(img, idx, e); } else { openOverlay(img); } }"
              style="cursor: pointer; border: 2px solid #2196f3"
            />
            <v-icon
              class="video-icon-overlay"
              style="position: absolute; bottom: 8px; left: 8px; color: #2196f3; background: white; border-radius: 50%;"
              >mdi-play-circle</v-icon
            >
          </template>
          <template v-else>
            <img
              :src="`${BACKEND_URL}/thumbnails/${img.id}`"
              class="thumbnail-img"
              @load="thumbLoaded[img.id] = true"
              @error="thumbLoaded[img.id] = true"
              @click.stop="(e) => { if (e.ctrlKey || e.metaKey || e.shiftKey) { handleImageSelect(img, idx, e); } else { openOverlay(img); } }"
              style="cursor: pointer"
            />
          </template>
        </div>
        <div v-if="selectedSort === 'ORDER BY created_at DESC' || selectedSort === 'ORDER BY created_at ASC'" class="thumbnail-info">
          {{ new Date(img.created_at).toLocaleString() }}
        </div>
        <div v-if="selectedSort === 'search_likeness'" class="thumbnail-info">
          {{ formatLikenessScore(img.likeness_score) }}
        </div>
      </v-card>
    </div>
  </div>
</template>
<script setup>

import { defineEmits, computed, onMounted, ref } from 'vue';
const emit = defineEmits(['open-overlay', 'select-image', 'clear-selection', 'infinite-scroll']);


// Props
const props = defineProps({
  images: Array,
  imagesLoading: Boolean,
  imagesError: String,
  thumbLoaded: Object,
  thumbnailSize: Number,
  columns: Number,
  sidebarVisible: Boolean,
  selectedImageIds: Array,
  showStars: Boolean,
  selectedSort: String,
  dragOverlayVisible: Boolean,
  dragOverlayMessage: String,
  BACKEND_URL: String,
  selectedCharacterObj: Object,
  config: Object,
  extractKeywords: Function,
});

// Internal computed for pagedImages (for now, just use images)
const pagedImages = computed(() => props.images);

// Selection logic
const isImageSelected = (id) => props.selectedImageIds && props.selectedImageIds.includes(id);

const getSelectionBorderClasses = (idx) => {
  const sorted = pagedImages.value;
  if (!isImageSelected(sorted[idx]?.id)) return "";
  const cols = props.columns;
  const total = sorted.length;
  const row = Math.floor(idx / cols);
  const col = idx % cols;
  const classes = [];
  if (row === 0 || !isImageSelected(sorted[(row - 1) * cols + col]?.id)) {
    classes.push("selected-border-top");
  }
  if (
    row === Math.floor((total - 1) / cols) ||
    !isImageSelected(sorted[(row + 1) * cols + col]?.id)
  ) {
    classes.push("selected-border-bottom");
  }
  if (col === 0 || !isImageSelected(sorted[row * cols + (col - 1)]?.id)) {
    classes.push("selected-border-left");
  }
  if (
    col === cols - 1 ||
    !isImageSelected(sorted[row * cols + (col + 1)]?.id)
  ) {
    classes.push("selected-border-right");
  }
  return classes.join(" ");
};

// Event handlers: these should emit events or call parent-provided functions
const onImageDragStart = (img, idx, event) => {
  if (props.selectedImageIds && props.selectedImageIds.includes(img.id)) {
    event.dataTransfer.setData(
      "application/json",
      JSON.stringify({ imageIds: props.selectedImageIds })
    );
  } else {
    event.dataTransfer.setData(
      "application/json",
      JSON.stringify({ imageIds: [img.id] })
    );
  }
  event.dataTransfer.effectAllowed = "move";
};

const handleGridBackgroundClick = (e) => {
  if (!e.target.closest(".thumbnail-card")) {
    emit('clear-selection');
  }
};

const handleImageSelect = (img, idx, event) => {
    emit('select-image', img, idx, event);
};

const openOverlay = (img) => {
  emit('open-overlay', img);
};

const setImageScore = (img, n) => {
  emit('set-image-score', img, n);
};

const formatLikenessScore = (score) => score !== undefined ? score.toFixed(2) : "";

const isSupportedVideoFile = (format) => {
  if (!format) return false;
  const videoExts = ["mp4", "avi", "mov", "webm", "mkv", "flv", "wmv", "m4v"];
  return videoExts.includes(format.toLowerCase());
};

const gridContainer = ref(null);

// Expose the grid DOM node to parent
defineExpose({ gridEl: gridContainer });

onMounted(() => {
  if (gridContainer.value) {
    gridContainer.value.addEventListener('scroll', onGridScroll);
  }
});

function onGridScroll(e) {
  const el = e.target;
  if (props.imagesLoading) return;
  if (el.scrollTop + el.clientHeight >= el.scrollHeight - 200) {
    emit('infinite-scroll');
  }
}
</script>
<style scoped>
.image-grid {
  display: grid;
  gap: 0;
  width: 100%;
  height: 100%;
  min-height: 64px;
  flex: 1 1 0%;
  padding: 0 12px 0 4px !important; /* Remove top/bottom padding, keep right for scrollbar */
  overflow-y: auto;
  background: #ddd;
  align-content: start;
  justify-content: start;
  padding-bottom: 72px !important;
}
.image-card {
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  padding: 0;
  margin: 0;
  transition: box-shadow 0.2s, border 0.2s;
  position: relative;
  z-index: 0; /* Ensure stacking context */
  border: 3px solid transparent;
}
.image-card.selected {
  z-index: 2;
  position: relative;
  border: 3px solid rgba(25, 118, 210, 0.32);
}
.selected-border-top {
  border-top-color: #1976d2 !important;
}
.selected-border-bottom {
  border-bottom-color: #1976d2 !important;
}
.selected-border-left {
  border-left-color: #1976d2 !important;
}
.selected-border-right {
  border-right-color: #1976d2 !important;
}
.image-card.selected::after {
  content: "";
  position: absolute;
  inset: 0;
  background: rgba(25, 118, 210, 0.32);
  border-radius: 0;
  pointer-events: none;
  z-index: 1; /* Lower than border */
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
  padding: 0;
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
.image-card {
  position: relative;
}
.v-card {
  position: relative;
  overflow: visible;
}
.thumbnail-info {
  font-size: 0.85em;
  color: #666;
  margin-top: 2px;
  text-align: center;
  word-break: break-all;
}
.thumbnail-container {
  width: 100%;
  position: relative;
  display: block;
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
  0% { transform: translate(-50%, -50%) rotate(0deg); }
  100% { transform: translate(-50%, -50%) rotate(360deg); }
}
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
  height: 100%;
  position: relative;
}
</style>
