import { ref, computed, onScopeDispose } from "vue";
import { defineStore } from "pinia";

import { onSessionReset } from "../utils/apiClient";

export const useFilterStore = defineStore("filter", () => {
  const mediaTypeFilter = ref("all"); // 'all' | 'images' | 'videos'
  const _minScore = ref(null);
  const _maxScore = ref(null);
  // Pictures nobody has rated (`unscored=1`, i.e. score IS NULL OR 0). Alone it
  // is the unrated only; beside a score range it adds the unrated to that range
  // (the filter menu's "Include unscored"), which the backend ORs.
  const _unscoredOnly = ref(false);
  const minScoreFilter = computed({
    get: () => _minScore.value,
    set: (v) => {
      _minScore.value = v ?? null;
    },
  });
  const maxScoreFilter = computed({
    get: () => _maxScore.value,
    set: (v) => {
      _maxScore.value = v ?? null;
    },
  });
  const unscoredOnlyFilter = computed({
    get: () => _unscoredOnly.value,
    set: (v) => {
      _unscoredOnly.value = Boolean(v);
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
  // The address itself, for the one control that sends the browser there
  // (the Workflow tab's Open in ComfyUI). Empty when none is configured.
  const comfyuiUrl = ref("");
  // The owner's ComfyUI is not the next session's: a share token cannot read
  // the config that would overwrite it (useWorkflowPullStore does the same).
  onScopeDispose(
    onSessionReset(() => {
      comfyuiUrl.value = "";
      comfyuiConfigured.value = false;
    }),
  );
  // Impossible-tag grid filter: array of source keys ("no_face" / "no_humans"),
  // OR'd together. Empty array means the filter is off.
  const impossibleSources = ref([]);
  // Stack state: 'all' | 'stacked' | 'unstacked' | 'unresolved'. Stacked and
  // unstacked are a filter rather than a destination, because neither carries a
  // to-do count. 'unresolved' (a group the duplicate queue has found but nobody
  // has ruled on yet) is still honoured by the store and the API, but the filter
  // panel no longer offers it: the duplicate queue owns that work.
  const stackStateFilter = ref("all");
  // One workflow card's pictures (v1.12 F7's *Show all N pictures*):
  // `{key, name}`, or null. The name is carried because the chip has to say
  // which workflow, and the grid holds no workflow cards to look it up in.
  const workflowFilter = ref(null);

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
    workflowFilter.value = null;
  }

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
    comfyuiUrl,
    impossibleSources,
    stackStateFilter,
    workflowFilter,
    resetFilters,
  };
});
