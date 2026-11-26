// useStudioStore.js - what the Studio lists (#2819), in the project scope the
// app is in. The scope itself is the project store's, so the Studio and Media
// share one choice; this store only holds the overview read for it.

import { computed, onScopeDispose, ref } from "vue";
import { defineStore } from "pinia";
import { getStudioOverview } from "../api/studio";
import { onSessionReset } from "../utils/apiClient";
import { errorMessage } from "../utils/apiError";
import { useProjectStore } from "./useProjectStore";

const EMPTY = { scenes: [], cast: [], outfits: [], locations: [], props: [] };

export const useStudioStore = defineStore("studio", () => {
  const projectStore = useProjectStore();
  const overview = ref(EMPTY);
  const loaded = ref(false);
  const error = ref("");
  let epoch = 0;

  /** `any`, `none` or a project id: the overview's `project`. */
  const scope = computed(() =>
    projectStore.projectViewMode !== "project"
      ? "any"
      : (projectStore.selectedProjectId ?? "none"),
  );

  /** Pick the scope: the same choice Media's selector makes. */
  function setScope(next) {
    if (next === "any") {
      projectStore.projectViewMode = "global";
      projectStore.selectedProjectId = null;
    } else {
      projectStore.projectViewMode = "project";
      projectStore.selectedProjectId = next === "none" ? null : next;
    }
  }

  async function refresh() {
    const asked = (epoch += 1);
    const of = scope.value;
    try {
      const read = await getStudioOverview(of);
      if (asked !== epoch) return;
      overview.value = { ...EMPTY, ...read };
      loaded.value = true;
      error.value = "";
    } catch (e) {
      if (asked !== epoch) return;
      console.warn("Couldn't read the Studio", of, e);
      error.value = `Couldn't read the Studio. ${errorMessage(e, "Please try again.")}`;
    }
  }

  /** Every item by `kind:id`, for the favourites and the lightboxes. */
  const byKey = computed(() => {
    const map = new Map();
    const add = (kind, list) => {
      for (const item of list) map.set(`${kind}:${item.id}`, { ...item, kind });
    };
    add("scene", overview.value.scenes);
    add("cast", overview.value.cast);
    add("outfit", overview.value.outfits);
    add("location", overview.value.locations);
    add("prop", overview.value.props);
    return map;
  });

  function reset() {
    epoch += 1;
    overview.value = EMPTY;
    loaded.value = false;
    error.value = "";
  }

  const unsubscribe = onSessionReset(reset);
  onScopeDispose(() => unsubscribe());

  return { overview, loaded, error, scope, setScope, refresh, byKey, reset };
});
