

<script setup>
import { ref, onMounted, watch } from 'vue'

const characters = ref([])
const loading = ref(false)
const error = ref(null)

const selectedCharacter = ref(null)
const images = ref([])
const imagesLoading = ref(false)
const imagesError = ref(null)

const BACKEND_URL = 'http://localhost:9537'

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


onMounted(fetchCharacters)

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
          <template v-if="selectedCharacter">
            <div v-if="imagesLoading" class="empty-state">Loading images...</div>
            <div v-else-if="imagesError" class="empty-state">{{ imagesError }}</div>
            <div v-else-if="images.length === 0" class="empty-state">No images found for this character.</div>
            <div v-else class="image-grid">
              <div v-for="img in images" :key="img.id" class="image-card">
                <v-card>
                  <v-img :src="`${BACKEND_URL}/thumbnails/${img.id}`" height="256" width="256" />
                  <v-card-title>{{ img.description || 'Image' }}</v-card-title>
                </v-card>
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
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 16px;
  width: 100%;
  padding: 8px 0;
  max-height: calc(100vh - 140px); /* Adjust as needed for header/sidebar */
  overflow-y: auto;
}
.image-card {
  min-width: 0;
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
  padding: 16px;
}
.main-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  justify-content: flex-start;
}
.empty-state {
  color: #aaa;
  font-size: 1.2rem;
  margin-top: 32px;
  text-align: center;
}
</style>
