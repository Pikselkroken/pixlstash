import { ref, computed } from "vue";
import { defineStore } from "pinia";

export const useFilterStore = defineStore("filter", () => {
  const mediaTypeFilter = ref("all"); // 'all' | 'images' | 'videos'
  const minScoreFilter = ref(null);
  const maxScoreFilter = ref(null);
  const smartScoreBucketFilter = ref(null);
  const resolutionBucketFilter = ref(null);
  const tagFilter = ref([]);
  const tagRejectedFilter = ref([]);
  const tagConfidenceAboveFilter = ref([]);
  const tagConfidenceBelowFilter = ref([]);
  const faceBboxFilter = ref(null);
  const sharedOnlyFilter = ref(false);
  const unassignedOnlyFilter = ref(false);
  const comfyuiModelFilter = ref([]);
  const comfyuiLoraFilter = ref([]);
  const comfyuiConfigured = ref(false);
  // Impossible-tag grid filter: array of source keys ("no_face" / "no_humans"),
  // OR'd together. Empty array means the filter is off.
  const impossibleSources = ref([]);

  function resetFilters() {
    mediaTypeFilter.value = "all";
    minScoreFilter.value = null;
    maxScoreFilter.value = null;
    smartScoreBucketFilter.value = null;
    resolutionBucketFilter.value = null;
    tagFilter.value = [];
    tagRejectedFilter.value = [];
    tagConfidenceAboveFilter.value = [];
    tagConfidenceBelowFilter.value = [];
    faceBboxFilter.value = null;
    sharedOnlyFilter.value = false;
    unassignedOnlyFilter.value = false;
    comfyuiModelFilter.value = [];
    comfyuiLoraFilter.value = [];
    impossibleSources.value = [];
  }

  const isActive = computed(
    () =>
      mediaTypeFilter.value !== "all" ||
      minScoreFilter.value != null ||
      maxScoreFilter.value != null ||
      smartScoreBucketFilter.value != null ||
      resolutionBucketFilter.value != null ||
      (Array.isArray(tagFilter.value) && tagFilter.value.length > 0) ||
      (Array.isArray(tagRejectedFilter.value) &&
        tagRejectedFilter.value.length > 0) ||
      (Array.isArray(tagConfidenceAboveFilter.value) &&
        tagConfidenceAboveFilter.value.length > 0) ||
      (Array.isArray(tagConfidenceBelowFilter.value) &&
        tagConfidenceBelowFilter.value.length > 0) ||
      (Array.isArray(comfyuiModelFilter.value) &&
        comfyuiModelFilter.value.length > 0) ||
      (Array.isArray(comfyuiLoraFilter.value) &&
        comfyuiLoraFilter.value.length > 0) ||
      (Array.isArray(impossibleSources.value) &&
        impossibleSources.value.length > 0) ||
      faceBboxFilter.value != null ||
      sharedOnlyFilter.value ||
      unassignedOnlyFilter.value,
  );

  const activeCount = computed(() => {
    let count = 0;
    if (mediaTypeFilter.value !== "all") count++;
    if (minScoreFilter.value != null) count++;
    if (maxScoreFilter.value != null) count++;
    if (smartScoreBucketFilter.value != null) count++;
    if (resolutionBucketFilter.value != null) count++;
    if (Array.isArray(tagFilter.value)) count += tagFilter.value.length;
    if (Array.isArray(tagRejectedFilter.value))
      count += tagRejectedFilter.value.length;
    if (Array.isArray(tagConfidenceAboveFilter.value))
      count += tagConfidenceAboveFilter.value.length;
    if (Array.isArray(tagConfidenceBelowFilter.value))
      count += tagConfidenceBelowFilter.value.length;
    if (Array.isArray(comfyuiModelFilter.value))
      count += comfyuiModelFilter.value.length;
    if (Array.isArray(comfyuiLoraFilter.value))
      count += comfyuiLoraFilter.value.length;
    if (Array.isArray(impossibleSources.value))
      count += impossibleSources.value.length;
    if (faceBboxFilter.value != null) count++;
    if (sharedOnlyFilter.value) count++;
    if (unassignedOnlyFilter.value) count++;
    return count;
  });

  return {
    mediaTypeFilter,
    minScoreFilter,
    maxScoreFilter,
    smartScoreBucketFilter,
    resolutionBucketFilter,
    tagFilter,
    tagRejectedFilter,
    tagConfidenceAboveFilter,
    tagConfidenceBelowFilter,
    faceBboxFilter,
    sharedOnlyFilter,
    unassignedOnlyFilter,
    comfyuiModelFilter,
    comfyuiLoraFilter,
    comfyuiConfigured,
    impossibleSources,
    resetFilters,
    isActive,
    activeCount,
  };
});
