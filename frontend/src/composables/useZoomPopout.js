import { computed, nextTick, ref } from "vue";

/**
 * Open a list of pictures in ZoomPopout: which one is zoomed, the wheel that
 * opened it, and stepping through the list.
 *
 * Shared by the Sheets step and the faces dialog, so a wheel up, double-click
 * or Z opens a thumbnail the same way on both.
 *
 * @param {import("vue").Ref<Array<number>>} list - the ids in stepping order.
 * @param {(id: number) => void} [onClosed] - after closing, with the id that was
 *   zoomed, to put focus back on its thumbnail.
 */
export function useZoomPopout(list, onClosed) {
  const zoomedId = ref(null);
  const zoomEntry = ref(null);
  const zoomPos = computed(() => list.value.indexOf(zoomedId.value));
  const hasPrev = computed(() => zoomPos.value > 0);
  const hasNext = computed(
    () => zoomPos.value >= 0 && zoomPos.value < list.value.length - 1,
  );

  function openZoom(id, entry = null) {
    zoomEntry.value = entry;
    zoomedId.value = id;
  }

  /** Wheel up over a thumbnail opens it zoomed, at the point under the cursor;
   * the same gesture goes on zooming in the pop-out. Wheel down still scrolls. */
  function onTileWheel(event, id) {
    if (event.deltaY >= 0) return;
    event.preventDefault();
    const r = event.currentTarget.getBoundingClientRect();
    openZoom(id, {
      deltaY: event.deltaY,
      deltaMode: event.deltaMode,
      fx: (event.clientX - r.left) / r.width,
      fy: (event.clientY - r.top) / r.height,
    });
  }

  function stepZoom(d) {
    const id = list.value[zoomPos.value + d];
    if (id == null) return;
    zoomEntry.value = null;
    zoomedId.value = id;
  }

  async function closeZoom() {
    const id = zoomedId.value;
    zoomedId.value = null;
    await nextTick();
    onClosed?.(id);
  }

  return {
    zoomedId,
    zoomEntry,
    zoomPos,
    hasPrev,
    hasNext,
    openZoom,
    onTileWheel,
    stepZoom,
    closeZoom,
  };
}
