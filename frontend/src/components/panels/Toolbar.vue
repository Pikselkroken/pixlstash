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
          :offset="8"
          transition="scale-transition"
        >
          <template #activator="{ props: menuProps }">
            <div
              class="bar-split-button"
              :class="{ 'bar-split-button--open': gbSortMenuOpen }"
            >
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
          <div class="tbm gb-sort-panel">
            <span class="tbm-caret tbm-caret--start"></span>
            <div class="tbm-header">
              <v-icon size="18" class="tbm-header-icon">{{
                gbSortTypeIcon
              }}</v-icon>
              <span class="tbm-title">Sort order</span>
              <span class="tbm-spacer"></span>
              <button
                class="tbm-ghost"
                type="button"
                :disabled="
                  gbSearchActive || gbSortModel === LIKENESS_GROUPS_SORT_KEY_GB
                "
                @click="gbToggleSortDirection"
              >
                <v-icon size="16">{{
                  gbDescendingModel
                    ? "mdi-sort-descending"
                    : "mdi-sort-ascending"
                }}</v-icon>
                <span>{{
                  gbDescendingModel ? "Descending" : "Ascending"
                }}</span>
              </button>
            </div>

            <div v-if="gbSearchActive" class="tbm-section gb-sort-search-note">
              Search relevance (fixed)
            </div>

            <div class="tbm-section">
              <div class="tbm-grid-2">
                <button
                  v-for="opt in filteredSortOptions"
                  :key="opt.value"
                  class="tbm-toggle"
                  :class="{ 'tbm-toggle--on': gbSortMenuModel === opt.value }"
                  type="button"
                  :disabled="gbSearchActive"
                  @click="gbHandleSortModelUpdate(opt.value)"
                >
                  <v-icon size="18" class="tbm-toggle-icon">{{
                    gbGetSortIcon(opt.value)
                  }}</v-icon>
                  <span class="tbm-toggle-label">{{ opt.label }}</span>
                  <span
                    v-if="
                      gbSortMenuModel === opt.value &&
                      (opt.value === SIMILARITY_SORT_KEY_GB ||
                        opt.value === LIKENESS_GROUPS_SORT_KEY_GB)
                    "
                    class="tbm-toggle-end"
                  >
                    <v-icon size="16">mdi-circle-medium</v-icon>
                  </span>
                </button>
              </div>
            </div>

            <div
              v-if="gbSortMenuModel === SIMILARITY_SORT_KEY_GB"
              class="tbm-section"
            >
              <span class="tbm-label">Similarity to …</span>
              <div
                class="gb-sim-grid"
                :class="{
                  'tbm-toggle--pending': gbIsPendingSimilarityParameter,
                }"
              >
                <button
                  v-for="opt in sortStore.similarityCharacterOptions ?? []"
                  :key="opt.value"
                  class="gb-sim-btn"
                  :class="{
                    'gb-sim-btn--on': gbSimilarityCharacterModel === opt.value,
                  }"
                  type="button"
                  :disabled="!gbHasSimilarityOptions"
                  :title="opt.text"
                  @click="gbHandleSimilarityOptionClick(opt.value)"
                >
                  <img
                    v-if="opt.thumbnail"
                    :src="opt.thumbnail"
                    class="gb-sim-avatar"
                    alt=""
                  />
                  <span
                    v-else
                    class="gb-sim-avatar gb-sim-avatar--placeholder"
                  ></span>
                  <span class="gb-sim-name">{{ opt.text }}</span>
                </button>
              </div>
            </div>

            <div
              v-if="gbSortMenuModel === LIKENESS_GROUPS_SORT_KEY_GB"
              class="tbm-section"
            >
              <span class="tbm-label">Group strictness</span>
              <div
                class="tbm-grid-2"
                :class="{ 'tbm-toggle--pending': gbIsPendingStackParameter }"
              >
                <button
                  v-for="opt in gbStackThresholdOptions"
                  :key="opt.value"
                  class="tbm-toggle"
                  :class="{
                    'tbm-toggle--on': gbStackThresholdModel === opt.value,
                  }"
                  type="button"
                  @click="
                    gbStackThresholdModel = opt.value;
                    gbHandleStackThresholdOptionClick(opt.value);
                  "
                >
                  <span class="tbm-toggle-label">{{ opt.label }}</span>
                </button>
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
          :offset="8"
          transition="scale-transition"
        >
          <template #activator="{ props: menuProps }">
            <button
              v-bind="menuProps"
              class="bar-btn bar-btn--boxed"
              :class="{
                'bar-btn--active': filterStore.isActive && !gbFilterMenuOpen,
                'bar-btn--open': gbFilterMenuOpen,
              }"
              type="button"
              title="Filters"
            >
              <span class="bar-icon-badge-wrap">
                <v-icon size="19">mdi-filter</v-icon>
                <span
                  v-if="filterStore.activeCount > 0"
                  class="bar-filter-badge"
                  >{{
                    filterStore.activeCount > 99
                      ? "99+"
                      : filterStore.activeCount
                  }}</span
                >
              </span>
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
          :offset="8"
          transition="scale-transition"
        >
          <template #activator="{ props: menuProps }">
            <button
              v-bind="menuProps"
              class="bar-btn bar-btn--boxed"
              :class="{ 'bar-btn--open': gbViewMenuOpen }"
              type="button"
              title="View options"
            >
              <v-icon size="19">mdi-view-grid</v-icon>
              <v-icon size="18" class="bar-btn-chevron">mdi-menu-down</v-icon>
            </button>
          </template>
          <div class="tbm gb-view-panel">
            <span class="tbm-caret tbm-caret--end"></span>
            <div class="tbm-header">
              <v-icon size="18" class="tbm-header-icon">mdi-view-grid</v-icon>
              <span class="tbm-title">Grid view</span>
              <span class="tbm-spacer"></span>
              <button
                class="tbm-btn tbm-btn--compact"
                :class="{ 'tbm-btn--on': gbCompactModeModel }"
                type="button"
                @click="gbCompactModeModel = !gbCompactModeModel"
              >
                <v-icon size="16">mdi-view-compact-outline</v-icon>
                <span>Compact</span>
              </button>
            </div>

            <div class="tbm-section gb-columns-row">
              <span class="gb-columns-label"
                >Columns: <b class="tbm-mono">{{ gbPendingColumns }}</b></span
              >
              <v-slider
                class="gb-columns-slider"
                v-model="gbPendingColumns"
                :min="gridStore.minColumns ?? 2"
                :max="gridStore.maxColumns ?? 14"
                :step="1"
                density="compact"
                hide-details
                color="primary"
                thumb-color="primary"
                @end="gbCommitColumns"
              />
            </div>

            <div class="tbm-section">
              <span class="tbm-label">Stacks</span>
              <div class="tbm-btngroup">
                <button
                  class="tbm-action tbm-action--secondary"
                  type="button"
                  style="flex: 1"
                  :disabled="gbExpandAllStacksDisabled"
                  @click="emit('expand-all-stacks')"
                >
                  <v-icon size="16">mdi-arrow-expand-vertical</v-icon>
                  Expand all
                </button>
                <button
                  class="tbm-action tbm-action--secondary"
                  type="button"
                  style="flex: 1"
                  :disabled="gbCollapseAllStacksDisabled"
                  @click="emit('collapse-all-stacks')"
                >
                  <v-icon size="16">mdi-arrow-collapse-vertical</v-icon>
                  Collapse all
                </button>
              </div>
            </div>

            <div class="tbm-section">
              <span class="tbm-label">Overlays</span>
              <div class="tbm-grid-3">
                <button
                  v-for="ovl in gbOverlayOptions"
                  :key="ovl.key"
                  class="tbm-toggle tbm-toggle--vertical"
                  :class="{ 'tbm-toggle--on': ovl.model.value }"
                  :title="ovl.label"
                  type="button"
                  @click="ovl.model.value = !ovl.model.value"
                >
                  <v-icon size="18" class="tbm-toggle-icon">{{
                    ovl.icon
                  }}</v-icon>
                  <span class="tbm-toggle-label">{{ ovl.label }}</span>
                </button>
              </div>
            </div>
          </div>
        </v-menu>
        <!-- ── Toolbar: divider ─────────────────────────────────────── -->
        <div class="bar-separator"></div>
        <!-- ── Toolbar: Search (icon trigger → search menu popover) ───── -->
        <v-menu
          v-model="gbSearchMenuOpen"
          :close-on-content-click="false"
          location="bottom end"
          origin="top end"
          :offset="8"
          transition="scale-transition"
        >
          <template #activator="{ props: menuProps }">
            <button
              v-bind="menuProps"
              class="bar-btn bar-btn--icon"
              :class="{
                'bar-btn--active':
                  searchStore.isSearchActive && !gbSearchMenuOpen,
                'bar-btn--open': gbSearchMenuOpen,
              }"
              type="button"
              title="Search (F)"
            >
              <v-icon size="20">mdi-magnify</v-icon>
            </button>
          </template>
          <div class="tbm gb-search-panel">
            <span class="tbm-caret tbm-caret--end"></span>
            <div class="tbm-header">
              <v-icon size="18" class="tbm-header-icon">mdi-magnify</v-icon>
              <span class="tbm-title">Search</span>
            </div>
            <div class="gb-search-field">
              <div class="tbm-input-wrap">
                <v-icon size="16" class="tbm-input-icon">mdi-magnify</v-icon>
                <input
                  ref="searchInputRef"
                  v-model="searchStore.searchInput"
                  class="tbm-input tbm-input--with-icon"
                  type="text"
                  placeholder="Search your library…"
                  autocomplete="off"
                  @keydown.enter.prevent="onSearchEnter"
                  @keydown.escape.prevent="onSearchEscape"
                />
              </div>
            </div>
            <div
              v-if="gbSearchHistoryItems.length"
              class="tbm-section gb-search-recent"
            >
              <span class="tbm-label">Recent</span>
              <div class="gb-recent-list">
                <button
                  v-for="item in gbSearchHistoryItems"
                  :key="item"
                  class="gb-recent-row"
                  type="button"
                  @click="gbApplySearchHistory(item)"
                >
                  <v-icon size="16" class="gb-recent-icon">mdi-history</v-icon>
                  <span class="gb-recent-label">{{ item }}</span>
                  <v-icon size="14" class="gb-recent-apply"
                    >mdi-arrow-top-left</v-icon
                  >
                </button>
              </div>
            </div>
          </div>
        </v-menu>
        <!-- ── Toolbar: Export ───────────────────────────────────────── -->
        <v-menu
          v-model="exportStore.exportMenuOpen"
          :close-on-content-click="false"
          location="bottom end"
          origin="top end"
          :offset="8"
          transition="scale-transition"
        >
          <template #activator="{ props: menuProps }">
            <button
              v-bind="menuProps"
              class="bar-btn bar-btn--icon tb-export-btn"
              :class="{ 'bar-btn--open': exportStore.exportMenuOpen }"
              type="button"
              title="Export current grid to zip"
            >
              <v-icon size="20">mdi-tray-arrow-down</v-icon>
            </button>
          </template>
          <TbExportPanel @confirm-export="emit('confirm-export-zip')" />
        </v-menu>
        <!-- ── Toolbar: Import (icon trigger → import menu popover) ────── -->
        <v-menu
          v-if="!isReadOnly"
          v-model="tbImportMenuOpen"
          :close-on-content-click="false"
          location="bottom end"
          origin="top end"
          :offset="8"
          transition="scale-transition"
        >
          <template #activator="{ props: menuProps }">
            <button
              v-bind="menuProps"
              class="bar-btn bar-btn--icon"
              :class="{ 'bar-btn--open': tbImportMenuOpen }"
              type="button"
              title="Import photos"
            >
              <v-icon size="20">mdi-cloud-upload-outline</v-icon>
            </button>
          </template>
          <TbImportPanel
            :backend-url="props.backendUrl"
            :open="tbImportMenuOpen"
            :default-project-id="projectStore.selectedProjectId"
            @local-import="
              emit('local-import', $event);
              tbImportMenuOpen = false;
            "
            @open-full-import="
              emit('open-import');
              tbImportMenuOpen = false;
            "
          />
        </v-menu>
        <!-- ── Toolbar: ComfyUI T2I ──────────────────────────────────── -->
        <v-menu
          v-if="filterStore.comfyuiConfigured"
          v-model="tbComfyuiMenuOpen"
          :close-on-content-click="false"
          location="bottom end"
          origin="top end"
          :offset="8"
          transition="scale-transition"
        >
          <template #activator="{ props: menuProps }">
            <button
              v-bind="menuProps"
              class="bar-btn bar-btn--icon"
              :class="{ 'bar-btn--open': tbComfyuiMenuOpen }"
              type="button"
              :disabled="isReadOnly"
              title="Generate new image with ComfyUI from a text prompt"
            >
              <v-icon size="20">mdi-image-plus-outline</v-icon>
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
        <!-- ── Separator: grid controls | actions ─────────────────────── -->
        <div class="bar-separator"></div>
        <!-- ── Toolbar: Review and fix tags (an action, not a menu) ───── -->
        <button
          class="bar-btn bar-btn--icon"
          type="button"
          :disabled="isReadOnly"
          title="Review and fix tags"
          @click="reviewFixesStore.overlayOpen = true"
        >
          <v-icon size="20">mdi-tag-check-outline</v-icon>
        </button>
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
          class="bar-btn bar-btn--icon tb-stats-btn"
          :class="{ 'bar-btn--active': sidebarStore.statsOpen }"
          type="button"
          :title="
            tasksStore.hasActiveTasks
              ? `${tasksStore.activeCount} active task${tasksStore.activeCount === 1 ? '' : 's'} running`
              : sidebarStore.statsOpen
                ? 'Hide stats sidebar'
                : 'Show stats sidebar'
          "
          @click="sidebarStore.toggleStats()"
        >
          <v-icon size="20">mdi-chart-bar</v-icon>
          <!-- App-wide activity light: pulses whenever the task manager has any
               active work, so background tasks are visible without opening the
               stats sidebar. -->
          <span
            v-if="tasksStore.hasActiveTasks"
            class="tb-stats-activity"
          ></span>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from "vue";
import { isReadOnly } from "../../utils/apiClient";
import { useFilterStore } from "../../stores/useFilterStore";
import { useSortStore } from "../../stores/useSortStore";
import { useSelectionStore } from "../../stores/useSelectionStore";
import { useGridStore } from "../../stores/useGridStore";
import { useExportStore } from "../../stores/useExportStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useSearchStore } from "../../stores/useSearchStore";
import { useReviewFixesStore } from "../../stores/useReviewFixesStore";
import { useTasksStore } from "../../stores/useTasksStore";
import { useProjectStore } from "../../stores/useProjectStore";
import GbFilterPanel from "./GbFilterPanel.vue";
import TbComfyPanel from "./TbComfyPanel.vue";
import TbExportPanel from "./TbExportPanel.vue";
import TbImportPanel from "./TbImportPanel.vue";
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
  "local-import",
  "open-settings",
]);

const tbImportMenuOpen = ref(false);

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
const tasksStore = useTasksStore();
const projectStore = useProjectStore();

const tbComfyuiMenuOpen = ref(false);
// ── Grid Bar: Sort ─────────────────────────────────────────────────────────────
const SIMILARITY_SORT_KEY_GB = "CHARACTER_LIKENESS";
const LIKENESS_GROUPS_SORT_KEY_GB = "LIKENESS_GROUPS";
const gbSortMenuOpen = ref(false);
const gbPendingSortSelection = ref(null);

// True while a text search is active — sort is then locked to relevance.
const gbSearchActive = computed(() =>
  Boolean(searchStore.searchQuery && searchStore.searchQuery.trim()),
);

// ── Grid Bar: Search (icon trigger → search menu popover) ──────────────────────
const searchInputRef = ref(null);
const gbSearchMenuOpen = ref(false);
// Full history when the field is empty; prefix-filtered while typing.
const gbSearchHistoryItems = computed(() => searchStore.filteredSearchHistory);

function onSearchEnter() {
  searchStore.commitSearch();
  gbSearchMenuOpen.value = false;
}
function onSearchEscape() {
  gbSearchMenuOpen.value = false;
}
function gbApplySearchHistory(item) {
  searchStore.searchInput = item;
  searchStore.commitSearch();
  gbSearchMenuOpen.value = false;
}

// Focus the field whenever the menu opens (click or the "F" shortcut).
watch(gbSearchMenuOpen, (open) => {
  if (open) nextTick(() => searchInputRef.value?.focus());
});

// The global "F" shortcut opens the search menu (App bumps this token rather
// than holding a ref down through ImageGrid → Toolbar).
watch(
  () => searchStore.searchFocusToken,
  () => {
    gbSearchMenuOpen.value = true;
  },
);

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
const gbShowDetectionsModel = computed({
  get: () => gridStore.showDetections,
  set: (v) => {
    gridStore.showDetections = Boolean(v);
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
    key: "detections",
    label: "Object boxes",
    icon: "mdi-shape-outline",
    model: {
      get value() {
        return gbShowDetectionsModel.value;
      },
      set value(v) {
        gbShowDetectionsModel.value = v;
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
  padding: 0 var(--space-3);
  margin: 0;
  height: 36px;
  /* 1px divider along the bottom. box-sizing:border-box keeps the bar at 36px
     (the border sits inside), so this doesn't grow the toolbar. */
  border-bottom: 1px solid rgb(var(--v-theme-divider));
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
  gap: var(--space-3);
  flex-shrink: 0;
}
.selection-bar-right {
  display: flex;
  align-items: center;
  gap: var(--space-3);
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
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
}

/* Open state: the whole split button adopts the panel fill + border so it reads
   as one object with its menu (the caret bridges the gap). */
.bar-split-button--open {
  border-color: rgb(var(--v-theme-border));
  background: rgb(var(--v-theme-panel));
}

.bar-split-button--open .bar-split-toggle,
.bar-split-button--open .bar-split-menu {
  background: transparent;
}

.bar-split-button--open .bar-btn-chevron {
  transform: rotate(180deg);
  transition: transform var(--dur-1) var(--ease-standard);
}

/* ── Search menu (icon trigger → popover with input + recent searches) ─────── */
.gb-search-panel {
  width: 420px;
  max-width: 92vw;
}
.gb-search-field {
  /* Match the section side padding (12px) so the input lines up with the recent
     rows and with every other toolbar menu; top matches the menu headers (8px).
     The tight 4px bottom only works when a Recent section sits below and supplies
     its own top padding. */
  padding: var(--space-3) var(--space-4) var(--space-2);
}
.gb-search-field:last-child {
  /* No Recent section below: the field owns the panel's bottom edge, so match the
     section last-child rhythm (16px) instead of hugging the edge with 4px. */
  padding-bottom: var(--space-5);
}
.gb-search-recent {
  padding-top: var(--space-4);
}
/* Icon-only triggers (e.g. Search) flag their open state in accent, matching the
   design's IconTrigger; the labelled Sort/Filter/View triggers stay text-coloured. */
.bar-btn--icon.bar-btn--open {
  color: rgb(var(--v-theme-accent)) !important;
}
.gb-recent-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}
.gb-recent-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
  padding: var(--space-2) var(--space-3);
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: rgb(var(--v-theme-on-panel));
  font-family: var(--font-ui);
  font-size: var(--text-sm);
  text-align: left;
  cursor: pointer;
  transition: background var(--dur-1) var(--ease-standard);
}
.gb-recent-row:hover {
  background: var(--hover-wash);
}
.gb-recent-icon {
  color: rgba(var(--v-theme-on-panel), 0.5);
  flex-shrink: 0;
}
.gb-recent-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.gb-recent-apply {
  color: rgba(var(--v-theme-on-panel), 0.35);
  flex-shrink: 0;
}
.gb-recent-row:hover .gb-recent-apply {
  color: rgba(var(--v-theme-on-panel), 0.7);
}

.bar-btn {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: 0 var(--space-3);
  cursor: pointer;
  font-size: var(--text-base);
  font-family: inherit;
  /* Icons and labels take the sidebar's treatment: the toolbar-text token
     (identical to sidebar-text) at the sidebar's muted alpha, brightening on
     hover/active — so the toolbar and sidebar chrome read as one strip. */
  color: rgb(var(--v-theme-toolbar-text));
  background: transparent;
  /* A transparent 1px border is reserved so the open state (which colours the
     border) does not change the box size and make the button jump. */
  border: 1px solid transparent;
  box-sizing: border-box;
  height: 32px;
  white-space: nowrap;
  position: relative;
}

.bar-btn:hover {
  background: rgba(var(--v-theme-toolbar-text), 0.1);
  color: rgb(var(--v-theme-toolbar-text));
}

.bar-btn--active {
  color: rgb(var(--v-theme-primary));
}

.bar-btn--boxed {
  border-radius: var(--radius-sm);
}

/* Icon-only bar button */
.bar-btn--icon {
  width: 32px;
  height: 32px;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

/* App-wide task-activity light on the stats toggle. */
.tb-stats-btn {
  position: relative;
}

.tb-stats-activity {
  position: absolute;
  top: 7px;
  right: 7px;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: rgb(var(--v-theme-primary));
  box-shadow: 0 0 5px rgba(var(--v-theme-primary), 0.7);
  animation: tb-stats-pulse 1.4s ease-in-out infinite;
  pointer-events: none;
}

@keyframes tb-stats-pulse {
  0%,
  100% {
    opacity: 1;
    transform: scale(1);
  }
  50% {
    opacity: 0.4;
    transform: scale(0.7);
  }
}

@media (prefers-reduced-motion: reduce) {
  .tb-stats-activity {
    animation: none;
  }
}

.bar-btn-label {
  max-width: 130px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--text-base);
}

.bar-btn-prefix {
  font-size: var(--text-sm);
  opacity: 0.6;
  white-space: nowrap;
  flex-shrink: 0;
}

.bar-split-toggle {
  border-radius: var(--radius-sm) 0 0 var(--radius-sm);
  padding-right: var(--space-2);
}

.bar-split-menu {
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
  padding-left: var(--space-3);
  padding-right: var(--space-3);
}

/* Wrapper so the filter-count badge can overlay the icon without affecting
   layout (the badge is absolutely positioned within this). */
.bar-icon-badge-wrap {
  position: relative;
  display: inline-flex;
  align-items: center;
}

/* Small count badge, overlaid on the top-right of the filter icon. Absolutely
   positioned so it never changes the size of the button or shifts the chevron
   and the buttons that follow it. */
.bar-filter-badge {
  position: absolute;
  top: -5px;
  right: -7px;
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
  border-radius: var(--radius-pill);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  padding: 0 var(--space-2);
  min-width: 15px;
  height: 15px;
  text-align: center;
  line-height: 15px;
  pointer-events: none;
}

.bar-separator {
  width: 1px;
  height: 24px;
  background: rgba(var(--v-theme-on-background), 0.2);
  margin: 0 var(--space-2);
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
  width: 320px;
  max-width: 92vw;
}

.gb-sort-search-note {
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-panel), 0.7);
}

/* Similarity character picker — a 2-up grid of avatar rows. */
.gb-sim-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-1) var(--space-4);
  max-height: 320px;
  overflow-y: auto;
  /* Never scroll sideways — long names ellipsize instead (see .gb-sim-name). */
  overflow-x: hidden;
}

.gb-sim-btn {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
  /* Allow the grid item to shrink below its content so the name can ellipsize
     rather than forcing a horizontal scrollbar. */
  min-width: 0;
  padding: var(--space-2) var(--space-3);
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: rgb(var(--v-theme-on-panel));
  font-family: var(--font-ui);
  font-size: var(--text-sm);
  text-align: left;
  cursor: pointer;
  transition: background var(--dur-1) var(--ease-standard);
}

.gb-sim-btn:hover {
  background: var(--hover-wash);
}

.gb-sim-btn--on {
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
  font-weight: var(--weight-semibold);
}

.gb-sim-btn--on:hover {
  background: rgb(var(--v-theme-primary));
}

.gb-sim-avatar {
  width: 24px;
  height: 24px;
  object-fit: cover;
  border-radius: 50%;
  flex-shrink: 0;
}

.gb-sim-avatar--placeholder {
  background: rgba(var(--v-theme-on-panel), 0.15);
}

.gb-sim-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ── View panel ───────────────────────────────────────────────────────────── */
.gb-view-panel {
  width: 264px;
  max-width: 92vw;
}

.gb-columns-row {
  display: flex;
  align-items: center;
  gap: var(--space-4);
}

.gb-columns-label {
  font-size: var(--text-sm);
  white-space: nowrap;
  flex-shrink: 0;
}

.gb-columns-slider {
  flex: 1;
  min-width: 0;
  margin-bottom: 0;
}

@media (hover: none) and (pointer: coarse) {
  .selection-bar-overlay {
    height: 56px;
    padding: 0 var(--space-2);
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
    border-radius: var(--radius-sm);
  }
}

.bar-btn-sort-type {
  max-width: 100px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--text-base);
  flex-shrink: 1;
  /* The current selection reads at full strength against the muted "Sort:". */
  color: rgb(var(--v-theme-toolbar-text));
}

.bar-btn-sort-secondary {
  max-width: 100px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--text-base);
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
