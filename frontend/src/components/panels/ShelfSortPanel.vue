<template>
  <div
    class="tbm shelf-sort-panel"
    :class="{ 'shelf-sort-panel--wide': showsGroup }"
  >
    <span class="tbm-caret tbm-caret--end"></span>
    <div class="tbm-header">
      <v-icon size="18" class="tbm-header-icon">{{ headerIcon }}</v-icon>
      <span class="tbm-title">{{ showsSort ? "Sort" : "Group" }}</span>
      <span class="tbm-spacer"></span>
      <!-- The direction lives in the header rather than as a sixth option: it
           is a property of whichever key is chosen, not a key of its own. -->
      <button
        v-if="showsSort"
        class="tbm-ghost"
        type="button"
        @click="toggleDirection"
      >
        <v-icon size="16">{{ directionIcon }}</v-icon>
        <span>{{ directionLabel }}</span>
      </button>
    </div>

    <!-- `role="group"` and `aria-pressed`, never `role="menu"`/`menuitemradio`:
         this panel is a plain div holding other controls (the direction button
         above), so a menu role here would promise a widget contract nothing
         honours. Same shape as DedupTierMenu. -->
    <div v-if="showsSort" class="tbm-section">
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
         the reader can see. -->
    <div v-if="showsGroup" class="tbm-section">
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

    <!-- The folder layout is a SUB-CHOICE of Folder, not a fourth axis, so it
         renders only while Folder is selected. Offered as `Sort: Drive |
         Folder` once, which was never a sort: it reordered nothing and grouped
         everything, and having it sit in the sort control is why the absence of
         real sorting went unnoticed. -->
    <div v-if="showsGroup && view.groupBy === 'folder'" class="tbm-section">
      <span class="tbm-label">Folders laid out</span>
      <div class="tbm-grid-2" role="group" aria-label="Folders laid out">
        <button
          v-for="key in FOLDER_LAYOUTS"
          :key="key"
          class="tbm-toggle"
          :class="{ 'tbm-toggle--on': view.folderLayout === key }"
          type="button"
          :aria-pressed="view.folderLayout === key"
          @click="store.setView({ folderLayout: key })"
        >
          <v-icon size="18" class="tbm-toggle-icon">{{
            FOLDER_LAYOUT_LABELS[key].icon
          }}</v-icon>
          <span class="tbm-toggle-label">{{
            FOLDER_LAYOUT_LABELS[key].label
          }}</span>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue";
import {
  FOLDER_LAYOUTS,
  GROUP_BY_KEYS,
  SORT_KEYS,
  useModelShelfStore,
} from "../../stores/useModelShelfStore";
import {
  FOLDER_LAYOUT_LABELS,
  GROUP_BY_LABELS,
  SORT_LABELS,
  sortDirectionLabel,
} from "../../utils/modelShelf";

// Two axes, two toolbar controls, one panel: the resolved design gives Sort and
// Group a button each, labelled with the value each currently holds, so the two
// sections that used to share one popover are drawn separately. Same component
// either way, because the toggles, the labels and the store writes are the same
// - only which section is on screen differs (#904).
const props = defineProps({
  /** `"sort"`, `"group"`, or `"all"` for both in one panel. */
  section: { type: String, default: "all" },
});

const store = useModelShelfStore();
const view = store.view;

const showsSort = computed(() => props.section !== "group");
const showsGroup = computed(() => props.section !== "sort");

const headerIcon = computed(() =>
  showsSort.value
    ? activeSort.value.icon
    : (GROUP_BY_LABELS[view.groupBy] || GROUP_BY_LABELS.none).icon,
);

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

/* Only the Group section needs the extra room: its three columns left ~51px of
   label at 320px, so "Base model" - the middle one - ellipsized, and it wants
   ~76px at --text-sm. 420px gives ~126px tracks, ~84px of label, clearing it
   on a wide fallback font too. Sort stays 320px, matching the Show popover
   beside it in the same bar. Below ~447px of viewport the 94vw cap wins and
   the label ellipsizes again, as everything in this bar already does. */
.shelf-sort-panel--wide {
  width: 420px;
}
</style>
