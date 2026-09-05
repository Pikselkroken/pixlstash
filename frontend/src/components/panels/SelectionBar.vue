<template>
  <!-- The selection half of the grid action pill. It is a run of controls, not
       a surface: GridActionPill owns the background, the seam, the motion and
       the bottom-edge anchor. The face count is folded into the menu trigger's
       label rather than standing on its own, so the half opens with exactly one
       number (merged-grid-action-pill.md §6.3). -->
  <div class="selection-bar">
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

              <label class="plugin-menu-checkbox-row">
                <input v-model="stackFilterOutputs" type="checkbox" />
                <span>Stack new images with the originals</span>
              </label>

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

                  <label class="plugin-menu-checkbox-row">
                    <input v-model="stackI2IOutputs" type="checkbox" />
                    <span>Stack new images with the originals</span>
                  </label>

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
        Selection ▾ dropdown - mirrors the right-click context menu for every
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
            <!-- A menu button with neither aria-haspopup nor aria-expanded
                 gives a screen-reader user no signal that it opens anything.
                 The count rides in the accessible name so it is read on focus,
                 which is why selecting images one at a time needs no live
                 region of its own. -->
            <button
              v-bind="menuProps"
              class="stack-btn"
              type="button"
              :disabled="selectedCount === 0 && selectedFaceCount === 0"
              :title="triggerTitle"
              :aria-label="triggerTitle"
              aria-haspopup="menu"
              :aria-expanded="selectionMenuOpen ? 'true' : 'false'"
              aria-keyshortcuts="S"
            >
              <v-icon size="20">mdi-image-multiple-outline</v-icon>
              <!-- Never truncated by the ladder: a count is the blast radius of
                   everything else in this half. -->
              <span class="bar-btn-apply-label">{{ selectionCountLabel }}</span>
              <v-icon size="18" class="bar-btn-chevron">mdi-menu-down</v-icon>
            </button>
          </template>
          <SelectionMenu
            ref="selectionMenuRef"
            :open="selectionMenuOpen"
            :selected-count="selectedCount"
            :selected-image-ids="selectedImageIds"
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
            :keep-cover-only-stack-count="props.keepCoverOnlyStackCount"
            :keep-cover-only-lock-reason="props.keepCoverOnlyLockReason"
            :rotate-block-reason="props.rotateBlockReason"
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
            @segment="$emit('segment')"
            @rotate-left="$emit('rotate-left')"
            @rotate-right="$emit('rotate-right')"
            @remove-from-group="$emit('remove-from-group')"
            @keep-cover-only="$emit('keep-cover-only')"
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
          v-if="
            selectedCount > 0 &&
            !isScrapheapView &&
            !isReadOnly &&
            impossibleSources.length > 0
          "
          class="stack-btn clear-impossible-btn"
          type="button"
          :disabled="clearingImpossible"
          :title="`Strip the impossible tags from the ${selectedCount} selected picture(s)`"
          @click="$emit('clear-impossible-tags')"
        >
          <v-icon size="18">mdi-tag-off-outline</v-icon>
          <span class="clear-impossible-label">{{
            clearingImpossible ? "Clearing…" : "Clear impossible tags"
          }}</span>
        </button>
        <button
          class="clear-btn"
          type="button"
          :disabled="!hasSelection"
          :title="clearTitle"
          :aria-label="clearTitle"
          :aria-keyshortcuts="ownsEscape ? 'Escape' : undefined"
          @click="$emit('clear-selection')"
        >
          <v-icon size="20" color="primary">mdi-selection-off</v-icon>
        </button>
        <!-- Separated from Clear selection by its own group gap. Two identical
             40px transparent icon buttons 8px apart, one of them destructive,
             is the adjacency this pill can least afford - and Delete now also
             sits in the same surface as the bulk Assign write. -->
        <button
          class="delete-btn"
          type="button"
          :disabled="!hasSelection || isReadOnly"
          :title="deleteTitle"
          :aria-label="deleteTitle"
          @click="$emit('delete-selected')"
        >
          <v-icon size="20" color="error">mdi-delete</v-icon>
        </button>
    </div>
    <!-- /selection-ctx-bar -->
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { API_BASE_URL, isReadOnly } from "../../utils/apiClient";
import { listWorkflows, runImageToImage } from "../../api/comfyui";
import { useGenStackPrefsStore } from "../../stores/useGenStackPrefsStore";
import SelectionMenu from "./SelectionMenu.vue";
import TbTagPanel from "./TbTagPanel.vue";
import PluginParametersUI from "../widgets/PluginParametersUI.vue";
import { isEditableElement } from "../../utils/dom.js";
import { errorDetail } from "../../utils/apiError";

const props = defineProps({
  selectedCount: Number,
  selectedExpandedCount: { type: Number, default: 0 },
  selectedFaceCount: { type: Number, default: 0 },
  selectedGroupName: String,
  selectedSort: { type: String, default: "" },
  /**
   * Esc reaches THIS half. Esc peels one layer per press (open menu → the
   * selection → the search), so while anything is selected the keycap belongs
   * here and the search half must not also claim it.
   */
  ownsEscape: { type: Boolean, default: true },
  scrapheapPicturesId: { type: String, required: true },
  backendUrl: { type: String, default: () => API_BASE_URL },
  selectedImageIds: { type: Array, default: () => [] },
  selectedMediaSupport: {
    type: Object,
    default: () => ({ hasImages: false, hasVideos: false }),
  },
  comfyuiClientId: { type: String, default: "" },
  comfyuiConfigured: { type: Boolean, default: false },
  showRemoveFromStack: { type: Boolean, default: false },
  selectedMultipleStackIds: { type: Array, default: () => [] },
  // Forwarded straight to SelectionMenu: Keep cover only lives in the overflow
  // only, never as a top-level pill button.
  keepCoverOnlyStackCount: { type: Number, default: 0 },
  keepCoverOnlyLockReason: { type: String, default: null },
  // Forwarded straight to SelectionMenu, like the two above: the rotate pair
  // lives in the overflow only, never as a top-level pill button.
  rotateBlockReason: { type: String, default: null },
  groupingLockReason: { type: String, default: null },
  availablePlugins: { type: Array, default: () => [] },
  taggerPlugins: { type: Array, default: () => [] },
  captionerPlugins: { type: Array, default: () => [] },
  allGridImages: { type: Array, default: () => [] },
  selectedCharacter: String,
  impossibleSources: { type: Array, default: () => [] },
  clearingImpossible: { type: Boolean, default: false },
});

const emit = defineEmits([
  "clear-selection",
  "added-to-set",
  "remove-from-group",
  "keep-cover-only",
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
  "segment",
  "rotate-left",
  "rotate-right",
  "selection-menu-open",
  "clear-impossible-tags",
]);

const isScrapheapView = computed(() => {
  const scrapheapId = String(
    props.scrapheapPicturesId || "SCRAPHEAP",
  ).toUpperCase();
  const selected = String(props.selectedCharacter || "").toUpperCase();
  return selected === scrapheapId;
});

const hasSelection = computed(
  () => props.selectedCount > 0 || props.selectedFaceCount > 0,
);

// One number opens the half. Pictures and faces are different units, so when
// both are live they are stated as two, never summed.
const selectionCountLabel = computed(() => {
  if (props.selectedCount > 0 && props.selectedFaceCount > 0) {
    return `${props.selectedCount} selected · ${props.selectedFaceCount} faces`;
  }
  if (props.selectedCount > 0) return `${props.selectedCount} selected`;
  return `${props.selectedFaceCount} faces selected`;
});

const triggerTitle = computed(() => {
  if (!hasSelection.value) return "Select images to apply actions";
  const stacks =
    props.selectedExpandedCount > props.selectedCount
      ? ` (${props.selectedExpandedCount} total including stacks)`
      : "";
  return `Actions for ${selectionCountLabel.value}${stacks} - press S`;
});

const clearTitle = computed(() =>
  props.ownsEscape ? "Clear selection (Esc)" : "Clear selection",
);

// "Delete" mis-set the expectation in the more alarming direction: outside the
// scrapheap the action moves pictures there, records to the operation log and
// raises an undoable receipt. Inside it, it is the irreversible one.
const deleteTitle = computed(() => {
  const n = props.selectedCount;
  return isScrapheapView.value
    ? `Delete ${n} forever (Del)`
    : `Move ${n} to Scrapheap (Del)`;
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
onUnmounted(() => {
  window.removeEventListener("keydown", handleSelectionMenuHotkey);
  clearComfyuiCloseTimer();
});

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

// Remembered "stack outputs with originals" prefs (persisted in localStorage).
const genStackPrefs = useGenStackPrefsStore();
const stackI2IOutputs = computed({
  get: () => genStackPrefs.stackI2IOutputs,
  set: (val) => genStackPrefs.setStackI2IOutputs(val),
});
const stackFilterOutputs = computed({
  get: () => genStackPrefs.stackFilterOutputs,
  set: (val) => genStackPrefs.setStackFilterOutputs(val),
});

// Auto-close timer for the I2I menu after a successful queue.
let comfyuiCloseTimer = null;
function clearComfyuiCloseTimer() {
  if (comfyuiCloseTimer !== null) {
    clearTimeout(comfyuiCloseTimer);
    comfyuiCloseTimer = null;
  }
}

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
  // A freshly-opened menu must never inherit a pending close from a prior run.
  clearComfyuiCloseTimer();
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
    const body = await listWorkflows();
    const workflows = body?.workflows;
    comfyuiWorkflows.value = Array.isArray(workflows) ? workflows : [];
  } catch (err) {
    comfyuiWorkflowError.value =
      errorDetail(err) || err?.message || String(err);
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
      stack: stackI2IOutputs.value,
    };
    const body = await runImageToImage(payload);
    const prompts = Array.isArray(body?.prompts) ? body.prompts : [];
    emit("comfyui-run", {
      prompts,
      pictureIds,
      pictureId: pictureIds[0] ?? null,
    });
    comfyuiRunSuccess.value = prompts.length
      ? `Queued ${prompts.length} run(s) in ComfyUI.`
      : "Queued in ComfyUI.";
    // Show the success message briefly, then close the menu.
    clearComfyuiCloseTimer();
    comfyuiCloseTimer = setTimeout(() => {
      comfyuiCloseTimer = null;
      comfyuiMenuOpen.value = false;
    }, 1200);
  } catch (err) {
    comfyuiRunError.value = errorDetail(err) || err?.message || String(err);
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
    stack: stackFilterOutputs.value,
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

// Same guard as the plugin/comfyui menus above. The whole half is unmounted by
// GridActionPill the moment the selection empties. If ESC clears the selection
// before Vuetify emits update:modelValue, the menu unmounts with tagMenuOpen
// still true and auto-reopens on the next selection. Reset it when it hides.
const showTagControls = computed(
  () => props.selectedCount > 0 && !isScrapheapView.value && !isReadOnly.value,
);
watch(showTagControls, (shown) => {
  if (!shown) tagMenuOpen.value = false;
});

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
/* No box of its own: the pill surface, the seam, the motion and the bottom-edge
   anchor all live in GridActionPill. `display: contents` lets these controls sit
   directly in the pill's flex run, so the half adds no nesting to the layout. */
.selection-bar {
  display: contents;
}

.selection-ctx-bar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.clear-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  color: rgb(var(--v-theme-on-background));
  padding: 0;
  width: 40px;
  height: 40px;
  border-radius: var(--radius-sm);
  font-family: inherit;
  flex-shrink: 0;
}
.clear-btn:hover:not(:disabled) {
  background: rgba(var(--v-theme-on-background), 0.12);
}
.clear-btn:disabled {
  border-color: transparent;
  color: rgb(var(--v-theme-on-background));
  opacity: 0.35;
  cursor: default;
}
/* Solid `warning` fill, so it is one of the few places `on-warning` is the right
   token. It is now authored (main.js) rather than Vuetify-derived: light
   #23211d on #b8861f = 4.95:1, dark #1b1b1b on #db7900 = 5.53:1. It used to
   resolve to #fff at 3.25:1 / 3.11:1 - a small white label under the 4.5 floor. */
.remove-btn {
  background: rgb(var(--v-theme-warning));
  color: rgb(var(--v-theme-on-warning));
  border: none;
  padding: var(--space-1) var(--space-4);
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: var(--text-sm);
  line-height: var(--leading-snug);
}
.remove-btn:hover {
  filter: brightness(1.3);
}
.delete-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  /* 8px here plus the run's own 8px gap = the --space-5 group gap that keeps
     the destructive control off its neighbour's elbow. */
  margin-left: var(--space-3);
  color: rgb(var(--v-theme-on-background));
  padding: 0;
  width: 40px;
  height: 40px;
  border-radius: var(--radius-sm);
  font-family: inherit;
  flex-shrink: 0;
}
.delete-btn:hover:not(:disabled) {
  background: rgba(var(--v-theme-on-background), 0.12);
}
.delete-btn:disabled {
  border-color: transparent;
  color: rgb(var(--v-theme-on-background));
  opacity: 0.35;
  cursor: default;
}
.stack-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: rgb(var(--v-theme-on-background));
  padding: 0 10px;
  border-radius: var(--radius-sm);
  font-size: var(--text-base);
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

/* Hidden panel activators - zero-size but remain in DOM for menu positioning */
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
  border-radius: var(--radius-md);
  box-shadow: var(--elevation-3);
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

.plugin-menu-checkbox-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
  font-size: 0.85rem;
  cursor: pointer;
}

.plugin-menu-checkbox-row input {
  cursor: pointer;
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
  font-size: var(--text-base);
  flex-shrink: 1;
}

.bar-btn-chevron {
  flex-shrink: 0;
}

/* The ladder drops the label of the conditional action first and NEVER the
   count: losing a selection count is worse than losing any word beside it
   (merged-grid-action-pill.md §7). The old rule hid both at 660px together. */
@container selbar (max-width: 1100px) {
  .clear-impossible-label {
    display: none;
  }
}

@media (hover: none) and (pointer: coarse) {
  .stack-btn,
  .clear-btn,
  .delete-btn {
    height: var(--bar-height);
  }
  .clear-btn,
  .delete-btn {
    width: var(--bar-height);
  }
}
</style>
