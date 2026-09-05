<template>
  <div class="selection-bar-overlay">
    <div class="selection-bar-content">
      <div class="selection-bar-left">
        <button
          v-if="sidebarStore.sidebarForcedHidden"
          class="bar-btn bar-btn--icon tb-mobile-nav"
          type="button"
          :aria-expanded="sidebarStore.sidebarVisible"
          aria-label="Open library navigation"
          title="Open library navigation"
          @click="sidebarStore.revealSidebar()"
        >
          <v-icon size="20">mdi-menu</v-icon>
        </button>
        <div
          v-if="sidebarStore.sidebarForcedHidden"
          class="bar-separator tb-mobile-nav-separator"
        ></div>
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

            <!-- Shown once, in the slot the removed sort order used to occupy.
                 A user who reached for "Likeness Groups" here needs one line
                 telling them where the capability went, not a changelog. -->
            <div
              v-if="dedupMigrationNotice.visible.value"
              class="tbm-section gb-sort-migration-note"
              data-testid="sort-migration-notice"
            >
              <v-icon size="16">mdi-content-duplicate</v-icon>
              <span class="gb-sort-migration-text">
                Looking for Likeness Groups? Duplicates now has its own place in
                the sidebar, with a count and a one-key review queue.
              </span>
              <button
                type="button"
                class="gb-sort-migration-open"
                @click="openDuplicatesFromNotice"
              >
                Open Duplicates
              </button>
              <button
                type="button"
                class="gb-sort-migration-dismiss"
                aria-label="Hide this notice"
                @click="dedupMigrationNotice.dismiss()"
              >
                <v-icon size="16">mdi-close</v-icon>
              </button>
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
                    v-if="opt.value === STACK_UPDATED_AT_SORT_KEY"
                    class="tbm-toggle-filter-badge"
                    title="Only available when viewing stacks"
                    aria-label="Only available when viewing stacks"
                    role="img"
                  >
                    <v-icon size="14">mdi-filter-outline</v-icon>
                  </span>
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
        <!-- Undo/redo moved to the right-side app-wide tail (see below): the
             canonical [separator][UndoControl][TbGlobalActions] cluster is
             identical in every view that writes the operation log, so the
             position learned here holds in Duplicates too. (The model shelf
             writes none and carries no undo - amendment #4.) -->
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
              class="bar-btn bar-btn--boxed tb-fold-600"
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

            <div class="tbm-section gb-size-section">
              <div class="gb-size-controls">
                <span class="gb-columns-label">Size</span>
                <v-slider
                  class="gb-columns-slider"
                  v-model="gbPendingSize"
                  :min="0"
                  :max="gbMaxSizeLevel"
                  :step="1"
                  density="compact"
                  hide-details
                  color="primary"
                  thumb-color="primary"
                  @end="gbCommitSize"
                />
              </div>
              <span class="gb-size-value">{{ gbSizeLabel }}</span>
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
        <!-- ── Separator G-S1: the lens run | the action run ────────────
             A separator marks a SEMANTIC boundary, not a group edge (the
             amendments in docs/design/toolbar-responsive-decisions.md).
             Renders at ALL widths: with the ⋯ standing at the end of the
             action run it collapses (amendment #2), the run keeps two
             members ([Search][⋯]) down to the floor, so both flanks stay
             populated and the rule never sits boxed. -->
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
            <span class="tbm-caret tbm-caret--icon-center-end"></span>
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
              class="bar-btn bar-btn--icon tb-export-btn tb-fold-700"
              :class="{ 'bar-btn--open': exportStore.exportMenuOpen }"
              type="button"
              :title="exportActionLabel('Export current grid to zip')"
            >
              <v-icon size="20">mdi-tray-arrow-down</v-icon>
            </button>
          </template>
          <TbExportPanel
            @confirm-export="emit('confirm-export-zip')"
            @confirm-export-folder="emit('confirm-export-folder', $event)"
          />
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
              class="bar-btn bar-btn--icon tb-fold-700"
              :class="{ 'bar-btn--open': tbImportMenuOpen }"
              type="button"
              title="Import photos"
            >
              <v-icon size="20">mdi-cloud-upload-outline</v-icon>
            </button>
          </template>
          <TbImportPanel
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
              class="bar-btn bar-btn--icon tb-fold-700"
              :class="{ 'bar-btn--open': tbComfyuiMenuOpen }"
              type="button"
              :disabled="isReadOnly"
              title="Generate new image with ComfyUI from a text prompt"
            >
              <v-icon size="20">mdi-image-plus-outline</v-icon>
            </button>
          </template>
          <TbComfyPanel
            :open="tbComfyuiMenuOpen"
            @run-grid="
              emit('comfyui-run-grid', $event);
              tbComfyuiMenuOpen = false;
            "
          />
        </v-menu>
        <!-- ── The ⋯ overflow (amendment #2 in docs/design/
             toolbar-responsive-decisions.md): a burger may only collapse
             controls from its OWN visual group, and it stands where those
             controls stood - so it lives HERE, at the end of the action run
             it collapses (Export/Import/ComfyUI at ≤700, View at ≤600), and
             never a control from across a group boundary. Fold = CSS both
             ways: each row shares its bar button's v-if, and the container
             queries flip which of the pair is visible. The panel keeps its
             right-aligned anchoring, so it opens leftward and stays
             on-screen. -->
        <TbOverflowMenu class="tb-overflow">
          <template #default="{ close }">
            <button
              type="button"
              class="tbm-action tb-row-700"
              @click="
                emit('confirm-export-zip');
                close();
              "
            >
              <v-icon size="18">mdi-tray-arrow-down</v-icon>
              <span>{{ exportActionLabel("Export grid to zip") }}</span>
            </button>
            <button
              v-if="!isReadOnly"
              type="button"
              class="tbm-action tb-row-700"
              @click="
                emit('open-import');
                close();
              "
            >
              <v-icon size="18">mdi-cloud-upload-outline</v-icon>
              <span>Import photos…</span>
            </button>
            <button
              v-if="filterStore.comfyuiConfigured"
              type="button"
              class="tbm-action tb-row-700"
              :disabled="isReadOnly"
              @click="
                close();
                tbComfyuiMenuOpen = true;
              "
            >
              <v-icon size="18">mdi-image-plus-outline</v-icon>
              <span>Generate with ComfyUI…</span>
            </button>
            <button
              type="button"
              class="tbm-action tb-row-600"
              @click="
                close();
                gbViewMenuOpen = true;
              "
            >
              <v-icon size="18">mdi-view-grid</v-icon>
              <span>View options…</span>
            </button>
          </template>
        </TbOverflowMenu>
      </div>
      <!-- No separator between the groups: the elastic gap IS the left|right
           boundary (two-boundary rule, docs/design/toolbar-responsive-
           decisions.md amendment). The old leading rule here boxed the Review
           button, and its narrow-width gap-guard twin drew a double rule. -->
      <div class="selection-bar-right">
        <!-- ── Toolbar: Review and fix tags (an action, not a menu) ─────
             Visible at ALL widths (amendment #2): it is the review overlay's
             only visible entry point, and folding it into the left group's
             burger would cross the group boundary. -->
        <button
          class="bar-btn bar-btn--icon"
          type="button"
          :disabled="isReadOnly"
          title="Review and fix tags"
          @click="reviewSessionsStore.overlayOpen = true"
        >
          <v-icon size="20">mdi-tag-check-outline</v-icon>
        </button>
        <!-- ── Separator G-S4: view-local actions | app-wide chrome ─────
             The canonical toolbar tail, identical in every view that writes
             the operation log (not the model shelf, amendment #4):
             [separator] [UndoControl] [TbGlobalActions]. The rule is
             required AND stays at every width (it mirrors the Duplicates
             bar's D-S2): proximity alone cannot separate identical 32px
             icon buttons into "this view's tools" and "the app's chrome". -->
        <div class="bar-separator"></div>
        <!-- Mounted in a read-only session too, inert: the demo has to
             show that undo exists. UndoControl owns that state. -->
        <UndoControl />
        <!-- ── Toolbar: Settings + stats toggle (shared with the duplicates
             queue, which is why they live in their own component). Never
             folds (amendment #2); the activity dot stays first-class on the
             Stats button at every width. -->
        <TbGlobalActions @open-settings="emit('open-settings')" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from "vue";
import { API_BASE_URL, isReadOnly } from "../../utils/apiClient";
import { useFilterStore } from "../../stores/useFilterStore";
import { useSortStore } from "../../stores/useSortStore";
import { useGridStore } from "../../stores/useGridStore";
import { useExportStore } from "../../stores/useExportStore";
import { useSearchStore } from "../../stores/useSearchStore";
import { useReviewSessionsStore } from "../../stores/useReviewSessionsStore";
import { useProjectStore } from "../../stores/useProjectStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import {
  MAX_THUMBNAIL_SIZE_LEVEL,
  DEFAULT_THUMBNAIL_SIZE_LEVEL,
  sizeLabelForLevel,
} from "../../utils/thumbnailSizes";
import GbFilterPanel from "./GbFilterPanel.vue";
import TbGlobalActions from "./TbGlobalActions.vue";
import TbComfyPanel from "./TbComfyPanel.vue";
import TbExportPanel from "./TbExportPanel.vue";
import TbImportPanel from "./TbImportPanel.vue";
import TbOverflowMenu from "./TbOverflowMenu.vue";
import UndoControl from "./UndoControl.vue";
import { useOneTimeNotice } from "../../composables/useOneTimeNotice";
const props = defineProps({
  selectedCount: Number,
  selectedCharacter: String,
  selectedSort: { type: String, default: "" },
  allPicturesId: { type: String, required: true },
  backendUrl: { type: String, default: () => API_BASE_URL },
  comfyuiConfigured: { type: Boolean, default: false },
});

const emit = defineEmits([
  "comfyui-run-grid",
  "expand-all-stacks",
  "collapse-all-stacks",
  "confirm-export-zip",
  "confirm-export-folder",
  "open-import",
  "local-import",
  "open-settings",
  "open-duplicates",
]);

const tbImportMenuOpen = ref(false);

// Shown once per browser and then never again. A migration nudge that came back
// after a reload would be an ad, so the flag is persisted rather than held in
// the transient notice store.
const dedupMigrationNotice = useOneTimeNotice("dedup-sort-migration");

/** Dismiss the notice and take the user to where the capability moved. */
function openDuplicatesFromNotice() {
  dedupMigrationNotice.dismiss();
  gbSortMenuOpen.value = false;
  emit("open-duplicates");
}

const LIKENESS_GROUPS_SORT_KEY = "LIKENESS_GROUPS";
const STACK_UPDATED_AT_SORT_KEY = "STACK_UPDATED_AT";

// The Likeness Groups sort order is gone from the menu in 1.9. It was a lens,
// not a task: it never told you how many duplicates you had, offered no verdict,
// had no bulk action, and forgot everything the moment you changed sort. It also
// implied a whole-library comparison every time it was used. The signal survives
// in the Duplicates destination, which builds groups you can act on instead of
// shuffling the grid.
//
// The backend still serves the mechanism, so a deep link or a saved preference
// that names it keeps working; it simply has no menu row any more, and the
// migration notice below points at where it went.
// ═══════════════════════════════════════════════════════════════════════════════
// Pinia stores (replaces gridBarState and toolbarState provide/inject)
// ═══════════════════════════════════════════════════════════════════════════════
const filterStore = useFilterStore();
const sortStore = useSortStore();
const gridStore = useGridStore();
const exportStore = useExportStore();
const searchStore = useSearchStore();
const reviewSessionsStore = useReviewSessionsStore();
const projectStore = useProjectStore();
const sidebarStore = useSidebarStore();

// Stack time belongs to the stack-deck lens, not to loose pictures. Keep it
// visibly special while that lens is active and absent everywhere else. A
// persisted/deep-linked stack-time sort also falls back safely when the user
// leaves the stacked view, so a hidden menu choice can never remain active.
const filteredSortOptions = computed(() =>
  (sortStore.sortOptions ?? []).filter((opt) => {
    if (opt.value === LIKENESS_GROUPS_SORT_KEY) return false;
    if (opt.value === STACK_UPDATED_AT_SORT_KEY) {
      return filterStore.stackStateFilter === "stacked";
    }
    return true;
  }),
);

watch(
  [() => filterStore.stackStateFilter, () => sortStore.selectedSort],
  ([stackState, selectedSort]) => {
    if (
      stackState !== "stacked" &&
      String(selectedSort || "").toUpperCase() === STACK_UPDATED_AT_SORT_KEY
    ) {
      sortStore.selectedSort = "DATE";
    }
  },
  { immediate: true },
);

// ── Toolbar: Export ────────────────────────────────────────────────────────────
// The export follows the grid's own selection: with a subset selected,
// ImageGrid.exportCurrentViewToZip sends those ids and nothing else. Name that
// subset on the control, so the user knows what lands in the zip before opening
// the panel (whose count is only refreshed once the menu is already open).
// `idle` is the caller's whole-grid wording, which differs between the button's
// tooltip and the overflow row.
function exportActionLabel(idle) {
  const count = Number(props.selectedCount) || 0;
  if (count <= 0) return idle;
  return `Export ${count} picture${count === 1 ? "" : "s"} to zip`;
}

const tbComfyuiMenuOpen = ref(false);
// ── Grid Bar: Sort ─────────────────────────────────────────────────────────────
const SIMILARITY_SORT_KEY_GB = "CHARACTER_LIKENESS";
const LIKENESS_GROUPS_SORT_KEY_GB = "LIKENESS_GROUPS";
const gbSortMenuOpen = ref(false);
const gbPendingSortSelection = ref(null);

// True while a text search is active - sort is then locked to relevance.
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
  STACK_UPDATED_AT: "mdi-layers-edit",
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
const gbMaxSizeLevel = MAX_THUMBNAIL_SIZE_LEVEL;
const gbPendingSize = ref(gridStore.sizeLevel ?? DEFAULT_THUMBNAIL_SIZE_LEVEL);
const gbSizeLabel = computed(() => sizeLabelForLevel(gbPendingSize.value));

watch(
  () => gridStore.sizeLevel,
  (v) => {
    if (!gbViewMenuOpen.value)
      gbPendingSize.value = v ?? DEFAULT_THUMBNAIL_SIZE_LEVEL;
  },
);

watch(gbViewMenuOpen, (isOpen) => {
  if (isOpen)
    gbPendingSize.value = gridStore.sizeLevel ?? DEFAULT_THUMBNAIL_SIZE_LEVEL;
});

function gbCommitSize() {
  gridStore.sizeLevel = gbPendingSize.value;
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
    icon: "mdi-alert-outline",
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
     (the border sits inside), so this doesn't grow the toolbar.
     POINT OF TRUTH for the shell band's box recipe: `.dq-toolbar`
     (DuplicateQueue.vue) copies `height`/`box-sizing`/zero vertical padding
     from here so the two bars never step (guardrail in Toolbar.test.js).
     A change to this recipe is a change to both bars. */
  border-bottom: 1px solid rgb(var(--v-theme-divider));
  box-sizing: border-box;
  display: flex;
  align-items: center;
  container-type: inline-size;
  /* Two names on one container: `selbar` for this bar's own ladder, and the
     shared `toolbar` name that UndoControl and the overflow write their
     scoped @container rules against - so the shared chrome degrades
     identically here and in the Duplicates bar (`dqbar toolbar`). */
  container-name: selbar toolbar;
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

/* The `.bar-*` family itself now lives unscoped in App.css, next to the
   `.bar-btn--open` state that was already there. It is shared chrome: the model
   shelf's toolbar uses the same classes, and a scoped rule cannot cross a
   component boundary, so every `.bar-btn` outside this file rendered unstyled.
   Only the toolbar's own overrides stay here. */

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
  border-radius: var(--radius-sm);
  color: rgb(var(--v-theme-on-panel));
  font-family: var(--font-ui);
  font-size: var(--text-sm);
  text-align: left;
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

/* ── Sort panel ───────────────────────────────────────────────────────────── */
.gb-sort-panel {
  /* Two tracks need enough room for the stack-only label plus its availability
     glyph without ellipsis. The viewport cap preserves the narrow layout. */
  width: 460px;
  max-width: 92vw;
}

/* The migration notice occupies the row the removed sort order used to hold, so
   it is a menu section rather than a floating card: the user is already looking
   here, which is the whole reason it works as a pointer. */
.gb-sort-migration-note {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-accent), 0.12);
  border: 1px solid rgba(var(--v-theme-accent), 0.35);
  font-size: var(--text-xs);
  line-height: var(--leading-body);
  color: rgb(var(--v-theme-on-surface));
}

.gb-sort-migration-text {
  flex: 1;
  min-width: 0;
}

.gb-sort-migration-open {
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  color: rgb(var(--v-theme-on-surface));
  font-family: inherit;
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.gb-sort-migration-dismiss {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  padding: var(--space-1);
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.gb-sort-migration-open:hover,
.gb-sort-migration-dismiss:hover {
  background: var(--hover-wash);
}

.gb-sort-search-note {
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-panel), 0.7);
}

/* Similarity character picker - a 2-up grid of avatar rows. */
.gb-sim-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-1) var(--space-4);
  max-height: 320px;
  overflow-y: auto;
  /* Never scroll sideways - long names ellipsize instead (see .gb-sim-name). */
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
  border-radius: var(--radius-sm);
  color: rgb(var(--v-theme-on-panel));
  font-family: var(--font-ui);
  font-size: var(--text-sm);
  text-align: left;
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

.gb-size-section {
  display: flex;
  flex-direction: column;
}

.gb-size-controls {
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

/* Current size name, right-aligned under the slider. */
.gb-size-value {
  align-self: flex-end;
  font-size: var(--text-sm);
  opacity: var(--opacity-text-secondary);
  white-space: nowrap;
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

/* Stack time is conditional on the stacked lens. The small filter glyph makes
   that narrower availability visible without turning the row into warning
   copy; the native title gives mouse users the precise rule, while aria-label
   gives the same explanation to assistive technology. */
.tbm-toggle-filter-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  color: rgb(var(--v-theme-primary));
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

/* ── The ⋯ overflow ladder (see docs/design/toolbar-responsive-decisions.md,
   amendment #2). The burger stands at the end of its OWN group (the action
   run) and collapses only that group's members. Fold = CSS both ways: a
   control's bar button and its overflow row share one v-if, and these
   queries flip which of the pair is visible. No JS measures anything. The
   trigger appears with the FIRST fold step; Undo, Review, Settings and
   Stats never fold at any width. ─────────────────────────────────────── */
.tb-overflow {
  display: none;
}

.tb-row-700,
.tb-row-600 {
  display: none;
}

@container selbar (max-width: 700px) {
  .tb-fold-700 {
    display: none;
  }
  .tb-overflow {
    display: flex;
  }
  .tb-row-700 {
    display: flex;
  }
}

@container toolbar (max-width: 600px) {
  .tb-fold-600 {
    display: none;
  }
  .tb-row-600 {
    display: flex;
  }
}

/* Once the command groups no longer fit side by side, recompose them as two
   deliberate shell bands instead of squeezing, clipping, or hiding core
   actions. Fine-pointer narrow windows keep the 48px desktop band; coarse
   pointers get 56px rows so every target clears the touch floor. */
@media (max-width: 640px) {
  .selection-bar-overlay {
    height: 96px;
    padding-right: max(var(--space-2), env(safe-area-inset-right));
    padding-left: max(var(--space-2), env(safe-area-inset-left));
  }

  .selection-bar-content {
    height: 100%;
    flex-direction: column;
    align-items: stretch;
    justify-content: flex-start;
  }

  .selection-bar-left,
  .selection-bar-right {
    width: 100%;
    height: 50%;
    min-width: 0;
    flex-shrink: 0;
    box-sizing: border-box;
  }

  .selection-bar-left {
    overflow: hidden;
  }

  .selection-bar-right {
    justify-content: flex-end;
    margin-left: 0;
    border-top: 1px solid rgb(var(--v-theme-divider));
  }
}

@media (max-width: 640px) and (hover: none) and (pointer: coarse) {
  .selection-bar-overlay {
    height: 112px;
  }
}
</style>
