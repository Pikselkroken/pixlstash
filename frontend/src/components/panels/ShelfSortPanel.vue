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

    <!-- `role="group"` and `aria-pressed`, never `role="menu"`/`menuitemradio`:
         this panel is a plain div holding other controls (the direction button
         above), so a menu role here would promise a widget contract nothing
         honours. Same shape as DedupTierMenu. -->
    <div class="tbm-section">
      <span class="tbm-label">Sort by</span>
      <div class="tbm-grid-2" role="group" aria-label="Sort by">
        <button
          v-for="key in SORT_KEYS"
          :key="key"
          class="tbm-toggle"
          :class="{ 'tbm-toggle--on': view.sortKey === key }"
          type="button"
          :aria-pressed="view.sortKey === key"
          @click="store.setView({ sortKey: key })"
        >
          <v-icon size="18" class="tbm-toggle-icon">{{
            SORT_LABELS[key].icon
          }}</v-icon>
          <span class="tbm-toggle-label">{{ SORT_LABELS[key].label }}</span>
        </button>
      </div>
    </div>

    <!-- Three axes, not four: Type is already a Show checkbox and is already on
         every row as an icon and a word, so grouping by it would restate what
         the reader can see. Folder is the drive band; it is a grouping VALUE
         rather than an outer tier, so the shelf never draws more than one level
         of headers. -->
    <div class="tbm-section">
      <span class="tbm-label">Group by</span>
      <div class="tbm-grid-3" role="group" aria-label="Group by">
        <button
          v-for="key in GROUP_BY_KEYS"
          :key="key"
          class="tbm-toggle"
          :class="{ 'tbm-toggle--on': view.groupBy === key }"
          type="button"
          :aria-pressed="view.groupBy === key"
          @click="store.setView({ groupBy: key })"
        >
          <v-icon size="18" class="tbm-toggle-icon">{{
            GROUP_BY_LABELS[key].icon
          }}</v-icon>
          <span class="tbm-toggle-label">{{ GROUP_BY_LABELS[key].label }}</span>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue";
import {
  GROUP_BY_KEYS,
  SORT_KEYS,
  useModelShelfStore,
} from "../../stores/useModelShelfStore";
import {
  GROUP_BY_LABELS,
  SORT_LABELS,
  sortDirectionLabel,
} from "../../utils/modelShelf";

const store = useModelShelfStore();
const view = store.view;

const activeSort = computed(
  () => SORT_LABELS[view.sortKey] || SORT_LABELS.added_at,
);

const directionLabel = computed(() =>
  sortDirectionLabel(view.sortKey, view.sortDirection),
);

const directionIcon = computed(() =>
  view.sortDirection === "asc" ? "mdi-sort-ascending" : "mdi-sort-descending",
);

function toggleDirection() {
  store.setView({
    sortDirection: view.sortDirection === "asc" ? "desc" : "asc",
  });
}
</script>

<style scoped>
.shelf-sort-panel {
  width: 320px;
  max-width: 94vw;
}
</style>
