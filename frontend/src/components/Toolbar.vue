<template>
  <div class="top-toolbar">
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
              @keydown.enter="handleSearchEnter"
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
      <div class="toolbar-sort-controls">
        <v-btn
          v-if="isMobile"
          icon
          :color="sidebarVisible ? 'primary' : 'surface'"
          @click="emit('toggle-sidebar')"
          title="Toggle sidebar"
          class="toolbar-action-btn"
        >
          <v-icon>mdi-dock-left</v-icon>
        </v-btn>
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
          v-model="sortMenuOpen"
          :close-on-content-click="false"
          location="top start"
          origin="bottom start"
          transition="scale-transition"
        >
          <template #activator="{ props }">
            <div
              class="toolbar-split-button"
              :class="{ 'toolbar-split-button--icon': isMobile }"
            >
              <v-btn
                class="toolbar-action-btn toolbar-split-toggle"
                :title="descendingModel ? 'Descending' : 'Ascending'"
                @click.stop="toggleSortDirection"
              >
                <v-icon>{{ sortButtonIcon }}</v-icon>
              </v-btn>
              <v-btn
                v-bind="props"
                ref="sortButtonRef"
                :icon="isMobile"
                class="toolbar-action-btn toolbar-sort-activator toolbar-split-menu"
                :title="sortButtonLabel"
              >
                <v-icon>{{ sortTypeIcon }}</v-icon>
                <span v-if="!isMobile" class="toolbar-sort-activator-label">
                  {{ sortButtonLabel }}
                </span>
                <v-icon v-if="!isMobile" size="18" class="toolbar-sort-chevron">
                  mdi-menu-down
                </v-icon>
              </v-btn>
            </div>
          </template>
          <div class="toolbar-sort-panel">
            <div class="toolbar-sort-header">
              <div class="toolbar-sort-panel-title">
                Sort order
                <span>Choose one</span>
              </div>
              <v-btn
                class="toolbar-sort-direction"
                variant="text"
                :disabled="isSearchActive"
                @click="toggleSortDirection"
              >
                <v-icon size="18">
                  {{
                    descendingModel
                      ? "mdi-sort-descending"
                      : "mdi-sort-ascending"
                  }}
                </v-icon>
                <span>
                  {{ descendingModel ? "Descending" : "Ascending" }}
                </span>
              </v-btn>
            </div>
            <div v-if="isSearchActive" class="toolbar-sort-search-note">
              Search relevance (fixed)
            </div>
            <v-btn-toggle
              v-model="sortModel"
              mandatory
              class="toolbar-sort-grid"
              :disabled="isSearchActive"
            >
              <v-btn
                v-for="opt in sortOptions"
                :key="opt.value"
                :value="opt.value"
                class="toolbar-sort-grid-btn"
                variant="text"
              >
                <v-icon size="18">{{ getSortIcon(opt.value) }}</v-icon>
                <span class="toolbar-sort-grid-label">{{ opt.label }}</span>
                <v-icon
                  v-if="sortModel === opt.value"
                  size="16"
                  class="toolbar-sort-grid-selected"
                >
                  mdi-check-circle
                </v-icon>
              </v-btn>
            </v-btn-toggle>

            <div
              v-if="sortModel === SIMILARITY_SORT_KEY"
              class="toolbar-sort-similarity-row"
            >
              <span>Similarity to</span>
              <div class="toolbar-similarity-scroll">
                <v-btn-toggle
                  v-model="similarityCharacterModel"
                  class="toolbar-sort-grid"
                  :disabled="!hasSimilarityOptions"
                  mandatory
                >
                  <v-btn
                    v-for="opt in similarityCharacterOptions"
                    :key="opt.value"
                    :value="opt.value"
                    class="toolbar-sort-grid-btn"
                    variant="text"
                  >
                    <img
                      v-if="opt.thumbnail"
                      :src="opt.thumbnail"
                      class="toolbar-similarity-thumb"
                      alt=""
                    />
                    <div
                      v-else
                      class="toolbar-similarity-thumb toolbar-similarity-thumb--placeholder"
                    ></div>
                    <span class="toolbar-sort-grid-label">{{ opt.text }}</span>
                    <v-icon
                      v-if="similarityCharacterModel === opt.value"
                      size="16"
                      class="toolbar-sort-grid-selected"
                    >
                      mdi-check-circle
                    </v-icon>
                  </v-btn>
                </v-btn-toggle>
              </div>
            </div>
            <div
              v-if="sortModel === STACKS_SORT_KEY"
              class="toolbar-sort-similarity-row"
            >
              <span>Stack strictness</span>
              <div class="toolbar-similarity-scroll">
                <v-btn-toggle
                  v-model="stackThresholdModel"
                  class="toolbar-sort-grid"
                  mandatory
                >
                  <v-btn
                    v-for="opt in stackThresholdOptions"
                    :key="opt.value"
                    :value="opt.value"
                    class="toolbar-sort-grid-btn"
                    variant="text"
                  >
                    <span class="toolbar-sort-grid-label">{{ opt.label }}</span>
                    <v-icon
                      v-if="stackThresholdModel === opt.value"
                      size="16"
                      class="toolbar-sort-grid-selected"
                    >
                      mdi-check-circle
                    </v-icon>
                  </v-btn>
                </v-btn-toggle>
              </div>
            </div>
          </div>
        </v-menu>
      </div>
      <div class="toolbar-controls">
        <v-menu
          v-model="columnsMenuOpenModel"
          offset-y
          :close-on-content-click="false"
          transition="scale-transition"
        >
          <template #activator="{ props }">
            <v-btn
              icon
              v-bind="props"
              :color="
                props['aria-expanded'] === 'true' ? 'primary' : 'undefined'
              "
              title="Set grid columns"
              class="toolbar-action-btn"
            >
              <v-icon>mdi-view-grid</v-icon>
            </v-btn>
          </template>
          <div
            style="
              padding: 8px 8px;
              min-width: 200px;
              background: rgba(var(--v-theme-background), 0.9);
              border-radius: 8px;
              box-shadow: 2px 2px 12px rgba(0, 0, 0, 0.4);
              display: flex;
              flex-direction: column;
              align-items: center;
              min-height: 56px;
              justify-content: center;
            "
          >
            <span
              style="
                font-size: 1.08em;
                margin-bottom: 6px;
                color: rgb(var(--v-theme-on-background));
                font-weight: 500;
                letter-spacing: 0.02em;
              "
              >Columns: {{ pendingColumns }}</span
            >
            <v-slider
              v-model="pendingColumns"
              :min="minColumns"
              :max="maxColumns"
              :step="1"
              vertical
              style="height: 40px; width: 80%; margin-bottom: 0"
              hide-details
              track-color="#888"
              thumb-color="primary"
              @end="commitColumns"
            />
          </div>
        </v-menu>
        <v-menu
          v-model="overlaysMenuOpenModel"
          offset-y
          :close-on-content-click="false"
          transition="scale-transition"
        >
          <template #activator="{ props }">
            <v-btn
              icon
              v-bind="props"
              :color="props['aria-expanded'] === 'true' ? 'primary' : 'surface'"
              title="Overlay options"
              class="toolbar-action-btn"
            >
              <v-icon :color="'on-background'">mdi-layers-outline</v-icon>
            </v-btn>
          </template>
          <div
            style="
              padding: 10px 12px;
              min-width: 220px;
              background: rgba(var(--v-theme-background), 0.9);
              color: rgb(var(--v-theme-on-background));
              border-radius: 8px;
              box-shadow: 2px 2px 12px rgba(0, 0, 0, 0.4);
              display: flex;
              flex-direction: column;
              gap: 6px;
            "
          >
            <div
              style="
                font-size: 1.02em;
                font-weight: 500;
                letter-spacing: 0.02em;
                margin-bottom: 4px;
              "
            >
              Image Information Overlays
            </div>
            <v-switch
              v-model="showStarsModel"
              label="Star ratings"
              color="primary"
              density="compact"
              hide-details
            />
            <v-switch
              v-model="showFaceBboxesModel"
              label="Face bounding boxes"
              color="primary"
              density="compact"
              hide-details
            />
            <v-switch
              v-model="showHandBboxesModel"
              label="Hand bounding boxes"
              color="primary"
              density="compact"
              hide-details
            />
            <v-switch
              v-model="showFormatModel"
              label="Image format"
              color="primary"
              density="compact"
              hide-details
            />
            <v-switch
              v-model="showResolutionModel"
              label="Resolution"
              color="primary"
              density="compact"
              hide-details
            />
            <v-switch
              v-model="showProblemIconModel"
              label="Image problem indicator"
              color="primary"
              density="compact"
              hide-details
            />
          </div>
        </v-menu>
        <v-menu
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
            <v-switch
              v-model="exportIncludeCharacterNameModel"
              label="Include character name"
              color="primary"
              density="comfortable"
              :disabled="
                exportCaptionMode === 'none' || exportTypeLocksCaptions
              "
            />
            <v-btn color="primary" @click="emit('confirm-export-zip')">
              Export
            </v-btn>
          </div>
        </v-menu>

        <v-menu
          offset-y
          :close-on-content-click="false"
          transition="scale-transition"
        >
          <template #activator="{ props }">
            <v-btn
              icon
              v-bind="props"
              :color="props['aria-expanded'] === 'true' ? 'primary' : 'surface'"
              title="Filter media type"
              class="toolbar-action-btn"
            >
              <v-icon :color="'on-background'">mdi-filter</v-icon>
            </v-btn>
          </template>
          <div
            style="
              padding: 10px 12px;
              min-width: 200px;
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
                font-size: 1.02em;
                font-weight: 500;
                letter-spacing: 0.02em;
              "
            >
              Media filter
            </div>
            <v-btn-toggle
              v-model="mediaTypeFilterModel"
              mandatory
              class="media-type-toggle"
              dense
            >
              <v-btn value="all" title="Show all media">
                <v-icon>mdi-multimedia</v-icon>
              </v-btn>
              <v-btn value="images" title="Show images only">
                <v-icon>mdi-image</v-icon>
              </v-btn>
              <v-btn value="videos" title="Show videos only">
                <v-icon>mdi-video</v-icon>
              </v-btn>
            </v-btn-toggle>
          </div>
        </v-menu>
        <v-btn
          icon
          v-bind="props"
          :color="props['aria-expanded'] === 'true' ? 'primary' : 'surface'"
          title="Filter media type"
          class="toolbar-action-btn"
          @click="emit('open-settings')"
        >
          <v-icon :color="'on-background'">mdi-cog</v-icon>
        </v-btn>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue";

const props = defineProps({
  isMobile: { type: Boolean, default: false },
  sidebarVisible: { type: Boolean, default: true },
  searchOverlayVisible: { type: Boolean, default: false },
  isSearchActive: { type: Boolean, default: false },
  searchInput: { type: String, default: "" },
  isSearchHistoryOpen: { type: Boolean, default: false },
  filteredSearchHistory: { type: Array, default: () => [] },
  columnsMenuOpen: { type: Boolean, default: false },
  overlaysMenuOpen: { type: Boolean, default: false },
  exportMenuOpen: { type: Boolean, default: false },
  columns: { type: Number, default: 4 },
  minColumns: { type: Number, default: 1 },
  maxColumns: { type: Number, default: 10 },
  showStars: { type: Boolean, default: true },
  showFaceBboxes: { type: Boolean, default: false },
  showHandBboxes: { type: Boolean, default: false },
  showFormat: { type: Boolean, default: true },
  showResolution: { type: Boolean, default: true },
  showProblemIcon: { type: Boolean, default: true },
  exportCount: { type: Number, default: 0 },
  exportType: { type: String, default: "full" },
  exportCaptionMode: { type: String, default: "description" },
  exportIncludeCharacterName: { type: Boolean, default: true },
  exportResolution: { type: String, default: "original" },
  exportTypeLocksCaptions: { type: Boolean, default: false },
  exportCaptionOptions: { type: Array, default: () => [] },
  exportTypeOptions: { type: Array, default: () => [] },
  exportResolutionOptions: { type: Array, default: () => [] },
  mediaTypeFilter: { type: String, default: "all" },
  sortOptions: { type: Array, default: () => [] },
  selectedSort: { type: String, default: "" },
  selectedDescending: { type: Boolean, default: true },
  similarityCharacterOptions: { type: Array, default: () => [] },
  selectedSimilarityCharacter: { type: [String, Number, null], default: null },
  stackThreshold: { type: [String, Number, null], default: null },
});

const emit = defineEmits([
  "update:searchInput",
  "update:isSearchHistoryOpen",
  "update:columnsMenuOpen",
  "update:overlaysMenuOpen",
  "update:exportMenuOpen",
  "update:columns",
  "update:showStars",
  "update:showFaceBboxes",
  "update:showHandBboxes",
  "update:showFormat",
  "update:showResolution",
  "update:showProblemIcon",
  "update:exportType",
  "update:exportCaptionMode",
  "update:exportResolution",
  "update:exportIncludeCharacterName",
  "update:mediaTypeFilter",
  "update:similarity-character",
  "update:stack-threshold",
  "open-search-overlay",
  "commit-search",
  "clear-search",
  "apply-search-history",
  "clear-search-history",
  "columns-end",
  "confirm-export-zip",
  "open-settings",
  "toggle-sidebar",
  "update:selected-sort",
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

const columnsMenuOpenModel = computed({
  get: () => props.columnsMenuOpen,
  set: (value) => emit("update:columnsMenuOpen", value),
});

const overlaysMenuOpenModel = computed({
  get: () => props.overlaysMenuOpen,
  set: (value) => emit("update:overlaysMenuOpen", value),
});

const exportMenuOpenModel = computed({
  get: () => props.exportMenuOpen,
  set: (value) => emit("update:exportMenuOpen", value),
});

const columnsModel = computed({
  get: () => props.columns,
  set: (value) => emit("update:columns", value),
});

const pendingColumns = ref(props.columns);

watch(
  () => props.columns,
  (value) => {
    if (!columnsMenuOpenModel.value) {
      pendingColumns.value = value;
    }
  },
);

watch(
  () => columnsMenuOpenModel.value,
  (isOpen) => {
    if (isOpen) {
      pendingColumns.value = props.columns;
    }
  },
);

const showStarsModel = computed({
  get: () => props.showStars,
  set: (value) => emit("update:showStars", value),
});

const showFaceBboxesModel = computed({
  get: () => props.showFaceBboxes,
  set: (value) => emit("update:showFaceBboxes", value),
});

const showHandBboxesModel = computed({
  get: () => props.showHandBboxes,
  set: (value) => emit("update:showHandBboxes", value),
});

const showFormatModel = computed({
  get: () => props.showFormat,
  set: (value) => emit("update:showFormat", value),
});

const showResolutionModel = computed({
  get: () => props.showResolution,
  set: (value) => emit("update:showResolution", value),
});

const showProblemIconModel = computed({
  get: () => props.showProblemIcon,
  set: (value) => emit("update:showProblemIcon", value),
});

const exportTypeModel = computed({
  get: () => props.exportType,
  set: (value) => emit("update:exportType", value),
});

const exportCaptionModeModel = computed({
  get: () => props.exportCaptionMode,
  set: (value) => emit("update:exportCaptionMode", value),
});

const exportResolutionModel = computed({
  get: () => props.exportResolution,
  set: (value) => emit("update:exportResolution", value),
});

const exportIncludeCharacterNameModel = computed({
  get: () => props.exportIncludeCharacterName,
  set: (value) => emit("update:exportIncludeCharacterName", value),
});

const mediaTypeFilterModel = computed({
  get: () => props.mediaTypeFilter,
  set: (value) => emit("update:mediaTypeFilter", value),
});

const sortMenuOpen = ref(false);
const sortButtonRef = ref(null);

const sortModel = computed({
  get: () => props.selectedSort,
  set: (value) =>
    emit("update:selected-sort", {
      sort: value != null ? String(value) : "",
      descending: descendingModel.value,
    }),
});

const SIMILARITY_SORT_KEY = "CHARACTER_LIKENESS";
const STACKS_SORT_KEY = "PICTURE_STACKS";

const hasSimilarityOptions = computed(() => {
  return (
    Array.isArray(props.similarityCharacterOptions) &&
    props.similarityCharacterOptions.length > 0
  );
});

const similarityCharacterModel = computed({
  get: () => props.selectedSimilarityCharacter,
  set: (value) => emit("update:similarity-character", value ?? null),
});

const stackThresholdOptions = [
  { label: "Very Loose", value: "0.92" },
  { label: "Loose", value: "0.95" },
  { label: "Medium", value: "0.97" },
  { label: "Strict", value: "0.99" },
  { label: "Very Strict", value: "0.995" },
];

const stackThresholdModel = computed({
  get: () => {
    if (props.stackThreshold == null || props.stackThreshold === "") {
      return "0.92";
    }
    const parsed = parseFloat(String(props.stackThreshold));
    if (!Number.isFinite(parsed) || parsed <= 0) {
      return "0.92";
    }
    return String(props.stackThreshold);
  },
  set: (value) => emit("update:stack-threshold", value),
});

const selectedSimilarityOption = computed(() => {
  return props.similarityCharacterOptions.find(
    (opt) => opt.value === similarityCharacterModel.value,
  );
});

const selectedSortOption = computed(() => {
  return props.sortOptions.find((opt) => opt.value === sortModel.value);
});

const selectedStackThresholdOption = computed(() => {
  return stackThresholdOptions.find(
    (opt) => opt.value === stackThresholdModel.value,
  );
});

const sortButtonLabel = computed(() => {
  if (props.isSearchActive) {
    return "Search relevance";
  }
  if (sortModel.value === SIMILARITY_SORT_KEY) {
    return selectedSimilarityOption.value?.text
      ? `Similarity: ${selectedSimilarityOption.value.text}`
      : "Similarity";
  }
  if (sortModel.value === STACKS_SORT_KEY) {
    return selectedStackThresholdOption.value?.label
      ? `Stacks: ${selectedStackThresholdOption.value.label}`
      : "Stacks";
  }
  return selectedSortOption.value?.label || "Sort";
});

const sortButtonIcon = computed(() => {
  return descendingModel.value ? "mdi-sort-descending" : "mdi-sort-ascending";
});

const sortTypeIcon = computed(() => {
  if (props.isSearchActive) {
    return "mdi-magnify";
  }
  return getSortIcon(sortModel.value);
});

const SORT_ICON_MAP = {
  DATE: "mdi-calendar",
  CHARACTER_LIKENESS: "mdi-account-search",
  SMART_SCORE: "mdi-brain",
  SCORE: "mdi-star",
  PICTURE_STACKS: "mdi-layers",
  NAME: "mdi-sort-alphabetical",
  IMAGE_SIZE: "mdi-image-size-select-large",
  RANDOM: "mdi-shuffle",
};

function getSortIcon(value) {
  if (!value) return "mdi-sort";
  const key = String(value).toUpperCase();
  return SORT_ICON_MAP[key] || "mdi-sort";
}

const descendingModel = computed({
  get: () => props.selectedDescending,
  set: (value) =>
    emit("update:selected-sort", {
      sort: sortModel.value,
      descending: Boolean(value),
    }),
});

function toggleSortDirection() {
  descendingModel.value = !descendingModel.value;
}

function commitColumns() {
  emit("update:columns", pendingColumns.value);
  emit("columns-end");
}

const mediaTypeFilterLabel = computed(() => {
  switch (props.mediaTypeFilter) {
    case "images":
      return "Images";
    case "videos":
      return "Videos";
    default:
      return "All";
  }
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

defineExpose({ blurSearchInput });
</script>

<style scoped>
.top-toolbar {
  background-color: rgb(var(--v-theme-toolbar)) !important;
  width: 100%;
  min-height: 38px;
  display: flex;
  vertical-align: top;
  padding: 4px 4px 4px 4px;
  z-index: 5;
  position: relative;
  --toolbar-control-height: 44px;
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
  grid-auto-rows: minmax(42px, auto);
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

.toolbar-sort-grid-selected {
  position: absolute;
  right: 2px;
  color: rgb(var(--v-theme-accent));
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
  color: rgba(var(--v-theme-on-background), 0.7);
  padding: 6px 8px;
  border-radius: 6px;
  background: rgba(var(--v-theme-surface), 0.2);
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
  background: rgba(var(--v-theme-surface), 0.2);
  min-height: var(--toolbar-control-height);
  height: var(--toolbar-control-height);
  align-items: center;
  box-shadow: 2px 2px 2px rgba(0, 0, 0, 0.4) !important;
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
  color: rgb(var(--v-theme-on-background)) !important;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.toolbar-action-btn:hover,
.toolbar-action-btn:focus-visible {
  box-shadow: none !important;
  background-color: transparent !important;
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

.toolbar-split-button {
  display: inline-flex;
  align-items: center;
  border-radius: 8px;
  overflow: hidden;
  background: rgba(var(--v-theme-surface), 0.2) !important;
  box-shadow: 2px 2px 2px rgba(0, 0, 0, 0.4) !important;
  height: var(--toolbar-control-height);
}

.toolbar-split-button .toolbar-action-btn {
  border-radius: 0;
  height: var(--toolbar-control-height);
}

.toolbar-split-toggle {
  border-right: 1px solid rgba(var(--v-theme-on-background), 0.1);
  background: rgba(var(--v-theme-primary), 0.35) !important;
  color: rgb(var(--v-theme-on-primary)) !important;
}

.toolbar-split-menu {
  padding: 0 10px;
}

.toolbar-sort-activator {
  padding: 0 10px;
  min-width: var(--toolbar-control-height);
  width: 190px;
  justify-content: flex-start;
}

.toolbar-split-button .toolbar-sort-activator {
  width: 190px;
}

.toolbar-split-button--icon .toolbar-sort-activator,
.toolbar-split-button--icon .toolbar-split-menu {
  width: auto;
  padding: 0;
}

.toolbar-sort-activator :deep(.v-btn__content) {
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
  border-radius: 8px;
  margin-left: 4px;
  display: inline-flex;
}

.media-type-toggle .v-btn {
  color: rgb(var(--v-theme-on-primary)) !important;
  height: var(--toolbar-control-height);
  width: var(--toolbar-control-height);
  background-color: rgba(var(--v-theme-surface), 0.3) !important;
  align-items: center;
}

.media-type-toggle {
  background-color: rgb(var(--v-theme-primary)) !important;
  color: rgb(var(--v-theme-on-primary)) !important;
}

@media (max-width: 900px) {
  .toolbar-actions {
    width: auto;
    flex-wrap: nowrap;
    gap: 2px;
    margin-left: 0;
    justify-content: flex-start;
  }
}
</style>
