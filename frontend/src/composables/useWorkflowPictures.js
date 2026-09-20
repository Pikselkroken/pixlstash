import { useRouter } from "vue-router";

import { useFilterStore } from "../stores/useFilterStore";

/**
 * *Show all N pictures*: open the library on one workflow card's pictures.
 *
 * The figure the Workflow tab's header and a card's ⓘ already show becomes the
 * link (v1.12 F7). It lands on All Pictures with #1387's filter strip carrying
 * a removable **Workflow** chip, rather than on a screen of its own: the
 * pictures of one workflow are pictures, and every sort, filter and gesture
 * the grid has should keep working on them.
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

  /** @param {{key: string, name: string}} card */
  function showPictures(card) {
    if (!card?.key) return;
    filterStore.workflowFilter = { key: card.key, name: card.name };
    router.push("/");
  }

  return { showPictures };
}
