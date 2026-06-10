<template>
  <transition name="selbar-pop">
    <div v-if="visible" class="floating-selection-bar">
      <span
        v-if="visible && selectedFaceCount > 0"
        class="selection-face-count"
      >
        {{ selectedFaceCount }} Faces selected
      </span>
      <div
        v-if="
          selectedCount > 0 &&
          !isScrapheapView &&
          pluginOptions.length &&
          !isReadOnly
        "
        class="plugin-run-controls"
        @keydown.esc="handlePluginMenuEsc"
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
            <div
              v-bind="menuProps"
              class="hidden-panel-activator"
              aria-hidden="true"
            ></div>
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
        v-if="selectedCount > 0 && !isScrapheapView && !isReadOnly"
        class="plugin-run-controls"
        @keydown.esc="handleComfyuiMenuEsc"
      >
        <v-menu
          v-if="props.comfyuiConfigured"
          v-model="comfyuiMenuOpen"
          :close-on-content-click="false"
          location-strategy="connected"
          location="bottom end"
          origin="top end"
          transition="scale-transition"
        >
          <template #activator="{ props: menuProps }">
            <div
              v-bind="menuProps"
              class="hidden-panel-activator"
              aria-hidden="true"
            ></div>
          </template>
          <div class="plugin-menu-panel">
            <div class="plugin-menu-header">
              Edit selected images with ComfyUI
            </div>
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

                  <template v-if="showComfyuiCaptionInput">
                    <label class="plugin-menu-label">Caption</label>
                    <textarea
                      v-model="comfyuiCaption"
                      class="plugin-menu-textarea"
                      rows="6"
                      placeholder="Optional caption for {{caption}}"
                      @keydown.stop
                    ></textarea>
                  </template>

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
      <!--
        Selection ▾ dropdown — mirrors the right-click context menu for every
        selection-scoped action, so keyboard ("S") and toolbar users reach the
        same actions as a right-click on the same selection. The context menu
        additionally offers three single-image actions (Share image, Find
        similar faces, Remove all shares) that are deliberately context-only:
        they act on a specific right-clicked image and its per-image face /
        share state, which the selection-scoped dropdown has no single target
        for. Multi-select parity is asserted by e2e/specs/menu-parity.spec.js.
      -->
      <div
        class="selection-ctx-bar"
        :class="{ 'selection-ctx-bar--active': selectedCount > 0 }"
      >
        <v-menu
          v-model="selectionMenuOpen"
          :close-on-content-click="false"
          location="bottom end"
          origin="top end"
          transition="scale-transition"
        >
          <template #activator="{ props: menuProps }">
            <button
              v-bind="menuProps"
              class="stack-btn"
              type="button"
              :disabled="selectedCount === 0"
              :title="
                selectedCount === 0
                  ? 'Select images to apply actions'
                  : props.selectedExpandedCount > selectedCount
                    ? `Actions for ${selectedCount} selected (${props.selectedExpandedCount} total including stacks) — press S`
                    : `Actions for ${selectedCount} selected — press S`
              "
            >
              <v-icon size="20">mdi-image-multiple-outline</v-icon>
              <span class="bar-btn-apply-label">({{ selectedCount }})</span>
              <v-icon size="18" class="bar-btn-chevron">mdi-menu-down</v-icon>
            </button>
          </template>
          <SelectionMenu
            ref="selectionMenuRef"
            :open="selectionMenuOpen"
            :selected-count="selectedCount"
            :selected-image-ids="selectedImageIds"
            :backend-url="backendUrl"
            :is-read-only="isReadOnly"
            :is-scrapheap-view="isScrapheapView"
            :grouping-lock-reason="props.groupingLockReason"
            :tagger-plugins="props.taggerPlugins"
            :captioner-plugins="props.captionerPlugins"
            :comfyui-configured="props.comfyuiConfigured"
            :has-plugin-options="pluginOptions.length > 0"
            :selected-sort="props.selectedSort"
            :selected-group-name="props.selectedGroupName"
            :selected-multiple-stack-ids="props.selectedMultipleStackIds"
            :show-remove-from-stack="props.showRemoveFromStack"
            @close="selectionMenuOpen = false"
            @set-project="$emit('set-project', $event)"
            @add-to-character="$emit('add-to-character', $event)"
            @remove-from-character="$emit('remove-from-character', $event)"
            @added-to-set="$emit('added-to-set', $event)"
            @remove-from-stack="$emit('remove-from-stack')"
            @create-stack="$emit('create-stack')"
            @dissolve-stacks="$emit('dissolve-stacks')"
            @create-stacks-from-groups="$emit('create-stacks-from-groups')"
            @open-tag-input="openTagInput()"
            @auto-tag="$emit('auto-tag', $event)"
            @generate-description="$emit('generate-description', $event)"
            @open-plugin-panel="openPluginPanel()"
            @open-comfyui-panel="openComfyuiPanel()"
            @reverse-image-search="$emit('reverse-image-search')"
            @remove-from-group="$emit('remove-from-group')"
            @delete-selected="$emit('delete-selected')"
          />
        </v-menu>
        <div v-if="!isScrapheapView && !isReadOnly" class="plugin-run-controls">
          <v-menu
            v-model="tagMenuOpen"
            :close-on-content-click="false"
            location-strategy="connected"
            location="bottom end"
            origin="top end"
            transition="scale-transition"
          >
            <template #activator="{ props: menuProps }">
              <div
                v-bind="menuProps"
                ref="tagBtnRef"
                class="hidden-panel-activator"
                aria-hidden="true"
              ></div>
            </template>
            <TbTagPanel
              :backend-url="props.backendUrl"
              :selected-count="selectedCount"
              :selected-image-ids="props.selectedImageIds"
              :all-grid-images="props.allGridImages"
              :open="tagMenuOpen"
              @tags-applied="emit('tags-applied', $event)"
              @close="tagMenuOpen = false"
            />
          </v-menu>
        </div>
        <button
          class="clear-btn"
          :disabled="!visible"
          @click="$emit('clear-selection')"
          title="Clear selection (ESC)"
        >
          <v-icon size="20" color="primary">mdi-selection-off</v-icon>
        </button>
        <button
          class="delete-btn"
          :disabled="!visible || isReadOnly"
          @click="$emit('delete-selected')"
          title="Delete selected items (DEL)"
        >
          <v-icon size="20" color="error">mdi-delete</v-icon>
        </button>
      </div>
      <!-- /selection-ctx-bar -->
    </div>
  </transition>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { apiClient, isReadOnly } from "../../utils/apiClient";
import SelectionMenu from "./SelectionMenu.vue";
import TbTagPanel from "./TbTagPanel.vue";
import PluginParametersUI from "../widgets/PluginParametersUI.vue";

const props = defineProps({
  selectedCount: Number,
  selectedExpandedCount: { type: Number, default: 0 },
  selectedFaceCount: { type: Number, default: 0 },
  selectedGroupName: String,
  selectedSort: { type: String, default: "" },
  visible: Boolean,
  scrapheapPicturesId: { type: String, required: true },
  backendUrl: { type: String, required: true },
  selectedImageIds: { type: Array, default: () => [] },
  selectedMediaSupport: {
    type: Object,
    default: () => ({ hasImages: false, hasVideos: false }),
  },
  comfyuiClientId: { type: String, default: "" },
  comfyuiConfigured: { type: Boolean, default: false },
  showRemoveFromStack: { type: Boolean, default: false },
  selectedMultipleStackIds: { type: Array, default: () => [] },
  groupingLockReason: { type: String, default: null },
  availablePlugins: { type: Array, default: () => [] },
  taggerPlugins: { type: Array, default: () => [] },
  captionerPlugins: { type: Array, default: () => [] },
  allGridImages: { type: Array, default: () => [] },
  selectedCharacter: String,
  selectedSet: String,
});

const emit = defineEmits([
  "clear-selection",
  "added-to-set",
  "remove-from-group",
  "delete-selected",
  "set-project",
  "add-to-character",
  "remove-from-character",
  "create-stack",
  "remove-from-stack",
  "dissolve-stacks",
  "create-stacks-from-groups",
  "run-plugin",
  "comfyui-run",
  "tags-applied",
  "auto-tag",
  "generate-description",
  "reverse-image-search",
  "selection-menu-open",
]);

const isScrapheapView = computed(() => {
  const scrapheapId = String(
    props.scrapheapPicturesId || "SCRAPHEAP",
  ).toUpperCase();
  const selected = String(props.selectedCharacter || "").toUpperCase();
  return selected === scrapheapId;
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
const selectionMenuOpen = ref(false);
const selectionMenuRef = ref(null);

function isEditableElement(el) {
  if (!(el instanceof HTMLElement)) return false;
  if (el.isContentEditable) return true;
  const tag = el.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (el.getAttribute("role") === "textbox") return true;
  return false;
}

function handleSelectionMenuHotkey(event) {
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  if (isEditableElement(event.target)) return;
  if (isEditableElement(document.activeElement)) return;
  // Down while menu is open and focus is outside panel → focus first item
  if (event.key === "ArrowDown" && selectionMenuOpen.value) {
    if (selectionMenuRef.value?.containsFocus?.()) return;
    event.preventDefault();
    nextTick(() => selectionMenuRef.value?.focusFirst());
    return;
  }
  if (event.key !== "s" && event.key !== "S") return;
  if (props.selectedCount <= 0) return;
  event.preventDefault();
  selectionMenuOpen.value = !selectionMenuOpen.value;
}

onMounted(() => window.addEventListener("keydown", handleSelectionMenuHotkey));
onUnmounted(() =>
  window.removeEventListener("keydown", handleSelectionMenuHotkey),
);

watch(selectionMenuOpen, (open) => emit("selection-menu-open", open));

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

// The plugin-run-controls and comfyui-run-controls divs use v-if. If either
// menu is open when the v-if condition transitions to false (e.g. selection
// cleared by ESC before Vuetify can emit update:modelValue), the VMenu
// unmounts without resetting the ref, leaving it true. The watcher below
// resets each ref whenever the hosting condition goes false so the panel
// does not auto-reopen when the condition becomes true again.
const showPluginControls = computed(
  () =>
    props.selectedCount > 0 &&
    !isScrapheapView.value &&
    pluginOptions.value.length > 0 &&
    !isReadOnly.value,
);
watch(showPluginControls, (shown) => {
  if (!shown) pluginMenuOpen.value = false;
});

const showComfyuiControls = computed(
  () => props.selectedCount > 0 && !isScrapheapView.value && !isReadOnly.value,
);
watch(showComfyuiControls, (shown) => {
  if (!shown) comfyuiMenuOpen.value = false;
});

const validComfyWorkflows = computed(() => {
  if (!Array.isArray(comfyuiWorkflows.value)) return [];
  return comfyuiWorkflows.value.filter(
    (workflow) => workflow?.workflow_type === "i2i",
  );
});

const selectedComfyWorkflow = computed(() =>
  (comfyuiWorkflows.value || []).find(
    (workflow) => workflow?.name === comfyuiSelectedWorkflow.value,
  ),
);

const showComfyuiCaptionInput = computed(() => {
  const missing = Array.isArray(
    selectedComfyWorkflow.value?.missing_placeholders,
  )
    ? selectedComfyWorkflow.value.missing_placeholders
    : [];
  return !missing.includes("{{caption}}");
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
  comfyuiWorkflowLoading.value = true;
  comfyuiWorkflowError.value = "";
  try {
    const res = await apiClient.get("/comfyui/workflows");
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

function handlePluginMenuEsc(event) {
  if (!pluginMenuOpen.value) return;
  event.preventDefault();
  event.stopPropagation();
  if (typeof event.stopImmediatePropagation === "function") {
    event.stopImmediatePropagation();
  }
  pluginMenuOpen.value = false;
}

function handleComfyuiMenuEsc(event) {
  if (!comfyuiMenuOpen.value) return;
  event.preventDefault();
  event.stopPropagation();
  if (typeof event.stopImmediatePropagation === "function") {
    event.stopImmediatePropagation();
  }
  comfyuiMenuOpen.value = false;
}

// ── Bulk tag ──────────────────────────────────────────────────────────────────
const tagMenuOpen = ref(false);
const tagBtnRef = ref(null);
function openTagInput() {
  if (tagMenuOpen.value) return;
  // Use a real click so Vuetify's location-strategy="connected" records the
  // activator element for positioning. Directly setting tagMenuOpen skips
  // that step and causes the menu to appear at (0, 0) on first open.
  tagBtnRef.value?.click();
}

function openPluginPanel() {
  if (pluginMenuOpen.value) return;
  // Ensure a plugin is selected (watcher is immediate, but guard anyway)
  if (!selectedPluginName.value && pluginOptions.value.length) {
    selectedPluginName.value = String(pluginOptions.value[0].name);
  }
  // nextTick lets any in-progress Vue render cycle complete (e.g. a context
  // menu closing on the same tick) before we open the overlay.
  nextTick(() => {
    pluginMenuOpen.value = true;
  });
}

function openComfyuiPanel() {
  if (comfyuiMenuOpen.value) return;
  nextTick(() => {
    comfyuiMenuOpen.value = true;
  });
}

defineExpose({ openTagInput, openPluginPanel, openComfyuiPanel });
</script>

<style scoped>
.floating-selection-bar {
  position: absolute;
  bottom: 18px;
  left: 50%;
  /* width: max-content so the pill hugs its icons. NOTE: do NOT add
     container-type here — inline-size containment makes the width ignore the
     contents, collapsing the pill to ~0 and leaving the icons floating with no
     visible background. */
  width: max-content;
  transform: translateX(-50%);
  z-index: 200;
  display: flex;
  align-items: center;
  gap: 6px;
  max-width: calc(100% - 24px);
  padding: 6px 10px;
  border-radius: 999px;
  background: rgba(var(--v-theme-surface), 0.86);
  box-shadow: 0 8px 28px rgba(0, 0, 0, 0.35);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.14);
  backdrop-filter: blur(12px);
}

.selbar-pop-enter-active,
.selbar-pop-leave-active {
  transition:
    transform 0.22s ease,
    opacity 0.22s ease;
}
.selbar-pop-enter-from,
.selbar-pop-leave-to {
  transform: translateX(-50%) translateY(120%);
  opacity: 0;
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
.selection-expanded-count {
  font-size: 0.85em;
  opacity: 0.75;
  white-space: nowrap;
  cursor: default;
}

.selection-ctx-bar {
  display: flex;
  align-items: center;
  gap: 6px;
}

.clear-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  background: transparent;
  color: rgb(var(--v-theme-on-background));
  border: none;
  padding: 0;
  width: 40px;
  height: 40px;
  border-radius: 5px;
  cursor: pointer;
  font-family: inherit;
  flex-shrink: 0;
}
.clear-btn:hover:not(:disabled) {
  background: rgba(var(--v-theme-on-background), 0.12);
}
.clear-btn:disabled {
  background: transparent;
  border-color: transparent;
  color: rgb(var(--v-theme-on-background));
  opacity: 0.35;
  cursor: default;
}
.remove-btn {
  background: rgb(var(--v-theme-warning));
  color: rgb(var(--v-theme-on-warning));
  border: none;
  padding: 2px 10px;
  border-radius: 3px;
  cursor: pointer;
  font-size: 0.85rem;
  line-height: 1.4;
}
.remove-btn:hover {
  filter: brightness(1.3);
}
.delete-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  background: transparent;
  color: rgb(var(--v-theme-on-background));
  border: none;
  padding: 0;
  width: 40px;
  height: 40px;
  border-radius: 5px;
  cursor: pointer;
  font-family: inherit;
  flex-shrink: 0;
}
.delete-btn:hover:not(:disabled) {
  background: rgba(var(--v-theme-on-background), 0.12);
}
.delete-btn:disabled {
  background: transparent;
  border-color: transparent;
  color: rgb(var(--v-theme-on-background));
  opacity: 0.35;
  cursor: default;
}
.stack-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: transparent;
  color: rgb(var(--v-theme-on-background));
  border: none;
  padding: 0 10px;
  border-radius: 5px;
  cursor: pointer;
  font-size: 0.88em;
  font-family: inherit;
  height: 40px;
  white-space: nowrap;
}
.stack-btn:hover:not(:disabled) {
  background: rgba(var(--v-theme-on-background), 0.12);
}
.stack-btn:disabled {
  opacity: 0.35;
  cursor: default;
}

/* Hidden panel activators — zero-size but remain in DOM for menu positioning */
.hidden-panel-activator {
  display: block;
  width: 0;
  min-width: 0;
  height: 0;
  padding: 0;
  margin: 0;
  border: none;
  background: none;
  overflow: hidden;
  pointer-events: none;
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

.bar-btn-apply-label {
  white-space: nowrap;
  font-size: 0.92em;
  flex-shrink: 1;
}

.bar-btn-chevron {
  flex-shrink: 0;
}

@container selbar (max-width: 660px) {
  .bar-btn-apply-label {
    display: none;
  }
}
</style>
