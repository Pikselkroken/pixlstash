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
                <span>Filters</span>
              </button>
            </template>
            <div class="plugin-menu-panel">
              <div class="plugin-menu-header">Apply Filters</div>
              <div class="plugin-menu-body">
                <label class="plugin-menu-label">Filters</label>
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
        <div
          v-if="selectedCount > 0 && !isScrapheapView"
          class="plugin-run-controls"
        >
          <v-menu
            v-model="comfyuiMenuOpen"
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
                :disabled="!selectedImageIds.length"
              >
                <v-icon size="16">mdi-robot</v-icon>
                <span>ComfyUI</span>
              </button>
            </template>
            <div class="plugin-menu-panel">
              <div class="plugin-menu-header">ComfyUI I2I</div>
              <div class="plugin-menu-body">
                <div v-if="comfyuiWorkflowLoading" class="plugin-menu-note">
                  Loading workflows...
                </div>
                <div v-else>
                  <div v-if="comfyuiWorkflowError" class="plugin-menu-error">
                    {{ comfyuiWorkflowError }}
                  </div>
                  <template v-if="validComfyWorkflows.length">
                    <label class="plugin-menu-label">Workflow</label>
                    <select
                      v-model="comfyuiSelectedWorkflow"
                      class="plugin-run-select"
                    >
                      <option
                        v-for="workflow in validComfyWorkflows"
                        :key="workflow.name"
                        :value="workflow.name"
                      >
                        {{ workflow.display_name || workflow.name }}
                      </option>
                    </select>

                    <label class="plugin-menu-label">Caption</label>
                    <textarea
                      v-model="comfyuiCaption"
                      class="plugin-menu-textarea"
                      rows="6"
                      placeholder="Optional caption for {{caption}}"
                      @keydown.stop
                    ></textarea>

                    <div class="plugin-menu-actions">
                      <button
                        class="stack-btn"
                        type="button"
                        :disabled="!canRunComfyWorkflow"
                        @click="runSelectedComfyWorkflow"
                      >
                        <v-icon size="16">mdi-play</v-icon>
                        <span>{{ comfyuiRunLoading ? "Running" : "Run" }}</span>
                      </button>
                    </div>
                  </template>
                  <div v-else class="plugin-menu-note">
                    No valid workflows found.
                  </div>
                  <div v-if="comfyuiRunError" class="plugin-menu-error">
                    {{ comfyuiRunError }}
                  </div>
                  <div v-if="comfyuiRunSuccess" class="plugin-menu-success">
                    {{ comfyuiRunSuccess }}
                  </div>
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
          <span>Stack</span>
        </button>
        <button
          v-if="showGroupStackButton"
          class="stack-btn"
          type="button"
          title="Create stacks from selected likeness groups"
          @click="$emit('create-stacks-from-groups')"
        >
          <v-icon size="16">mdi-layers-plus</v-icon>
          <span>Stack Groups</span>
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
import { apiClient } from "../utils/apiClient";
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
  selectedMediaSupport: {
    type: Object,
    default: () => ({ hasImages: false, hasVideos: false }),
  },
  comfyuiClientId: { type: String, default: "" },
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
  "comfyui-run",
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
  if (isScrapheapView.value) return "Permanently Delete";
  return "Delete";
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
  const hasImages = props.selectedMediaSupport?.hasImages === true;
  const hasVideos = props.selectedMediaSupport?.hasVideos === true;
  return props.availablePlugins.filter((plugin) => {
    if (!plugin || !plugin.name) return false;
    const supportsImages = plugin.supports_images !== false;
    const supportsVideos = plugin.supports_videos === true;
    if (hasImages && !supportsImages) return false;
    if (hasVideos && !supportsVideos) return false;
    return true;
  });
});

const selectedPluginName = ref("");
const pluginMenuOpen = ref(false);
const pluginParameters = ref({});
const comfyuiMenuOpen = ref(false);
const comfyuiWorkflows = ref([]);
const comfyuiWorkflowLoading = ref(false);
const comfyuiWorkflowError = ref("");
const comfyuiSelectedWorkflow = ref("");
const comfyuiCaption = ref("");
const comfyuiRunLoading = ref(false);
const comfyuiRunError = ref("");
const comfyuiRunSuccess = ref("");

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

const validComfyWorkflows = computed(() => {
  if (!Array.isArray(comfyuiWorkflows.value)) return [];
  return comfyuiWorkflows.value.filter(
    (workflow) => workflow?.workflow_type === "i2i",
  );
});

const canRunComfyWorkflow = computed(() => {
  if (comfyuiRunLoading.value) return false;
  if (!props.backendUrl) return false;
  if (
    !Array.isArray(props.selectedImageIds) ||
    !props.selectedImageIds.length
  ) {
    return false;
  }
  return !!comfyuiSelectedWorkflow.value;
});

watch(comfyuiMenuOpen, async (isOpen) => {
  if (!isOpen) return;
  comfyuiRunError.value = "";
  comfyuiRunSuccess.value = "";
  await fetchComfyWorkflows();
  if (!comfyuiSelectedWorkflow.value && validComfyWorkflows.value.length) {
    comfyuiSelectedWorkflow.value = String(validComfyWorkflows.value[0].name);
  }
});

async function fetchComfyWorkflows() {
  if (comfyuiWorkflowLoading.value) return;
  if (!props.backendUrl) return;
  comfyuiWorkflowLoading.value = true;
  comfyuiWorkflowError.value = "";
  try {
    const res = await apiClient.get(`${props.backendUrl}/comfyui/workflows`);
    const workflows = res.data?.workflows;
    comfyuiWorkflows.value = Array.isArray(workflows) ? workflows : [];
  } catch (err) {
    comfyuiWorkflowError.value =
      err?.response?.data?.detail || err?.message || String(err);
    comfyuiWorkflows.value = [];
  } finally {
    comfyuiWorkflowLoading.value = false;
  }
}

async function runSelectedComfyWorkflow() {
  if (!canRunComfyWorkflow.value) return;
  comfyuiRunLoading.value = true;
  comfyuiRunError.value = "";
  comfyuiRunSuccess.value = "";
  try {
    const pictureIds = (
      Array.isArray(props.selectedImageIds) ? props.selectedImageIds : []
    )
      .map((id) => Number(id))
      .filter((id) => Number.isFinite(id) && id > 0);
    if (!pictureIds.length) return;

    const payload = {
      picture_ids: pictureIds,
      workflow_name: comfyuiSelectedWorkflow.value,
      caption: comfyuiCaption.value || "",
      client_id: props.comfyuiClientId || undefined,
    };
    const res = await apiClient.post(
      `${props.backendUrl}/comfyui/run_i2i`,
      payload,
    );
    const prompts = Array.isArray(res.data?.prompts) ? res.data.prompts : [];
    emit("comfyui-run", {
      prompts,
      pictureIds,
      pictureId: pictureIds[0] ?? null,
    });
    comfyuiRunSuccess.value = prompts.length
      ? `Queued ${prompts.length} run(s) in ComfyUI.`
      : "Queued in ComfyUI.";
  } catch (err) {
    comfyuiRunError.value =
      err?.response?.data?.detail || err?.message || String(err);
  } finally {
    comfyuiRunLoading.value = false;
  }
}

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
  min-width: 0;
  flex: 1 1 0;
  overflow: hidden;
}
.selection-count,
.selection-face-count {
  font-weight: bold;
  font-size: 1.1em;
  text-align: left;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}
.selection-bar-actions {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-left: auto;
  flex-shrink: 0;
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
  width: 420px;
  max-width: min(92vw, 560px);
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

.plugin-menu-textarea {
  width: 100%;
  border-radius: 4px;
  border: 1px solid rgba(var(--v-theme-primary), 0.4);
  background: rgba(var(--v-theme-background), 0.7);
  color: rgb(var(--v-theme-on-background));
  padding: 8px;
  resize: vertical;
  min-height: 160px;
}

.plugin-menu-note {
  font-size: 0.82rem;
  opacity: 0.85;
}

.plugin-menu-error {
  margin-top: 8px;
  color: rgb(var(--v-theme-error));
  font-size: 0.8rem;
}

.plugin-menu-success {
  margin-top: 8px;
  color: rgb(var(--v-theme-success));
  font-size: 0.8rem;
}
</style>
