<template>
  <div v-if="visible" class="selection-bar-overlay">
    <div class="selection-bar-content">
      <div class="selection-bar-left">
        <button class="clear-btn" @click="$emit('clear-selection')">
          Clear
        </button>
        <span v-if="selectedCount > 0" class="selection-count"
          >{{ selectedCount }} Images selected</span
        >
        <span v-if="selectedFaceCount > 0" class="selection-face-count">
          {{ selectedFaceCount }} Faces selected
        </span>
      </div>
      <div class="selection-bar-actions">
        <button
          v-if="selectedCount > 0"
          class="refresh-btn"
          type="button"
          title="Refresh tags for selected images"
          @click="$emit('refresh-tags')"
        >
          <v-icon size="16">mdi-refresh</v-icon>
          <span>Refresh Tags</span>
        </button>
        <AddToSetControl
          v-if="selectedCount > 0"
          :backend-url="backendUrl"
          :picture-ids="selectedImageIds"
        />
        <AddToCharacterControl
          v-if="selectedCount > 0"
          :backend-url="backendUrl"
          :picture-ids="selectedImageIds"
          @added="$emit('add-to-character', $event)"
        />
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
        <button
          v-if="selectedCount > 0"
          class="delete-btn"
          @click="$emit('delete-selected')"
        >
          Delete Pictures
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import AddToSetControl from "./AddToSetControl.vue";
import AddToCharacterControl from "./AddToCharacterControl.vue";
const props = defineProps({
  selectedCount: Number,
  selectedFaceCount: { type: Number, default: 0 },
  selectedCharacter: String,
  selectedSet: String,
  selectedGroupName: String,
  visible: Boolean,
  allPicturesId: { type: String, required: true },
  unassignedPicturesId: { type: String, required: true },
  backendUrl: { type: String, required: true },
  selectedImageIds: { type: Array, default: () => [] },
});
</script>

<style scoped>
.selection-bar-overlay {
  position: absolute !important;
  left: 0;
  bottom: 0;
  width: 100%;
  z-index: 100;
  background: rgba(var(--v-theme-background), 0.95);
  padding: 2px 8px 8px 16px !important;
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
.refresh-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: rgba(var(--v-theme-on-surface), 0.08);
  color: rgba(var(--v-theme-on-surface), 0.85);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  padding: 6px 12px;
  border-radius: 999px;
  cursor: pointer;
  font-size: 0.9em;
}
.refresh-btn:hover {
  background: rgba(var(--v-theme-on-surface), 0.16);
}
.clear-btn {
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
  border: none;
  padding: 6px 14px;
  border-radius: 4px;
  cursor: pointer;
}
.clear-btn:hover {
  filter: brightness(1.3);
}
.remove-btn {
  background: rgb(var(--v-theme-warning));
  color: rgb(var(--v-theme-on-warning));
  border: none;
  padding: 6px 14px;
  border-radius: 4px;
  cursor: pointer;
}
.remove-btn:hover {
  filter: brightness(1.3);
}
.delete-btn {
  background: rgb(var(--v-theme-error));
  color: #fff;
  border: none;
  padding: 6px 18px;
  border-radius: 4px;
  cursor: pointer;
}
.delete-btn:hover {
  filter: brightness(1.3);
}
</style>
