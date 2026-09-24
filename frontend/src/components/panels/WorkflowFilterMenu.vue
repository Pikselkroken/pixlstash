<template>
  <div class="fm-cascade">
    <div
      ref="rootRef"
      class="tbm fm-root"
      role="group"
      aria-label="Workflow filters"
    >
      <span class="tbm-caret tbm-caret--start"></span>
      <div class="tbm-header">
        <v-icon size="18" class="tbm-header-icon">mdi-filter</v-icon>
        <span class="tbm-title">Filters</span>
        <span class="tbm-spacer"></span>
        <span class="fm-n">{{ store.filterOfLabel }}</span>
        <button
          class="tbm-ghost"
          type="button"
          :disabled="!store.filterChips.length"
          @click="store.clearFilters()"
        >
          Clear all
        </button>
      </div>

      <!-- Switches, not lists, so they stay on the root where one click flips
           them. The first two are the server's flags, so ticking one re-reads
           the grid; the third only ever removes a card and is applied here.
           No section label, as the grid's Problems section has none: "Show"
           over "Hide one-offs" read as its own opposite. -->
      <div class="tbm-section">
        <label v-for="row in CHECKS" :key="row.key" class="tbm-check fm-check">
          <input
            type="checkbox"
            :checked="store.filters[row.key]"
            :data-testid="`wff-${row.key}`"
            @change="store.setFilters({ [row.key]: $event.target.checked })"
          />
          <span class="fm-check-label">{{ row.label }}</span>
          <span class="fm-n">{{ counts[row.key] }}</span>
        </label>
      </div>

      <div class="tbm-section">
        <span class="tbm-label">Workflow</span>
        <button
          v-for="k in KINDS"
          :key="k.key"
          :ref="(el) => (rowRefs[k.key] = el)"
          class="fm-row"
          type="button"
          :data-testid="`wff-row-${k.key}`"
          :aria-expanded="sub === k.key ? 'true' : 'false'"
          :aria-controls="sub === k.key ? submenuId : undefined"
          :title="rowValue(k.key) || undefined"
          @click="toggle(k.key)"
          @keydown.right.prevent="openKind(k.key)"
        >
          <v-icon size="16" class="fm-row-lead">{{ k.icon }}</v-icon>
          <span class="fm-row-label">{{ k.label }}</span>
          <span class="fm-n wff-val">{{ rowValue(k.key) }}</span>
          <v-icon size="16" class="fm-row-trail">mdi-chevron-right</v-icon>
        </button>
      </div>

      <div class="tbm-footer">
        <v-icon size="14">mdi-information-outline</v-icon>
        A one-off: under 3 pictures, unrated, no recipe, not imported.
      </div>
    </div>

    <div
      v-if="sub"
      :id="submenuId"
      ref="subRef"
      class="fm-sub-slot"
      :style="{ marginTop: `${subTop}px` }"
      @keydown.left.capture="onLeft"
    >
      <!-- Pick one, and the only open-ended list searchable, as the grid's
           Checkpoint is. The store models one checkpoint, so its rows carry a
           check rather than checkboxes. -->
      <FilterChecklistMenu
        v-if="sub === 'checkpoint'"
        pick-one
        title="Checkpoint"
        placeholder="Filter checkpoints…"
        :items="checkpointItems"
        :checked="
          store.filters.checkpoint == null ? [] : [store.filters.checkpoint]
        "
        :clearable="store.filters.checkpoint != null"
        hint="picks the highlighted one."
        empty-text="No workflow here names a checkpoint."
        @toggle="(v) => store.setFilters({ checkpoint: v })"
        @clear="store.setFilters({ checkpoint: null })"
      />

      <!-- Rating copies the grid's Score flyout: star rows, "at least" only. -->
      <div
        v-else-if="sub === 'minRating'"
        class="tbm fm-sub"
        role="group"
        aria-label="Rating"
      >
        <div class="tbm-header">
          <span class="tbm-title">Rating</span>
          <span class="tbm-spacer"></span>
          <button
            class="tbm-ghost"
            type="button"
            :disabled="store.filters.minRating == null"
            @click="store.setFilters({ minRating: null })"
          >
            Clear
          </button>
        </div>
        <div
          class="tbm-section"
          role="radiogroup"
          aria-label="At least"
          @keydown="onStarKeydown"
        >
          <span class="tbm-label">At least</span>
          <button
            v-for="o in starOptions"
            :key="String(o.id)"
            class="fm-row"
            type="button"
            role="radio"
            :aria-checked="store.filters.minRating === o.id ? 'true' : 'false'"
            :aria-label="o.id == null ? 'Any' : `At least ${o.id} stars`"
            :tabindex="
              o.id === tabStopId(starOptions, store.filters.minRating) ? 0 : -1
            "
            @click="store.setFilters({ minRating: o.id })"
          >
            <span class="fm-row-label">
              <template v-if="o.id == null">Any</template>
              <span v-else class="wff-stars" aria-hidden="true">
                <v-icon
                  v-for="i in 5"
                  :key="i"
                  size="14"
                  :class="{ 'wff-star--off': i > o.id }"
                  >mdi-star</v-icon
                >
              </span>
            </span>
            <span class="fm-n">{{ o.count }}</span>
            <v-icon size="16" class="fm-row-trail">{{
              store.filters.minRating === o.id ? "mdi-check" : ""
            }}</v-icon>
          </button>
        </div>
      </div>

      <!-- Type and Source: short pick-one lists. -->
      <div
        v-else
        class="tbm fm-sub"
        role="group"
        :aria-label="kindOf(sub).label"
      >
        <div class="tbm-header">
          <span class="tbm-title">{{ kindOf(sub).label }}</span>
          <span class="tbm-spacer"></span>
          <button
            class="tbm-ghost"
            type="button"
            :disabled="store.filters[sub] == null"
            @click="store.setFilters({ [sub]: null })"
          >
            Clear
          </button>
        </div>
        <div class="tbm-section">
          <p v-if="!options(sub).length" class="fm-help">
            Nothing in this grid says.
          </p>
          <OptionRows
            v-else
            :options="options(sub)"
            :model-value="store.filters[sub]"
            :aria-label="kindOf(sub).label"
            @update:model-value="(id) => store.setFilters({ [sub]: id })"
          >
            <template #meta="{ option }">
              <span class="fm-n">{{ option.count }}</span>
            </template>
          </OptionRows>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * The Workflows screen's Filters menu (v1.12 F7): the picture grid's cascade
 * (`FilterMenu.vue`) with one exception. The three show/hide switches stay on
 * the root, where one click flips them; Type, Checkpoint, Source and Rating
 * are rows that carry their current value and open a flyout beside the menu.
 * The classes are the shared `.tbm-*` / `.fm-*` ones that menu made global.
 *
 * The state lives in `useWorkflowsStore` and nowhere else: the strip's chips,
 * this menu's row values and the filter button's badge all read
 * `filterChips`, so the three cannot disagree about which filters are on.
 */
import { computed, nextTick, reactive, ref, watch } from "vue";
import { VIcon } from "vuetify/components";

import { useWorkflowsStore } from "../../stores/useWorkflowsStore";
import { arrowStep, tabStopId } from "../../utils/radioGroup.js";
import OptionRows from "../widgets/OptionRows.vue";
import FilterChecklistMenu from "./FilterChecklistMenu.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
});

const store = useWorkflowsStore();

const CHECKS = [
  { key: "hideOneOffs", label: "Hide one-offs" },
  { key: "showHidden", label: "Hidden workflows" },
  { key: "ghosts", label: "Keeps something deleted" },
];

// `chip` is the key of this kind's chip in `workflowFilterChips`, whose value
// is what the row says is picked.
const KINDS = [
  { key: "type", label: "Type", icon: "mdi-shape-outline", chip: "type" },
  // The checkpoint glyph is the model shelf's, as on the grid's menu.
  {
    key: "checkpoint",
    label: "Checkpoint",
    icon: "mdi-package-variant-closed",
    chip: "checkpoint",
  },
  { key: "source", label: "Source", icon: "mdi-file-outline", chip: "source" },
  {
    key: "minRating",
    label: "Rating",
    icon: "mdi-star-outline",
    chip: "min-rating",
  },
];

const OPTION_LISTS = { type: "types", source: "sources" };

const sub = ref(null);
const subTop = ref(0);
const rootRef = ref(null);
const subRef = ref(null);
const rowRefs = reactive({});
const submenuId = "wff-submenu";

function kindOf(key) {
  return KINDS.find((k) => k.key === key);
}

function options(key) {
  return store.filterOptions[OPTION_LISTS[key]] ?? [];
}

// An empty value means "any".
function rowValue(key) {
  const chip = kindOf(key).chip;
  return store.filterChips.find((c) => c.key === chip)?.value ?? "";
}

// The number beside each checkbox is what it is holding back, or letting in:
// the server's own counts for the first two, and this grid's for the third.
const counts = computed(() => ({
  hideOneOffs: store.oneOffs,
  showHidden: store.hidden,
  ghosts: store.filterOptions.ghosts,
}));

const checkpointItems = computed(() =>
  store.filterOptions.checkpoints.map((c) => ({
    value: c.id,
    label: c.label,
    count: c.count,
  })),
);

const starOptions = computed(() => [
  { id: null, count: "" },
  ...store.filterOptions.ratings.map((r) => ({ id: r.id, count: r.count })),
]);

// The radiogroup contract (utils/radioGroup.js): one tab stop, arrows select.
function onStarKeydown(event) {
  const id = arrowStep(event, starOptions.value, store.filters.minRating);
  if (id !== undefined) store.setFilters({ minRating: id });
}

// The grid cascade's placement (`FilterMenu.vue`'s `openKind`): line the
// flyout up with its row, but never so low that its bottom sits below the
// root's, which would make the whole cascade taller and flip the v-menu.
function openKind(kind) {
  sub.value = kind;
  const row = rowRefs[kind];
  const rowTop = row ? Math.max(0, row.offsetTop - 8) : 0;
  subTop.value = rowTop;
  nextTick(() => {
    const rootHeight = rootRef.value?.offsetHeight ?? 0;
    const subHeight = subRef.value?.offsetHeight ?? 0;
    subTop.value = Math.max(0, Math.min(rowTop, rootHeight - subHeight));
    // Into the body, never the header's Clear. The checklist focuses its own
    // search field once it mounts.
    const body = subRef.value?.querySelector(".tbm-section");
    const target =
      body?.querySelector('[role="radio"][tabindex="0"]') ??
      body?.querySelector("input:not([disabled]), button:not([disabled])");
    target?.focus();
  });
}

function toggle(kind) {
  if (sub.value === kind) sub.value = null;
  else openKind(kind);
}

// Left arrow inside a flyout returns to its row, unless it is moving a caret
// that has somewhere left to go. Caught on the way DOWN, because the flyouts'
// radio groups (`arrowStep`) take ← as "previous option" and stop it, so a
// reader pressing it to go back would change the filter instead.
function onLeft(event) {
  const t = event.target;
  if (t?.tagName === "INPUT" && (t.selectionStart || t.selectionEnd)) return;
  event.preventDefault();
  event.stopPropagation();
  rowRefs[sub.value]?.focus();
  sub.value = null;
}

watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) sub.value = null;
  },
);
</script>

<style scoped>
.fm-cascade {
  display: flex;
  /* A phone has no room for two menus side by side: the flyout wraps below. */
  flex-wrap: wrap;
  align-items: flex-start;
  gap: var(--space-2);
}
.fm-root {
  width: var(--filter-menu-w);
  max-width: 94vw;
}
.fm-sub-slot {
  max-height: min(80vh, 760px);
  overflow-y: auto;
  overscroll-behavior: contain;
}
/* The row says what is picked, not a count: in ink, and it gives way to the
   label before it pushes the chevron off. */
.wff-val {
  max-width: 50%;
  overflow: hidden;
  text-overflow: ellipsis;
  color: rgb(var(--v-theme-on-panel));
}
.wff-stars {
  display: inline-flex;
  color: rgb(var(--v-theme-surface-warning));
}
.wff-star--off {
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
  opacity: 0.6;
}
</style>
