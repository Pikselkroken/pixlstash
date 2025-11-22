<!--- TEMPLATE CODE GOES BELOW THE FOLLOWING LINE -->
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
            <v-icon small class="criteria-handle" style="vertical-align: middle"
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
    <v-btn color="orange" variant="outlined" style="width: 100%" @click="selectAllDuplicates">
      Select All Duplicates
    </v-btn>
  </Toolbox>
  <ImageOverlay
    :open="overlayOpen"
    :initialImage="overlayImage"
    :allImages="overlayImages"
    :backendUrl="props.backendUrl"
    @close="closeOverlay"
    @apply-score="applyScore"
  />
  <div style="position: relative; min-height: 100%; width: 100%">
    <div class="likeness-rows" ref="likenessRowsContainer" @click="onBackgroundClick">
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
            <div
              v-for="img in row"
              :key="img.id"
              class="likeness-image-card"
              :class="{
                selected: selectedImageIds.includes(img.id),
                deleted: img._deleted
              }"
              @click="!img._deleted && onThumbnailClick(img, row, $event)"
              :draggable="selectedImageIds.includes(img.id) && !img._deleted"
              @dragstart="onImageDragStart(img, $event)"
              style="position: relative"
            >
            <div
              v-if="selectedImageIds.includes(img.id)"
              class="selection-overlay"
            ></div>
            <div class="likeness-img-wrapper">
              <img
                :src="`${backendUrl}/pictures/${img.id}`"
                class="likeness-img"
                :style="{
                  width: `${thumbnailSize}px`,
                  height: `${thumbnailSize}px`,
                }"
                @click.stop="onThumbnailClick(img, row, $event)"
              />
              <div
                class="metadata-overlay"
                v-if="
                  img.width &&
                  img.height &&
                  img.sharpness !== undefined &&
                  img.noise_level !== undefined
                "
              >
                <span class="meta-icon">
                  <v-icon small>mdi-image</v-icon>
                  {{ img.width }}×{{ img.height }}
                </span>
                <span class="meta-icon">
                  Sharpness:
                  {{
                    typeof img.sharpness === "number"
                      ? img.sharpness.toFixed(2)
                      : img.sharpness
                  }}
                </span>
                <span class="meta-icon">
                  Noise:
                  {{
                    typeof img.noise_level === "number"
                      ? img.noise_level.toFixed(2)
                      : img.noise_level
                  }}
                </span>
              </div>
            </div>
            <!-- Removed text metrics, now shown as icon overlay -->
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
          </div>
        </div>
      </div>
      <div v-if="loading" class="loading-indicator">Loading...</div>
    </div>
    <div
      style="position: absolute; left: 0; bottom: 0; width: 100%; z-index: 100"
    >
      <SelectionBar
        v-if="selectedImageIds.length > 0"
        :selectedCount="selectedImageIds.length"
        :selectedCharacter="null"
        :selectedSet="null"
        :selectedGroupName="null"
        :visible="selectedImageIds.length > 0"
        @clear-selection="clearSelection"
        @remove-from-group="() => {}"
        @delete-selected="deleteSelected"
      />
    </div>
  </div>
</template>
<!--- TEMPLATE CODE GOES ABOVE THE PREVIOUS LINE -->
<!--- JAVASCRIPT CODE GOES BELOW THE FOLLOWING LINE -->
<script setup>
// Selection and drag logic
const selectedImageIds = ref([]);
function handleImageCardClick(img, event) {
  if (event.ctrlKey || event.metaKey) {
    if (selectedImageIds.value.includes(img.id)) {
      selectedImageIds.value = selectedImageIds.value.filter(
        (id) => id !== img.id
      );
    } else {
      selectedImageIds.value.push(img.id);
    }
  } else {
    if (
      selectedImageIds.value.length === 1 &&
      selectedImageIds.value[0] === img.id
    ) {
      selectedImageIds.value = [];
    } else {
      selectedImageIds.value = [img.id];
    }
  }
}
function onImageDragStart(img, event) {
  if (!selectedImageIds.value.includes(img.id)) {
    event.preventDefault();
    return;
  }
  event.dataTransfer.effectAllowed = "move";
  event.dataTransfer.setData(
    "application/json",
    JSON.stringify({ imageIds: selectedImageIds.value })
  );
}
import Toolbox from "./Toolbox.vue";
import Draggable from "vuedraggable";
import SelectionBar from "./SelectionBar.vue";

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


// Clear selection when clicking background (not on image card)
function onBackgroundClick(e) {
  clearSelection();
}

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
    // Always reset pagination and visibleRows before loading
    visibleRows.value = [];
    pageOffset = 0;
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

function onThumbnailClick(img, row, event) {
  // Always prevent default and stop propagation to avoid double triggers
  event.preventDefault();
  event.stopPropagation();
  if (event.ctrlKey || event.shiftKey || event.metaKey) {
    handleImageCardClick(img, event);
    return;
  }
  openOverlay(img, row);
}


async function deleteSelected() {
  if (!selectedImageIds.value.length) return;
  if (!confirm(`Delete ${selectedImageIds.value.length} selected image(s)? This cannot be undone.`)) return;
  const backendUrl = props.backendUrl;
  // For each row, if a deleted image has a higher score than the main image, transfer it
  const toDelete = new Set(selectedImageIds.value);
  visibleRows.value.forEach(row => {
    if (!row.length) return;
    const mainImg = row[0];
    let maxScore = mainImg.score || 0;
    let transferScore = null;
    row.forEach(img => {
      if (toDelete.has(img.id) && (img.score || 0) > maxScore) {
        maxScore = img.score;
        transferScore = img.score;
      }
    });
    if (transferScore !== null && transferScore > (mainImg.score || 0)) {
      // Transfer score to main image
      mainImg.score = transferScore;
      fetch(`${backendUrl}/pictures/${mainImg.id}?score=${transferScore}`, { method: "PATCH" });
    }
  });
  // Now delete as before
  Promise.all(
    selectedImageIds.value.map((id) =>
      fetch(`${backendUrl}/pictures/${id}`, { method: "DELETE" })
        .then((res) => {
          if (!res.ok) throw new Error(`Failed to delete image ${id}`);
        })
        .catch((err) => {
          alert(`Error deleting image ${id}: ${err.message}`);
        })
    )
  ).then(() => {
    // Mark deleted images as deleted (greyed out) until next manual refresh
    visibleRows.value.forEach(row => {
      row.forEach(img => {
        if (toDelete.has(img.id)) img._deleted = true;
      });
    });
    allRows.value.forEach(row => {
      row.forEach(img => {
        if (toDelete.has(img.id)) img._deleted = true;
      });
    });
    selectedImageIds.value = [];
    pageOffset = 0;
    // Do not call fetchLikenessRows();
  });
}

function clearSelection() {
  selectedImageIds.value = [];
}

// Select all duplicate images (excluding main images in each row)
function selectAllDuplicates() {
  const ids = [];
  visibleRows.value.forEach(row => {
    // Skip main image (first in row)
    row.slice(1).forEach(img => {
      if (!img._deleted) ids.push(img.id);
    });
  });
  selectedImageIds.value = ids;
}

// ESC key handler to clear selection
onMounted(() => {
  window.addEventListener("keydown", onKeyDown);
});
onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKeyDown);
});
function onKeyDown(e) {
  if (e.key === "Escape") {
    clearSelection();
  } else if ((e.key === "Delete" || e.key === "Del") && selectedImageIds.value.length > 0) {
    // Only trigger if not in an input/textarea
    const tag = (e.target && e.target.tagName) ? e.target.tagName.toLowerCase() : "";
    if (tag !== "input" && tag !== "textarea") {
      deleteSelected();
    }
  }
}
</script>
<!--- JAVASCRIPT CODE GOES ABOVE THE PREVIOUS LINE --->
<!--- CSS CODE GOES BELOW THE FOLLOWING LINE -->
<style scoped>
.likeness-image-card.selected {
  /* Remove border and shadow for selection, use overlay instead */
  box-shadow: none;
  border: none;
  z-index: auto;
}
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
  margin-right: 0px;
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
  top: 8px;
  right: 8px;
  left: auto;
  bottom: auto;
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
/* Metadata overlay for thumbnail hover */
.metadata-overlay {
  position: absolute;
  left: 8px;
  bottom: 8px;
  background: rgba(40, 40, 40, 0.72);
  color: #fff;
  border-radius: 6px;
  padding: 4px 8px;
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 10px;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.18s;
  z-index: 20;
}
.likeness-img-wrapper:hover .metadata-overlay {
  opacity: 1;
  pointer-events: auto;
}
.meta-icon {
  display: flex;
  align-items: center;
  gap: 2px;
  font-size: 0.8em;
}
.selection-overlay {
  position: absolute;
  inset: 0;
  background: rgba(25, 118, 210, 0.62);
  pointer-events: none;
  z-index: 2;
}
.likeness-image-card.deleted {
  opacity: 0.45;
  filter: grayscale(1);
  pointer-events: none;
}
</style>
<!--- CSS CODE GOES ABOVE THE PREVIOUS LINE -->
