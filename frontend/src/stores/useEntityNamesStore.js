import { ref } from "vue";
import { defineStore } from "pinia";

/**
 * Reactive id → display-name maps for the entities a breadcrumb needs to label.
 *
 * The route is the single source of truth for *what* is being viewed, but it
 * only carries IDs. This store supplies the human names so the ImageGrid
 * breadcrumb can render "Project › Character" instead of "5 › 12". The SideBar
 * — which already fetches these lists — publishes into it after every fetch.
 *
 * Lookups are intentionally **one-directional** (id → name). Names are not
 * unique (two characters can share a name), so the breadcrumb never maps a
 * name back to an id; navigation always uses the IDs already in the route.
 *
 * Setters **merge** rather than replace: the sidebar refetches scoped subsets
 * (e.g. only a project's sets in project view), and replacing would drop names
 * resolved under a different scope. Accumulating id → name is safe because the
 * mapping is stable.
 */
export const useEntityNamesStore = defineStore("entityNames", () => {
  const characterNames = ref({}); // id -> name
  const setNames = ref({}); // id -> name
  const projectNames = ref({}); // id -> name
  const refFolderLabels = ref({}); // id -> label
  const importFolderLabels = ref({}); // id -> label

  function _merge(target, list, nameKey) {
    if (!Array.isArray(list) || list.length === 0) return;
    const next = { ...target.value };
    for (const item of list) {
      if (item == null || item.id == null) continue;
      const name = item[nameKey] ?? item.label ?? item.folder;
      if (name != null) next[item.id] = name;
    }
    target.value = next;
  }

  function mergeCharacterNames(list) {
    _merge(characterNames, list, "name");
  }
  function mergeSetNames(list) {
    _merge(setNames, list, "name");
  }
  function mergeProjectNames(list) {
    _merge(projectNames, list, "name");
  }
  function mergeRefFolderLabels(list) {
    _merge(refFolderLabels, list, "label");
  }
  function mergeImportFolderLabels(list) {
    _merge(importFolderLabels, list, "label");
  }

  return {
    characterNames,
    setNames,
    projectNames,
    refFolderLabels,
    importFolderLabels,
    mergeCharacterNames,
    mergeSetNames,
    mergeProjectNames,
    mergeRefFolderLabels,
    mergeImportFolderLabels,
  };
});
