import { nextTick } from "vue";
import { patchUserConfig } from "../api/config";
import { useGridStore } from "../stores/useGridStore";
import { useSortStore } from "../stores/useSortStore";
import { useProjectStore } from "../stores/useProjectStore";
import { useSidebarStore } from "../stores/useSidebarStore";
import { useUserPrefsStore } from "../stores/useUserPrefsStore";

/**
 * The handlers behind the sidebar's and settings dialog's controls.
 *
 * They are deliberately thin: each one validates its input and writes the
 * store, and the config composable's watchers do the persisting. Nothing here
 * pushes a route - the project-mode and project-picker handlers in particular
 * only mirror their value into the store, because switching the sidebar's
 * scope must not move the grid (the route is what does that).
 *
 * @param {object} deps
 * @param {import("vue").Ref} deps.gridContainer - the grid, for the few
 *   actions it exposes imperatively.
 * @param {import("vue").Ref} deps.statsSidebarRef - the stats panel, for the
 *   Tasks-tab deep link.
 * @param {Function} deps.onNavigated - close the mobile sidebar after an
 *   action that moves the view.
 * @param {Function} deps.pushAppRoute - navigate (viewing a project is the
 *   one control here that does move the grid).
 */
export function useAppSettingsHandlers({
  gridContainer,
  statsSidebarRef,
  onNavigated,
  pushAppRoute,
}) {
  const gridStore = useGridStore();
  const sortStore = useSortStore();
  const projectStore = useProjectStore();
  const sidebarStore = useSidebarStore();
  const userPrefsStore = useUserPrefsStore();

  function handleUpdateProjectViewMode(mode) {
    projectStore.projectViewMode = mode;
  }

  function handleUpdateSelectedProjectId(id) {
    projectStore.selectedProjectId = id;
  }

  // Explicit "view this project" entry click → navigate. useViewStore (watching
  // the route) sets projectViewMode/selectedProjectId from the URL, which scopes
  // the grid to the project.
  function handleViewProject(id) {
    if (id == null) return;
    pushAppRoute({ name: "project", params: { id: String(id) } });
  }

  async function handleUpdateSelectedSort({ sort, descending }) {
    sortStore.selectedSort = sort;
    sortStore.selectedDescending = descending;
    onNavigated?.();
  }

  function handleUpdateSortOptions(options) {
    sortStore.sortOptions = Array.isArray(options) ? options : [];
  }

  function handleStackStatsUpdate(payload) {
    const expanded = Number(payload?.expanded ?? 0);
    const total = Number(payload?.total ?? 0);
    gridStore.expandedStackCount = Number.isFinite(expanded)
      ? Math.max(0, expanded)
      : 0;
    gridStore.totalStackCount = Number.isFinite(total) ? Math.max(0, total) : 0;
  }

  function handleUpdateSimilarityCharacter(val) {
    sortStore.selectedSimilarityCharacter = val;
    gridStore.refreshGridVersion();
    onNavigated?.();
  }

  function handleUpdateSimilarityOptions(options) {
    sortStore.similarityCharacterOptions = Array.isArray(options)
      ? options
      : [];
  }

  function handleUpdateHiddenTags(tags) {
    const nextTags = Array.isArray(tags) ? tags : [];
    if (
      userPrefsStore.hiddenTags.length === nextTags.length &&
      userPrefsStore.hiddenTags.every((tag, index) => tag === nextTags[index])
    ) {
      return;
    }
    userPrefsStore.hiddenTags = nextTags;
  }

  function handleUpdateApplyTagFilter(value) {
    const nextValue = Boolean(value);
    if (userPrefsStore.applyTagFilter === nextValue) return;
    userPrefsStore.applyTagFilter = nextValue;
  }

  function handleUpdateDateFormat(value) {
    if (value == null) return;
    const nextValue = String(value);
    if (nextValue === userPrefsStore.dateFormat) return;
    userPrefsStore.dateFormat = nextValue;
  }

  function handleUpdateThemeMode(value) {
    if (value == null) return;
    userPrefsStore.themeMode = String(value);
  }

  async function handleUpdateCheckForUpdates(value) {
    userPrefsStore.checkForUpdates = value;
    try {
      await patchUserConfig({ check_for_updates: value });
    } catch (e) {
      console.error("Failed to save check_for_updates preference:", e);
    }
  }

  function handleUpdateSidebarThumbnailSize(value) {
    const nextValue = Number(value);
    if (!Number.isFinite(nextValue)) return;
    userPrefsStore.sidebarThumbnailSize = nextValue;
  }

  // The sidebar's Scrapheap context menu asks to empty the heap. The sidebar has
  // already switched the view to the scrapheap; defer to the next tick so the grid
  // is showing that view before we open its existing consent-gated empty-forever
  // confirm (whose post-confirm refetch then reconciles the right view). The grid
  // is still fetching that view at this point, by construction — the confirm is
  // deliberately not gated on that, and takes its counts from the server preview.
  function handleEmptyScrapheapFromSidebar() {
    nextTick(() => gridContainer.value?.confirmEmptyScrapheap?.());
  }

  // The sidebar's person context menu asks for more pictures of that person
  // (#636). The search runs across the whole library rather than inside the
  // current view, so nothing here changes the selection or the route: the grid
  // owns the search mode and shows its own result bar.
  function handleSuggestPicturesForCharacter(character) {
    if (character?.id == null) return;
    nextTick(() =>
      gridContainer.value?.suggestPicturesForCharacter?.({
        id: character.id,
        name: character.name,
      }),
    );
  }

  // Open the stats sidebar and focus its Tasks tab. Shared by the thumbnail-mode
  // "View progress" notice action and the ThumbnailUpgradeBanner's link, so both
  // use the same statsSidebarRef.focusTasksTab() plumbing.
  function focusTasksTabPanel() {
    sidebarStore.statsOpen = true;
    nextTick(() => statsSidebarRef.value?.focusTasksTab?.());
  }

  function handleUpdateThumbnailMode(value) {
    if (value !== "square" && value !== "justified") return;
    if (value === gridStore.thumbnailMode) return;
    // Apply immediately with no regeneration: both modes render from the same
    // stored bitmap (justified shows it whole; square crops it to the stored
    // rectangle), so the grid re-lays-out at once. The persist watch saves it,
    // and the radiogroup's aria-checked change is what a screen reader announces.
    gridStore.thumbnailMode = value;
  }

  function handleUpdateSidebarWidth(value) {
    const nextValue = Number(value);
    if (!Number.isFinite(nextValue)) return;
    userPrefsStore.sidebarWidth = nextValue;
  }

  return {
    handleUpdateProjectViewMode,
    handleUpdateSelectedProjectId,
    handleViewProject,
    handleUpdateSelectedSort,
    handleUpdateSortOptions,
    handleStackStatsUpdate,
    handleUpdateSimilarityCharacter,
    handleUpdateSimilarityOptions,
    handleUpdateHiddenTags,
    handleUpdateApplyTagFilter,
    handleUpdateDateFormat,
    handleUpdateThemeMode,
    handleUpdateCheckForUpdates,
    handleUpdateSidebarThumbnailSize,
    handleEmptyScrapheapFromSidebar,
    handleSuggestPicturesForCharacter,
    focusTasksTabPanel,
    handleUpdateThumbnailMode,
    handleUpdateSidebarWidth,
  };
}
