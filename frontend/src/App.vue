<script setup>
import { computed, ref, onMounted, watch, onBeforeUnmount } from "vue";
import { VTextField } from "vuetify/components";
import unknownPerson from "./assets/unknown-person.png"; // Import for unknown character icon

// Drag-and-drop overlay state (for image grid only)
const dragOverlayVisible = ref(false);
const dragOverlayMessage = ref('');
const gridContainer = ref(null); // already used for grid

const PIL_IMAGE_EXTENSIONS = [
  'jpg', 'jpeg', 'png', 'bmp', 'gif', 'tiff', 'tif', 'webp', 'ppm', 'pgm', 'pbm', 'pnm', 'ico', 'icns', 'svg', 'dds', 'msp', 'pcx', 'xbm', 'im', 'fli', 'flc', 'eps', 'psd', 'pdf', 'jp2', 'j2k', 'jpf', 'jpx', 'j2c', 'jpc', 'tga', 'ras', 'sgi', 'rgb', 'rgba', 'bw', 'exr', 'hdr', 'pic', 'pict', 'pct', 'cur', 'emf', 'wmf', 'heic', 'heif', 'avif'
];
function isSupportedImageFile(file) {
  const ext = file.name.split('.').pop().toLowerCase();
  return PIL_IMAGE_EXTENSIONS.includes(ext);
}
function handleGridDragEnter(e) {
  console.debug('handleGridDragEnter', e);
  if (!e.dataTransfer || !e.dataTransfer.items) return;
  // Accept any image/* MIME type for overlay
  const hasImageType = Array.from(e.dataTransfer.items).some(item => {
    return item.kind === 'file' && item.type.startsWith('image/');
  });
  console.debug('hasImageType', hasImageType);
  if (hasImageType) {
    dragOverlayVisible.value = true;
    dragOverlayMessage.value = 'Drop files here to import';
    e.preventDefault();
    console.debug('Overlay shown');
  } else {
    dragOverlayVisible.value = false;
    console.debug('Overlay hidden (unsupported)');
  }
}
function handleGridDragOver(e) {
  console.debug('handleGridDragOver', e, 'overlayVisible:', dragOverlayVisible.value);
  if (dragOverlayVisible.value) e.preventDefault();
}
function handleGridDragLeave(e) {
  console.debug('handleGridDragLeave', e);
  // Only hide overlay if leaving the .image-grid entirely
  if (!e.relatedTarget || !e.currentTarget.contains(e.relatedTarget)) {
    dragOverlayVisible.value = false;
    console.debug('Overlay hidden (left grid)');
  } else {
    console.debug('Drag still inside grid, overlay remains');
  }
}
function handleGridDrop(e) {
  console.debug('handleGridDrop', e);
  dragOverlayVisible.value = false;
}

// Selection state for file manager
const selectedImageIds = ref([]);
let lastSelectedIndex = null;

// Sidebar visibility state
const sidebarVisible = ref(true);

// Overlay state for full image view
const overlayOpen = ref(false);
const overlayImage = ref(null);

// Trophy button color: dark blue when not selected, orange when selected
const trophyButtonColor = (charId) =>
  selectedCharacter.value === charId && selectedReferenceMode.value
    ? 'orange'
    : '#29405a'; // darker blue than sidebar

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

// Handle drop on Reference Images child
function onReferenceDrop(characterId, event) {
  dragOverCharacter.value = null;
  try {
    const data = JSON.parse(event.dataTransfer.getData("application/json"));
    if (!data.imageIds || !Array.isArray(data.imageIds)) return;
    assignImagesAsReference(data.imageIds, characterId);
  } catch (e) {}
}

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
const selectedReferenceMode = ref(false); // true = show reference images for selected character
const expandedCharacters = ref({}); // { [characterId]: true/false }
// Collapsible sidebar sections
const sidebarSections = ref({
  pictures: true,
  people: true,
});
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
  // Always select All Pictures at startup
  selectedCharacter.value = ALL_PICTURES_ID;
  selectedReferenceMode.value = false;
  fetchCharacters().then(() => {
    // After loading characters, ensure All Pictures is still selected
    selectedCharacter.value = ALL_PICTURES_ID;
    selectedReferenceMode.value = false;
    // Explicitly trigger image loading if already on All Pictures
    if (
      selectedCharacter.value === ALL_PICTURES_ID &&
      !selectedReferenceMode.value
    ) {
      // This mimics the watcher logic
      images.value = [];
      imagesError.value = null;
      selectedImageIds.value = [];
      imagesLoading.value = true;
      let url = `${BACKEND_URL}/pictures?info=true`;
      fetch(url)
        .then((res) => {
          if (!res.ok) throw new Error("Failed to fetch images");
          return res.json();
        })
        .then((baseImages) => {
          images.value = baseImages.map((img) => ({
            ...img,
            score: typeof img.score !== "undefined" ? img.score : null,
          }));
          setTimeout(updateColumns, 0);
        })
        .catch((e) => {
          imagesError.value = e.message;
        })
        .finally(() => {
          imagesLoading.value = false;
        });
    }
  });
  window.addEventListener("resize", updateColumns);
  watch(thumbnailSize, updateColumns);
  setTimeout(updateColumns, 100); // Initial update after mount
});

watch([selectedCharacter, selectedReferenceMode], async ([id, refMode]) => {
  images.value = [];
  imagesError.value = null;
  selectedImageIds.value = [];
  if (!id) return;
  imagesLoading.value = true;
  try {
    let url;
    if (id === ALL_PICTURES_ID) {
      url = `${BACKEND_URL}/pictures?info=true`;
    } else if (id === UNASSIGNED_PICTURES_ID) {
      url = `${BACKEND_URL}/pictures?character_id=&info=true`;
    } else if (refMode) {
      url = `${BACKEND_URL}/characters/reference_pictures/${encodeURIComponent(
        id
      )}`;
    } else {
      url = `${BACKEND_URL}/pictures?character_id=${encodeURIComponent(
        id
      )}&info=true`;
    }
    const res = await fetch(url);
    if (!res.ok) throw new Error("Failed to fetch images");
    let baseImages = await res.json();
    if (refMode && baseImages.reference_pictures) {
      baseImages = baseImages.reference_pictures;
      // Map picture_id to id for frontend compatibility
      baseImages = baseImages.map((img) => ({ ...img, id: img.picture_id }));
    }
    // Filter for is_reference === true if in reference mode (legacy, not needed for new endpoint)
    if (refMode && !baseImages[0]?.id) {
      baseImages = baseImages.filter(
        (img) => img.is_reference == true || img.is_reference == 1
      );
    }
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
    await fetchCharacters();
    if (
      selectedCharacter.value === characterId ||
      selectedCharacter.value === ALL_PICTURES_ID ||
      selectedCharacter.value === UNASSIGNED_PICTURES_ID
    ) {
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

// Assign images as reference images for a character (set is_reference=true and character_id)
async function assignImagesAsReference(imageIds, characterId) {
  try {
    await Promise.all(
      imageIds.map(async (id) => {
        // Fetch image to check if it already has the character
        let needsChar = true;
        try {
          const res = await fetch(`${BACKEND_URL}/pictures/${id}`);
          if (res.ok) {
            const data = await res.json();
            if (data.character_id === characterId) needsChar = false;
          }
        } catch (e) {}
        // Always set is_reference=true, and set character_id if needed
        let url = `${BACKEND_URL}/pictures/${id}?is_reference=1`;
        if (needsChar)
          url += `&character_id=${encodeURIComponent(characterId)}`;
        const res2 = await fetch(url, { method: "PATCH" });
        if (!res2.ok)
          throw new Error(`Failed to set reference for image ${id}`);
      })
    );
    await fetchCharacters();
    // Refresh images if needed
    if (
      selectedCharacter.value === characterId ||
      selectedCharacter.value === ALL_PICTURES_ID ||
      selectedCharacter.value === UNASSIGNED_PICTURES_ID
    ) {
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
    alert("Failed to set reference: " + (e.message || e));
  }
}
</script>

<template>
  <v-app>
    <div class="app-viewport">
      <div class="top-toolbar">
      <v-btn
        icon
        @click="sidebarVisible = !sidebarVisible"
        title="Toggle sidebar"
        class="sidebar-toggle-btn"
        style="margin-right: 16px;"
      >
        <v-icon>{{ sidebarVisible ? 'mdi-menu-open' : 'mdi-menu' }}</v-icon>
      </v-btn>
      <v-text-field
        v-model="searchQuery"
        placeholder="Search images..."
        hide-details
        dense
        solo
        clearable
        prepend-inner-icon="mdi-magnify"
        style="min-width: 400px; max-width: 800px; margin-right: 16px; background-color: a;"
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
      <div class="file-manager">
      <aside v-if="sidebarVisible" class="sidebar">
  <div class="sidebar-section-header" @click="sidebarSections.pictures = !sidebarSections.pictures">
          <v-icon small style="margin-right: 8px;">{{ sidebarSections.pictures ? 'mdi-chevron-down' : 'mdi-chevron-right' }}</v-icon>
          Pictures
        </div>
        <transition name="fade">
          <div v-show="sidebarSections.pictures">
            <div
              :class="[
                'sidebar-list-item',
                { active: selectedCharacter === ALL_PICTURES_ID },
              ]"
              @click="selectedCharacter = ALL_PICTURES_ID"
            >
              <span class="sidebar-list-icon">
                <v-icon size="44">mdi-image-multiple</v-icon>
              </span>
              <span class="sidebar-list-label">All Pictures</span>
            </div>
            <div
              :class="[
                'sidebar-list-item',
                { active: selectedCharacter === UNASSIGNED_PICTURES_ID },
              ]"
              @click="selectedCharacter = UNASSIGNED_PICTURES_ID"
            >
              <span class="sidebar-list-icon">
                <v-icon size="44">mdi-help-circle-outline</v-icon>
              </span>
              <span class="sidebar-list-label">Unassigned Pictures</span>
            </div>
          </div>
        </transition>
  <div class="sidebar-section-header" @click="sidebarSections.people = !sidebarSections.people">
          <v-icon small style="margin-right: 8px;">{{ sidebarSections.people ? 'mdi-chevron-down' : 'mdi-chevron-right' }}</v-icon>
          People
        </div>
        <transition name="fade">
          <div v-show="sidebarSections.people">
            <div v-if="error" class="sidebar-error">{{ error }}</div>
            <div
              v-for="char in sortedCharacters"
              :key="char.id"
              class="sidebar-character-group"
            >
              <div
                :class="[
                  'sidebar-list-item',
                  {
                    active: selectedCharacter === char.id && !selectedReferenceMode,
                    droppable: dragOverCharacter === char.id,
                  },
                ]"
                @click="
                  selectedCharacter = char.id;
                  selectedReferenceMode = false;
                "
              >
                <span class="sidebar-list-icon">
                  <img
                    :src="characterThumbnails[char.id] ? characterThumbnails[char.id] : unknownPerson"
                    alt=""
                    class="sidebar-character-thumb"
                  />
                </span>
                <span class="sidebar-list-label">
                  {{ char.name.charAt(0).toUpperCase() + char.name.slice(1) }}
                </span>
                <v-btn
                  icon
                  flat
                  :color="trophyButtonColor(char.id)"
                  class="sidebar-trophy-btn"
                  @click.stop="
                    if (selectedCharacter === char.id && selectedReferenceMode) {
                      selectedReferenceMode = false;
                    } else {
                      selectedCharacter = char.id;
                      selectedReferenceMode = true;
                    }
                  "
                  title="Show Reference Images"
                >
                  <v-icon color="white">mdi-trophy</v-icon>
                </v-btn>
              </div>
            </div>
            <div v-if="loading" class="sidebar-loading">Loading...</div>
          </div>
        </transition>
      </aside>
  <main class="main-area" :class="{ 'full-width': !sidebarVisible }">
  <div :class="['main-content', selectedCharacter ? 'accent-border' : '']">
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
              style="position: relative;"
              @dragenter.prevent="handleGridDragEnter"
              @dragover.prevent="handleGridDragOver"
              @dragleave.prevent="handleGridDragLeave"
              @drop.prevent="handleGridDrop"
            >
              <div v-if="dragOverlayVisible" class="drag-overlay-grid">
                <span>{{ dragOverlayMessage }}</span>
              </div>
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
    </div>
  </v-app>
</template>

<style scoped>

.app-viewport {
  position: relative;
  inset: 0;
  width: 100vw;
  height: 100vh;
  overflow: hidden;
  z-index: 0;
}

.drag-overlay-grid {
  position: absolute;
  inset: 0;
  background: rgba(32, 32, 32, 0.25);
  color: #fff8e1;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 2.2rem;
  font-weight: 700;
  z-index: 1000;
  pointer-events: none;
  user-select: none;
  letter-spacing: 0.04em;
  text-shadow: 0 2px 8px #000a;
}

body {
  margin: 0;
  padding: 0;
}

.image-grid {
  display: grid;
  gap: 0;
  width: 100%;
  height: 100%;
  min-height: 0;
  flex: 1 1 0%;
  padding: 4px 12px 4px 4px; /* Extra right padding for scrollbar */
  overflow-y: auto;
  background: #eee;
  align-content: start;
  justify-content: start;
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
  width: 100vw;
  height: 100vh;
  min-height: 0;
  inset: 0;
  min-width: 0;
  background: #ccc;
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}
.sidebar {
  width: 280px;
  background: #506168ff;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  min-height: 100vh;
  box-sizing: border-box;
}
.sidebar-section-header {
  font-size: 1.2rem;
  font-weight: 800;
  padding: 2px 2px 2px 2px;
  margin-bottom: 2px;
  margin-top: 0px;
  border-radius: 0px;
  box-shadow: 0 1px 1px rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  cursor: pointer;
  user-select: none;
  background: #7f95aa;
  color: #fff;
  transition: background 0.2s, color 0.2s;
}
/* Fade transition for collapsible sections */
.fade-enter-active, .fade-leave-active {
  transition: opacity 0.2s;
}
.fade-enter-from, .fade-leave-to {
  opacity: 0;
}
.sidebar-list-item, .sidebar-list-item.active  {
  display: flex;
  align-items: center;
  min-height: 56px;
  padding: 8px 16px;
  cursor: pointer;
  border-radius: 0px;
  margin-bottom: 0px;
  font-size: 1em;
  font-weight: 500;
  background: transparent;
  color: #fff;
  transition: background 0.18s, color 0.18s;
  width: 100%;
}
.sidebar-list-item.active {
  background: #f0f0f055;
  color: #fff;
  border-right: 0;
  position: relative;
}

.sidebar-list-item.active::after {
  content: "";
  position: absolute;
  top: 0;
  right: 0;
  width: 20px;
  height: 100%;
  background: linear-gradient(to right, rgba(255, 165, 0, 0) 0%, rgba(255, 165, 0, 1.0) 100%);
  pointer-events: none;
  z-index: 2;
}

.sidebar-list-item:hover {
  background: #6c7a8a;
  color: #fff;
}

.sidebar-list-icon {
  display: flex;
  align-items: center;
  margin-right: 12px;
  width: 44px;
  min-width: 44px;
  justify-content: center;
}
.sidebar-list-label {
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  text-align: left;
}
.sidebar-character-thumb {
  width: 44px;
  height: 44px;
  object-fit: cover;
  border-radius: 6px;
  box-shadow: 0 0px 0px #bbb;
}
.sidebar-trophy-btn {
  margin-left: 4px;
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
  margin: 0;
  transition: width 0.2s;
}
.main-area.full-width {
  width: 100vw;
}
.sidebar-toggle-btn {
  min-width: 40px;
  min-height: 40px;
  margin-left: -8px;
}
.main-content {
  flex: 1 1 0%;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  justify-content: flex-start;
  padding: 0;
  border-left: 4px solid orange;
  transition: border-color 0.2s;
  min-height: 0;
  height: 100%;
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
  border-bottom: 2px solid #888;
  margin-bottom: 0;
  z-index: 2;
  position: relative;
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
