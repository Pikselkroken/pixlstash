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
      <div class="toolbar-controls">
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
          style="margin-left: 4px"
        >
          <v-icon>mdi-magnify</v-icon>
        </v-btn>
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
              style="margin-left: 4px"
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
              >Columns: {{ columns }}</span
            >
            <v-slider
              v-model="columnsModel"
              :min="minColumns"
              :max="maxColumns"
              :step="1"
              vertical
              style="height: 40px; width: 80%; margin-bottom: 0"
              hide-details
              track-color="#888"
              thumb-color="primary"
              @end="emit('columns-end')"
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
              style="margin-left: 4px"
            >
              <v-icon :color="'onBackground'">mdi-layers-outline</v-icon>
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
              style="margin-left: 4px"
            >
              <v-icon :color="'onBackground'">mdi-download</v-icon>
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
              :color="'onSurface'"
              :items="exportTypeOptions"
              item-title="title"
              item-value="value"
              label="Export type"
              density="comfortable"
            />
            <v-select
              v-model="exportCaptionModeModel"
              :background-color="'surface'"
              :color="'onSurface'"
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
              :color="'onSurface'"
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
              style="margin-left: 4px"
            >
              <v-icon :color="'onBackground'">mdi-filter</v-icon>
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
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from "vue";

const props = defineProps({
  isMobile: { type: Boolean, default: false },
  searchOverlayVisible: { type: Boolean, default: false },
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
  "open-search-overlay",
  "commit-search",
  "clear-search",
  "apply-search-history",
  "clear-search-history",
  "columns-end",
  "confirm-export-zip",
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
  min-height: 30px;
  display: flex;
  align-items: center;
  padding: 4px 4px 4px 4px;
  z-index: 5;
  position: relative;
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
}

.toolbar-search-field :deep(.v-field) {
  border-radius: 8px;
  background: rgba(var(--v-theme-surface), 0.2);
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
  min-width: 32px;
  min-height: 32px;
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

.toolbar-action-btn.v-btn--active {
  border-color: rgba(var(--v-theme-surface), 0.2);
}

.media-type-toggle {
  border-radius: 8px;
  margin-left: 4px;
  display: inline-flex;
}

.media-type-toggle .v-btn {
  color: rgb(var(--v-theme-on-secondary)) !important;
  height: 32px;
  width: 32px;
  background-color: rgba(var(--v-theme-surface), 0.3) !important;
  align-items: center;
}

.media-type-toggle {
  background-color: rgb(var(--v-theme-secondary)) !important;
  color: white !important;
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
