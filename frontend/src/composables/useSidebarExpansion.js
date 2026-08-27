import { ref, watch } from "vue";

// Which sidebar sections are open is a per-browser view preference, not user
// data, so it stays on the client: one JSON blob in localStorage, one read on
// mount, one write per change. A single key (rather than one per section)
// keeps hydration to a single parse and lets the shape grow without minting
// new keys; `v` guards against reading a shape an older build wrote.

const STORAGE_KEY = "pixlstash:sidebar:expansion";
const SCHEMA_VERSION = 1;

// The folder tree is unbounded (any browsable path can be expanded), so cap
// what we carry between sessions. The oldest keys are dropped first; losing
// one only means a folder starts collapsed and one click reopens it.
const MAX_FOLDER_KEYS = 200;
const MAX_PROJECT_IDS = 500;

/**
 * Warn at most once per sidebar - a browser that refuses localStorage (private
 * mode, disabled storage, quota) would otherwise log on every single toggle.
 */
function makeStorageWarner() {
  let warned = false;
  return (action, error) => {
    if (warned) return;
    warned = true;
    console.warn(
      `Sidebar expansion state could not be ${action} (localStorage unavailable). ` +
        `Sections use their defaults and the choice won't survive a reload.`,
      error,
    );
  };
}

function loadState(warnStorage) {
  let raw;
  try {
    raw = window.localStorage?.getItem(STORAGE_KEY);
  } catch (e) {
    warnStorage("read", e);
    return {};
  }
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return {};
    // A blob from a different schema is discarded rather than half-applied.
    if (parsed.v !== SCHEMA_VERSION) return {};
    return parsed;
  } catch (e) {
    console.warn(
      "Sidebar expansion state in localStorage is not valid JSON; falling back to defaults.",
      e,
    );
    return {};
  }
}

function saveState(snapshot, warnStorage) {
  try {
    window.localStorage?.setItem(STORAGE_KEY, JSON.stringify(snapshot));
  } catch (e) {
    warnStorage("saved", e);
  }
}

function readBool(value, fallback) {
  return typeof value === "boolean" ? value : fallback;
}

/**
 * Rebuild a key Set from a persisted array, dropping anything that isn't a
 * usable key. Keys are numeric ids or path strings, so both types survive.
 */
function readKeySet(value, limit) {
  if (!Array.isArray(value)) return new Set();
  const keys = value.filter(
    (key) =>
      (typeof key === "number" && Number.isFinite(key)) ||
      (typeof key === "string" && key !== ""),
  );
  return new Set(keys.slice(-limit));
}

/** Toggle `key` in a Set ref, replacing the Set so watchers/templates react. */
function toggleKey(setRef, key) {
  const next = new Set(setRef.value);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  setRef.value = next;
}

/**
 * Expansion state for the sidebar's sections, folder tree and project tree,
 * hydrated from and persisted to localStorage.
 *
 * Sections and the project tree default to expanded, so what is stored for
 * those is the *collapsed* set: a project created after this preference was
 * written still opens by default. The folder tree defaults to collapsed, so
 * there the expanded keys are stored.
 */
export function useSidebarExpansion() {
  const warnStorage = makeStorageWarner();
  const stored = loadState(warnStorage);

  // --- Library sections -----------------------------------------------
  const peopleSectionCollapsed = ref(readBool(stored.peopleCollapsed, false));
  const setsSectionCollapsed = ref(readBool(stored.setsCollapsed, false));

  // --- Folders tab sections --------------------------------------------
  const referenceFoldersCollapsed = ref(
    readBool(stored.referenceFoldersCollapsed, false),
  );
  const importFoldersCollapsed = ref(
    readBool(stored.importFoldersCollapsed, false),
  );

  // --- Project tree -----------------------------------------------------
  // `expandedProjectIds` is derived state: it is filled in by
  // `syncProjectExpansion` as projects arrive, from the persisted collapsed set.
  const expandedProjectIds = ref(new Set());
  const collapsedProjectIds = ref(
    readKeySet(stored.collapsedProjectIds, MAX_PROJECT_IDS),
  );
  const projectTreePeopleCollapsed = ref(
    readKeySet(stored.projectPeopleCollapsed, MAX_PROJECT_IDS),
  );
  const projectTreeSetsCollapsed = ref(
    readKeySet(stored.projectSetsCollapsed, MAX_PROJECT_IDS),
  );

  // --- Folder tree ------------------------------------------------------
  // Mixed keys: a reference-folder id (number) or a subfolder path (string).
  const expandedFolderIds = ref(
    readKeySet(stored.expandedFolderKeys, MAX_FOLDER_KEYS),
  );

  // Projects already reflected in `expandedProjectIds`. Tracked separately from
  // the expanded set so a manual collapse isn't undone by the next fetch.
  const seenProjectIds = new Set();

  function togglePeopleSection() {
    peopleSectionCollapsed.value = !peopleSectionCollapsed.value;
  }

  function toggleSetsSection() {
    setsSectionCollapsed.value = !setsSectionCollapsed.value;
  }

  function toggleReferenceFoldersSection() {
    referenceFoldersCollapsed.value = !referenceFoldersCollapsed.value;
  }

  function toggleImportFoldersSection() {
    importFoldersCollapsed.value = !importFoldersCollapsed.value;
  }

  function toggleProjectExpanded(id) {
    toggleKey(expandedProjectIds, id);
    // Mirror into the persisted set: expanded is the default, so only the
    // negative choice is worth remembering.
    const collapsed = new Set(collapsedProjectIds.value);
    if (expandedProjectIds.value.has(id)) collapsed.delete(id);
    else collapsed.add(id);
    collapsedProjectIds.value = collapsed;
  }

  function toggleProjectTreePeople(id) {
    toggleKey(projectTreePeopleCollapsed, id);
  }

  function toggleProjectTreeSets(id) {
    toggleKey(projectTreeSetsCollapsed, id);
  }

  function toggleFolderExpanded(folderKey) {
    toggleKey(expandedFolderIds, folderKey);
  }

  /** Drop ids for projects that no longer exist, so the blob can't grow forever. */
  function pruneProjectIds(liveIds) {
    const live = new Set(liveIds);
    for (const setRef of [
      collapsedProjectIds,
      projectTreePeopleCollapsed,
      projectTreeSetsCollapsed,
    ]) {
      const kept = [...setRef.value].filter((id) => live.has(id));
      if (kept.length !== setRef.value.size) setRef.value = new Set(kept);
    }
  }

  /**
   * Apply the default-expanded rule to projects seen for the first time, minus
   * the ones the user collapsed in an earlier session, and forget projects that
   * are gone. Call it whenever the project list changes.
   */
  function syncProjectExpansion(ids) {
    const newIds = ids.filter((id) => !seenProjectIds.has(id));
    if (newIds.length > 0) {
      newIds.forEach((id) => seenProjectIds.add(id));
      const toExpand = newIds.filter(
        (id) => !collapsedProjectIds.value.has(id),
      );
      if (toExpand.length > 0) {
        expandedProjectIds.value = new Set([
          ...expandedProjectIds.value,
          ...toExpand,
        ]);
      }
    }
    // An empty list means "not loaded yet" as often as it means "no projects",
    // so never prune on it - that would wipe the restored preference on boot.
    if (ids.length > 0) pruneProjectIds(ids);
  }

  function snapshot() {
    return {
      v: SCHEMA_VERSION,
      peopleCollapsed: peopleSectionCollapsed.value,
      setsCollapsed: setsSectionCollapsed.value,
      referenceFoldersCollapsed: referenceFoldersCollapsed.value,
      importFoldersCollapsed: importFoldersCollapsed.value,
      collapsedProjectIds: [...collapsedProjectIds.value].slice(
        -MAX_PROJECT_IDS,
      ),
      projectPeopleCollapsed: [...projectTreePeopleCollapsed.value].slice(
        -MAX_PROJECT_IDS,
      ),
      projectSetsCollapsed: [...projectTreeSetsCollapsed.value].slice(
        -MAX_PROJECT_IDS,
      ),
      expandedFolderKeys: [...expandedFolderIds.value].slice(-MAX_FOLDER_KEYS),
    };
  }

  // One watcher for the whole blob: every section is toggled straight from the
  // template (`x = !x`), so persisting on change beats wrapping each site.
  watch(
    [
      peopleSectionCollapsed,
      setsSectionCollapsed,
      referenceFoldersCollapsed,
      importFoldersCollapsed,
      collapsedProjectIds,
      projectTreePeopleCollapsed,
      projectTreeSetsCollapsed,
      expandedFolderIds,
    ],
    () => saveState(snapshot(), warnStorage),
  );

  return {
    peopleSectionCollapsed,
    setsSectionCollapsed,
    referenceFoldersCollapsed,
    importFoldersCollapsed,
    expandedProjectIds,
    collapsedProjectIds,
    projectTreePeopleCollapsed,
    projectTreeSetsCollapsed,
    expandedFolderIds,
    togglePeopleSection,
    toggleSetsSection,
    toggleReferenceFoldersSection,
    toggleImportFoldersSection,
    toggleProjectExpanded,
    toggleProjectTreePeople,
    toggleProjectTreeSets,
    toggleFolderExpanded,
    syncProjectExpansion,
  };
}

export const SIDEBAR_EXPANSION_STORAGE_KEY = STORAGE_KEY;
