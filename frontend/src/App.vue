
<script setup>
import { computed, ref, onMounted, watch, onBeforeUnmount } from 'vue'
// Selection state for file manager
const selectedImageIds = ref([])
let lastSelectedIndex = null

function handleImageSelect(img, idx, event) {
  const id = img.id
  const isSelected = selectedImageIds.value.includes(id)
  const isCtrl = event.ctrlKey || event.metaKey
  const isShift = event.shiftKey

  if (isShift && lastSelectedIndex !== null) {
    // Range select
    const start = Math.min(lastSelectedIndex, idx)
    const end = Math.max(lastSelectedIndex, idx)
    const rangeIds = images.value.slice(start, end + 1).map(i => i.id)
    const newSelection = isCtrl
      ? Array.from(new Set([...selectedImageIds.value, ...rangeIds]))
      : rangeIds
    selectedImageIds.value = newSelection
  } else if (isCtrl) {
    // Toggle selection
    if (isSelected) {
      selectedImageIds.value = selectedImageIds.value.filter(i => i !== id)
    } else {
      selectedImageIds.value = [...selectedImageIds.value, id]
    }
    lastSelectedIndex = idx
  } else {
    // Single select
    selectedImageIds.value = [id]
    lastSelectedIndex = idx
  }
}

const isImageSelected = (id) => selectedImageIds.value.includes(id)

// Logic to determine if a selected image is on the outer edge of a selection group
const getSelectionBorderClasses = (idx) => {
  if (!isImageSelected(images.value[idx]?.id)) return ''
  const cols = columns.value
  const total = images.value.length
  const row = Math.floor(idx / cols)
  const col = idx % cols
  let classes = []
  // Check neighbors: top, right, bottom, left
  // Top
  if (row === 0 || !isImageSelected(images.value[(row - 1) * cols + col]?.id)) {
    classes.push('selected-border-top')
  }
  // Bottom
  if (row === Math.floor((total - 1) / cols) || !isImageSelected(images.value[(row + 1) * cols + col]?.id)) {
    classes.push('selected-border-bottom')
  }
  // Left
  if (col === 0 || !isImageSelected(images.value[row * cols + (col - 1)]?.id)) {
    classes.push('selected-border-left')
  }
  // Right
  if (col === cols - 1 || !isImageSelected(images.value[row * cols + (col + 1)]?.id)) {
    classes.push('selected-border-right')
  }
  return classes.join(' ')
}

const characters = ref([])
const loading = ref(false)
const error = ref(null)

const selectedCharacter = ref(null)
const images = ref([])
const imagesLoading = ref(false)
const imagesError = ref(null)

const BACKEND_URL = 'http://localhost:9537'

// Thumbnail size slider state
const thumbnailSizes = [128, 192, 256]
const thumbnailLabels = ['Small', 'Medium', 'Large']
const thumbnailSize = ref(256)

// Responsive columns
const columns = ref(5)
const gridContainer = ref(null)

function updateColumns() {
  if (!gridContainer.value) return
  const containerWidth = gridContainer.value.offsetWidth
  columns.value = Math.max(1, Math.floor(containerWidth / (thumbnailSize.value + 32)))
}

async function fetchCharacters() {
  loading.value = true
  error.value = null
  try {
    const res = await fetch(`${BACKEND_URL}/characters`)
    if (!res.ok) throw new Error('Failed to fetch characters')
    characters.value = await res.json()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}


onMounted(() => {
  fetchCharacters()
  window.addEventListener('resize', updateColumns)
  watch(thumbnailSize, updateColumns)
  setTimeout(updateColumns, 100) // Initial update after mount
})

watch(selectedCharacter, async (id) => {
  images.value = []
  imagesError.value = null
  if (!id) return
  imagesLoading.value = true
  try {
    const res = await fetch(`${BACKEND_URL}/pictures?character_id=${encodeURIComponent(id)}&info=true`)
    if (!res.ok) throw new Error('Failed to fetch images')
    images.value = await res.json()
  } catch (e) {
    imagesError.value = e.message
  } finally {
    imagesLoading.value = false
  }
})

// Full image overlay state
const overlayOpen = ref(false)
const overlayImage = ref(null)

function openOverlay(img) {
  overlayImage.value = img
  overlayOpen.value = true
}

function closeOverlay() {
  overlayOpen.value = false
  overlayImage.value = null
}




  function handleOverlayKeydown(e) {
    if (overlayOpen.value) {
      if (e.key === 'ArrowLeft') {
        showPrevImage()
        e.preventDefault()
        return
      } else if (e.key === 'ArrowRight') {
        showNextImage()
        e.preventDefault()
        return
      } else if (e.key === 'Escape') {
        closeOverlay()
        e.preventDefault()
        return
      }
    }
    // Grid navigation and selection
    if (!images.value.length) return
    const cols = columns.value
    let idx = lastSelectedIndex
    if (idx === null || idx < 0 || idx >= images.value.length) idx = 0
    let nextIdx = idx
    if (e.key === 'ArrowLeft') {
      if (idx % cols > 0) nextIdx = idx - 1
      else return
    } else if (e.key === 'ArrowRight') {
      if (idx % cols < cols - 1 && idx + 1 < images.value.length) nextIdx = idx + 1
      else return
    } else if (e.key === 'ArrowUp') {
      if (idx - cols >= 0) nextIdx = idx - cols
      else return
    } else if (e.key === 'ArrowDown') {
      if (idx + cols < images.value.length) nextIdx = idx + cols
      else return
    } else {
      return
    }
    const isCtrl = e.ctrlKey || e.metaKey
    const isShift = e.shiftKey
    if (isShift && lastSelectedIndex !== null) {
      // Range select
      const start = Math.min(lastSelectedIndex, nextIdx)
      const end = Math.max(lastSelectedIndex, nextIdx)
      const rangeIds = images.value.slice(start, end + 1).map(i => i.id)
      const newSelection = isCtrl
        ? Array.from(new Set([...selectedImageIds.value, ...rangeIds]))
        : rangeIds
      selectedImageIds.value = newSelection
    } else if (isCtrl) {
      // Toggle selection of nextIdx
      const id = images.value[nextIdx].id
      if (selectedImageIds.value.includes(id)) {
        selectedImageIds.value = selectedImageIds.value.filter(i => i !== id)
      } else {
        selectedImageIds.value = [...selectedImageIds.value, id]
      }
      lastSelectedIndex = nextIdx
    } else {
      // Single select
      selectedImageIds.value = [images.value[nextIdx].id]
      lastSelectedIndex = nextIdx
    }
    e.preventDefault()
  }

  onMounted(() => {
    fetchCharacters()
    window.addEventListener('resize', updateColumns)
    watch(thumbnailSize, updateColumns)
    setTimeout(updateColumns, 100) // Initial update after mount
    window.addEventListener('keydown', handleOverlayKeydown)
  })

  onBeforeUnmount(() => {
    window.removeEventListener('keydown', handleOverlayKeydown)
  })
function showPrevImage() {
  if (!overlayImage.value || !images.value.length) return
  const idx = images.value.findIndex(i => i.id === overlayImage.value.id)
  const prevIdx = (idx - 1 + images.value.length) % images.value.length
  overlayImage.value = images.value[prevIdx]
}

function showNextImage() {
  if (!overlayImage.value || !images.value.length) return
  const idx = images.value.findIndex(i => i.id === overlayImage.value.id)
  const nextIdx = (idx + 1) % images.value.length
  overlayImage.value = images.value[nextIdx]
}
</script>

<template>
  <v-app>
    <div class="file-manager">
      <aside class="sidebar">
        <div class="sidebar-title">Characters</div>
        <div v-if="loading" class="sidebar-loading">Loading...</div>
        <div v-if="error" class="sidebar-error">{{ error }}</div>
        <div
          v-for="char in characters"
          :key="char.id"
          :class="['sidebar-item', { active: selectedCharacter === char.id }]"
          @click="selectedCharacter = char.id"
        >
          {{ char.name }}
        </div>
      </aside>
      <main class="main-area">
        <div class="main-content">
          <!-- Thumbnail size slider -->
          <div class="thumbnail-slider">
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
              style="max-width: 220px; display: inline-block; vertical-align: middle; margin: 0 8px;"
            />
            <v-icon small>mdi-image-size-select-large</v-icon>
          </div>
          <template v-if="selectedCharacter">
            <div v-if="imagesLoading" class="empty-state">Loading images...</div>
            <div v-else-if="imagesError" class="empty-state">{{ imagesError }}</div>
            <div v-else-if="images.length === 0" class="empty-state">No images found for this character.</div>
            <div v-else class="image-grid" :style="{ gridTemplateColumns: `repeat(${columns}, 1fr)` }" ref="gridContainer">
              <div
                v-for="(img, idx) in images"
                :key="img.id"
                class="image-card"
                :class="[isImageSelected(img.id) ? 'selected' : '', getSelectionBorderClasses(idx)]"
                @click="handleImageSelect(img, idx, $event)"
              >
                <v-card>
                  <v-img
                    :src="`${BACKEND_URL}/thumbnails/${img.id}`"
                    :height="thumbnailSize"
                    :width="thumbnailSize"
                    @click.stop="(e) => {
                      if (e.ctrlKey || e.metaKey || e.shiftKey) {
                        handleImageSelect(img, idx, e)
                      } else {
                        openOverlay(img)
                      }
                    }"
                    style="cursor:pointer;"
                  />
                  <v-card-title>{{ img.description || 'Image' }}</v-card-title>
                </v-card>
              </div>
    <!-- Full image overlay -->
    <div v-if="overlayOpen" class="image-overlay" @click.self="closeOverlay">
      <div class="overlay-content">
        <button class="overlay-close" @click="closeOverlay" aria-label="Close">&times;</button>
        <div class="overlay-flex-row">
          <button class="overlay-nav overlay-nav-left" @click.stop="showPrevImage" aria-label="Previous">&#8592;</button>
          <div class="overlay-img-container">
            <img
              v-if="overlayImage"
              :src="`${BACKEND_URL}/pictures/${overlayImage.id}`"
              :alt="overlayImage.description || 'Full Image'"
              class="overlay-img"
            />
            <div class="overlay-desc">{{ overlayImage?.description }}</div>
          </div>
          <button class="overlay-nav overlay-nav-right" @click.stop="showNextImage" aria-label="Next">&#8594;</button>
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
  content: '';
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
  background: #fff;
  min-width: 0;
  min-height: 0;
  box-sizing: border-box;
}
.sidebar {
  width: 220px;
  background: #fff;
  border-right: 1px solid #eee;
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
.sidebar-item.active, .sidebar-item:hover {
  background: #f0f0f0;
}
.sidebar-loading, .sidebar-error {
  padding: 8px 16px;
  color: #888;
}
.main-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: #fff;
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
  background: rgba(0,0,0,0.85);
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
  box-shadow: 0 2px 16px rgba(0,0,0,0.5);
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
  box-shadow: 0 1px 8px rgba(0,0,0,0.4);
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

</style>
