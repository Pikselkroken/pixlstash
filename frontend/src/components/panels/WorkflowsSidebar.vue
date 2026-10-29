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
 */
import { computed, onMounted, ref, watch } from "vue";
import AppButton from "../widgets/AppButton.vue";
import ModelMark from "../widgets/ModelMark.vue";
import Tooltip from "../widgets/Tooltip.vue";
import WorkflowsComfyuiSlot from "./WorkflowsComfyuiSlot.vue";
import { useFilterStore } from "../../stores/useFilterStore";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";
import { useWorkflowsStore } from "../../stores/useWorkflowsStore";
import { useWorkflowTemplatesStore } from "../../stores/useWorkflowTemplatesStore";
import { isReadOnly } from "../../utils/apiClient";

const emit = defineEmits(["open-settings"]);

const store = useWorkflowsStore();
const tpl = useWorkflowTemplatesStore();
const filterStore = useFilterStore();
const userPrefsStore = useUserPrefsStore();

// The library sidebar's width, so switching workspace does not move the edge.
const width = computed(() => userPrefsStore.sidebarWidth ?? 240);

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

const placeRows = computed(() => {
  const counts = {
    all: store.cards.length,
    favourites: store.favouriteCount,
    recent: store.recentCount,
  };
  return PLACES.map((place) => ({
    ...place,
    count: counts[place.id],
    lit: !tpl.shown && store.place === place.id,
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

function pickPlace(id) {
  tpl.shown = false;
  store.setPlace(id);
}

function pickTemplates() {
  tpl.shown = true;
}

/** A facet toggles; picking one while Templates is on goes back to the grid. */
function pickFacet(key, id) {
  tpl.shown = false;
  store.setFilters({ [key]: store.filters[key] === id ? null : id });
}
</script>

<template>
  <div class="workflows-sidebar" :style="{ width: `${width}px` }">
    <!-- A shared view cannot read the owner's ComfyUI address, so it would
         always read as not set up. -->
    <WorkflowsComfyuiSlot
      v-if="!isReadOnly"
      @open-settings="(section) => emit('open-settings', section)"
    />
    <nav
      class="workflows-sidebar__list sidebar-scroll"
      aria-label="Workflow navigation"
    >
      <button
        v-for="place in placeRows"
        :key="place.id"
        type="button"
        class="wfs-row"
        :class="{ 'wfs-row--lit': place.lit }"
        :aria-current="place.lit ? 'page' : undefined"
        @click="pickPlace(place.id)"
      >
        <v-icon class="wfs-row__glyph" aria-hidden="true">{{
          place.icon
        }}</v-icon>
        <span class="wfs-row__label">{{ place.label }}</span>
        <!-- Blank until the grid is read: a 0 there reads as an empty
             library while the first read is still out, or after it failed. -->
        <span v-if="store.loaded" class="wfs-row__count">{{
          place.count
        }}</span>
      </button>

      <div class="section-label wfs-label">From ComfyUI</div>
      <button
        type="button"
        class="wfs-row"
        :class="{ 'wfs-row--lit': tpl.shown }"
        :aria-current="tpl.shown ? 'page' : undefined"
        @click="pickTemplates"
      >
        <v-icon class="wfs-row__glyph" aria-hidden="true"
          >mdi-shape-outline</v-icon
        >
        <span class="wfs-row__label">Templates</span>
        <span v-if="showNew" class="wfs-new"
          >{{ tpl.newCount }} new</span
        >
        <span class="wfs-row__count">{{ templatesCount }}</span>
      </button>

      <div class="section-label wfs-label">Type</div>
      <button
        v-for="option in typeRows"
        :key="option.id"
        type="button"
        class="wfs-row"
        :class="{ 'wfs-row--lit': option.lit }"
        :aria-current="option.lit ? 'page' : undefined"
        @click="pickFacet('type', option.id)"
      >
        <v-icon class="wfs-row__glyph" aria-hidden="true">{{
          option.icon
        }}</v-icon>
        <span class="wfs-row__label">{{ option.label }}</span>
        <span class="wfs-row__count">{{ option.count }}</span>
      </button>

      <div class="section-label wfs-label wfs-label--action">
        <span>Base model</span>
        <button
          v-if="baseRows.length > 1"
          type="button"
          class="wfs-sort"
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
      </div>
      <button
        v-for="option in baseRows"
        :key="option.id"
        type="button"
        class="wfs-row"
        :class="{ 'wfs-row--lit': option.lit }"
        :aria-current="option.lit ? 'page' : undefined"
        @click="pickFacet('checkpoint', option.id)"
      >
        <!-- The card strip's tile for this model, so a row and the cards it
             narrows to wear the same mark. Hidden: the label names it. -->
        <span class="wfs-row__glyph" aria-hidden="true">
          <ModelMark :row="option.mark" named />
        </span>
        <span class="wfs-row__label">{{ option.label }}</span>
        <span class="wfs-row__count">{{ option.count }}</span>
      </button>
    </nav>

    <div class="wfs-footer">
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

<style scoped>
/* The shell is SideBar's: its width and right rule, the sidebar ground, its
   own column. */
.workflows-sidebar {
  box-sizing: border-box;
  height: 100%;
  min-height: 0;
  max-height: 100%;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  background: rgb(var(--v-theme-sidebar));
  color: rgb(var(--v-theme-sidebar-text));
  border-right: 1px solid rgb(var(--v-theme-border));
}

/* The list takes the room and scrolls; the footer below it does not. No top
   padding: the first row meets the slot's rule, as the Media list's does. */
.workflows-sidebar__list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0 0 var(--space-2);
}

/* A 32px row, as the sidebar's own rows are. The 3px rail is always present
   and transparent on the base, so selecting a row does not shift its label. */
.wfs-row {
  box-sizing: border-box;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
  height: var(--control-h-bar);
  margin: 0;
  padding: 0 var(--space-3);
  border: 0;
  border-left: var(--rail-w) solid transparent;
  border-radius: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  font-size: var(--text-base);
  text-align: left;
  cursor: pointer;
  transition: background var(--dur-1) var(--ease-standard);
}

.wfs-row:hover {
  background: var(--hover-wash);
}

/* Inset, as the Media rows' is: the list clips an outer ring at its edges. */
.wfs-row:focus-visible {
  box-shadow: var(--focus-ring-inset);
  outline: none;
}

/* Selected is the olive wash and rail; the words keep the surface's ink. */
.wfs-row--lit,
.wfs-row--lit:hover {
  background: var(--active-wash);
  border-left-color: var(--active-bar);
  color: var(--active-text);
}

.wfs-row--lit:hover {
  background:
    linear-gradient(var(--hover-wash), var(--hover-wash)), var(--active-wash);
}

/* The entity slot on every row, not only on the Base model rows that fill
   it, so every label in the rail starts at one x. A glyph centres in it at
   the glyph size, set here rather than by `v-icon`'s `size`, which writes the
   width inline and would shrink the slot back to the glyph. */
.wfs-row__glyph {
  flex: none;
  width: var(--entity-thumb);
  height: var(--entity-thumb);
  font-size: var(--gutter-glyph);
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

/* The row prints the model's name, version included; at the rail's 24px the
   tile's version and role pills would cover most of the glyph they label. */
.wfs-row__glyph :deep(.mmark-version) {
  display: none;
}

.wfs-row__label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wfs-row__count {
  flex: none;
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
  opacity: var(--opacity-text-secondary);
}

.wfs-row--lit .wfs-row__count {
  opacity: 1;
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

/* The label sits past the row's rail, so it lines up with the row text. */
.wfs-label {
  padding: var(--space-5) var(--space-3) var(--space-2)
    calc(var(--space-3) + var(--rail-w));
}

/* A label with a control at its right edge. The button's negative margins
   take it out of the line's height, so this head stands exactly as tall as
   the plain ones and the rows below it do not move. */
.wfs-label--action {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.wfs-sort {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--control-h-sm);
  height: var(--control-h-sm);
  margin: calc(var(--control-h-sm) / -2) 0;
  padding: 0;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  /* 10% under the rows' ink: at full strength the glyph outshone the labels
     beside it (owner's call). */
  color: rgba(var(--v-theme-sidebar-text), 0.9);
  cursor: pointer;
}

.wfs-sort:hover {
  background: var(--hover-wash);
}

/* The band the Add workflow button sits in, with the rail's own top edge:
   the shell's sidebar band, the Media sidebar's Import band's height. */
.wfs-footer {
  flex: none;
  box-sizing: border-box;
  height: var(--sidebar-band-h);
  display: flex;
  align-items: center;
  padding: 0 var(--space-3);
  border-top: 1px solid rgb(var(--v-theme-border));
}

.wfs-add {
  height: var(--control-h-bar);
}
</style>
