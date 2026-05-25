<template>
  <div class="selection-bar-overlay">
    <div class="selection-bar-content">
      <div class="selection-bar-left">
        <!-- ── Sidebar toggle ──────────────────────────────────── -->
        <button
          class="bar-btn bar-btn--icon"
          :class="{ 'bar-btn--active': sidebarStore.sidebarVisible }"
          type="button"
          :title="sidebarStore.sidebarVisible ? 'Hide sidebar' : 'Show sidebar'"
          @click="sidebarStore.toggleSidebar()"
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
          :class="{ 'bar-btn--active': gb?.isSearchActive?.value }"
          type="button"
          title="Search (F)"
          @click="searchStore.searchOverlayVisible = true"
        >
          <v-icon size="20">mdi-magnify</v-icon>
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
              title="Generate with ComfyUI (T2I)"
            >
              <v-icon size="20">mdi-robot</v-icon>
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
                      ? `Actions for ${selectedCount} selected (${props.selectedExpandedCount} total including stacks) — press S`
                      : `Actions for ${selectedCount} selected — press S`
                "
              >
                <v-icon size="20">mdi-image-multiple-outline</v-icon>
                <span class="bar-btn-apply-label">({{ selectedCount }})</span>
                <v-icon size="18" class="bar-btn-chevron">mdi-menu-down</v-icon>
              </button>
            </template>
            <div
              ref="selectionMenuPanelRef"
              class="selection-menu-panel"
              :class="{ 'ctx-flip-sub': selectionPanelFlipped }"
              @keydown="onSelectionMenuKeydown"
            >
              <!-- ── Read-only indicator ──────────────────────────── -->
              <div v-if="isReadOnly" class="ctx-readonly-header">
                <span class="ctx-readonly-pill">
                  <v-icon size="10">mdi-lock-outline</v-icon>
                  Read only
                </span>
              </div>
              <!-- ── Set / Character / Project ─────────────────────── -->
              <template v-if="!isScrapheapView">
                <AddToEntityControl
                  ref="ateProjectRef"
                  type="project"
                  placement="right"
                  :backend-url="backendUrl"
                  :picture-ids="selectedImageIds"
                  :disabled="selectedCount === 0"
                  :readonly="isReadOnly"
                  @selected="$emit('set-project', $event)"
                />
                <AddToEntityControl
                  ref="ateCharacterRef"
                  type="character"
                  placement="right"
                  :backend-url="backendUrl"
                  :picture-ids="selectedImageIds"
                  :disabled="selectedCount === 0"
                  :readonly="isReadOnly"
                  @added="$emit('add-to-character', $event)"
                  @removed="$emit('remove-from-character', $event)"
                />
                <AddToEntityControl
                  ref="ateSetRef"
                  type="set"
                  placement="right"
                  :backend-url="backendUrl"
                  :picture-ids="selectedImageIds"
                  :disabled="selectedCount === 0"
                  :readonly="isReadOnly"
                  @added="$emit('added-to-set', $event)"
                />
                <div class="ctx-sep" />
              </template>

              <!-- ── Stack / Unstack ───────────────────────────────── -->
              <template v-if="!isScrapheapView">
                <button
                  v-if="showRemoveStackButton"
                  class="ctx-item"
                  :disabled="isReadOnly"
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
                  :disabled="isReadOnly"
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
                  :disabled="isReadOnly"
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
                  :disabled="isReadOnly"
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
              <template v-if="!isScrapheapView">
                <button
                  class="ctx-item"
                  :disabled="selectedCount === 0 || isReadOnly"
                  title="Tag selected (T)"
                  @click="
                    openTagInput();
                    selectionMenuOpen = false;
                  "
                >
                  <v-icon class="ctx-icon" size="15">mdi-tag-plus</v-icon>
                  Tag
                </button>
                <div
                  v-if="props.taggerPlugins.length"
                  ref="autoTagSubmenuWrapRef"
                  class="ctx-submenu-wrap"
                  @mouseenter="autoTagSubmenuOpen = true"
                  @mouseleave="autoTagSubmenuOpen = false"
                >
                  <button
                    class="ctx-item"
                    :disabled="selectedCount === 0 || isReadOnly"
                  >
                    <v-icon class="ctx-icon" size="15">mdi-tag-outline</v-icon>
                    Tag automatically
                    <v-icon class="ctx-arrow" size="14"
                      >mdi-chevron-right</v-icon
                    >
                  </button>
                  <div v-if="autoTagSubmenuOpen" class="ctx-submenu">
                    <button
                      v-for="plugin in props.taggerPlugins"
                      :key="plugin.name"
                      class="ctx-item"
                      :disabled="selectedCount === 0 || isReadOnly"
                      @click="
                        $emit('auto-tag', { model: plugin.name });
                        selectionMenuOpen = false;
                      "
                    >
                      <v-icon class="ctx-icon" size="15"
                        >mdi-tag-outline</v-icon
                      >
                      {{ plugin.display_name || plugin.name }}
                      <span
                        v-if="plugin.default_enabled"
                        class="ctx-default-pill"
                        >default</span
                      >
                    </button>
                  </div>
                </div>
                <div
                  v-if="props.captionerPlugins.length"
                  ref="descriptionSubmenuWrapRef"
                  class="ctx-submenu-wrap"
                  @mouseenter="descriptionSubmenuOpen = true"
                  @mouseleave="descriptionSubmenuOpen = false"
                >
                  <button
                    class="ctx-item"
                    :disabled="selectedCount === 0 || isReadOnly"
                  >
                    <v-icon class="ctx-icon" size="15"
                      >mdi-text-box-outline</v-icon
                    >
                    Generate description
                    <v-icon class="ctx-arrow" size="14"
                      >mdi-chevron-right</v-icon
                    >
                  </button>
                  <div v-if="descriptionSubmenuOpen" class="ctx-submenu">
                    <button
                      v-for="plugin in props.captionerPlugins"
                      :key="plugin.name"
                      class="ctx-item"
                      :disabled="selectedCount === 0 || isReadOnly"
                      @click="
                        $emit('generate-description', { model: plugin.name });
                        selectionMenuOpen = false;
                      "
                    >
                      <v-icon class="ctx-icon" size="15"
                        >mdi-text-box-outline</v-icon
                      >
                      {{ plugin.display_name || plugin.name }}
                      <span
                        v-if="plugin.default_enabled"
                        class="ctx-default-pill"
                        >default</span
                      >
                    </button>
                  </div>
                </div>
                <button
                  v-if="pluginOptions.length"
                  class="ctx-item"
                  :disabled="selectedCount === 0 || isReadOnly"
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
                  :disabled="selectedCount === 0 || isReadOnly"
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
                v-if="showRemoveButton"
                class="ctx-item ctx-item--danger"
                :disabled="selectedCount === 0 || isReadOnly"
                @click="
                  $emit('remove-from-group');
                  selectionMenuOpen = false;
                "
              >
                {{ removeButtonLabel }}
              </button>
              <button
                class="ctx-item ctx-item--danger"
                :disabled="selectedCount === 0 || isReadOnly"
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
                <div
                  v-bind="menuProps"
                  ref="tagBtnRef"
                  class="hidden-panel-activator"
                  aria-hidden="true"
                ></div>
              </template>
              <TbTagPanel
                :backend-url="props.backendUrl"
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
            class="clear-btn"
            :disabled="!visible"
            @click="$emit('clear-selection')"
            title="Clear selection (ESC)"
          >
            <v-icon size="20" color="primary">mdi-selection-off</v-icon>
          </button>
          <button
            class="delete-btn"
            :disabled="!visible || isReadOnly"
            @click="$emit('delete-selected')"
            title="Delete selected items (DEL)"
          >
            <v-icon size="20" color="error">mdi-delete</v-icon>
          </button>
        </div>
        <!-- /selection-ctx-bar -->
        <!-- ── Separator: Delete | Settings ───────────────────────────── -->
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
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { apiClient, isReadOnly } from "../../utils/apiClient";
import { useFilterStore } from "../../stores/useFilterStore";
import { useSortStore } from "../../stores/useSortStore";
import { useSelectionStore } from "../../stores/useSelectionStore";
import { useGridStore } from "../../stores/useGridStore";
import { useExportStore } from "../../stores/useExportStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useSearchStore } from "../../stores/useSearchStore";
import AddToEntityControl from "../widgets/AddToEntityControl.vue";
import GbFilterPanel from "./GbFilterPanel.vue";
import TbComfyPanel from "./TbComfyPanel.vue";
import TbExportPanel from "./TbExportPanel.vue";
import TbTagPanel from "./TbTagPanel.vue";
import PluginParametersUI from "../widgets/PluginParametersUI.vue";
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
  taggerPlugins: { type: Array, default: () => [] },
  captionerPlugins: { type: Array, default: () => [] },
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
  "comfyui-run-grid",
  "tags-applied",
  "auto-tag",
  "generate-description",
  "expand-all-stacks",
  "collapse-all-stacks",
  "confirm-export-zip",
  "open-import",
  "open-settings",
  "selection-menu-open",
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

const tbComfyuiMenuOpen = ref(false);
const autoTagSubmenuOpen = ref(false);
const descriptionSubmenuOpen = ref(false);
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
  if (Boolean(searchStore.searchQuery && searchStore.searchQuery.trim()))
    return;
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
  if (Boolean(searchStore.searchQuery && searchStore.searchQuery.trim()))
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
  if (Boolean(searchStore.searchQuery && searchStore.searchQuery.trim()))
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
  if (Boolean(searchStore.searchQuery && searchStore.searchQuery.trim()))
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
const selectionMenuPanelRef = ref(null);
const selectionPanelFlipped = ref(false);
const selectionMenuOpen = ref(false);
const ateProjectRef = ref(null);
const ateCharacterRef = ref(null);
const ateSetRef = ref(null);
const autoTagSubmenuWrapRef = ref(null);
const descriptionSubmenuWrapRef = ref(null);

function isEditableElement(el) {
  if (!(el instanceof HTMLElement)) return false;
  if (el.isContentEditable) return true;
  const tag = el.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (el.getAttribute("role") === "textbox") return true;
  return false;
}

function getMenuItems() {
  const panel = selectionMenuPanelRef.value;
  if (!panel) return [];
  const focused = document.activeElement;
  // Inside an open ATE flyout menu?
  const openAteMenu = focused?.closest(".ate-menu.open");
  if (openAteMenu && panel.contains(openAteMenu)) {
    return Array.from(
      openAteMenu.querySelectorAll("input, button:not(:disabled)"),
    ).filter((el) => el.offsetParent !== null);
  }
  // Inside a ctx-submenu (v-if, only in DOM when open)?
  const ctxSub = focused?.closest(".ctx-submenu");
  if (ctxSub && panel.contains(ctxSub)) {
    return Array.from(ctxSub.querySelectorAll("button:not(:disabled)")).filter(
      (el) => el.offsetParent !== null,
    );
  }
  // Top level: buttons not inside any .ate-menu
  return Array.from(panel.querySelectorAll("button:not(:disabled)")).filter(
    (el) => el.closest(".ate-menu") === null && el.offsetParent !== null,
  );
}

async function onSelectionMenuKeydown(event) {
  const key = event.key;
  if (
    !["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Enter"].includes(key)
  )
    return;
  const focused = document.activeElement;
  const panel = selectionMenuPanelRef.value;
  if (!panel) return;
  // Enter: activate the focused button (Vuetify intercepts Enter in menu overlays)
  if (key === "Enter") {
    if (focused?.tagName === "BUTTON" && !focused.disabled) {
      event.stopPropagation();
      focused.click();
    }
    return;
  }
  // For left/right in a text input, only intercept Left at cursor start
  if (focused?.tagName === "INPUT") {
    if (key === "ArrowRight") return;
    if (key === "ArrowLeft") {
      const atStart =
        focused.selectionStart === 0 && focused.selectionEnd === 0;
      if (!atStart) return;
    }
  }
  event.preventDefault();
  event.stopPropagation();
  const items = getMenuItems();
  const idx = items.indexOf(focused);
  if (key === "ArrowDown") {
    (idx === -1
      ? items[0]
      : items[Math.min(idx + 1, items.length - 1)]
    )?.focus();
    return;
  }
  if (key === "ArrowUp") {
    (idx === -1
      ? items[items.length - 1]
      : items[Math.max(idx - 1, 0)]
    )?.focus();
    return;
  }
  if (key === "ArrowRight") {
    if (!focused) return;
    // ATE flyout trigger → open it and focus its search input
    if (
      focused.classList.contains("ate-btn") &&
      focused.closest(".ate--flyout")
    ) {
      const ateRoot = focused.closest(".ate");
      if (!ateRoot.classList.contains("open")) focused.click();
      await nextTick();
      ateRoot.querySelector(".ate-menu.open input")?.focus();
      return;
    }
    // ctx-submenu-wrap trigger → open submenu and focus first item
    const wrap = focused.closest(".ctx-submenu-wrap");
    if (wrap) {
      if (autoTagSubmenuWrapRef.value === wrap) autoTagSubmenuOpen.value = true;
      else if (descriptionSubmenuWrapRef.value === wrap)
        descriptionSubmenuOpen.value = true;
      await nextTick();
      wrap.querySelector(".ctx-submenu button:not(:disabled)")?.focus();
    }
    return;
  }
  if (key === "ArrowLeft") {
    if (!focused) return;
    // Inside an open ATE flyout → close it, refocus trigger
    const openAteMenu = focused.closest(".ate-menu.open");
    if (openAteMenu && panel.contains(openAteMenu)) {
      const ateRoot = openAteMenu.closest(".ate");
      for (const ateRef of [ateProjectRef, ateCharacterRef, ateSetRef]) {
        if (ateRef.value?.$el === ateRoot) {
          ateRef.value.closeMenu();
          break;
        }
      }
      await nextTick();
      ateRoot?.querySelector(".ate-btn")?.focus();
      return;
    }
    // Inside a ctx-submenu → close it, refocus trigger
    const ctxSub = focused.closest(".ctx-submenu");
    if (ctxSub && panel.contains(ctxSub)) {
      const wrap = ctxSub.closest(".ctx-submenu-wrap");
      if (autoTagSubmenuWrapRef.value === wrap)
        autoTagSubmenuOpen.value = false;
      else if (descriptionSubmenuWrapRef.value === wrap)
        descriptionSubmenuOpen.value = false;
      await nextTick();
      wrap?.querySelector(":scope > button.ctx-item")?.focus();
    }
  }
}

function handleSelectionMenuHotkey(event) {
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  if (isEditableElement(event.target)) return;
  if (isEditableElement(document.activeElement)) return;
  // Down while menu is open and focus is outside panel → focus first item
  if (event.key === "ArrowDown" && selectionMenuOpen.value) {
    if (selectionMenuPanelRef.value?.contains(document.activeElement)) return;
    event.preventDefault();
    nextTick(() => getMenuItems()[0]?.focus());
    return;
  }
  if (event.key !== "s" && event.key !== "S") return;
  if (props.selectedCount <= 0) return;
  event.preventDefault();
  selectionMenuOpen.value = !selectionMenuOpen.value;
}

onMounted(() => window.addEventListener("keydown", handleSelectionMenuHotkey));
onUnmounted(() =>
  window.removeEventListener("keydown", handleSelectionMenuHotkey),
);

watch(selectionMenuOpen, async (open) => {
  emit("selection-menu-open", open);
  if (!open) {
    selectionPanelFlipped.value = false;
    return;
  }
  await nextTick();
  if (!selectionMenuPanelRef.value) return;
  const rect = selectionMenuPanelRef.value.getBoundingClientRect();
  selectionPanelFlipped.value = rect.right + 185 > window.innerWidth - 8;
});
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

/* ═══════════════════════════════════════════════════════════════════════════
   Grid Bar – Sort / Filter / View buttons and panels
   ═══════════════════════════════════════════════════════════════════════════ */

/* ── Bar buttons ──────────────────────────────────────────────────────────── */
.bar-split-button {
  display: flex;
  align-items: center;
  border-radius: 5px;
  border: 1px solid rgba(var(--v-theme-on-background), 0.07);
  background: rgba(var(--v-theme-on-background), 0.02);
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
  border-left: 1px solid rgba(var(--v-theme-on-background), 0.07);
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
