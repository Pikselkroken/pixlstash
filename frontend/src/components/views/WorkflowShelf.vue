<template>
  <!-- role="region" because a bare div is role `generic`, which prohibits an
       accessible name: without it the aria-label is dropped and #wf-help is
       never announced. -->
  <div
    ref="rootEl"
    class="wfshelf"
    role="region"
    tabindex="-1"
    aria-label="Workflows"
    aria-describedby="wf-help"
  >
    <p id="wf-help" class="visually-hidden">
      Every workflow PixlStash has found in the pictures it has read. One row is
      one graph, however many models it was bound to; the variants under a row
      are the same graph with different models, and Right and Left open and
      close them. Group, Sort and Show choose the order, the bands and which
      rows are listed. Nothing on this screen writes anything. Right-click a row
      for what can be done with it. Escape clears the selection.
    </p>

    <!-- One announcement for a resort, because the rows reorder silently: the
         buttons' own names change, but a reader who is not on them hears
         nothing. -->
    <p class="visually-hidden" role="status">{{ announcement }}</p>

    <div class="wfshelf-toolbar shelfbar toolbar">
      <span class="wfshelf-title">Workflows</span>
      <span class="wfshelf-sub num">{{ subtitle }}</span>

      <v-menu
        v-model="groupMenuOpen"
        location="bottom start"
        origin="top start"
        :offset="8"
        :close-on-content-click="false"
      >
        <template #activator="{ props: menuProps }">
          <button
            v-bind="menuProps"
            class="bar-btn"
            type="button"
            aria-haspopup="menu"
            :aria-expanded="groupMenuOpen"
          >
            Group: {{ GROUP_LABELS[store.view.groupBy].label }}
          </button>
        </template>
        <div class="tbm">
          <span class="tbm-caret tbm-caret--start"></span>
          <div class="tbm-header">
            <v-icon size="18" class="tbm-header-icon"
              >mdi-format-list-group</v-icon
            >
            <span class="tbm-title">Group</span>
          </div>
          <div class="tbm-section">
            <span class="tbm-label">Group by</span>
            <div class="tbm-grid-3" role="group" aria-label="Group by">
              <button
                v-for="key in GROUP_BY_KEYS"
                :key="key"
                class="tbm-toggle"
                :class="{ 'tbm-toggle--on': store.view.groupBy === key }"
                type="button"
                :aria-pressed="store.view.groupBy === key"
                @click="store.setView({ groupBy: key })"
              >
                <v-icon size="18" class="tbm-toggle-icon">{{
                  GROUP_LABELS[key].icon
                }}</v-icon>
                <span class="tbm-toggle-label">{{
                  GROUP_LABELS[key].label
                }}</span>
              </button>
            </div>
          </div>
        </div>
      </v-menu>

      <v-menu
        v-model="sortMenuOpen"
        location="bottom start"
        origin="top start"
        :offset="8"
        :close-on-content-click="false"
      >
        <template #activator="{ props: menuProps }">
          <button
            v-bind="menuProps"
            class="bar-btn"
            type="button"
            aria-haspopup="menu"
            :aria-expanded="sortMenuOpen"
          >
            Sort: {{ SORT_LABELS[store.view.sortKey].label }}
          </button>
        </template>
        <div class="tbm">
          <span class="tbm-caret tbm-caret--start"></span>
          <div class="tbm-header">
            <v-icon size="18" class="tbm-header-icon">mdi-sort</v-icon>
            <span class="tbm-title">Sort</span>
            <span class="tbm-spacer"></span>
            <!-- The direction lives in the header rather than as a sixth
                 option: it is a property of whichever key is chosen. -->
            <button
              class="tbm-ghost"
              type="button"
              @click="store.setView({ descending: !store.view.descending })"
            >
              <v-icon size="16">{{
                store.view.descending ? "mdi-arrow-down" : "mdi-arrow-up"
              }}</v-icon>
              <span>{{
                store.view.descending ? "Most first" : "Least first"
              }}</span>
            </button>
          </div>
          <div class="tbm-section">
            <span class="tbm-label">Sort by</span>
            <div class="tbm-grid-2" role="group" aria-label="Sort by">
              <button
                v-for="key in SORT_KEYS"
                :key="key"
                class="tbm-toggle"
                :class="{ 'tbm-toggle--on': store.view.sortKey === key }"
                type="button"
                :aria-pressed="store.view.sortKey === key"
                @click="store.setView({ sortKey: key })"
              >
                <v-icon size="18" class="tbm-toggle-icon">{{
                  SORT_LABELS[key].icon
                }}</v-icon>
                <span class="tbm-toggle-label">{{
                  SORT_LABELS[key].label
                }}</span>
              </button>
            </div>
          </div>
        </div>
      </v-menu>

      <v-menu
        v-model="showMenuOpen"
        location="bottom start"
        origin="top start"
        :offset="8"
        :close-on-content-click="false"
      >
        <template #activator="{ props: menuProps }">
          <button
            v-bind="menuProps"
            class="bar-btn"
            type="button"
            aria-haspopup="menu"
            :aria-expanded="showMenuOpen"
          >
            Show: {{ SHOW_LABELS[store.view.show].label }}
          </button>
        </template>
        <div class="tbm">
          <span class="tbm-caret tbm-caret--start"></span>
          <div class="tbm-header">
            <v-icon size="18" class="tbm-header-icon"
              >mdi-filter-outline</v-icon
            >
            <span class="tbm-title">Show</span>
          </div>
          <div class="tbm-section">
            <span class="tbm-label">Which workflows</span>
            <div class="tbm-grid-3" role="group" aria-label="Which workflows">
              <button
                v-for="key in SHOW_KEYS"
                :key="key"
                class="tbm-toggle"
                :class="{ 'tbm-toggle--on': store.view.show === key }"
                type="button"
                :aria-pressed="store.view.show === key"
                @click="store.setView({ show: key })"
              >
                <v-icon size="18" class="tbm-toggle-icon">{{
                  SHOW_LABELS[key].icon
                }}</v-icon>
                <span class="tbm-toggle-label">{{
                  SHOW_LABELS[key].label
                }}</span>
              </button>
            </div>
          </div>
        </div>
      </v-menu>

      <!-- The bar deliberately ends here. F11's ghosts chip lands beside these
           three, so the room it needs is left rather than filled. -->
      <span class="wfshelf-spacer"></span>
    </div>

    <div class="wfshelf-head" aria-hidden="true">
      <span class="wfshelf-head-ident"></span>
      <span class="wfshelf-head-cell wfshelf-col--name">Name</span>
      <span class="wfshelf-head-cell wfshelf-col--num">Pictures</span>
      <span class="wfshelf-head-cell wfshelf-col--num">Variants</span>
      <span class="wfshelf-head-cell wfshelf-col--models">Models</span>
      <span class="wfshelf-head-cell wfshelf-col--date">Last used</span>
    </div>

    <div class="wfshelf-scroll">
      <div
        v-if="store.error"
        class="wfshelf-note wfshelf-note--error"
        role="alert"
      >
        {{ store.error }}
      </div>

      <!-- The four states of an empty list. Three of them are the first thing a
           new user sees and none is a failure, so none wears an error hue: each
           says which of the three it is — not looked yet, looking, or looked and
           there is genuinely nothing. -->
      <div v-if="!store.error && emptyState" class="wfshelf-empty">
        <v-icon size="34" class="wfshelf-empty-icon">{{
          emptyState.icon
        }}</v-icon>
        <p class="wfshelf-empty-title">{{ emptyState.title }}</p>
        <p class="wfshelf-empty-body">{{ emptyState.body }}</p>
      </div>

      <div v-for="group in store.groups" :key="group.key ?? '__flat__'">
        <h3 v-if="group.key" class="wfshelf-group">
          <button
            class="wfshelf-group-btn"
            type="button"
            :aria-expanded="!store.isCollapsed(group.key)"
            @click="store.toggleCollapsed(group.key)"
          >
            <v-icon size="18">{{
              store.isCollapsed(group.key)
                ? "mdi-chevron-right"
                : "mdi-chevron-down"
            }}</v-icon>
            <span class="wfshelf-group-label">{{ group.label }}</span>
            <span class="wfshelf-spacer"></span>
            <span class="wfshelf-group-count num">{{
              workflowCount(group.rows.length)
            }}</span>
          </button>
        </h3>

        <!-- role="treegrid": the rows have columns, and a variant is a CHILD
             row of the workflow above it. That is the same keyboard model the
             list already implements — Up and Down walk rows, Right and Left
             open and close a workflow — and unlike a listbox it can carry a
             columnheader, so the figures in a row are named. -->
        <ul
          v-if="!group.key || !store.isCollapsed(group.key)"
          class="wfshelf-list"
          role="treegrid"
          :aria-label="group.key ? group.label : 'Workflows'"
        >
          <!-- The column names, on every grid and drawn on none of them: a
               columnheader heads the grid it is in, so grouping needs one strip
               per group, and a visible band per group is what the strip above
               the list exists to avoid. -->
          <li class="visually-hidden" role="row">
            <span role="columnheader">Workflow</span>
            <span role="columnheader">Name</span>
            <span role="columnheader">Pictures</span>
            <span role="columnheader">Variants</span>
            <span role="columnheader">Models</span>
            <span role="columnheader">Last used</span>
          </li>

          <template v-for="row in group.rows" :key="row.topology_hash">
            <li
              class="wfshelf-row"
              :class="{
                'wfshelf-row--selected':
                  store.selectedHash === row.topology_hash,
                'wfshelf-row--unused': !row.pictures,
              }"
              role="row"
              aria-level="1"
              :aria-posinset="group.rows.indexOf(row) + 1"
              :aria-setsize="group.rows.length"
              :aria-expanded="
                row.variants > 1 ? store.isOpen(row.topology_hash) : undefined
              "
              :aria-selected="store.selectedHash === row.topology_hash"
              aria-keyshortcuts="Shift+F10"
              :tabindex="row.topology_hash === rovingKey ? 0 : -1"
              :data-row-key="row.topology_hash"
              @click="store.select(row.topology_hash)"
              @contextmenu.prevent="openRowMenu(row, $event)"
              @keydown="onRowKeydown(row.topology_hash, $event)"
              @focus="rovingKey = row.topology_hash"
            >
              <span role="gridcell" class="wfshelf-row-ident">
                <!-- Only a workflow with more than one variant has anything to
                     open. A disclosure that opens onto the row's own single
                     recipe would restate the row. -->
                <button
                  v-if="row.variants > 1"
                  class="wfshelf-twisty"
                  type="button"
                  :aria-expanded="store.isOpen(row.topology_hash)"
                  :aria-label="`${row.variants} variants of this workflow`"
                  @click.stop="store.toggleOpen(row.topology_hash)"
                >
                  <v-icon size="18">{{
                    store.isOpen(row.topology_hash)
                      ? "mdi-chevron-down"
                      : "mdi-chevron-right"
                  }}</v-icon>
                </button>
                <span
                  v-else
                  class="wfshelf-twisty wfshelf-twisty--empty"
                ></span>
              </span>

              <span role="gridcell" class="wfshelf-col--name">
                <!-- Italic, because nothing has named it: naming a workflow is a
                     later step, so in this release EVERY row reads this way and
                     the line is built from what the graph itself says. -->
                <span class="wfshelf-row-name">{{
                  workflowDescriptor(row)
                }}</span>
                <span class="wfshelf-row-sub">not named</span>
              </span>

              <span role="gridcell" class="wfshelf-col--num num">
                <!-- Never a bare 0: a workflow whose every picture is in the
                     Scrapheap is exactly what the hub exists to keep, and "none
                     kept" says that where a zero reads as a defect. -->
                <span v-if="row.pictures">{{
                  groupedNumber(row.pictures)
                }}</span>
                <span v-else class="wfshelf-quiet">none kept</span>
              </span>

              <span role="gridcell" class="wfshelf-col--num num">{{
                groupedNumber(row.variants)
              }}</span>

              <!-- One call, not two: `v-if` plus an interpolation of the same
                   expression sorts the asset list twice on every render, and on
                   the widest family that list is every model its variants
                   name. -->
              <span
                role="gridcell"
                class="wfshelf-col--models"
                :class="{ 'wfshelf-quiet': !modelSummary(row.assets) }"
                >{{ modelSummary(row.assets) || "no model names" }}</span
              >

              <span
                role="gridcell"
                class="wfshelf-col--date"
                :title="dateTitle(row.last_used)"
                >{{ dateCell(row.last_used) }}</span
              >
            </li>

            <!-- The variants, drawn as rows rather than as a nested list: they
                 already ARE rows of the same shape, one column narrower because
                 a variant has no variants of its own. -->
            <template
              v-if="row.variants > 1 && store.isOpen(row.topology_hash)"
            >
              <li
                v-if="store.isVariantsLoading(row.topology_hash)"
                class="wfshelf-row wfshelf-row--variant"
                role="row"
                aria-level="2"
              >
                <!-- One spanning cell, because a grid row owes a cell per
                     column and this row has one thing to say. -->
                <span
                  role="gridcell"
                  :aria-colspan="COLUMN_COUNT"
                  class="wfshelf-quiet"
                  >Reading variants…</span
                >
              </li>
              <li
                v-for="variant in store.variants[row.topology_hash] || []"
                :key="variant.structural_hash"
                class="wfshelf-row wfshelf-row--variant"
                role="row"
                aria-level="2"
                :aria-posinset="variantIndex(row, variant) + 1"
                :aria-setsize="(store.variants[row.topology_hash] || []).length"
                aria-keyshortcuts="Shift+F10"
                :tabindex="variantKey(row, variant) === rovingKey ? 0 : -1"
                :data-row-key="variantKey(row, variant)"
                @contextmenu.prevent="openVariantMenu(row, variant, $event)"
                @keydown="onRowKeydown(variantKey(row, variant), $event)"
                @focus="rovingKey = variantKey(row, variant)"
              >
                <span role="gridcell" class="wfshelf-row-ident">
                  <v-icon size="14">mdi-subdirectory-arrow-right</v-icon>
                </span>
                <span role="gridcell" class="wfshelf-col--name">
                  <span class="wfshelf-row-name wfshelf-row-name--variant">{{
                    variantLabel(variant)
                  }}</span>
                </span>
                <span role="gridcell" class="wfshelf-col--num num">
                  <span v-if="variant.pictures">{{
                    groupedNumber(variant.pictures)
                  }}</span>
                  <span v-else class="wfshelf-quiet">none kept</span>
                </span>
                <!-- A variant has no variants of its own, and a grid row owes a
                     cell per column: an empty one is the honest way to say so. -->
                <span role="gridcell" class="wfshelf-col--num"></span>
                <span
                  role="gridcell"
                  class="wfshelf-col--models"
                  :class="{ 'wfshelf-quiet': !modelSummary(variant.assets) }"
                  >{{ modelSummary(variant.assets) || "no model names" }}</span
                >
                <span
                  role="gridcell"
                  class="wfshelf-col--date"
                  :title="dateTitle(variant.last_used)"
                  >{{ dateCell(variant.last_used) }}</span
                >
              </li>
            </template>
          </template>
        </ul>
      </div>
    </div>

    <!-- The row's verb menu, anchored to the pointer rather than to the row,
         which is what a context menu is. Every verb here READS: nothing in this
         release writes to a workflow, so there is no confirmation to own and no
         destructive item to keep away from the top. -->
    <v-menu
      v-model="menuOpen"
      :target="menuAt"
      :close-on-content-click="true"
      location="bottom end"
      origin="top start"
      :offset="2"
    >
      <div class="tbm wfshelf-menu" role="menu">
        <button
          v-if="menuRow && menuRow.variants > 1"
          class="tbm-btn"
          type="button"
          role="menuitem"
          @click="store.toggleOpen(menuRow.topology_hash)"
        >
          <v-icon size="18">mdi-file-tree</v-icon>
          <span>{{
            store.isOpen(menuRow.topology_hash)
              ? "Close its variants"
              : `Show its ${menuRow.variants} variants`
          }}</span>
        </button>
        <button
          class="tbm-btn"
          type="button"
          role="menuitem"
          :disabled="!canExport"
          :title="exportTitle"
          @click="exportGraph()"
        >
          <v-icon size="18">mdi-code-json</v-icon>
          <span>Export the graph…</span>
        </button>
        <button
          class="tbm-btn"
          type="button"
          role="menuitem"
          @click="copyHash()"
        >
          <v-icon size="18">mdi-identifier</v-icon>
          <span>Copy its identity</span>
        </button>
      </div>
    </v-menu>
  </div>
</template>

<script setup>
// The Workflows view (implementation plan §F1), built on the model shelf's
// pattern rather than as a new kind of screen: the same dense table, the same
// Group / Sort / Show trio, the same expandable row and row menu. One route
// beside /models buys the whole interaction model, which is the decision
// recorded in the design's DECISIONS.md.
//
// **The list opens at topology level.** A row is one graph; the recipes filed
// under it are the same graph bound to different models and are the row's
// expansion. On the owner's library that is ~192 rows rather than ~617, and the
// worst family holds 159 variants — which is why they are fetched when the row
// is opened and not before, and why the expansion is drawn as rows rather than
// as a nested widget with a scroll of its own.
//
// **Nothing here writes.** Naming a workflow, running one and forgetting its
// ghosts are later steps; this is the view and the inspector, and the row menu
// offers only what can be read today. F11's ghosts filter lands beside Group /
// Sort / Show, so the toolbar leaves that room rather than filling it.

import { computed, onMounted, ref, watch } from "vue";

import { getWorkflowGraph, listWorkflowVariants } from "../../api/workflows";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";
import { useWorkflowShelfStore } from "../../stores/useWorkflowShelfStore";
import { copyText } from "../../utils/clipboard";
import { formatUserDate, formatUserDay } from "../../utils/utils";
import {
  GROUP_BY_KEYS,
  SHOW_KEYS,
  SORT_KEYS,
  groupedNumber,
  modelSummary,
  workflowDescriptor,
} from "../../utils/workflowShelf";

const store = useWorkflowShelfStore();
const notices = useNoticeStore();
const userPrefs = useUserPrefsStore();

const rootEl = ref(null);
const groupMenuOpen = ref(false);
const sortMenuOpen = ref(false);
const showMenuOpen = ref(false);
const menuOpen = ref(false);
/** `[x, y]` in client coordinates — what v-menu's `target` takes. */
const menuAt = ref([0, 0]);
const menuRow = ref(null);
const menuVariant = ref(null);
const rovingKey = ref(null);

/** How many columns a row owes a cell for. */
const COLUMN_COUNT = 6;

const GROUP_LABELS = {
  none: { label: "Nothing", icon: "mdi-format-list-bulleted" },
  base_model: { label: "Base model", icon: "mdi-cube-outline" },
  size: { label: "Size", icon: "mdi-graph-outline" },
};

const SORT_LABELS = {
  used: { label: "Most used", icon: "mdi-image-multiple-outline" },
  recent: { label: "Recently used", icon: "mdi-clock-outline" },
  variants: { label: "Variants", icon: "mdi-file-tree" },
  nodes: { label: "Nodes", icon: "mdi-graph-outline" },
  added: { label: "First seen", icon: "mdi-calendar-plus" },
};

const SHOW_LABELS = {
  all: { label: "All", icon: "mdi-select-all" },
  in_use: { label: "In use", icon: "mdi-image-check-outline" },
  unused: { label: "Unused", icon: "mdi-image-off-outline" },
};

/** What each empty state says, and which of the three it is. */
const EMPTY_STATES = {
  unscanned: {
    icon: "mdi-magnify",
    title: "Nothing read yet",
    body:
      "PixlStash has not looked through your pictures for the workflows that " +
      "made them. It does that in the background, and you can carry on.",
  },
  scanning: {
    icon: "mdi-progress-clock",
    title: "Reading your pictures",
    body: "Counts climb while it reads. Nothing here is wrong, it is just not finished.",
  },
  none: {
    icon: "mdi-image-outline",
    title: "No workflows in this library",
    body:
      "Every picture has been read and none of them carries one. That is normal " +
      "for a library of photographs or imports: only pictures made by ComfyUI " +
      "bring a workflow with them.",
  },
};

const emptyState = computed(() =>
  store.state === "listed" ? null : EMPTY_STATES[store.state],
);

const subtitle = computed(() => {
  const families = store.visibleRows.length;
  const variants = store.shownVariantCount;
  return `${groupedNumber(families)} ${families === 1 ? "family" : "families"} · ${groupedNumber(variants)} ${variants === 1 ? "variant" : "variants"}`;
});

const announcement = computed(
  () =>
    `Sorted by ${SORT_LABELS[store.view.sortKey].label}, ${
      store.view.descending ? "most first" : "least first"
    }. ${groupedNumber(store.visibleRows.length)} shown.`,
);

/**
 * Which recipe "Export the graph" would write.
 *
 * A topology has no document of its own — the graph belongs to the recipe — so
 * the verb is offered on a variant, and on a workflow that has exactly one
 * variant, where there is nothing to choose between. On a workflow with several
 * it is disabled and says why, rather than silently exporting one of them.
 */
/**
 * Whether "Export the graph" has an unambiguous recipe to write.
 *
 * A topology has no document of its own — the graph belongs to the recipe — so
 * the verb needs a **structural** hash. A variant names one directly, and a
 * workflow with exactly one variant has nothing to choose between. On one with
 * several the item is disabled and says why, rather than silently exporting
 * whichever came back first.
 *
 * **This deliberately does not fall back to the topology hash.** It did, and
 * that hash addresses no recipe: `GET /workflows/recipes/{hash}/graph` 404s on
 * it, so the verb failed on the commonest kind of row in the list while
 * promising to write a file. Whether the recipe is already in hand is a
 * different question, answered in `exportGraph` by fetching it.
 */
const canExport = computed(
  () => Boolean(menuVariant.value) || menuRow.value?.variants === 1,
);

const exportTitle = computed(() =>
  canExport.value
    ? "Write the workflow's structure to a file"
    : "This workflow has several variants — open it and export the one you mean",
);

/**
 * The recipe to export, fetching the workflow's variants if it has to.
 *
 * A single-variant row never renders a disclosure, so its recipe is never in
 * the store: nothing but this asks for it, and asking on the press is one
 * request on a deliberate gesture rather than a request per row on a list of
 * 192.
 */
async function resolveExportHash() {
  if (menuVariant.value) return menuVariant.value.structural_hash;
  const row = menuRow.value;
  if (!row || row.variants !== 1) return null;
  const known = store.variants[row.topology_hash];
  if (known?.length) return known[0].structural_hash;
  const fetched = await listWorkflowVariants(row.topology_hash);
  return fetched.length ? fetched[0].structural_hash : null;
}

function workflowCount(n) {
  return `${groupedNumber(n)} ${n === 1 ? "workflow" : "workflows"}`;
}

/** A variant's line: what distinguishes it from its siblings is its models. */
function variantLabel(variant) {
  const models = modelSummary(variant?.assets, 3);
  return models || "models not named";
}

function dateCell(iso) {
  return iso ? formatUserDay(iso, userPrefs.dateFormat) : "";
}

function dateTitle(iso) {
  return iso
    ? `Last used: ${formatUserDate(iso, userPrefs.dateFormat)}`
    : undefined;
}

function openRowMenu(row, event) {
  store.select(row.topology_hash);
  rovingKey.value = row.topology_hash;
  menuRow.value = row;
  menuVariant.value = null;
  menuAt.value = [event.clientX, event.clientY];
  menuOpen.value = true;
}

function openVariantMenu(row, variant, event) {
  store.select(row.topology_hash);
  menuRow.value = row;
  menuVariant.value = variant;
  menuAt.value = [event.clientX, event.clientY];
  menuOpen.value = true;
}

/**
 * Write one recipe's stored graph to a file.
 *
 * **Named "the graph" and not "the JSON" on purpose.** What is stored is the
 * recipe's structural document, with its parameters, seeds and prompts nulled
 * and its assets named by an opaque reference — which is exactly what lets a
 * workflow outlive the pictures it made. It describes the workflow; it will not
 * open and run in ComfyUI. A verbatim export is §B5's, and is not shipped, so
 * the notice says so rather than letting somebody find out by trying it.
 */
async function exportGraph() {
  try {
    const hash = await resolveExportHash();
    if (!hash) {
      notices.push({
        level: "error",
        text: "This workflow has no stored graph to write.",
      });
      return;
    }
    const body = await getWorkflowGraph(hash);
    const blob = new Blob([JSON.stringify(body.document, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `workflow-${hash.slice(0, 12)}.json`;
    document.body.appendChild(anchor);
    anchor.click();
    setTimeout(() => {
      document.body.removeChild(anchor);
      URL.revokeObjectURL(url);
    }, 100);
    notices.push({
      level: "info",
      text: "Saved the workflow's structure. It describes the graph and does not run in ComfyUI.",
    });
  } catch (err) {
    console.warn("[workflows] could not export the graph", err);
    notices.push({
      level: "error",
      text: "Could not read that workflow's graph.",
    });
  }
}

async function copyHash() {
  const hash =
    menuVariant.value?.structural_hash || menuRow.value?.topology_hash;
  if (!hash) return;
  const ok = await copyText(hash);
  notices.push({
    level: ok ? "success" : "error",
    text: ok ? "Copied the workflow's identity." : "Could not copy it.",
  });
}

/** The context-menu key, which a row owes as much as it owes the right button. */
function isMenuKey(event) {
  return event.key === "ContextMenu" || (event.key === "F10" && event.shiftKey);
}

/**
 * The key a variant is drawn, focused and identified by.
 *
 * Carries the workflow as well as the recipe: the same recipe cannot appear
 * under two topologies, but the key is also the DOM's `data-row-key`, and one
 * that did not name the parent would be a tab stop with no way back up.
 */
function variantKey(row, variant) {
  return `${row.topology_hash}:${variant.structural_hash}`;
}

/** Where a variant sits among its siblings, for `aria-posinset`. */
function variantIndex(row, variant) {
  return (store.variants[row.topology_hash] || []).indexOf(variant);
}

/**
 * Every row the reader can walk, in the order they are drawn.
 *
 * A variant is a CHILD row of the workflow above it — that is the "tree" half of
 * `treegrid` — so Up and Down have to walk THROUGH an open workflow rather than
 * over it. Flattening here rather than branching in the key handler is what
 * makes "the row below" mean the same thing to the keyboard as it does on
 * screen.
 */
const walkableRows = computed(() => {
  const flat = [];
  for (const row of store.visibleRows) {
    flat.push({ key: row.topology_hash, row, variant: null });
    if (row.variants > 1 && store.isOpen(row.topology_hash)) {
      for (const variant of store.variants[row.topology_hash] || []) {
        flat.push({ key: variantKey(row, variant), row, variant });
      }
    }
  }
  return flat;
});

function focusRow(key) {
  rovingKey.value = key;
  // A QUOTED attribute selector, and no `CSS.escape`: a variant's key holds a
  // colon, which is a combinator unquoted — and `CSS.escape` is absent in the
  // test environment, so reaching for it puts a TypeError on the one path a
  // keyboard user takes. Every key here is hex and colons, so quoting is enough
  // and there is nothing left to escape.
  const el = rootEl.value?.querySelector(`[data-row-key="${key}"]`);
  el?.focus();
}

function onRowKeydown(key, event) {
  const rows = walkableRows.value;
  const index = rows.findIndex((entry) => entry.key === key);
  if (index === -1) return;
  const { row, variant } = rows[index];

  if (event.key === "ArrowDown" && index < rows.length - 1) {
    event.preventDefault();
    focusRow(rows[index + 1].key);
  } else if (event.key === "ArrowUp" && index > 0) {
    event.preventDefault();
    focusRow(rows[index - 1].key);
  } else if (event.key === "ArrowRight" && !variant) {
    if (row.variants > 1 && !store.isOpen(row.topology_hash)) {
      event.preventDefault();
      store.toggleOpen(row.topology_hash);
    }
  } else if (event.key === "ArrowLeft") {
    // From inside an open workflow, Left closes it and lands on the row that
    // was holding the variants — the treegrid rule, and the only way back out
    // of an expansion 159 rows deep without walking every one of them.
    if (variant) {
      event.preventDefault();
      store.toggleOpen(row.topology_hash);
      focusRow(row.topology_hash);
    } else if (store.isOpen(row.topology_hash)) {
      event.preventDefault();
      store.toggleOpen(row.topology_hash);
    }
  } else if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    store.select(row.topology_hash);
  } else if (event.key === "Escape") {
    store.clearSelection();
  } else if (isMenuKey(event)) {
    event.preventDefault();
    const box = event.target?.getBoundingClientRect?.();
    menuRow.value = row;
    menuVariant.value = variant;
    menuAt.value = box ? [box.left + 24, box.bottom] : [0, 0];
    menuOpen.value = true;
  }
}

// The roving tab stop has to exist before the list is walked, and it has to
// survive a resort: the row it named may no longer be shown.
watch(
  walkableRows,
  (rows) => {
    if (!rows.length) {
      rovingKey.value = null;
      return;
    }
    if (!rows.some((entry) => entry.key === rovingKey.value)) {
      rovingKey.value = rows[0].key;
    }
  },
  { immediate: true },
);

onMounted(() => store.fetchRows());
</script>

<style scoped>
.wfshelf {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  position: relative;
  background: rgb(var(--v-theme-background));
  color: rgb(var(--v-theme-on-background));
  outline: none;
  /* Picking rows is the gesture on this panel, and the browser's text selection
     rides along with it: a fast double click word-selects the row under it. */
  -webkit-user-select: none;
  user-select: none;
}

.wfshelf-toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  /* The shelf bar's own box recipe: a fixed 36 with the hairline INSIDE it and
     no vertical padding, so this bar and the model shelf's sit at the same
     height beside each other. `--bar-height` is 48 and would not. */
  box-sizing: border-box;
  height: 36px;
  padding: 0 var(--space-3) 0 var(--space-5);
  flex: none;
  background: rgb(var(--v-theme-toolbar));
  border-bottom: 1px solid rgb(var(--v-theme-divider));
}

.wfshelf-title {
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
}

.wfshelf-sub {
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wfshelf-spacer {
  flex: 1;
}

/* The column-name strip, standing above the list rather than inside it: a
   grouped list is one grid per band, and a visible heading row per band is
   exactly what this strip exists to avoid. */
.wfshelf-head {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  flex: none;
  box-sizing: border-box;
  height: var(--rule-h-seam);
  /* The 3px matches the row's always-present transparent left border, so the
     headings sit over the cells they name rather than 3px to their left. */
  padding: 0 var(--space-4) 0 calc(var(--space-6) + 3px);
  border-bottom: 1px solid rgb(var(--v-theme-divider));
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wfshelf-head-ident {
  width: var(--space-7);
  flex: none;
}

.wfshelf-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.wfshelf-list {
  list-style: none;
  padding: 0;
  margin: 0;
}

.wfshelf-group {
  margin: 0;
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
}

.wfshelf-group-btn {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  width: 100%;
  padding: var(--space-3) var(--space-4);
  background: rgb(var(--v-theme-toolbar));
  border: 0;
  border-bottom: 1px solid rgb(var(--v-theme-divider));
  color: inherit;
  font: inherit;
  letter-spacing: inherit;
  text-transform: inherit;
  cursor: pointer;
}

.wfshelf-group-count {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  text-transform: none;
  letter-spacing: normal;
}

.wfshelf-row {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-3) var(--space-4) var(--space-3) var(--space-6);
  /* Always present, always transparent: only its colour changes, so a row that
     is selected does not move a pixel. */
  border-left: 3px solid transparent;
  border-bottom: 1px solid rgb(var(--v-theme-divider));
  cursor: pointer;
  font-size: var(--text-sm);
  transition: background var(--dur-1) var(--ease-standard);
  /* Native windowing: the browser skips layout and paint for rows outside the
     viewport, which is what a 159-variant family needs and is two lines rather
     than a virtual scroller. */
  content-visibility: auto;
  contain-intrinsic-size: auto var(--space-9);
}

.wfshelf-row:hover {
  background: var(--hover-wash);
}

.wfshelf-row:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: -2px;
}

.wfshelf-row--selected {
  background: var(--active-wash);
  border-left-color: rgb(var(--v-theme-accent));
}

.wfshelf-row--variant {
  padding-left: var(--space-8);
  cursor: default;
  background: rgba(var(--v-theme-on-surface), 0.02);
}

.wfshelf-row-ident {
  width: var(--space-7);
  flex: none;
  display: inline-flex;
  justify-content: center;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wfshelf-twisty {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--space-6);
  height: var(--space-6);
  padding: 0;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: inherit;
  cursor: pointer;
}

.wfshelf-twisty--empty {
  cursor: default;
}

/* FIXED column widths, not `auto`: grouping makes one grid per band, so `auto`
   tracks would be measured against that band's rows alone and the columns would
   step sideways from one band to the next. */
.wfshelf-col--name {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.wfshelf-col--num {
  /* Wide enough for the widest thing either numeric column holds, which is not
     a number: "none kept" is what a workflow that outlived its pictures reads,
     and it must not wrap or be clipped. */
  width: 5.5rem;
  flex: none;
  text-align: right;
}

.wfshelf-col--models {
  width: 14rem;
  flex: none;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wfshelf-col--date {
  width: 7rem;
  flex: none;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wfshelf-row-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  /* Every workflow in this release is unnamed, and the line is a description of
     the graph rather than a title somebody chose. Italic is what says so. */
  font-style: italic;
}

.wfshelf-row-name--variant {
  font-style: normal;
}

.wfshelf-row-sub {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wfshelf-quiet {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  font-size: var(--text-xs);
}

.wfshelf-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-9) var(--space-6);
  text-align: center;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wfshelf-empty-icon {
  opacity: var(--opacity-text-secondary);
}

.wfshelf-empty-title {
  margin: 0;
  font-size: var(--text-md);
  font-weight: var(--weight-medium);
  color: rgb(var(--v-theme-on-background));
}

.wfshelf-empty-body {
  margin: 0;
  max-width: 46ch;
  font-size: var(--text-sm);
  line-height: var(--leading-body);
}

.wfshelf-note {
  margin: var(--space-4);
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  font-size: var(--text-sm);
}

.wfshelf-note--error {
  border-left: 3px solid rgb(var(--v-theme-error));
  background: rgba(var(--v-theme-error), 0.08);
}

.wfshelf-menu {
  display: flex;
  flex-direction: column;
  padding: var(--space-2);
  gap: var(--space-1);
}
</style>
