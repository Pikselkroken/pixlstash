import { ref, computed } from "vue";
import { defineStore } from "pinia";

export const useFilterStore = defineStore("filter", () => {
  const mediaTypeFilter = ref("all"); // 'all' | 'images' | 'videos'
  const _minScore = ref(null);
  const _maxScore = ref(null);
  // "Only pictures nobody has rated" (`unscored=1`, i.e. score IS NULL OR 0).
  // It is the complement of a score range rather than a point on it, so the two
  // are mutually exclusive - and that is enforced here, in the setters, so the
  // filter panel and the stats histogram both inherit it without either one
  // having to remember.
  const _unscoredOnly = ref(false);
  const minScoreFilter = computed({
    get: () => _minScore.value,
    set: (v) => {
      _minScore.value = v ?? null;
      if (_minScore.value != null) _unscoredOnly.value = false;
    },
  });
  const maxScoreFilter = computed({
    get: () => _maxScore.value,
    set: (v) => {
      _maxScore.value = v ?? null;
      if (_maxScore.value != null) _unscoredOnly.value = false;
    },
  });
  const unscoredOnlyFilter = computed({
    get: () => _unscoredOnly.value,
    set: (v) => {
      _unscoredOnly.value = Boolean(v);
      if (_unscoredOnly.value) {
        _minScore.value = null;
        _maxScore.value = null;
      }
    },
  });
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
  // Stack state: 'all' | 'stacked' | 'unstacked' | 'unresolved'. Stacked and
  // unstacked are a filter rather than a destination, because neither carries a
  // to-do count. 'unresolved' (a group the duplicate queue has found but nobody
  // has ruled on yet) is still honoured by the store and the API, but the filter
  // panel no longer offers it: the duplicate queue owns that work.
  const stackStateFilter = ref("all");

  function resetFilters() {
    mediaTypeFilter.value = "all";
    _minScore.value = null;
    _maxScore.value = null;
    _unscoredOnly.value = false;
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
    stackStateFilter.value = "all";
  }

  const isActive = computed(
    () =>
      mediaTypeFilter.value !== "all" ||
      minScoreFilter.value != null ||
      maxScoreFilter.value != null ||
      unscoredOnlyFilter.value ||
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
      unassignedOnlyFilter.value ||
      stackStateFilter.value !== "all",
  );

  const activeCount = computed(() => {
    let count = 0;
    if (mediaTypeFilter.value !== "all") count++;
    if (minScoreFilter.value != null) count++;
    if (maxScoreFilter.value != null) count++;
    if (unscoredOnlyFilter.value) count++;
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
    if (stackStateFilter.value !== "all") count++;
    return count;
  });

  return {
    mediaTypeFilter,
    minScoreFilter,
    maxScoreFilter,
    unscoredOnlyFilter,
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
    stackStateFilter,
    resetFilters,
    isActive,
    activeCount,
  };
});
