<template>
  <!-- The whole view is the drop target for workflow files, the same door as
       the sidebar's *Add workflow* (`addWorkflowFiles`). -->
  <div
    class="wfv"
    :class="{ 'wfv--drop': dropActive }"
    role="region"
    aria-label="Workflows"
    @dragenter="onFileDragEnter"
    @dragover="onFileDragOver"
    @dragleave="onFileDragLeave"
    @drop="onFileDrop"
  >
    <p class="visually-hidden" role="status">{{ announcement }}</p>

    <div class="wfv-toolbar shelfbar toolbar">
      <!-- The browse controls, in the order a reader reads a list: how it is
           sorted, what is filtered out, then what is searched for. Mine or
           Templates is the sidebar's choice now, and Add workflow is its footer's. -->
      <v-menu
        v-model="sortMenuOpen"
        location="bottom start"
        origin="top start"
        :offset="8"
        :close-on-content-click="false"
      >
        <template #activator="{ props: menuProps }">
          <AppBarButton
            v-bind="menuProps"
            prefix="Sort:"
            aria-haspopup="menu"
            :aria-expanded="sortMenuOpen"
            >{{
              onTemplates
                ? TEMPLATE_SORT_LABELS[templates.sortKey].label
                : SORT_LABELS[store.sortKey].label
            }}</AppBarButton
          >
        </template>
        <div class="tbm">
          <span class="tbm-caret tbm-caret--start"></span>
          <div class="tbm-header">
            <v-icon size="18" class="tbm-header-icon">mdi-sort</v-icon>
            <span class="tbm-title">Sort</span>
          </div>
          <div class="tbm-section">
            <span class="tbm-label">Sort by</span>
            <OptionRows
              v-if="onTemplates"
              :options="templateSortOptions"
              :model-value="templates.sortKey"
              aria-label="Sort by"
              @update:model-value="(key) => (templates.sortKey = key)"
            />
            <OptionRows
              v-else
              :options="sortOptions"
              :model-value="store.sortKey"
              aria-label="Sort by"
              @update:model-value="store.setSortKey"
            />
          </div>
          <!-- Templates are not favourites; the box is Mine's alone. -->
          <div v-if="!onTemplates" class="tbm-section">
            <label class="tbm-check fm-check">
              <input
                type="checkbox"
                :checked="store.favouritesLead"
                @change="store.setFavouritesLead($event.target.checked)"
              />
              <span class="fm-check-label">Favourites first</span>
            </label>
            <p class="fm-help">
              Inside each group, favourites lead and the sort orders the rest.
              Off, a favourite sits where the sort puts it.
            </p>
          </div>
        </div>
      </v-menu>

      <!-- The hidden inputs: *Choose thumbnail…* from the card menu (one image
           for one card), and the workflow file picker, which the empty state's
           button and the sidebar's Add workflow (`addRequest`) both open. -->
      <input
        ref="fileInput"
        class="wfv-file-input"
        type="file"
        accept=".json,application/json"
        multiple
        tabindex="-1"
        aria-hidden="true"
        @change="filesChosen"
      />
      <input
        ref="thumbnailInput"
        class="wfv-file-input"
        type="file"
        accept="image/png,image/jpeg,image/webp"
        tabindex="-1"
        aria-hidden="true"
        @change="thumbnailChosen"
        @cancel="focusCursorRow"
      />

      <!-- No count on the strip's own chips and a count here, which is the
           reverse of the picture grid: that toolbar keeps its strip on screen
           beside the button, and this one is a row that only exists while a
           filter is on. Both read the same `filterChips`. -->
      <v-menu
        v-model="filterMenuOpen"
        location="bottom start"
        origin="top start"
        :offset="8"
        :close-on-content-click="false"
      >
        <template #activator="{ props: menuProps }">
          <AppBarButton
            v-bind="menuProps"
            icon="filter"
            :badge="filterCount || null"
            :active="filterCount > 0 && !filterMenuOpen"
            :open="filterMenuOpen"
            aria-haspopup="menu"
            :aria-expanded="filterMenuOpen"
            >Filter</AppBarButton
          >
        </template>
        <WorkflowTemplateFilterMenu v-if="onTemplates" :open="filterMenuOpen" />
        <WorkflowFilterMenu v-else :open="filterMenuOpen" />
      </v-menu>

      <!-- The active filters, one chip each with its own ×, in the bar rather
           than on a row under it. Templates keep their own chips in the
           templates store. -->
      <FilterStrip
        inline
        class="wfv-strip"
        :chips="onTemplates ? templates.filterChips : store.filterChips"
        :of-label="onTemplates ? templates.filterOfLabel : store.filterOfLabel"
        :on-clear-all="onTemplates ? templates.clearFilters : store.clearFilters"
      />

      <span class="wfv-spacer"></span>

      <!-- The name filter, bound to the same text as the Filters menu's own
           field: `store.filters.name`, or the templates store's while
           Templates is shown. F focuses it (`searchFocusToken`). -->
      <div class="wfv-search">
        <NameFilterField
          ref="nameFieldRef"
          class="wfv-search-field"
          :model-value="searchName"
          placeholder="Search"
          :label="
            onTemplates
              ? 'Search templates by title, tag or model'
              : 'Search workflows by name'
          "
          @update:model-value="setSearchName"
        />
        <kbd class="wfv-search-key" aria-hidden="true">F</kbd>
      </div>

      <!-- The app-wide tail, minus undo: this view replaces the grid and its
           toolbar, so without it neither Settings nor the right rail has a
           control on the screen — and the rail is where `WorkflowTab` is
           shown (`AppInspector` gates it on
           `sidebarStore.workflowInspectorOpen`), so the rail would be
           unreachable (#1415). Its own `--space-3` cluster because this bar
           spaces its controls wider, `rail-name` because the toggle's tooltip
           is also its accessible name and this rail is not the stats sidebar,
           and `rail` because the inspector has its own open flag. -->
      <span class="wfv-bar-tail">
        <TbGlobalActions
          separator
          rail="workflows"
          rail-name="Inspector"
        />
      </span>
    </div>

    <WorkflowTemplatesPanel
      v-if="onTemplates"
      :column-min="COLUMN_MIN"
      :column-gap="COLUMN_GAP"
      @add="addSelectedTemplates"
      @open-settings="(section) => emit('open-settings', section)"
    />

    <template v-else>
      <p v-if="store.error" class="wfv-error" role="alert">{{ store.error }}</p>

      <!-- A Recipe section's Open that named a workflow this grid does not list.
           Said on screen and not only in the live region, because the reader
           made a deliberate gesture and the screen otherwise looks as though it
           ignored them. -->
      <p v-if="linkMissed" class="wfv-note">
        That workflow is not in this grid.
        <template v-if="withheldNames">Left out: {{ withheldNames }}.</template>
      </p>

      <!-- A deep link whose card the payload holds and a filter hides.
           Not the note above: that one is about what the SERVER left out and
           offers no way in, and this one is one control away from fixed. -->
      <p v-if="linkFiltered" class="wfv-note" data-testid="wfv-link-filtered">
        That workflow is in this grid, but a filter is hiding it.
        <button
          class="wfv-note-clear"
          type="button"
          @click="store.clearFilters()"
        >
          Clear all
        </button>
      </p>

      <!-- Files an import refused because the library has them already. The
           view lands on the card and pulses it; this names it, so the reader
           knows why nothing new appeared. -->
      <p v-if="alreadyHere.length" class="wfv-note" data-testid="wfv-already-here">
        {{ alreadyHereSentence }}
        <button
          v-if="alreadyHereFiltered"
          class="wfv-note-clear"
          type="button"
          @click="store.clearFilters()"
        >
          Clear all
        </button>
        <button class="wfv-note-clear" type="button" @click="alreadyHere = []">
          Dismiss
        </button>
      </p>

      <!-- The fifth kind of empty: the library HAS workflows and the filters
           leave none. Not the empty state below - that screen offers three ways
           to get a first workflow, and this reader already has some. The strip
           above carries the way back, so this only has to say which it is. -->
      <p v-if="filteredOut" class="wfv-note">
        No workflow matches these filters.
        <button
          class="wfv-note-clear"
          type="button"
          @click="store.clearFilters()"
        >
          Clear all
        </button>
      </p>

      <!-- The §9 empty state: the shipped art, a Tiny5 `--text-2xl` headline and
           one `--text-sm` line, then the routes out as plain buttons. Not the
           bordered option rows `LibraryEmptyState` uses — that screen explains
           three unfamiliar choices at first run; these three are one verb each. -->
      <div v-if="showEmptyState" class="wfv-empty">
        <div class="wfv-empty__stack">
          <div class="wfv-empty__card">
            <div class="wfv-empty__illustration" aria-hidden="true">
              <img src="/Empty.png" alt="" />
            </div>
            <h2 class="wfv-empty__title">Nothing found yet</h2>
            <p v-if="withheldNames" class="wfv-empty__lead">
              Every workflow this library has is being left out of the grid:
              {{ withheldNames }}. Nothing has been lost.
            </p>
            <p v-else class="wfv-empty__lead">
              A workflow arrives with the pictures it made, or as a file of its own.
            </p>
            <div class="wfv-empty__actions">
              <AppButton
                size="sm"
                variant="primary"
                icon-left="tray-arrow-up"
                @click="fileInput?.click()"
              >
                Drop a workflow file
              </AppButton>
              <AppButton
                v-if="watchedFolder"
                size="sm"
                variant="secondary"
                icon-left="folder-eye-outline"
                @click="openWatchedFolder"
              >
                Open watched folder
              </AppButton>
              <!-- Gone once connected: there is nothing to offer somebody who has
                   already done it. -->
              <AppButton
                v-if="!comfyuiConfigured"
                size="sm"
                variant="secondary"
                icon-left="graph-outline"
                @click="emit('open-settings', 'comfyui')"
              >
                Connect ComfyUI
              </AppButton>
            </div>
            <p v-if="watchedFolder" class="wfv-empty__path">{{ watchedPath }}</p>
          </div>

          <!-- Deleting the last workflow leaves its recipes unfiled, and they
               are still the reader's to rename, extract or delete (#2356). One
               column at the card's width: no grid is measured here. Hidden
               while there are none, so a bare library sees only the card. -->
          <WorkflowRecipesTab
            unfiled
            class="wfv-unfiled wfv-empty__recipes"
            workflow-name="Unfiled recipes"
            :style="{ '--wf-columns': 1 }"
          />
        </div>
      </div>

      <!-- The scroller carries the padding so the grid below is the bare
           track: `measure()` reads the grid's `clientWidth`, which INCLUDES its
           own padding, and a padded grid would be measured 32px wider than the
           space the columns actually have.
           `tabindex="-1"` so a click on the background (or a gap in the grid)
           lands focus here rather than on `<body>`, where Ctrl+A would select
           the page's text instead of the cards. `.self` keeps keys from the
           rows (already handled by the grid) and from the recipes tab out. -->
      <div
        v-else
        ref="scrollEl"
        class="wfv-scroll"
        :class="{ 'wfv-scroll--edge-tab': !sidebarStore.workflowInspectorOpen }"
        tabindex="-1"
        @keydown.self="onBackgroundKeyDown"
        @pointerdown="
          pressedBackground = $event.button === 0 && isBackground($event.target)
        "
        @click="onBackgroundClick"
      >
        <!-- One `treegrid` and one tab stop: the cursor roves with the arrow
             keys and the focused row is the only one at `tabindex="0"`. -->
        <div
          ref="gridEl"
          class="wfv-grid"
          role="treegrid"
          aria-label="Workflow cards"
          aria-multiselectable="true"
          :aria-busy="store.loading"
          :style="{
            '--wf-columns': columns,
            '--wf-column-min': `${COLUMN_MIN}px`,
            '--wf-gap': `${COLUMN_GAP}px`,
          }"
          @keydown="onKeyDown"
        >
          <template v-for="(entry, index) in flatRows" :key="entry.id">
            <div
              class="wfv-row"
              role="row"
              aria-level="1"
              :aria-selected="cardSelected(entry.key)"
              :aria-posinset="entry.cardIndex + 1"
              :aria-setsize="store.sortedCards.length"
              :tabindex="index === cursorIndex ? 0 : -1"
              :data-key="entry.key"
              @click="onRowClick(index, $event)"
              @dblclick="onRowDblClick"
              @contextmenu.prevent="openRowMenu(index, $event)"
            >
              <!-- A model file dropped here from outside goes beside this
                   workflow's models (useModelDropTarget). -->
              <div
                class="wfv-cell"
                :class="{
                  'wfv-cell--drop': modelDropKey === entry.key,
                  'wfv-cell--pulse': pulseKeys.includes(entry.key),
                }"
                role="gridcell"
                :data-model-drop-models="dropModels(entry.card)"
                @dragover="
                  dropModels(entry.card) && onModelDragOver(entry.key, $event)
                "
                @dragleave="onModelDragLeave(entry.key, $event)"
              >
                <WorkflowCard
                  :card="entry.card"
                  :selected="cardSelected(entry.key)"
                  :run-failure="runDialog.failures[entry.card.id] ?? null"
                  :favourite="favourites.isFavourite('workflow', entry.card.id)"
                  @run="runCard(entry.card)"
                  @toggle-favourite="toggleFavourites([entry.key])"
                />
              </div>
            </div>
          </template>
        </div>

        <!-- Saved recipes whose workflow is gone (deleted, or never filed).
             The Recipes tab's own rows, laid on this grid's columns; the tab
             hides itself while there are none. -->
        <WorkflowRecipesTab
          unfiled
          class="wfv-unfiled"
          workflow-name="Unfiled recipes"
          :style="{ '--wf-columns': columns }"
        />
      </div>
    </template>

    <!-- The pill floats over the END of the list, exactly as the shelf's and
         the photo grid's do: the list is what the selection was made in, and a
         docked strip between the toolbar and the cards would push the whole
         grid down every time a card was clicked. `.selbar-float` turns
         `pointer-events` off on the strip and back on for the pill, so the
         cards underneath it stay clickable. -->
    <!-- The closed inspector's handle: names the selection, and bounces on a
         new one (`inspectorNudge`). Never opens the inspector by itself -
         that would take a column away under the pointer. -->
    <InspectorEdgeTab
      v-if="!sidebarStore.workflowInspectorOpen"
      :label="edgeTabLabel"
      :nudge="sidebarStore.inspectorNudge"
      @open="openInspectorFromTab"
    />

    <div v-if="!onTemplates" class="selbar-float wfv-pill-host">
      <WorkflowSelectionBar
        ref="selBarRef"
        @select-all="selectAllShown"
        @menu-closed="focusCursorRow"
        @open-cover="openCoverPicture"
        @show-pictures="showSelectedPictures"
        @run="runSelected"
        @open-comfyui="openSelectedInComfyui"
        @rename="startRename"
        @set-thumbnail="thumbnailInput?.click()"
        @pin-thumbnail="pinThumbnail"
        @hide="hideSelected"
        @export="exportSelected"
        @duplicate="duplicateSelected"
        @inpaint-copy="inpaintCopySelected"
        @use-set="useSetOnSelected"
        @download-missing="downloadMissingSelected"
        @favourite="toggleFavourites([...store.selectedKeys])"
        @delete="confirmDelete"
      />
    </div>

    <!-- Rename needs somewhere to type, and the card has nowhere: its name row
         is one line of four in a fixed block, and an inline field there would
         reflow the card it is editing. One small dialog instead. -->
    <!-- `closeRename`, not `renameOpen = false`: a dialog closing has the same
         debt the context menu has — focus lands wherever the teleported
         surface left it, which is outside the grid. -->
    <AppDialog
      :open="renameOpen"
      title="Rename workflow"
      size="sm"
      @close="closeRename"
    >
      <AppInput
        v-model="renameDraft"
        label="Name"
        autofocus
        autoselect
        @enter="saveRename"
      />
      <p class="wfv-hint">
        Leave this empty to use the name PixlStash builds from the workflow
        itself.
      </p>
      <template #footer>
        <AppButton size="sm" variant="ghost" @click="closeRename"
          >Cancel</AppButton
        >
        <AppButton size="sm" variant="primary" @click="saveRename"
          >Rename</AppButton
        >
      </template>
    </AppDialog>

  </div>
</template>

<script setup>
/**
 * The Workflows grid (v1.12 Workflows & Recipes, F1a) — on `/workflows` since
 * F1b retired the shelf. See `docs/frontend_architecture.md` §5.
 *
 * One card per workflow, keyed by its `id` (#1623). The cards are one flat
 * index space for the roving cursor: `index ± columns` is the row above or
 * below.
 *
 */
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";
import { useRoute, useRouter } from "vue-router";
import { VIcon, VMenu } from "vuetify/components";

import { existingWorkflow, importWorkflow } from "../../api/comfyui";
import { listImportFolders } from "../../api/folders";
import { exportWorkflow, workflowCoverUrl } from "../../api/workflows";
import { openWorkflowInComfyui } from "../../composables/useComfyuiOpen";
import { useConfirm } from "../../composables/useConfirm";
import {
  downloadableInWorkflows,
  downloadModels,
} from "../../composables/useModelDownloads";
import { useWorkflowPictures } from "../../composables/useWorkflowPictures";
import {
  FAVOURITE_KEY,
  useFavouritesStore,
} from "../../stores/useFavouritesStore";
import { useFilterStore } from "../../stores/useFilterStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useRunDialogStore } from "../../stores/useRunDialogStore";
import { useSearchStore } from "../../stores/useSearchStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import {
  SORT_KEYS,
  SORT_LABELS,
  useWorkflowsStore,
} from "../../stores/useWorkflowsStore";
import {
  TEMPLATE_SORT_KEYS,
  TEMPLATE_SORT_LABELS,
  useWorkflowTemplatesStore,
} from "../../stores/useWorkflowTemplatesStore";
import { errorMessage } from "../../utils/apiError";
import { saveJsonAs } from "../../utils/downloadFile";
import { isInternalImageDrag, isWorkflowFile } from "../../utils/media";
import { outputNoun } from "../../utils/workflowCard";
import FilterStrip from "../panels/FilterStrip.vue";
import TbGlobalActions from "../panels/TbGlobalActions.vue";
import WorkflowFilterMenu from "../panels/WorkflowFilterMenu.vue";
import WorkflowRecipesTab from "../panels/WorkflowRecipesTab.vue";
import WorkflowSelectionBar from "../panels/WorkflowSelectionBar.vue";
import WorkflowTemplateFilterMenu from "../panels/WorkflowTemplateFilterMenu.vue";
import WorkflowTemplatesPanel from "../panels/WorkflowTemplatesPanel.vue";
import AppBarButton from "../widgets/AppBarButton.vue";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import AppInput from "../widgets/AppInput.vue";
import InspectorEdgeTab from "../widgets/InspectorEdgeTab.vue";
import NameFilterField from "../widgets/NameFilterField.vue";
import OptionRows from "../widgets/OptionRows.vue";
import WorkflowCard from "../widgets/WorkflowCard.vue";
import { useFileDropHighlight } from "../../composables/useModelDropTarget";

/**
 * The two numbers the column count is derived from, and the only copy of them.
 *
 * They are written onto the grid as `--wf-column-min` and `--wf-gap` rather
 * than read back out of the sheet, so the arithmetic here and the track the
 * browser paints are the same two numbers by construction. `COLUMN_GAP` is
 * `--space-3` (see below); `COLUMN_MIN` is the card's width, the partner of the
 * `--wf-meta-h` `WorkflowCard.vue` keeps locally. The card's HEIGHT is not a
 * number either side holds: the cover is a 6:5 box, so a card is as tall as
 * its column is wide plus the fixed meta block.
 */
const COLUMN_MIN = 240;
// `--space-3`. It was `--space-4` (12), which is the "comfortable gap inside a
// group" and is what the reading surfaces use; this is a grid of cards, and
// its siblings are dense - the picture grid sets its thumbnails `--space-2`
// apart and the model shelf's rows touch. 8 is the small gap between
// bordered things, which is what these are.
const COLUMN_GAP = 8;

// Settings is App.vue's dialog; TbGlobalActions flips the sidebar store itself.
const emit = defineEmits(["open-settings"]);

const store = useWorkflowsStore();
const favourites = useFavouritesStore();
const filterStore = useFilterStore();
const notices = useNoticeStore();
const runDialog = useRunDialogStore();
const {
  dropKey: modelDropKey,
  onDragOver: onModelDragOver,
  onDragLeave: onModelDragLeave,
} = useFileDropHighlight();

/** The card's shelf files, by hash, for a model dropped on it. */
function dropModels(card) {
  return [...(card.models ?? []), ...(card.loras ?? [])]
    .map((model) => model.sha256)
    .filter(Boolean)
    .join(" ");
}
const sidebarStore = useSidebarStore();
const router = useRouter();
const route = useRoute();
const { confirm } = useConfirm();
const { showWorkflowsPictures } = useWorkflowPictures();

const gridEl = ref(null);
const fileInput = ref(null);
const selBarRef = ref(null);
const scrollEl = ref(null);
const sortMenuOpen = ref(false);
const filterMenuOpen = ref(false);
const nameFieldRef = ref(null);
// The app's search key (F) finds by name here: the bar's Search field, which is
// always drawn.
watch(
  () => useSearchStore().searchFocusToken,
  () => nameFieldRef.value?.focus(),
);
const renameOpen = ref(false);
const renameDraft = ref("");
const columns = ref(1);
// **The cursor is an entry id, not an index.** `flatRows` is rebuilt by a
// resort and by a refetch, and an index held across either names a different card — or, when the list shrinks, no card at all,
// which takes the grid's only tab stop with it and strands a keyboard user
// outside a screen they cannot get back into.
const cursorId = ref("");
const announcement = ref("");
// A `?topology=` or `?workflow=` link that named a workflow the grid does not list.
const linkMissed = ref(false);
// The same link, but the card IS in the payload and a filter is hiding it.
const linkFiltered = ref(false);
// Files an import refused because the library has them already, and the cards
// that are them: the note names them, and those cards pulse once.
const alreadyHere = ref([]);
const pulseKeys = ref([]);
// Whether that import turned *Show hidden* on to reach a hidden one.
const revealedHidden = ref(false);
// Two beats of `--dur-4` (`.wfv-cell--pulse`) and a margin.
const PULSE_MS = 1000;
let pulseTimer = null;
onBeforeUnmount(() => clearTimeout(pulseTimer));
const watchedFolder = ref(null);

const sortOptions = SORT_KEYS.map((key) => ({
  id: key,
  label: SORT_LABELS[key].label,
}));

// ── Mine | Templates (#2000) ──────────────────────────────────────────────
//
// The source lives in the templates store, not here: the right rail is
// `App.vue`'s, and it mounts the template inspector while this one is shown.
// The left sidebar chooses it (`shown`); this view only reads it.
const templates = useWorkflowTemplatesStore();
const onTemplates = computed(() => templates.shown);
const templateSortOptions = TEMPLATE_SORT_KEYS.map((key) => ({
  id: key,
  label: TEMPLATE_SORT_LABELS[key].label,
}));
/** The Filters button's badge: the filters the shown tab has on. */
const filterCount = computed(() =>
  onTemplates.value ? templates.activeFilterCount : store.filterChips.length,
);

/** Show one source. Showing Templates reads its list (the watch below). */
function showSource(which) {
  templates.shown = which === "templates";
}

// However the source changes (the sidebar, a drop, an added template), the
// menus that belong to the old one close, and Templates reads its list the
// first time it is shown.
watch(
  () => templates.shown,
  (on) => {
    sortMenuOpen.value = false;
    filterMenuOpen.value = false;
    if (on) templates.fetchTemplates();
  },
);

// The sidebar's amber Add workflow: the same file picker the empty state opens.
watch(
  () => store.addRequest,
  () => fileInput.value?.click(),
);

/** The name filter of whichever source is shown, and its setter. */
const searchName = computed(() =>
  onTemplates.value ? templates.filters.name : store.filters.name,
);
function setSearchName(name) {
  if (onTemplates.value) templates.setFilters({ name });
  else store.setFilters({ name });
}

// Arriving on a workflow by link means Mine, whatever tab was left open.
watch(
  () => route.query?.workflow || route.query?.topology,
  (linked) => {
    if (linked) templates.shown = false;
  },
  { immediate: true },
);
if (templates.shown) templates.fetchTemplates();
watch(
  () => templates.addRequest,
  () => addSelectedTemplates(),
);

/**
 * The pill's and the inspector's Add: store the selected templates, then land
 * on Mine with what was made selected, so the next look is at it.
 */
async function addSelectedTemplates() {
  // Read before the Add: what it was added from decides what a draft means.
  const shipped = !templates.reachable;
  const body = await templates.add();
  if (!body) {
    if (templates.error) notices.push({ level: "error", text: templates.error });
    return;
  }
  const added = body.added ?? [];
  const failed = body.failed ?? [];
  if (failed.length) {
    notices.push({
      level: "error",
      text: `Not added: ${failed
        .map((entry) => `${entry.name} (${entry.detail})`)
        .join("; ")}`,
    });
  }
  if (!added.length) return;
  // What did not go in stays picked on Templates, to try again.
  templates.selectRange(failed.map((entry) => `${entry.module}/${entry.name}`));
  await store.refetch();
  showSource("mine");
  const ids = added.map((entry) => entry.workflow_id);
  store.selectRange(ids);
  const at = flatRows.value.findIndex((entry) => entry.key === ids[0]);
  if (at >= 0) moveCursor(at);
  const drafts = added.filter((entry) => entry.draft).length;
  let text =
    added.length === 1 ? "Added 1 workflow." : `Added ${added.length} workflows.`;
  // Only when every one is: ComfyUI can come back between the list and the
  // Add, and then some convert and run.
  if (drafts && shipped && drafts === added.length) {
    text +=
      added.length === 1
        ? " It is a draft: once ComfyUI runs, Finish in ComfyUI makes it runnable."
        : " They are drafts: once ComfyUI runs, Finish in ComfyUI makes each runnable.";
  } else if (drafts && added.length === 1) {
    text += " It is a draft: open it in ComfyUI and save it to finish it.";
  } else if (drafts) {
    text += ` ${drafts} of them are drafts: open each in ComfyUI and save it to finish it.`;
  }
  notices.push({ level: "success", text });
  announcement.value =
    added.length === 1 ? "Added 1 workflow" : `Added ${added.length} workflows`;
}

const comfyuiConfigured = computed(() => filterStore.comfyuiConfigured);
const showEmptyState = computed(
  () => store.loaded && !store.loading && store.cards.length === 0,
);
const filteredOut = computed(
  () =>
    store.loaded &&
    !store.loading &&
    store.cards.length > 0 &&
    store.sortedCards.length === 0,
);

/**
 * What the grid HAS but is not drawing, in words: "hidden workflows and 12
 * one-offs", or "" when nothing is left out.
 *
 * "Nothing found yet" over a library whose every workflow is hidden is a
 * screen arguing with itself, so the empty state says which it is in.
 *
 * **Hidden workflows are named and never counted.** Somebody hid them to stop
 * looking at them, and a running total on the toolbar keeps them in view; the
 * Filters menu's Hidden row is the way back to them.
 */
const withheldOneOffs = computed(() => {
  const n = store.filters.hideOneOffs ? store.oneOffs : 0;
  if (!n) return "";
  return n === 1 ? "1 one-off" : `${n} one-offs`;
});
const withheldNames = computed(() => {
  const parts = [];
  if (store.hidden && !store.filters.showHidden) parts.push("hidden workflows");
  if (withheldOneOffs.value) parts.push(withheldOneOffs.value);
  return parts.join(" and ");
});

/**
 * The cards as the cursor walks them: `{kind: "card", id, key, card,
 * cardIndex}`, where `key` is the workflow id and `id` the row's own.
 */
const flatRows = computed(() =>
  store.sortedCards.map((card, cardIndex) => ({
    kind: "card",
    id: `card:${card.id}`,
    key: card.id,
    card,
    cardIndex,
  })),
);

/** Whether the card for workflow `key` wears the selection mark. */
function cardSelected(key) {
  return store.selectedKeys.includes(key);
}

/** Where the cursor is now. Always a real row: it falls back to the first. */
const cursorIndex = computed(() => {
  const at = flatRows.value.findIndex((entry) => entry.id === cursorId.value);
  return at >= 0 ? at : 0;
});

// When the cursor's card leaves the grid (deleted, hidden, filtered out), the
// cursor moves to the card that took its place, as a file manager's does.
// Falling back to the first row instead put focus there after a Delete and
// scrolled the grid to the top.
watch(flatRows, (rows, previous) => {
  if (!cursorId.value || rows.some((entry) => entry.id === cursorId.value))
    return;
  const at = previous?.findIndex((entry) => entry.id === cursorId.value) ?? -1;
  if (at < 0) return;
  const alive = new Set(rows.map((entry) => entry.id));
  const next =
    previous.slice(at + 1).find((entry) => alive.has(entry.id)) ??
    previous.slice(0, at).findLast((entry) => alive.has(entry.id));
  if (next) cursorId.value = next.id;
});

// ── Columns from the real container width ─────────────────────────────────
// The count the grid is TOLD to draw, so `auto-fill` cannot disagree with the
// arithmetic the cursor and the notch are built on.
function measure() {
  // The grid carries no padding (`.wfv-scroll` does), so `clientWidth` is the
  // track, not the track plus a border box's worth of inset.
  const width = gridEl.value?.clientWidth ?? 0;
  columns.value = Math.max(
    1,
    Math.floor((width + COLUMN_GAP) / (COLUMN_MIN + COLUMN_GAP)),
  );
}

let observer = null;
watch(gridEl, (element) => {
  observer?.disconnect();
  observer = null;
  if (!element || typeof ResizeObserver === "undefined") return;
  observer = new ResizeObserver(measure);
  observer.observe(element);
  measure();
});

onBeforeUnmount(() => {
  observer?.disconnect();
  observer = null;
});

/**
 * Where the reader was before they opened a picture, claimed once at SETUP.
 *
 * Taken here rather than read later because `unpark` clears it: claiming it
 * synchronously is what stops a second mount — a remount for any other reason
 * — from also thinking it is a return journey. Null on an ordinary arrival,
 * which is every visit that did not come back from a cover click.
 */
const parked = store.unpark();

/**
 * Put the reader back, once the cards it names actually exist.
 *
 * **Not in `onMounted`.** The grid is fetched there and the answer lands a
 * round trip later, so a restore that ran on mount would look for the cursor's
 * card in an empty list and give up; the store's cards can also already BE
 * there from a previous visit, which is why the watcher is `immediate` rather
 * than waiting for a change that may never come.
 *
 * By ID, never by index: `flatRows` is rebuilt by the refetch this mount
 * fires, and an index held across it names a different card. Once
 * applied it stops, so a later refetch — a file added, a workflow renamed —
 * does not yank the cursor back to where the reader was ten minutes ago.
 */
let placeRestored = false;
watch(
  () => store.sortedCards,
  () => {
    if (!parked || placeRestored || !store.loaded) return;
    placeRestored = true;
    nextTick(() => {
      if (scrollEl.value && parked.scrollTop) {
        scrollEl.value.scrollTop = parked.scrollTop;
      }
      const at = flatRows.value.findIndex(
        (entry) => entry.id === parked.cursorId,
      );
      // Only if the card is still there. A workflow hidden or deleted while
      // the reader was in the lightbox leaves the cursor where it falls back
      // to, which is the first row, rather than being moved to nothing.
      if (at >= 0) moveCursor(at);
    });
  },
  { immediate: true },
);

onMounted(async () => {
  store.fetchCards();
  favourites.ensureLoaded();
  try {
    const body = await listImportFolders();
    watchedFolder.value = (body?.folders ?? [])[0] ?? null;
  } catch (err) {
    // Not an error worth a banner: it costs the empty state one of its three
    // routes out, and the other two still work.
    console.warn("[workflows] could not read the watched folders", err);
  }
});

/** What the owner calls the folder: the host path when the server has one. */
const watchedPath = computed(
  () => watchedFolder.value?.host_path || watchedFolder.value?.folder || "",
);

// ── Arriving from a picture's Recipe section (#1313) ──────────────────────
//
// `?topology=<hash>` selects the workflow holding that topology (one of its
// card's `topologies`) and puts the cursor on it, so the link from the
// lightbox lands on the card rather than at the top of the grid.
//
// **It can only reach the cards the GRID lists, and that is not every card.**
// `GET /workflows` leaves out the hidden ones and the one-offs (under
// three pictures, unrated, not imported, no saved recipe) — which is the
// ordinary state of a workflow used once, and exactly when "what made this?"
// is worth asking.
//
// So a miss SAYS SO rather than doing nothing. Dropping the reader at the top
// of a grid that does not contain what they clicked, with no word about it, is
// the silent failure this screen must not have; `withheldNames` says what is
// being left out, so the sentence has something true to point at. F7
// replaces this link with the card's own *Show all N pictures* chip.
//
// Honoured once per value rather than on every `cards` change, because the
// grid is refetched (a file is added, a workflow renamed) while the query
// string stays put, and a second application would take focus back off
// whatever the reader had moved to. `immediate` because the store's cards
// outlive a route change: a second visit on the same link has nothing left to
// change for the watcher to see.
//
// **Only a HIT is marked honoured.** The store outlives this component, so the
// `immediate` pass runs during setup against whatever the last visit left in
// `cards` — before `onMounted` refetches. Marking a MISS honoured there makes
// a verdict reached on a stale list permanent: a workflow that has since
// crossed the one-off threshold, or arrived from the watched folder, is in the
// fresh answer, but the refetch's re-fire returns at the guard above and the
// screen goes on saying the opposite of what the grid holds. A hit still
// applies once, which is all the focus protection was ever for; only the "not
// here" verdict is left open to fresher data.
let honouredTopology = null;

/**
 * Say why a deep link's card is not drawn, or clear the notes when it is.
 * Returns the drawn card, or null.
 *
 * **The verdict is read off the payload, not off what is drawn.** With a
 * client-side filter on, `sortedCards` can be missing a card the answer
 * plainly contains, and saying "not in this grid" about it sends the reader
 * looking for a workflow that is one × away. The two are different states with
 * different ways out, so they are said differently.
 */
function linkVerdict(matches) {
  const card = store.sortedCards.find(matches);
  if (card) {
    linkMissed.value = false;
    linkFiltered.value = false;
    return card;
  }
  if (store.cards.some(matches)) {
    announcement.value =
      "That workflow is in this grid, but a filter is hiding it.";
    linkFiltered.value = true;
    linkMissed.value = false;
    return null;
  }
  announcement.value = withheldNames.value
    ? `That workflow is not in the grid. Left out: ${withheldNames.value}.`
    : "That workflow is not in the grid.";
  linkMissed.value = true;
  linkFiltered.value = false;
  return null;
}

/**
 * The query can go without a remount - the sidebar's Workflows entry pushes
 * this same route with no query - and the notes belong to the link, not to
 * the screen. Only once neither link is left: either watcher may be the one
 * whose query went.
 */
function clearLinkNotes() {
  if (route.query?.topology || route.query?.workflow) return;
  linkMissed.value = false;
  linkFiltered.value = false;
}

watch(
  [() => route.query?.topology, () => store.sortedCards],
  ([wanted]) => {
    if (!wanted) {
      honouredTopology = null;
      clearLinkNotes();
      return;
    }
    // A repeated `?topology=` makes this an array, which matches no card. It
    // takes the same path as any other miss rather than a branch of its own.
    if (honouredTopology === wanted) return;
    // Nothing to say until the grid has been read: "not here" is false while
    // the answer is still on the wire.
    if (!store.loaded) return;
    // ponytail: one topology can sit in several workflows (one per base-model
    // family), and this lands on the first that draws it. Only a picture the
    // hub has not keyed yet links by topology; give the link the family too if
    // landing on the wrong one ever matters.
    const card = linkVerdict((entry) =>
      (entry.topologies ?? []).includes(wanted),
    );
    if (!card) return;
    honouredTopology = wanted;
    store.select(card.id);
    // The one deep link that lands on a selection: it opens the inspector,
    // without persisting it, as `?workflow=` and `?tab=recipes` do.
    sidebarStore.openWorkflowInspector();
    // Not while the reader is inside a popover or the file dialog: the cards
    // arrive asynchronously, so this can fire a second after the screen went
    // interactive, and yanking focus out from under a gesture in progress is
    // worse than landing at the top of the grid. `onKeyDown` guards on the
    // same flag.
    //
    // The row is looked up BY ID inside the callback, never as an index
    // computed out here: `flatRows` is rebuilt by a resort and by a refetch,
    // and an index held across either names a different card. That is the
    // whole reason the cursor is an id (see `cursorId`).
    if (!sortMenuOpen.value) {
      nextTick(() =>
        moveCursor(
          flatRows.value.findIndex((entry) => entry.id === `card:${card.id}`),
        ),
      );
    }
  },
  { immediate: true },
);

// ── Arriving by workflow id (`?workflow=<id>`) ───────────────────────────
//
// The Run popup's *Open in Workflows* and the lightbox Edit tab's *Open*.
// `WorkflowTab` selects the card and opens the rail; this half puts the grid's
// cursor on it, which focuses the row and so scrolls it into view, as
// `?topology=` does. An id the grid does not list (hidden, a one-off, filtered
// out) moves nothing - the rail still shows the card - but says why, with the
// same notes `?topology=` gives.
//
// Honoured once per value and only on a hit, for the reasons given above.
let honouredWorkflowId = null;

watch(
  [() => route.query?.workflow, () => store.sortedCards],
  ([wanted]) => {
    if (typeof wanted !== "string" || !wanted) {
      honouredWorkflowId = null;
      clearLinkNotes();
      return;
    }
    if (honouredWorkflowId === wanted || !store.loaded) return;
    if (!linkVerdict((entry) => entry.id === wanted)) return;
    honouredWorkflowId = wanted;
    if (sortMenuOpen.value) return;
    nextTick(() =>
      moveCursor(
        flatRows.value.findIndex((entry) => entry.id === `card:${wanted}`),
      ),
    );
  },
  { immediate: true },
);

// ── The closed inspector (visual-language "Closed inspector") ─────────────
//
// A NEW selection nudges the inspector: the edge tab bounces and the rail
// toggle's glyph flashes, both only while the inspector is closed. Keyed on
// the selection's identity, so re-clicking the selected card (a fresh array
// holding the same key) is not news, and clearing the selection is not either.
watch(
  () => [...store.selectedKeys].sort().join("\n"),
  (identity) => {
    if (identity) sidebarStore.inspectorNudge += 1;
  },
);

/** What the closed inspector would describe: a name, `N workflows`, or "". */
const edgeTabLabel = computed(() => {
  if (onTemplates.value) {
    const chosen = templates.selectedTemplates;
    if (chosen.length === 1) return chosen[0].title ?? "";
    return chosen.length > 1 ? `${chosen.length} templates` : "";
  }
  // `runnableCard` is the one card the inspector describes.
  if (store.runnableCard) return store.runnableCard.name ?? "";
  const count = store.selectedKeys.length;
  return count > 1 ? `${count} workflows` : "";
});

// The same act as the toolbar toggle, so it persists. The tab unmounts as the
// inspector opens, so focus is handed to the inspector's active tab rather
// than dropping to `body`.
function openInspectorFromTab() {
  sidebarStore.toggleWorkflowInspector();
  nextTick(() =>
    document.querySelector(".wftab .inspector-tab--active")?.focus(),
  );
}

// Everything the folder ever brought in: the library, Imported from it.
function openWatchedFolder() {
  const folder = watchedFolder.value?.folder;
  if (!folder) return;
  filterStore.importedFromFilter = folder;
  router.push({ name: "all-pictures" });
}

// ── Selection ─────────────────────────────────────────────────────────────

function onRowClick(index, event) {
  const entry = flatRows.value[index];
  if (!entry) return;
  cursorId.value = entry.id;
  if (event?.shiftKey) selectToCursor(index);
  else store.select(entry.key, { additive: event?.ctrlKey || event?.metaKey });
}

/**
 * A double-click opens a shut inspector on the card's Workflow tab. Its two
 * clicks have already selected the card. A double-click on one of the card's
 * own buttons (Run it…, the heart) is that button's.
 */
function onRowDblClick(event) {
  if (event.target?.closest?.("button, a[href], [role='button']")) return;
  sidebarStore.showCardInInspector("workflows");
}

/**
 * A click on the scroller or a gap in the grid clears the selection, as on the
 * picture grid. Only those two: the unfiled recipes below are content too.
 *
 * Decided by where the press STARTED: a press that slips off one card onto
 * another fires its click on the grid, their common ancestor, and that is a
 * fumbled pick rather than a click on nothing.
 * Primary button only, and a click with `detail` 0 (Enter or Space on a
 * control inside, or a programmatic one) never counts: a press can end
 * without a click, and its leftover flag must not arm a later keyboard one.
 */
let pressedBackground = false;

function isBackground(target) {
  return target === scrollEl.value || target === gridEl.value;
}

function onBackgroundClick(event) {
  if (pressedBackground && event.detail > 0) store.clearSelection();
  pressedBackground = false;
}

/**
 * Every card between the first already-selected row and `index`.
 *
 * The anchor is read off the selection rather than remembered, so a Shift-click
 * after a Ctrl-click extends from the topmost of them.
 */
function selectToCursor(index) {
  const anchor = flatRows.value.findIndex((entry) =>
    store.selectedKeys.includes(entry.key),
  );
  const from = anchor < 0 ? index : anchor;
  const [start, end] = from <= index ? [from, index] : [index, from];
  store.selectRange(
    flatRows.value.slice(start, end + 1).map((entry) => entry.key),
  );
  // A Shift range can take in a screenful without the cursor passing over
  // most of it, so the count is said the way Ctrl+A's is.
  announceSelection();
}

// ── The roving cursor ─────────────────────────────────────────────────────

/** `index` when it names a row, else null. */
function rowAt(index) {
  return index >= 0 && index < flatRows.value.length ? index : null;
}

/**
 * The row above or below the cursor, keeping its column. Past either end it
 * settles on the first or last card, so the last row stays reachable from
 * every column.
 */
function verticalTarget(direction) {
  const cols = Math.max(1, columns.value);
  const target = cursorIndex.value + direction * cols;
  return rowAt(Math.min(Math.max(target, 0), flatRows.value.length - 1));
}

/** The DOM row for one flat entry. Keys are compared, never put in a selector. */
function rowElement(entry) {
  if (!entry) return undefined;
  return Array.from(gridEl.value?.querySelectorAll(".wfv-row") ?? []).find(
    (element) => element.dataset.key === entry.key,
  );
}

/** Put the cursor on whichever real row `index` names, if there is one. */
function moveCursor(index) {
  const entry = flatRows.value[index];
  if (!entry) return;
  cursorId.value = entry.id;
  nextTick(() => rowElement(entry)?.focus());
}

function onKeyDown(event) {
  // The sort popover owns its own keys, Escape included.
  if (sortMenuOpen.value) return;
  const entry = flatRows.value[cursorIndex.value];
  // Select all shown, the chord the pill's count menu prints a keycap for.
  // Checked before the switch because it is the only binding here that takes a
  // modifier, and a keycap naming a key nothing listens for is a lie the
  // reader can only discover by pressing it.
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "a") {
    event.preventDefault();
    selectAllShown();
    return;
  }
  // H favourites the focused card, or the selection it is in: a card the
  // gesture did not select is selected alone first, as a right-click does.
  if (
    event.key.toLowerCase() === FAVOURITE_KEY &&
    !event.ctrlKey &&
    !event.metaKey &&
    !event.altKey &&
    !event.shiftKey
  ) {
    event.preventDefault();
    if (entry) {
      if (!store.selectedKeys.includes(entry.key)) store.select(entry.key);
      toggleFavourites([...store.selectedKeys]);
    }
    return;
  }
  // R is Run…, the key the rail's footer prints beside it: the focused card,
  // or the selection it is in, which Run takes only one of. A draft has no
  // graph to run, so the key does nothing there, as the rail's button does.
  if (
    event.key.toLowerCase() === "r" &&
    !event.ctrlKey &&
    !event.metaKey &&
    !event.altKey &&
    !event.shiftKey
  ) {
    event.preventDefault();
    if (entry && !store.selectedKeys.includes(entry.key)) store.select(entry.key);
    if (!event.repeat && !store.runnableCard?.draft) runSelected();
    return;
  }
  switch (event.key) {
    case "ArrowRight":
      event.preventDefault();
      moveCursor(rowAt(cursorIndex.value + 1));
      return;
    case "ArrowLeft":
      event.preventDefault();
      moveCursor(rowAt(cursorIndex.value - 1));
      return;
    case "ArrowDown":
      event.preventDefault();
      moveCursor(verticalTarget(1));
      return;
    case "ArrowUp":
      event.preventDefault();
      moveCursor(verticalTarget(-1));
      return;
    case " ":
      event.preventDefault();
      if (entry) store.select(entry.key, { additive: true });
      return;
    case "Enter":
      event.preventDefault();
      // Enter opens ⓘ, the card's own button.
      activeCardButton(".wf-card__info")?.click();
      return;
    case "F2":
      // The rename key, everywhere a list has one. Single-selection only,
      // which is the gate the bar's button carries too.
      event.preventDefault();
      if (entry) {
        if (!store.selectedKeys.includes(entry.key)) store.select(entry.key);
        startRename();
      }
      return;
    case "Delete":
      // The menu's Delete…, behind the same gate and the same confirmation.
      // The selection's verb, as on the shelf and the picture grid: nothing
      // selected, nothing happens. One press, one ask: a held key must not
      // stack dialogs.
      event.preventDefault();
      if (!event.repeat && selBarRef.value?.deletable) confirmDelete();
      return;
    case "F10":
      if (!event.shiftKey) return;
      event.preventDefault();
      // The context-menu keys reach the card menu, which is what those keys
      // mean everywhere else in this app and in every file manager (#1455).
      openMenuAtCursor();
      return;
    case "ContextMenu":
      event.preventDefault();
      openMenuAtCursor();
      return;
    case "Escape":
      // The popover and the card menu are `VMenu`s: they consume their own
      // Escape and it never reaches here, so what is left is the selection.
      if (store.selectedKeys.length) {
        event.preventDefault();
        store.clearSelection();
      }
      return;
    default:
  }
}

/**
 * Keys pressed with the background focused. Only the ones that do not act on
 * the cursor card: from here the cursor is not visible, so Space, Enter, F2,
 * the arrows and the menu keys would hit a card the reader cannot see. Delete
 * acts on the selection, so Ctrl+A then Delete works from here too.
 */
function onBackgroundKeyDown(event) {
  const selectAll =
    (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "a";
  if (selectAll || event.key === "Escape" || event.key === "Delete") {
    onKeyDown(event);
  }
}

/**
 * Favourite, or unfavourite, some workflows: a card's heart, H, and the
 * selection's verb all come here. Under *Favourites only* a card the gesture
 * unfavourites leaves the grid, so the selection is pruned with it.
 */
function toggleFavourites(keys) {
  const on = !favourites.allFavourite("workflow", keys);
  favourites.toggle("workflow", keys);
  store.keepSelectionOnScreen();
  const n = keys.length;
  announcement.value = `${on ? "Favourited" : "Unfavourited"} ${
    n === 1 ? "workflow" : `${n} workflows`
  }`;
}

/** One of the cursor card's own `tabindex="-1"` buttons. */
function activeCardButton(selector) {
  return rowElement(flatRows.value[cursorIndex.value])?.querySelector(selector);
}

// ── The selection's verbs (#1455) ─────────────────────────────────────────
//
// The bar owns the gates and emits; this owns the confirmation, the dialog and
// the store call — `ModelShelf.vue` splits the shelf's the same way, and for
// the same reason: two confirmations half here and half there is how they
// drift apart.

/** The selected card, when exactly one is. Null otherwise. */
const onlyCard = computed(() =>
  store.selectedCards.length === 1 ? store.selectedCards[0] : null,
);

/**
 * Open a picture one of a card's covers draws, in the lightbox.
 *
 * The lightbox is mounted inside `ImageGrid`, which this screen REPLACES, so
 * the only way to it is the picture-grid route — and `?from=` is what makes
 * closing it come back here instead of leaving the reader on All Pictures
 * (#1446). This is the caller that mechanism has been waiting for.
 *
 * `route.path` and not the full route: `?topology=` is a one-shot instruction
 * from a Recipe link that this view honours on mount, so carrying it back
 * would re-select whichever workflow that link named over whichever one the
 * reader has since chosen.
 */
function openPicture(pictureId) {
  // Where the reader is, so the return lands here rather than at the top.
  // The store holds it because this component is remounted on the way back
  // (`App.vue` has no `<KeepAlive>` for this view), so nothing local survives.
  store.park({
    cursorId: cursorId.value,
    scrollTop: scrollEl.value?.scrollTop ?? 0,
  });
  router.push({
    name: "all-pictures",
    query: { overlay: String(pictureId), from: route.path },
  });
}

/**
 * Run one card: the Run popup, on the card rather than on a picture (#1407).
 *
 * The popup is `App.vue`'s and the store is how anything reaches it. No
 * picture behind it, so it shows the card's cover, an empty prompt and a set
 * picker for where the output is filed — the one thing a card-sourced run has
 * to be told and a picture-sourced one already knows.
 */
function runCard(card) {
  if (!card) return;
  runDialog.openRun({
    kind: "card",
    workflowId: card.id,
    name: card.name,
    // Through the helper: a raw `covers` entry is API-relative and an
    // `<img src>` would resolve it against the page origin instead.
    coverUrl: card.covers?.[0] ? workflowCoverUrl(card.covers[0]) : "",
    emptyPrompt: true,
  });
}

function runSelected() {
  runCard(store.runnableCard);
}

/** The menus' *Open in ComfyUI*, which the bar gates. */
function openSelectedInComfyui(original) {
  openWorkflowInComfyui(store.runnableCard?.id, original);
}

/**
 * Hide, or unhide, the selection.
 *
 * `unhide` is the bar's reading of the selection, handed down rather than
 * recomputed here: the button the reader pressed said which of the two it was
 * about to do, and a second derivation could disagree with the label they
 * just read.
 */
async function hideSelected(unhide) {
  const count = store.selectedKeys.length;
  const { refused } = await store.hideSelected(!unhide);
  const verb = unhide ? "unhidden" : "hidden";
  if (refused) {
    notices.push({
      level: "error",
      text:
        refused === count
          ? `None of those workflows could be ${verb}.`
          : `${count - refused} of ${count} workflows were ${verb}; the rest could not be.`,
    });
    return;
  }
  announcement.value =
    count === 1 ? `Workflow ${verb}` : `${count} workflows ${verb}`;
}

// ── Rename ────────────────────────────────────────────────────────────────

/**
 * Put focus back on the roving cursor's row.
 *
 * Every surface this view opens is TELEPORTED — the context menu to a pair of
 * coordinates, the rename dialog to the end of `<body>` — so none of them has
 * an activator the browser can restore focus to on close, and it lands on
 * `document.body`: outside the grid, with the cursor's row still marked and
 * the next arrow key going nowhere (WCAG 2.4.3).
 */
function focusCursorRow() {
  const entry = flatRows.value[cursorIndex.value];
  if (!entry) return;
  nextTick(() => rowElement(entry)?.focus());
}

// The Recipes tab's thumbnails, which live outside this view (see the store).
watch(
  () => store.pictureToOpen,
  (id) => {
    if (id == null) return;
    store.pictureToOpen = null;
    openPicture(id);
  },
);

/**
 * Open whichever picture the menu's open row named.
 *
 * The bar decides which that is — the tile the right-click landed on, else
 * the card's cover — so this takes the answer rather than recomputing it,
 * which is what keeps the row's label and what it opens the same thing.
 */
function openCoverPicture() {
  const id = selBarRef.value?.openTarget?.id;
  if (id != null) openPicture(id);
}

/** *Show all pictures using these workflows* (#1797): the grid, filtered. */
function showSelectedPictures() {
  showWorkflowsPictures(store.selectedCards);
}

function closeRename() {
  renameOpen.value = false;
  focusCursorRow();
}

/**
 * Open Rename on what the card is called now, selected.
 *
 * `name` is never null — the server falls back to the workflow file, then to a
 * description built from what the card loads and does — and the payload does
 * not say WHICH of the three it gave. So saving it untouched writes nothing
 * (`saveRename`): writing it would turn a generated name into a typed one, and
 * the card would stop following the workflow from then on, invisibly.
 * Emptying the field clears the stored name back to the generated one.
 */
function startRename() {
  const card = onlyCard.value;
  if (!card) return;
  renameDraft.value = card.name ?? "";
  renameOpen.value = true;
}

async function saveRename() {
  const card = onlyCard.value;
  closeRename();
  if (!card) return;
  const name = renameDraft.value.trim();
  if (name === String(card.name ?? "").trim()) return;
  if (await store.renameCard(card.id, name)) {
    // The cleared case says what the card is called NOW rather than "cleared":
    // the server re-derives a name immediately, so "cleared" would describe a
    // state the grid never shows. It is read back off the re-fetched card.
    const renamed = store.cards.find((entry) => entry.id === card.id);
    announcement.value = name
      ? `Renamed to ${name}`
      : `Name cleared, now ${renamed?.name ?? "unnamed"}`;
  }
}

// ── The custom thumbnail ──────────────────────────────────────────────────

const thumbnailInput = ref(null);

/**
 * The image *Choose thumbnail…* picked, onto the one selected card.
 *
 * Focus goes back to the cursor row however the picker closed (`cancel` is
 * the other way out): it was opened from code, so nothing else returns it.
 */
async function thumbnailChosen(event) {
  const file = event.target?.files?.[0];
  event.target.value = "";
  const card = onlyCard.value;
  try {
    if (file && card && (await store.setThumbnail(card.id, file))) {
      announcement.value = `Thumbnail set on ${card.name}`;
    }
  } finally {
    focusCursorRow();
  }
}

/** Pin the selected card's thumbnail over its pictures, or unpin it. */
async function pinThumbnail(pinned) {
  const card = onlyCard.value;
  if (!card) return;
  if (await store.pinThumbnail(card.id, pinned)) {
    announcement.value = pinned
      ? `${card.name} now always shows its thumbnail`
      : `${card.name} shows its ${outputNoun(card)}s once it has some`;
  }
}

// ── Export, duplicate, delete ─────────────────────────────────────────────

// A second Export while the first is still fetching or in its Save dialog
// would open another dialog.
let exporting = false;

/**
 * Save this card as a ComfyUI file somebody else can open.
 *
 * The scrub is the SERVER's (`GET /workflows/{id}/export`): prompts and seeds
 * blanked, LoRAs outside the default recipe bypassed, model names this machine does not hold
 * left out. What comes back names what it took out, and the notice says so —
 * a file that quietly differs from the workflow it was exported from is worse
 * than one that says which parts did not travel.
 */
async function exportSelected() {
  const card = onlyCard.value;
  if (!card || exporting) return;
  exporting = true;
  try {
    await exportCard(card);
  } finally {
    exporting = false;
  }
}

async function exportCard(card) {
  let body;
  try {
    body = await exportWorkflow(card.id);
  } catch (err) {
    console.warn(`[workflows] could not export ${card.id}`, err);
    notices.push({
      level: "error",
      text: errorMessage(err, "That workflow could not be exported."),
    });
    return;
  }
  const filename = body.filename || "workflow.json";
  try {
    if (!(await saveJsonAs(body.workflow, filename))) return;
  } catch (err) {
    console.warn(`[workflows] could not save ${filename}`, err);
    notices.push({
      level: "error",
      text: errorMessage(err, "That workflow could not be saved."),
    });
    return;
  }
  notices.push({
    level: "success",
    text: body.removed?.length
      ? `Exported ${filename}, without: ${body.removed.join(", ")}.`
      : `Exported ${filename}.`,
  });
}

async function duplicateSelected() {
  const card = onlyCard.value;
  if (!card) return;
  const body = await store.duplicateCard(card.id);
  if (!body) return;
  notices.push({ level: "success", text: `Duplicated as ${body.name}.` });
  landOn(body.workflow_id);
}

async function inpaintCopySelected() {
  const card = onlyCard.value;
  if (!card) return;
  const body = await store.inpaintCopyCard(card.id);
  if (!body) return;
  notices.push({ level: "success", text: `Converted as ${body.name}.` });
  landOn(body.workflow_id);
}

/**
 * Select a workflow this view just made (a duplicate, an import) and put
 * the cursor on its card, so the reader's next look is at what they made.
 * The grid has been re-read by then; a card a filter hides is still selected,
 * and the inspector shows it.
 */
function landOn(workflowId) {
  if (!workflowId) return;
  store.select(workflowId);
  const at = flatRows.value.findIndex((entry) => entry.key === workflowId);
  if (at >= 0) moveCursor(at);
}

/**
 * *Change model set…* from the grid's menu. The menu itself is the Workflow tab's, in
 * the rail: it is asked for through the store, and the rail is opened (not
 * persisted, as a deep link's "show me" is) so there is somewhere for it to
 * open.
 */
function useSetOnSelected() {
  const card = onlyCard.value;
  if (!card) return;
  sidebarStore.openWorkflowInspector();
  store.requestUseSet(card.id);
}

/**
 * *Download missing models* from the card menu: ask the run pre-flight what
 * each selected workflow lacks (as the Workflow tab does, from ComfyUI's last
 * node list), and download every file PixlStash knows where to get. A draft
 * has no graph to ask about.
 */
// A second press while the checks run would start the same files twice.
let checkingMissing = false;
async function downloadMissingSelected() {
  if (checkingMissing) return;
  checkingMissing = true;
  try {
    await downloadMissingOf(store.selectedCards.filter((card) => !card.draft));
  } finally {
    checkingMissing = false;
  }
}

async function downloadMissingOf(cards) {
  const { files, unasked } = await downloadableInWorkflows(cards);
  if (unasked === cards.length && cards.length) {
    notices.error(
      cards.length === 1
        ? "Could not check what this workflow is missing."
        : "Could not check what these workflows are missing.",
    );
    return;
  }
  if (unasked) {
    notices.push({
      level: "warning",
      text: `Could not check what ${unasked} of these ${cards.length} workflows are missing.`,
    });
  }
  if (!files.length) {
    notices.push({
      level: "info",
      text:
        cards.length === 1
          ? "Nothing this workflow is missing can be downloaded."
          : "Nothing these workflows are missing can be downloaded.",
    });
    return;
  }
  await downloadModels(files);
}

/**
 * Delete the selected MANUAL workflows, after saying what happens to their
 * pictures.
 *
 * The one destructive verb here, so the one that asks first. The pictures
 * stay, filed back under their automatic workflow, which the question says,
 * because "delete this workflow" reads like losing the pictures it made.
 */
async function confirmDelete() {
  const cards = [...store.selectedCards];
  if (!cards.length) return;
  const one = cards.length === 1;
  const noun = cards.every((card) => outputNoun(card) === "video")
    ? "videos"
    : "pictures";
  const ok = await confirm({
    title: one ? "Delete this workflow?" : `Delete these ${cards.length} workflows?`,
    message:
      `${one ? `“${cards[0].name}”` : "They"} ${one ? "goes" : "go"} to your ` +
      `system trash. The ${noun} stay, filed under their automatic ` +
      "workflow, and saved recipes on it are listed under Unfiled recipes.",
    confirmLabel: "Delete",
    danger: true,
  });
  if (!ok) return;
  // A selection made without the cursor (Ctrl+A, the rail) still leaves it on
  // a deleted card, so it lands beside them rather than on the first row.
  if (!cursorId.value) cursorId.value = `card:${cards[0].id}`;
  const { refused } = await store.deleteSelected();
  // The focused row may have just gone, even in a partial delete, which drops
  // focus to <body>, where the next key reaches nothing.
  focusCursorRow();
  if (refused) {
    notices.push({
      level: "error",
      text:
        refused === cards.length
          ? "None of those workflows could be deleted."
          : `${cards.length - refused} of ${cards.length} workflows were deleted; the rest could not be.`,
    });
    return;
  }
  announcement.value = one
    ? "Workflow deleted"
    : `${cards.length} workflows deleted`;
}

// ── The card menu ─────────────────────────────────────────────────────────

/**
 * Right-click a card: the full verb inventory, at the pointer.
 *
 * The file-manager rule, which is the shelf's and the picture grid's: right-
 * clicking a card that is NOT selected selects it and acts on it alone;
 * right-clicking one that IS leaves the selection alone, so a menu opened on
 * any of forty selected cards acts on all forty. Without that, the commonest
 * gesture in a bulk edit — select, then right-click one of them — would
 * silently drop the other thirty-nine.
 */
function openRowMenu(index, event) {
  const entry = flatRows.value[index];
  if (!entry) return;
  cursorId.value = entry.id;
  if (!store.selectedKeys.includes(entry.key)) store.select(entry.key);
  const tile = pictureUnder(event);
  selBarRef.value?.openContextMenu(
    event.clientX,
    event.clientY,
    tile && { ...tile, noun: outputNoun(entry.card) },
  );
}

/**
 * The cover tile a pointer event landed on, or null.
 *
 * Read off the EVENT's own target rather than tracked by the card: a
 * right-click on a card's third thumbnail and one on its name row are the same
 * `contextmenu` on the same row, and the only thing that tells them apart is
 * where the pointer was. The tile carries its own id and position
 * (`WorkflowCard.vue`), so this is a lookup rather than a second piece of
 * state that can go stale.
 *
 * `closest`, not the target itself: the press lands on the `<img>` inside the
 * cell rather than on the cell.
 */
function pictureUnder(event) {
  const tile = event.target?.closest?.(".wf-card__pic");
  const id = Number(tile?.dataset.pictureId);
  if (!tile || !Number.isFinite(id)) return null;
  return {
    id,
    index: Number(tile.dataset.pictureIndex) || 1,
    total: Number(tile.dataset.pictureTotal) || 1,
  };
}

/**
 * Open the card menu over the cursor row's own box, for the keyboard's sake.
 *
 * **Nothing to point at is nothing to open.** With the cursor out of range
 * of a list that has just shrunk, there is no card the
 * menu would act on — it opened all-disabled at viewport (0, 0), which is a
 * menu about nothing in the corner of the screen.
 */
function openMenuAtCursor() {
  const entry = flatRows.value[cursorIndex.value];
  if (!entry) return;
  if (!store.selectedKeys.includes(entry.key)) store.select(entry.key);
  const box = rowElement(entry)?.getBoundingClientRect();
  selBarRef.value?.openContextMenu(
    box ? box.left + 24 : 0,
    box ? box.bottom : 0,
  );
}

/**
 * *Select all shown*, from the pill's count menu and from Ctrl/Cmd+A.
 */
function selectAllShown() {
  store.selectRange(store.sortedCards.map((card) => card.id));
  // **Said aloud, because nothing else says it.** Ctrl/Cmd+A can select the
  // whole grid without moving the cursor or changing a single visible row a
  // screen reader is on, and the pill is a `role="toolbar"` rather than a live
  // region — so the selection it counts changed in silence.
  announceSelection();
}

/** How many cards are selected, for the live region. */
function announceSelection() {
  const n = store.selectedKeys.length;
  announcement.value = n === 1 ? "1 workflow selected" : `${n} workflows selected`;
}

// ── Add a workflow file ───────────────────────────────────────────────────

/** Larger than any real workflow; a bigger file is not read into memory. */
const MAX_WORKFLOW_BYTES = 50 * 1024 * 1024;

function filesChosen(event) {
  const files = Array.from(event.target?.files ?? []);
  event.target.value = "";
  return addWorkflowFiles(files);
}

/**
 * Add workflow files, from the file picker or a drop: each one is a new manual
 * workflow, and the last to land is opened.
 *
 * A file the library already has (the server's 409) is not added: the view
 * lands on the workflow it already is when nothing new landed, that card
 * pulses, and the note names it. A hidden one turns *Show hidden* on, since a
 * card the grid leaves out cannot be shown.
 */
async function addWorkflowFiles(files) {
  let last = null;
  let added = 0;
  const refused = [];
  alreadyHere.value = [];
  pulseKeys.value = [];
  revealedHidden.value = false;
  for (const file of files) {
    if (file.size > MAX_WORKFLOW_BYTES) {
      store.error = `${file.name} is too large to be a workflow.`;
      continue;
    }
    try {
      last = await importWorkflow({
        name: file.name.replace(/\.json$/i, ""),
        workflow: JSON.parse(await file.text()),
      });
      added += 1;
    } catch (err) {
      const existing = existingWorkflow(err);
      if (existing) {
        refused.push({ file: file.name, ...existing });
        continue;
      }
      store.error = errorMessage(
        err,
        `Could not add ${file.name}: it may not be a workflow.`,
      );
      console.warn(`[workflows] could not add ${file.name}`, err);
    }
  }
  if (!last && !refused.length) return;
  // A drop on the Templates tab still adds to Mine, so show Mine.
  if (onTemplates.value) showSource("mine");
  if (refused.some((r) => r.hidden) && !store.filters.showHidden) {
    store.setFilters({ showHidden: true });
    revealedHidden.value = true;
  }
  await store.refetch();
  landOn(last ? last.workflow_id : refused[refused.length - 1].workflow_id);
  alreadyHere.value = refused;
  pulseKeys.value = [...new Set(refused.map((r) => r.workflow_id))];
  // Off on a timer, not on `animationend`: a key left on would pulse the
  // card again whenever it remounts (a filter toggled off and on).
  clearTimeout(pulseTimer);
  pulseTimer = setTimeout(() => (pulseKeys.value = []), PULSE_MS);
  // Opened on the last, but the live region says how many arrived and which
  // were here already.
  const parts = [];
  if (added) parts.push(added > 1 ? `Added ${added} workflows` : `Added ${last.name}`);
  if (refused.length) {
    const subject = added
      ? refused.length
      : refused.length > 1
        ? `${refused.length} files`
        : refused[0].file;
    parts.push(
      `${subject} ${refused.length > 1 ? "are" : "is"} already here as ${refusedNames(refused)}`,
    );
  }
  announcement.value = parts.join("; ");
}

/** The names of the workflows refused files already are, once each. */
function refusedNames(refused) {
  return [...new Set(refused.map((r) => r.name || "an unnamed workflow"))].join(
    ", ",
  );
}

/** Whether a client-side filter hides a card the note names. */
const alreadyHereFiltered = computed(() =>
  alreadyHere.value.some(
    (r) =>
      !store.sortedCards.some((card) => card.id === r.workflow_id) &&
      store.cards.some((card) => card.id === r.workflow_id),
  ),
);

const alreadyHereSentence = computed(() => {
  const refused = alreadyHere.value;
  if (!refused.length) return "";
  const names = refusedNames(refused);
  let sentence =
    refused.length === 1
      ? `${refused[0].file} is already here as ${names}, so it was not added again.`
      : `${refused.length} files are already here, so they were not added again: ${names}.`;
  if (revealedHidden.value && store.filters.showHidden) {
    sentence += ` ${refused.length === 1 ? "It is" : "One is"} hidden, so hidden workflows are shown now.`;
  }
  if (alreadyHereFiltered.value) sentence += " A filter is hiding it.";
  return sentence;
});

// ── Drop a workflow file ──────────────────────────────────────────────────
//
// Only a drag carrying files is a drop target, so a card or text drag passes
// over the view untouched. An in-app picture drag is not one even when the
// desktop shell fills its `files` (`isInternalImageDrag`, the window
// importer's guard). `dragenter` and `dragleave` fire for every child crossed,
// hence a depth count rather than a boolean.

const dropActive = ref(false);
let dragDepth = 0;

function carriesFiles(event) {
  if (isInternalImageDrag(event.dataTransfer)) return false;
  return Array.from(event.dataTransfer?.types || []).includes("Files");
}

function onFileDragEnter(event) {
  if (!carriesFiles(event)) return;
  event.preventDefault();
  dragDepth += 1;
  dropActive.value = true;
}

function onFileDragOver(event) {
  if (!carriesFiles(event)) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = "copy";
}

function onFileDragLeave(event) {
  if (!carriesFiles(event)) return;
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) dropActive.value = false;
}

/**
 * Only the `.json` files are this view's. Anything else in the same drop is
 * the window-wide importer's, which stands aside only for a drop of JSON
 * alone, so it is left to that and not reported twice.
 */
function onFileDrop(event) {
  if (!carriesFiles(event)) return;
  event.preventDefault();
  dragDepth = 0;
  dropActive.value = false;
  const files = Array.from(event.dataTransfer.files || []).filter(isWorkflowFile);
  if (files.length) return addWorkflowFiles(files);
}

// A drop of model files alone never reaches `onFileDrop`: the window importer
// claims it in the capture phase. So the wash is put out at the window too.
function endFileDrag() {
  dragDepth = 0;
  dropActive.value = false;
}
onMounted(() => {
  window.addEventListener("drop", endFileDrag, true);
  window.addEventListener("dragend", endFileDrag, true);
});
onBeforeUnmount(() => {
  window.removeEventListener("drop", endFileDrag, true);
  window.removeEventListener("dragend", endFileDrag, true);
});
</script>

<style scoped>
.wfv {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
  overflow: hidden;
  /* The positioned ancestor `.selbar-float` is absolute against: only the
     container knows where its list ends, which is why the shared float rule
     is absolute rather than fixed (`App.css`). */
  position: relative;
}

/* The drop target is marked the way the design system marks a selection and
   the model shelf marks its drop bands: the olive wash and ring. On a layer
   above the toolbar and the cards, which are opaque and would hide the ring
   along the top; an outline rather than a shadow so it survives forced
   colours. */
.wfv--drop::after {
  content: "";
  position: absolute;
  inset: 0;
  z-index: var(--z-floating);
  pointer-events: none;
  background: var(--active-wash);
  outline: 2px solid var(--active-bar);
  outline-offset: -2px;
}

/* The retired workflow shelf's box recipe, carried over so the screen that
   replaced it sits at the same height beside the model shelf: a fixed 36
   with the hairline INSIDE it and no vertical padding. `--bar-height` is 48
   and would not. `container-name` carries the names the shared chrome's own
   `@container` rules are written against — the class attribute alone does
   nothing, they are container names and not classes. */
.wfv-toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  container-type: inline-size;
  container-name: shelfbar toolbar;
  box-sizing: border-box;
  flex: none;
  height: 36px;
  padding: 0 var(--space-3) 0 var(--space-5);
  background: rgb(var(--v-theme-toolbar));
  border-bottom: 1px solid rgb(var(--v-theme-divider));
}

.wfv-spacer {
  flex: 1;
}

/* The chips in the bar: the strip's own row chrome dropped, so each chip sits
   on the 36px band. The strip shrinks (its chips scroll) rather than pushing
   the Search field off the right edge. */
.wfv-toolbar .wfv-strip {
  flex: 0 1 auto;
  min-width: 0;
  height: auto;
  padding: 0;
  background: none;
  border-bottom: 0;
}

/* The Search field: 260px, and it gives room to the toolbar's other controls
   before it shrinks below 96px. The F keycap sits inside the field's right
   edge, where the text does not reach. */
.wfv-search {
  position: relative;
  flex: 0 1 260px;
  min-width: 96px;
}
.wfv-search :deep(.tbm-input) {
  padding-right: var(--space-7);
}
.wfv-search-key {
  position: absolute;
  top: 50%;
  right: var(--space-3);
  transform: translateY(-50%);
  pointer-events: none;
}

/* The app-wide tail's own gap: this bar spaces its controls at --space-4, and
   the tail must land at the --space-3 every other host lays it out at. Never
   the member that gives — it is the app-wide chrome, and the control that
   opens the rail must not be the one the edge eats. */
.wfv-bar-tail {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex: 0 0 auto;
}

/* The pill's container: its words drop below this width (WorkflowSelectionBar's
   `wfpill` query), so the pill never runs off the list. */
.wfv-pill-host {
  container: wfpill / inline-size;
}

.wfv-error {
  margin: 0;
  padding: var(--space-3) var(--space-5);
  color: rgb(var(--v-theme-error));
  font-size: var(--text-sm);
}

/* Not an error - the grid is behaving as designed and the reader simply asked
   for something it does not draw - so the quiet ink rather than the error one. */
.wfv-note {
  margin: 0;
  padding: var(--space-3) var(--space-5);
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: var(--text-sm);
}
/* The rename dialog's helper line. Deliberately NOT `.wfv-note`: that class is
   a band the GRID shows when it has something to say, and this is body copy
   inside a dialog. */
.wfv-hint {
  margin: 0;
  padding-top: var(--space-3);
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: var(--text-sm);
}

.wfv-note-clear {
  /* The inline-button reset again — without `font: inherit` this one also
     loses `.wfv-note`'s `--text-sm` to the UA's button size. */
  padding: 0;
  font: inherit;
  color: rgb(var(--v-theme-on-surface));
  text-decoration: underline;
}
.wfv-note-clear:hover {
  color: rgb(var(--v-theme-primary));
}

/* The scroller, and the only thing here carrying padding: the grid inside it
   has to be a bare track, because its `clientWidth` is what the column count
   is computed from and that measurement includes an element's own padding. */
.wfv-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  /* `--space-3`, not the `--space-5` this shipped with. 16 down every side is
     what Moves and Insights use, and those say in as many words that they are
     reading surfaces; the picture grid, which this is actually a sibling of,
     carries no side padding at all and lets the scrollbar's own gutter be the
     gap. Cards have a border, so bleeding them to the window edge would look
     like a mistake - 8 is the smallest inset that still reads as deliberate.

     `scrollbar-gutter: stable` is reserved as the model shelf reserves it,
     though for a smaller reason than the shelf's: nothing here breaks without
     it - the grid's `clientWidth` shrinks, the `ResizeObserver` fires and the
     columns recompute correctly - but the whole track jumps sideways the
     moment the grid crosses one screen. Reserved, the only movement is the
     vertical one the reader asked for. */
  scrollbar-gutter: stable;
  padding: var(--space-3);
}

/* Focused by a background click, as the target for Ctrl+A. The ring is the
   inset form (visual-language.md §11) without the glow: anything outset would
   sit outside the pane, under the toolbar and the inspector edge. */
.wfv-scroll:focus-visible {
  outline: none;
  box-shadow: inset 0 0 0 var(--focus-width) var(--focus-stroke);
}

/* The closed inspector's edge tab (34px) floats over this edge: the cards
   stop short of it, so a click on a card's edge never lands on the tab and
   opens the inspector under the pointer. The scrollbar still runs under the
   tab's band, which is why it opens on click only. */
.wfv-scroll--edge-tab {
  padding-right: calc(34px + var(--space-3));
}

/* `--wf-columns` is set from the measured width, so the painted grid and the
   cursor's arithmetic cannot disagree; `--wf-gap` and `--wf-column-min` are
   the two numbers that count was derived from, written by the same JS that
   derived it. The gap is `--space-4` and the floor is the card's own width,
   but they are set from script rather than read from the sheet because a
   token edit that moved one without the other would leave the painted grid
   and the cursor's arithmetic disagreeing silently — which is the exact
   failure this whole "computed, not auto-fill" design exists to prevent. */
.wfv-grid {
  display: grid;
  grid-template-columns: repeat(var(--wf-columns), minmax(0, 1fr));
  align-content: start;
  gap: var(--wf-gap);
}

.wfv-cell {
  border-radius: var(--radius-md);
}

/* Outside the card, which paints an opaque surface over its whole cell. */
.wfv-cell--drop {
  outline: 2px solid var(--active-bar);
  outline-offset: 2px;
}

/* "This one is already here": the selection's olive ring, sent outward twice
   as an expressive one-shot (`--dur-4`), so the eye finds the card the view
   just moved to. The selection ring stays; the token file's global
   reduced-motion rule leaves only that and the note. */
.wfv-cell--pulse {
  animation: wfv-cell-pulse var(--dur-4) var(--ease-standard) 2;
}
@keyframes wfv-cell-pulse {
  from {
    outline: 2px solid var(--active-bar);
    outline-offset: 2px;
  }
  to {
    outline: 2px solid transparent;
    outline-offset: var(--space-3);
  }
}
/* Below the cards, on their columns: `--wf-columns` is set inline from the
   same measurement the grid uses. */
.wfv-unfiled {
  margin-top: var(--space-6);
}

.wfv-unfiled :deep(.wfrt-list) {
  display: grid;
  grid-template-columns: repeat(var(--wf-columns), minmax(0, 1fr));
  gap: var(--space-3);
}

/* The selection mark is the CARD's own (`WorkflowCard.vue`,
   `.wf-card--selected`). It was here, on the cell, and never showed: the card
   paints an opaque surface across the whole cell and covered it. */

.wfv-file-input {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
  pointer-events: none;
}

/* The §9 empty state: the shipped art, a Tiny5 `--text-2xl` headline, a
   `--text-sm` line, and the routes out. */
.wfv-empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: auto;
}

.wfv-empty__card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-4);
  width: min(480px, calc(100% - var(--space-7)));
  box-sizing: border-box;
  margin: auto;
  padding: var(--space-6) var(--space-7);
  border-radius: var(--radius-lg);
  background: rgb(var(--v-theme-panel));
  color: rgb(var(--v-theme-on-background));
  text-align: center;
  box-shadow: var(--elevation-3);
}

/* The card and the Unfiled recipes under it, centred as one. `margin: auto`
   rather than the parent's centring, so a stack taller than the pane scrolls
   from its top instead of clipping it. */
.wfv-empty__stack {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 100%;
  margin: auto;
}

/* At the card's width. The gap and the bottom inset go on the header and the
   list, which are only drawn while there are recipes: with none, the card sits
   exactly where it always has. */
.wfv-unfiled.wfv-empty__recipes {
  width: min(480px, calc(100% - var(--space-7)));
  margin-top: 0;
}

.wfv-empty__recipes :deep(.wfrt-head) {
  margin-top: var(--space-6);
}

.wfv-empty__recipes :deep(.wfrt-list) {
  margin-bottom: var(--space-6);
}

/* The cap `LibraryEmptyState` uses, so the two cards show the art at one size. */
.wfv-empty__illustration {
  width: 90%;
  max-width: 260px;
  color: rgba(var(--v-theme-on-panel), 0.45);
}

.wfv-empty__illustration img {
  display: block;
  width: 100%;
  height: auto;
}

.wfv-empty__title {
  margin: 0;
  font-family: var(--font-pixel);
  font-size: var(--text-2xl);
  font-weight: var(--weight-regular);
  line-height: var(--leading-tight);
}

.wfv-empty__lead {
  margin: 0;
  max-width: 46ch;
  color: rgba(var(--v-theme-on-background), 0.72);
  font-size: var(--text-sm);
  line-height: var(--leading-body);
}

.wfv-empty__actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: var(--space-3);
}

/* The path is a value, not prose: monospace, and it wraps rather than
   ellipsizes, because half a path names the wrong folder. */
.wfv-empty__path {
  margin: 0;
  max-width: 100%;
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-background), 0.72);
  overflow-wrap: anywhere;
}
</style>
