import { nextTick, watch } from "vue";
import { useUserPrefsStore } from "../stores/useUserPrefsStore";
import { useSelectionStore } from "../stores/useSelectionStore";
import { useSearchStore } from "../stores/useSearchStore";
import { useSortStore } from "../stores/useSortStore";
import { useGridStore } from "../stores/useGridStore";
import { useExportStore } from "../stores/useExportStore";
import { useFilterStore } from "../stores/useFilterStore";
import { useWsStore } from "../stores/useWsStore";
import {
  ALL_PICTURES_ID,
  SCRAPHEAP_PICTURES_ID,
  UNASSIGNED_PICTURES_ID,
} from "../stores/useViewStore";

/**
 * What happens after a picture-level action that changes more than the grid:
 * assigning images to a person, moving them between folders, confirming an
 * export, or clearing the search back to All Pictures.
 *
 * Each of these has to reconcile two surfaces - the grid and the sidebar's
 * counts - which is why they sit together rather than in the components that
 * trigger them.
 *
 * @param {object} deps
 * @param {import("vue").Ref} deps.gridContainer
 * @param {Function} deps.refreshSidebar
 * @param {Function} deps.onTagFilterChanged - debounced sidebar refresh for a
 *   tag-visibility change.
 * @param {Function} deps.onNavigated
 */
export function useAppEntityActions({
  gridContainer,
  refreshSidebar,
  onNavigated,
  onTagFilterChanged,
}) {
  const selectionStore = useSelectionStore();
  const searchStore = useSearchStore();
  const sortStore = useSortStore();
  const gridStore = useGridStore();
  const exportStore = useExportStore();
  const userPrefsStore = useUserPrefsStore();
  const filterStore = useFilterStore();
  const wsStore = useWsStore();

  async function handleImagesAssignedToCharacter({ characterId, imageIds }) {
    const current = selectionStore.selectedCharacter;
    // Unassigned view: assigned pictures leave the unassigned bucket - drop their
    // tiles from the grid immediately.
    if (current === UNASSIGNED_PICTURES_ID && !selectionStore.selectedSet) {
      if (
        gridContainer.value &&
        typeof gridContainer.value.removeImagesById === "function"
      ) {
        gridContainer.value.removeImagesById(imageIds);
      }
      return;
    }
    // Viewing a specific character: reassigning pictures (and their whole stack)
    // to a DIFFERENT character moves them out of this view. Refetch so they
    // disappear right away instead of lingering until the view changes - a plain
    // removeImagesById can't catch every stack member (a collapsed drag only
    // carries the leader id).
    const isSpecificCharacterView =
      current != null &&
      !selectionStore.selectedSet &&
      String(current) !== String(ALL_PICTURES_ID) &&
      String(current) !== String(UNASSIGNED_PICTURES_ID) &&
      String(current) !== String(SCRAPHEAP_PICTURES_ID);
    if (isSpecificCharacterView && String(current) !== String(characterId)) {
      gridStore.refreshGridVersion();
    }
  }

  function handleImagesMoved({ imageIds, kind, refresh }) {
    if (kind === "reference-folder" || refresh) {
      wsStore.clearSortChangedExternalIds();
      gridStore.refreshGridVersion();
      refreshSidebar();
      return;
    }
    if (
      selectionStore.selectedCharacter !== UNASSIGNED_PICTURES_ID ||
      selectionStore.selectedSet
    ) {
      return;
    }
    if (
      gridContainer.value &&
      typeof gridContainer.value.removeImagesById === "function"
    ) {
      gridContainer.value.removeImagesById(imageIds);
    }
  }

  function handleFacesAssignedToCharacter() {
    if (
      gridContainer.value &&
      typeof gridContainer.value.clearFaceSelection === "function"
    ) {
      gridContainer.value.clearFaceSelection();
    }
  }

  function refreshExportCount() {
    const counts = gridContainer.value?.getExportCount?.();
    if (!counts) return;
    exportStore.exportSelectedCount = Number(counts.selectedCount) || 0;
    exportStore.exportTotalCount = Number(counts.totalCount) || 0;
  }

  function confirmExportZip() {
    gridContainer.value?.exportCurrentViewToZip({
      exportType: exportStore.exportType,
      captionMode: exportStore.exportCaptionMode,
      tagFormat: exportStore.exportTagFormat,
      includeCharacterName: exportStore.exportIncludeCharacterName,
      useOriginalFileNames: exportStore.exportUseOriginalFileNames,
      resolution: exportStore.exportResolution,
      bboxMode: exportStore.exportBboxMode,
    });
    exportStore.exportMenuOpen = false;
  }

  function confirmExportFolder(destination) {
    gridContainer.value?.exportCurrentViewToFolder({
      destination,
      exportType: exportStore.exportType,
      captionMode: exportStore.exportCaptionMode,
      tagFormat: exportStore.exportTagFormat,
      includeCharacterName: exportStore.exportIncludeCharacterName,
      useOriginalFileNames: exportStore.exportUseOriginalFileNames,
      resolution: exportStore.exportResolution,
      bboxMode: exportStore.exportBboxMode,
    });
    exportStore.exportMenuOpen = false;
  }

  // --- Review tags overlay ---
  // Visibility lives in the store so the grid toolbar can open it directly.

  function handleClearSearch() {
    searchStore.searchQuery = "";
    searchStore.searchInput = "";
    searchStore.isSearchHistoryOpen = false;
    gridStore.refreshGridVersion();
  }

  function handleResetToAll() {
    selectionStore.selectedCharacter = ALL_PICTURES_ID;
    selectionStore.selectedSet = null;
    selectionStore.selectedSetIds = [];
    selectionStore.lastSelectedCharacterLabel = "All Pictures";
    sortStore.selectedSort = "DATE";
    sortStore.selectedDescending = true;
    sortStore.selectedSimilarityCharacter = null;
    searchStore.searchQuery = "";
    filterStore.resetFilters();
    gridStore.refreshGridVersion();
    onNavigated?.();
  }

  // --- Watchers ---

  // Opening the export menu is when its count has to be right; computing it
  // eagerly would cost a request per selection change.
  watch(
    () => exportStore.exportMenuOpen,
    async (isOpen) => {
      if (!isOpen) return;
      await nextTick();
      refreshExportCount();
    },
  );

  // Hiding tags, or turning the tag filter on, changes which pictures the grid
  // and the sidebar counts should show.
  watch(
    () => userPrefsStore.hiddenTags,
    () => {
      gridStore.refreshGridVersion();
      if (userPrefsStore.applyTagFilter) onTagFilterChanged?.();
    },
  );

  watch(
    () => userPrefsStore.applyTagFilter,
    () => {
      gridStore.refreshGridVersion();
      onTagFilterChanged?.();
    },
  );

  return {
    handleImagesAssignedToCharacter,
    handleImagesMoved,
    handleFacesAssignedToCharacter,
    refreshExportCount,
    confirmExportZip,
    confirmExportFolder,
    handleClearSearch,
    handleResetToAll,
  };
}
