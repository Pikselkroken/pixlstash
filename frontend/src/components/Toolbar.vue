<template>
  <div
    :class="[
      'top-toolbar',
      isMobile && !sidebarVisible ? 'toolbar-connected' : '',
    ]"
  >
    <div class="toolbar-actions">
      <div class="toolbar-search-slot">
        <v-menu
          v-if="!isMobile"
          v-model="searchHistoryOpenModel"
          :close-on-content-click="false"
          :disabled="filteredSearchHistory.length === 0"
          open-on-focus
          transition="scale-transition"
          location="bottom"
          offset="6"
        >
          <template #activator="{ props }">
            <v-text-field
              v-bind="props"
              v-model="searchInputModel"
              ref="searchInputField"
              density="compact"
              variant="solo-filled"
              hide-details
              clearable
              prepend-inner-icon="mdi-magnify"
              class="toolbar-search-field"
              autocomplete="off"
              placeholder="Search (F)"
              @keydown.enter="handleSearchEnter"
              @keydown.esc="blurSearchInput"
              @click:prepend-inner="emit('commit-search')"
              @click:clear="emit('clear-search')"
            />
          </template>
          <v-list density="compact" class="search-history-list">
            <v-list-item
              v-for="item in filteredSearchHistory"
              :key="item"
              @click="emit('apply-search-history', item)"
            >
              <v-list-item-title>{{ item }}</v-list-item-title>
            </v-list-item>
            <v-divider />
            <v-list-item
              class="search-history-clear"
              @click="emit('clear-search-history')"
            >
              <v-list-item-title>Clear history</v-list-item-title>
            </v-list-item>
          </v-list>
        </v-menu>
      </div>
      <div class="toolbar-controls">
        <v-btn
          v-if="isMobile"
          icon
          color="primary"
          @click="emit('toggle-sidebar')"
          title="Toggle sidebar"
          :class="[
            'toolbar-action-btn',
            'toolbar-sidebar-btn',
            !sidebarVisible ? 'toolbar-sidebar-btn--connected' : '',
          ]"
        >
          <v-icon color="on-primary">mdi-dock-left</v-icon>
        </v-btn>
        <div v-if="isMobile" class="toolbar-mobile-spacer"></div>
        <v-btn
          v-if="isMobile"
          icon
          :color="
            searchOverlayVisible
              ? 'primary'
              : 'rgba(var(--v-theme--background), 0.3)'
          "
          @click="emit('open-search-overlay')"
          title="Search"
          class="toolbar-action-btn"
        >
          <v-icon>mdi-magnify</v-icon>
        </v-btn>
        <v-menu
          v-if="!isMobile"
          v-model="exportMenuOpenModel"
          offset-y
          :close-on-content-click="false"
          transition="scale-transition"
        >
          <template #activator="{ props }">
            <v-btn
              icon
              v-bind="props"
              :color="props['aria-expanded'] === 'true' ? 'primary' : 'surface'"
              title="Export current grid to zip"
              class="toolbar-action-btn"
            >
              <v-icon :color="'on-background'">mdi-download</v-icon>
            </v-btn>
          </template>
          <div
            style="
              padding: 10px 12px;
              min-width: 240px;
              background: rgba(var(--v-theme-background), 0.9);
              color: rgb(var(--v-theme-on-background));
              border-radius: 8px;
              box-shadow: 2px 2px 12px rgba(0, 0, 0, 0.4);
              display: flex;
              flex-direction: column;
              gap: 10px;
            "
          >
            <div
              style="
                font-size: 1.08em;
                color: rgb(var(--v-theme-on-background));
                font-weight: 500;
                letter-spacing: 0.02em;
              "
            >
              Export {{ exportCount }} picture{{ exportCount === 1 ? "" : "s" }}
            </div>
            <v-select
              v-model="exportTypeModel"
              :background-color="'surface'"
              :color="'on-surface'"
              :items="exportTypeOptions"
              item-title="title"
              item-value="value"
              label="Export type"
              density="comfortable"
            />
            <v-select
              v-model="exportCaptionModeModel"
              :background-color="'surface'"
              :color="'on-surface'"
              :items="exportCaptionOptions"
              item-title="title"
              item-value="value"
              label="Captions"
              density="comfortable"
              :disabled="exportTypeLocksCaptions"
            />
            <v-select
              v-model="exportResolutionModel"
              :background-color="'surface'"
              :color="'on-surface'"
              :items="exportResolutionOptions"
              item-title="title"
              item-value="value"
              label="Resolution"
              density="comfortable"
            />
            <v-select
              v-if="exportCaptionMode === 'tags'"
              v-model="exportTagFormatModel"
              :background-color="'surface'"
              :color="'on-surface'"
              :items="exportTagFormatOptions"
              item-title="title"
              item-value="value"
              label="Tag format"
              density="comfortable"
            />
            <v-switch
              v-model="exportIncludeCharacterNameModel"
              label="Include character name"
              color="primary"
              density="comfortable"
              :disabled="
                exportCaptionMode === 'none' || exportTypeLocksCaptions
              "
            />
            <v-switch
              v-model="exportUseOriginalFileNamesModel"
              label="Use original file names"
              color="primary"
              density="comfortable"
            />
            <v-btn color="primary" @click="emit('confirm-export-zip')">
              Export
            </v-btn>
          </div>
        </v-menu>

        <v-menu
          v-if="props.comfyuiConfigured && !isReadOnly"
          v-model="comfyuiMenuOpen"
          :close-on-content-click="false"
          location="top end"
          origin="bottom end"
          transition="scale-transition"
        >
          <template #activator="{ props: menuProps }">
            <v-btn
              icon
              v-bind="menuProps"
              :color="comfyuiMenuOpen ? 'primary' : 'surface'"
              title="Generate with ComfyUI"
              class="toolbar-action-btn"
            >
              <v-icon :color="'on-background'">mdi-robot</v-icon>
            </v-btn>
          </template>
          <div class="toolbar-comfyui-panel">
            <div class="toolbar-comfyui-header">
              Generate with ComfyUI (T2I)
            </div>
            <div class="toolbar-comfyui-body">
              <div v-if="comfyuiWorkflowLoading" class="toolbar-comfyui-note">
                Loading workflows...
              </div>
              <div v-else>
                <div v-if="comfyuiWorkflowError" class="toolbar-comfyui-error">
                  {{ comfyuiWorkflowError }}
                </div>
                <template v-if="validComfyWorkflows.length">
                  <label class="toolbar-comfyui-label">Workflow</label>
                  <select
                    v-model="comfyuiSelectedWorkflow"
                    class="toolbar-comfyui-select"
                  >
                    <option
                      v-for="workflow in validComfyWorkflows"
                      :key="workflow.name"
                      :value="workflow.name"
                    >
                      {{ workflow.display_name || workflow.name }}
                    </option>
                  </select>
                  <label class="toolbar-comfyui-label">Caption</label>
                  <textarea
                    v-model="comfyuiCaption"
                    class="toolbar-comfyui-textarea"
                    rows="6"
                    placeholder="Optional caption for {{caption}}"
                    @keydown.stop
                  ></textarea>
                  <label class="toolbar-comfyui-label">Seed</label>
                  <div class="toolbar-comfyui-seed-row">
                    <button
                      type="button"
                      class="toolbar-comfyui-seed-btn"
                      :class="{ active: comfyuiSeedMode === 'random' }"
                      @click="comfyuiSeedMode = 'random'"
                    >
                      Random
                    </button>
                    <button
                      type="button"
                      class="toolbar-comfyui-seed-btn"
                      :class="{ active: comfyuiSeedMode === 'fixed' }"
                      @click="comfyuiSeedMode = 'fixed'"
                    >
                      Fixed
                    </button>
                    <input
                      v-if="comfyuiSeedMode === 'fixed'"
                      v-model.number="comfyuiSeed"
                      type="number"
                      class="toolbar-comfyui-seed-input"
                      min="0"
                      max="4294967295"
                      @keydown.stop
                    />
                    <button
                      class="toolbar-comfyui-run-btn"
                      type="button"
                      :disabled="!canRunComfyWorkflow"
                      @click="runComfyuiOnGrid"
                    >
                      <v-icon size="16">mdi-play</v-icon>
                      <span>Run</span>
                    </button>
                  </div>
                </template>
                <div v-else class="toolbar-comfyui-note">
                  No valid workflows found.
                </div>
                <div v-if="comfyuiRunError" class="toolbar-comfyui-error">
                  {{ comfyuiRunError }}
                </div>
              </div>
            </div>
          </div>
        </v-menu>

        <v-btn
          icon
          :color="statsOpen ? 'primary' : 'surface'"
          title="Toggle stats sidebar"
          class="toolbar-action-btn"
          @click="emit('toggle-stats')"
        >
          <v-icon :color="'on-background'">mdi-chart-bar</v-icon>
        </v-btn>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import { apiClient, isReadOnly } from "../utils/apiClient";

const props = defineProps({
  isMobile: { type: Boolean, default: false },
  sidebarVisible: { type: Boolean, default: true },
  searchOverlayVisible: { type: Boolean, default: false },
  searchInput: { type: String, default: "" },
  isSearchHistoryOpen: { type: Boolean, default: false },
  filteredSearchHistory: { type: Array, default: () => [] },
  exportMenuOpen: { type: Boolean, default: false },
  exportCount: { type: Number, default: 0 },
  exportType: { type: String, default: "full" },
  exportCaptionMode: { type: String, default: "description" },
  exportTagFormat: { type: String, default: "spaces" },
  exportIncludeCharacterName: { type: Boolean, default: true },
  exportUseOriginalFileNames: { type: Boolean, default: false },
  exportResolution: { type: String, default: "original" },
  exportTypeLocksCaptions: { type: Boolean, default: false },
  exportCaptionOptions: { type: Array, default: () => [] },
  exportTypeOptions: { type: Array, default: () => [] },
  exportResolutionOptions: { type: Array, default: () => [] },
  exportTagFormatOptions: { type: Array, default: () => [] },
  comfyuiConfigured: { type: Boolean, default: false },
  statsOpen: { type: Boolean, default: true },
  backendUrl: { type: String, default: "" },
});

const emit = defineEmits([
  "update:searchInput",
  "update:isSearchHistoryOpen",
  "update:exportMenuOpen",
  "update:exportType",
  "update:exportCaptionMode",
  "update:exportTagFormat",
  "update:exportResolution",
  "update:exportIncludeCharacterName",
  "update:exportUseOriginalFileNames",
  "open-search-overlay",
  "commit-search",
  "clear-search",
  "apply-search-history",
  "clear-search-history",
  "confirm-export-zip",
  "open-settings",
  "toggle-sidebar",
  "toggle-stats",
  "comfyui-run-grid",
]);

const searchInputField = ref(null);

const searchInputModel = computed({
  get: () => props.searchInput,
  set: (value) => emit("update:searchInput", value ?? ""),
});

const searchHistoryOpenModel = computed({
  get: () => props.isSearchHistoryOpen,
  set: (value) => emit("update:isSearchHistoryOpen", value),
});


const exportMenuOpenModel = computed({
  get: () => props.exportMenuOpen,
  set: (value) => emit("update:exportMenuOpen", value),
});

function handleSearchEnter(event) {
  if (event?.target) {
    event.target.blur();
  }
  blurSearchInput();
  emit("commit-search");
}

function blurSearchInput() {
  const field = searchInputField.value;
  if (field && field.$el) {
    const input = field.$el.querySelector("input");
    if (input) input.blur();
  }
  if (document.activeElement instanceof HTMLElement) {
    document.activeElement.blur();
  }
}

function focusSearchInput() {
  const field = searchInputField.value;
  if (field && field.$el) {
    const input = field.$el.querySelector("input");
    if (input) input.focus();
  }
}

// ============================================================
// COMFYUI
// ============================================================
const comfyuiMenuOpen = ref(false);
const comfyuiWorkflows = ref([]);
const comfyuiWorkflowLoading = ref(false);
const comfyuiWorkflowError = ref("");
const comfyuiSelectedWorkflow = ref("");
const comfyuiCaption = ref("");
const comfyuiRunError = ref("");
const comfyuiSeedMode = ref(
  sessionStorage.getItem("comfyui_t2i_seed_mode") === "fixed"
    ? "fixed"
    : "random",
);
const _savedSeed = Number(sessionStorage.getItem("comfyui_t2i_seed"));
const comfyuiSeed = ref(
  Number.isFinite(_savedSeed) && _savedSeed >= 0 ? _savedSeed : 0,
);
watch(comfyuiSeedMode, (val) =>
  sessionStorage.setItem("comfyui_t2i_seed_mode", val),
);
watch(comfyuiSeed, (val) =>
  sessionStorage.setItem("comfyui_t2i_seed", String(val)),
);

const validComfyWorkflows = computed(() => {
  if (!Array.isArray(comfyuiWorkflows.value)) return [];
  return comfyuiWorkflows.value.filter((w) => w?.workflow_type === "t2i");
});

const canRunComfyWorkflow = computed(
  () => !!comfyuiSelectedWorkflow.value && !!props.backendUrl,
);

watch(comfyuiMenuOpen, async (isOpen) => {
  if (!isOpen) return;
  comfyuiRunError.value = "";
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

function runComfyuiOnGrid() {
  if (!canRunComfyWorkflow.value) return;
  emit("comfyui-run-grid", {
    workflowName: comfyuiSelectedWorkflow.value,
    caption: comfyuiCaption.value || "",
    seedMode: comfyuiSeedMode.value,
    seed: comfyuiSeed.value,
  });
  comfyuiMenuOpen.value = false;
}

defineExpose({ blurSearchInput, focusSearchInput });
</script>

<style scoped>
.top-toolbar {
  background-color: rgb(var(--v-theme-toolbar)) !important;
  width: 100%;
  display: flex;
  align-items: center;
  vertical-align: top;
  padding: 2px 4px;
  z-index: 5;
  position: relative;
  --toolbar-control-height: 32px;
}

.toolbar-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
  margin-left: 0;
  margin-right: 0;
  padding-right: 2px;
  gap: 8px;
}

.toolbar-search-slot {
  flex: 1 1 0;
  display: flex;
  align-items: center;
  min-width: 0;
}

.search-history-list {
  max-height: 200px;
  overflow-y: auto;
  background-color: rgba(var(--v-theme-background), 0.9);
}

.toolbar-controls {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-left: auto;
}

.toolbar-sort-controls {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: 8px;
}

.toolbar-mobile-spacer {
  flex: 1;
}

.toolbar-action-btn.toolbar-sidebar-btn,
.toolbar-action-btn.toolbar-sidebar-btn:hover,
.toolbar-action-btn.toolbar-sidebar-btn:focus-visible {
  background-color: rgb(var(--v-theme-sidebar)) !important;
  border: none !important;
  border-radius: 8px !important;
}

.toolbar-action-btn.toolbar-sidebar-btn.toolbar-sidebar-btn--connected,
.toolbar-action-btn.toolbar-sidebar-btn.toolbar-sidebar-btn--connected:hover,
.toolbar-action-btn.toolbar-sidebar-btn.toolbar-sidebar-btn--connected:focus-visible {
  border-radius: 0 8px 8px 0 !important;
}

.toolbar-sidebar-btn .v-icon {
  color: rgb(var(--v-theme-accent)) !important;
}

.toolbar-connected {
  padding-left: 0 !important;
}

.toolbar-connected .toolbar-actions {
  gap: 0;
}

.toolbar-connected .toolbar-sidebar-btn {
  margin-left: 0 !important;
}

.toolbar-sort-panel {
  padding: 8px;
  min-width: 340px;
  max-width: 340px;
  width: 340px;
  max-height: 70vh;
  background: rgba(var(--v-theme-background), 0.92);
  color: rgb(var(--v-theme-on-background));
  border-radius: 10px;
  box-shadow: 2px 2px 12px rgba(0, 0, 0, 0.4);
  display: flex;
  flex-direction: column;
  gap: 6px;
  overflow-y: auto;
}

.toolbar-sort-panel-title {
  font-size: 1em;
  font-weight: 500;
  letter-spacing: 0.02em;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.toolbar-sort-panel-title span {
  font-size: 0.8em;
  font-weight: 400;
  color: rgba(var(--v-theme-on-background), 0.6);
}

.toolbar-sort-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.toolbar-sort-grid {
  display: grid !important;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  grid-auto-rows: minmax(38px, auto);
  gap: 6px;
  align-content: start;
  width: 100%;
  height: auto;
  overflow: visible;
  padding-bottom: 4px;
}

.toolbar-sort-grid-btn {
  justify-content: flex-start;
  gap: 4px;
  text-transform: none;
  border-radius: 4px;
  min-height: var(--toolbar-control-height);
  padding: 2px 2px;
  background: rgba(var(--v-theme-surface), 0.2) !important;
  color: rgb(var(--v-theme-on-background)) !important;
  border: none;
}

.toolbar-sort-grid-btn :deep(.v-btn__content) {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  min-width: 0;
}

.toolbar-sort-grid-btn.v-btn--active {
  background: rgba(var(--v-theme-primary), 0.62) !important;
  color: rgb(var(--v-theme-on-background)) !important;
  border-color: rgba(var(--v-theme-primary), 0.6);
  box-shadow: 0 0 0 1px rgba(var(--v-theme-primary), 0.2);
}

.toolbar-sort-grid-btn:focus,
.toolbar-sort-grid-btn:focus-visible,
.toolbar-sort-grid-btn:active {
  outline: none !important;
  box-shadow: none !important;
}

.toolbar-sort-grid-btn.v-btn--active .toolbar-sort-grid-label {
  font-weight: 600;
}

.toolbar-sort-grid--pending-parameter
  :deep(.toolbar-sort-grid-btn.v-btn--active) {
  background: rgba(var(--v-theme-primary), 0.22) !important;
  box-shadow: 0 0 0 1px rgba(var(--v-theme-primary), 0.12);
}

.toolbar-sort-grid--pending-parameter
  :deep(.toolbar-sort-grid-btn.v-btn--active .toolbar-sort-grid-label) {
  font-weight: 500;
  opacity: 0.88;
}

.toolbar-sort-grid-selected {
  position: absolute;
  right: 4px;
  color: rgba(var(--v-theme-on-background), 0.56);
}

.toolbar-sort-grid-btn.v-btn--active .toolbar-sort-grid-selected {
  color: rgba(var(--v-theme-accent), 0.95);
}

.toolbar-sort-grid-selected--pending {
  opacity: 0.6;
}

.toolbar-sort-grid-label {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 0.8em;
}

.toolbar-sort-similarity-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.toolbar-sort-similarity-row > span {
  font-size: 0.85em;
  color: rgba(var(--v-theme-on-background), 0.7);
  white-space: nowrap;
}

.toolbar-similarity-scroll {
  width: 100%;
  max-height: 400px;
  overflow-y: auto;
  padding-right: 2px;
}

.toolbar-similarity-thumb {
  width: 20px;
  height: 20px;
  border-radius: 6px;
  object-fit: cover;
  background: rgba(var(--v-theme-surface), 0.35);
}

.toolbar-similarity-thumb--placeholder {
  display: inline-block;
}

.toolbar-sort-direction {
  align-self: center;
  gap: 6px;
  text-transform: none;
  border-radius: 8px;
  min-height: var(--toolbar-control-height);
  padding: 6px 6px;
  background: rgba(var(--v-theme-surface), 0.2) !important;
  color: rgb(var(--v-theme-on-background)) !important;
}

.toolbar-sort-search-note {
  font-size: 0.85em;
  color: rgba(var(--v-theme-on-background), 0.6);
  padding: 6px 8px;
  border-radius: 6px;
  background: rgba(var(--v-theme-surface), 0.2);
}

.toolbar-stacks-controls {
  width: 100%;
  margin-top: 6px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.toolbar-stacks-title {
  font-size: 0.85em;
  color: rgba(var(--v-theme-on-background), 0.7);
  font-weight: 500;
  letter-spacing: 0.01em;
  width: 100%;
  text-align: left;
}

.toolbar-stacks-buttons {
  width: 100%;
  display: flex;
  flex-direction: row;
  gap: 6px;
}

.toolbar-columns-slider :deep(.v-label) {
  font-size: 0.85em;
  font-weight: 500;
}

.toolbar-stack-toggle-btn {
  flex: 1 1 0;
  text-transform: none;
  color: rgb(var(--v-theme-on-primary)) !important;
}

.toolbar-stack-toggle-btn.v-btn--disabled {
  color: rgba(var(--v-theme-on-primary), 0.45) !important;
  filter: saturate(0.15) brightness(0.9);
}

.toolbar-search-field {
  flex: 1 1 auto;
  min-width: 220px;
  max-width: none;
  width: 100%;
  margin-left: 4px;
  margin-right: 4px;
}

.toolbar-search-field :deep(.v-input__control) {
  width: 100%;
  min-height: var(--toolbar-control-height);
}

.toolbar-search-field :deep(.v-field) {
  border-radius: 8px;
  background: rgba(var(--v-theme-surface), 0.7);
  border: 1px solid rgba(var(--v-theme-border), 0.42);
  min-height: var(--toolbar-control-height);
  height: var(--toolbar-control-height);
  align-items: center;
  box-shadow: 0 1px 3px rgba(var(--v-theme-shadow), 0.2) !important;
  transition:
    border-color 0.18s ease,
    box-shadow 0.18s ease,
    background-color 0.18s ease;
}

.toolbar-search-field:hover :deep(.v-field) {
  border-color: rgba(var(--v-theme-accent), 0.48);
  background: rgba(var(--v-theme-surface), 0.76);
}

.toolbar-search-field:focus-within :deep(.v-field) {
  border-color: rgba(var(--v-theme-accent), 0.78);
  box-shadow: 0 0 0 2px rgba(var(--v-theme-accent), 0.18) !important;
  background: rgba(var(--v-theme-surface), 0.82);
}

.toolbar-search-field :deep(.v-field__input) {
  padding-top: 0;
  padding-bottom: 0;
  min-height: var(--toolbar-control-height);
  display: flex;
  align-items: center;
}

.toolbar-search-field :deep(.v-field__input) {
  color: rgb(var(--v-theme-on-background));
}

.toolbar-search-field :deep(.v-label) {
  color: rgba(var(--v-theme-on-background), 0.7);
}

.toolbar-search-field :deep(.v-icon) {
  color: rgba(var(--v-theme-on-background), 0.7);
}

.toolbar-search-field :deep(.v-field__clearable) {
  color: rgba(var(--v-theme-on-background), 0.6);
}

.toolbar-action-btn {
  min-width: var(--toolbar-control-height);
  min-height: var(--toolbar-control-height);
  padding: 0;
  border: none;
  border-radius: 8px;
  text-transform: none;
  letter-spacing: 0.02em;
  font-weight: 500;
  box-shadow: none;
  background-color: transparent !important;
  color: rgba(var(--v-theme-on-background), 0.76) !important;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition:
    color 0.16s ease,
    opacity 0.16s ease,
    background-color 0.16s ease;
}

.toolbar-action-btn:hover,
.toolbar-action-btn:focus-visible {
  box-shadow: none !important;
  background-color: rgba(var(--v-theme-accent), 0.14) !important;
  color: rgba(var(--v-theme-on-background), 0.95) !important;
}

.toolbar-action-btn :deep(.v-icon) {
  color: inherit;
  opacity: 0.86;
  transition:
    color 0.16s ease,
    opacity 0.16s ease;
}

.toolbar-action-btn:hover :deep(.v-icon),
.toolbar-action-btn:focus-visible :deep(.v-icon) {
  opacity: 1;
}

.toolbar-action-btn:focus,
.toolbar-action-btn:focus-visible,
.toolbar-action-btn:active {
  outline: none !important;
  box-shadow: none !important;
}

.toolbar-action-btn.v-btn--active {
  border-color: rgba(var(--v-theme-surface), 0.2) !important;
}

.filter-count-badge {
  position: absolute;
  top: -1px;
  right: 8px;
  min-width: 16px;
  height: 16px;
  padding: 0 3px;
  border-radius: 10px;
  background: #c0392b;
  color: #ffffff;
  font-size: 9px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none;
  z-index: 1;
}

.toolbar-split-button {
  display: inline-flex;
  align-items: center;
  border-radius: 8px;
  overflow: hidden;
  background: rgba(var(--v-theme-surface), 0.5) !important;
  box-shadow: 2px 2px 2px rgba(0, 0, 0, 0.4) !important;
  height: var(--toolbar-control-height);
}

.toolbar-split-button .toolbar-action-btn {
  border-radius: 0;
  height: var(--toolbar-control-height);
}

.toolbar-split-toggle {
  border-right: 1px solid rgba(var(--v-theme-on-background), 0.1);
  background: rgba(var(--v-theme-primary), 0.85) !important;
  color: rgb(var(--v-theme-on-primary)) !important;
}

.toolbar-split-menu {
  padding: 0 10px;
}

.toolbar-sort-activator {
  padding: 0;
  min-width: var(--toolbar-control-height);
  width: auto;
  justify-content: flex-start;
}

.toolbar-split-button .toolbar-sort-activator {
  padding: 0 10px;
  width: 190px;
}

.toolbar-split-button--icon .toolbar-sort-activator,
.toolbar-split-button--icon .toolbar-split-menu {
  width: auto;
  padding: 0;
}

.toolbar-split-button .toolbar-sort-activator :deep(.v-btn__content) {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  justify-content: flex-start;
  position: relative;
  padding-right: 22px;
}

.toolbar-sort-chevron {
  position: absolute;
  top: 2px;
  right: 4px;
}

.toolbar-sort-activator-label {
  font-size: 0.9em;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}

.media-type-toggle {
  margin-left: 0;
  width: 100%;
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0;
  min-height: 38px;
  border-radius: 10px;
  padding: 0;
  overflow: hidden;
  background: rgba(var(--v-theme-on-background), 0.09);
}

.media-type-toggle :deep(.media-type-button) {
  min-height: 38px !important;
  height: 38px !important;
  width: 100%;
  min-width: 0;
  border-radius: 0 !important;
  color: rgb(var(--v-theme-on-background)) !important;
  background: rgba(var(--v-theme-surface), 0.34) !important;
}

.media-type-toggle :deep(.media-type-button:not(:first-child)) {
  border-left: 1px solid rgba(var(--v-theme-on-background), 0.14);
}

.media-type-toggle :deep(.media-type-button:hover),
.media-type-toggle :deep(.media-type-button:focus-visible) {
  background: rgba(var(--v-theme-surface), 0.52) !important;
}

.media-type-toggle :deep(.media-type-button--active) {
  color: rgb(var(--v-theme-on-primary)) !important;
  background: rgba(var(--v-theme-primary), 0.9) !important;
}

.media-type-toggle :deep(.media-type-button .v-btn__content) {
  min-height: 38px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

@media (max-width: 900px) {
  .toolbar-actions {
    width: 100%;
    flex-wrap: nowrap;
    gap: 2px;
    margin-left: 0;
    justify-content: flex-start;
  }

  .toolbar-search-slot {
    flex: 0 0 0;
  }

  .toolbar-sort-controls {
    flex: 1;
    margin-left: 0;
  }
}

.toolbar-filter-panel {
  padding: 10px 12px;
  min-width: 260px;
  max-width: 320px;
  width: 320px;
  max-height: 70vh;
  overflow-y: auto;
  background: rgba(var(--v-theme-background), 0.92);
  color: rgb(var(--v-theme-on-background));
  border-radius: 10px;
  box-shadow: 2px 2px 12px rgba(0, 0, 0, 0.4);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.toolbar-filter-panel-title {
  font-size: 1em;
  font-weight: 500;
  letter-spacing: 0.02em;
  margin-bottom: 2px;
}

.toolbar-filter-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 2px;
}

.toolbar-filter-panel-header .toolbar-filter-panel-title {
  margin-bottom: 0;
}

.toolbar-filter-section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  margin-bottom: 2px;
}

.filter-clear-all-btn {
  min-width: 0;
  padding: 0 4px;
  height: 18px;
  font-size: 0.75em;
}

.filter-shared-only-row {
  display: flex;
  align-items: center;
  margin-bottom: 8px;
  margin-top: 2px;
}

.filter-shared-only-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.9em;
  color: rgb(var(--v-theme-on-background));
  cursor: pointer;
  user-select: none;
}

.toolbar-filter-section-label {
  font-size: 0.85em;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: rgba(var(--v-theme-on-background), 0.6);
  margin-bottom: 2px;
}

.toolbar-filter-panel :deep(.v-label) {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}

.toolbar-filter-panel :deep(.v-checkbox .v-selection-control) {
  min-height: 0;
}

.toolbar-filter-panel :deep(.v-checkbox .v-selection-control__wrapper) {
  height: 20px;
  width: 20px;
}

.toolbar-filter-panel :deep(.v-checkbox .v-label) {
  padding-top: 1px;
  padding-bottom: 1px;
}

.toolbar-comfyui-panel {
  padding: 10px 12px;
  min-width: 380px;
  max-height: 50vh;
  overflow-y: auto;
  background: rgba(var(--v-theme-background), 0.92);
  color: rgb(var(--v-theme-on-background));
  border-radius: 10px;
  box-shadow: 2px 2px 12px rgba(0, 0, 0, 0.4);
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.toolbar-comfyui-header {
  font-size: 1.02em;
  font-weight: 500;
  letter-spacing: 0.02em;
}

.toolbar-comfyui-body {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.toolbar-comfyui-label {
  font-size: 0.82em;
  font-weight: 500;
  color: rgba(var(--v-theme-on-background), 0.7);
  display: block;
  margin-bottom: 2px;
}

.toolbar-comfyui-select {
  width: 100%;
  background: rgba(var(--v-theme-surface), 0.25);
  color: rgb(var(--v-theme-on-background));
  border: 1px solid rgba(var(--v-theme-on-background), 0.15);
  border-radius: 6px;
  padding: 5px 8px;
  font-size: 0.88em;
  outline: none;
}

.toolbar-comfyui-textarea {
  width: 100%;
  background: rgba(var(--v-theme-surface), 0.25);
  color: rgb(var(--v-theme-on-background));
  border: 1px solid rgba(var(--v-theme-on-background), 0.15);
  border-radius: 6px;
  padding: 5px 8px;
  font-size: 0.88em;
  resize: vertical;
  outline: none;
  font-family: inherit;
  min-height: 160px;
}

.toolbar-comfyui-run-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: rgba(var(--v-theme-primary), 0.85);
  color: rgb(var(--v-theme-on-primary));
  border: none;
  border-radius: 6px;
  padding: 4px 10px;
  font-size: 0.88em;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
  flex-shrink: 0;
  transition: background 0.15s;
}

.toolbar-comfyui-run-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.toolbar-comfyui-run-btn:not(:disabled):hover {
  background: rgba(var(--v-theme-primary), 1);
}

.toolbar-comfyui-note {
  font-size: 0.85em;
  color: rgba(var(--v-theme-on-background), 0.6);
}

.toolbar-comfyui-error {
  font-size: 0.85em;
  color: rgb(var(--v-theme-error));
}

.toolbar-comfyui-seed-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.toolbar-comfyui-seed-btn {
  background: rgba(var(--v-theme-surface), 0.25);
  color: rgb(var(--v-theme-on-background));
  border: 1px solid rgba(var(--v-theme-on-background), 0.15);
  border-radius: 6px;
  padding: 4px 10px;
  font-size: 0.85em;
  cursor: pointer;
  transition:
    background 0.15s,
    border-color 0.15s;
}

.toolbar-comfyui-seed-btn.active {
  background: rgba(var(--v-theme-primary), 0.8);
  color: rgb(var(--v-theme-on-primary));
  border-color: transparent;
}

.toolbar-comfyui-seed-input {
  flex: 1;
  min-width: 0;
  max-width: 110px;
  background: rgba(var(--v-theme-surface), 0.25);
  color: rgb(var(--v-theme-on-background));
  border: 1px solid rgba(var(--v-theme-on-background), 0.15);
  border-radius: 6px;
  padding: 4px 8px;
  font-size: 0.88em;
  outline: none;
}

.tag-filter-input-wrap {
  position: relative;
  width: 100%;
}

.tag-filter-input {
  width: 100%;
  background: rgba(var(--v-theme-on-background), 0.06);
  color: rgb(var(--v-theme-on-background));
  border: 1px solid rgba(var(--v-theme-on-background), 0.2);
  border-radius: 6px;
  padding: 5px 8px;
  font-size: 0.82em;
  outline: none;
  box-sizing: border-box;
}

.tag-filter-input:focus {
  border-color: rgba(var(--v-theme-primary), 0.6);
}

.tag-filter-dropdown {
  position: absolute;
  top: calc(100% + 3px);
  left: 0;
  right: 0;
  z-index: 999;
  background: color-mix(in srgb, rgb(var(--v-theme-shadow)) 85%, transparent);
  backdrop-filter: blur(6px);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.15);
  border-radius: 6px;
  box-shadow: 0 4px 18px rgba(0, 0, 0, 0.45);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.tag-filter-suggestion {
  display: block;
  width: 100%;
  text-align: left;
  padding: 5px 10px;
  font-size: 0.75rem;
  background: transparent;
  border: none;
  color: rgb(var(--v-theme-on-dark-surface));
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tag-filter-dropdown--hover-enabled .tag-filter-suggestion:hover,
.tag-filter-suggestion--active {
  background: rgba(var(--v-theme-primary), 0.22);
}

.tag-filter-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 4px;
}

.tag-chip {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  border-radius: 12px;
  padding: 2px 7px;
  font-size: 0.78rem;
  cursor: pointer;
  line-height: 1.5;
  white-space: nowrap;
  border: none;
}

.tag-chip--filter {
  background: rgba(var(--v-theme-primary), 0.18);
  border: 1px solid rgba(var(--v-theme-primary), 0.5);
  color: rgb(var(--v-theme-on-surface));
}

.tag-chip--filter:hover {
  background: rgba(var(--v-theme-error), 0.18);
  border-color: rgba(var(--v-theme-error), 0.55);
}

.tag-chip--filter-rejected {
  background: rgba(var(--v-theme-error), 0.14);
  border: 1px solid rgba(var(--v-theme-error), 0.5);
  color: rgb(var(--v-theme-error));
}

.tag-chip--filter-rejected:hover {
  background: rgba(var(--v-theme-primary), 0.18);
  border-color: rgba(var(--v-theme-primary), 0.5);
  color: rgb(var(--v-theme-on-surface));
}

.tag-chip--confidence-above {
  background: rgba(var(--v-theme-success), 0.14);
  border: 1px solid rgba(var(--v-theme-success), 0.5);
  color: rgb(var(--v-theme-success));
}

.tag-chip--confidence-above:hover {
  background: rgba(var(--v-theme-error), 0.14);
  border-color: rgba(var(--v-theme-error), 0.5);
}

.tag-chip--confidence-below {
  background: rgba(var(--v-theme-warning), 0.14);
  border: 1px solid rgba(var(--v-theme-warning), 0.5);
  color: rgb(var(--v-theme-warning));
}

.tag-chip--confidence-below:hover {
  background: rgba(var(--v-theme-error), 0.14);
  border-color: rgba(var(--v-theme-error), 0.5);
}

.confidence-filter-row {
  display: flex;
  align-items: center;
  gap: 4px;
  width: 100%;
}

.confidence-filter-tag-wrap {
  flex: 1;
  min-width: 0;
  position: relative;
}

.confidence-filter-tag-input {
  width: 100%;
}

.confidence-threshold-stepper {
  display: flex;
  align-items: stretch;
  border: 1px solid rgba(var(--v-theme-on-background), 0.18);
  border-radius: 6px;
  overflow: hidden;
  flex-shrink: 0;
}

.threshold-step-btn {
  background: rgba(var(--v-theme-on-background), 0.06);
  color: rgb(var(--v-theme-on-background));
  border: none;
  border-radius: 0;
  padding: 0 6px;
  font-size: 1em;
  line-height: 1;
  cursor: pointer;
  transition: background 0.15s;
  display: flex;
  align-items: center;
  justify-content: center;
}

.threshold-step-btn:hover:not(:disabled) {
  background: rgba(var(--v-theme-primary), 0.18);
}

.threshold-step-btn:disabled {
  opacity: 0.3;
  cursor: default;
}

.confidence-threshold-input {
  width: 40px;
  background: rgba(var(--v-theme-on-background), 0.04);
  color: rgb(var(--v-theme-on-background));
  border: none;
  border-left: 1px solid rgba(var(--v-theme-on-background), 0.12);
  border-right: 1px solid rgba(var(--v-theme-on-background), 0.12);
  padding: 5px 2px;
  font-size: 0.82em;
  outline: none;
  box-sizing: border-box;
  text-align: center;
}

.confidence-threshold-input:focus {
  background: rgba(var(--v-theme-primary), 0.07);
}

.confidence-threshold-input::-webkit-inner-spin-button,
.confidence-threshold-input::-webkit-outer-spin-button {
  -webkit-appearance: none;
  appearance: none;
  margin: 0;
}

.confidence-threshold-input[type="number"] {
  -moz-appearance: textfield;
}

.confidence-mode-btn {
  background: rgba(var(--v-theme-on-background), 0.08);
  color: rgb(var(--v-theme-on-background));
  border: 1px solid rgba(var(--v-theme-on-background), 0.2);
  border-radius: 6px;
  padding: 4px 8px;
  font-size: 0.9em;
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.15s;
}

.confidence-mode-btn:hover {
  background: rgba(var(--v-theme-primary), 0.18);
  border-color: rgba(var(--v-theme-primary), 0.4);
}

.confidence-add-btn {
  background: rgba(var(--v-theme-primary), 0.15);
  color: rgb(var(--v-theme-primary));
  border: 1px solid rgba(var(--v-theme-primary), 0.4);
  border-radius: 6px;
  padding: 4px 10px;
  font-size: 0.82em;
  cursor: pointer;
  transition: background 0.15s;
}

.score-range-section {
  margin-top: 10px;
}

.score-range-headers {
  display: flex;
  justify-content: space-between;
  margin-bottom: 1px;
}

.score-range-header-label {
  font-size: 0.72em;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  opacity: 0.5;
  user-select: none;
}

.score-range-filter {
  display: flex;
  justify-content: space-between;
  gap: 6px;
}

.score-range-stars {
  display: flex;
  gap: 0;
}

.score-range-stars--right {
  justify-content: flex-end;
}

.score-range-stars :deep(.score-star-btn.v-btn) {
  width: 20px !important;
  height: 20px !important;
  min-width: 20px !important;
  padding: 0 !important;
}

.confidence-add-btn:hover:not(:disabled) {
  background: rgba(var(--v-theme-primary), 0.3);
}

.confidence-add-btn:disabled {
  opacity: 0.4;
  cursor: default;
}

.tag-chip-label {
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tag-chip-close {
  opacity: 0.6;
}

.face-no-detection-icon {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.face-no-detection-icon::after {
  content: "";
  position: absolute;
  top: 50%;
  left: -1px;
  right: -1px;
  height: 1.5px;
  background: currentColor;
  transform: translateY(-50%) rotate(-35deg);
  border-radius: 1px;
}
</style>
