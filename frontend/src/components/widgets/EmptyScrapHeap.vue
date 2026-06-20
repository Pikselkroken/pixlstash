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
  top: var(--selbar-height, 48px);
  width: 100%;
  z-index: 100;
  background: rgba(var(--v-theme-background), 0.95);
  padding: 0 var(--space-3) var(--space-2);
  margin: 0;
  height: 30px;
  box-sizing: border-box;
  display: flex;
  align-items: center;
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
  gap: var(--space-4);
  min-width: 0;
}
.selection-bar-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-left: auto;
  flex-wrap: nowrap;
}
.restore-btn {
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
  border: none;
  padding: var(--space-1) var(--space-3);
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  line-height: 1.4;
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
  color: rgb(var(--v-theme-on-error));
  border: none;
  padding: var(--space-1) var(--space-4);
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  line-height: 1.4;
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
