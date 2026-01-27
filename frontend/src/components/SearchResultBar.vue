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
      <template v-else> Search result found {{ count }} items </template>
    </span>
    <v-btn color="primary" @click="$emit('clear')">Clear</v-btn>
  </div>
</template>

<script setup>
defineProps({
  imagesLoading: { type: Boolean, default: false },
  count: { type: Number, default: 0 },
});

defineEmits(["clear"]);
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
</style>
