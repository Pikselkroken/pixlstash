/* Make horizontal scrollbar more visible */ .likeness-row::-webkit-scrollbar {
height: 12px; } .likeness-row::-webkit-scrollbar-thumb { background: #888;
border-radius: 6px; } .likeness-row::-webkit-scrollbar-track { background: #eee;
border-radius: 6px; }
<template>
  <Toolbox>
    <div style="margin-bottom: 24px">
      <label for="likeness-threshold" style="font-weight: bold"
        >Likeness Threshold</label
      >
      <v-slider
        id="likeness-threshold"
        v-model="likenessThreshold"
        :min="0"
        :max="1"
        :step="0.01"
        :thumb-label="showThumbLabel"
        style="margin-top: 8px"
        @mousedown="onSliderStart"
        @touchstart="onSliderStart"
        @mouseup="onSliderEnd"
        @touchend="onSliderEnd"
      />
      <div style="font-size: 0.9em; color: #aaa">
        Current: {{ likenessThreshold }}
      </div>
    </div>
    <div style="margin-bottom: 24px">
      <label style="font-weight: bold">Prioritisation Criteria</label>
      <Draggable
        v-model="prioritisationCriteria"
        item-key="name"
        :animation="200"
      >
        <template #item="{ element, index }">
          <div class="criteria-item">
            <v-icon
              small
              class="criteria-handle"
              style="margin-right: 8px; vertical-align: middle"
              >mdi-drag</v-icon
            >
            <span>{{ element.label }}</span>
            <span
              class="criteria-index"
              style="margin-left: auto; font-size: 0.95em; color: #bbb"
              >{{ index + 1 }}</span
            >
          </div>
        </template>
      </Draggable>
      <div style="font-size: 0.9em; color: #aaa">Drag to reorder</div>
    </div>
    <v-btn color="red" variant="outlined" style="width: 100%"
      >Delete All Duplicates</v-btn
    >
  </Toolbox>
  <ImageOverlay
    :open="overlayOpen"
    :initialImage="overlayImage"
    :allImages="overlayImages"
    :backendUrl="props.backendUrl"
    @close="closeOverlay"
    @apply-score="applyScore"
  />
  <div class="likeness-rows" ref="likenessRowsContainer">
    <div
      v-if="loggedVisibleRows.length === 0 && !loading"
      class="empty-message"
    >
      <span>No likeness groups found (visibleRows is empty).</span>
    </div>
    <div v-else>
      <div
        v-for="(row, rowIdx) in loggedVisibleRows"
        :key="rowIdx"
        class="likeness-row"
      >
        <div v-for="img in row" :key="img.id" class="likeness-image-card">
          <div class="likeness-img-wrapper">
            <img
              :src="`${backendUrl}/pictures/${img.id}`"
              class="likeness-img"
              :style="{
                width: `${thumbnailSize}px`,
                height: `${thumbnailSize}px`,
              }"
              @click="openOverlay(img, row)"
            />
            <div v-if="props.showStars" class="star-overlay">
              <v-icon
                v-for="n in 5"
                :key="n"
                :large="true"
                :color="n <= (img.score || 0) ? 'orange' : 'grey darken-2'"
                style="cursor: pointer"
                @click.stop="setScore(img, n)"
                >mdi-star</v-icon
              >
            </div>
          </div>
          <div class="likeness-metrics">
            <span v-if="img.width && img.height" style="display: block"
              >Resolution: {{ img.width }} x {{ img.height }}</span
            >
            <span v-if="img.sharpness" style="display: block"
              >Sharpness:
              {{
                typeof img.sharpness === "number"
                  ? img.sharpness.toFixed(2)
                  : img.sharpness
              }}</span
            >
            <span v-if="img.noise_level" style="display: block"
              >Noise Level:
              {{
                typeof img.noise_level === "number"
                  ? img.noise_level.toFixed(2)
                  : img.noise_level
              }}</span
            >
          </div>
          <div
            v-if="props.showStars"
            class="star-overlay"
            style="margin-top: 2px"
          >
            <v-icon
              v-for="n in 5"
              :key="n"
              :large="true"
              :color="n <= (img.score || 0) ? 'orange' : 'grey darken-2'"
              style="cursor: pointer"
              @click.stop="setScore(img, n)"
              >mdi-star</v-icon
            >
          </div>
          <div class="likeness-toggle" style="margin-top: 8px">
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
    </div>
    <div v-if="loading" class="loading-indicator">Loading...</div>
  </div>
</template>

<script setup>
import Toolbox from "./Toolbox.vue";
import Draggable from "vuedraggable";

import { reactive, watch, watchEffect } from "vue";
import { ref, onMounted, onBeforeUnmount, computed } from "vue";
import { defineProps, defineEmits, defineExpose } from "vue";
import ImageOverlay from "./ImageOverlay.vue";

const props = defineProps({
  backendUrl: String,
  thumbnailSize: Number,
  showStars: Boolean,
  storedLikenessThreshold: Number,
});

const likenessThreshold = ref(props.storedLikenessThreshold ?? 0.97);

watch(
  () => props.storedLikenessThreshold,
  (val) => {
    if (val !== likenessThreshold.value) {
      likenessThreshold.value = val;
    }
  }
);

const showThumbLabel = ref(false);
let lastCommittedThreshold = likenessThreshold.value;

function onSliderStart() {
  showThumbLabel.value = true;
}

function onSliderEnd() {
  showThumbLabel.value = false;
  if (likenessThreshold.value !== lastCommittedThreshold) {
    lastCommittedThreshold = likenessThreshold.value;
    emit("update:likeness-threshold", likenessThreshold.value);
    fetchLikenessRows();
  }
}
const prioritisationCriteria = ref([
  { name: "resolution", label: "Resolution" },
  { name: "score", label: "Score" },
  { name: "sharpness", label: "Sharpness" },
  { name: "noise", label: "Noise Level" },
]);

const emit = defineEmits([
  "update:likeness-threshold",
  "update:prioritization-criteria",
]);

watchEffect(likenessThreshold, (newVal) => {
  emit("update:likeness-threshold", newVal);
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
  console.log(
    `[LikenessRows.vue] fetchLikenessRows called, threshold=${likenessThreshold.value}`
  );
  loading.value = true;
  try {
    const url = `${props.backendUrl}/picture_stacks?threshold=${likenessThreshold.value}`;
    console.log(`[LikenessRows.vue] Fetching: ${url}`);
    const res = await fetch(url);
    if (!res.ok) {
      const text = await res.text();
      console.error(
        `[LikenessRows.vue] Fetch failed: status=${res.status}, body=${text}`
      );
      throw new Error("Failed to fetch likeness stacks");
    }
    const data = await res.json();
    console.log("[LikenessRows.vue] Response:", data);
    const rows = [];
    if (data && Array.isArray(data.stacks)) {
      for (const stack of data.stacks) {
        rows.push(stack.pictures);
      }
    }
    console.log(`[LikenessRows.vue] Parsed rows count: ${rows.length}`);
    allRows.value = rows;
    // Always reset pagination and visibleRows
    pageOffset = 0;
    visibleRows.value = [];
    loading.value = false;
    loadMoreRows();
  } catch (e) {
    console.error("[LikenessRows.vue] Error in fetchLikenessRows:", e);
    allRows.value = [];
    visibleRows.value = [];
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
  console.log("[LikenessRows.vue] loadMoreRows called");
  console.log("[LikenessRows.vue] allRows before slice:", allRows.value);
  console.log("[LikenessRows.vue] pageOffset before:", pageOffset);
  setTimeout(() => {
    const nextRows = allRows.value.slice(pageOffset, pageOffset + pageSize);
    console.log("[LikenessRows.vue] nextRows:", nextRows);
    visibleRows.value = [...visibleRows.value, ...nextRows];
    console.log(
      "[LikenessRows.vue] visibleRows after update:",
      visibleRows.value
    );
    pageOffset += pageSize;
    console.log("[LikenessRows.vue] pageOffset after:", pageOffset);
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
    likenessRowsContainer.value.addEventListener("scroll", onScroll);
  }
});

// Expose refresh method for parent
defineExpose({ refreshLikeness: fetchLikenessRows });

// Clean up scroll listener
onBeforeUnmount(() => {
  if (likenessRowsContainer.value) {
    likenessRowsContainer.value.removeEventListener("scroll", onScroll);
  }
});

const loggedVisibleRows = computed(() => {
  console.log("[LikenessRows.vue] Rendering visibleRows:", visibleRows.value);
  return visibleRows.value;
});
</script>

<style scoped>
/* Draggable criteria styling */
.criteria-item {
  display: flex;
  align-items: center;
  background: #333;
  color: #fff;
  border-radius: 6px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.12);
  padding: 8px 12px;
  margin-bottom: 8px;
  cursor: grab;
  transition: box-shadow 0.2s, background 0.2s;
}
.criteria-item:hover {
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.18);
  background: #444;
}
.criteria-handle {
  cursor: grab;
  color: #bbb;
}
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
  display: block;
  overflow-x: auto;
  white-space: nowrap;
  padding-bottom: 4px;
  text-align: left;
}
.likeness-image-card {
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  background: #f5f5f5;
  border-radius: 8px;
  padding: 4px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
  min-width: 128px;
  box-sizing: border-box;
  margin-right: 8px;
}
.likeness-img {
  object-fit: cover;
  border-radius: 6px;
}
.likeness-img-wrapper {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  flex-grow: 0;
}
.star-overlay {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 4px;
  display: flex;
  justify-content: center;
  gap: 2px;
  background: rgba(0, 0, 0, 0.18);
  border-radius: 0 0 6px 6px;
  padding: 2px 0;
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
