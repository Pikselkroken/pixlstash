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
      <div class="toolbar-sort-controls">
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
          v-model="sortMenuOpen"
          :close-on-content-click="false"
          location="top start"
          origin="bottom start"
          transition="scale-transition"
        >
          <template #activator="{ props }">
            <div :class="{ 'toolbar-split-button': !isMobile }">
              <v-btn
                v-if="!isMobile"
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
                class="toolbar-action-btn"
                :class="{
                  'toolbar-sort-activator toolbar-split-menu': !isMobile,
                }"
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
              :model-value="sortMenuModel"
              @update:model-value="handleSortModelUpdate"
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
                  v-if="sortMenuModel === opt.value"
                  size="16"
                  class="toolbar-sort-grid-selected"
                >
                  mdi-circle-medium
                </v-icon>
              </v-btn>
            </v-btn-toggle>

            <div
              v-if="sortMenuModel === SIMILARITY_SORT_KEY"
              class="toolbar-sort-similarity-row"
            >
              <span>Similarity to ...</span>
              <div class="toolbar-similarity-scroll">
                <v-btn-toggle
                  v-model="similarityCharacterModel"
                  class="toolbar-sort-grid"
                  :class="{
                    'toolbar-sort-grid--pending-parameter':
                      isPendingSimilarityParameter,
                  }"
                  :disabled="!hasSimilarityOptions"
                  mandatory
                >
                  <v-btn
                    v-for="opt in similarityCharacterOptions"
                    :key="opt.value"
                    :value="opt.value"
                    class="toolbar-sort-grid-btn"
                    variant="text"
                    @click="handleSimilarityOptionClick(opt.value)"
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
                      :class="{
                        'toolbar-sort-grid-selected--pending':
                          isPendingSimilarityParameter,
                      }"
                    >
                      mdi-circle-medium
                    </v-icon>
                  </v-btn>
                </v-btn-toggle>
              </div>
            </div>
            <div
              v-if="sortMenuModel === LIKENESS_GROUPS_SORT_KEY"
              class="toolbar-sort-similarity-row"
            >
              <span>Group strictness</span>
              <div class="toolbar-similarity-scroll">
                <v-btn-toggle
                  v-model="stackThresholdModel"
                  class="toolbar-sort-grid"
                  :class="{
                    'toolbar-sort-grid--pending-parameter':
                      isPendingStackParameter,
                  }"
                  mandatory
                >
                  <v-btn
                    v-for="opt in stackThresholdOptions"
                    :key="opt.value"
                    :value="opt.value"
                    class="toolbar-sort-grid-btn"
                    variant="text"
                    @click="handleStackThresholdOptionClick(opt.value)"
                  >
                    <span class="toolbar-sort-grid-label">{{ opt.label }}</span>
                    <v-icon
                      v-if="stackThresholdModel === opt.value"
                      size="16"
                      class="toolbar-sort-grid-selected"
                      :class="{
                        'toolbar-sort-grid-selected--pending':
                          isPendingStackParameter,
                      }"
                    >
                      mdi-circle-medium
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
              title="Grid View Options"
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
              color: rgb(var(--v-theme-on-background));
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
              >Grid View Options</span
            >
            <v-slider
              class="toolbar-columns-slider"
              v-model="pendingColumns"
              :min="minColumns"
              :max="maxColumns"
              :step="1"
              vertical
              density="compact"
              style="
                height: var(--toolbar-control-height);
                width: 100%;
                margin-bottom: 0;
                color: rgb(var(--v-theme-on-background));
              "
              hide-details
              track-color="#888"
              thumb-color="primary"
              label="Columns"
              @end="commitColumns"
            />
            <div class="toolbar-stacks-controls">
              <div class="toolbar-stacks-title">Stacks</div>
              <div class="toolbar-stacks-buttons">
                <v-btn
                  class="toolbar-stack-toggle-btn"
                  color="primary"
                  variant="flat"
                  size="small"
                  :disabled="expandAllStacksDisabled"
                  @click="emit('expand-all-stacks')"
                >
                  Expand all
                </v-btn>
                <v-btn
                  class="toolbar-stack-toggle-btn"
                  color="primary"
                  variant="flat"
                  size="small"
                  :disabled="collapseAllStacksDisabled"
                  @click="emit('collapse-all-stacks')"
                >
                  Collapse all
                </v-btn>
              </div>
            </div>
            <v-switch
              v-model="compactModeModel"
              label="Compact mode"
              color="primary"
              density="compact"
              hide-details
            />
          </div>
        </v-menu>
        <v-menu
          v-model="filterMenuOpen"
          :close-on-content-click="false"
          location="top end"
          origin="bottom end"
          transition="scale-transition"
        >
          <template #activator="{ props }">
            <v-btn
              icon
              v-bind="props"
              :color="isFilterActive ? 'primary' : 'undefined'"
              title="Filters"
              class="toolbar-action-btn"
            >
              <v-icon :color="isFilterActive ? 'on-primary' : 'on-background'"
                >mdi-filter</v-icon
              >
            </v-btn>
          </template>
          <div class="toolbar-filter-panel">
            <div class="toolbar-filter-panel-title">Filters</div>
            <div class="toolbar-filter-section-label">Media</div>
            <div
              class="media-type-toggle"
              role="group"
              aria-label="Media type filter"
            >
              <v-btn
                v-for="opt in mediaTypeOptions"
                :key="opt.value"
                class="media-type-button"
                :class="{
                  'media-type-button--active':
                    mediaTypeFilterModel === opt.value,
                }"
                variant="text"
                :title="opt.title"
                :aria-pressed="mediaTypeFilterModel === opt.value"
                @click="setMediaTypeFilter(opt.value)"
              >
                <v-icon size="16">{{ opt.icon }}</v-icon>
              </v-btn>
            </div>
            <div class="toolbar-filter-section-label" style="margin-top: 10px">
              Min Score
            </div>
            <div style="display: flex; align-items: center; gap: 2px">
              <v-btn
                v-for="n in 5"
                :key="n"
                :icon="true"
                size="x-small"
                variant="text"
                :color="
                  minScoreFilterModel != null && n <= minScoreFilterModel
                    ? 'warning'
                    : undefined
                "
                :title="`Show ${n}+ star pictures`"
                @click="
                  minScoreFilterModel = minScoreFilterModel === n ? null : n
                "
              >
                <v-icon size="20">{{
                  minScoreFilterModel != null && n <= minScoreFilterModel
                    ? "mdi-star"
                    : "mdi-star-outline"
                }}</v-icon>
              </v-btn>
              <span
                v-if="minScoreFilterModel != null"
                style="
                  font-size: 0.78em;
                  margin-left: 4px;
                  color: rgb(var(--v-theme-on-background));
                  opacity: 0.8;
                "
                >{{ minScoreFilterModel }}+ stars</span
              >
            </div>
            <div class="toolbar-filter-section-label" style="margin-top: 10px">
              Tags
            </div>
            <div class="tag-filter-input-wrap">
              <input
                v-model="tagFilterInput"
                class="tag-filter-input"
                placeholder="Filter by tag…"
                autocomplete="off"
                @keydown.enter.prevent="
                  tagFilterIndex >= 0 && tagFilterSuggestions.length
                    ? addTagFilter(tagFilterSuggestions[tagFilterIndex])
                    : addTagFilter(tagFilterInput.trim())
                "
                @keydown.tab.prevent="
                  tagFilterSuggestions.length
                    ? addTagFilter(
                        tagFilterSuggestions[
                          tagFilterIndex >= 0 ? tagFilterIndex : 0
                        ],
                      )
                    : addTagFilter(tagFilterInput.trim())
                "
                @keydown.down.prevent="
                  tagFilterIndex = Math.min(
                    tagFilterIndex + 1,
                    tagFilterSuggestions.length - 1,
                  )
                "
                @keydown.up.prevent="
                  tagFilterIndex = Math.max(tagFilterIndex - 1, -1)
                "
                @keydown.escape.prevent="tagFilterSuggestions = []"
              />
              <div
                v-if="tagFilterSuggestions.length"
                class="tag-filter-dropdown"
                :class="{
                  'tag-filter-dropdown--hover-enabled': tagFilterHoverEnabled,
                }"
                @mousemove.once="tagFilterHoverEnabled = true"
              >
                <button
                  v-for="(tag, idx) in tagFilterSuggestions"
                  :key="tag"
                  class="tag-filter-suggestion"
                  :class="{
                    'tag-filter-suggestion--active': idx === tagFilterIndex,
                  }"
                  type="button"
                  @mousedown.prevent="addTagFilter(tag)"
                  @mousemove="tagFilterIndex = idx"
                >
                  {{ tag }}
                </button>
              </div>
            </div>
            <div
              v-if="tagFilterModel.length || tagRejectedFilterModel.length"
              class="tag-filter-chips"
            >
              <button
                v-for="tag in tagFilterModel"
                :key="`confirmed-${tag}`"
                class="tag-chip tag-chip--filter"
                type="button"
                :title="`'${tag}' – click to switch to rejected match`"
                @click.stop="toggleTagRejected(tag)"
              >
                <span class="tag-chip-label">{{ tag }}</span>
                <v-icon
                  size="11"
                  class="tag-chip-close"
                  @click.stop="removeTagFilter(tag)"
                  >mdi-close</v-icon
                >
              </button>
              <button
                v-for="tag in tagRejectedFilterModel"
                :key="`rejected-${tag}`"
                class="tag-chip tag-chip--filter tag-chip--filter-rejected"
                type="button"
                :title="`'${tag}' (rejected) – click to switch to confirmed match`"
                @click.stop="toggleTagRejected(tag)"
              >
                <span class="tag-chip-label">{{ tag }}</span>
                <v-icon
                  size="11"
                  class="tag-chip-close"
                  @click.stop="removeTagFilter(tag)"
                  >mdi-close</v-icon
                >
              </button>
            </div>
            <div class="toolbar-filter-section-label" style="margin-top: 10px">
              Tag confidence
            </div>
            <div class="confidence-filter-row">
              <div class="tag-filter-input-wrap confidence-filter-tag-wrap">
                <input
                  v-model="confidenceTagInput"
                  class="tag-filter-input confidence-filter-tag-input"
                  placeholder="Tag…"
                  autocomplete="off"
                  @keydown.enter.prevent="
                    confidenceTagIndex >= 0 && confidenceTagSuggestions.length
                      ? selectConfidenceTagSuggestion(
                          confidenceTagSuggestions[confidenceTagIndex],
                        )
                      : addConfidenceFilter(confidenceTagInput.trim())
                  "
                  @keydown.tab.prevent="
                    confidenceTagSuggestions.length
                      ? selectConfidenceTagSuggestion(
                          confidenceTagSuggestions[
                            confidenceTagIndex >= 0 ? confidenceTagIndex : 0
                          ],
                        )
                      : undefined
                  "
                  @keydown.down.prevent="
                    confidenceTagIndex = Math.min(
                      confidenceTagIndex + 1,
                      confidenceTagSuggestions.length - 1,
                    )
                  "
                  @keydown.up.prevent="
                    confidenceTagIndex = Math.max(confidenceTagIndex - 1, -1)
                  "
                  @keydown.escape.prevent="confidenceTagSuggestions = []"
                />
                <div
                  v-if="confidenceTagSuggestions.length"
                  class="tag-filter-dropdown"
                  :class="{
                    'tag-filter-dropdown--hover-enabled':
                      confidenceTagHoverEnabled,
                  }"
                  @mousemove.once="confidenceTagHoverEnabled = true"
                >
                  <button
                    v-for="(tag, idx) in confidenceTagSuggestions"
                    :key="tag"
                    class="tag-filter-suggestion"
                    :class="{
                      'tag-filter-suggestion--active':
                        idx === confidenceTagIndex,
                    }"
                    type="button"
                    @mousedown.prevent="selectConfidenceTagSuggestion(tag)"
                    @mousemove="confidenceTagIndex = idx"
                  >
                    {{ tag }}
                  </button>
                </div>
              </div>
              <div class="confidence-threshold-stepper">
                <button
                  class="threshold-step-btn"
                  type="button"
                  tabindex="-1"
                  :disabled="confidenceThreshold <= 0"
                  @click="
                    confidenceThreshold = Math.max(
                      0,
                      +(confidenceThreshold - 0.05).toFixed(2),
                    )
                  "
                >
                  −
                </button>
                <input
                  v-model.number="confidenceThreshold"
                  type="number"
                  min="0"
                  max="1"
                  step="0.05"
                  class="confidence-threshold-input"
                />
                <button
                  class="threshold-step-btn"
                  type="button"
                  tabindex="-1"
                  :disabled="confidenceThreshold >= 1"
                  @click="
                    confidenceThreshold = Math.min(
                      1,
                      +(confidenceThreshold + 0.05).toFixed(2),
                    )
                  "
                >
                  +
                </button>
              </div>
              <button
                class="confidence-mode-btn"
                type="button"
                :title="
                  confidenceMode === 'above'
                    ? 'High confidence, not labelled – click to switch'
                    : 'Low confidence, labelled – click to switch'
                "
                @click="
                  confidenceMode =
                    confidenceMode === 'above' ? 'below' : 'above'
                "
              >
                {{ confidenceMode === "above" ? "≥" : "<" }}
              </button>
              <button
                class="confidence-add-btn"
                type="button"
                :disabled="!confidenceTagInput.trim()"
                @click="addConfidenceFilter()"
              >
                Add
              </button>
            </div>
            <div
              v-if="
                tagConfidenceAboveFilterModel.length ||
                tagConfidenceBelowFilterModel.length
              "
              class="tag-filter-chips"
            >
              <button
                v-for="entry in tagConfidenceAboveFilterModel"
                :key="`ca-${entry}`"
                class="tag-chip tag-chip--filter tag-chip--confidence-above"
                type="button"
                :title="`Prediction ≥${Math.round(parseFloat(entry.split(':')[1]) * 100)}%, not labelled`"
              >
                <span class="tag-chip-label"
                  >≥{{ confidenceEntryLabel(entry) }}</span
                >
                <v-icon
                  size="11"
                  class="tag-chip-close"
                  @click.stop="removeConfidenceAboveFilter(entry)"
                  >mdi-close</v-icon
                >
              </button>
              <button
                v-for="entry in tagConfidenceBelowFilterModel"
                :key="`cb-${entry}`"
                class="tag-chip tag-chip--filter tag-chip--confidence-below"
                type="button"
                :title="`Prediction <${Math.round(parseFloat(entry.split(':')[1]) * 100)}%, labelled`"
              >
                <span class="tag-chip-label"
                  >&lt;{{ confidenceEntryLabel(entry) }}</span
                >
                <v-icon
                  size="11"
                  class="tag-chip-close"
                  @click.stop="removeConfidenceBelowFilter(entry)"
                  >mdi-close</v-icon
                >
              </button>
            </div>
            <template
              v-if="comfyuiModelOptions.length || comfyuiLoraOptions.length"
            >
              <div
                class="toolbar-filter-section-label"
                style="margin-top: 10px"
              >
                ComfyUI
              </div>
              <template v-if="comfyuiModelOptions.length">
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
                    v-if="comfyuiModelFilterModel.length"
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
                    @click="comfyuiModelFilterModel = []"
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
                    v-for="m in comfyuiModelOptions"
                    :key="m"
                    v-model="comfyuiModelFilterModel"
                    :value="m"
                    :label="m.replace(/\.[^/.]+$/, '')"
                    density="compact"
                    hide-details
                    color="primary"
                  />
                </div>
              </template>
              <template v-if="comfyuiLoraOptions.length">
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
                    v-if="comfyuiLoraFilterModel.length"
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
                    @click="comfyuiLoraFilterModel = []"
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
                    v-for="l in comfyuiLoraOptions"
                    :key="l"
                    v-model="comfyuiLoraFilterModel"
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
              title="Image Information Overlays"
              class="toolbar-action-btn"
            >
              <v-icon :color="'on-background'">mdi-face-recognition</v-icon>
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
          v-if="props.comfyuiConfigured"
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
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import { apiClient } from "../utils/apiClient";

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
  showFormat: { type: Boolean, default: true },
  showResolution: { type: Boolean, default: true },
  showProblemIcon: { type: Boolean, default: true },
  showStacks: { type: Boolean, default: true },
  compactMode: { type: Boolean, default: false },
  stackExpandedCount: { type: Number, default: 0 },
  stackTotalCount: { type: Number, default: 0 },
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
  mediaTypeFilter: { type: String, default: "all" },
  comfyuiConfigured: { type: Boolean, default: false },
  comfyuiModelFilter: { type: Array, default: () => [] },
  comfyuiLoraFilter: { type: Array, default: () => [] },
  minScoreFilter: { type: Number, default: null },
  tagFilter: { type: Array, default: () => [] },
  tagRejectedFilter: { type: Array, default: () => [] },
  tagConfidenceAboveFilter: { type: Array, default: () => [] },
  tagConfidenceBelowFilter: { type: Array, default: () => [] },
  sortOptions: { type: Array, default: () => [] },
  selectedSort: { type: String, default: "" },
  selectedDescending: { type: Boolean, default: true },
  similarityCharacterOptions: { type: Array, default: () => [] },
  selectedSimilarityCharacter: { type: [String, Number, null], default: null },
  stackThreshold: { type: [String, Number, null], default: null },
  backendUrl: { type: String, default: "" },
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
  "update:showFormat",
  "update:showResolution",
  "update:showProblemIcon",
  "update:showStacks",
  "update:compactMode",
  "expand-all-stacks",
  "collapse-all-stacks",
  "update:exportType",
  "update:exportCaptionMode",
  "update:exportTagFormat",
  "update:exportResolution",
  "update:exportIncludeCharacterName",
  "update:exportUseOriginalFileNames",
  "update:mediaTypeFilter",
  "update:comfyuiModelFilter",
  "update:comfyuiLoraFilter",
  "update:minScoreFilter",
  "update:tagFilter",
  "update:tagRejectedFilter",
  "update:tagConfidenceAboveFilter",
  "update:tagConfidenceBelowFilter",
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
const filterMenuOpen = ref(false);

const isFilterActive = computed(
  () =>
    props.mediaTypeFilter !== "all" ||
    props.minScoreFilter != null ||
    (Array.isArray(props.tagFilter) && props.tagFilter.length > 0) ||
    (Array.isArray(props.tagRejectedFilter) &&
      props.tagRejectedFilter.length > 0) ||
    (Array.isArray(props.tagConfidenceAboveFilter) &&
      props.tagConfidenceAboveFilter.length > 0) ||
    (Array.isArray(props.tagConfidenceBelowFilter) &&
      props.tagConfidenceBelowFilter.length > 0) ||
    (Array.isArray(props.comfyuiModelFilter) &&
      props.comfyuiModelFilter.length > 0) ||
    (Array.isArray(props.comfyuiLoraFilter) &&
      props.comfyuiLoraFilter.length > 0),
);

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

watch(filterMenuOpen, async (isOpen) => {
  if (isOpen) {
    if (
      props.backendUrl &&
      !comfyuiModelOptions.value.length &&
      !comfyuiLoraOptions.value.length
    ) {
      try {
        const [mRes, lRes] = await Promise.all([
          apiClient.get(`${props.backendUrl}/pictures/comfyui_models`),
          apiClient.get(`${props.backendUrl}/pictures/comfyui_loras`),
        ]);
        comfyuiModelOptions.value = Array.isArray(mRes.data) ? mRes.data : [];
        comfyuiLoraOptions.value = Array.isArray(lRes.data) ? lRes.data : [];
      } catch {}
    }
  } else {
    tagFilterInput.value = "";
    tagFilterSuggestions.value = [];
  }
});

const showStarsModel = computed({
  get: () => props.showStars,
  set: (value) => emit("update:showStars", value),
});

const showFaceBboxesModel = computed({
  get: () => props.showFaceBboxes,
  set: (value) => emit("update:showFaceBboxes", value),
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

const compactModeModel = computed({
  get: () => props.compactMode,
  set: (value) => emit("update:compactMode", value),
});

const expandAllStacksDisabled = computed(() => {
  const total = Number(props.stackTotalCount || 0);
  const expanded = Number(props.stackExpandedCount || 0);
  return total <= 0 || expanded >= total;
});

const collapseAllStacksDisabled = computed(
  () => Number(props.stackExpandedCount || 0) <= 0,
);

const exportTypeModel = computed({
  get: () => props.exportType,
  set: (value) => emit("update:exportType", value),
});

const exportCaptionModeModel = computed({
  get: () => props.exportCaptionMode,
  set: (value) => emit("update:exportCaptionMode", value),
});

const exportTagFormatModel = computed({
  get: () => props.exportTagFormat,
  set: (value) => emit("update:exportTagFormat", value),
});

const exportResolutionModel = computed({
  get: () => props.exportResolution,
  set: (value) => emit("update:exportResolution", value),
});

const exportIncludeCharacterNameModel = computed({
  get: () => props.exportIncludeCharacterName,
  set: (value) => emit("update:exportIncludeCharacterName", value),
});

const exportUseOriginalFileNamesModel = computed({
  get: () => props.exportUseOriginalFileNames,
  set: (value) => emit("update:exportUseOriginalFileNames", value),
});

const mediaTypeFilterModel = computed({
  get: () => props.mediaTypeFilter,
  set: (value) => emit("update:mediaTypeFilter", value),
});

const mediaTypeOptions = [
  { value: "all", icon: "mdi-multimedia", title: "Show all media" },
  { value: "images", icon: "mdi-image", title: "Show images only" },
  { value: "videos", icon: "mdi-video", title: "Show videos only" },
];

function setMediaTypeFilter(value) {
  mediaTypeFilterModel.value = value;
}

const comfyuiModelFilterModel = computed({
  get: () => props.comfyuiModelFilter,
  set: (value) => emit("update:comfyuiModelFilter", value ?? []),
});
const comfyuiLoraFilterModel = computed({
  get: () => props.comfyuiLoraFilter,
  set: (value) => emit("update:comfyuiLoraFilter", value ?? []),
});

const minScoreFilterModel = computed({
  get: () => props.minScoreFilter,
  set: (value) => emit("update:minScoreFilter", value ?? null),
});
const tagFilterModel = computed({
  get: () => props.tagFilter,
  set: (value) => emit("update:tagFilter", value ?? []),
});
const tagRejectedFilterModel = computed({
  get: () => props.tagRejectedFilter,
  set: (value) => emit("update:tagRejectedFilter", value ?? []),
});
const tagConfidenceAboveFilterModel = computed({
  get: () => props.tagConfidenceAboveFilter,
  set: (value) => emit("update:tagConfidenceAboveFilter", value ?? []),
});
const tagConfidenceBelowFilterModel = computed({
  get: () => props.tagConfidenceBelowFilter,
  set: (value) => emit("update:tagConfidenceBelowFilter", value ?? []),
});
const tagFilterInput = ref("");
const tagFilterSuggestions = ref([]);
const tagFilterHoverEnabled = ref(false);
const tagFilterIndex = ref(-1);

async function loadTagFilterSuggestions(input) {
  if (!input || input.length < 1) {
    tagFilterSuggestions.value = [];
    return;
  }
  try {
    const res = await apiClient.get(`${props.backendUrl}/tags`);
    const all = Array.isArray(res.data) ? res.data : [];
    const q = input.toLowerCase();
    tagFilterSuggestions.value = all
      .filter(
        (t) =>
          t.tag.toLowerCase().includes(q) &&
          !tagFilterModel.value.includes(t.tag) &&
          !tagRejectedFilterModel.value.includes(t.tag),
      )
      .slice(0, 8)
      .map((t) => t.tag);
  } catch {
    tagFilterSuggestions.value = [];
  }
}

watch(tagFilterInput, (val) => {
  tagFilterIndex.value = -1;
  loadTagFilterSuggestions(val);
});

function addTagFilter(tag) {
  if (
    tag &&
    !tagFilterModel.value.includes(tag) &&
    !tagRejectedFilterModel.value.includes(tag)
  ) {
    tagFilterModel.value = [...tagFilterModel.value, tag];
  }
  tagFilterInput.value = "";
  tagFilterSuggestions.value = [];
  tagFilterIndex.value = -1;
}

function removeTagFilter(tag) {
  tagFilterModel.value = tagFilterModel.value.filter((t) => t !== tag);
  tagRejectedFilterModel.value = tagRejectedFilterModel.value.filter(
    (t) => t !== tag,
  );
}

function toggleTagRejected(tag) {
  if (tagFilterModel.value.includes(tag)) {
    tagFilterModel.value = tagFilterModel.value.filter((t) => t !== tag);
    tagRejectedFilterModel.value = [...tagRejectedFilterModel.value, tag];
  } else {
    tagRejectedFilterModel.value = tagRejectedFilterModel.value.filter(
      (t) => t !== tag,
    );
    tagFilterModel.value = [...tagFilterModel.value, tag];
  }
}

const confidenceTagInput = ref("");
const confidenceTagSuggestions = ref([]);
const confidenceTagHoverEnabled = ref(false);
const confidenceTagIndex = ref(-1);
const confidenceThreshold = ref(0.7);
const confidenceMode = ref("above");
let suppressConfidenceSuggestionLoad = false;

async function loadConfidenceTagSuggestions(input) {
  if (!input || input.length < 1) {
    confidenceTagSuggestions.value = [];
    return;
  }
  try {
    const res = await apiClient.get(`${props.backendUrl}/tags`);
    const all = Array.isArray(res.data) ? res.data : [];
    const q = input.toLowerCase();
    confidenceTagSuggestions.value = all
      .filter((t) => t.tag.toLowerCase().includes(q))
      .slice(0, 8)
      .map((t) => t.tag);
  } catch {
    confidenceTagSuggestions.value = [];
  }
}

watch(confidenceTagInput, (val) => {
  if (suppressConfidenceSuggestionLoad) {
    suppressConfidenceSuggestionLoad = false;
    return;
  }
  confidenceTagIndex.value = -1;
  loadConfidenceTagSuggestions(val);
});

function selectConfidenceTagSuggestion(tag) {
  suppressConfidenceSuggestionLoad = true;
  confidenceTagInput.value = tag;
  confidenceTagSuggestions.value = [];
  confidenceTagIndex.value = -1;
}

function addConfidenceFilter(tagArg) {
  const tag = (tagArg ?? confidenceTagInput.value).trim();
  if (!tag) return;
  const entry = `${tag}:${confidenceThreshold.value.toFixed(2)}`;
  if (confidenceMode.value === "above") {
    if (!tagConfidenceAboveFilterModel.value.includes(entry)) {
      tagConfidenceAboveFilterModel.value = [
        ...tagConfidenceAboveFilterModel.value,
        entry,
      ];
    }
  } else {
    if (!tagConfidenceBelowFilterModel.value.includes(entry)) {
      tagConfidenceBelowFilterModel.value = [
        ...tagConfidenceBelowFilterModel.value,
        entry,
      ];
    }
  }
  confidenceTagInput.value = "";
  confidenceTagSuggestions.value = [];
  confidenceTagIndex.value = -1;
}

function removeConfidenceAboveFilter(entry) {
  tagConfidenceAboveFilterModel.value =
    tagConfidenceAboveFilterModel.value.filter((e) => e !== entry);
}

function removeConfidenceBelowFilter(entry) {
  tagConfidenceBelowFilterModel.value =
    tagConfidenceBelowFilterModel.value.filter((e) => e !== entry);
}

function confidenceEntryLabel(entry) {
  const [tag, threshold] = entry.split(":");
  return `${tag} ${Math.round(parseFloat(threshold) * 100)}%`;
}

const comfyuiModelOptions = ref([]);
const comfyuiLoraOptions = ref([]);

const sortMenuOpen = ref(false);
const sortButtonRef = ref(null);
const pendingSortSelection = ref(null);

const sortModel = computed({
  get: () => props.selectedSort,
  set: (value) =>
    emit("update:selected-sort", {
      sort: value != null ? String(value) : "",
      descending: descendingModel.value,
    }),
});

const SIMILARITY_SORT_KEY = "CHARACTER_LIKENESS";
const LIKENESS_GROUPS_SORT_KEY = "LIKENESS_GROUPS";

const sortMenuModel = computed(() => {
  return pendingSortSelection.value ?? sortModel.value;
});

const pendingSortKey = computed(() => {
  return String(sortMenuModel.value || "").toUpperCase();
});

const committedSortKey = computed(() => {
  return String(sortModel.value || "").toUpperCase();
});

const isPendingParameterSortCommit = computed(() => {
  return (
    sortRequiresParameter(pendingSortKey.value) &&
    pendingSortKey.value !== committedSortKey.value
  );
});

const isPendingSimilarityParameter = computed(() => {
  return (
    isPendingParameterSortCommit.value &&
    pendingSortKey.value === SIMILARITY_SORT_KEY
  );
});

const isPendingStackParameter = computed(() => {
  return (
    isPendingParameterSortCommit.value &&
    pendingSortKey.value === LIKENESS_GROUPS_SORT_KEY
  );
});

watch(sortMenuOpen, (isOpen) => {
  if (isOpen) {
    pendingSortSelection.value = sortModel.value;
  } else {
    pendingSortSelection.value = null;
  }
});

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
  if (sortModel.value === LIKENESS_GROUPS_SORT_KEY) {
    return selectedStackThresholdOption.value?.label
      ? `Groups: ${selectedStackThresholdOption.value.label}`
      : "Groups";
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

function sortRequiresParameter(sortValue) {
  const key = String(sortValue || "").toUpperCase();
  return key === SIMILARITY_SORT_KEY || key === LIKENESS_GROUPS_SORT_KEY;
}

function commitSortSelection(sortValue) {
  sortModel.value = sortValue != null ? String(sortValue) : "";
}

function handleSortModelUpdate(sortValue) {
  if (props.isSearchActive) return;

  pendingSortSelection.value = sortValue != null ? String(sortValue) : "";

  if (!sortRequiresParameter(pendingSortSelection.value)) {
    commitSortSelection(pendingSortSelection.value);
    sortMenuOpen.value = false;
  }
}

function handleSimilarityOptionClick(_value) {
  if (String(sortMenuModel.value || "").toUpperCase() === SIMILARITY_SORT_KEY) {
    commitSortSelection(SIMILARITY_SORT_KEY);
    sortMenuOpen.value = false;
  }
}

function handleStackThresholdOptionClick(_value) {
  if (
    String(sortMenuModel.value || "").toUpperCase() === LIKENESS_GROUPS_SORT_KEY
  ) {
    commitSortSelection(LIKENESS_GROUPS_SORT_KEY);
    sortMenuOpen.value = false;
  }
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
  padding: 3px 4px;
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
</style>
