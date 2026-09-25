<template>
  <div class="wff" @keydown.left.capture="onLeft">
    <div
      ref="rootRef"
      class="tbm wff-root"
      role="group"
      aria-label="Workflow filters"
    >
      <span class="tbm-caret tbm-caret--start"></span>
      <div class="tbm-header">
        <v-icon size="18" class="tbm-header-icon">mdi-filter</v-icon>
        <span class="tbm-title">Filters</span>
        <span class="tbm-spacer"></span>
        <span class="wff-of">{{ store.filterOfLabel }}</span>
        <button
          class="tbm-ghost"
          type="button"
          :disabled="!store.filterChips.length"
          @click="store.clearFilters()"
        >
          Clear all
        </button>
      </div>

      <div v-for="section in SECTIONS" :key="section.label" class="tbm-section">
        <span class="tbm-label">{{ section.label }}</span>
        <button
          v-for="kind in section.kinds"
          :key="kind"
          :ref="(el) => (rowRefs[kind] = el)"
          class="fm-row"
          type="button"
          :data-kind="kind"
          :aria-expanded="sub === kind ? 'true' : 'false'"
          :aria-controls="sub === kind ? submenuId : undefined"
          @click="toggle(kind)"
          @keydown.right.prevent="openKind(kind)"
          @keydown.down.prevent="stepRow(kind, 1)"
          @keydown.up.prevent="stepRow(kind, -1)"
        >
          <v-icon size="16" class="fm-row-lead">{{ KINDS[kind].icon }}</v-icon>
          <span class="fm-row-label">{{ KINDS[kind].label }}</span>
          <span class="fm-n">{{ rowValue(kind) }}</span>
          <v-icon size="16" class="fm-row-trail">mdi-chevron-right</v-icon>
        </button>
      </div>
    </div>

    <div
      v-if="sub"
      :id="submenuId"
      ref="subRef"
      class="wff-sub-slot"
      :style="{ marginTop: `${subTop}px` }"
    >
      <div
        class="tbm fm-sub"
        role="group"
        :aria-label="KINDS[sub].label"
        :data-testid="`wff-sub-${sub}`"
      >
        <div class="tbm-header">
          <span class="tbm-title">{{ KINDS[sub].label }}</span>
        </div>

        <div v-if="sub === 'checkpoint'" class="tbm-section">
          <div class="tbm-input-wrap">
            <v-icon size="16" class="tbm-input-icon">mdi-magnify</v-icon>
            <input
              v-model="checkpointQuery"
              class="tbm-input tbm-input--with-icon"
              placeholder="Find a checkpoint…"
              aria-label="Find a checkpoint"
              autocomplete="off"
              @keydown.down.prevent="focusChosen"
            />
          </div>
        </div>

        <div class="tbm-section">
          <span v-if="sub === 'minRating'" class="tbm-label">At least</span>
          <div :class="{ 'wff-scroll': sub === 'checkpoint' }">
            <OptionRows
              :options="rows(sub)"
              :model-value="store.filters[sub]"
              :aria-label="sub === 'minRating' ? 'At least' : KINDS[sub].label"
              @update:model-value="(id) => store.setFilters({ [sub]: id })"
            >
              <template v-if="sub === 'minRating'" #label="{ option }">
                <template v-if="option.id == null">Any</template>
                <span v-else class="wff-stars" aria-hidden="true">
                  <v-icon
                    v-for="i in 5"
                    :key="i"
                    size="14"
                    :class="{ 'wff-star--off': i > option.id }"
                    >mdi-star</v-icon
                  >
                </span>
              </template>
              <template #meta="{ option }">
                <span class="fm-n">{{ option.count }}</span>
              </template>
            </OptionRows>
          </div>
          <p
            v-if="
              sub === 'checkpoint' &&
              checkpointQuery.trim() &&
              !checkpointHits.length
            "
            class="fm-help"
          >
            No checkpoint matches.
          </p>
        </div>

        <div v-if="footer(sub)" class="tbm-footer">{{ footer(sub) }}</div>
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * The Workflows screen's Filters menu: the picture grid's cascade
 * (`FilterMenu.vue`) with the Workflows screen's rows. A root list of filter
 * kinds under section labels, each opening a pick-one submenu beside it, so
 * somebody who has learnt one screen's Filters button knows the other.
 *
 * **Every filter is a pick-one list**, the three flags included: the grid's
 * menu has no checkboxes, so Hide/Show one-offs is a two-row radio list the
 * way Stacks is All / Stacked / Unstacked. A root row says only whether its
 * filter is on (`1`, or the rating as `4★+`); the chosen value is on the
 * chip in the strip.
 *
 * The state lives in `useWorkflowsStore` and nowhere else: the strip's chips,
 * this menu and the filter button's badge all read `filterChips`, so the
 * three cannot disagree about which filters are on.
 */
import { computed, nextTick, reactive, ref, watch } from "vue";
import { VIcon } from "vuetify/components";

import {
  DEFAULT_FILTERS,
  useWorkflowsStore,
} from "../../stores/useWorkflowsStore";
import OptionRows from "../widgets/OptionRows.vue";

const props = defineProps({
  // The v-menu keeps its content mounted, so a closed menu says so here and
  // reopens on the root alone.
  open: { type: Boolean, default: false },
});

const store = useWorkflowsStore();

const KINDS = {
  type: { label: "Type", icon: "mdi-shape-outline" },
  // The checkpoint glyph is the model shelf's, as in the grid's menu.
  checkpoint: { label: "Checkpoint", icon: "mdi-package-variant-closed" },
  source: { label: "Source", icon: "mdi-file-import-outline" },
  minRating: { label: "Rating", icon: "mdi-star-outline" },
  hideOneOffs: { label: "One-offs", icon: "mdi-numeric-1-circle-outline" },
  showHidden: { label: "Hidden", icon: "mdi-eye-off-outline" },
  ghosts: { label: "Ghosts", icon: "mdi-ghost-outline" },
};

// What a workflow is, how you judged it, and the flags that change which
// cards exist at all — last, where the grid puts Problems.
const SECTIONS = [
  { label: "Workflow", kinds: ["type", "checkpoint", "source"] },
  { label: "Quality", kinds: ["minRating"] },
  { label: "Show", kinds: ["hideOneOffs", "showHidden", "ghosts"] },
];
const ROW_ORDER = SECTIONS.flatMap((section) => section.kinds);

const FOOTERS = {
  hideOneOffs:
    "A one-off has fewer than 3 pictures, no rating, no saved recipe, and was not imported. The count is how many there are.",
  showHidden: "Workflows you hid from the grid.",
  ghosts: "A deleted picture, or a model that is no longer on the shelf.",
};

const sub = ref(null);
const subTop = ref(0);
const rootRef = ref(null);
const subRef = ref(null);
const rowRefs = reactive({});
const submenuId = "wff-submenu";
const checkpointQuery = ref("");

const checkpointHits = computed(() => {
  const q = checkpointQuery.value.trim().toLowerCase();
  const all = store.filterOptions.checkpoints;
  return q ? all.filter((c) => c.id.toLowerCase().includes(q)) : all;
});

function rowValue(kind) {
  if (kind === "minRating") {
    return store.filters.minRating == null
      ? ""
      : `${store.filters.minRating}★+`;
  }
  return store.filters[kind] === DEFAULT_FILTERS[kind] ? "" : "1";
}

/**
 * One submenu's radio rows. The pick-one lists lead with the row that turns
 * the filter off, counting every card; the flags carry what they hold back —
 * the server's own counts for one-offs and hidden, this grid's for ghosts.
 */
function rows(kind) {
  const total = store.cards.length;
  const opts = store.filterOptions;
  switch (kind) {
    case "type":
      return [{ id: null, label: "All", count: total }, ...opts.types];
    case "checkpoint":
      return [
        { id: null, label: "Any", count: total },
        ...checkpointHits.value,
      ];
    case "source":
      return [{ id: null, label: "Any", count: total }, ...opts.sources];
    case "minRating":
      return [
        { id: null, label: "Any", ariaLabel: "Any rating", count: total },
        ...opts.ratings.map((r) => ({
          ...r,
          ariaLabel: `At least ${r.id} stars`,
        })),
      ];
    case "hideOneOffs":
      return [
        { id: true, label: "Hide", count: store.oneOffs },
        { id: false, label: "Show", count: store.oneOffs },
      ];
    case "showHidden":
      return [
        { id: false, label: "Hide", count: store.hidden },
        { id: true, label: "Show", count: store.hidden },
      ];
    case "ghosts":
      return [
        { id: false, label: "Any", count: total },
        { id: true, label: "Keeps something deleted", count: opts.ghosts },
      ];
  }
  return [];
}

function footer(kind) {
  if (kind === "checkpoint") {
    return `${store.filterOptions.checkpoints.length} checkpoints in this grid.`;
  }
  return FOOTERS[kind] ?? "";
}

function focusChosen() {
  subRef.value?.querySelector('[role="radio"][tabindex="0"]')?.focus();
}

function openKind(kind) {
  sub.value = kind;
  checkpointQuery.value = "";
  const row = rowRefs[kind];
  const rowTop = row ? Math.max(0, row.offsetTop - 8) : 0;
  subTop.value = rowTop;
  nextTick(() => {
    // Level with its row, but never hanging below the root, as the grid's.
    const rootHeight = rootRef.value?.offsetHeight ?? 0;
    const subHeight = subRef.value?.offsetHeight ?? 0;
    subTop.value = Math.max(0, Math.min(rowTop, rootHeight - subHeight));
    // The Checkpoint field first, else the chosen radio; never the header.
    const field = subRef.value?.querySelector("input");
    if (field) field.focus();
    else focusChosen();
  });
}

function toggle(kind) {
  if (sub.value === kind) sub.value = null;
  else openKind(kind);
}

function stepRow(kind, step) {
  const at = ROW_ORDER.indexOf(kind);
  const next = ROW_ORDER[(at + step + ROW_ORDER.length) % ROW_ORDER.length];
  rowRefs[next]?.focus();
}

// Left arrow inside a submenu returns to its row, unless it is moving a caret.
// Caught on the way down: a radiogroup would otherwise take it as "previous".
function onLeft(event) {
  if (!sub.value || event.target?.tagName === "INPUT") return;
  if (!subRef.value?.contains(event.target)) return;
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
.wff {
  display: flex;
  /* A phone has no room for two menus side by side: the submenu wraps below. */
  flex-wrap: wrap;
  align-items: flex-start;
  gap: var(--space-2);
}
.wff-root {
  width: var(--filter-menu-w);
  max-width: 94vw;
}
.wff-of {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
  white-space: nowrap;
}
.wff-sub-slot {
  max-height: min(80vh, 760px);
  overflow-y: auto;
  overscroll-behavior: contain;
}
/* The one list that grows with the library: six rows of the shared menu-row
   height, then it scrolls. */
.wff-scroll {
  max-height: calc(var(--control-h-bar) * 6);
  overflow-y: auto;
  scrollbar-width: thin;
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
