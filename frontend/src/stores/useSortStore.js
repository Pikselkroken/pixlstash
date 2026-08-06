import { ref } from "vue";
import { defineStore } from "pinia";

export const useSortStore = defineStore("sort", () => {
  const selectedSort = ref("");
  const selectedDescending = ref(true);
  const sortOptions = ref([]);
  const stackThreshold = ref(null);
  const selectedSimilarityCharacter = ref(null);
  const similarityCharacterOptions = ref([]);

  function setSortOptions(options) {
    sortOptions.value = Array.isArray(options) ? options : [];
  }

  function setSimilarityCharacterOptions(options) {
    similarityCharacterOptions.value = Array.isArray(options) ? options : [];
  }

  return {
    setSortOptions,
    setSimilarityCharacterOptions,
    selectedSort,
    selectedDescending,
    sortOptions,
    stackThreshold,
    selectedSimilarityCharacter,
    similarityCharacterOptions,
  };
});
