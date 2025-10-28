<script setup>
import { computed, ref, onMounted, watch, onBeforeUnmount } from "vue";
import { VTextField } from "vuetify/components";
import unknownPerson from "./assets/unknown-person.png"; // Import for unknown character icon
// Selection state for file manager
const selectedImageIds = ref([]);
let lastSelectedIndex = null;

// Overlay state for full image view
const overlayOpen = ref(false);
const overlayImage = ref(null);

function openOverlay(img) {
  overlayImage.value = img;
  overlayOpen.value = true;
}

function closeOverlay() {
  overlayOpen.value = false;
}

// Search bar state and logic
const searchQuery = ref("");
async function searchImages() {
  const query = searchQuery.value.trim();
  if (!query) return;
  imagesLoading.value = true;
  imagesError.value = null;
  try {
    const url = `${BACKEND_URL}/pictures/search?query=${encodeURIComponent(
      query
    )}&threshold=0.3&top_n=1000`;
    const res = await fetch(url);
    if (!res.ok) throw new Error("Search failed");
    const baseImages = await res.json();
    images.value = baseImages.map((img) => ({
      ...img,
      score: typeof img.score !== "undefined" ? img.score : null,
    }));
    setTimeout(updateColumns, 0);
  } catch (e) {
    imagesError.value = e.message;
  } finally {
    imagesLoading.value = false;
  }
}

function handleImageSelect(img, idx, event) {
  const id = img.id;
  const isSelected = selectedImageIds.value.includes(id);
  const isCtrl = event.ctrlKey || event.metaKey;
  const isShift = event.shiftKey;

  if (isShift && lastSelectedIndex !== null) {
    // Range select
    const start = Math.min(lastSelectedIndex, idx);
    const end = Math.max(lastSelectedIndex, idx);
    const rangeIds = images.value.slice(start, end + 1).map((i) => i.id);
    const newSelection = isCtrl
      ? Array.from(new Set([...selectedImageIds.value, ...rangeIds]))
      : rangeIds;
    selectedImageIds.value = newSelection;
  } else if (isCtrl) {
    // Toggle selection
    if (isSelected) {
      selectedImageIds.value = selectedImageIds.value.filter((i) => i !== id);
    } else {
      selectedImageIds.value = [...selectedImageIds.value, id];
    }
    lastSelectedIndex = idx;
  } else {
    // Single select
    selectedImageIds.value = [id];
    lastSelectedIndex = idx;
  }
}

// Fetch score for an image if missing (called on thumbnail load)

// Fetch score for an image if missing (called on thumbnail load)
async function fetchScoreIfMissing(img) {
  if (typeof img.score === "undefined" || img.score === null) {
    try {
      const res = await fetch(`${BACKEND_URL}/pictures/${img.id}`);
      if (res.ok) {
        const data = await res.json();
        if ("score" in data) {
          // Ensure reactivity
          Object.assign(img, { score: data.score });
        }
      }
    } catch (e) {}
  }
}

const isImageSelected = (id) => selectedImageIds.value.includes(id);

// Logic to determine if a selected image is on the outer edge of a selection group
const getSelectionBorderClasses = (idx) => {
  if (!isImageSelected(images.value[idx]?.id)) return "";
  const cols = columns.value;
  const total = images.value.length;
  const row = Math.floor(idx / cols);
  const col = idx % cols;
  let classes = [];
  // Check neighbors: top, right, bottom, left
  // Top
  if (row === 0 || !isImageSelected(images.value[(row - 1) * cols + col]?.id)) {
    classes.push("selected-border-top");
  }
  // Bottom
  if (
    row === Math.floor((total - 1) / cols) ||
    !isImageSelected(images.value[(row + 1) * cols + col]?.id)
  ) {
    classes.push("selected-border-bottom");
  }
  // Left
  if (col === 0 || !isImageSelected(images.value[row * cols + (col - 1)]?.id)) {
    classes.push("selected-border-left");
  }
  // Right
  if (
    col === cols - 1 ||
    !isImageSelected(images.value[row * cols + (col + 1)]?.id)
  ) {
    classes.push("selected-border-right");
  }
  return classes.join(" ");
};

const ALL_PICTURES_ID = "__all__";
const UNASSIGNED_PICTURES_ID = "__unassigned__";
const characters = ref([]);
// Computed: characters sorted alphabetically by name (case-insensitive)
const sortedCharacters = computed(() => {
  return [...characters.value].sort((a, b) => {
    if (!a.name && !b.name) return 0;
    if (!a.name) return 1;
    if (!b.name) return -1;
    return a.name.localeCompare(b.name, undefined, { sensitivity: "base" });
  });
});
const characterThumbnails = ref({}); // { [characterId]: thumbnailUrl }
const loading = ref(false);
const error = ref(null);

const selectedCharacter = ref(ALL_PICTURES_ID);
const images = ref([]);
const imagesLoading = ref(false);
const imagesError = ref(null);

const BACKEND_URL = "http://localhost:9537";

// Thumbnail size slider state
const thumbnailSizes = [128, 192, 256];
const thumbnailLabels = ["Small", "Medium", "Large"];
const thumbnailSize = ref(256);

// Responsive columns
const columns = ref(5);
const gridContainer = ref(null);

function updateColumns() {
  if (!gridContainer.value) return;
  const containerWidth = gridContainer.value.offsetWidth;
  columns.value = Math.max(
    1,
    Math.floor(containerWidth / (thumbnailSize.value + 32))
  );
}

async function fetchCharacters() {
  loading.value = true;
  error.value = null;
  try {
    const res = await fetch(`${BACKEND_URL}/characters`);
    if (!res.ok) throw new Error("Failed to fetch characters");
    const chars = await res.json();
    characters.value = chars;
    // For each character, fetch their first image's thumbnail (if any)
    for (const char of chars) {
      fetchCharacterThumbnail(char.id);
    }
  } catch (e) {
    error.value = e.message;
  } finally {
    loading.value = false;
  }
}

async function fetchCharacterThumbnail(characterId) {
  try {
    // Add cache-busting query param to ensure fresh thumbnail
    const cacheBuster = Date.now();
    const thumbUrl = `${BACKEND_URL}/face_thumbnail/${characterId}?cb=${cacheBuster}`;
    // Test if the endpoint returns an image (status 200 and content-type image/png)
    const res = await fetch(thumbUrl);
    if (res.ok && res.headers.get("content-type")?.includes("image/png")) {
      characterThumbnails.value[characterId] = thumbUrl;
    } else {
      characterThumbnails.value[characterId] = null;
    }
  } catch (e) {
    characterThumbnails.value[characterId] = null;
  }
}

onMounted(() => {
  fetchCharacters();
  window.addEventListener("resize", updateColumns);
  watch(thumbnailSize, updateColumns);
  setTimeout(updateColumns, 100); // Initial update after mount
});

watch(selectedCharacter, async (id) => {
  images.value = [];
  imagesError.value = null;
  selectedImageIds.value = []; // Clear selection on character change
  if (!id) return;
  imagesLoading.value = true;
  try {
    let url;
    if (id === ALL_PICTURES_ID) {
      url = `${BACKEND_URL}/pictures?info=true`;
    } else if (id === UNASSIGNED_PICTURES_ID) {
      url = `${BACKEND_URL}/pictures?character_id=&info=true`;
    } else {
      url = `${BACKEND_URL}/pictures?character_id=${encodeURIComponent(
        id
      )}&info=true`;
    }
    const res = await fetch(url);
    if (!res.ok) throw new Error("Failed to fetch images");
    const baseImages = await res.json();
    // Only set up the images array with a score property (null if missing)
    images.value = baseImages.map((img) => ({
      ...img,
      score: typeof img.score !== "undefined" ? img.score : null,
    }));
    // Ensure columns are recalculated after images are loaded
    setTimeout(updateColumns, 0);
  } catch (e) {
    imagesError.value = e.message;
  } finally {
    imagesLoading.value = false;
  }
});

function handleOverlayKeydown(e) {
  // Ctrl+A: select all images in grid
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a") {
    if (images.value.length) {
      selectedImageIds.value = images.value.map((img) => img.id);
      e.preventDefault();
    }
    return;
  }
  if (overlayOpen.value) {
    if (e.key === "ArrowLeft") {
      showPrevImage();
      e.preventDefault();
      return;
    } else if (e.key === "ArrowRight") {
      showNextImage();
      e.preventDefault();
      return;
    } else if (e.key === "Escape") {
      closeOverlay();
      e.preventDefault();
      return;
    }
  }
  // Grid navigation and selection
  if (!images.value.length) return;
  const cols = columns.value;
  let idx = lastSelectedIndex;
  if (idx === null || idx < 0 || idx >= images.value.length) idx = 0;
  let nextIdx = idx;
  if (e.key === "ArrowLeft") {
    if (idx % cols > 0) nextIdx = idx - 1;
    else return;
  } else if (e.key === "ArrowRight") {
    if (idx % cols < cols - 1 && idx + 1 < images.value.length)
      nextIdx = idx + 1;
    else return;
  } else if (e.key === "ArrowUp") {
    if (idx - cols >= 0) nextIdx = idx - cols;
    else return;
  } else if (e.key === "ArrowDown") {
    if (idx + cols < images.value.length) nextIdx = idx + cols;
    else return;
  } else if (e.key === "Delete") {
    if (selectedImageIds.value.length) {
      deleteSelectedImages();
      e.preventDefault();
      return;
    }
  }
  // Score shortcuts 1-5
  if (/^[1-5]$/.test(e.key) && selectedImageIds.value.length) {
    showStars.value = true;
    patchScoreForSelection(Number(e.key));
    e.preventDefault();
    return;
  } else {
    return;
  }
  const isCtrl = e.ctrlKey || e.metaKey;
  const isShift = e.shiftKey;
  if (isShift && lastSelectedIndex !== null) {
    // Range select
    const start = Math.min(lastSelectedIndex, nextIdx);
    const end = Math.max(lastSelectedIndex, nextIdx);
    const rangeIds = images.value.slice(start, end + 1).map((i) => i.id);
    const newSelection = isCtrl
      ? Array.from(new Set([...selectedImageIds.value, ...rangeIds]))
      : rangeIds;
    selectedImageIds.value = newSelection;
  } else if (isCtrl) {
    // Toggle selection of nextIdx
    const id = images.value[nextIdx].id;
    if (selectedImageIds.value.includes(id)) {
      selectedImageIds.value = selectedImageIds.value.filter((i) => i !== id);
    } else {
      selectedImageIds.value = [...selectedImageIds.value, id];
    }
    lastSelectedIndex = nextIdx;
  } else {
    // Single select
    selectedImageIds.value = [images.value[nextIdx].id];
    lastSelectedIndex = nextIdx;
  }
  e.preventDefault();
}

onMounted(() => {
  fetchCharacters();
  window.addEventListener("resize", updateColumns);
  watch(thumbnailSize, updateColumns);
  setTimeout(updateColumns, 100); // Initial update after mount
  window.addEventListener("keydown", handleOverlayKeydown);
});

onBeforeUnmount(() => {
  window.removeEventListener("keydown", handleOverlayKeydown);
});
function showPrevImage() {
  if (!overlayImage.value || !images.value.length) return;
  const idx = images.value.findIndex((i) => i.id === overlayImage.value.id);
  const prevIdx = (idx - 1 + images.value.length) % images.value.length;
  overlayImage.value = images.value[prevIdx];
}

function showNextImage() {
  if (!overlayImage.value || !images.value.length) return;
  const idx = images.value.findIndex((i) => i.id === overlayImage.value.id);
  const nextIdx = (idx + 1) % images.value.length;
  overlayImage.value = images.value[nextIdx];
}

// Delete functionality
async function deleteSelectedImages() {
  if (!selectedImageIds.value.length) return;
  const confirmed = confirm(
    `Delete ${selectedImageIds.value.length} selected image(s)? This cannot be undone.`
  );
  if (!confirmed) return;
  for (const id of selectedImageIds.value) {
    try {
      const res = await fetch(`${BACKEND_URL}/pictures/${id}`, {
        method: "DELETE",
      });
      if (!res.ok) throw new Error(`Failed to delete image ${id}`);
    } catch (e) {
      alert(e.message);
    }
  }
  // Remove deleted images from UI
  images.value = images.value.filter(
    (img) => !selectedImageIds.value.includes(img.id)
  );
  selectedImageIds.value = [];
}

// Patch score for selected images
async function patchScoreForSelection(score) {
  if (!selectedImageIds.value.length) return;
  for (const id of selectedImageIds.value) {
    try {
      const res = await fetch(`${BACKEND_URL}/pictures/${id}?score=${score}`, {
        method: "PATCH",
      });
      if (!res.ok) throw new Error(`Failed to set score for image ${id}`);
      // Update local image score
      const result = await res.json();
      const img = images.value.find((i) => i.id === id);
      if (img) img.score = score;
    } catch (e) {
      alert(e.message);
    }
  }
}

// Set score for a single image (click on star)
async function setImageScore(img, n) {
  const newScore = (img.score || 0) === n ? 0 : n;
  try {
    const res = await fetch(
      `${BACKEND_URL}/pictures/${img.id}?score=${newScore}`,
      { method: "PATCH" }
    );
    if (!res.ok) throw new Error(`Failed to set score for image ${img.id}`);
    img.score = newScore;
  } catch (e) {
    alert(e.message);
  }
}

const showStars = ref(true);

// Drag and drop logic for assigning images to characters
const dragOverCharacter = ref(null);
function onImageDragStart(img, idx, event) {
  // Only allow dragging if this image is selected
  let ids =
    selectedImageIds.value.length && isImageSelected(img.id)
      ? selectedImageIds.value
      : [img.id];
  event.dataTransfer.setData(
    "application/json",
    JSON.stringify({ imageIds: ids })
  );
  event.dataTransfer.effectAllowed = "move";
}
function onCharacterDragOver(charId) {
  dragOverCharacter.value = charId;
}
function onCharacterDragLeave(charId) {
  if (dragOverCharacter.value === charId) dragOverCharacter.value = null;
}
async function onCharacterDrop(charId, event) {
  dragOverCharacter.value = null;
  try {
    const data = JSON.parse(event.dataTransfer.getData("application/json"));
    if (!data.imageIds || !Array.isArray(data.imageIds)) return;
    await assignImagesToCharacter(data.imageIds, charId);
  } catch (e) {}
}

// Assign images to a character by PATCHing their character_id
async function assignImagesToCharacter(imageIds, characterId) {
  try {
    await Promise.all(
      imageIds.map(async (id) => {
        const res = await fetch(
          `${BACKEND_URL}/pictures/${id}?character_id=${encodeURIComponent(
            characterId
          )}`,
          { method: "PATCH" }
        );
        if (!res.ok)
          throw new Error(`Failed to assign character for image ${id}`);
      })
    );
    // Refresh images and character thumbnails after assignment
    await fetchCharacters();
    // If the current view is the character we just assigned to, or All Pictures, or Unassigned Pictures, refresh images
    if (
      selectedCharacter.value === characterId ||
      selectedCharacter.value === ALL_PICTURES_ID ||
      selectedCharacter.value === UNASSIGNED_PICTURES_ID
    ) {
      // Re-fetch images for the current character, all, or unassigned
      const id = selectedCharacter.value;
      let url;
      if (id === ALL_PICTURES_ID) {
        url = `${BACKEND_URL}/pictures?info=true`;
      } else if (id === UNASSIGNED_PICTURES_ID) {
        url = `${BACKEND_URL}/pictures?character_id=&info=true`;
      } else {
        url = `${BACKEND_URL}/pictures?character_id=${encodeURIComponent(
          id
        )}&info=true`;
      }
      const res = await fetch(url);
      if (res.ok) {
        const baseImages = await res.json();
        images.value = baseImages.map((img) => ({
          ...img,
          score: typeof img.score !== "undefined" ? img.score : null,
        }));
        setTimeout(updateColumns, 0);
      }
    }
  } catch (e) {
    alert("Failed to assign character: " + (e.message || e));
  }
}
</script>

<template>
  <v-app>
    <div class="file-manager">
      <aside class="sidebar">
        <div class="sidebar-title">Pictures</div>
        <div
          :class="[
            'sidebar-item',
            { active: selectedCharacter === ALL_PICTURES_ID },
          ]"
          @click="selectedCharacter = ALL_PICTURES_ID"
          style="
            display: flex;
            align-items: center;
            min-height: 56px;
            padding-top: 6px;
            padding-bottom: 6px;
            font-weight: bold;
          "
        >
          <span style="display: flex; align-items: center; margin-right: 12px">
            <v-icon size="44">mdi-image-multiple</v-icon>
          </span>
          <span style="font-size: 1.15em; line-height: 1.2">All Pictures</span>
        </div>
        <div
          :class="[
            'sidebar-item',
            { active: selectedCharacter === UNASSIGNED_PICTURES_ID },
          ]"
          @click="selectedCharacter = UNASSIGNED_PICTURES_ID"
          style="
            display: flex;
            align-items: center;
            min-height: 56px;
            padding-top: 6px;
            padding-bottom: 6px;
            font-weight: bold;
          "
        >
          <span style="display: flex; align-items: center; margin-right: 12px">
            <v-icon size="44">mdi-help-circle-outline</v-icon>
          </span>
          <span style="font-size: 1.15em; line-height: 1.2"
            >Unassigned Pictures</span
          >
        </div>
        <div class="sidebar-title">Characters</div>
        <div v-if="error" class="sidebar-error">{{ error }}</div>
        <div
          v-for="char in sortedCharacters"
          :key="char.id"
          :class="[
            'sidebar-item',
            {
              active: selectedCharacter === char.id,
              droppable: dragOverCharacter === char.id,
            },
          ]"
          @click="selectedCharacter = char.id"
          @dragover.prevent="onCharacterDragOver(char.id)"
          @dragleave="onCharacterDragLeave(char.id)"
          @drop="onCharacterDrop(char.id, $event)"
          style="
            display: flex;
            align-items: center;
            min-height: 56px;
            padding-top: 6px;
            padding-bottom: 6px;
          "
        >
          <span style="display: flex; align-items: center; margin-right: 12px">
            <img
              :src="
                characterThumbnails[char.id]
                  ? characterThumbnails[char.id]
                  : unknownPerson
              "
              alt=""
              style="
                width: 64px;
                height: 64px;
                object-fit: cover;
                border-radius: 6px;
                box-shadow: 0 0px 0px #bbb;
              "
            />
          </span>
          <span style="font-size: 1.15em; line-height: 1.2">{{
            char.name.charAt(0).toUpperCase() + char.name.slice(1)
          }}</span>
        </div>
        <div v-if="loading" class="sidebar-loading">Loading...</div>
      </aside>
      <main class="main-area">
        <!-- Top toolbar with right-aligned slider and delete button -->
        <div class="top-toolbar">
          <v-text-field
            v-model="searchQuery"
            placeholder="Search images..."
            hide-details
            dense
            solo
            clearable
            prepend-inner-icon="mdi-magnify"
            style="max-width: 320px; margin-right: 16px"
            @keydown.enter="searchImages"
            @click:append-outer="searchImages"
          />
          <div style="flex: 1"></div>
          <v-btn
            icon
            :color="showStars ? 'amber darken-2' : 'grey'"
            @click="showStars = !showStars"
            title="Toggle star ratings"
            style="margin-right: 12px"
          >
            <v-icon>{{ showStars ? "mdi-star" : "mdi-star-outline" }}</v-icon>
          </v-btn>
          <v-btn
            icon
            color="red darken-2"
            :disabled="!selectedImageIds.length"
            @click="deleteSelectedImages"
            title="Delete selected images"
            style="margin-right: 12px"
          >
            <v-icon>mdi-trash-can-outline</v-icon>
          </v-btn>
          <v-icon small>mdi-image-size-select-small</v-icon>
          <v-slider
            v-model="thumbnailSize"
            :min="128"
            :max="256"
            :step="64"
            :ticks="true"
            :tick-labels="thumbnailLabels"
            class="slider"
            hide-details
            style="
              max-width: 220px;
              display: inline-block;
              vertical-align: middle;
              margin: 0 8px;
            "
          />
          <v-icon small>mdi-image-size-select-large</v-icon>
        </div>
        <div class="main-content">
          <template v-if="selectedCharacter">
            <div v-if="imagesLoading" class="empty-state">
              Loading images...
            </div>
            <div v-else-if="imagesError" class="empty-state">
              {{ imagesError }}
            </div>
            <div v-else-if="images.length === 0" class="empty-state">
              No images found for this character.
            </div>
            <div
              v-else
              class="image-grid"
              :style="{ gridTemplateColumns: `repeat(${columns}, 1fr)` }"
              ref="gridContainer"
            >
              <div
                v-for="(img, idx) in images"
                :key="img.id"
                class="image-card"
                :class="[
                  isImageSelected(img.id) ? 'selected' : '',
                  getSelectionBorderClasses(idx),
                ]"
                @click="handleImageSelect(img, idx, $event)"
                :draggable="isImageSelected(img.id)"
                @dragstart="onImageDragStart(img, idx, $event)"
              >
                <v-card>
                  <div class="star-overlay" v-if="showStars">
                    <v-icon
                      v-for="n in 5"
                      :key="n"
                      small
                      :color="
                        n <= (img.score || 0) ? 'amber' : 'grey lighten-1'
                      "
                      style="cursor: pointer"
                      @click.stop="setImageScore(img, n)"
                      >mdi-star</v-icon
                    >
                  </div>
                  <v-img
                    :src="`${BACKEND_URL}/thumbnails/${img.id}`"
                    :height="thumbnailSize"
                    :width="thumbnailSize"
                    @click.stop="
                      (e) => {
                        if (e.ctrlKey || e.metaKey || e.shiftKey) {
                          handleImageSelect(img, idx, e);
                        } else {
                          openOverlay(img);
                        }
                      }
                    "
                    @load="fetchScoreIfMissing(img)"
                    style="cursor: pointer"
                  />
                  <!-- Removed image description from grid -->
                </v-card>
              </div>
              <!-- Full image overlay -->
              <div
                v-if="overlayOpen"
                class="image-overlay"
                @click.self="closeOverlay"
              >
                <div class="overlay-content">
                  <button
                    class="overlay-close"
                    @click="closeOverlay"
                    aria-label="Close"
                  >
                    &times;
                  </button>
                  <div class="overlay-flex-row">
                    <button
                      class="overlay-nav overlay-nav-left"
                      @click.stop="showPrevImage"
                      aria-label="Previous"
                    >
                      &#8592;
                    </button>
                    <div class="overlay-img-container">
                      <img
                        v-if="overlayImage"
                        :src="`${BACKEND_URL}/pictures/${overlayImage.id}`"
                        :alt="overlayImage.description || 'Full Image'"
                        class="overlay-img"
                      />
                      <div class="overlay-desc">
                        {{ overlayImage?.description }}
                      </div>
                    </div>
                    <button
                      class="overlay-nav overlay-nav-right"
                      @click.stop="showNextImage"
                      aria-label="Next"
                    >
                      &#8594;
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </template>
          <template v-else>
            <div class="empty-state">Select a character to view images.</div>
          </template>
        </div>
      </main>
    </div>
  </v-app>
</template>

<style scoped>
.image-grid {
  display: grid;
  gap: 0;
  width: 100%;
  padding: 4px 12px 4px 4px; /* Extra right padding for scrollbar */
  max-height: calc(100vh - 140px);
  overflow-y: auto;
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
.v-img {
  display: block;
  margin: 0 auto;
  box-sizing: border-box;
  padding: 0;
}
.v-card-title {
  width: 100%;
  max-width: 256px;
  min-height: 2.5em;
  font-size: 1rem;
  text-align: center;
  white-space: normal;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  word-break: break-word;
  margin: 0 auto 2px auto;
  padding: 2px 4px 0 4px;
}
/* Original simple file manager layout */
.file-manager {
  display: flex;
  flex-direction: row;
  position: fixed;
  inset: 0;
  background: #ccc;
  min-width: 0;
  min-height: 0;
  box-sizing: border-box;
}
.sidebar {
  width: 280px;
  background: #506168ff;
  border-right: 1px solid #bbb;
  padding: 16px 0 16px 0;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  min-height: 100vh;
}
.sidebar-title {
  font-size: 1.25rem;
  font-weight: 600;
  margin-bottom: 12px;
  padding-left: 16px;
}
.sidebar-item {
  padding: 8px 16px;
  cursor: pointer;
  border-radius: 4px;
  margin-bottom: 4px;
  transition: background 0.2s;
}
.sidebar-item.active,
.sidebar-item:hover {
  background: #f0f0f055;
}
.sidebar-loading,
.sidebar-error {
  padding: 8px 16px;
  color: #888;
}
.sidebar,
.sidebar-item {
  color: #ffffffff;
}
.main-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: #eee;
  min-width: 0;
  min-height: 100vh;
  box-sizing: border-box;
  padding: 0;
}
.main-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  justify-content: flex-start;
  padding: 0;
}
.empty-state {
  color: #aaa;
  font-size: 1.2rem;
  margin-top: 32px;
  text-align: center;
}
.thumbnail-slider {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  width: 100%;
  margin-bottom: 32px;
  min-height: 48px;
}
.slider {
  flex: 1;
  margin: 0 8px;
  min-width: 120px;
  max-width: 220px;
}
.thumbnail-slider {
  margin-bottom: 4px;
  min-height: 32px;
}
.slider {
  margin: 0 2px;
  min-width: 80px;
  max-width: 180px;
}
.image-grid {
  gap: 0px;
  max-height: calc(100vh - 80px);
}
/* Overlay modal for full image view */
.image-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  background: rgba(0, 0, 0, 0.85);
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
}
.overlay-content {
  position: relative;
  width: 80vw;
  height: 80vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: #222;
  border-radius: 8px;
  box-shadow: 0 2px 16px rgba(0, 0, 0, 0.5);
  padding: 24px 24px 16px 24px;
}
.overlay-flex-row {
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
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
  object-fit: contain;
  border-radius: 4px;
  background: #111;
  box-shadow: 0 1px 8px rgba(0, 0, 0, 0.4);
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
/* Overlay navigation buttons */
.overlay-nav {
  position: absolute;
  top: 50%;
  font-size: 2.5rem;
  color: #000;
  background: #bbb;
  width: 52px;
  height: 52px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  user-select: none;
}

.overlay-nav-left {
  left: 12px;
}

.overlay-nav-right {
  right: 12px;
}

.overlay-nav:hover {
  background: #fff;
  color: #000;
}
.overlay-nav {
  z-index: 1200;
}
.top-toolbar {
  width: 100%;
  background: #cdcdcdff;
  min-height: 48px;
  display: flex;
  align-items: center;
  padding: 0 24px;
  border-bottom: 1px solid #ccc;
  margin-bottom: 4px;
  z-index: 2;
}
.star-overlay {
  position: absolute;
  top: 5px;
  right: 10px;
  transform: translateX(-25%);
  display: flex;
  flex-direction: row;
  z-index: 10;
  background: rgba(255, 255, 255, 0.85);
  border-radius: 6px;
  padding: 1px 4px 1px 2px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.08);
  font-size: 0.85em;
}
.star-overlay .v-icon {
  font-size: 16px !important;
  width: 16px;
  height: 16px;
}
.image-card {
  position: relative;
}
.v-card {
  position: relative;
  overflow: visible;
}
.v-img {
  display: block;
  position: relative;
  z-index: 1;
}
</style>
