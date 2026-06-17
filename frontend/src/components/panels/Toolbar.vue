<template>
  <div class="selection-bar-overlay">
    <div class="selection-bar-content">
      <div class="selection-bar-left">
        <!-- ── Sort split-button ──────────────────────────────────── -->
        <v-menu
          v-model="gbSortMenuOpen"
          :close-on-content-click="false"
          location="bottom start"
          origin="top start"
          transition="scale-transition"
        >
          <template #activator="{ props: menuProps }">
            <div class="bar-split-button">
              <button
                class="bar-btn bar-split-toggle"
                type="button"
                :title="gbDescendingModel ? 'Descending' : 'Ascending'"
                :disabled="gbSortModel === LIKENESS_GROUPS_SORT_KEY_GB"
                @click.stop="gbToggleSortDirection"
              >
                <v-icon size="19">{{ gbSortButtonIcon }}</v-icon>
              </button>
              <button
                v-bind="menuProps"
                class="bar-btn bar-split-menu"
                type="button"
                :title="gbSortButtonLabel"
              >
                <span class="bar-btn-prefix">Sort:</span>
                <v-icon size="19">{{ gbSortTypeIcon }}</v-icon>
                <span class="bar-btn-sort-type">{{ gbSortTypeName }}</span>
                <span
                  v-if="gbSortSecondaryLabel"
                  class="bar-btn-sort-secondary"
                  >{{ gbSortSecondaryLabel }}</span
                >
                <v-icon size="18" class="bar-btn-chevron">mdi-menu-down</v-icon>
              </button>
            </div>
          </template>
          <div class="gb-sort-panel popup-panel">
            <div class="gb-sort-header">
              <div class="gb-sort-panel-title">
                Sort order
                <span>Choose one</span>
              </div>
              <v-btn
                class="gb-sort-direction"
                variant="text"
                :disabled="
                  Boolean(
                    searchStore.searchQuery && searchStore.searchQuery.trim(),
                  ) || gbSortModel === LIKENESS_GROUPS_SORT_KEY_GB
                "
                @click="gbToggleSortDirection"
              >
                <v-icon size="18">
                  {{
                    gbDescendingModel
                      ? "mdi-sort-descending"
                      : "mdi-sort-ascending"
                  }}
                </v-icon>
                <span>{{
                  gbDescendingModel ? "Descending" : "Ascending"
                }}</span>
              </v-btn>
            </div>
            <div
              v-if="
                Boolean(
                  searchStore.searchQuery && searchStore.searchQuery.trim(),
                )
              "
              class="gb-sort-search-note"
            >
              Search relevance (fixed)
            </div>
            <v-btn-toggle
              :model-value="gbSortMenuModel"
              @update:model-value="gbHandleSortModelUpdate"
              mandatory
              class="gb-sort-grid"
              :disabled="
                Boolean(
                  searchStore.searchQuery && searchStore.searchQuery.trim(),
                )
              "
            >
              <v-btn
                v-for="opt in filteredSortOptions"
                :key="opt.value"
                :value="opt.value"
                class="gb-sort-grid-btn"
                variant="text"
              >
                <v-icon size="18">{{ gbGetSortIcon(opt.value) }}</v-icon>
                <span class="gb-sort-grid-label">{{ opt.label }}</span>
                <v-icon
                  v-if="gbSortMenuModel === opt.value"
                  size="16"
                  class="gb-sort-grid-selected"
                  >mdi-circle-medium</v-icon
                >
              </v-btn>
            </v-btn-toggle>
            <div
              v-if="gbSortMenuModel === SIMILARITY_SORT_KEY_GB"
              class="gb-sort-similarity-row"
            >
              <span>Similarity to ...</span>
              <div class="gb-similarity-scroll">
                <v-btn-toggle
                  v-model="gbSimilarityCharacterModel"
                  class="gb-sort-grid"
                  :class="{
                    'gb-sort-grid--pending-parameter':
                      gbIsPendingSimilarityParameter,
                  }"
                  :disabled="!gbHasSimilarityOptions"
                >
                  <v-btn
                    v-for="opt in sortStore.similarityCharacterOptions ?? []"
                    :key="opt.value"
                    :value="opt.value"
                    class="gb-sort-grid-btn"
                    variant="text"
                    @click="gbHandleSimilarityOptionClick(opt.value)"
                  >
                    <img
                      v-if="opt.thumbnail"
                      :src="opt.thumbnail"
                      class="gb-similarity-thumb"
                      alt=""
                    />
                    <div
                      v-else
                      class="gb-similarity-thumb gb-similarity-thumb--placeholder"
                    ></div>
                    <span class="gb-sort-grid-label">{{ opt.text }}</span>
                    <v-icon
                      v-if="gbSimilarityCharacterModel === opt.value"
                      size="16"
                      class="gb-sort-grid-selected"
                      :class="{
                        'gb-sort-grid-selected--pending':
                          gbIsPendingSimilarityParameter,
                      }"
                      >mdi-circle-medium</v-icon
                    >
                  </v-btn>
                </v-btn-toggle>
              </div>
            </div>
            <div
              v-if="gbSortMenuModel === LIKENESS_GROUPS_SORT_KEY_GB"
              class="gb-sort-similarity-row"
            >
              <span>Group strictness</span>
              <div class="gb-similarity-scroll">
                <v-btn-toggle
                  v-model="gbStackThresholdModel"
                  class="gb-sort-grid"
                  :class="{
                    'gb-sort-grid--pending-parameter':
                      gbIsPendingStackParameter,
                  }"
                >
                  <v-btn
                    v-for="opt in gbStackThresholdOptions"
                    :key="opt.value"
                    :value="opt.value"
                    class="gb-sort-grid-btn"
                    variant="text"
                    @click="gbHandleStackThresholdOptionClick(opt.value)"
                  >
                    <span class="gb-sort-grid-label">{{ opt.label }}</span>
                    <v-icon
                      v-if="gbStackThresholdModel === opt.value"
                      size="16"
                      class="gb-sort-grid-selected"
                      :class="{
                        'gb-sort-grid-selected--pending':
                          gbIsPendingStackParameter,
                      }"
                      >mdi-circle-medium</v-icon
                    >
                  </v-btn>
                </v-btn-toggle>
              </div>
            </div>
          </div>
        </v-menu>
        <!-- ── Filter button ──────────────────────────────────────── -->
        <v-menu
          v-model="gbFilterMenuOpen"
          :close-on-content-click="false"
          location="bottom end"
          origin="top end"
          transition="scale-transition"
        >
          <template #activator="{ props: menuProps }">
            <button
              v-bind="menuProps"
              class="bar-btn bar-btn--boxed"
              :class="{ 'bar-btn--active': filterStore.isActive }"
              type="button"
              title="Filters"
            >
              <v-icon size="19">mdi-filter</v-icon>
              <span
                v-if="filterStore.activeCount > 0"
                class="bar-filter-badge"
                >{{
                  filterStore.activeCount > 99 ? "99+" : filterStore.activeCount
                }}</span
              >
              <v-icon size="18" class="bar-btn-chevron">mdi-menu-down</v-icon>
            </button>
          </template>
          <GbFilterPanel
            :backend-url="props.backendUrl"
            :selected-character="props.selectedCharacter"
            :all-pictures-id="props.allPicturesId"
            :open="gbFilterMenuOpen"
          />
        </v-menu>
        <!-- ── View button ────────────────────────────────────────── -->
        <v-menu
          v-model="gbViewMenuOpen"
          :close-on-content-click="false"
          location="bottom end"
          origin="top end"
          transition="scale-transition"
        >
          <template #activator="{ props: menuProps }">
            <button
              v-bind="menuProps"
              class="bar-btn bar-btn--boxed"
              type="button"
              title="View options"
            >
              <v-icon size="19">mdi-view-grid</v-icon>
              <v-icon size="18" class="bar-btn-chevron">mdi-menu-down</v-icon>
            </button>
          </template>
          <div class="gb-view-panel popup-panel">
            <div class="gb-filter-section-label">Grid View</div>
            <v-switch
              v-model="gbCompactModeModel"
              label="Compact mode"
              color="primary"
              density="compact"
              hide-details
              class="gb-view-switch"
            />
            <div class="gb-columns-row">
              <span class="gb-columns-label"
                >Columns: {{ gbPendingColumns }}</span
              >
              <v-slider
                class="gb-columns-slider"
                v-model="gbPendingColumns"
                :min="gridStore.minColumns ?? 2"
                :max="gridStore.maxColumns ?? 14"
                :step="1"
                density="compact"
                hide-details
                track-color="#888"
                thumb-color="primary"
                @end="gbCommitColumns"
              />
            </div>
            <div class="gb-stacks-controls">
              <div class="gb-filter-section-label">Stacks</div>
              <div class="gb-stacks-buttons">
                <v-btn
                  class="gb-stack-toggle-btn"
                  color="primary"
                  variant="flat"
                  size="small"
                  :disabled="gbExpandAllStacksDisabled"
                  @click="emit('expand-all-stacks')"
                  >Expand all</v-btn
                >
                <v-btn
                  class="gb-stack-toggle-btn"
                  color="primary"
                  variant="flat"
                  size="small"
                  :disabled="gbCollapseAllStacksDisabled"
                  @click="emit('collapse-all-stacks')"
                  >Collapse all</v-btn
                >
              </div>
            </div>
            <div class="gb-filter-section-label" style="margin-top: 10px">
              Overlays
            </div>
            <div class="gb-overlay-grid">
              <button
                v-for="ovl in gbOverlayOptions"
                :key="ovl.key"
                class="gb-overlay-btn"
                :class="{ 'gb-overlay-btn--active': ovl.model.value }"
                :title="ovl.label"
                type="button"
                @click="ovl.model.value = !ovl.model.value"
              >
                <v-icon size="16">{{ ovl.icon }}</v-icon>
                <span class="gb-overlay-btn-label">{{ ovl.label }}</span>
              </button>
            </div>
          </div>
        </v-menu>
        <!-- ── Toolbar: divider ─────────────────────────────────────── -->
        <div class="bar-separator"></div>
        <!-- ── Toolbar: Search ──────────────────────────────────────── -->
        <button
          class="bar-btn bar-btn--icon"
          :class="{ 'bar-btn--active': searchStore.isSearchActive }"
          type="button"
          title="Search (F)"
          @click="searchStore.searchOverlayVisible = true"
        >
          <v-icon size="20">mdi-magnify</v-icon>
        </button>
        <!-- ── Toolbar: Review suggested tag fixes ───────────────────── -->
        <button
          class="bar-btn bar-btn--icon"
          type="button"
          :disabled="isReadOnly"
          title="Review suggested tag fixes"
          @click="reviewFixesStore.overlayOpen = true"
        >
          <v-icon size="20">mdi-tag-check-outline</v-icon>
        </button>
        <!-- ── Toolbar: Export ───────────────────────────────────────── -->
        <v-menu
          v-model="exportStore.exportMenuOpen"
          :close-on-content-click="false"
          location="bottom start"
          origin="top start"
          transition="scale-transition"
        >
          <template #activator="{ props: menuProps }">
            <button
              v-bind="menuProps"
              class="bar-btn bar-btn--icon tb-export-btn"
              type="button"
              title="Export current grid to zip"
            >
              <v-icon size="20">mdi-download</v-icon>
            </button>
          </template>
          <TbExportPanel @confirm-export="emit('confirm-export-zip')" />
        </v-menu>
        <!-- ── Toolbar: Import ───────────────────────────────────────── -->
        <button
          class="bar-btn bar-btn--icon"
          type="button"
          :disabled="isReadOnly"
          title="Import photos"
          @click="emit('open-import')"
        >
          <v-icon size="20">mdi-cloud-upload-outline</v-icon>
        </button>
        <!-- ── Toolbar: ComfyUI T2I ──────────────────────────────────── -->
        <v-menu
          v-if="filterStore.comfyuiConfigured"
          v-model="tbComfyuiMenuOpen"
          :close-on-content-click="false"
          location="bottom start"
          origin="top start"
          transition="scale-transition"
        >
          <template #activator="{ props: menuProps }">
            <button
              v-bind="menuProps"
              class="bar-btn bar-btn--icon"
              :class="{ 'bar-btn--active': tbComfyuiMenuOpen }"
              type="button"
              :disabled="isReadOnly"
              title="Generate new image with ComfyUI from a text prompt"
            >
              <v-icon size="20">mdi-image-plus</v-icon>
            </button>
          </template>
          <TbComfyPanel
            :backend-url="props.backendUrl"
            :open="tbComfyuiMenuOpen"
            @run-grid="
              emit('comfyui-run-grid', $event);
              tbComfyuiMenuOpen = false;
            "
          />
        </v-menu>
      </div>
      <!-- ── Conditional separator: ComfyUI | ApplyTo (shown when bar is narrow) ── -->
      <div class="bar-separator bar-separator--gap-guard"></div>
      <div class="selection-bar-right">
        <!-- ── Separator: grid controls | Settings ────────────────────── -->
        <div class="bar-separator"></div>
        <!-- ── Toolbar: Settings ─────────────────────────────────────── -->
        <button
          class="bar-btn bar-btn--icon"
          type="button"
          title="Settings"
          @click="emit('open-settings')"
        >
          <v-icon size="20">mdi-cog-outline</v-icon>
        </button>
        <!-- ── Toolbar: Stats toggle ──── -->
        <button
          class="bar-btn bar-btn--icon"
          :class="{ 'bar-btn--active': sidebarStore.statsOpen }"
          type="button"
          :title="
            sidebarStore.statsOpen ? 'Hide stats sidebar' : 'Show stats sidebar'
          "
          @click="sidebarStore.toggleStats()"
        >
          <v-icon size="20">mdi-chart-bar</v-icon>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import { isReadOnly } from "../../utils/apiClient";
import { useFilterStore } from "../../stores/useFilterStore";
import { useSortStore } from "../../stores/useSortStore";
import { useSelectionStore } from "../../stores/useSelectionStore";
import { useGridStore } from "../../stores/useGridStore";
import { useExportStore } from "../../stores/useExportStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useSearchStore } from "../../stores/useSearchStore";
import { useReviewFixesStore } from "../../stores/useReviewFixesStore";
import GbFilterPanel from "./GbFilterPanel.vue";
import TbComfyPanel from "./TbComfyPanel.vue";
import TbExportPanel from "./TbExportPanel.vue";
const props = defineProps({
  selectedCount: Number,
  selectedCharacter: String,
  selectedSort: { type: String, default: "" },
  allPicturesId: { type: String, required: true },
  unassignedPicturesId: { type: String, required: true },
  backendUrl: { type: String, required: true },
  comfyuiConfigured: { type: Boolean, default: false },
});

const emit = defineEmits([
  "comfyui-run-grid",
  "expand-all-stacks",
  "collapse-all-stacks",
  "confirm-export-zip",
  "open-import",
  "open-settings",
]);

const LIKENESS_GROUPS_SORT_KEY = "LIKENESS_GROUPS";
const SCRAPHEAP_PICTURES_ID = "SCRAPHEAP";

const filteredSortOptions = computed(() => {
  const options = sortStore.sortOptions ?? [];
  if (selectionStore.selectedCharacter === SCRAPHEAP_PICTURES_ID) {
    return options.filter((opt) => opt.value !== LIKENESS_GROUPS_SORT_KEY);
  }
  return options;
});

// ═══════════════════════════════════════════════════════════════════════════════
// Pinia stores (replaces gridBarState and toolbarState provide/inject)
// ═══════════════════════════════════════════════════════════════════════════════
const filterStore = useFilterStore();
const sortStore = useSortStore();
const selectionStore = useSelectionStore();
const gridStore = useGridStore();
const exportStore = useExportStore();
const sidebarStore = useSidebarStore();
const searchStore = useSearchStore();
const reviewFixesStore = useReviewFixesStore();

const tbComfyuiMenuOpen = ref(false);
// ── Grid Bar: Sort ─────────────────────────────────────────────────────────────
const SIMILARITY_SORT_KEY_GB = "CHARACTER_LIKENESS";
const LIKENESS_GROUPS_SORT_KEY_GB = "LIKENESS_GROUPS";
const gbSortMenuOpen = ref(false);
const gbPendingSortSelection = ref(null);

const gbSortModel = computed({
  get: () => sortStore.selectedSort ?? "",
  set: (value) => {
    sortStore.selectedSort = value != null ? String(value) : "";
  },
});

const gbDescendingModel = computed({
  get: () => sortStore.selectedDescending ?? true,
  set: (value) => {
    sortStore.selectedDescending = Boolean(value);
  },
});

const gbSortMenuModel = computed(
  () => gbPendingSortSelection.value ?? gbSortModel.value,
);

const gbPendingSortKey = computed(() =>
  String(gbSortMenuModel.value || "").toUpperCase(),
);
const gbCommittedSortKey = computed(() =>
  String(gbSortModel.value || "").toUpperCase(),
);

const gbIsPendingParameterSortCommit = computed(
  () =>
    gbSortRequiresParameter(gbPendingSortKey.value) &&
    gbPendingSortKey.value !== gbCommittedSortKey.value,
);
const gbIsPendingSimilarityParameter = computed(
  () =>
    gbIsPendingParameterSortCommit.value &&
    gbPendingSortKey.value === SIMILARITY_SORT_KEY_GB,
);
const gbIsPendingStackParameter = computed(
  () =>
    gbIsPendingParameterSortCommit.value &&
    gbPendingSortKey.value === LIKENESS_GROUPS_SORT_KEY_GB,
);

watch(gbSortMenuOpen, (isOpen) => {
  if (isOpen) gbPendingSortSelection.value = gbSortModel.value;
  else gbPendingSortSelection.value = null;
});

const gbHasSimilarityOptions = computed(
  () =>
    Array.isArray(sortStore.similarityCharacterOptions) &&
    sortStore.similarityCharacterOptions.length > 0,
);

const gbSimilarityCharacterModel = computed({
  get: () => sortStore.selectedSimilarityCharacter ?? null,
  set: (value) => {
    sortStore.selectedSimilarityCharacter = value ?? null;
  },
});

const gbStackThresholdOptions = [
  { label: "Very Loose", value: "0.92" },
  { label: "Loose", value: "0.95" },
  { label: "Medium", value: "0.97" },
  { label: "Strict", value: "0.99" },
  { label: "Very Strict", value: "0.995" },
];

const gbStackThresholdModel = computed({
  get: () => {
    const v = sortStore.stackThreshold;
    if (v == null || v === "") return "0.92";
    const parsed = parseFloat(String(v));
    if (!Number.isFinite(parsed) || parsed <= 0) return "0.92";
    return String(v);
  },
  set: (value) => {
    sortStore.stackThreshold = value;
  },
});

const GB_SORT_ICON_MAP = {
  DATE: "mdi-calendar",
  IMPORTED_AT: "mdi-calendar-import",
  SMART_SCORE: "mdi-brain",
  SCORE: "mdi-star",
  NAME: "mdi-sort-alphabetical",
  IMAGE_SIZE: "mdi-image-size-select-large",
  RANDOM: "mdi-shuffle",
  TEXT_CONTENT: "mdi-text-recognition",
  CHARACTER_LIKENESS: "mdi-account-search",
  LIKENESS_GROUPS: "mdi-layers",
};

function gbGetSortIcon(value) {
  if (!value) return "mdi-sort";
  return GB_SORT_ICON_MAP[String(value).toUpperCase()] || "mdi-sort";
}

function gbSortRequiresParameter(sortValue) {
  const key = String(sortValue || "").toUpperCase();
  return key === SIMILARITY_SORT_KEY_GB || key === LIKENESS_GROUPS_SORT_KEY_GB;
}

function gbCommitSortSelection(sortValue) {
  gbSortModel.value = sortValue != null ? String(sortValue) : "";
}

function gbHandleSortModelUpdate(sortValue) {
  if (searchStore.searchQuery && searchStore.searchQuery.trim()) return;
  gbPendingSortSelection.value = sortValue != null ? String(sortValue) : "";
  if (!gbSortRequiresParameter(gbPendingSortSelection.value)) {
    gbCommitSortSelection(gbPendingSortSelection.value);
    gbSortMenuOpen.value = false;
  }
}

function gbHandleSimilarityOptionClick(selectedValue) {
  if (
    String(gbSortMenuModel.value || "").toUpperCase() === SIMILARITY_SORT_KEY_GB
  ) {
    if (selectedValue != null) {
      sortStore.selectedSimilarityCharacter = selectedValue;
    }
    gbCommitSortSelection(SIMILARITY_SORT_KEY_GB);
    gbSortMenuOpen.value = false;
  }
}

function gbHandleStackThresholdOptionClick() {
  if (
    String(gbSortMenuModel.value || "").toUpperCase() ===
    LIKENESS_GROUPS_SORT_KEY_GB
  ) {
    gbCommitSortSelection(LIKENESS_GROUPS_SORT_KEY_GB);
    gbSortMenuOpen.value = false;
  }
}

function gbToggleSortDirection() {
  gbDescendingModel.value = !gbDescendingModel.value;
}

const gbSelectedSortOption = computed(() =>
  filteredSortOptions.value.find((opt) => opt.value === gbSortModel.value),
);
const gbSelectedSimilarityOption = computed(() =>
  (sortStore.similarityCharacterOptions ?? []).find(
    (opt) => opt.value === gbSimilarityCharacterModel.value,
  ),
);
const gbSelectedStackThresholdOption = computed(() =>
  gbStackThresholdOptions.find(
    (opt) => opt.value === gbStackThresholdModel.value,
  ),
);

const gbSortButtonLabel = computed(() => {
  if (searchStore.searchQuery && searchStore.searchQuery.trim())
    return "Search relevance";
  if (gbSortModel.value === SIMILARITY_SORT_KEY_GB)
    return gbSelectedSimilarityOption.value?.text
      ? `Similarity: ${gbSelectedSimilarityOption.value.text}`
      : "Similarity";
  if (gbSortModel.value === LIKENESS_GROUPS_SORT_KEY_GB)
    return gbSelectedStackThresholdOption.value?.label
      ? `Groups: ${gbSelectedStackThresholdOption.value.label}`
      : "Groups";
  return gbSelectedSortOption.value?.label || "Sort";
});

const gbSortTypeName = computed(() => {
  if (searchStore.searchQuery && searchStore.searchQuery.trim())
    return "Search relevance";
  if (gbSortModel.value === SIMILARITY_SORT_KEY_GB) return "Similarity";
  if (gbSortModel.value === LIKENESS_GROUPS_SORT_KEY_GB) return "Groups";
  return gbSelectedSortOption.value?.label || "Sort";
});

const gbSortSecondaryLabel = computed(() => {
  if (gbSortModel.value === SIMILARITY_SORT_KEY_GB)
    return gbSelectedSimilarityOption.value?.text || null;
  if (gbSortModel.value === LIKENESS_GROUPS_SORT_KEY_GB)
    return gbSelectedStackThresholdOption.value?.label || null;
  return null;
});

const gbSortButtonIcon = computed(() =>
  gbDescendingModel.value ? "mdi-sort-descending" : "mdi-sort-ascending",
);

const gbSortTypeIcon = computed(() => {
  if (searchStore.searchQuery && searchStore.searchQuery.trim())
    return "mdi-magnify";
  return gbGetSortIcon(gbSortModel.value);
});

// ── Grid Bar: Filter ───────────────────────────────────────────────────────────
const gbFilterMenuOpen = ref(false);

// ── Grid Bar: View ─────────────────────────────────────────────────────────────
const gbViewMenuOpen = ref(false);
const gbPendingColumns = ref(gridStore.columns ?? 4);

watch(
  () => gridStore.columns,
  (v) => {
    if (!gbViewMenuOpen.value) gbPendingColumns.value = v ?? 4;
  },
);

watch(gbViewMenuOpen, (isOpen) => {
  if (isOpen) gbPendingColumns.value = gridStore.columns ?? 4;
});

function gbCommitColumns() {
  gridStore.columns = gbPendingColumns.value;
}

const gbCompactModeModel = computed({
  get: () => gridStore.compactMode,
  set: (v) => {
    gridStore.compactMode = Boolean(v);
  },
});
const gbShowFaceBboxesModel = computed({
  get: () => gridStore.showFaceBboxes,
  set: (v) => {
    gridStore.showFaceBboxes = Boolean(v);
  },
});
const gbShowProblemIconModel = computed({
  get: () => gridStore.showProblemIcon,
  set: (v) => {
    gridStore.showProblemIcon = Boolean(v);
  },
});

const gbOverlayOptions = computed(() => [
  {
    key: "faces",
    label: "Face boxes",
    icon: "mdi-face-recognition",
    model: {
      get value() {
        return gbShowFaceBboxesModel.value;
      },
      set value(v) {
        gbShowFaceBboxesModel.value = v;
      },
    },
  },
  {
    key: "problem",
    label: "Problems",
    icon: "mdi-alert",
    model: {
      get value() {
        return gbShowProblemIconModel.value;
      },
      set value(v) {
        gbShowProblemIconModel.value = v;
      },
    },
  },
]);

const gbExpandAllStacksDisabled = computed(() => {
  const total = Number(gridStore.totalStackCount || 0);
  const expanded = Number(gridStore.expandedStackCount || 0);
  return total <= 0 || expanded >= total;
});

const gbCollapseAllStacksDisabled = computed(
  () => Number(gridStore.expandedStackCount || 0) <= 0,
);
</script>

<style scoped>
.selection-bar-overlay {
  position: absolute !important;
  left: 0;
  top: 0;
  width: 100%;
  z-index: 100;
  /* Paint from the `toolbar` token (not `background`) so the toolbar strip can be
     tuned independently of the grid canvas. Set `toolbar` == `background` in the
     theme to keep them identical. */
  background: rgba(var(--v-theme-toolbar), 0.95);
  padding: 0 10px;
  margin: 0;
  height: 48px;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  container-type: inline-size;
  container-name: selbar;
}
.selection-bar-content {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  position: relative;
}
.selection-bar-left {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.selection-bar-right {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
  flex-shrink: 0;
}

/* ═══════════════════════════════════════════════════════════════════════════
   Grid Bar – Sort / Filter / View buttons and panels
   ═══════════════════════════════════════════════════════════════════════════ */

/* ── Bar buttons ──────────────────────────────────────────────────────────── */
.bar-split-button {
  display: flex;
  align-items: center;
  background: transparent;
  border: none;
}

.bar-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 0 9px;
  cursor: pointer;
  font-size: 0.88em;
  font-family: inherit;
  color: rgb(var(--v-theme-on-background));
  background: transparent;
  border: none;
  height: 40px;
  white-space: nowrap;
  position: relative;
}

.bar-btn:hover {
  background: rgba(var(--v-theme-on-background), 0.1);
}

.bar-btn--active {
  color: rgb(var(--v-theme-primary));
}

.bar-btn--boxed {
  border-radius: 4px;
}

/* Icon-only bar button */
.bar-btn--icon {
  width: 40px;
  height: 40px;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.bar-btn-label {
  max-width: 130px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 0.92em;
}

.bar-btn-prefix {
  font-size: 0.82em;
  opacity: 0.55;
  white-space: nowrap;
  flex-shrink: 0;
}

.bar-split-toggle {
  border-radius: 5px 0 0 5px;
  padding-right: 5px;
  border: none;
}

.bar-split-menu {
  border-radius: 0 5px 5px 0;
  padding-left: 6px;
  padding-right: 6px;
}

.bar-filter-badge {
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
  border-radius: 8px;
  font-size: 0.65em;
  font-weight: 700;
  padding: 0 4px;
  min-width: 14px;
  text-align: center;
  line-height: 14px;
  flex-shrink: 0;
}

.bar-separator {
  width: 1px;
  height: 24px;
  background: rgba(var(--v-theme-on-background), 0.2);
  margin: 0 4px;
  align-self: center;
  flex-shrink: 0;
}

/* Gap-guard: hidden by default, only shown via container query when the bar
   is narrow enough that the left and right groups start crowding each other */
.bar-separator--gap-guard {
  display: none;
}

@container selbar (max-width: 800px) {
  .bar-separator--gap-guard {
    display: block;
  }
}

/* ── Sort panel ───────────────────────────────────────────────────────────── */
.gb-sort-panel {
  min-width: 340px;
  max-width: 400px;
  padding: 8px 8px 10px;
}
.gb-sort-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}

.gb-sort-panel-title {
  font-size: 1em;
  font-weight: 500;
  letter-spacing: 0.02em;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.gb-sort-panel-title span {
  font-size: 0.78em;
  font-weight: 400;
  opacity: 0.6;
}

.gb-sort-direction {
  font-size: 0.82em;
  min-width: 0;
}

.gb-sort-search-note {
  font-size: 0.8em;
  opacity: 0.7;
  padding: 4px 4px 8px;
}

.gb-sort-grid {
  display: flex !important;
  flex-wrap: wrap;
  gap: 2px;
  width: 100%;
  height: auto !important;
  background: transparent !important;
}

.gb-sort-grid--pending-parameter .v-btn {
  opacity: 0.5;
}

.gb-sort-grid-btn {
  flex: 1 1 calc(50% - 2px);
  min-width: 100px;
  max-width: none;
  height: 32px !important;
  justify-content: flex-start !important;
  padding: 0 8px !important;
  font-size: 0.82em;
  color: rgb(var(--v-theme-on-background)) !important;
  opacity: 1 !important;
}

.gb-sort-grid-btn.v-btn--active {
  color: rgb(var(--v-theme-on-primary)) !important;
  background: rgb(var(--v-theme-primary)) !important;
}

.gb-sort-grid-label {
  flex: 1;
  text-align: left;
  font-size: 0.9em;
  margin-left: 4px;
}

.gb-sort-grid-selected {
  color: rgb(var(--v-theme-on-primary)) !important;
  margin-left: auto;
}

.gb-sort-grid-selected--pending {
  opacity: 0.4;
}

.gb-sort-similarity-row {
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 0 2px;
}

.gb-sort-similarity-row > span {
  font-size: 0.85em;
  font-weight: 600;
  opacity: 0.75;
}

.gb-similarity-scroll {
  overflow-x: hidden;
  width: 100%;
}

.gb-similarity-thumb {
  width: 28px;
  height: 28px;
  object-fit: cover;
  border-radius: 50%;
  flex-shrink: 0;
}

.gb-similarity-thumb--placeholder {
  background: rgba(var(--v-theme-on-background), 0.15);
}

/* ── View panel ───────────────────────────────────────────────────────────── */
.gb-view-panel {
  align-items: flex-start;
  gap: 6px;
  padding: 8px 12px 12px;
  min-width: 220px;
}

.gb-view-switch {
  width: 100%;
}

.gb-columns-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}

.gb-columns-label {
  font-size: 0.82em;
  white-space: nowrap;
  flex-shrink: 0;
  min-width: 76px;
}

.gb-columns-slider {
  flex: 1;
  min-width: 0;
  margin-bottom: 0;
}

.gb-stacks-controls {
  width: 100%;
}

.gb-stacks-buttons {
  display: flex;
  gap: 6px;
}

.gb-stack-toggle-btn {
  flex: 1;
}

.gb-overlay-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 4px;
  width: 100%;
}

.gb-overlay-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
  padding: 5px 4px;
  border-radius: 5px;
  border: 1px solid rgba(var(--v-theme-on-background), 0.14);
  background: rgba(var(--v-theme-on-background), 0.04);
  color: rgb(var(--v-theme-on-background));
  cursor: pointer;
  font-size: 0;
  opacity: 0.5;
  transition:
    opacity 0.12s,
    background 0.12s;
}

.gb-overlay-btn:hover {
  opacity: 0.8;
  background: rgba(var(--v-theme-on-background), 0.09);
}

.gb-overlay-btn--active {
  opacity: 1;
  color: rgb(var(--v-theme-on-primary));
  border-color: rgb(var(--v-theme-primary));
  background: rgb(var(--v-theme-primary));
}

.gb-overlay-btn-label {
  font-size: 0.72em;
  white-space: nowrap;
  line-height: 1.2;
  color: inherit;
}

@media (hover: none) and (pointer: coarse) {
  .selection-bar-overlay {
    height: 56px;
    padding: 0 4px;
  }

  .bar-btn,
  .bar-split-menu,
  .clear-btn,
  .delete-btn,
  .stack-btn {
    min-height: 46px;
  }

  .bar-btn--icon {
    width: 46px;
    height: 46px;
  }

  .bar-split-toggle,
  .bar-separator,
  .tb-export-btn,
  .bar-btn-chevron {
    display: none;
  }

  .bar-split-menu {
    border-left: none;
    border-radius: 5px;
  }
}

.bar-btn-sort-type {
  max-width: 100px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 0.92em;
  flex-shrink: 1;
}

.bar-btn-sort-secondary {
  max-width: 100px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 0.92em;
  flex-shrink: 1;
}

/* ── Responsive: progressive label dropping via container queries ─────────── */
@container selbar (max-width: 960px) {
  .bar-btn-prefix,
  .bar-btn-sort-type {
    display: none;
  }
}

@container selbar (max-width: 840px) {
  .bar-btn-label--filter {
    display: none;
  }
}

@container selbar (max-width: 740px) {
  .bar-btn-label--view {
    display: none;
  }
}

@container selbar (max-width: 580px) {
  .visible-range-pill {
    display: none;
  }
}
</style>
