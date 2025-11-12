<template>
  <div class="likeness-rows" ref="likenessRowsContainer">
    <div class="likeness-placeholder" style="margin-bottom: 24px; color: #1976d2; font-size: 1.2em; text-align: center;">
      Likeness view placeholder: No likeness data yet.
    </div>
    <div v-for="(row, rowIdx) in visibleRows" :key="rowIdx" class="likeness-row">
      <div v-for="img in row" :key="img.id" class="likeness-image-card">
        <img :src="`${backendUrl}/thumbnails/${img.id}`" class="likeness-img" />
        <div class="likeness-metrics">
          <span>Res: {{ img.resolution }}</span>
          <span>Sharp: {{ img.sharpness }}</span>
          <span>Noise: {{ img.noisiness }}</span>
        </div>
      </div>
    </div>
    <div v-if="loading" class="loading-indicator">Loading...</div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue';
const props = defineProps({
  backendUrl: String,
  likenessRows: Array, // Array of arrays: [[img1, img2, ...], ...]
});
const visibleRows = ref([]);
const loading = ref(false);
const pageSize = 10;
let pageOffset = 0;
const likenessRowsContainer = ref(null);

function loadMoreRows() {
  if (loading.value) return;
  loading.value = true;
  // Simulate async loading; replace with backend call if needed
  setTimeout(() => {
    const nextRows = props.likenessRows.slice(pageOffset, pageOffset + pageSize);
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
  loadMoreRows();
  // Attach scroll listener for infinite scroll
  if (likenessRowsContainer.value) {
    likenessRowsContainer.value.addEventListener('scroll', onScroll);
  }
});

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
