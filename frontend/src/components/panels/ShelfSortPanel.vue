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

    <!-- Pick-one, so `radiogroup`/`radio` with `aria-checked` (the approved
         contract, docs/design/buttons.md "Pick one"), never `role="menu"`/
         `menuitemradio`: this panel is a plain div holding other controls (the
         direction button above), so a menu role would promise a widget
         contract nothing honours. Sort by is OptionRows; the short axes below
         are Segmented. -->
    <div v-if="showsSort" class="tbm-section">
      <span class="tbm-label">Sort by</span>
      <OptionRows
        :options="sortOptions"
        :model-value="view.sortKey"
        :columns="2"
        aria-label="Sort by"
        @update:model-value="(key) => store.setView({ sortKey: key })"
      />
    </div>

    <!-- Three axes, not four: Type is already a Show checkbox and is already on
         every row as an icon and a word, so grouping by it would restate what
         the reader can see. -->
    <div v-if="showsGroup" class="tbm-section">
      <span class="tbm-label">Group by</span>
      <Segmented
        :options="groupOptions"
        :model-value="view.groupBy"
        full
        aria-label="Group by"
        @update:model-value="(key) => store.setView({ groupBy: key })"
      />
    </div>

    <!-- The folder layout is a SUB-CHOICE of Folder, not a fourth axis, so it
         renders only while Folder is selected. Offered as `Sort: Drive |
         Folder` once, which was never a sort: it reordered nothing and grouped
         everything, and having it sit in the sort control is why the absence of
         real sorting went unnoticed. -->
    <div v-if="showsGroup && view.groupBy === 'folder'" class="tbm-section">
      <span class="tbm-label">Folders laid out</span>
      <Segmented
        :options="layoutOptions"
        :model-value="view.folderLayout"
        full
        aria-label="Folders laid out"
        @update:model-value="(key) => store.setView({ folderLayout: key })"
      />
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
import OptionRows from "../widgets/OptionRows.vue";
import Segmented from "../widgets/Segmented.vue";

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

// `icon` renders in the OptionRows lists; the Segmented axes take the default
// label variant, where a glyph costs the room a label like "Drive, then folder"
// needs. One mapper either way.
const asOptions = (keys, labels) =>
  keys.map((key) => ({
    id: key,
    label: labels[key].label,
    icon: labels[key].icon,
  }));
const sortOptions = asOptions(SORT_KEYS, SORT_LABELS);
const groupOptions = asOptions(GROUP_BY_KEYS, GROUP_BY_LABELS);
const layoutOptions = asOptions(FOLDER_LAYOUTS, FOLDER_LAYOUT_LABELS);

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

/* Only the Group section needs the extra room: its four segments share the
   track, and "Base model" wants ~76px of label at --text-sm. Sort stays 320px, matching the Show popover
   beside it in the same bar. Below ~447px of viewport the 94vw cap wins and
   the label ellipsizes again, as everything in this bar already does. */
.shelf-sort-panel--wide {
  width: 420px;
}
</style>
