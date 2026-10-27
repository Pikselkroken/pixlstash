<script setup>
/**
 * The Workflows workspace's own left rail (#2165): the three places the grid
 * can be narrowed to, the ComfyUI templates, the Type and Base model facets,
 * and the footer's Add workflow. App.vue mounts it in place of SideBar on
 * /workflows; SideBar stays mounted, hidden, because it owns the refs and
 * drop targets the rest of the app reaches through.
 *
 * Places and facets are state in `useWorkflowsStore`; the Templates place is
 * `useWorkflowTemplatesStore().shown`, which the grid's view reads too. Picking
 * a place or a facet leaves the Templates place.
 */
import { computed, onMounted } from "vue";
import AppButton from "../widgets/AppButton.vue";
import { useWorkflowsStore } from "../../stores/useWorkflowsStore";
import { useWorkflowTemplatesStore } from "../../stores/useWorkflowTemplatesStore";

const store = useWorkflowsStore();
const tpl = useWorkflowTemplatesStore();

// The list is read once per session and the server keeps its own copy of
// ComfyUI's, so this is one cheap read, not a ComfyUI round trip per visit. It
// is needed here, not only on the Templates place: the "N new" pill compares
// the list with what was seen, which cannot be known without reading it.
onMounted(() => {
  tpl.fetchTemplates();
});

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

// Blank until the list is read; "–" when ComfyUI did not answer, so an
// unconnected owner reads a dash rather than a zero.
const templatesCount = computed(() => {
  if (!tpl.loaded) return "";
  return tpl.reachable ? String(tpl.templates.length) : "–";
});

const typeRows = computed(() =>
  store.filterOptions.types.map((option) => ({
    ...option,
    lit: !tpl.shown && store.filters.type === option.id,
  })),
);

const baseRows = computed(() =>
  store.filterOptions.checkpoints.map((option) => ({
    ...option,
    lit: !tpl.shown && store.filters.checkpoint === option.id,
  })),
);

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
  <div class="workflows-sidebar">
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
        <v-icon size="16" class="wfs-row__glyph" aria-hidden="true">{{
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
        <v-icon size="16" class="wfs-row__glyph" aria-hidden="true"
          >mdi-shape-outline</v-icon
        >
        <span class="wfs-row__label">Templates</span>
        <span v-if="tpl.loaded && tpl.reachable && tpl.newCount > 0" class="wfs-new"
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
        <span class="wfs-row__glyph" aria-hidden="true"></span>
        <span class="wfs-row__label">{{ option.label }}</span>
        <span class="wfs-row__count">{{ option.count }}</span>
      </button>

      <div class="section-label wfs-label">Base model</div>
      <button
        v-for="option in baseRows"
        :key="option.id"
        type="button"
        class="wfs-row"
        :class="{ 'wfs-row--lit': option.lit }"
        :aria-current="option.lit ? 'page' : undefined"
        @click="pickFacet('checkpoint', option.id)"
      >
        <v-icon size="16" class="wfs-row__glyph" aria-hidden="true"
          >mdi-layers-triple-outline</v-icon
        >
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
/* The shell is SideBar's: 240px, the sidebar ground, its own column. */
.workflows-sidebar {
  box-sizing: border-box;
  width: 240px;
  height: 100%;
  min-height: 0;
  max-height: 100%;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  background: rgb(var(--v-theme-sidebar));
  color: rgb(var(--v-theme-sidebar-text));
}

/* The list takes the room and scrolls; the footer below it does not. */
.workflows-sidebar__list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: var(--space-2) 0;
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

.wfs-row__glyph {
  flex: none;
  width: 16px;
  height: 16px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
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

/* The band the Add workflow button sits in, with the rail's own top edge.
   52px is the workspace shell's sidebar band (#2170's shared rules); the
   Media sidebar's PR names it `--sidebar-band-h`, and this takes the token
   when that lands. */
.wfs-footer {
  flex: none;
  box-sizing: border-box;
  height: 52px;
  display: flex;
  align-items: center;
  padding: 0 var(--space-3);
  border-top: 1px solid rgb(var(--v-theme-border));
}

.wfs-add {
  height: var(--control-h-bar);
}
</style>
