<template>
  <div v-if="visible" class="selection-bar-overlay">
    <div class="selection-bar-content">
      <div class="selection-bar-left">
        <button
          class="restore-btn"
          :disabled="restoreDisabled"
          @click="$emit('restore-scrapheap')"
        >
          Restore All
        </button>
      </div>
      <div class="selection-bar-actions">
        <button
          class="delete-btn"
          :disabled="disabled"
          @click="$emit('empty-scrapheap')"
        >
          Empty Scrap Heap
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  visible: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  restoreDisabled: { type: Boolean, default: false },
});
</script>

<style scoped>
.selection-bar-overlay {
  position: absolute !important;
  left: 0;
  top: 0;
  width: 100%;
  z-index: 100;
  background: rgba(var(--v-theme-background), 0.95);
  padding: 4px 8px;
  margin: 0;
  height: 38px;
  box-sizing: border-box;
}
.selection-bar-content {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}
.selection-bar-left {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}
.selection-bar-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
  flex-wrap: nowrap;
}
.restore-btn {
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
  border: none;
  padding: 4px 10px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.85rem;
  font-weight: 500;
  white-space: nowrap;
}
.restore-btn:hover {
  filter: brightness(1.3);
}
.restore-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.delete-btn {
  background: rgb(var(--v-theme-error));
  color: #fff;
  border: none;
  padding: 4px 12px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.85rem;
  font-weight: 500;
  white-space: nowrap;
}
.delete-btn:hover {
  filter: brightness(1.3);
}
.delete-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
