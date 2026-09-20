<template>
  <div
    v-if="chips.length"
    class="filter-strip"
    :class="{ 'filter-strip--inline': inline }"
    role="region"
    aria-label="Active filters"
  >
    <div class="filter-strip-chips">
      <span v-for="chip in chips" :key="chip.key" class="filter-chip">
        <span class="filter-chip-text"
          ><span class="filter-chip-kind">{{ chip.kind }}</span>
          {{ chip.value }}</span
        >
        <button
          class="filter-chip-x"
          type="button"
          :aria-label="`Remove filter: ${chip.kind} ${chip.value}`"
          @click="chip.remove()"
        >
          <v-icon size="14">mdi-close</v-icon>
        </button>
      </span>
    </div>
    <span class="filter-strip-tail">
      <span class="filter-strip-count">{{ ofLabel }}</span>
      <button class="filter-strip-clear" type="button" @click="clearAll">
        Clear all
      </button>
    </span>
  </div>
</template>

<script setup>
/**
 * The row of active filters under the toolbar: one chip per filter, each with
 * its own ×, and Clear all at the end. It is only there while a filter is on.
 *
 * **Two screens, one strip.** Given no `chips` it draws the picture grid's, off
 * the filter store, positioned under that grid's selection bar. The Workflows
 * screen (F7) hands its own chips and `inline`, because its toolbar is in the
 * normal flow and a strip absolutely positioned against a selection bar it
 * does not have would sit on top of the first row of cards. Everything a chip
 * needs is in the chip - a `kind`, a `value` and its own `remove` - so nothing
 * here knows which screen it is on.
 */
import { computed } from "vue";
import { useFilterCounts } from "../../composables/useFilterCounts";
import { useFilterStore } from "../../stores/useFilterStore";
import { useGridStore } from "../../stores/useGridStore";
import { filterChips } from "../../utils/filterChips";

const props = defineProps({
  // The grid's view with no filters, pre-encoded (buildFilterCountBaseQuery).
  countBaseQuery: { type: String, default: null },
  allPicturesView: { type: Boolean, default: true },
  // `[{key, kind, value, remove}]`. Null means "the picture grid's own".
  chips: { type: Array, default: null },
  // What the tail says instead of the picture grid's "N of M".
  ofLabel: { type: String, default: null },
  // In the normal flow rather than pinned under the selection bar.
  inline: { type: Boolean, default: false },
});

const store = useFilterStore();
const gridStore = useGridStore();

const chips = computed(
  () =>
    props.chips ??
    filterChips(store, { allPicturesView: props.allPicturesView }),
);

// The view's size, the "of N", from the same cache the menu header reads.
const { count } = useFilterCounts(() => props.countBaseQuery);
const total = computed(() => (chips.value.length ? count("") : undefined));

const ofLabel = computed(() => {
  if (props.ofLabel != null) return props.ofLabel;
  const n = Number(gridStore.matchCount || 0).toLocaleString();
  return total.value == null ? n : `${n} of ${total.value.toLocaleString()}`;
});

function clearAll() {
  for (const chip of chips.value) chip.remove();
}
</script>

<style scoped>
.filter-strip {
  position: absolute;
  left: 0;
  top: var(--selbar-height, 48px);
  width: 100%;
  height: var(--toolbar-height);
  box-sizing: border-box;
  z-index: calc(var(--z-sticky) - 1);
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: 0 var(--space-3);
  background: rgba(var(--v-theme-toolbar), 0.95);
  border-bottom: 1px solid rgb(var(--v-theme-divider));
}
/* After `.filter-strip`, not before: same specificity, so source order is what
   makes the modifier win. */
.filter-strip--inline {
  position: static;
  width: auto;
  z-index: auto;
}
.filter-strip-chips {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
  overflow-x: auto;
  /* Visible, so chips past the edge read as more to scroll to. */
  scrollbar-width: thin;
}
.filter-chip {
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
  height: var(--control-h-sm);
  padding-left: var(--space-3);
  border-radius: var(--radius-sm);
  background: color-mix(
    in srgb,
    rgb(var(--v-theme-on-surface)) 10%,
    transparent
  );
  color: rgb(var(--v-theme-on-surface));
  font-size: var(--text-sm);
  white-space: nowrap;
}
.filter-chip-text {
  padding-right: var(--space-1);
}
.filter-chip-kind {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
.filter-chip-x {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  margin-right: var(--space-1);
  border-radius: var(--radius-sm);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
.filter-chip-x:hover {
  background: var(--hover-wash);
  color: rgb(var(--v-theme-surface-error));
}
.filter-strip-tail {
  margin-left: auto;
  display: flex;
  align-items: center;
  flex-shrink: 0;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
.filter-strip-count {
  font-variant-numeric: tabular-nums;
}
.filter-strip-clear {
  height: var(--control-h-sm);
  padding: 0 var(--space-3);
  border-radius: var(--radius-sm);
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-surface));
}
.filter-strip-clear:hover {
  background: var(--hover-wash);
}
</style>
