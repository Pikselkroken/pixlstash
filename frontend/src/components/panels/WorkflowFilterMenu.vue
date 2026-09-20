<template>
  <div class="tbm wff" role="group" aria-label="Workflow filters">
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

    <!-- The three the design draws as checkboxes. The first two are the
         server's flags, so ticking one re-reads the grid; the third only ever
         removes a card and is applied here. -->
    <div class="tbm-section">
      <label
        v-for="row in CHECKS"
        :key="row.key"
        class="tbm-check fm-check wff-check"
      >
        <input
          type="checkbox"
          :checked="store.filters[row.key]"
          :data-testid="`wff-${row.key}`"
          @change="store.setFilters({ [row.key]: $event.target.checked })"
        />
        <span class="fm-check-label">
          {{ row.label }}
          <span v-if="row.note" class="wff-note">{{ row.note }}</span>
        </span>
        <span class="fm-n">{{ counts[row.key] }}</span>
      </label>
      <p class="fm-help">
        A one-off is a workflow nobody is going to look for: fewer than 3
        pictures, no rating, no saved recipe, not imported.
      </p>
    </div>

    <div v-for="pick in PICKS" :key="pick.key" class="tbm-section">
      <div class="wff-label-row">
        <span class="tbm-label">{{ pick.label }}</span>
        <button
          class="tbm-ghost"
          type="button"
          :disabled="store.filters[pick.key] == null"
          @click="store.setFilters({ [pick.key]: null })"
        >
          Clear
        </button>
      </div>
      <p v-if="!options(pick.key).length" class="fm-help">
        Nothing in this grid says.
      </p>
      <div v-else class="wff-scroll">
        <OptionRows
          :options="options(pick.key)"
          :model-value="store.filters[pick.key]"
          :aria-label="pick.label"
          @update:model-value="(id) => store.setFilters({ [pick.key]: id })"
        >
          <template #meta="{ option }">
            <span class="fm-n">{{ option.count }}</span>
          </template>
        </OptionRows>
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * The Workflows screen's Filters panel (v1.12 F7).
 *
 * **One panel, not #1387's cascade.** That menu fans out into submenus because
 * the picture grid has eleven filter kinds and several of them are searchable
 * lists of hundreds of tags; this screen has seven filters whose lists are the
 * library's own workflow types, its checkpoints and five stars. The design
 * draws them as one panel and they fit in one, so the rows, the counts and the
 * chip strip are #1387's and the second level is not. The classes are the
 * shared `.tbm-*` / `.fm-*` ones that menu made global.
 *
 * The state lives in `useWorkflowsStore` and nowhere else: the strip's chips,
 * this panel and the filter button's badge all read `filterChips`, so the
 * three cannot disagree about which filters are on.
 */
import { computed } from "vue";
import { VIcon } from "vuetify/components";

import { useWorkflowsStore } from "../../stores/useWorkflowsStore";
import OptionRows from "../widgets/OptionRows.vue";

const store = useWorkflowsStore();

const CHECKS = [
  { key: "hideOneOffs", label: "Hide one-offs" },
  { key: "showHidden", label: "Show hidden workflows" },
  { key: "ghosts", label: "Ghosts", note: "Keeps something deleted" },
];

const PICKS = [
  { key: "type", label: "Type" },
  { key: "checkpoint", label: "Checkpoint" },
  { key: "source", label: "Source" },
  { key: "minRating", label: "Min rating" },
];

const OPTION_LISTS = {
  type: "types",
  checkpoint: "checkpoints",
  source: "sources",
  minRating: "ratings",
};

function options(key) {
  return store.filterOptions[OPTION_LISTS[key]] ?? [];
}

// The number beside each checkbox is what it is holding back, or letting in:
// the server's own counts for the first two, and this grid's for Ghosts.
const counts = computed(() => ({
  hideOneOffs: store.oneOffs,
  showHidden: store.hidden,
  ghosts: store.filterOptions.ghosts,
}));
</script>

<style scoped>
.wff {
  width: var(--filter-submenu-w);
  max-width: 94vw;
}
/* `.tbm-check` brings the checkbox itself and `.fm-check` the menu row, as
   `FilterChecklistMenu` pairs them. All this adds is the vertical centring the
   Ghosts row's second line would otherwise lose. */
.wff-check {
  align-items: center;
}
/* The rule below the name, as drawn: the row stays one line and the reason
   sits under it in secondary ink. */
.wff-note {
  display: block;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}
.wff-label-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}
/* A library can hold more checkpoints than a panel can be tall. Six rows of
   the shared menu-row height, so the cap is a number of rows rather than a
   pixel count of its own, and a seventh reads as more to scroll to. */
.wff-scroll {
  max-height: calc(var(--control-h-bar) * 6);
  overflow-y: auto;
  scrollbar-width: thin;
}
</style>
