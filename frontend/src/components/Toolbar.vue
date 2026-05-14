<template>
  <div class="selection-bar-overlay">
    <div class="selection-bar-content">
      <div class="selection-bar-left">
        <!-- ── Sidebar toggle ──────────────────────────────────── -->
        <button
          class="bar-btn bar-btn--icon"
          :class="{ 'bar-btn--active': tb?.sidebarVisible?.value }"
          type="button"
          :title="tb?.sidebarVisible?.value ? 'Hide sidebar' : 'Show sidebar'"
          @click="tb?.toggleSidebar?.()"
        >
          <v-icon size="20">mdi-dock-left</v-icon>
        </button>
        <div class="bar-separator"></div>
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
          <div class="gb-sort-panel">
            <div class="gb-sort-header">
              <div class="gb-sort-panel-title">
                Sort order
                <span>Choose one</span>
              </div>
              <v-btn
                class="gb-sort-direction"
                variant="text"
                :disabled="gb?.isSearchActive?.value"
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
            <div v-if="gb?.isSearchActive?.value" class="gb-sort-search-note">
              Search relevance (fixed)
            </div>
            <v-btn-toggle
              :model-value="gbSortMenuModel"
              @update:model-value="gbHandleSortModelUpdate"
              mandatory
              class="gb-sort-grid"
              :disabled="gb?.isSearchActive?.value"
            >
              <v-btn
                v-for="opt in gb?.sortOptions?.value ?? []"
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
                    v-for="opt in gb?.similarityCharacterOptions?.value ?? []"
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
              :class="{ 'bar-btn--active': gbIsFilterActive }"
              type="button"
              title="Filters"
            >
              <v-icon size="19">mdi-filter</v-icon>
              <span v-if="gbActiveFilterCount > 0" class="bar-filter-badge">{{
                gbActiveFilterCount > 99 ? "99+" : gbActiveFilterCount
              }}</span>
              <v-icon size="18" class="bar-btn-chevron">mdi-menu-down</v-icon>
            </button>
          </template>
          <div class="gb-filter-panel">
            <div class="gb-filter-panel-header">
              <div class="gb-filter-panel-title">Filters</div>
              <v-btn
                v-if="gbIsFilterActive"
                variant="text"
                density="compact"
                size="x-small"
                color="primary"
                class="gb-filter-clear-all-btn"
                @click="gbClearAllFilters"
                >Clear all</v-btn
              >
            </div>
            <div
              v-if="!isReadOnly || isAllPicturesView"
              class="gb-filter-shared-only-row"
            >
              <label v-if="!isReadOnly" class="gb-filter-shared-only-label">
                <input
                  type="checkbox"
                  :checked="gbSharedOnlyFilter"
                  @change="gbSharedOnlyFilter = $event.target.checked"
                />
                Shared pictures only
              </label>
              <label
                v-if="isAllPicturesView"
                class="gb-filter-shared-only-label gb-filter-shared-only-label--right"
              >
                <input
                  type="checkbox"
                  :checked="gbUnassignedOnlyFilter"
                  @change="gbUnassignedOnlyFilter = $event.target.checked"
                />
                Unassigned only
              </label>
            </div>
            <div class="gb-filter-section-label">Media</div>
            <div
              class="gb-media-type-toggle"
              role="group"
              aria-label="Media type filter"
            >
              <v-btn
                v-for="opt in gbMediaTypeOptions"
                :key="opt.value"
                class="gb-media-type-button"
                :class="{
                  'gb-media-type-button--active':
                    gbMediaTypeFilter === opt.value,
                }"
                variant="text"
                :title="opt.title"
                :aria-pressed="gbMediaTypeFilter === opt.value"
                @click="gbSetMediaTypeFilter(opt.value)"
              >
                <v-icon size="16">{{ opt.icon }}</v-icon>
              </v-btn>
            </div>
            <div class="gb-score-range-section">
              <div class="gb-score-range-headers">
                <span class="gb-score-range-header-label">Min Score</span>
                <span
                  class="gb-score-range-header-label gb-score-range-header-label--right"
                  >Max Score</span
                >
              </div>
              <div class="gb-score-range-filter">
                <div class="gb-score-range-stars">
                  <button
                    v-for="n in 5"
                    :key="'min-' + n"
                    class="gb-score-star-btn"
                    type="button"
                    :title="`Set minimum score ${n}`"
                    @click="gbSetMinScore(n)"
                  >
                    <v-icon
                      size="15"
                      :color="
                        gbMinScoreFilter != null && n <= gbMinScoreFilter
                          ? 'warning'
                          : undefined
                      "
                      >{{
                        gbMinScoreFilter != null && n <= gbMinScoreFilter
                          ? "mdi-star"
                          : "mdi-star-outline"
                      }}</v-icon
                    >
                  </button>
                </div>
                <div class="gb-score-range-stars gb-score-range-stars--right">
                  <button
                    v-for="n in 5"
                    :key="'max-' + n"
                    class="gb-score-star-btn"
                    type="button"
                    :title="`Set maximum score ${n}`"
                    @click="gbSetMaxScore(n)"
                  >
                    <v-icon
                      size="15"
                      :color="
                        gbMaxScoreFilter != null && n <= gbMaxScoreFilter
                          ? 'warning'
                          : undefined
                      "
                      >{{
                        gbMaxScoreFilter != null && n <= gbMaxScoreFilter
                          ? "mdi-star"
                          : "mdi-star-outline"
                      }}</v-icon
                    >
                  </button>
                </div>
              </div>
            </div>
            <div class="gb-filter-section-label" style="margin-top: 10px">
              Face
            </div>
            <div
              class="gb-media-type-toggle"
              role="group"
              aria-label="Face filter"
            >
              <v-btn
                v-for="opt in gbFaceBboxFilterOptions"
                :key="String(opt.value)"
                class="gb-media-type-button"
                :class="{
                  'gb-media-type-button--active':
                    gbFaceBboxFilter === opt.value,
                }"
                variant="text"
                :title="opt.title"
                :aria-pressed="gbFaceBboxFilter === opt.value"
                @click="gbSetFaceBboxFilter(opt.value)"
              >
                <span
                  v-if="opt.value === 'without_face'"
                  class="gb-face-no-detection-icon"
                >
                  <v-icon size="16">{{ opt.icon }}</v-icon>
                </span>
                <v-icon v-else size="16">{{ opt.icon }}</v-icon>
              </v-btn>
            </div>
            <div class="gb-filter-section-header" style="margin-top: 10px">
              <span class="gb-filter-section-label" style="margin-top: 0"
                >Tags</span
              >
              <v-btn
                v-if="gbTagFilter.length || gbTagRejectedFilter.length"
                variant="text"
                density="compact"
                size="x-small"
                color="primary"
                class="gb-filter-clear-all-btn"
                @click="
                  gbTagFilter = [];
                  gbTagRejectedFilter = [];
                "
                >Clear</v-btn
              >
            </div>
            <div class="gb-tag-filter-input-wrap">
              <input
                v-model="gbTagFilterInput"
                class="gb-tag-filter-input"
                placeholder="Filter by tag…"
                autocomplete="off"
                @keydown.enter.prevent="
                  gbTagFilterIndex >= 0 && gbTagFilterSuggestions.length
                    ? gbAddTagFilter(gbTagFilterSuggestions[gbTagFilterIndex])
                    : gbAddTagFilter(gbTagFilterInput.trim())
                "
                @keydown.tab.prevent="
                  gbTagFilterSuggestions.length
                    ? gbAddTagFilter(
                        gbTagFilterSuggestions[
                          gbTagFilterIndex >= 0 ? gbTagFilterIndex : 0
                        ],
                      )
                    : gbAddTagFilter(gbTagFilterInput.trim())
                "
                @keydown.down.prevent="
                  gbTagFilterIndex = Math.min(
                    gbTagFilterIndex + 1,
                    gbTagFilterSuggestions.length - 1,
                  )
                "
                @keydown.up.prevent="
                  gbTagFilterIndex = Math.max(gbTagFilterIndex - 1, -1)
                "
                @keydown.escape.prevent="gbTagFilterSuggestions = []"
              />
              <div
                v-if="gbTagFilterSuggestions.length"
                class="gb-tag-filter-dropdown"
                :class="{
                  'gb-tag-filter-dropdown--hover-enabled':
                    gbTagFilterHoverEnabled,
                }"
                @mousemove.once="gbTagFilterHoverEnabled = true"
              >
                <button
                  v-for="(tag, idx) in gbTagFilterSuggestions"
                  :key="tag"
                  class="gb-tag-filter-suggestion"
                  :class="{
                    'gb-tag-filter-suggestion--active':
                      idx === gbTagFilterIndex,
                  }"
                  type="button"
                  @mousedown.prevent="gbAddTagFilter(tag)"
                  @mousemove="gbTagFilterIndex = idx"
                >
                  {{ tag }}
                </button>
              </div>
            </div>
            <div
              v-if="gbTagFilter.length || gbTagRejectedFilter.length"
              class="gb-tag-filter-chips"
            >
              <button
                v-for="tag in gbTagFilter"
                :key="`confirmed-${tag}`"
                class="tag-chip tag-chip--filter"
                type="button"
                :title="`'${tag}' – click to switch to rejected match`"
                @click.stop="gbToggleTagRejected(tag)"
              >
                <span class="tag-chip-label">{{ tag }}</span>
                <v-icon
                  size="11"
                  class="tag-chip-close"
                  @click.stop="gbRemoveTagFilter(tag)"
                  >mdi-close</v-icon
                >
              </button>
              <button
                v-for="tag in gbTagRejectedFilter"
                :key="`rejected-${tag}`"
                class="tag-chip tag-chip--filter tag-chip--filter-rejected"
                type="button"
                :title="`'${tag}' (rejected) – click to switch to confirmed match`"
                @click.stop="gbToggleTagRejected(tag)"
              >
                <span class="tag-chip-label">{{ tag }}</span>
                <v-icon
                  size="11"
                  class="tag-chip-close"
                  @click.stop="gbRemoveTagFilter(tag)"
                  >mdi-close</v-icon
                >
              </button>
            </div>
            <div class="gb-filter-section-label" style="margin-top: 10px">
              Tag confidence
            </div>
            <div class="gb-confidence-filter-row">
              <div
                class="gb-tag-filter-input-wrap gb-confidence-filter-tag-wrap"
              >
                <input
                  v-model="gbConfidenceTagInput"
                  class="gb-tag-filter-input gb-confidence-filter-tag-input"
                  placeholder="Tag…"
                  autocomplete="off"
                  @keydown.enter.prevent="
                    gbConfidenceTagIndex >= 0 &&
                    gbConfidenceTagSuggestions.length
                      ? gbSelectConfidenceTagSuggestion(
                          gbConfidenceTagSuggestions[gbConfidenceTagIndex],
                        )
                      : gbAddConfidenceFilter(gbConfidenceTagInput.trim())
                  "
                  @keydown.tab.prevent="
                    gbConfidenceTagSuggestions.length
                      ? gbSelectConfidenceTagSuggestion(
                          gbConfidenceTagSuggestions[
                            gbConfidenceTagIndex >= 0 ? gbConfidenceTagIndex : 0
                          ],
                        )
                      : undefined
                  "
                  @keydown.down.prevent="
                    gbConfidenceTagIndex = Math.min(
                      gbConfidenceTagIndex + 1,
                      gbConfidenceTagSuggestions.length - 1,
                    )
                  "
                  @keydown.up.prevent="
                    gbConfidenceTagIndex = Math.max(
                      gbConfidenceTagIndex - 1,
                      -1,
                    )
                  "
                  @keydown.escape.prevent="gbConfidenceTagSuggestions = []"
                />
                <div
                  v-if="gbConfidenceTagSuggestions.length"
                  class="gb-tag-filter-dropdown"
                  :class="{
                    'gb-tag-filter-dropdown--hover-enabled':
                      gbConfidenceTagHoverEnabled,
                  }"
                  @mousemove.once="gbConfidenceTagHoverEnabled = true"
                >
                  <button
                    v-for="(tag, idx) in gbConfidenceTagSuggestions"
                    :key="tag"
                    class="gb-tag-filter-suggestion"
                    :class="{
                      'gb-tag-filter-suggestion--active':
                        idx === gbConfidenceTagIndex,
                    }"
                    type="button"
                    @mousedown.prevent="gbSelectConfidenceTagSuggestion(tag)"
                    @mousemove="gbConfidenceTagIndex = idx"
                  >
                    {{ tag }}
                  </button>
                </div>
              </div>
              <div class="gb-confidence-threshold-stepper">
                <input
                  v-model.number="gbConfidenceThreshold"
                  type="number"
                  min="0"
                  max="1"
                  step="0.05"
                  class="gb-confidence-threshold-input"
                />
              </div>
              <button
                class="gb-confidence-mode-btn"
                type="button"
                :title="
                  gbConfidenceMode === 'above'
                    ? 'High confidence, not labelled – click to switch'
                    : 'Low confidence, labelled – click to switch'
                "
                @click="
                  gbConfidenceMode =
                    gbConfidenceMode === 'above' ? 'below' : 'above'
                "
              >
                {{ gbConfidenceMode === "above" ? "≥" : "<" }}
              </button>
              <button
                class="gb-confidence-add-btn"
                type="button"
                :disabled="!gbConfidenceTagInput.trim()"
                @click="gbAddConfidenceFilter()"
              >
                Add
              </button>
            </div>
            <div
              v-if="
                gbTagConfidenceAboveFilter.length ||
                gbTagConfidenceBelowFilter.length
              "
              class="gb-tag-filter-chips"
            >
              <button
                v-for="entry in gbTagConfidenceAboveFilter"
                :key="`ca-${entry}`"
                class="tag-chip tag-chip--filter tag-chip--confidence-above"
                type="button"
                :title="`Prediction ≥${Math.round(parseFloat(entry.split(':')[1]) * 100)}%, not labelled`"
              >
                <span class="tag-chip-label"
                  >≥{{ gbConfidenceEntryLabel(entry) }}</span
                >
                <v-icon
                  size="11"
                  class="tag-chip-close"
                  @click.stop="gbRemoveConfidenceAboveFilter(entry)"
                  >mdi-close</v-icon
                >
              </button>
              <button
                v-for="entry in gbTagConfidenceBelowFilter"
                :key="`cb-${entry}`"
                class="tag-chip tag-chip--filter tag-chip--confidence-below"
                type="button"
                :title="`Prediction <${Math.round(parseFloat(entry.split(':')[1]) * 100)}%, labelled`"
              >
                <span class="tag-chip-label"
                  >&lt;{{ gbConfidenceEntryLabel(entry) }}</span
                >
                <v-icon
                  size="11"
                  class="tag-chip-close"
                  @click.stop="gbRemoveConfidenceBelowFilter(entry)"
                  >mdi-close</v-icon
                >
              </button>
            </div>
            <template
              v-if="gbComfyuiModelOptions.length || gbComfyuiLoraOptions.length"
            >
              <div
                class="gb-filter-section-header gb-comfyui-section-header"
                style="margin-top: 10px; cursor: pointer"
                @click="gbComfyuiFilterExpanded = !gbComfyuiFilterExpanded"
              >
                <span class="gb-filter-section-label" style="margin-top: 0"
                  >ComfyUI</span
                >
                <v-icon size="16" style="opacity: 0.6">{{
                  gbComfyuiFilterExpanded
                    ? "mdi-chevron-up"
                    : "mdi-chevron-down"
                }}</v-icon>
              </div>
              <template
                v-if="gbComfyuiModelOptions.length && gbComfyuiFilterExpanded"
              >
                <div
                  style="
                    display: flex;
                    align-items: center;
                    justify-content: space-between;
                    margin-bottom: 4px;
                    width: 100%;
                  "
                >
                  <span
                    style="
                      font-size: 0.85em;
                      color: rgb(var(--v-theme-on-background));
                    "
                    >Models</span
                  >
                  <v-btn
                    v-if="gbComfyuiModelFilter.length"
                    variant="text"
                    density="compact"
                    size="x-small"
                    color="primary"
                    style="
                      min-width: 0;
                      padding: 0 4px;
                      height: 18px;
                      font-size: 0.75em;
                    "
                    @click="gbComfyuiModelFilter = []"
                    >Clear</v-btn
                  >
                </div>
                <div
                  style="
                    width: 100%;
                    height: 200px;
                    overflow-y: auto;
                    margin-bottom: 8px;
                    border: 1px solid rgba(var(--v-theme-on-background), 0.18);
                    border-radius: 6px;
                    padding: 2px 4px;
                    background: rgba(var(--v-theme-on-background), 0.04);
                    color: rgb(var(--v-theme-on-background));
                  "
                >
                  <v-checkbox
                    v-for="m in gbComfyuiModelOptions"
                    :key="m"
                    v-model="gbComfyuiModelFilter"
                    :value="m"
                    :label="m.replace(/\.[^/.]+$/, '')"
                    density="compact"
                    hide-details
                    color="primary"
                  />
                </div>
              </template>
              <template
                v-if="gbComfyuiLoraOptions.length && gbComfyuiFilterExpanded"
              >
                <div
                  style="
                    display: flex;
                    align-items: center;
                    justify-content: space-between;
                    margin-bottom: 4px;
                    width: 100%;
                  "
                >
                  <span
                    style="
                      font-size: 0.85em;
                      color: rgb(var(--v-theme-on-background));
                    "
                    >LoRAs</span
                  >
                  <v-btn
                    v-if="gbComfyuiLoraFilter.length"
                    variant="text"
                    density="compact"
                    size="x-small"
                    color="primary"
                    style="
                      min-width: 0;
                      padding: 0 4px;
                      height: 18px;
                      font-size: 0.75em;
                    "
                    @click="gbComfyuiLoraFilter = []"
                    >Clear</v-btn
                  >
                </div>
                <div
                  style="
                    width: 100%;
                    height: 200px;
                    overflow-y: auto;
                    border: 1px solid rgba(var(--v-theme-on-background), 0.18);
                    border-radius: 6px;
                    padding: 2px 4px;
                    background: rgba(var(--v-theme-on-background), 0.04);
                    color: rgb(var(--v-theme-on-background));
                  "
                >
                  <v-checkbox
                    v-for="l in gbComfyuiLoraOptions"
                    :key="l"
                    v-model="gbComfyuiLoraFilter"
                    :value="l"
                    :label="l.replace(/\.[^/.]+$/, '')"
                    density="compact"
                    hide-details
                    color="primary"
                  />
                </div>
              </template>
            </template>
          </div>
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
          <div class="gb-view-panel">
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
                :min="gb?.minColumns?.value ?? 2"
                :max="gb?.maxColumns?.value ?? 14"
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
                  @click="gb?.expandAllStacks?.()"
                  >Expand all</v-btn
                >
                <v-btn
                  class="gb-stack-toggle-btn"
                  color="primary"
                  variant="flat"
                  size="small"
                  :disabled="gbCollapseAllStacksDisabled"
                  @click="gb?.collapseAllStacks?.()"
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
          :class="{ 'bar-btn--active': gb?.isSearchActive?.value }"
          type="button"
          title="Search (F)"
          @click="tb?.openSearchOverlay?.()"
        >
          <v-icon size="20">mdi-magnify</v-icon>
        </button>
        <!-- ── Toolbar: Export ───────────────────────────────────────── -->
        <v-menu
          v-if="!isReadOnly"
          v-model="tbExportMenuOpen"
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
          <div class="tb-export-panel">
            <div class="tb-export-title">
              Export {{ tb?.exportCount?.value ?? 0 }} picture{{
                (tb?.exportCount?.value ?? 0) === 1 ? "" : "s"
              }}
            </div>
            <v-select
              v-model="tbExportTypeModel"
              :items="tb?.exportTypeOptions ?? []"
              item-title="title"
              item-value="value"
              label="Export type"
              density="comfortable"
            />
            <v-select
              v-model="tbExportCaptionModeModel"
              :items="tb?.exportCaptionOptions ?? []"
              item-title="title"
              item-value="value"
              label="Captions"
              density="comfortable"
              :disabled="tb?.exportTypeLocksCaptions?.value"
            />
            <v-select
              v-model="tbExportResolutionModel"
              :items="tb?.exportResolutionOptions ?? []"
              item-title="title"
              item-value="value"
              label="Resolution"
              density="comfortable"
            />
            <v-select
              v-if="tbExportCaptionModeModel === 'tags'"
              v-model="tbExportTagFormatModel"
              :items="tb?.exportTagFormatOptions ?? []"
              item-title="title"
              item-value="value"
              label="Tag format"
              density="comfortable"
            />
            <v-switch
              v-model="tbExportIncludeCharacterNameModel"
              label="Include character name"
              color="primary"
              density="comfortable"
              :disabled="
                tbExportCaptionModeModel === 'none' ||
                tb?.exportTypeLocksCaptions?.value
              "
            />
            <v-switch
              v-model="tbExportUseOriginalFileNamesModel"
              label="Use original file names"
              color="primary"
              density="comfortable"
            />
            <v-btn
              color="primary"
              @click="
                tb?.confirmExportZip?.();
                tbExportMenuOpen = false;
              "
            >
              Export
            </v-btn>
          </div>
        </v-menu>
        <!-- ── Toolbar: Import ───────────────────────────────────────── -->
        <button
          v-if="!isReadOnly && tb"
          class="bar-btn bar-btn--icon"
          type="button"
          title="Import photos"
          @click="tb?.openImport?.()"
        >
          <v-icon size="20">mdi-cloud-upload-outline</v-icon>
        </button>
        <!-- ── Toolbar: ComfyUI T2I ──────────────────────────────────── -->
        <v-menu
          v-if="gb?.comfyuiConfigured?.value && !isReadOnly"
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
              title="Generate with ComfyUI (T2I)"
            >
              <v-icon size="20">mdi-robot</v-icon>
            </button>
          </template>
          <div class="tb-comfyui-panel">
            <div class="tb-comfyui-header">Generate with ComfyUI (T2I)</div>
            <div class="tb-comfyui-body">
              <div v-if="tbComfyuiWorkflowLoading" class="tb-comfyui-note">
                Loading workflows...
              </div>
              <div v-else>
                <div v-if="tbComfyuiWorkflowError" class="tb-comfyui-error">
                  {{ tbComfyuiWorkflowError }}
                </div>
                <template v-if="tbValidComfyWorkflows.length">
                  <label class="tb-comfyui-label">Workflow</label>
                  <select
                    v-model="tbComfyuiSelectedWorkflow"
                    class="tb-comfyui-select"
                  >
                    <option
                      v-for="wf in tbValidComfyWorkflows"
                      :key="wf.name"
                      :value="wf.name"
                    >
                      {{ wf.display_name || wf.name }}
                    </option>
                  </select>
                  <label class="tb-comfyui-label">Caption</label>
                  <textarea
                    v-model="tbComfyuiCaption"
                    class="tb-comfyui-textarea"
                    rows="4"
                    placeholder="Optional caption for {{caption}}"
                    @keydown.stop
                  ></textarea>
                  <label class="tb-comfyui-label">Seed</label>
                  <div class="tb-comfyui-seed-row">
                    <button
                      type="button"
                      class="tb-comfyui-seed-btn"
                      :class="{ active: tbComfyuiSeedMode === 'random' }"
                      @click="tbComfyuiSeedMode = 'random'"
                    >
                      Random
                    </button>
                    <button
                      type="button"
                      class="tb-comfyui-seed-btn"
                      :class="{ active: tbComfyuiSeedMode === 'fixed' }"
                      @click="tbComfyuiSeedMode = 'fixed'"
                    >
                      Fixed
                    </button>
                    <input
                      v-if="tbComfyuiSeedMode === 'fixed'"
                      v-model.number="tbComfyuiSeed"
                      type="number"
                      class="tb-comfyui-seed-input"
                      min="0"
                      max="4294967295"
                      @keydown.stop
                    />
                    <button
                      class="tb-comfyui-run-btn"
                      type="button"
                      :disabled="!tbCanRunComfyWorkflow"
                      @click="tbRunComfyuiOnGrid"
                    >
                      <v-icon size="14">mdi-play</v-icon> Run
                    </button>
                  </div>
                </template>
                <div v-else class="tb-comfyui-note">
                  No valid T2I workflows found.
                </div>
                <div v-if="tbComfyuiRunError" class="tb-comfyui-error">
                  {{ tbComfyuiRunError }}
                </div>
              </div>
            </div>
          </div>
        </v-menu>
      </div>
      <!-- ── Conditional separator: ComfyUI | ApplyTo (shown when bar is narrow) ── -->
      <div class="bar-separator bar-separator--gap-guard"></div>
      <div class="selection-bar-right">
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
              <button
                v-bind="menuProps"
                class="hidden-panel-activator"
                type="button"
                tabindex="-1"
                aria-hidden="true"
              ></button>
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
              <button
                v-bind="menuProps"
                class="hidden-panel-activator"
                type="button"
                tabindex="-1"
                aria-hidden="true"
              ></button>
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
        <!-- Selection ▾ dropdown — mirrors the right-click context menu exactly -->
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
                      ? `Actions for ${selectedCount} selected (${props.selectedExpandedCount} total including stacks)`
                      : `Actions for ${selectedCount} selected`
                "
              >
                <v-icon size="20">mdi-image-multiple-outline</v-icon>
                <span class="bar-btn-apply-label">({{ selectedCount }})</span>
                <v-icon size="18" class="bar-btn-chevron">mdi-menu-down</v-icon>
              </button>
            </template>
            <div class="selection-menu-panel">
              <!-- ── Set / Character / Project ─────────────────────── -->
              <template v-if="!isScrapheapView && !isReadOnly">
                <AddToSetControl
                  placement="right"
                  :backend-url="backendUrl"
                  :picture-ids="selectedImageIds"
                  :disabled="selectedCount === 0"
                  @added="$emit('added-to-set', $event)"
                />
                <AddToCharacterControl
                  placement="right"
                  :backend-url="backendUrl"
                  :picture-ids="selectedImageIds"
                  :disabled="selectedCount === 0"
                  @added="$emit('add-to-character', $event)"
                  @removed="$emit('remove-from-character', $event)"
                />
                <AddToProjectControl
                  placement="right"
                  :backend-url="backendUrl"
                  :picture-ids="selectedImageIds"
                  :disabled="selectedCount === 0"
                  @selected="$emit('set-project', $event)"
                />
                <div class="ctx-sep" />
              </template>

              <!-- ── Stack / Unstack ───────────────────────────────── -->
              <template v-if="!isScrapheapView && !isReadOnly">
                <button
                  v-if="showRemoveStackButton"
                  class="ctx-item"
                  title="Remove selected images from their stack"
                  @click="
                    $emit('remove-from-stack');
                    selectionMenuOpen = false;
                  "
                >
                  <v-icon class="ctx-icon" size="15">mdi-layers-off</v-icon>
                  Unstack
                </button>
                <button
                  v-else-if="selectedCount > 1"
                  class="ctx-item"
                  title="Create a stack from the selected images"
                  @click="
                    $emit('create-stack');
                    selectionMenuOpen = false;
                  "
                >
                  <v-icon class="ctx-icon" size="15">mdi-layers</v-icon>
                  Stack
                </button>
                <button
                  v-if="showUnstackMultipleButton"
                  class="ctx-item"
                  title="Dissolve all selected stacks"
                  @click="
                    $emit('dissolve-stacks');
                    selectionMenuOpen = false;
                  "
                >
                  <v-icon class="ctx-icon" size="15">mdi-layers-off</v-icon>
                  Unstack all
                </button>
                <button
                  v-if="showGroupStackButton"
                  class="ctx-item"
                  title="Create stacks from selected likeness groups"
                  @click="
                    $emit('create-stacks-from-groups');
                    selectionMenuOpen = false;
                  "
                >
                  <v-icon class="ctx-icon" size="15">mdi-layers-plus</v-icon>
                  Stack groups
                </button>
                <div v-if="showAnyStackAction" class="ctx-sep" />
              </template>

              <!-- ── Tag / Filters / ComfyUI ───────────────────────── -->
              <template v-if="!isScrapheapView && !isReadOnly">
                <button
                  class="ctx-item"
                  :disabled="selectedCount === 0"
                  title="Tag selected (T)"
                  @click="
                    openTagInput();
                    selectionMenuOpen = false;
                  "
                >
                  <v-icon class="ctx-icon" size="15">mdi-tag-plus</v-icon>
                  Tag
                </button>
                <button
                  v-if="pluginOptions.length"
                  class="ctx-item"
                  :disabled="selectedCount === 0"
                  @click="
                    openPluginPanel();
                    selectionMenuOpen = false;
                  "
                >
                  <v-icon class="ctx-icon" size="15">mdi-tune-variant</v-icon>
                  Filters
                </button>
                <button
                  v-if="props.comfyuiConfigured"
                  class="ctx-item"
                  :disabled="selectedCount === 0"
                  @click="
                    openComfyuiPanel();
                    selectionMenuOpen = false;
                  "
                >
                  <v-icon class="ctx-icon" size="15">mdi-robot</v-icon>
                  ComfyUI
                </button>
                <div class="ctx-sep" />
              </template>

              <!-- ── Remove / Delete (danger) ──────────────────────── -->
              <button
                v-if="showRemoveButton && !isReadOnly"
                class="ctx-item ctx-item--danger"
                :disabled="selectedCount === 0"
                @click="
                  $emit('remove-from-group');
                  selectionMenuOpen = false;
                "
              >
                {{ removeButtonLabel }}
              </button>
              <button
                v-if="!isReadOnly"
                class="ctx-item ctx-item--danger"
                :disabled="selectedCount === 0"
                title="Delete selected items (DEL)"
                @click="
                  $emit('delete-selected');
                  selectionMenuOpen = false;
                "
              >
                <v-icon class="ctx-icon" size="15">mdi-delete</v-icon>
                {{ deleteButtonLabel }}
              </button>
            </div>
          </v-menu>
          <div
            v-if="!isScrapheapView && !isReadOnly"
            class="plugin-run-controls"
          >
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
                  class="hidden-panel-activator"
                  type="button"
                  tabindex="-1"
                  aria-hidden="true"
                ></button>
              </template>
              <div class="plugin-menu-panel tag-panel-wide">
                <div class="plugin-menu-header">
                  Tag {{ selectedCount }} Image{{
                    selectedCount !== 1 ? "s" : ""
                  }}
                </div>
                <div class="tag-panel-columns">
                  <!-- ── Left column: mini-grid preview ── -->
                  <div
                    v-if="previewImages.length"
                    class="tag-preview-column"
                    :class="[
                      `tag-preview-column--cols-${previewColumns}`,
                      previewImages.length === 2
                        ? 'tag-preview-column--stacked'
                        : '',
                    ]"
                  >
                    <div class="tag-preview-header">Selected images</div>
                    <div
                      class="tag-preview-grid"
                      :class="[
                        `tag-preview-grid--cols-${previewColumns}`,
                        previewImages.length > 1
                          ? 'tag-preview-grid--multi'
                          : '',
                      ]"
                    >
                      <div
                        v-for="img in previewImages"
                        :key="img.id"
                        class="tag-preview-tile"
                      >
                        <img
                          v-if="img.fullUrl"
                          :src="img.fullUrl"
                          class="tag-preview-img"
                          :alt="String(img.id)"
                          draggable="false"
                        />
                        <div
                          v-else
                          class="tag-preview-img tag-preview-img--placeholder"
                        />
                      </div>
                    </div>
                  </div>
                  <!-- ── Right column: tag controls ── -->
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
                          :class="[
                            'tag-chip',
                            'tag-chip--all',
                            { 'tag-chip--penalised': isPenalisedTagSB(t.name) },
                          ]"
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
                          :class="[
                            'tag-chip',
                            'tag-chip--some',
                            { 'tag-chip--penalised': isPenalisedTagSB(t.name) },
                          ]"
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
                    <div
                      v-if="aggregatedPredictions.length"
                      class="tag-current-section"
                    >
                      <div
                        class="tag-current-label tag-current-label--clickable"
                      >
                        <button
                          class="tag-current-toggle"
                          type="button"
                          @click="
                            rejectedTagsCollapsedSB = !rejectedTagsCollapsedSB
                          "
                        >
                          Rejected Tags
                          <span class="rejected-threshold-label"
                            >({{
                              Object.keys(labelThresholdsSB).length
                                ? "per-tag threshold"
                                : `> ${(predictionAcceptanceThresholdSB * 100).toFixed(0)}%`
                            }}
                            to be auto-applied)</span
                          >
                          <v-icon size="12">{{
                            rejectedTagsCollapsedSB
                              ? "mdi-chevron-down"
                              : "mdi-chevron-up"
                          }}</v-icon>
                        </button>
                      </div>
                      <div
                        v-show="!rejectedTagsCollapsedSB"
                        class="tag-chips-row"
                      >
                        <button
                          v-for="p in aggregatedPredictions"
                          :key="'pred-' + p.tag"
                          :class="[
                            'tag-chip',
                            'tag-chip--prediction',
                            { 'tag-chip--penalised': isPenalisedTagSB(p.tag) },
                          ]"
                          type="button"
                          :disabled="predActionLoading.includes(p.tag)"
                          :style="{ '--pred-confidence': p.avgConf }"
                          :title="`Rejected on ${p.count} image${p.count !== 1 ? 's' : ''}, avg ${(p.avgConf * 100).toFixed(0)}%, needs +${(p.avgNeeded * 100).toFixed(0)}% to auto-accept — click to confirm all`"
                          @click="confirmPredictionOnAll(p)"
                        >
                          <span class="tag-chip-label">{{ p.tag }}</span>
                          <span class="tag-chip-count"
                            >{{ p.count }}/{{
                              fetchedPredictionData.length
                            }}</span
                          >
                        </button>
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
              </div>
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
            v-if="!isReadOnly"
            class="delete-btn"
            :disabled="!visible"
            @click="$emit('delete-selected')"
            title="Delete selected items (DEL)"
          >
            <v-icon size="20" color="error">mdi-delete</v-icon>
          </button>
        </div>
        <!-- /selection-ctx-bar -->
        <!-- ── Separator: Delete | Settings ───────────────────────────── -->
        <div v-if="tb" class="bar-separator"></div>
        <!-- ── Toolbar: Settings ─────────────────────────────────────── -->
        <button
          v-if="tb"
          class="bar-btn bar-btn--icon"
          type="button"
          title="Settings"
          @click="tb?.openSettings?.()"
        >
          <v-icon size="20">mdi-cog-outline</v-icon>
        </button>
        <!-- ── Toolbar: Stats toggle ──── -->
        <button
          v-if="tb"
          class="bar-btn bar-btn--icon"
          :class="{ 'bar-btn--active': tb?.statsOpen?.value }"
          type="button"
          :title="
            tb?.statsOpen?.value ? 'Hide stats sidebar' : 'Show stats sidebar'
          "
          @click="tb?.toggleStats?.()"
        >
          <v-icon size="20">mdi-chart-bar</v-icon>
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
        <span
          v-if="idx === (tagSuggestionIndex >= 0 ? tagSuggestionIndex : 0)"
          class="sb-tag-autocomplete-tab-hint"
          >TAB</span
        >
      </button>
    </div>
  </Teleport>
</template>

<script setup>
import { computed, inject, nextTick, ref, watch } from "vue";
import { apiClient, isReadOnly } from "../utils/apiClient";
import AddToSetControl from "./AddToSetControl.vue";
import AddToCharacterControl from "./AddToCharacterControl.vue";
import AddToProjectControl from "./AddToProjectControl.vue";
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
  selectedMultipleStackIds: { type: Array, default: () => [] },
  availablePlugins: { type: Array, default: () => [] },
  allGridImages: { type: Array, default: () => [] },
});

const MAX_TAG_FETCH = 100;
const MAX_PREVIEW_IMAGES = 16;

const emit = defineEmits([
  "clear-selection",
  "added-to-set",
  "add-to-character",
  "remove-from-character",
  "set-project",
  "remove-from-stack",
  "dissolve-stacks",
  "create-stack",
  "create-stacks-from-groups",
  "remove-from-group",
  "delete-selected",
  "run-plugin",
  "comfyui-run",
  "tags-applied",
]);

const LIKENESS_GROUPS_SORT_KEY = "LIKENESS_GROUPS";

// ═══════════════════════════════════════════════════════════════════════════════
// Grid Bar state (inject from App.vue via provide/inject)
// ═══════════════════════════════════════════════════════════════════════════════
const gb = inject("gridBarState", null);

// ═══════════════════════════════════════════════════════════════════════════════
// Toolbar state (inject from App.vue via provide/inject)
// ═══════════════════════════════════════════════════════════════════════════════
const tb = inject("toolbarState", null);

// ── Toolbar: export computed models ───────────────────────────────────────────
const tbExportTypeModel = computed({
  get: () => tb?.exportType?.value ?? "full",
  set: (v) => {
    if (tb?.exportType) tb.exportType.value = v;
  },
});
const tbExportCaptionModeModel = computed({
  get: () => tb?.exportCaptionMode?.value ?? "description",
  set: (v) => {
    if (tb?.exportCaptionMode) tb.exportCaptionMode.value = v;
  },
});
const tbExportTagFormatModel = computed({
  get: () => tb?.exportTagFormat?.value ?? "spaces",
  set: (v) => {
    if (tb?.exportTagFormat) tb.exportTagFormat.value = v;
  },
});
const tbExportResolutionModel = computed({
  get: () => tb?.exportResolution?.value ?? "original",
  set: (v) => {
    if (tb?.exportResolution) tb.exportResolution.value = v;
  },
});
const tbExportIncludeCharacterNameModel = computed({
  get: () => tb?.exportIncludeCharacterName?.value ?? true,
  set: (v) => {
    if (tb?.exportIncludeCharacterName) tb.exportIncludeCharacterName.value = v;
  },
});
const tbExportUseOriginalFileNamesModel = computed({
  get: () => tb?.exportUseOriginalFileNames?.value ?? false,
  set: (v) => {
    if (tb?.exportUseOriginalFileNames) tb.exportUseOriginalFileNames.value = v;
  },
});
const tbExportMenuOpen = computed({
  get: () => tb?.exportMenuOpen?.value ?? false,
  set: (v) => {
    if (tb?.exportMenuOpen) tb.exportMenuOpen.value = v;
  },
});

// ── Toolbar: ComfyUI T2I ───────────────────────────────────────────────────────
const tbComfyuiMenuOpen = ref(false);
const tbComfyuiWorkflows = ref([]);
const tbComfyuiWorkflowLoading = ref(false);
const tbComfyuiWorkflowError = ref("");
const tbComfyuiSelectedWorkflow = ref("");
const tbComfyuiCaption = ref("");
const tbComfyuiRunError = ref("");
const tbComfyuiSeedMode = ref(
  sessionStorage.getItem("comfyui_t2i_seed_mode") === "fixed"
    ? "fixed"
    : "random",
);
const _tbSavedSeed = Number(sessionStorage.getItem("comfyui_t2i_seed"));
const tbComfyuiSeed = ref(
  Number.isFinite(_tbSavedSeed) && _tbSavedSeed >= 0 ? _tbSavedSeed : 0,
);
watch(tbComfyuiSeedMode, (val) =>
  sessionStorage.setItem("comfyui_t2i_seed_mode", val),
);
watch(tbComfyuiSeed, (val) =>
  sessionStorage.setItem("comfyui_t2i_seed", String(val)),
);

const tbValidComfyWorkflows = computed(() => {
  if (!Array.isArray(tbComfyuiWorkflows.value)) return [];
  return tbComfyuiWorkflows.value.filter((w) => w?.workflow_type === "t2i");
});
const tbCanRunComfyWorkflow = computed(() => !!tbComfyuiSelectedWorkflow.value);

watch(tbComfyuiMenuOpen, async (isOpen) => {
  if (!isOpen) return;
  tbComfyuiRunError.value = "";
  await tbFetchComfyWorkflows();
  if (!tbComfyuiSelectedWorkflow.value && tbValidComfyWorkflows.value.length) {
    tbComfyuiSelectedWorkflow.value = String(
      tbValidComfyWorkflows.value[0].name,
    );
  }
});

async function tbFetchComfyWorkflows() {
  if (tbComfyuiWorkflowLoading.value) return;
  const url = gb?.backendUrl?.value ?? props.backendUrl;
  if (!url) return;
  tbComfyuiWorkflowLoading.value = true;
  tbComfyuiWorkflowError.value = "";
  try {
    const res = await apiClient.get(`${url}/comfyui/workflows`);
    const workflows = res.data?.workflows;
    tbComfyuiWorkflows.value = Array.isArray(workflows) ? workflows : [];
  } catch (err) {
    tbComfyuiWorkflowError.value =
      err?.response?.data?.detail || err?.message || String(err);
    tbComfyuiWorkflows.value = [];
  } finally {
    tbComfyuiWorkflowLoading.value = false;
  }
}

function tbRunComfyuiOnGrid() {
  if (!tbCanRunComfyWorkflow.value) return;
  tb?.comfyuiRunGrid?.({
    workflowName: tbComfyuiSelectedWorkflow.value,
    caption: tbComfyuiCaption.value || "",
    seedMode: tbComfyuiSeedMode.value,
    seed: tbComfyuiSeed.value,
  });
  tbComfyuiMenuOpen.value = false;
}

// ── Grid Bar: Sort ─────────────────────────────────────────────────────────────
const SIMILARITY_SORT_KEY_GB = "CHARACTER_LIKENESS";
const LIKENESS_GROUPS_SORT_KEY_GB = "LIKENESS_GROUPS";
const gbSortMenuOpen = ref(false);
const gbPendingSortSelection = ref(null);

const gbSortModel = computed({
  get: () => gb?.selectedSort?.value ?? "",
  set: (value) =>
    gb?.updateSort?.({
      sort: value != null ? String(value) : "",
      descending: gb?.selectedDescending?.value ?? true,
    }),
});

const gbDescendingModel = computed({
  get: () => gb?.selectedDescending?.value ?? true,
  set: (value) =>
    gb?.updateSort?.({ sort: gbSortModel.value, descending: Boolean(value) }),
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
    Array.isArray(gb?.similarityCharacterOptions?.value) &&
    gb.similarityCharacterOptions.value.length > 0,
);

const gbSimilarityCharacterModel = computed({
  get: () => gb?.selectedSimilarityCharacter?.value ?? null,
  set: (value) => gb?.updateSimilarityCharacter?.(value ?? null),
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
    const v = gb?.stackThreshold?.value;
    if (v == null || v === "") return "0.92";
    const parsed = parseFloat(String(v));
    if (!Number.isFinite(parsed) || parsed <= 0) return "0.92";
    return String(v);
  },
  set: (value) => gb?.updateStackThreshold?.(value),
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
  if (gb?.isSearchActive?.value) return;
  gbPendingSortSelection.value = sortValue != null ? String(sortValue) : "";
  if (!gbSortRequiresParameter(gbPendingSortSelection.value)) {
    gbCommitSortSelection(gbPendingSortSelection.value);
    gbSortMenuOpen.value = false;
  }
}

function gbHandleSimilarityOptionClick() {
  if (
    String(gbSortMenuModel.value || "").toUpperCase() === SIMILARITY_SORT_KEY_GB
  ) {
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
  (gb?.sortOptions?.value ?? []).find((opt) => opt.value === gbSortModel.value),
);
const gbSelectedSimilarityOption = computed(() =>
  (gb?.similarityCharacterOptions?.value ?? []).find(
    (opt) => opt.value === gbSimilarityCharacterModel.value,
  ),
);
const gbSelectedStackThresholdOption = computed(() =>
  gbStackThresholdOptions.find(
    (opt) => opt.value === gbStackThresholdModel.value,
  ),
);

const gbSortButtonLabel = computed(() => {
  if (gb?.isSearchActive?.value) return "Search relevance";
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
  if (gb?.isSearchActive?.value) return "Search relevance";
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
  if (gb?.isSearchActive?.value) return "mdi-magnify";
  return gbGetSortIcon(gbSortModel.value);
});

// ── Grid Bar: Filter ───────────────────────────────────────────────────────────
const gbFilterMenuOpen = ref(false);

const gbMediaTypeFilter = computed({
  get: () => gb?.mediaTypeFilter?.value ?? "all",
  set: (v) => {
    if (gb?.mediaTypeFilter) gb.mediaTypeFilter.value = v;
  },
});
const gbMinScoreFilter = computed({
  get: () => gb?.minScoreFilter?.value ?? null,
  set: (v) => {
    if (gb?.minScoreFilter) gb.minScoreFilter.value = v ?? null;
  },
});
const gbMaxScoreFilter = computed({
  get: () => gb?.maxScoreFilter?.value ?? null,
  set: (v) => {
    if (gb?.maxScoreFilter) gb.maxScoreFilter.value = v ?? null;
  },
});
const gbFaceBboxFilter = computed({
  get: () => gb?.faceBboxFilter?.value ?? null,
  set: (v) => {
    if (gb?.faceBboxFilter) gb.faceBboxFilter.value = v;
  },
});
const gbTagFilter = computed({
  get: () => gb?.tagFilter?.value ?? [],
  set: (v) => {
    if (gb?.tagFilter) gb.tagFilter.value = v ?? [];
  },
});
const gbTagRejectedFilter = computed({
  get: () => gb?.tagRejectedFilter?.value ?? [],
  set: (v) => {
    if (gb?.tagRejectedFilter) gb.tagRejectedFilter.value = v ?? [];
  },
});
const gbTagConfidenceAboveFilter = computed({
  get: () => gb?.tagConfidenceAboveFilter?.value ?? [],
  set: (v) => {
    if (gb?.tagConfidenceAboveFilter)
      gb.tagConfidenceAboveFilter.value = v ?? [];
  },
});
const gbTagConfidenceBelowFilter = computed({
  get: () => gb?.tagConfidenceBelowFilter?.value ?? [],
  set: (v) => {
    if (gb?.tagConfidenceBelowFilter)
      gb.tagConfidenceBelowFilter.value = v ?? [];
  },
});
const gbSharedOnlyFilter = computed({
  get: () => gb?.sharedOnlyFilter?.value ?? false,
  set: (v) => {
    if (gb?.sharedOnlyFilter) gb.sharedOnlyFilter.value = Boolean(v);
  },
});
const gbUnassignedOnlyFilter = computed({
  get: () => gb?.unassignedOnlyFilter?.value ?? false,
  set: (v) => {
    if (gb?.unassignedOnlyFilter) gb.unassignedOnlyFilter.value = Boolean(v);
  },
});
const isAllPicturesView = computed(
  () =>
    String(props.selectedCharacter ?? "") === String(props.allPicturesId ?? ""),
);
const gbComfyuiModelFilter = computed({
  get: () => gb?.comfyuiModelFilter?.value ?? [],
  set: (v) => {
    if (gb?.comfyuiModelFilter) gb.comfyuiModelFilter.value = v ?? [];
  },
});
const gbComfyuiLoraFilter = computed({
  get: () => gb?.comfyuiLoraFilter?.value ?? [],
  set: (v) => {
    if (gb?.comfyuiLoraFilter) gb.comfyuiLoraFilter.value = v ?? [];
  },
});

const gbIsFilterActive = computed(
  () =>
    (gb?.mediaTypeFilter?.value ?? "all") !== "all" ||
    (gb?.minScoreFilter?.value ?? null) != null ||
    (gb?.maxScoreFilter?.value ?? null) != null ||
    (gb?.smartScoreBucketFilter?.value ?? null) != null ||
    (gb?.resolutionBucketFilter?.value ?? null) != null ||
    (Array.isArray(gb?.tagFilter?.value) && gb.tagFilter.value.length > 0) ||
    (Array.isArray(gb?.tagRejectedFilter?.value) &&
      gb.tagRejectedFilter.value.length > 0) ||
    (Array.isArray(gb?.tagConfidenceAboveFilter?.value) &&
      gb.tagConfidenceAboveFilter.value.length > 0) ||
    (Array.isArray(gb?.tagConfidenceBelowFilter?.value) &&
      gb.tagConfidenceBelowFilter.value.length > 0) ||
    (Array.isArray(gb?.comfyuiModelFilter?.value) &&
      gb.comfyuiModelFilter.value.length > 0) ||
    (Array.isArray(gb?.comfyuiLoraFilter?.value) &&
      gb.comfyuiLoraFilter.value.length > 0) ||
    (gb?.faceBboxFilter?.value ?? null) != null ||
    (gb?.sharedOnlyFilter?.value ?? false) ||
    (gb?.unassignedOnlyFilter?.value ?? false),
);

const gbActiveFilterCount = computed(() => {
  let count = 0;
  if ((gb?.mediaTypeFilter?.value ?? "all") !== "all") count++;
  if ((gb?.minScoreFilter?.value ?? null) != null) count++;
  if ((gb?.maxScoreFilter?.value ?? null) != null) count++;
  if ((gb?.smartScoreBucketFilter?.value ?? null) != null) count++;
  if ((gb?.resolutionBucketFilter?.value ?? null) != null) count++;
  if (Array.isArray(gb?.tagFilter?.value)) count += gb.tagFilter.value.length;
  if (Array.isArray(gb?.tagRejectedFilter?.value))
    count += gb.tagRejectedFilter.value.length;
  if (Array.isArray(gb?.tagConfidenceAboveFilter?.value))
    count += gb.tagConfidenceAboveFilter.value.length;
  if (Array.isArray(gb?.tagConfidenceBelowFilter?.value))
    count += gb.tagConfidenceBelowFilter.value.length;
  if (Array.isArray(gb?.comfyuiModelFilter?.value))
    count += gb.comfyuiModelFilter.value.length;
  if (Array.isArray(gb?.comfyuiLoraFilter?.value))
    count += gb.comfyuiLoraFilter.value.length;
  if ((gb?.faceBboxFilter?.value ?? null) != null) count++;
  if (gb?.sharedOnlyFilter?.value) count++;
  if (gb?.unassignedOnlyFilter?.value) count++;
  return count;
});

function gbClearAllFilters() {
  if (!gb) return;
  if (gb.mediaTypeFilter) gb.mediaTypeFilter.value = "all";
  if (gb.minScoreFilter) gb.minScoreFilter.value = null;
  if (gb.maxScoreFilter) gb.maxScoreFilter.value = null;
  if (gb.smartScoreBucketFilter) gb.smartScoreBucketFilter.value = null;
  if (gb.resolutionBucketFilter) gb.resolutionBucketFilter.value = null;
  if (gb.faceBboxFilter) gb.faceBboxFilter.value = null;
  if (gb.tagFilter) gb.tagFilter.value = [];
  if (gb.tagRejectedFilter) gb.tagRejectedFilter.value = [];
  if (gb.tagConfidenceAboveFilter) gb.tagConfidenceAboveFilter.value = [];
  if (gb.tagConfidenceBelowFilter) gb.tagConfidenceBelowFilter.value = [];
  if (gb.comfyuiModelFilter) gb.comfyuiModelFilter.value = [];
  if (gb.comfyuiLoraFilter) gb.comfyuiLoraFilter.value = [];
  if (gb.sharedOnlyFilter) gb.sharedOnlyFilter.value = false;
  if (gb.unassignedOnlyFilter) gb.unassignedOnlyFilter.value = false;
}

const gbMediaTypeOptions = [
  { value: "all", icon: "mdi-multimedia", title: "Show all media" },
  { value: "images", icon: "mdi-image", title: "Show images only" },
  { value: "videos", icon: "mdi-video", title: "Show videos only" },
];

function gbSetMediaTypeFilter(value) {
  gbMediaTypeFilter.value = value;
}

const gbFaceBboxFilterOptions = [
  { value: null, icon: "mdi-all-inclusive", title: "All pictures" },
  { value: "with_face", icon: "mdi-face-man", title: "With detected face" },
  {
    value: "without_face",
    icon: "mdi-face-man",
    title: "Without detected face",
  },
];

function gbSetFaceBboxFilter(value) {
  gbFaceBboxFilter.value =
    gbFaceBboxFilter.value === value && value !== null ? null : value;
}

function gbSetMinScore(n) {
  const newMin = gbMinScoreFilter.value === n ? null : n;
  gbMinScoreFilter.value = newMin;
  if (
    newMin !== null &&
    gbMaxScoreFilter.value !== null &&
    newMin > gbMaxScoreFilter.value
  ) {
    gbMaxScoreFilter.value = newMin;
  }
}

function gbSetMaxScore(n) {
  const newMax = gbMaxScoreFilter.value === n ? null : n;
  gbMaxScoreFilter.value = newMax;
  if (
    newMax !== null &&
    gbMinScoreFilter.value !== null &&
    newMax < gbMinScoreFilter.value
  ) {
    gbMinScoreFilter.value = newMax;
  }
}

const gbTagFilterInput = ref("");
const gbTagFilterSuggestions = ref([]);
const gbTagFilterHoverEnabled = ref(false);
const gbTagFilterIndex = ref(-1);

async function gbLoadTagFilterSuggestions(input) {
  if (!input || input.length < 1) {
    gbTagFilterSuggestions.value = [];
    return;
  }
  try {
    const res = await apiClient.get(`${gb?.backendUrl ?? ""}/tags`);
    const all = Array.isArray(res.data) ? res.data : [];
    const q = input.toLowerCase();
    gbTagFilterSuggestions.value = all
      .filter(
        (t) =>
          t.tag.toLowerCase().includes(q) &&
          !gbTagFilter.value.includes(t.tag) &&
          !gbTagRejectedFilter.value.includes(t.tag),
      )
      .slice(0, 8)
      .map((t) => t.tag);
  } catch {
    gbTagFilterSuggestions.value = [];
  }
}

watch(gbTagFilterInput, (val) => {
  gbTagFilterIndex.value = -1;
  gbLoadTagFilterSuggestions(val);
});

function gbAddTagFilter(tag) {
  if (
    tag &&
    !gbTagFilter.value.includes(tag) &&
    !gbTagRejectedFilter.value.includes(tag)
  ) {
    gbTagFilter.value = [...gbTagFilter.value, tag];
  }
  gbTagFilterInput.value = "";
  gbTagFilterSuggestions.value = [];
  gbTagFilterIndex.value = -1;
}

function gbRemoveTagFilter(tag) {
  gbTagFilter.value = gbTagFilter.value.filter((t) => t !== tag);
  gbTagRejectedFilter.value = gbTagRejectedFilter.value.filter(
    (t) => t !== tag,
  );
}

function gbToggleTagRejected(tag) {
  if (gbTagFilter.value.includes(tag)) {
    gbTagFilter.value = gbTagFilter.value.filter((t) => t !== tag);
    gbTagRejectedFilter.value = [...gbTagRejectedFilter.value, tag];
  } else {
    gbTagRejectedFilter.value = gbTagRejectedFilter.value.filter(
      (t) => t !== tag,
    );
    gbTagFilter.value = [...gbTagFilter.value, tag];
  }
}

const gbConfidenceTagInput = ref("");
const gbConfidenceTagSuggestions = ref([]);
const gbConfidenceTagHoverEnabled = ref(false);
const gbConfidenceTagIndex = ref(-1);
const gbConfidenceThreshold = ref(0.7);
const gbConfidenceMode = ref("above");
let gbSuppressConfidenceSuggestionLoad = false;

async function gbLoadConfidenceTagSuggestions(input) {
  if (!input || input.length < 1) {
    gbConfidenceTagSuggestions.value = [];
    return;
  }
  try {
    const res = await apiClient.get(`${gb?.backendUrl ?? ""}/tags`);
    const all = Array.isArray(res.data) ? res.data : [];
    const q = input.toLowerCase();
    gbConfidenceTagSuggestions.value = all
      .filter((t) => t.tag.toLowerCase().includes(q))
      .slice(0, 8)
      .map((t) => t.tag);
  } catch {
    gbConfidenceTagSuggestions.value = [];
  }
}

watch(gbConfidenceTagInput, (val) => {
  if (gbSuppressConfidenceSuggestionLoad) {
    gbSuppressConfidenceSuggestionLoad = false;
    return;
  }
  gbConfidenceTagIndex.value = -1;
  gbLoadConfidenceTagSuggestions(val);
});

function gbSelectConfidenceTagSuggestion(tag) {
  gbSuppressConfidenceSuggestionLoad = true;
  gbConfidenceTagInput.value = tag;
  gbConfidenceTagSuggestions.value = [];
  gbConfidenceTagIndex.value = -1;
}

function gbAddConfidenceFilter(tagArg) {
  const tag = (tagArg ?? gbConfidenceTagInput.value).trim();
  if (!tag) return;
  const entry = `${tag}:${gbConfidenceThreshold.value.toFixed(2)}`;
  if (gbConfidenceMode.value === "above") {
    if (!gbTagConfidenceAboveFilter.value.includes(entry))
      gbTagConfidenceAboveFilter.value = [
        ...gbTagConfidenceAboveFilter.value,
        entry,
      ];
  } else {
    if (!gbTagConfidenceBelowFilter.value.includes(entry))
      gbTagConfidenceBelowFilter.value = [
        ...gbTagConfidenceBelowFilter.value,
        entry,
      ];
  }
  gbConfidenceTagInput.value = "";
  gbConfidenceTagSuggestions.value = [];
  gbConfidenceTagIndex.value = -1;
}

function gbRemoveConfidenceAboveFilter(entry) {
  gbTagConfidenceAboveFilter.value = gbTagConfidenceAboveFilter.value.filter(
    (e) => e !== entry,
  );
}

function gbRemoveConfidenceBelowFilter(entry) {
  gbTagConfidenceBelowFilter.value = gbTagConfidenceBelowFilter.value.filter(
    (e) => e !== entry,
  );
}

function gbConfidenceEntryLabel(entry) {
  const [tag, threshold] = entry.split(":");
  return `${tag} ${Math.round(parseFloat(threshold) * 100)}%`;
}

const gbComfyuiModelOptions = ref([]);
const gbComfyuiLoraOptions = ref([]);
const gbComfyuiFilterExpanded = ref(false);

watch(gbFilterMenuOpen, async (isOpen) => {
  if (isOpen) {
    const backendUrl = gb?.backendUrl ?? "";
    if (
      backendUrl &&
      !gbComfyuiModelOptions.value.length &&
      !gbComfyuiLoraOptions.value.length
    ) {
      try {
        const [mRes, lRes] = await Promise.all([
          apiClient.get(`${backendUrl}/pictures/comfyui_models`),
          apiClient.get(`${backendUrl}/pictures/comfyui_loras`),
        ]);
        gbComfyuiModelOptions.value = Array.isArray(mRes.data) ? mRes.data : [];
        gbComfyuiLoraOptions.value = Array.isArray(lRes.data) ? lRes.data : [];
      } catch {}
    }
  } else {
    gbTagFilterInput.value = "";
    gbTagFilterSuggestions.value = [];
  }
});

// ── Grid Bar: View ─────────────────────────────────────────────────────────────
const gbViewMenuOpen = ref(false);
const gbPendingColumns = ref(gb?.columns?.value ?? 4);

watch(
  () => gb?.columns?.value,
  (v) => {
    if (!gbViewMenuOpen.value) gbPendingColumns.value = v ?? 4;
  },
);

watch(gbViewMenuOpen, (isOpen) => {
  if (isOpen) gbPendingColumns.value = gb?.columns?.value ?? 4;
});

function gbCommitColumns() {
  if (gb?.columns) gb.columns.value = gbPendingColumns.value;
}

const gbCompactModeModel = computed({
  get: () => gb?.compactMode?.value ?? false,
  set: (v) => {
    if (gb?.compactMode) gb.compactMode.value = Boolean(v);
  },
});
const gbShowFaceBboxesModel = computed({
  get: () => gb?.showFaceBboxes?.value ?? false,
  set: (v) => {
    if (gb?.showFaceBboxes) gb.showFaceBboxes.value = Boolean(v);
  },
});
const gbShowProblemIconModel = computed({
  get: () => gb?.showProblemIcon?.value ?? true,
  set: (v) => {
    if (gb?.showProblemIcon) gb.showProblemIcon.value = Boolean(v);
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
  const total = Number(gb?.stackTotalCount?.value || 0);
  const expanded = Number(gb?.stackExpandedCount?.value || 0);
  return total <= 0 || expanded >= total;
});

const gbCollapseAllStacksDisabled = computed(
  () => Number(gb?.stackExpandedCount?.value || 0) <= 0,
);

// ── Tag-panel mini-grid ───────────────────────────────────────────────────────
const previewImages = computed(() => {
  const ids = new Set(
    (Array.isArray(props.selectedImageIds) ? props.selectedImageIds : []).map(
      (id) => String(id),
    ),
  );
  if (!ids.size) return [];
  const candidates = (
    Array.isArray(props.allGridImages) ? props.allGridImages : []
  )
    .filter((img) => img && img.id != null && ids.has(String(img.id)))
    .slice(0, MAX_PREVIEW_IMAGES);
  const useFullRes = candidates.length <= 2;
  return candidates.map((img) => {
    const ext = img.format ? img.format.toLowerCase() : null;
    const fullUrl =
      useFullRes && ext && props.backendUrl
        ? `${props.backendUrl}/pictures/${img.id}.${ext}`
        : img.thumbnail || null;
    return { ...img, fullUrl };
  });
});

// 1 image → 1 col (full width), 2 images → 1 col (stacked), 3+ → 2 cols
const previewColumns = computed(() => (previewImages.value.length > 2 ? 2 : 1));

const isScrapheapView = computed(() => {
  const scrapheapId = String(
    props.scrapheapPicturesId || "SCRAPHEAP",
  ).toUpperCase();
  const selected = String(props.selectedCharacter || "").toUpperCase();
  return selected === scrapheapId;
});

const normalizedSelectedCharacter = computed(() => {
  const raw = String(props.selectedCharacter ?? "")
    .trim()
    .toUpperCase();
  if (!raw || raw === "NULL" || raw === "UNDEFINED") return "";
  return raw;
});

const hasSetSelectionContext = computed(() => {
  const setId = Number(props.selectedSet);
  return Number.isFinite(setId) && setId > 0;
});

const showRemoveButton = computed(() => {
  if (props.selectedCount <= 0) return false;
  return isScrapheapView.value;
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
  return (
    props.selectedCount > 0 && props.selectedSort === LIKENESS_GROUPS_SORT_KEY
  );
});

const showAnyStackAction = computed(() => {
  if (isScrapheapView.value || isReadOnly.value) return false;
  return (
    showRemoveStackButton.value ||
    (props.selectedCount > 1 && !showRemoveStackButton.value) ||
    showUnstackMultipleButton.value ||
    showGroupStackButton.value
  );
});

const showRemoveStackButton = computed(() => {
  if (isScrapheapView.value) return false;
  return props.showRemoveFromStack === true;
});

// True when selected images span multiple stacks (or one stack mixed with
// non-stacked images) — the single-stack case is handled by showRemoveStackButton.
const showUnstackMultipleButton = computed(() => {
  if (isScrapheapView.value) return false;
  if (showRemoveStackButton.value) return false;
  return props.selectedMultipleStackIds.length > 0;
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
const fetchedPredictionData = ref([]);
const predictionLoading = ref(false);
const predictionAcceptanceThresholdSB = ref(0.95);
const labelThresholdsSB = ref({});
const rejectedTagsCollapsedSB = ref(loadRejectedTagsCollapsedSB());
const penalisedTagsSB = ref(new Set());
let penalisedTagsFetchedAt = 0;

function loadRejectedTagsCollapsedSB() {
  if (typeof window === "undefined") return true;
  const raw = window.sessionStorage?.getItem(
    "pixlstash:selectionBar:rejectedTagsCollapsed",
  );
  if (raw == null) return true;
  return raw === "1";
}

function persistRejectedTagsCollapsedSB(value) {
  if (typeof window === "undefined") return;
  window.sessionStorage?.setItem(
    "pixlstash:selectionBar:rejectedTagsCollapsed",
    value ? "1" : "0",
  );
}

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

async function fetchSelectedImagePredictions() {
  const ids = (
    Array.isArray(props.selectedImageIds) ? props.selectedImageIds : []
  )
    .map((id) => Number(id))
    .filter((id) => Number.isFinite(id) && id > 0)
    .slice(0, MAX_TAG_FETCH);
  if (!ids.length) {
    fetchedPredictionData.value = [];
    return;
  }
  predictionLoading.value = true;
  try {
    const results = await Promise.all(
      ids.map((id) =>
        apiClient
          .get(
            `${props.backendUrl}/pictures/${id}/tag_predictions?status=REJECTED&include_meta=1`,
          )
          .then((r) => {
            const payload = r.data;
            const predictions = Array.isArray(payload)
              ? payload
              : Array.isArray(payload?.tag_predictions)
                ? payload.tag_predictions
                : [];
            const threshold = Number(payload?.meta?.acceptance_threshold);
            if (Number.isFinite(threshold) && threshold > 0 && threshold <= 1) {
              predictionAcceptanceThresholdSB.value = threshold;
            }
            labelThresholdsSB.value = payload?.meta?.label_thresholds || {};
            return { id, predictions };
          })
          .catch(() => ({ id, predictions: [] })),
      ),
    );
    fetchedPredictionData.value = results;
  } catch {
    fetchedPredictionData.value = [];
  } finally {
    predictionLoading.value = false;
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

// Prediction tags that appear on >= predMinCoverage images, not already confirmed on all
const predMinCoverage = ref(1);

const aggregatedPredictions = computed(() => {
  if (!fetchedPredictionData.value.length) return [];
  const confirmedAll = new Set(
    tagsOnAll.value.map((t) => t.name.toLowerCase()),
  );
  const freq = new Map();
  for (const { id, predictions } of fetchedPredictionData.value) {
    for (const p of predictions) {
      const key = p.tag.toLowerCase();
      if (confirmedAll.has(key)) continue;
      if (!freq.has(key))
        freq.set(key, { tag: p.tag, count: 0, totalConf: 0, ids: [] });
      const e = freq.get(key);
      e.count++;
      e.totalConf += p.confidence;
      e.ids.push(id);
    }
  }
  return [...freq.values()]
    .filter((e) => e.count >= predMinCoverage.value)
    .map((e) => {
      const avgConf = e.totalConf / e.count;
      const perLabel = labelThresholdsSB.value[e.tag];
      const threshold =
        typeof perLabel === "number" && Number.isFinite(perLabel)
          ? perLabel
          : Number(predictionAcceptanceThresholdSB.value) || 0.95;
      const avgNeeded = Math.max(0, threshold - avgConf);
      return { ...e, avgConf, avgNeeded };
    })
    .sort((a, b) => b.count - a.count || b.avgConf - a.avgConf);
});

const predActionLoading = ref([]);

async function confirmPredictionOnAll(predEntry) {
  if (predActionLoading.value.includes(predEntry.tag)) return;
  predActionLoading.value = [...predActionLoading.value, predEntry.tag];
  tagError.value = "";
  try {
    await Promise.all(
      predEntry.ids.map((id) =>
        apiClient.post(
          `${props.backendUrl}/pictures/${id}/tag_predictions/${encodeURIComponent(predEntry.tag)}/confirm`,
        ),
      ),
    );
    emit("tags-applied", {
      tag: predEntry.tag,
      pictureIds: predEntry.ids,
      action: "add",
    });
    await Promise.all([
      fetchSelectedImageTags(),
      fetchSelectedImagePredictions(),
    ]);
  } catch (err) {
    tagError.value = err?.response?.data?.detail || err?.message || String(err);
  } finally {
    predActionLoading.value = predActionLoading.value.filter(
      (n) => n !== predEntry.tag,
    );
  }
}

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

  // Build lookup of average rejected-prediction confidence across selected images
  const rejectedConf = new Map();
  for (const p of aggregatedPredictions.value) {
    if (typeof p.avgConf === "number") {
      rejectedConf.set(p.tag.trim().toLowerCase(), p.avgConf);
    }
  }

  return allTagsSB.value
    .filter((item) => item.tag.toLowerCase().startsWith(query))
    .sort((a, b) => {
      const aConf = rejectedConf.get(a.tag.toLowerCase()) ?? -1;
      const bConf = rejectedConf.get(b.tag.toLowerCase()) ?? -1;
      // Rejected predictions first, sorted by avg confidence desc
      if (aConf !== bConf) return bConf - aConf;
      // Then by global usage count desc (original order)
      return (b.count || 0) - (a.count || 0);
    })
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
    fetchedPredictionData.value = [];
    tagDataCapped.value = false;
    tagMinCoverage.value = 1;
    predMinCoverage.value = 1;
    return;
  }
  // Focus the input as soon as the menu is rendered so keystrokes typed
  // immediately after pressing T are captured, rather than waiting for all
  // fetch calls to complete before the input receives focus.
  await nextTick();
  tagInputRef.value?.focus();
  await Promise.all([
    fetchTagsSB(),
    fetchPenalisedTagsSB(),
    fetchSelectedImageTags(),
    fetchSelectedImagePredictions(),
  ]);
});

watch(rejectedTagsCollapsedSB, (value) => {
  persistRejectedTagsCollapsedSB(Boolean(value));
});

async function fetchPenalisedTagsSB() {
  if (isReadOnly.value) return;
  const now = Date.now();
  if (now - penalisedTagsFetchedAt < 60_000) return;
  try {
    const res = await apiClient.get("/users/me/config");
    let list = [];
    if (Array.isArray(res.data?.smart_score_penalised_tags)) {
      list = res.data.smart_score_penalised_tags;
    } else if (
      res.data?.smart_score_penalised_tags &&
      typeof res.data.smart_score_penalised_tags === "object"
    ) {
      list = Object.keys(res.data.smart_score_penalised_tags);
    }
    penalisedTagsSB.value = new Set(
      list
        .map((t) =>
          String(t || "")
            .trim()
            .toLowerCase(),
        )
        .filter(Boolean),
    );
    penalisedTagsFetchedAt = now;
  } catch {
    // non-critical
  }
}

function isPenalisedTagSB(name) {
  return penalisedTagsSB.value.has(
    String(name || "")
      .trim()
      .toLowerCase(),
  );
}

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
    event.preventDefault();
    event.stopPropagation();
    if (typeof event.stopImmediatePropagation === "function") {
      event.stopImmediatePropagation();
    }
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
.selection-bar-overlay {
  position: absolute !important;
  left: 0;
  top: 0;
  width: 100%;
  z-index: 100;
  background: rgba(var(--v-theme-background), 0.95);
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
.selection-bar-right {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
  flex-shrink: 0;
}

.selection-ctx-bar {
  display: flex;
  align-items: center;
  gap: 6px;
}

@media (max-width: 989px) {
  .selection-ctx-bar {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    height: 64px;
    background: rgba(var(--v-theme-background), 0.97);
    border-top: 1px solid rgba(var(--v-theme-on-background), 0.08);
    border-radius: 16px 16px 0 0;
    z-index: 200;
    justify-content: center;
    gap: 20px;
    box-shadow: 0 -4px 20px rgba(0, 0, 0, 0.18);
    backdrop-filter: blur(8px);
    transform: translateY(110%);
    transition: transform 0.25s ease;
  }

  .selection-ctx-bar.selection-ctx-bar--active {
    transform: translateY(0);
  }

  .selection-ctx-bar .clear-btn,
  .selection-ctx-bar .delete-btn {
    width: 52px;
    height: 52px;
  }

  .selection-ctx-bar .stack-btn {
    height: 52px;
    padding: 0 16px;
  }
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
.stack-toggle-btn {
  min-width: 5.5rem;
  justify-content: center;
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

/* Selection ▾ dropdown panel — styled like ImageGridContextMenu */
.selection-menu-panel {
  min-width: 160px;
  background: rgba(var(--v-theme-surface), 0.98);
  color: rgb(var(--v-theme-on-surface));
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  border-radius: 6px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.3);
  padding: 4px 0;
}

.ctx-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 6px 14px;
  background: none;
  border: none;
  color: rgb(var(--v-theme-on-surface));
  font-size: 0.85rem;
  cursor: pointer;
  text-align: left;
  white-space: nowrap;
}

.ctx-item:hover:not(:disabled) {
  background: rgba(var(--v-theme-on-surface), 0.08);
}

.ctx-item:disabled {
  opacity: 0.4;
  cursor: default;
}

.ctx-item--danger {
  color: rgb(var(--v-theme-error));
}

.ctx-icon {
  flex-shrink: 0;
  opacity: 0.8;
}

.ctx-sep {
  height: 1px;
  margin: 4px 0;
  background: rgba(var(--v-theme-on-surface), 0.12);
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

.tag-current-label--clickable {
  margin-bottom: 4px;
}

.tag-current-toggle {
  border: none;
  background: transparent;
  color: inherit;
  font: inherit;
  text-transform: inherit;
  letter-spacing: inherit;
  opacity: inherit;
  padding: 0;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.rejected-threshold-label {
  font-size: 0.7rem;
  opacity: 0.85;
  font-weight: 400;
  text-transform: none;
  letter-spacing: 0.01em;
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

.tag-chip--penalised {
  color: rgb(var(--v-theme-error)) !important;
  border-color: rgba(var(--v-theme-error), 0.55) !important;
  background: rgba(var(--v-theme-error), 0.12) !important;
}

.tag-chip--penalised:hover:not(:disabled) {
  background: rgba(var(--v-theme-error), 0.22) !important;
  border-color: rgba(var(--v-theme-error), 0.75) !important;
}

.tag-chip--prediction {
  --pc: clamp(0.25, var(--pred-confidence, 0.6), 1);
  --pm: calc(22% + var(--pc) * 52%);
  background: color-mix(
    in srgb,
    rgba(var(--v-theme-primary), 0.14) var(--pm),
    rgba(var(--v-theme-on-surface), 0.05)
  );
  border: 1px dashed
    color-mix(
      in srgb,
      rgba(var(--v-theme-primary), 0.55) var(--pm),
      rgba(var(--v-theme-on-surface), 0.2)
    );
  color: color-mix(
    in srgb,
    rgba(var(--v-theme-primary), 0.9) var(--pm),
    rgba(var(--v-theme-on-surface), 0.65)
  );
  display: inline-flex;
  align-items: center;
  gap: 3px;
  filter: saturate(0.86) brightness(0.92);
  border-width: 1px;
}

.tag-chip--prediction:hover:not(:disabled) {
  opacity: 1;
  background: rgba(var(--v-theme-primary), 0.14);
  border-style: solid;
  border-color: rgba(var(--v-theme-primary), 0.55);
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

.sb-tag-autocomplete-tab-hint {
  display: inline-block;
  margin-left: 8px;
  padding: 0 4px;
  font-size: 0.55rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  border-radius: 3px;
  background: rgba(var(--v-theme-on-surface), 0.12);
  color: rgba(var(--v-theme-on-surface), 0.45);
  vertical-align: middle;
  line-height: 1.5;
}

/* ── Tag panel two-column layout ── */
.tag-panel-wide {
  width: auto !important;
  max-width: min(96vw, 1280px) !important;
}

.tag-panel-columns {
  display: flex;
  flex-direction: row;
  align-items: stretch;
}

.tag-preview-column {
  flex-shrink: 0;
  border-right: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  /* No padding — images go edge-to-edge; header floats over or is minimal */
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* 1 image: wide, fills panel height */
.tag-preview-column--cols-1 {
  width: 580px;
  min-height: min(72vh, 700px);
  max-height: min(72vh, 700px);
}

/* 2 images stacked */
.tag-preview-column--cols-1.tag-preview-column--stacked {
  width: 540px;
  max-height: min(72vh, 700px);
}

/* 3+ images in 2-column grid — wide enough that each cell is >384px */
.tag-preview-column--cols-2 {
  width: 820px;
  max-height: min(72vh, 700px);
}

.tag-preview-header {
  font-size: 0.68rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  opacity: 0.45;
  padding: 4px 6px 3px;
  flex-shrink: 0;
  background: rgba(var(--v-theme-surface), 0.7);
}

.tag-preview-grid {
  display: grid;
  gap: 2px;
  overflow-y: auto;
  flex: 1;
  min-height: 0;
}

.tag-preview-grid--cols-1 {
  grid-template-columns: 1fr;
}

/* Single image: the one row stretches to fill all available height */
.tag-preview-grid--cols-1:not(.tag-preview-grid--multi) {
  grid-template-rows: 1fr;
}

/* 2 images stacked: each row is an explicit height so scrolling works correctly */
.tag-preview-grid--cols-1.tag-preview-grid--multi {
  grid-auto-rows: 360px;
  align-content: start;
}

.tag-preview-grid--cols-2 {
  grid-template-columns: 1fr 1fr;
  /* explicit row height = ~3/4 of each cell width (820px col, 2px gap → ~409px/cell) */
  grid-auto-rows: 307px;
  align-content: start;
}

.tag-preview-tile {
  overflow: hidden;
  background: rgba(0, 0, 0, 0.3);
}

/* Single-image tile fills all height */
.tag-preview-grid--cols-1:not(.tag-preview-grid--multi) .tag-preview-tile {
  height: 100%;
}

.tag-preview-img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  object-position: center;
  display: block;
}

.tag-preview-img--placeholder {
  aspect-ratio: 1;
  background: rgba(var(--v-theme-on-surface), 0.12);
}

.tag-panel-wide .plugin-menu-body {
  flex: 1;
  min-width: 340px;
}

/* ═══════════════════════════════════════════════════════════════════════════
   Grid Bar – Sort / Filter / View buttons and panels
   ═══════════════════════════════════════════════════════════════════════════ */

/* ── Bar buttons ──────────────────────────────────────────────────────────── */
.bar-split-button {
  display: flex;
  align-items: center;
  border-radius: 4px;
}

.bar-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 0 9px;
  border-radius: 5px;
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
  border-radius: 5px;
  flex-shrink: 0;
}

/* Export panel */
.tb-export-panel {
  padding: 12px 14px;
  min-width: 260px;
  background: rgb(var(--v-theme-background));
  color: rgb(var(--v-theme-on-background));
  border-radius: 8px;
  box-shadow: 2px 2px 16px rgba(0, 0, 0, 0.4);
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.tb-export-title {
  font-size: 1em;
  font-weight: 500;
  padding-bottom: 4px;
}

/* ComfyUI T2I panel */
.tb-comfyui-panel {
  padding: 12px 14px;
  min-width: 260px;
  background: rgb(var(--v-theme-background));
  color: rgb(var(--v-theme-on-background));
  border-radius: 8px;
  box-shadow: 2px 2px 16px rgba(0, 0, 0, 0.4);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.tb-comfyui-header {
  font-size: 1em;
  font-weight: 500;
}

.tb-comfyui-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.tb-comfyui-label {
  font-size: 0.82em;
  font-weight: 500;
  color: rgba(var(--v-theme-on-background), 0.65);
  margin-bottom: 2px;
  display: block;
}

.tb-comfyui-select,
.tb-comfyui-textarea,
.tb-comfyui-seed-input {
  width: 100%;
  background: rgba(var(--v-theme-surface), 0.5);
  border: 1px solid rgba(var(--v-theme-on-background), 0.2);
  border-radius: 4px;
  color: rgb(var(--v-theme-on-background));
  font-family: inherit;
  font-size: 0.88em;
  padding: 4px 8px;
  box-sizing: border-box;
}

.tb-comfyui-seed-row {
  display: flex;
  gap: 6px;
  align-items: center;
  flex-wrap: wrap;
  margin-top: 4px;
}

.tb-comfyui-seed-btn {
  background: rgba(var(--v-theme-surface), 0.3);
  border: 1px solid rgba(var(--v-theme-on-background), 0.2);
  border-radius: 4px;
  color: rgb(var(--v-theme-on-background));
  cursor: pointer;
  padding: 3px 9px;
  font-family: inherit;
  font-size: 0.82em;
  transition: background 0.15s;
}

.tb-comfyui-seed-btn.active {
  background: rgba(var(--v-theme-primary), 0.35);
  border-color: rgba(var(--v-theme-primary), 0.6);
}

.tb-comfyui-seed-input {
  flex: 1;
  min-width: 70px;
}

.tb-comfyui-run-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border-radius: 4px;
  background: rgba(var(--v-theme-primary), 0.25);
  border: 1px solid rgba(var(--v-theme-primary), 0.5);
  color: rgb(var(--v-theme-primary));
  cursor: pointer;
  font-family: inherit;
  font-size: 0.85em;
  font-weight: 500;
  transition: background 0.15s;
}

.tb-comfyui-run-btn:disabled {
  opacity: 0.4;
  cursor: default;
}

.tb-comfyui-note {
  font-size: 0.85em;
  opacity: 0.65;
  padding: 2px 0;
}

.tb-comfyui-error {
  font-size: 0.82em;
  color: rgb(var(--v-theme-error));
  padding: 2px 0;
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
}

.bar-split-menu {
  border-radius: 0 5px 5px 0;
  border-left: 1px solid rgba(var(--v-theme-on-background), 0.18);
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
  background: rgba(var(--v-theme-background), 0.96);
  color: rgb(var(--v-theme-on-background));
  border-radius: 8px;
  box-shadow: 2px 2px 12px rgba(0, 0, 0, 0.4);
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

/* ── Filter panel ─────────────────────────────────────────────────────────── */
.gb-filter-panel {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
  padding: 10px 12px;
  min-width: 280px;
  max-width: 340px;
  background: rgba(var(--v-theme-background), 0.96);
  color: rgb(var(--v-theme-on-background));
  border-radius: 8px;
  box-shadow: 2px 2px 12px rgba(0, 0, 0, 0.4);
}

.gb-filter-panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
}

.gb-filter-panel-title {
  font-size: 1.02em;
  font-weight: 500;
  letter-spacing: 0.02em;
}

.gb-filter-clear-all-btn {
  min-width: 0;
  padding: 0 4px;
  height: 20px;
  font-size: 0.8em;
}

.gb-filter-shared-only-row {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.gb-filter-shared-only-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.85em;
  cursor: pointer;
}

.gb-filter-shared-only-label--right {
  margin-left: auto;
}

.gb-filter-section-label {
  font-size: 0.8em;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  opacity: 0.6;
  margin-top: 4px;
  width: 100%;
}

.gb-filter-section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}

.gb-media-type-toggle {
  display: flex;
  gap: 2px;
  width: 100%;
}

.gb-media-type-button {
  flex: 1 !important;
  min-width: 0 !important;
  padding: 0 4px !important;
  height: 32px !important;
  border-radius: 4px !important;
  opacity: 0.55 !important;
}

.gb-media-type-button--active {
  opacity: 1 !important;
  color: rgb(var(--v-theme-on-primary)) !important;
  background: rgb(var(--v-theme-primary)) !important;
}

.gb-face-no-detection-icon {
  position: relative;
  display: inline-flex;
}

.gb-face-no-detection-icon::after {
  content: "";
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%) rotate(45deg);
  width: 2px;
  height: 18px;
  background: currentColor;
  pointer-events: none;
}

.gb-score-range-section {
  width: 100%;
}

.gb-score-range-headers {
  display: flex;
  justify-content: space-between;
  margin-bottom: 2px;
}

.gb-score-range-header-label {
  font-size: 0.78em;
  opacity: 0.65;
}

.gb-score-range-filter {
  display: flex;
  justify-content: space-between;
}

.gb-score-range-stars {
  display: flex;
  flex-shrink: 0;
}

.gb-score-star-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 1px 2px;
  background: none;
  border: none;
  cursor: pointer;
  color: rgb(var(--v-theme-on-background));
  opacity: 0.7;
  line-height: 1;
}
.gb-score-star-btn:hover {
  opacity: 1;
}

.gb-tag-filter-input-wrap {
  position: relative;
  width: 100%;
}

.gb-tag-filter-input {
  width: 100%;
  padding: 4px 8px;
  border-radius: 4px;
  border: 1px solid rgba(var(--v-theme-on-background), 0.25);
  background: rgba(var(--v-theme-on-background), 0.06);
  color: rgb(var(--v-theme-on-background));
  font-size: 0.85em;
  outline: none;
  box-sizing: border-box;
}

.gb-tag-filter-input:focus {
  border-color: rgb(var(--v-theme-primary));
}

.gb-tag-filter-dropdown {
  position: absolute;
  top: calc(100% + 2px);
  left: 0;
  right: 0;
  z-index: 200;
  background: rgba(var(--v-theme-background), 0.98);
  border: 1px solid rgba(var(--v-theme-on-background), 0.2);
  border-radius: 4px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
  overflow: hidden;
  pointer-events: none;
}

.gb-tag-filter-dropdown--hover-enabled {
  pointer-events: auto;
}

.gb-tag-filter-suggestion {
  display: block;
  width: 100%;
  padding: 5px 10px;
  text-align: left;
  cursor: pointer;
  font-size: 0.85em;
  background: transparent;
  border: none;
  color: rgb(var(--v-theme-on-background));
}

.gb-tag-filter-suggestion--active,
.gb-tag-filter-suggestion:hover {
  background: rgba(var(--v-theme-primary), 0.12);
}

.gb-tag-filter-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  width: 100%;
}

.gb-confidence-filter-row {
  display: flex;
  align-items: center;
  gap: 4px;
  width: 100%;
  flex-wrap: wrap;
}

.gb-confidence-filter-tag-wrap {
  flex: 1;
  min-width: 80px;
}

.gb-confidence-filter-tag-input {
  width: 100%;
}

.gb-confidence-threshold-stepper {
  display: flex;
  align-items: center;
  gap: 2px;
}

.gb-threshold-step-btn {
  width: 20px;
  height: 20px;
  border-radius: 3px;
  border: 1px solid rgba(var(--v-theme-on-background), 0.3);
  background: transparent;
  color: rgb(var(--v-theme-on-background));
  cursor: pointer;
  font-size: 0.9em;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
}

.gb-threshold-step-btn:disabled {
  opacity: 0.35;
  cursor: default;
}

.gb-confidence-threshold-input {
  width: 64px;
  padding: 4px 4px;
  border-radius: 4px;
  border: 1px solid rgba(var(--v-theme-on-background), 0.25);
  background: rgba(var(--v-theme-on-background), 0.06);
  color: rgb(var(--v-theme-on-background));
  font-size: 0.85em;
  text-align: center;
  height: 28px;
  box-sizing: border-box;
}

.gb-confidence-mode-btn {
  min-width: 28px;
  height: 28px;
  border-radius: 4px;
  border: 1px solid rgba(var(--v-theme-on-background), 0.25);
  background: transparent;
  color: rgb(var(--v-theme-on-background));
  cursor: pointer;
  font-size: 0.9em;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 4px;
}

.gb-confidence-add-btn {
  padding: 0 10px;
  border-radius: 4px;
  border: 1px solid rgba(var(--v-theme-primary), 0.5);
  background: rgba(var(--v-theme-primary), 0.12);
  color: rgb(var(--v-theme-primary));
  cursor: pointer;
  font-size: 0.85em;
  height: 28px;
  box-sizing: border-box;
}

.gb-confidence-add-btn:disabled {
  opacity: 0.35;
  cursor: default;
}

.gb-comfyui-section-header {
  width: 100%;
}

/* ── View panel ───────────────────────────────────────────────────────────── */
.gb-view-panel {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
  padding: 8px 12px 12px;
  min-width: 220px;
  background: rgba(var(--v-theme-background), 0.96);
  color: rgb(var(--v-theme-on-background));
  border-radius: 8px;
  box-shadow: 2px 2px 12px rgba(0, 0, 0, 0.4);
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

.bar-btn-apply-label {
  white-space: nowrap;
  font-size: 0.92em;
  flex-shrink: 1;
}

.bar-btn-clear-label {
  white-space: nowrap;
}

.bar-btn-delete-label {
  white-space: nowrap;
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

@container selbar (max-width: 660px) {
  .bar-btn-apply-label {
    display: none;
  }
}

@container selbar (max-width: 580px) {
  .visible-range-pill {
    display: none;
  }
}

@container selbar (max-width: 500px) {
  .bar-btn-clear-label {
    display: none;
  }
}

@container selbar (max-width: 420px) {
  .bar-btn-delete-label {
    display: none;
  }
}
</style>
