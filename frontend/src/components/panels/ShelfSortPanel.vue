<template>
  <div class="tbm shelf-sort-panel">
    <span class="tbm-caret tbm-caret--end"></span>
    <div class="tbm-header">
      <v-icon size="18" class="tbm-header-icon">{{ activeSort.icon }}</v-icon>
      <span class="tbm-title">Sort</span>
      <span class="tbm-spacer"></span>
      <!-- The direction lives in the header rather than as a sixth option: it
           is a property of whichever key is chosen, not a key of its own. -->
      <button class="tbm-ghost" type="button" @click="toggleDirection">
        <v-icon size="16">{{ directionIcon }}</v-icon>
        <span>{{ directionLabel }}</span>
      </button>
    </div>

    <!-- Pick-one, so `radiogroup`/`radio` with `aria-checked` (the approved
         contract, docs/design/buttons.md "Pick one"), never `role="menu"`/
         `menuitemradio`: this panel is a plain div holding other controls (the
         direction button above), so a menu role would promise a widget
         contract nothing honours. -->
    <div class="tbm-section">
      <span class="tbm-label">Sort by</span>
      <OptionRows
        :options="onSets ? setSortOptions : sortOptions"
        :model-value="onSets ? view.setSortKey : view.sortKey"
        :columns="2"
        aria-label="Sort by"
        @update:model-value="
          (key) => store.setView(onSets ? { setSortKey: key } : { sortKey: key })
        "
      />
      <!-- A tie-break inside each group, not a place: the groups and the sort
           stay as they are, and a favourite leads its own group. -->
      <label class="tbm-check fm-check shelf-sort-favourites">
        <input
          type="checkbox"
          :checked="view.favouritesFirst"
          @change="
            store.setView({ favouritesFirst: $event.target.checked })
          "
        />
        <span class="fm-check-label">Favourites first</span>
      </label>
      <p class="tbm-footer">
        Inside each group, favourites lead and the sort orders the rest. Off, a
        favourite sits where the sort puts it.
      </p>
    </div>

    <!-- The list's grouping (#2168). The Sets place is a card grid whose
         grouping is its own, and a folder is not banded, so this section is not
         offered on either. Type, Base model, Feature and None are the only
         bands a list can take: the folder is the sidebar's slot now, and a
         workflow set is the Sets place. -->
    <div
      v-if="store.place !== 'sets' && store.place !== 'folder'"
      class="tbm-section shelf-group-section"
    >
      <span class="tbm-label">Group by</span>
      <OptionRows
        :options="groupOptions"
        :model-value="view.listGroupBy"
        :columns="2"
        aria-label="Group by"
        @update:model-value="
          (key) => store.setView({ groupBy: key, listGroupBy: key })
        "
      />
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue";
import {
  GRID_GROUP_BY,
  LIST_GROUP_BY_KEYS,
  SET_SORT_KEYS,
  SET_SORT_LABELS,
  SORT_KEYS,
  useModelShelfStore,
} from "../../stores/useModelShelfStore";
import {
  GROUP_BY_LABELS,
  SORT_LABELS,
  sortDirectionLabel,
} from "../../utils/modelShelf";
import OptionRows from "../widgets/OptionRows.vue";

const store = useModelShelfStore();
const view = store.view;

// Labels only: OptionRows draws no option icon.
const asOptions = (keys, labels) =>
  keys.map((key) => ({ id: key, label: labels[key].label }));
const sortOptions = asOptions(SORT_KEYS, SORT_LABELS);
const setSortOptions = asOptions(SET_SORT_KEYS, SET_SORT_LABELS);
const groupOptions = asOptions(LIST_GROUP_BY_KEYS, GROUP_BY_LABELS);

/** The set grid sorts on its own keys and keeps its own direction. */
const onSets = computed(() => view.groupBy === GRID_GROUP_BY);

const activeSort = computed(() =>
  onSets.value
    ? SET_SORT_LABELS[view.setSortKey] || SET_SORT_LABELS.used
    : SORT_LABELS[view.sortKey] || SORT_LABELS.added_at,
);

const activeDirection = computed(() =>
  onSets.value ? view.setSortDirection : view.sortDirection,
);

const directionLabel = computed(() =>
  onSets.value
    ? activeSort.value.words[activeDirection.value === "asc" ? 0 : 1]
    : sortDirectionLabel(view.sortKey, view.sortDirection),
);

const directionIcon = computed(() =>
  activeDirection.value === "asc" ? "mdi-sort-ascending" : "mdi-sort-descending",
);

function toggleDirection() {
  const next = activeDirection.value === "asc" ? "desc" : "asc";
  store.setView(
    onSets.value ? { setSortDirection: next } : { sortDirection: next },
  );
}
</script>

<style scoped>
.shelf-sort-panel {
  width: 320px;
  max-width: 94vw;
}
</style>
