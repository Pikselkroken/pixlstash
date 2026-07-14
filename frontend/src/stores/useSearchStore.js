import { computed, ref } from "vue";
import { defineStore } from "pinia";

const MAX_SEARCH_HISTORY = 8;

export const useSearchStore = defineStore("search", () => {
  const searchQuery = ref("");
  const searchInput = ref("");
  const searchHistory = ref([]);
  const isSearchHistoryOpen = ref(false);
  // Bumped to ask the toolbar's inline search field to take focus (e.g. the "F"
  // shortcut). The field watches this token rather than App holding a ref down
  // through ImageGrid → Toolbar.
  const searchFocusToken = ref(0);

  const isSearchActive = computed(() => !!searchQuery.value?.trim());

  function requestSearchFocus() {
    searchFocusToken.value++;
  }

  const filteredSearchHistory = computed(() => {
    const needle = (searchInput.value || "").trim().toLowerCase();
    if (!needle) return searchHistory.value;
    return searchHistory.value.filter((item) =>
      item.toLowerCase().startsWith(needle),
    );
  });

  function addToSearchHistory(query) {
    if (!query) return;
    const existingIndex = searchHistory.value.findIndex(
      (item) => item === query,
    );
    if (existingIndex !== -1) {
      searchHistory.value.splice(existingIndex, 1);
    }
    searchHistory.value.unshift(query);
    if (searchHistory.value.length > MAX_SEARCH_HISTORY) {
      searchHistory.value = searchHistory.value.slice(0, MAX_SEARCH_HISTORY);
    }
  }

  function clearSearchHistory() {
    searchHistory.value = [];
    isSearchHistoryOpen.value = false;
  }

  function commitSearch() {
    const nextQuery =
      typeof searchInput.value === "string"
        ? searchInput.value.trim()
        : "";
    if (nextQuery === searchQuery.value) return;
    searchQuery.value = nextQuery;
    addToSearchHistory(nextQuery);
    isSearchHistoryOpen.value = false;
  }

  return {
    searchQuery,
    searchInput,
    searchHistory,
    isSearchHistoryOpen,
    searchFocusToken,
    isSearchActive,
    filteredSearchHistory,
    requestSearchFocus,
    addToSearchHistory,
    clearSearchHistory,
    commitSearch,
  };
});
