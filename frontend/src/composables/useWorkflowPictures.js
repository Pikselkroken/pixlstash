import { useRouter } from "vue-router";

import { useFilterStore } from "../stores/useFilterStore";
import { useGridStore } from "../stores/useGridStore";
import { useSearchStore } from "../stores/useSearchStore";
import { useSelectionStore } from "../stores/useSelectionStore";
import { useSortStore } from "../stores/useSortStore";
import { ALL_PICTURES_ID } from "../stores/useViewStore";

/**
 * *Show all N pictures*: open the library on one workflow card's pictures.
 *
 * The figure the Workflow tab's header and a card's ⓘ already show becomes the
 * link (v1.12 F7). It lands on All Pictures with #1387's filter strip carrying
 * a removable **Workflow** chip, rather than on a screen of its own: the
 * pictures of one workflow are pictures, and every sort, filter and gesture
 * the grid has should keep working on them.
 *
 * **It clears the view it is landing in, because the link says "all".** This
 * is the one place in the app where a control promises a COUNT it was handed
 * from somewhere else — 184, read off the card — and a reader with a tag
 * filter on, or a character selected in the sidebar, would be shown fewer than
 * that with nothing saying why. The alternative was to stop saying "all"; the
 * number is the whole point of the link, so the view gives way instead. The
 * clearing is `useAppEntityActions.handleResetToAll`'s, which is the shipped
 * vocabulary for "show me this set of pictures, unencumbered" — repeated here
 * rather than called because that composable takes App-level deps (the grid
 * container, the sidebar refresh) a panel in the rail does not have. The two
 * surfaces it reconciles look after themselves on this path: the route change
 * remounts `ImageGrid`, and the sidebar counts do not depend on a filter.
 *
 * `workflow_key` and not `workflow_stack`, even for a stack's cover, because
 * the number the reader clicked is that CARD's own picture count — the grid
 * draws a stack as its cover and never sums its members. A stack filter would
 * answer with more pictures than the link said.
 *
 * Here rather than in either component because both of them need it and a
 * second spelling of the chip's shape is a chip the strip cannot remove.
 */
export function useWorkflowPictures() {
  const router = useRouter();
  const filterStore = useFilterStore();
  const selectionStore = useSelectionStore();
  const sortStore = useSortStore();
  const searchStore = useSearchStore();
  const gridStore = useGridStore();

  /** @param {{key: string, name: string}} card */
  function showPictures(card) {
    if (!card?.key) return;
    selectionStore.selectedCharacter = ALL_PICTURES_ID;
    selectionStore.selectedSet = null;
    selectionStore.selectedSetIds = [];
    selectionStore.lastSelectedCharacterLabel = "All Pictures";
    sortStore.selectedSort = "DATE";
    sortStore.selectedDescending = true;
    sortStore.selectedSimilarityCharacter = null;
    searchStore.searchQuery = "";
    // Before the workflow filter, never after: `resetFilters` clears
    // `workflowFilter` too, so the order is what decides whether this
    // navigates to one workflow's pictures or to the whole library.
    filterStore.resetFilters();
    filterStore.workflowFilter = { key: card.key, name: card.name };
    gridStore.refreshGridVersion();
    router.push("/");
  }

  return { showPictures };
}
