<template>
  <div class="search-result-bar">
    <span class="search-result-status">
      <template v-if="imagesLoading">
        <v-progress-circular
          indeterminate
          size="16"
          width="2"
          color="primary"
          class="search-result-spinner"
        ></v-progress-circular>
        <span>Searching…</span>
      </template>
      <template v-else>
        <span>Search result found {{ count }} items</span>
        <span v-if="showScopeNote" class="search-result-scope">
          • Searched {{ categoryLabel }} only
        </span>
      </template>
    </span>
    <div class="search-result-actions">
      <v-btn v-if="showSearchAll" variant="tonal" @click="$emit('search-all')">
        Search All Pictures
      </v-btn>
      <v-btn color="primary" @click="$emit('clear')">Clear Search</v-btn>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue";

const props = defineProps({
  imagesLoading: { type: Boolean, default: false },
  count: { type: Number, default: 0 },
  categoryLabel: { type: String, default: "Category" },
  isAllPicturesActive: { type: Boolean, default: false },
});

defineEmits(["clear", "search-all"]);

const showScopeNote = computed(
  () => !props.imagesLoading && !props.isAllPicturesActive,
);

const showSearchAll = computed(
  () => !props.imagesLoading && !props.isAllPicturesActive,
);
</script>

<style scoped>
.search-result-bar {
  position: absolute;
  bottom: 0;
  left: 0;
  width: 100%;
  z-index: 200;
  background-color: #f5f5f5;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px;
  box-shadow: 0 -2px 4px rgba(0, 0, 0, 0.1);
}

.search-result-status {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.search-result-spinner {
  flex: 0 0 auto;
}

.search-result-scope {
  color: rgba(0, 0, 0, 0.6);
}

.search-result-actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
</style>
