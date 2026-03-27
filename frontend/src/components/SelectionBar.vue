<template>
  <div v-if="visible" class="selection-bar-overlay">
    <div class="selection-bar-content">
      <div class="selection-bar-left">
        <button
          class="clear-btn"
          @click="$emit('clear-selection')"
          title="Clear selection (ESC)"
        >
          Clear
        </button>
        <span v-if="selectedCount > 0" class="selection-count"
          >{{ selectedCount }} Images selected</span
        >
        <span
          v-if="props.selectedExpandedCount > selectedCount"
          class="selection-expanded-count"
          title="including images in stacks"
        >
          ({{ props.selectedExpandedCount }}
          <v-icon
            size="14"
            style="vertical-align: middle"
            class="selection-count-explanation"
            >mdi-information-outline</v-icon
          >)
        </span>
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
            v-if="props.comfyuiConfigured"
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
        <AddToSetControl
          v-if="!isScrapheapView"
          :backend-url="backendUrl"
          :picture-ids="selectedImageIds"
          @added="$emit('added-to-set', $event)"
        />
        <AddToCharacterControl
          v-if="!isScrapheapView"
          :backend-url="backendUrl"
          :picture-ids="selectedImageIds"
          @added="$emit('add-to-character', $event)"
        />
        <button
          v-if="!isScrapheapView"
          class="stack-btn stack-toggle-btn"
          type="button"
          :disabled="showRemoveStackButton ? false : selectedCount <= 1"
          :title="
            showRemoveStackButton
              ? 'Remove selected images from their stack'
              : 'Create a stack from the selected images'
          "
          @click="
            showRemoveStackButton
              ? $emit('remove-from-stack')
              : $emit('create-stack')
          "
        >
          <v-icon size="16">{{
            showRemoveStackButton ? "mdi-layers-off" : "mdi-layers"
          }}</v-icon>
          <span>{{ showRemoveStackButton ? "Unstack" : "Stack" }}</span>
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
        <div class="plugin-run-controls">
          <v-menu
            v-model="tagMenuOpen"
            :close-on-content-click="false"
            location-strategy="connected"
            location="bottom end"
            origin="top end"
            transition="scale-transition"
          >
            <template #activator="{ props: menuProps }">
              <button
                v-bind="menuProps"
                ref="tagBtnRef"
                class="stack-btn"
                type="button"
                title="Tag selected (T)"
                :disabled="isScrapheapView"
              >
                <v-icon size="16">mdi-tag-plus</v-icon>
                <span>Tag</span>
              </button>
            </template>
            <div class="plugin-menu-panel">
              <div class="plugin-menu-header">
                Tag {{ selectedCount }} Image{{
                  selectedCount !== 1 ? "s" : ""
                }}
              </div>
              <div class="plugin-menu-body">
                <div v-if="tagDataLoading" class="tag-data-loading">
                  Loading tags...
                </div>
                <div
                  v-else-if="tagsOnAll.length || tagsOnSome.length"
                  class="tag-current-section"
                >
                  <div class="tag-current-label">
                    Current tags
                    <span v-if="tagDataCapped" class="tag-data-capped">
                      (first {{ MAX_TAG_FETCH }})
                    </span>
                  </div>
                  <div class="tag-chips-row">
                    <button
                      v-for="t in tagsOnAll"
                      :key="'all-' + t.name"
                      class="tag-chip tag-chip--all"
                      type="button"
                      :disabled="tagActionLoading.includes(t.name)"
                      :title="`On all ${totalWithTagData} selected — click to remove`"
                      @click="removeTagFromAll(t)"
                    >
                      <span class="tag-chip-label">{{ t.name }}</span>
                      <v-icon size="11" class="tag-chip-close"
                        >mdi-close</v-icon
                      >
                    </button>
                    <button
                      v-for="t in tagsOnSome"
                      :key="'some-' + t.name"
                      class="tag-chip tag-chip--some"
                      type="button"
                      :disabled="tagActionLoading.includes(t.name)"
                      :title="`On ${t.count} of ${totalWithTagData} — click to add to all`"
                      @click="addTagToRemaining(t)"
                    >
                      <span class="tag-chip-label">{{ t.name }}</span>
                      <span class="tag-chip-count"
                        >{{ t.count }}/{{ totalWithTagData }}</span
                      >
                    </button>
                  </div>
                  <div class="tag-coverage-filter">
                    <label class="tag-coverage-label">
                      Min coverage:
                      <input
                        v-model.number="tagMinCoverage"
                        type="range"
                        min="1"
                        :max="Math.max(1, totalWithTagData - 1)"
                        class="tag-coverage-slider"
                      />
                      {{ tagMinCoverage }}/{{ totalWithTagData }}
                    </label>
                    <span
                      v-if="tagsOnSomeHiddenCount"
                      class="tag-coverage-hidden"
                    >
                      {{ tagsOnSomeHiddenCount }} hidden
                    </span>
                  </div>
                </div>
                <div class="tag-new-label">New tag</div>
                <input
                  ref="tagInputRef"
                  v-model="tagInput"
                  class="tag-menu-input"
                  placeholder="Tag name..."
                  autocomplete="off"
                  @keydown.enter.prevent="applyTag"
                  @keydown="handleTagKey"
                />
                <div class="plugin-menu-actions">
                  <button
                    class="stack-btn"
                    type="button"
                    :disabled="!tagInput.trim() || tagLoading"
                    @click="applyTag"
                  >
                    {{ tagLoading ? "Applying..." : "Apply to All" }}
                  </button>
                </div>
                <div v-if="tagError" class="plugin-menu-error">
                  {{ tagError }}
                </div>
                <div v-if="tagSuccess" class="plugin-menu-success">
                  {{ tagSuccess }}
                </div>
              </div>
            </div>
          </v-menu>
        </div>
        <button
          class="delete-btn"
          @click="$emit('delete-selected')"
          title="Delete selected items (DEL)"
        >
          {{ deleteButtonLabel }}
        </button>
      </div>
    </div>
  </div>
  <Teleport to="body">
    <div
      v-if="tagMenuOpen && tagSuggestions.length && tagInputRect"
      class="sb-tag-autocomplete-dropdown"
      :style="{
        top: tagInputRect.bottom + 4 + 'px',
        left: tagInputRect.left + 'px',
        minWidth: Math.max(tagInputRect.width, 180) + 'px',
      }"
    >
      <button
        v-for="(item, idx) in tagSuggestions"
        :key="item.tag"
        class="sb-tag-autocomplete-item"
        :class="{
          'sb-tag-autocomplete-item--active': idx === tagSuggestionIndex,
        }"
        @mousedown.prevent="selectTagSuggestion(item)"
      >
        {{ item.tag }}
      </button>
    </div>
  </Teleport>
</template>

<script setup>
import { computed, nextTick, ref, watch } from "vue";
import { apiClient } from "../utils/apiClient";
import AddToSetControl from "./AddToSetControl.vue";
import AddToCharacterControl from "./AddToCharacterControl.vue";
import PluginParametersUI from "./PluginParametersUI.vue";
const props = defineProps({
  selectedCount: Number,
  selectedExpandedCount: { type: Number, default: 0 },
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
  comfyuiConfigured: { type: Boolean, default: false },
  showRemoveFromStack: { type: Boolean, default: false },
  availablePlugins: { type: Array, default: () => [] },
});

const MAX_TAG_FETCH = 100;

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
  "tags-applied",
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

// ── Bulk tag ──────────────────────────────────────────────────────────────────
const tagMenuOpen = ref(false);
const tagBtnRef = ref(null);
const tagInputRef = ref(null);
const tagInput = ref("");
const tagLoading = ref(false);
const tagError = ref("");
const tagSuccess = ref("");
const allTagsSB = ref([]);
let allTagsFetchedAt = 0;
const tagSuggestionIndex = ref(-1);
const tagInputRect = ref(null);
const tagActionLoading = ref([]);
const fetchedTagData = ref([]);
const tagDataLoading = ref(false);
const tagDataCapped = ref(false);

async function fetchSelectedImageTags() {
  const ids = (
    Array.isArray(props.selectedImageIds) ? props.selectedImageIds : []
  )
    .map((id) => Number(id))
    .filter((id) => Number.isFinite(id) && id > 0);
  tagDataCapped.value = ids.length > MAX_TAG_FETCH;
  const toFetch = ids.slice(0, MAX_TAG_FETCH);
  if (!toFetch.length) {
    fetchedTagData.value = [];
    return;
  }
  tagDataLoading.value = true;
  try {
    const res = await apiClient.post(
      `${props.backendUrl}/pictures/tags/bulk_fetch`,
      {
        picture_ids: toFetch,
      },
    );
    fetchedTagData.value = Array.isArray(res.data) ? res.data : [];
  } catch {
    fetchedTagData.value = [];
  } finally {
    tagDataLoading.value = false;
  }
}

// How many images we actually have tag data for.
const totalWithTagData = computed(() => fetchedTagData.value.length);

// Map of tagName → { count, tagsByImageId: Map<imageId, tagId> }
const tagFrequency = computed(() => {
  const freq = new Map();
  for (const img of fetchedTagData.value) {
    for (const t of img.tags || []) {
      const name = typeof t === "string" ? t : t.tag;
      const tagId = typeof t === "string" ? null : t.id;
      if (!name) continue;
      if (!freq.has(name))
        freq.set(name, { count: 0, tagsByImageId: new Map() });
      const entry = freq.get(name);
      entry.count++;
      entry.tagsByImageId.set(Number(img.id), tagId);
    }
  }
  return freq;
});

const tagsOnAll = computed(() => {
  if (!totalWithTagData.value) return [];
  return [...tagFrequency.value.entries()]
    .filter(([, v]) => v.count === totalWithTagData.value)
    .map(([name, v]) => ({
      name,
      count: v.count,
      tagsByImageId: v.tagsByImageId,
    }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
});

const tagMinCoverage = ref(1); // minimum count to show in the partial list

const tagsOnSome = computed(() => {
  if (!totalWithTagData.value) return [];
  return [...tagFrequency.value.entries()]
    .filter(
      ([, v]) =>
        v.count > 0 &&
        v.count < totalWithTagData.value &&
        v.count >= tagMinCoverage.value,
    )
    .map(([name, v]) => ({
      name,
      count: v.count,
      tagsByImageId: v.tagsByImageId,
    }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
});

const tagsOnSomeHiddenCount = computed(() => {
  if (!totalWithTagData.value || tagMinCoverage.value <= 1) return 0;
  return [...tagFrequency.value.entries()].filter(
    ([, v]) =>
      v.count > 0 &&
      v.count < totalWithTagData.value &&
      v.count < tagMinCoverage.value,
  ).length;
});

async function removeTagFromAll(tagEntry) {
  if (tagActionLoading.value.includes(tagEntry.name)) return;
  tagActionLoading.value = [...tagActionLoading.value, tagEntry.name];
  tagError.value = "";
  try {
    await Promise.all(
      [...tagEntry.tagsByImageId.entries()]
        .filter(([, tagId]) => tagId != null)
        .map(([imgId, tagId]) =>
          apiClient.delete(
            `${props.backendUrl}/pictures/${imgId}/tags/${tagId}`,
          ),
        ),
    );
    emit("tags-applied", {
      tag: tagEntry.name,
      pictureIds: [...tagEntry.tagsByImageId.keys()],
      action: "remove",
    });
    await fetchSelectedImageTags();
  } catch (err) {
    tagError.value = err?.response?.data?.detail || err?.message || String(err);
  } finally {
    tagActionLoading.value = tagActionLoading.value.filter(
      (n) => n !== tagEntry.name,
    );
  }
}

async function addTagToRemaining(tagEntry) {
  if (tagActionLoading.value.includes(tagEntry.name)) return;
  tagActionLoading.value = [...tagActionLoading.value, tagEntry.name];
  tagError.value = "";
  const missingIds = fetchedTagData.value
    .filter((img) => !tagEntry.tagsByImageId.has(Number(img.id)))
    .map((img) => Number(img.id));
  try {
    await Promise.all(
      missingIds.map((id) =>
        apiClient.post(`${props.backendUrl}/pictures/${id}/tags`, {
          tag: tagEntry.name,
        }),
      ),
    );
    emit("tags-applied", {
      tag: tagEntry.name,
      pictureIds: missingIds,
      action: "add",
    });
    await fetchSelectedImageTags();
  } catch (err) {
    tagError.value = err?.response?.data?.detail || err?.message || String(err);
  } finally {
    tagActionLoading.value = tagActionLoading.value.filter(
      (n) => n !== tagEntry.name,
    );
  }
}

const tagSuggestions = computed(() => {
  const query = tagInput.value.trim().toLowerCase();
  if (!query) return [];
  return allTagsSB.value
    .filter((item) => item.tag.toLowerCase().startsWith(query))
    .slice(0, 8);
});

watch(tagInput, () => {
  tagSuggestionIndex.value = -1;
});

watch(
  () => [tagMenuOpen.value, tagSuggestions.value.length],
  () => {
    if (tagMenuOpen.value && tagSuggestions.value.length) {
      nextTick(() => {
        tagInputRect.value = tagInputRef.value
          ? tagInputRef.value.getBoundingClientRect()
          : null;
      });
    } else {
      tagInputRect.value = null;
    }
  },
);

watch(tagMenuOpen, async (isOpen) => {
  if (!isOpen) {
    tagInput.value = "";
    tagError.value = "";
    tagSuccess.value = "";
    tagSuggestionIndex.value = -1;
    fetchedTagData.value = [];
    tagDataCapped.value = false;
    tagMinCoverage.value = 1;
    return;
  }
  await Promise.all([fetchTagsSB(), fetchSelectedImageTags()]);
  nextTick(() => tagInputRef.value?.focus());
});

async function fetchTagsSB() {
  if (!props.backendUrl) return;
  const now = Date.now();
  if (now - allTagsFetchedAt < 30_000) return;
  try {
    const res = await apiClient.get(`${props.backendUrl}/tags`);
    if (Array.isArray(res.data)) {
      allTagsSB.value = res.data;
      allTagsFetchedAt = now;
    }
  } catch (_e) {
    // non-critical
  }
}

function selectTagSuggestion(item) {
  tagInput.value = typeof item === "string" ? item : item.tag;
  tagSuggestionIndex.value = -1;
  nextTick(() => applyTag());
}

function handleTagKey(event) {
  if (event.key === "ArrowDown") {
    if (tagSuggestions.value.length) {
      event.preventDefault();
      tagSuggestionIndex.value = Math.min(
        tagSuggestionIndex.value + 1,
        tagSuggestions.value.length - 1,
      );
    }
  } else if (event.key === "ArrowUp") {
    if (tagSuggestions.value.length) {
      event.preventDefault();
      tagSuggestionIndex.value = Math.max(tagSuggestionIndex.value - 1, -1);
    }
  } else if (event.key === "Tab") {
    if (tagSuggestions.value.length) {
      event.preventDefault();
      const idx = tagSuggestionIndex.value >= 0 ? tagSuggestionIndex.value : 0;
      selectTagSuggestion(tagSuggestions.value[idx]);
    }
  } else if (event.key === "Escape") {
    tagMenuOpen.value = false;
  }
}

async function applyTag() {
  if (
    tagSuggestionIndex.value >= 0 &&
    tagSuggestions.value.length > tagSuggestionIndex.value
  ) {
    const item = tagSuggestions.value[tagSuggestionIndex.value];
    tagInput.value = typeof item === "string" ? item : item.tag;
    tagSuggestionIndex.value = -1;
    nextTick(() => applyTag());
    return;
  }
  const tag = tagInput.value.trim();
  if (!tag) return;
  const ids = (
    Array.isArray(props.selectedImageIds) ? props.selectedImageIds : []
  )
    .map((id) => Number(id))
    .filter((id) => Number.isFinite(id) && id > 0);
  if (!ids.length) return;
  tagLoading.value = true;
  tagError.value = "";
  tagSuccess.value = "";
  try {
    await Promise.all(
      ids.map((id) =>
        apiClient.post(`${props.backendUrl}/pictures/${id}/tags`, { tag }),
      ),
    );
    tagSuccess.value = `Tagged ${ids.length} image${ids.length !== 1 ? "s" : ""} with "${tag}"`;
    tagInput.value = "";
    // Invalidate the tag cache so new tags appear in suggestions
    allTagsFetchedAt = 0;
    emit("tags-applied", { tag, pictureIds: ids });
    await fetchSelectedImageTags();
  } catch (err) {
    tagError.value = err?.response?.data?.detail || err?.message || String(err);
  } finally {
    tagLoading.value = false;
  }
}

function openTagInput() {
  if (tagMenuOpen.value) return;
  // Use a real click so Vuetify's location-strategy="connected" records the
  // activator element for positioning. Directly setting tagMenuOpen skips
  // that step and causes the menu to appear at (0, 0) on first open.
  tagBtnRef.value?.click();
}

defineExpose({ openTagInput });
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
.selection-expanded-count {
  font-size: 0.85em;
  opacity: 0.75;
  white-space: nowrap;
  cursor: default;
}
.selection-bar-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
  flex-shrink: 0;
}
.clear-btn {
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
  border: none;
  padding: 4px 10px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.85rem;
}
.clear-btn:hover {
  filter: brightness(1.3);
}
.remove-btn {
  background: rgb(var(--v-theme-warning));
  color: rgb(var(--v-theme-on-warning));
  border: none;
  padding: 4px 10px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.85rem;
}
.remove-btn:hover {
  filter: brightness(1.3);
}
.delete-btn {
  background: rgb(var(--v-theme-error));
  color: #fff;
  border: none;
  padding: 4px 12px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.85rem;
}
.delete-btn:hover {
  filter: brightness(1.3);
}
.stack-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: rgba(var(--v-theme-primary), 0.15);
  color: rgb(var(--v-theme-on-background));
  border: 1px solid rgba(var(--v-theme-primary), 0.4);
  padding: 4px 8px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.85rem;
}
.stack-btn:hover:not(:disabled) {
  filter: brightness(1.2);
}
.stack-btn:disabled {
  opacity: 0.35;
  cursor: default;
}
.stack-toggle-btn {
  min-width: 5.5rem;
  justify-content: center;
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

.tag-menu-input {
  width: 100%;
  height: 32px;
  border-radius: 4px;
  border: 1px solid rgba(var(--v-theme-primary), 0.4);
  background: rgba(var(--v-theme-background), 0.7);
  color: rgb(var(--v-theme-on-background));
  padding: 0 8px;
  font-size: 0.85rem;
  outline: none;
}

.tag-menu-input:focus {
  border-color: rgba(var(--v-theme-primary), 0.8);
}

.tag-data-loading {
  font-size: 0.78rem;
  opacity: 0.6;
  margin-bottom: 10px;
}

.tag-data-capped {
  font-size: 0.68rem;
  opacity: 0.7;
  font-weight: normal;
  text-transform: none;
  letter-spacing: 0;
}

/* ── Tag coverage chips ──────────────────────────────────────── */
.tag-current-section {
  margin-bottom: 10px;
  display: flex;
  flex-direction: column;
}

.tag-current-label {
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  opacity: 0.55;
  margin-bottom: 5px;
  flex-shrink: 0;
}

.tag-new-label {
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  opacity: 0.55;
  margin-top: 10px;
  margin-bottom: 5px;
}

.tag-chips-row {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  height: 200px;
  overflow-y: auto;
  padding-right: 2px;
  align-content: flex-start;
}

.tag-chip {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  border-radius: 12px;
  padding: 2px 7px;
  font-size: 0.78rem;
  cursor: pointer;
  transition:
    background 0.15s,
    opacity 0.15s;
  line-height: 1.5;
  white-space: nowrap;
}

.tag-chip:disabled {
  opacity: 0.45;
  cursor: default;
}

.tag-chip--all {
  background: rgba(var(--v-theme-primary), 0.18);
  border: 1px solid rgba(var(--v-theme-primary), 0.5);
  color: rgb(var(--v-theme-on-surface));
}

.tag-chip--all:hover:not(:disabled) {
  background: rgba(var(--v-theme-error), 0.18);
  border-color: rgba(var(--v-theme-error), 0.55);
}

.tag-chip--some {
  background: transparent;
  border: 1px dashed rgba(var(--v-theme-on-surface), 0.35);
  color: rgb(var(--v-theme-on-surface));
  opacity: 0.7;
}

.tag-chip--some:hover:not(:disabled) {
  opacity: 1;
  background: rgba(var(--v-theme-primary), 0.12);
  border-style: solid;
  border-color: rgba(var(--v-theme-primary), 0.45);
}

.tag-chip-count {
  font-size: 0.68rem;
  opacity: 0.65;
  font-variant-numeric: tabular-nums;
}

.tag-chip-close {
  opacity: 0.6;
}

.tag-coverage-filter {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 6px;
  gap: 8px;
}

.tag-coverage-label {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 0.72rem;
  opacity: 0.65;
  white-space: nowrap;
}

.tag-coverage-slider {
  width: 80px;
  accent-color: rgb(var(--v-theme-primary));
  cursor: pointer;
}

.tag-coverage-hidden {
  font-size: 0.7rem;
  opacity: 0.5;
  white-space: nowrap;
}

.selection-count-explanation {
  opacity: 0.75;
  color: red;

  cursor: default;
}

.sb-tag-autocomplete-dropdown {
  position: fixed;
  z-index: 9999;
  background: color-mix(in srgb, rgb(var(--v-theme-surface)) 92%, transparent);
  backdrop-filter: blur(6px);
  border: 1px solid rgba(var(--v-theme-primary), 0.3);
  border-radius: 6px;
  box-shadow: 0 4px 18px rgba(0, 0, 0, 0.45);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.sb-tag-autocomplete-item {
  display: block;
  width: 100%;
  text-align: left;
  padding: 5px 10px;
  font-size: 0.8rem;
  background: transparent;
  border: none;
  color: rgb(var(--v-theme-on-surface));
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.sb-tag-autocomplete-item:hover,
.sb-tag-autocomplete-item--active {
  background: rgba(var(--v-theme-primary), 0.22);
}
</style>
