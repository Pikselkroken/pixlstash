<script setup>
/**
 * The Workflows workspace's own left rail (#2165): the ComfyUI connection in
 * the header slot (#2166), the three places the grid can be narrowed to, the
 * ComfyUI templates, the Type and Base model facets, and the footer's Add
 * workflow. App.vue mounts it in place of SideBar on
 * /workflows; SideBar stays mounted, hidden, because it owns the refs and
 * drop targets the rest of the app reaches through.
 *
 * Places and facets are state in `useWorkflowsStore`; the Templates place is
 * `useWorkflowTemplatesStore().shown`, which the grid's view reads too. Picking
 * a place or a facet leaves the Templates place.
 *
 * A click picks one row, as the Media rail's does: a facet alone, or a place
 * alone. Ctrl/Cmd-click keeps the rest lit: a facet row toggles in beside the
 * place and the other facet, a place row swaps only the place. Each section
 * still holds one value, so a Ctrl-click within one swaps it.
 */
import { computed, onMounted, ref, watch } from "vue";
import AppButton from "../widgets/AppButton.vue";
import ModelMark from "../widgets/ModelMark.vue";
import SidebarResizeHandle from "./SidebarResizeHandle.vue";
import Tooltip from "../widgets/Tooltip.vue";
import WorkflowsComfyuiSlot from "./WorkflowsComfyuiSlot.vue";
import { useSidebarWidth } from "../../composables/useSidebarWidth";
import { useFilterStore } from "../../stores/useFilterStore";
import { useWorkflowsStore } from "../../stores/useWorkflowsStore";
import { useWorkflowTemplatesStore } from "../../stores/useWorkflowTemplatesStore";
import { isReadOnly } from "../../utils/apiClient";

const emit = defineEmits(["open-settings"]);

const store = useWorkflowsStore();
const tpl = useWorkflowTemplatesStore();
const filterStore = useFilterStore();
// The width every workspace's sidebar shares, so a switch does not move the edge.
const { width, narrow } = useSidebarWidth();

// The list is read once per session and the server keeps its own copy of
// ComfyUI's, so this is one cheap read, not a ComfyUI round trip per visit. It
// is needed here, not only on the Templates place: the "N new" pill compares
// the list with what was seen, which cannot be known without reading it.
onMounted(() => {
  tpl.fetchTemplates();
});

// A new address is another ComfyUI: its list, not the one read before. Not
// the config's first read, which only says which address the mount read.
watch(
  () => [filterStore.comfyuiUrl, filterStore.comfyuiConfigRead],
  ([url], [was, wasRead]) => {
    if (wasRead && url && url !== was) tpl.fetchTemplates({ refresh: true });
  },
);

const PLACES = [
  { id: "all", label: "All workflows", icon: "mdi-view-grid-outline" },
  { id: "favourites", label: "Favourites", icon: "mdi-heart-outline" },
  { id: "recent", label: "Recently used", icon: "mdi-history" },
];

const facetLit = computed(
  () => store.filters.type != null || store.filters.checkpoint != null,
);

const placeRows = computed(() => {
  const counts = {
    all: store.cards.length,
    favourites: store.favouriteCount,
    recent: store.recentCount,
  };
  return PLACES.map((place) => ({
    ...place,
    count: counts[place.id],
    // All workflows is the absence of a filter, so a lit facet puts it out.
    lit:
      !tpl.shown &&
      store.place === place.id &&
      !(place.id === "all" && facetLit.value),
  }));
});

// "–" with no ComfyUI set up, or when it did not answer, so an unconnected
// owner reads a dash rather than a zero; blank until the list is read.
const templatesCount = computed(() => {
  if (!filterStore.comfyuiConfigRead || !tpl.loaded) return "";
  if (!filterStore.comfyuiConfigured) return "–";
  return tpl.reachable ? String(tpl.templates.length) : "–";
});

const showNew = computed(
  () =>
    filterStore.comfyuiConfigured &&
    tpl.loaded &&
    tpl.reachable &&
    tpl.newCount > 0,
);

// One glyph per type the server serves (`_TYPE_LABELS` in
// routes/workflows.py). A type this build does not know takes the generic
// graph rather than an empty slot.
const TYPE_ICONS = {
  txt2img: "mdi-text-box-outline",
  img2img: "mdi-image-sync-outline",
  inpaint: "mdi-brush-outline",
  outpaint: "mdi-arrow-expand-all",
  upscale: "mdi-magnify-plus-outline",
  video: "mdi-video-outline",
};

const typeRows = computed(() =>
  store.filterOptions.types.map((option) => ({
    ...option,
    icon: TYPE_ICONS[option.id] ?? "mdi-sitemap-outline",
    lit: !tpl.shown && store.filters.type === option.id,
  })),
);

// The Base model list reads A-Z by default; the head's toggle switches it to
// most-used first (the store's own order). A per-browser view preference, so
// it stays in localStorage; a refused read or write only loses the choice.
const BASE_SORT_KEY = "pixlstash:workflowsBaseModelSort";

function readBaseSort() {
  try {
    return localStorage.getItem(BASE_SORT_KEY) === "count" ? "count" : "name";
  } catch (e) {
    console.warn("Base model sort could not be read; sorting by name.", e);
    return "name";
  }
}

const baseSort = ref(readBaseSort());

const baseSortLabel = computed(() =>
  baseSort.value === "name"
    ? "Sorted by name. Sort by count"
    : "Sorted by count. Sort by name",
);

function toggleBaseSort() {
  baseSort.value = baseSort.value === "name" ? "count" : "name";
  try {
    localStorage.setItem(BASE_SORT_KEY, baseSort.value);
  } catch (e) {
    console.warn("Base model sort could not be saved; it resets on reload.", e);
  }
}

const byLabel = (a, b) =>
  a.label.localeCompare(b.label, undefined, {
    numeric: true,
    sensitivity: "base",
  }) || a.id.localeCompare(b.id);

const baseRows = computed(() => {
  const options = [...store.filterOptions.checkpoints];
  if (baseSort.value === "name") options.sort(byLabel);
  return options.map((option) => ({
    ...option,
    lit: !tpl.shown && store.filters.checkpoint === option.id,
  }));
});

const isAdd = (event) => Boolean(event?.ctrlKey || event?.metaKey);

/** A place clears the facets; Ctrl/Cmd keeps them and swaps only the place. */
function pickPlace(id, event) {
  tpl.shown = false;
  if (!isAdd(event)) {
    store.setPlace(id);
    return;
  }
  store.setFilters({
    favouritesOnly: id === "favourites",
    recent: id === "recent",
  });
}

function pickTemplates() {
  tpl.shown = true;
}

/**
 * A click lights this facet alone: the place goes back to All and the other
 * facet clears. Clicking the one lit row turns it off. Ctrl/Cmd toggles it
 * and leaves the place and the other facet lit. Either way the grid comes
 * back from Templates.
 */
function pickFacet(key, id, event) {
  // Read as drawn: under Templates a stored facet shows unlit, so a click on
  // it lights it rather than clearing it.
  const lit = !tpl.shown && store.filters[key] === id;
  tpl.shown = false;
  if (isAdd(event)) {
    store.setFilters({ [key]: lit ? null : id });
    return;
  }
  const other = key === "type" ? "checkpoint" : "type";
  const alone = lit && store.place === "all" && store.filters[other] == null;
  store.setFilters({
    favouritesOnly: false,
    recent: false,
    type: null,
    checkpoint: null,
    [key]: alone ? null : id,
  });
}
</script>

<template>
  <div
    class="workflows-sidebar sidebar-panel"
    :class="{ 'sidebar--narrow': narrow }"
    :style="{ width: `${width}px` }"
  >
    <SidebarResizeHandle />
    <!-- A shared view cannot read the owner's ComfyUI address, so it would
         always read as not set up. -->
    <WorkflowsComfyuiSlot
      v-if="!isReadOnly"
      @open-settings="(section) => emit('open-settings', section)"
    />
    <nav
      class="workflows-sidebar__list sidebar-nav-list sidebar-scroll"
      aria-label="Workflow navigation"
    >
      <button
        v-for="place in placeRows"
        :key="place.id"
        type="button"
        class="sidebar-list-item"
        :class="{ active: place.lit }"
        :aria-current="place.lit ? 'page' : undefined"
        @click="pickPlace(place.id, $event)"
      >
        <span class="sidebar-list-icon" aria-hidden="true"
          ><v-icon size="16">{{ place.icon }}</v-icon></span
        >
        <span class="sidebar-list-label sidebar-list-label-text">{{ place.label }}</span>
        <!-- Blank until the grid is read: a 0 there reads as an empty
             library while the first read is still out, or after it failed. -->
        <span v-if="store.loaded" class="sidebar-list-count">{{
          place.count
        }}</span>
      </button>

      <div class="sidebar-section-header" role="presentation">
        <span class="sidebar-section-title">From ComfyUI</span>
      </div>
      <button
        type="button"
        class="sidebar-list-item"
        :class="{ active: tpl.shown }"
        :aria-current="tpl.shown ? 'page' : undefined"
        @click="pickTemplates"
      >
        <span class="sidebar-list-icon" aria-hidden="true"
          ><v-icon size="16">mdi-shape-outline</v-icon></span
        >
        <span class="sidebar-list-label sidebar-list-label-text">Templates</span>
        <span v-if="showNew" class="wfs-new"
          >{{ tpl.newCount }} new</span
        >
        <span class="sidebar-list-count">{{ templatesCount }}</span>
      </button>

      <div class="sidebar-section-header" role="presentation">
        <span class="sidebar-section-title">Type</span>
      </div>
      <button
        v-for="option in typeRows"
        :key="option.id"
        type="button"
        class="sidebar-list-item"
        :class="{ active: option.lit }"
        :aria-current="option.lit ? 'page' : undefined"
        @click="pickFacet('type', option.id, $event)"
      >
        <span class="sidebar-list-icon" aria-hidden="true"
          ><v-icon size="16">{{ option.icon }}</v-icon></span
        >
        <span class="sidebar-list-label sidebar-list-label-text">{{ option.label }}</span>
        <span class="sidebar-list-count">{{ option.count }}</span>
      </button>

      <div class="sidebar-section-header">
        <span class="sidebar-section-title">Base model</span>
        <span v-if="baseRows.length > 1" class="sidebar-header-actions">
          <button
            type="button"
            class="sidebar-section-add wfs-sort"
            :aria-label="baseSortLabel"
            @click="toggleBaseSort"
          >
            <Tooltip :text="baseSortLabel" activator="parent" :describe="false" />
            <v-icon size="16" aria-hidden="true">{{
              baseSort === "name"
                ? "mdi-sort-alphabetical-variant"
                : "mdi-sort-numeric-descending"
            }}</v-icon>
          </button>
        </span>
      </div>
      <button
        v-for="option in baseRows"
        :key="option.id"
        type="button"
        class="sidebar-list-item"
        :class="{ active: option.lit }"
        :aria-current="option.lit ? 'page' : undefined"
        @click="pickFacet('checkpoint', option.id, $event)"
      >
        <!-- The card strip's tile for this model, so a row and the cards it
             narrows to wear the same mark. Hidden: the label names it. -->
        <span class="sidebar-list-icon wfs-mark" aria-hidden="true">
          <ModelMark :row="option.mark" named />
        </span>
        <span class="sidebar-list-label sidebar-list-label-text">{{ option.label }}</span>
        <span class="sidebar-list-count">{{ option.count }}</span>
      </button>
    </nav>

    <div class="sidebar-footer-band">
      <AppButton
        class="wfs-add"
        variant="primary"
        icon-left="plus"
        block
        @click="store.requestAdd()"
        >Add workflow</AppButton
      >
    </div>
  </div>
</template>

<style scoped src="./SidebarNav.css"></style>
<style scoped>
/* The list takes the room and scrolls; the footer below it does not. No top
   padding: the first row meets the slot's rule, as the Media list's does. */
.workflows-sidebar__list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0 0 var(--space-2);
}

/* The model tile, drawn at the icon slot's size rather than its own 24px. */
.wfs-mark {
  --entity-thumb: var(--sidebar-thumb-size);
}

/* The row prints the model's name, version included; at the rail's size the
   tile's version and role pills would cover most of the glyph they label. */
.wfs-mark :deep(.mmark-version) {
  display: none;
}

/* A count of templates, not pictures: the outline pill, the one count that
   is not filled (visual-language.md section 12). */
.wfs-new {
  flex: none;
  display: inline-flex;
  align-items: center;
  height: var(--badge-size);
  padding: 0 var(--space-2);
  border: 1px solid currentColor;
  border-radius: var(--radius-pill);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

/* The head's sort toggle, the section "+"'s shape, 10% under the rows' ink:
   at full strength the glyph outshone the labels beside it (owner's call). */
.wfs-sort {
  color: rgba(var(--v-theme-sidebar-text), 0.9);
}

.wfs-add {
  height: var(--control-h-bar);
}
</style>
