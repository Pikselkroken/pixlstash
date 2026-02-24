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
        <div
          v-if="selectedCount > 0 && !isScrapheapView && pluginOptions.length"
          class="plugin-run-controls"
        >
          <v-menu
            v-model="pluginMenuOpen"
            :close-on-content-click="false"
            location-strategy="connected"
            location="bottom end"
            origin="top end"
            transition="scale-transition"
          >
            <template #activator="{ props: menuProps }">
              <button
                v-bind="menuProps"
                class="stack-btn"
                type="button"
                :disabled="!selectedPluginName"
              >
                <v-icon size="16">mdi-tune-variant</v-icon>
                <span>Run Plugin</span>
              </button>
            </template>
            <div class="plugin-menu-panel">
              <div class="plugin-menu-header">Image Plugin</div>
              <div class="plugin-menu-body">
                <label class="plugin-menu-label">Plugin</label>
                <select v-model="selectedPluginName" class="plugin-run-select">
                  <option
                    v-for="plugin in pluginOptions"
                    :key="plugin.name"
                    :value="plugin.name"
                  >
                    {{ plugin.display_name || plugin.name }}
                  </option>
                </select>

                <PluginParametersUI
                  v-model="pluginParameters"
                  :plugin="activePluginSchema"
                  :show-description="true"
                  tone="auto"
                  input-class="plugin-run-select"
                  label-class="plugin-menu-label"
                />

                <div class="plugin-menu-actions">
                  <button
                    class="stack-btn"
                    type="button"
                    :disabled="!selectedPluginName || !selectedImageIds.length"
                    @click="runSelectedPlugin"
                  >
                    <v-icon size="16">mdi-play</v-icon>
                    <span>Run</span>
                  </button>
                </div>
              </div>
            </div>
          </v-menu>
        </div>
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
import { computed, ref, watch } from "vue";
import AddToSetControl from "./AddToSetControl.vue";
import AddToCharacterControl from "./AddToCharacterControl.vue";
import PluginParametersUI from "./PluginParametersUI.vue";
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
  availablePlugins: { type: Array, default: () => [] },
});

const emit = defineEmits([
  "clear-selection",
  "added-to-set",
  "add-to-character",
  "remove-from-stack",
  "create-stack",
  "create-stacks-from-groups",
  "remove-from-group",
  "delete-selected",
  "run-plugin",
]);

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

const pluginOptions = computed(() => {
  if (!Array.isArray(props.availablePlugins)) return [];
  return props.availablePlugins.filter((plugin) => plugin && plugin.name);
});

const selectedPluginName = ref("");
const pluginMenuOpen = ref(false);
const pluginParameters = ref({});

const activePluginSchema = computed(() => {
  if (!selectedPluginName.value) return null;
  return (
    pluginOptions.value.find(
      (plugin) => String(plugin.name) === String(selectedPluginName.value),
    ) || null
  );
});

watch(
  pluginOptions,
  (plugins) => {
    if (!Array.isArray(plugins) || !plugins.length) {
      selectedPluginName.value = "";
      return;
    }
    if (!selectedPluginName.value) {
      selectedPluginName.value = String(plugins[0].name);
      return;
    }
    const stillExists = plugins.some(
      (plugin) => String(plugin.name) === String(selectedPluginName.value),
    );
    if (!stillExists) {
      selectedPluginName.value = String(plugins[0].name);
    }
  },
  { immediate: true },
);

watch(selectedPluginName, () => {
  pluginParameters.value = {};
});

watch(pluginMenuOpen, (isOpen) => {
  if (!isOpen) return;
  if (!selectedPluginName.value && pluginOptions.value.length) {
    selectedPluginName.value = String(pluginOptions.value[0].name);
  }
  pluginParameters.value = {};
});

function runSelectedPlugin() {
  if (!selectedPluginName.value) return;
  emit("run-plugin", {
    pluginName: selectedPluginName.value,
    pictureIds: props.selectedImageIds,
    parameters: pluginParameters.value || {},
  });
  pluginMenuOpen.value = false;
}
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

.plugin-run-controls {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.plugin-menu-panel {
  width: 320px;
  max-width: min(92vw, 420px);
  background: rgba(var(--v-theme-surface), 0.96);
  color: rgb(var(--v-theme-on-surface));
  border: 1px solid rgba(var(--v-theme-primary), 0.3);
  border-radius: 8px;
  box-shadow: 0 8px 28px rgba(0, 0, 0, 0.3);
}

.plugin-menu-header {
  font-size: 0.9rem;
  font-weight: 600;
  color: rgb(var(--v-theme-on-surface));
  padding: 10px 12px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.12);
}

.plugin-menu-body {
  padding: 10px 12px;
}

.plugin-menu-label {
  display: block;
  font-size: 0.78rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-bottom: 4px;
  opacity: 0.9;
}

.plugin-menu-actions {
  margin-top: 12px;
  display: flex;
  justify-content: flex-end;
}

.plugin-run-select {
  height: 32px;
  width: 100%;
  border-radius: 4px;
  border: 1px solid rgba(var(--v-theme-primary), 0.4);
  background: rgba(var(--v-theme-background), 0.7);
  color: rgb(var(--v-theme-on-background));
  padding: 0 8px;
}
</style>
