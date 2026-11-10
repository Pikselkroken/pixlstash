/**
 * The left sidebar's width, shared by every workspace's sidebar (Media,
 * Workflows, Models), so a drag in one is the width in all of them and a
 * workspace switch never moves the edge. `SidebarResizeHandle` drags it.
 */
import { computed, ref } from "vue";
import { useUserPrefsStore } from "../stores/useUserPrefsStore";

export const SIDEBAR_WIDTH_DEFAULT = 240;
// Clamp ≈60%–125% of the default.
export const SIDEBAR_WIDTH_MIN = 140;
export const SIDEBAR_WIDTH_MAX = 300;
// Below this there is no room for the per-row counts; the rows drop them.
const SIDEBAR_NARROW_BELOW = 150;

export const clampSidebarWidth = (value) =>
  Math.min(SIDEBAR_WIDTH_MAX, Math.max(SIDEBAR_WIDTH_MIN, Math.round(value)));

// The live width while a drag is under way, null otherwise. Module state, not
// per component: there is one sidebar edge whichever workspace draws it. A drag
// commits once on pointer-up rather than persisting every frame.
const dragWidth = ref(null);

export function useSidebarWidth() {
  const userPrefsStore = useUserPrefsStore();

  /** The persisted width; setting it clamps and saves. */
  const savedWidth = computed({
    get: () => userPrefsStore.sidebarWidth ?? SIDEBAR_WIDTH_DEFAULT,
    set: (value) => {
      const parsed = Number(value);
      if (!Number.isFinite(parsed)) return;
      userPrefsStore.setSidebarWidth(clampSidebarWidth(parsed));
    },
  });

  /** What the sidebar draws at: the drag's width while dragging. */
  const width = computed(() => dragWidth.value ?? savedWidth.value);
  const narrow = computed(() => width.value < SIDEBAR_NARROW_BELOW);

  return { width, savedWidth, dragWidth, narrow };
}
