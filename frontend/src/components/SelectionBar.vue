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
          v-if="selectedCount > 0 && !isScrapheapView"
          class="refresh-btn"
          type="button"
          title="Refresh tags for selected images"
          @click="$emit('refresh-tags')"
        >
          <v-icon size="16">mdi-refresh</v-icon>
          <span>Refresh Tags</span>
        </button>
        <AddToSetControl
          v-if="selectedCount > 0 && !isScrapheapView"
          :backend-url="backendUrl"
          :picture-ids="selectedImageIds"
          @added="$emit('added-to-set', $event)"
        />
        <AddToCharacterControl
          v-if="selectedCount > 0 && !isScrapheapView"
          :backend-url="backendUrl"
          :picture-ids="selectedImageIds"
          @added="$emit('add-to-character', $event)"
        />
        <button
          v-if="showRemoveStackButton"
          class="stack-btn"
          type="button"
          title="Remove selected images from their stack"
          @click="$emit('remove-from-stack')"
        >
          <v-icon size="16">mdi-layers-minus</v-icon>
          <span>Remove From Stack</span>
        </button>
        <button
          v-else-if="selectedCount > 1 && !isScrapheapView"
          class="stack-btn"
          type="button"
          title="Create a stack from the selected images"
          @click="$emit('create-stack')"
        >
          <v-icon size="16">mdi-layers</v-icon>
          <span>Create Stack</span>
        </button>
        <button
          v-if="showGroupStackButton"
          class="stack-btn"
          type="button"
          title="Create stacks from selected likeness groups"
          @click="$emit('create-stacks-from-groups')"
        >
          <v-icon size="16">mdi-layers-plus</v-icon>
          <span>Create Stacks from Groups</span>
        </button>
        <button
          v-if="showRemoveButton"
          class="remove-btn"
          @click="$emit('remove-from-group')"
        >
          {{ removeButtonLabel }}
        </button>
        <button
          v-if="selectedCount > 0"
          class="delete-btn"
          @click="$emit('delete-selected')"
        >
          {{ deleteButtonLabel }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue";
import AddToSetControl from "./AddToSetControl.vue";
import AddToCharacterControl from "./AddToCharacterControl.vue";
const props = defineProps({
  selectedCount: Number,
  selectedFaceCount: { type: Number, default: 0 },
  selectedCharacter: String,
  selectedSet: String,
  selectedGroupName: String,
  selectedSort: { type: String, default: "" },
  visible: Boolean,
  allPicturesId: { type: String, required: true },
  unassignedPicturesId: { type: String, required: true },
  scrapheapPicturesId: { type: String, required: true },
  backendUrl: { type: String, required: true },
  selectedImageIds: { type: Array, default: () => [] },
  showRemoveFromStack: { type: Boolean, default: false },
});

const STACKS_SORT_KEY = "PICTURE_STACKS";

const isScrapheapView = computed(() => {
  const scrapheapId = String(
    props.scrapheapPicturesId || "SCRAPHEAP",
  ).toUpperCase();
  const selected = String(props.selectedCharacter || "").toUpperCase();
  return selected === scrapheapId;
});

const showRemoveButton = computed(() => {
  if (props.selectedCount <= 0) return false;
  if (isScrapheapView.value) return true;
  return (
    props.selectedCharacter &&
    props.selectedCharacter !== props.allPicturesId &&
    props.selectedCharacter !== props.unassignedPicturesId
  );
});

const removeButtonLabel = computed(() => {
  if (isScrapheapView.value) return "Restore Selected";
  return `Remove from ${props.selectedGroupName ? props.selectedGroupName : "group"}`;
});

const deleteButtonLabel = computed(() => {
  if (isScrapheapView.value) return "Permanently Delete Pictures";
  return "Delete Pictures";
});

const showGroupStackButton = computed(() => {
  if (isScrapheapView.value) return false;
  return props.selectedCount > 0 && props.selectedSort === STACKS_SORT_KEY;
});

const showRemoveStackButton = computed(() => {
  if (isScrapheapView.value) return false;
  return props.showRemoveFromStack === true;
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
  padding: 2px 8px 8px 8px !important;
  margin: 0;
  height: 48px;
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
  background-color: rgba(var(--v-theme-dark-surface), 0.6);
  color: rgba(var(--v-theme-on-dark-surface), 1);
  border: none;
  padding: 6px 12px;
  border-radius: 4px;
  cursor: pointer;
}
.refresh-btn:hover {
  filter: brightness(1.75);
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
.stack-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: rgba(var(--v-theme-primary), 0.15);
  color: rgb(var(--v-theme-on-background));
  border: 1px solid rgba(var(--v-theme-primary), 0.4);
  padding: 6px 12px;
  border-radius: 4px;
  cursor: pointer;
}
.stack-btn:hover {
  filter: brightness(1.2);
}
</style>
