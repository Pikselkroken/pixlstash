import { watch } from "vue";
import { useSearchStore } from "../stores/useSearchStore";
import { useGridStore } from "../stores/useGridStore";

/**
 * Keep the search bar's input in step with the committed query, and decide
 * when the history dropdown should be showing.
 *
 * The two are separate on purpose: `searchQuery` is what the grid fetched
 * against, `searchInput` is what the user is typing. They only converge when
 * the query changes from somewhere else (a cleared search, a restored route).
 */
export function useSearchBarSync() {
  const searchStore = useSearchStore();
  const gridStore = useGridStore();

  watch(
    () => searchStore.searchQuery,
    (newVal, oldVal) => {
      if (searchStore.searchInput !== newVal) {
        searchStore.searchInput = newVal || "";
      }
      // Clearing a search widens the result set, so the grid has to repaint.
      if (!newVal && oldVal) {
        gridStore.refreshGridVersion();
      }
    },
  );

  watch(
    [() => searchStore.searchInput, () => searchStore.searchHistory],
    () => {
      const needle = (searchStore.searchInput || "").trim();
      if (!needle) {
        searchStore.isSearchHistoryOpen = false;
        return;
      }
      searchStore.isSearchHistoryOpen =
        searchStore.filteredSearchHistory.length > 0;
    },
  );
}
