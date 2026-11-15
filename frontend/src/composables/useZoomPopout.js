import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";

/**
 * Open a list of pictures in ZoomPopout: which one is zoomed, the wheel that
 * opened it, and stepping through the list.
 *
 * Shared by the Sheets step and the faces dialog, so a wheel up, double-click
 * or Z opens a thumbnail the same way on both.
 *
 * Escape closes the pop-out wherever focus is. The pop-out handles its own
 * keys only while focus is inside it, and the dialog it opens over can keep
 * focus on the thumbnail that opened it; Escape then reached only the
 * dialog, which is persistent while zoomed, and nothing closed.
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

  // A reload can replace the list under an open pop-out (a new strictness in
  // the faces dialog, sheets arriving); close it rather than show a picture
  // that is no longer in the list, with dead prev and next.
  watch(zoomPos, (pos) => {
    if (pos < 0 && zoomedId.value != null) zoomedId.value = null;
  });

  async function closeZoom() {
    const id = zoomedId.value;
    if (id == null) return;
    zoomedId.value = null;
    await nextTick();
    onClosed?.(id);
  }

  function onEscape(event) {
    if (event.key !== "Escape" || zoomedId.value == null) return;
    event.preventDefault();
    event.stopPropagation();
    closeZoom();
  }

  // Capture, so it is heard before a dialog's own document listener.
  watch(zoomedId, (id, before) => {
    if (id != null && before == null) {
      window.addEventListener("keydown", onEscape, true);
    } else if (id == null) {
      window.removeEventListener("keydown", onEscape, true);
    }
  });
  onBeforeUnmount(() => window.removeEventListener("keydown", onEscape, true));

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
