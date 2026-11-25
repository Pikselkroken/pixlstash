import { ref, onScopeDispose, watch } from "vue";
import { defineStore } from "pinia";

import { onSessionReset } from "../utils/apiClient";
import { COLOUR_AMOUNT_DEFAULT, colourEntry } from "../utils/colourSwatches";
import { useSelectionStore } from "./useSelectionStore";

export const useFilterStore = defineStore("filter", () => {
  const mediaTypeFilter = ref("all"); // 'all' | 'images' | 'videos'
  const minScoreFilter = ref(null);
  const maxScoreFilter = ref(null);
  // Pictures nobody has rated (`unscored=1`, i.e. score IS NULL OR 0). Alone it
  // is the unrated only; beside a score range it adds the unrated to that range
  // (the filter menu's Score sets it for a range from 0 stars), which the
  // backend ORs.
  const unscoredOnlyFilter = ref(false);
  // "Favourites only" (`favourite=1`): the owner's hearted pictures, in
  // whatever the grid is showing.
  const favouriteOnlyFilter = ref(false);
  const smartScoreBucketFilter = ref(null);
  const resolutionBucketFilter = ref(null);
  const tagFilter = ref([]);
  const tagRejectedFilter = ref([]);
  const tagConfidenceAboveFilter = ref([]);
  const tagConfidenceBelowFilter = ref([]);
  // Search by colour (#2151): the picked swatches' ids, and the one
  // "contains at least N%" every one of them shares. A picture must hold each
  // picked swatch at that amount (`colourFilterParams`).
  const colourFilter = ref([]);
  const colourAmountFilter = ref(COLOUR_AMOUNT_DEFAULT);
  const faceBboxFilter = ref(null);
  const sharedOnlyFilter = ref(false);
  // "Imported from": an import folder's path, or null for any picture.
  const importedFromFilter = ref(null);
  // "Problems" in All Pictures: no named person, and in no set. Independent;
  // both on is the old combined "unassigned" view.
  const noCharacterFilter = ref(false);
  const noSetFilter = ref(false);
  const comfyuiModelFilter = ref([]);
  const comfyuiLoraFilter = ref([]);
  const comfyuiConfigured = ref(false);
  // Whether the config has been read, so `comfyuiConfigured` false means none
  // is saved rather than not known yet (the Workflows sidebar's Set up row).
  const comfyuiConfigRead = ref(false);
  // The address itself, for the one control that sends the browser there
  // (the Workflow tab's Open in ComfyUI). Empty when none is configured.
  const comfyuiUrl = ref("");
  // The owner's ComfyUI is not the next session's: a share token cannot read
  // the config that would overwrite it.
  onScopeDispose(
    onSessionReset(() => {
      comfyuiUrl.value = "";
      comfyuiConfigured.value = false;
      comfyuiConfigRead.value = false;
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
  // "Likeness" (one person's view): the cluster view's "Cluster N" at a
  // strictness, `{cluster, threshold, label}` (`face_cluster` /
  // `face_cluster_threshold`), or null. The numbers are that person's, so
  // leaving the person drops it.
  const faceClusterFilter = ref(null);
  const selection = useSelectionStore();
  watch(
    // By value ("7" and 7 are one person), and a multi-person selection is
    // no one person's view.
    () =>
      (selection.selectedCharacterIds || []).length > 1
        ? "many"
        : String(selection.selectedCharacter ?? ""),
    () => {
      faceClusterFilter.value = null;
    },
    // At the change itself, so a pick made right after it survives.
    { flush: "sync" },
  );

  function resetFilters() {
    mediaTypeFilter.value = "all";
    minScoreFilter.value = null;
    maxScoreFilter.value = null;
    unscoredOnlyFilter.value = false;
    favouriteOnlyFilter.value = false;
    smartScoreBucketFilter.value = null;
    resolutionBucketFilter.value = null;
    tagFilter.value = [];
    tagRejectedFilter.value = [];
    tagConfidenceAboveFilter.value = [];
    tagConfidenceBelowFilter.value = [];
    colourFilter.value = [];
    colourAmountFilter.value = COLOUR_AMOUNT_DEFAULT;
    faceBboxFilter.value = null;
    sharedOnlyFilter.value = false;
    importedFromFilter.value = null;
    noCharacterFilter.value = false;
    noSetFilter.value = false;
    comfyuiModelFilter.value = [];
    comfyuiLoraFilter.value = [];
    impossibleSources.value = [];
    stackStateFilter.value = "all";
    workflowFilter.value = [];
    faceClusterFilter.value = null;
  }

  return {
    mediaTypeFilter,
    minScoreFilter,
    maxScoreFilter,
    unscoredOnlyFilter,
    favouriteOnlyFilter,
    smartScoreBucketFilter,
    resolutionBucketFilter,
    tagFilter,
    tagRejectedFilter,
    tagConfidenceAboveFilter,
    tagConfidenceBelowFilter,
    colourFilter,
    colourAmountFilter,
    faceBboxFilter,
    sharedOnlyFilter,
    importedFromFilter,
    noCharacterFilter,
    noSetFilter,
    comfyuiModelFilter,
    comfyuiLoraFilter,
    comfyuiConfigured,
    comfyuiConfigRead,
    comfyuiUrl,
    impossibleSources,
    stackStateFilter,
    workflowFilter,
    faceClusterFilter,
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

/**
 * The listing params the colour filter sends: one `colour=<id>:<percent>` per
 * picked swatch, each at the shared amount.
 *
 * @param {{colourFilter?: string[], colourAmountFilter?: number}} store
 * @returns {Array<[string, string]>}
 */
export function colourFilterParams(store) {
  return (store.colourFilter || []).map((id) => [
    "colour",
    colourEntry(id, store.colourAmountFilter),
  ]);
}
