import { ref, onScopeDispose } from "vue";
import { defineStore } from "pinia";

import { onSessionReset } from "../utils/apiClient";

export const useFilterStore = defineStore("filter", () => {
  const mediaTypeFilter = ref("all"); // 'all' | 'images' | 'videos'
  const minScoreFilter = ref(null);
  const maxScoreFilter = ref(null);
  // Pictures nobody has rated (`unscored=1`, i.e. score IS NULL OR 0). Alone it
  // is the unrated only; beside a score range it adds the unrated to that range
  // (the filter menu's Score sets it for a range from 0 stars), which the
  // backend ORs.
  const unscoredOnlyFilter = ref(false);
  const smartScoreBucketFilter = ref(null);
  const resolutionBucketFilter = ref(null);
  const tagFilter = ref([]);
  const tagRejectedFilter = ref([]);
  const tagConfidenceAboveFilter = ref([]);
  const tagConfidenceBelowFilter = ref([]);
  const faceBboxFilter = ref(null);
  const sharedOnlyFilter = ref(false);
  // "Problems" in All Pictures: no named person, and in no set. Independent;
  // both on is the old combined "unassigned" view.
  const noCharacterFilter = ref(false);
  const noSetFilter = ref(false);
  const comfyuiModelFilter = ref([]);
  const comfyuiLoraFilter = ref([]);
  const comfyuiConfigured = ref(false);
  // The address itself, for the one control that sends the browser there
  // (the Workflow tab's Open in ComfyUI). Empty when none is configured.
  const comfyuiUrl = ref("");
  // The owner's ComfyUI is not the next session's: a share token cannot read
  // the config that would overwrite it.
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
  // The pictures of any of these workflows (#1797; OR'd, like the checkpoint
  // filter): a list of `{id, name}`, empty for none. The Filters menu's
  // Workflow row ticks them; *Show all N pictures* (v1.12 F7) and the
  // Workflows screen's *Show all pictures* arrive as a list of their own. With
  // exactly one, the Workflow tab's LoRA pile narrows it to one LoRA:
  // `{id, lora, loraName, name}` (`workflowFilterParams`). A default-recipe
  // row's *Show N* adds `opened: {kind, value, label}`, the
  // `comfyuiModelFilter` / `comfyuiLoraFilter` value it set beside it, so the
  // Workflow chip's × clears that too. The names are carried because the chips
  // have to say which, and the grid holds no workflow cards to look them up in.
  const workflowFilter = ref([]);

  function resetFilters() {
    mediaTypeFilter.value = "all";
    minScoreFilter.value = null;
    maxScoreFilter.value = null;
    unscoredOnlyFilter.value = false;
    smartScoreBucketFilter.value = null;
    resolutionBucketFilter.value = null;
    tagFilter.value = [];
    tagRejectedFilter.value = [];
    tagConfidenceAboveFilter.value = [];
    tagConfidenceBelowFilter.value = [];
    faceBboxFilter.value = null;
    sharedOnlyFilter.value = false;
    noCharacterFilter.value = false;
    noSetFilter.value = false;
    comfyuiModelFilter.value = [];
    comfyuiLoraFilter.value = [];
    impossibleSources.value = [];
    stackStateFilter.value = "all";
    workflowFilter.value = [];
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
    noCharacterFilter,
    noSetFilter,
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

/**
 * The listing params a `workflowFilter` sends: one `workflow` per workflow,
 * and `workflow_lora` when exactly one is ticked and it names a LoRA (the
 * listing matches nothing for a LoRA beside several).
 *
 * @param {Array<{id?: string, lora?: string}>} filter
 * @returns {Array<[string, string]>}
 */
export function workflowFilterParams(filter) {
  const list = filter || [];
  const lora = list.length === 1 ? list[0].lora : null;
  return [
    ...list.filter((w) => w.id).map((w) => ["workflow", w.id]),
    ...(lora ? [["workflow_lora", lora]] : []),
  ];
}
