import { ref } from "vue";
import { defineStore } from "pinia";

export const useSortStore = defineStore("sort", () => {
  const selectedSort = ref("");
  const selectedDescending = ref(true);
  const sortOptions = ref([]);
  const stackThreshold = ref(null);
  const selectedSimilarityCharacter = ref(null);
  const similarityCharacterOptions = ref([]);

  return {
    selectedSort,
    selectedDescending,
    sortOptions,
    stackThreshold,
    selectedSimilarityCharacter,
    similarityCharacterOptions,
  };
});
