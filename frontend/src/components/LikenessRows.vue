<template>
  <ImageOverlay
    :open="overlayOpen"
    :initialImage="overlayImage"
    :allImages="overlayImages"
    :backendUrl="props.backendUrl"
    @close="closeOverlay"
    @apply-score="applyScore"
  />
  <div class="likeness-rows" ref="likenessRowsContainer">
    <div v-for="(row, rowIdx) in visibleRows" :key="rowIdx" class="likeness-row">
      <div v-for="img in row" :key="img.id" class="likeness-image-card">
        <img
          :src="`${backendUrl}/pictures/${img.id}`"
          class="likeness-img"
          :style="{ width: `${thumbnailSize}px`, height: `${thumbnailSize}px` }"
          @click="openOverlay(img, row)"
        />
        <div class="likeness-metrics">
          <span v-if="img.width && img.height" style="margin-right: 1em;">Resolution: {{ img.width }} x {{ img.height }}</span>
          <span v-if="img.sharpness" style="margin-right: 1em;">Sharp: {{ typeof img.sharpness === 'number' ? img.sharpness.toFixed(2) : img.sharpness }}</span>
          <span v-if="img.noise_level">Noise: {{ typeof img.noise_level === 'number' ? img.noise_level.toFixed(2) : img.noise_level }}</span>
        </div>
        <div v-if="props.showStars" class="star-overlay" style="margin-top: 2px;">
          <v-icon
            v-for="n in 5"
            :key="n"
            :large="true"
            :color="n <= (img.score || 0) ? 'orange' : 'grey darken-2'"
            style="cursor: pointer"
            @click.stop="setScore(img, n)"
          >mdi-star</v-icon>
        </div>
        <div class="likeness-toggle" style="margin-top: 8px;">
          <v-switch
            v-model="toggleStates[rowIdx][img.id]"
            :label="toggleStates[rowIdx][img.id] ? 'keep' : 'delete'"
            :color="toggleStates[rowIdx][img.id] ? 'success' : 'red'"
            inset
            hide-details
          />
        </div>
      </div>
    </div>
    <div v-if="loading" class="loading-indicator">Loading...</div>
  </div>
</template>

<script setup>
import { reactive, watchEffect } from 'vue';
import { ref, onMounted, onBeforeUnmount, computed } from 'vue';
import ImageOverlay from './ImageOverlay.vue';

const props = defineProps({
  backendUrl: String,
  thumbnailSize: Number,
  showStars: Boolean,
});

// Overlay state
const overlayOpen = ref(false);
const overlayImage = ref(null);
const overlayImages = ref([]);

function openOverlay(img, row) {
  overlayImage.value = img;
  overlayImages.value = row;
  overlayOpen.value = true;
}

function closeOverlay() {
  overlayOpen.value = false;
}

const thumbnailSize = computed(() => props.thumbnailSize);
const visibleRows = ref([]);
const loading = ref(false);
const pageSize = 10;
let pageOffset = 0;
const likenessRowsContainer = ref(null);
const allRows = ref([]);

// Track toggle state for each image in each row
const toggleStates = reactive({});

watchEffect(() => {
  visibleRows.value.forEach((row, rowIdx) => {
    if (!toggleStates[rowIdx]) toggleStates[rowIdx] = {};
    row.forEach((img, imgIdx) => {
      if (!(img.id in toggleStates[rowIdx])) {
        // Leftmost image defaults to keep, others to delete
        toggleStates[rowIdx][img.id] = imgIdx === 0;
      }
    });
  });
});

async function fetchLikenessRows() {
  loading.value = true;
  try {
    const res = await fetch(`${props.backendUrl}/picture_stacks?threshold=0.97`);
    if (!res.ok) throw new Error("Failed to fetch likeness stacks");
    const data = await res.json();
    const rows = [];
    for (const stack of data.stacks) {
      rows.push(stack.pictures);
    }
    allRows.value = rows;
    // Reset pagination
    pageOffset = 0;
    visibleRows.value = [];
    loadMoreRows();
  } catch (e) {
    allRows.value = [];
    visibleRows.value = [];
  } finally {
    loading.value = false;
  }
}

// Score logic for likeness view
async function setScore(img, n) {
  const newScore = (img.score || 0) === n ? 0 : n;
  await applyScore(img, newScore);
}

async function applyScore(img, newScore) {
  const imageId = img.id;
  if (!imageId) return;
  try {
    const res = await fetch(
      `${props.backendUrl}/pictures/${imageId}?score=${newScore}`,
      { method: "PATCH" }
    );
    if (!res.ok) throw new Error(`Failed to set score for image ${imageId}`);
    img.score = newScore;
  } catch (e) {
    // Optionally show error
  }
}


function loadMoreRows() {
  if (loading.value) return;
  loading.value = true;
  setTimeout(() => {
    const nextRows = allRows.value.slice(pageOffset, pageOffset + pageSize);
    visibleRows.value = [...visibleRows.value, ...nextRows];
    pageOffset += pageSize;
    loading.value = false;
  }, 300);
}

function onScroll(e) {
  const el = e.target;
  if (el.scrollTop + el.clientHeight >= el.scrollHeight - 200) {
    loadMoreRows();
  }
}


onMounted(() => {
  fetchLikenessRows();
  if (likenessRowsContainer.value) {
    likenessRowsContainer.value.addEventListener('scroll', onScroll);
  }
});

// Expose refresh method for parent
defineExpose({ refreshLikeness: fetchLikenessRows });

// Clean up scroll listener
onBeforeUnmount(() => {
  if (likenessRowsContainer.value) {
    likenessRowsContainer.value.removeEventListener('scroll', onScroll);
  }
});
</script>

<style scoped>
.likeness-rows {
  display: flex;
  flex-direction: column;
  gap: 16px;
  width: 100%;
  height: 100%;
  overflow-y: auto;
  padding: 8px;
}
.likeness-row {
  display: flex;
  flex-direction: row;
  gap: 8px;
  align-items: center;
}
.likeness-image-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  background: #f5f5f5;
  border-radius: 8px;
  padding: 4px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.08);
}
.likeness-img {
  width: 128px;
  height: 128px;
  object-fit: cover;
  border-radius: 6px;
}
.likeness-metrics {
  font-size: 0.85em;
  color: #555;
  margin-top: 2px;
  text-align: center;
}
.loading-indicator {
  text-align: center;
  color: #1976d2;
  margin: 16px 0;
}
</style>
