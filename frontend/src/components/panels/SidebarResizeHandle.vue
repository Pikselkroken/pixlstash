<script setup>
/**
 * The drag grip on a sidebar's right edge, one for every workspace's sidebar.
 * It resizes its parent: the parent draws at `useSidebarWidth().width` and is
 * `position: relative`. Arrow keys step the width (Shift for a bigger step).
 */
import { onBeforeUnmount, ref } from "vue";
import Tooltip from "../widgets/Tooltip.vue";
import {
  SIDEBAR_WIDTH_MAX,
  SIDEBAR_WIDTH_MIN,
  clampSidebarWidth,
  useSidebarWidth,
} from "../../composables/useSidebarWidth";

const { width, savedWidth, dragWidth } = useSidebarWidth();
const handle = ref(null);

let startX = 0;
let startWidth = 0;

function onMove(event) {
  dragWidth.value = clampSidebarWidth(startWidth + (event.clientX - startX));
}

function onEnd() {
  window.removeEventListener("pointermove", onMove);
  window.removeEventListener("pointerup", onEnd);
  window.removeEventListener("pointercancel", onEnd);
  document.body.style.cursor = "";
  document.body.style.userSelect = "";
  // Commit the final width once (store → persist).
  if (dragWidth.value != null) {
    savedWidth.value = dragWidth.value;
    dragWidth.value = null;
  }
}

function onStart(event) {
  event.preventDefault();
  const rect = handle.value?.parentElement?.getBoundingClientRect();
  startWidth = rect?.width || savedWidth.value;
  startX = event.clientX;
  dragWidth.value = clampSidebarWidth(startWidth);
  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onEnd);
  // A cancelled pointer (a touch taken over by the browser) ends it too.
  window.addEventListener("pointercancel", onEnd);
  document.body.style.cursor = "ew-resize";
  document.body.style.userSelect = "none";
}

function onKey(event) {
  const step = event.shiftKey ? 24 : 8;
  if (event.key === "ArrowLeft") {
    savedWidth.value = savedWidth.value - step;
    event.preventDefault();
  } else if (event.key === "ArrowRight") {
    savedWidth.value = savedWidth.value + step;
    event.preventDefault();
  }
}

// A drag still under way when the sidebar goes (a workspace switch) commits.
onBeforeUnmount(() => {
  if (dragWidth.value != null) onEnd();
});
</script>

<template>
  <div
    ref="handle"
    class="sidebar-resize-handle"
    role="separator"
    aria-orientation="vertical"
    aria-label="Resize sidebar"
    :aria-valuenow="width"
    :aria-valuemin="SIDEBAR_WIDTH_MIN"
    :aria-valuemax="SIDEBAR_WIDTH_MAX"
    tabindex="0"
    @pointerdown="onStart"
    @keydown="onKey"
  >
    <Tooltip text="Drag to resize sidebar" activator="parent" />
  </div>
</template>

<style scoped>
/* Over the sidebar's right rule, invisible until hovered, focused or dragged. */
.sidebar-resize-handle {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  width: 6px;
  z-index: var(--z-raised);
  cursor: ew-resize;
  background: transparent;
  touch-action: none;
  transition: background var(--dur-1) var(--ease-standard);
}

.sidebar-resize-handle:hover {
  background: var(--hover-wash);
}

/* Flush against the rail's clipped edge, so the ring is drawn inset. */
.sidebar-resize-handle:focus-visible {
  box-shadow: var(--focus-ring-inset);
  outline: none;
}
</style>
