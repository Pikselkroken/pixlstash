<template>
  <div v-if="visible" class="selection-bar-overlay">
    <div class="selection-bar-content">
      <div class="selection-bar-left">
        <button class="clear-btn" @click="$emit('clear-selection')">
          Clear
        </button>
        <span class="selection-count">{{ selectedCount }} selected</span>
      </div>
      <div class="selection-bar-actions">
        <button
          v-if="
            selectedCharacter &&
            selectedCharacter !== $props.allPicturesId &&
            selectedCharacter !== $props.unassignedPicturesId
          "
          class="remove-btn"
          @click="$emit('remove-from-group')"
        >
          {{ `Remove from ${selectedGroupName ? selectedGroupName : "group"}` }}
        </button>
        <button class="delete-btn" @click="$emit('delete-selected')">
          Delete
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue";
const props = defineProps({
  selectedCount: Number,
  selectedCharacter: String,
  selectedSet: String,
  selectedGroupName: String,
  visible: Boolean,
});
</script>

<style scoped>
.selection-bar-overlay {
  position: absolute !important;
  left: 0;
  bottom: 0;
  width: 100%;
  z-index: 100;
  background: rgba(195, 205, 210, 0.95);
  padding: 8px 16px 8px 16px !important;
  margin: 0;
  height: 52px;
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
}
.selection-count {
  font-weight: bold;
  font-size: 1.1em;
  text-align: left;
}
.selection-bar-actions {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-left: auto;
}
.clear-btn {
  background: #eee;
  border: none;
  padding: 6px 14px;
  border-radius: 4px;
  cursor: pointer;
}
.remove-btn {
  background: #ffd700;
  border: none;
  padding: 6px 14px;
  border-radius: 4px;
  cursor: pointer;
}
.delete-btn {
  background: #e53935;
  color: #fff;
  border: none;
  padding: 6px 18px;
  border-radius: 4px;
  cursor: pointer;
}
</style>
