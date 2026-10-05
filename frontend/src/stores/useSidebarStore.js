import { computed, ref } from "vue";
import { defineStore } from "pinia";

/**
 * A remembered on/off preference: `fallback` until the reader first sets it.
 * Storage can be missing or throw (a private window, a locked profile), and
 * then the preference lasts this session only.
 */
function loadFlag(key, fallback) {
  try {
    const stored = window.localStorage?.getItem(key);
    return stored == null ? fallback : stored !== "false";
  } catch (err) {
    console.warn(`[sidebar] could not read ${key}; using ${fallback}`, err);
    return fallback;
  }
}

function saveFlag(key, val) {
  try {
    window.localStorage?.setItem(key, val ? "true" : "false");
  } catch (err) {
    console.warn(`[sidebar] could not save ${key}; it lasts this session`, err);
  }
}

// Stats: hidden on a fresh install so the grid is uncluttered on first run;
// the reader opens it from the toolbar toggle and the choice is then kept.
const STATS_KEY = "pixlstash:statsSidebarOpen";

// The Workflows inspector's own open flag. It describes the selection, so it
// defaults OPEN: the grid is laid out around it from the first paint and a
// click on a card never reflows the columns under the pointer. The stats
// sidebar describes the library and keeps its own, closed-by-default flag.
const WORKFLOW_INSPECTOR_KEY = "pixlstash:workflowInspectorOpen";

// The Models screen's rail (Models | Tasks). Closed on a fresh install and then
// however the reader left it: it lists the shelf rather than describing a
// selection, so nothing opens it on the reader's behalf.
const MODELS_RAIL_KEY = "pixlstash:modelsRailOpen";

// Width: full sidebar vs. narrow icon dock. Set in Settings → Appearance.
const SIDEBAR_DOCKED_KEY = "pixlstash:sidebarDocked";

// Visibility: pinned (always shown, pushes the grid) vs. unpinned/auto (hidden,
// slides in as an overlay on hover). Toggled from the sidebar's Library header.
const SIDEBAR_PINNED_KEY = "pixlstash:sidebarPinned";

export const useSidebarStore = defineStore("sidebar", () => {
  const sidebarDocked = ref(loadFlag(SIDEBAR_DOCKED_KEY, false)); // width pref
  const sidebarPinned = ref(loadFlag(SIDEBAR_PINNED_KEY, true)); // visibility pref
  // Transient reveal state for auto-hide: true while the sidebar is peeked open.
  const autoRevealed = ref(false);
  const statsOpen = ref(loadFlag(STATS_KEY, false));
  const workflowInspectorOpen = ref(loadFlag(WORKFLOW_INSPECTOR_KEY, true));
  const modelsRailOpen = ref(loadFlag(MODELS_RAIL_KEY, false));
  // Bumped by whichever screen owns a selection-describing inspector when its
  // selection changes to something NEW. The closed-inspector edge tab and the
  // rail toggle's glyph both play their one-shot nudge on it. A counter, like
  // `tasksTabRequest`, so two new selections in a row are two nudges.
  const inspectorNudge = ref(0);
  // Responsive override: a narrow/mobile window is always auto-hide + full width.
  const sidebarForcedHidden = ref(false);
  const statsForcedHidden = ref(false);

  const effectivePinned = computed(() =>
    sidebarForcedHidden.value ? false : sidebarPinned.value,
  );
  // Width used by the layout - forced to full on mobile.
  // True while a reference/import folder is being scanned. The sidebar sets it
  // (it owns the folder lists); the grid reads it, so its empty state can say
  // "still scanning" instead of "nothing here".
  const folderScanning = ref(false);

  const effectiveDocked = computed(() =>
    sidebarForcedHidden.value ? false : sidebarDocked.value,
  );

  // True when the sidebar should currently be on screen.
  const sidebarVisible = computed(
    () => effectivePinned.value || autoRevealed.value,
  );
  // True when the sidebar floats over the grid (auto-hide / drawer) rather than
  // taking layout space.
  const sidebarOverlay = computed(() => !effectivePinned.value);

  function setSidebarDocked(val) {
    sidebarDocked.value = !!val;
    saveFlag(SIDEBAR_DOCKED_KEY, sidebarDocked.value);
  }

  function setSidebarPinned(val) {
    sidebarPinned.value = !!val;
    saveFlag(SIDEBAR_PINNED_KEY, sidebarPinned.value);
    // When unpinning, keep it revealed - the pointer is still inside the sidebar
    // - until the pointer leaves. When pinning, clear the transient reveal.
    autoRevealed.value = !sidebarPinned.value;
  }

  function revealSidebar() {
    if (sidebarOverlay.value) autoRevealed.value = true;
  }

  function hideAutoSidebar() {
    autoRevealed.value = false;
  }

  function toggleStats() {
    statsOpen.value = !statsOpen.value;
    saveFlag(STATS_KEY, statsOpen.value);
  }

  // The reader's own choice, from the toolbar toggle or the edge tab: kept.
  function toggleWorkflowInspector() {
    workflowInspectorOpen.value = !workflowInspectorOpen.value;
    saveFlag(WORKFLOW_INSPECTOR_KEY, workflowInspectorOpen.value);
  }

  function setModelsRailOpen(val) {
    modelsRailOpen.value = !!val;
    saveFlag(MODELS_RAIL_KEY, modelsRailOpen.value);
  }

  function toggleModelsRail() {
    setModelsRailOpen(!modelsRailOpen.value);
  }

  // A deep link's "show me": opened, not persisted (see `showTasksTab`).
  function openWorkflowInspector() {
    workflowInspectorOpen.value = true;
  }

  // "Take me to the task manager", from a notice or a banner. A counter rather
  // than a flag, so asking twice in a row is two requests and nothing has to
  // remember to clear it. Whichever inspector is on screen watches it and
  // selects its own Tasks tab: the rail has more than one occupant, and a
  // deep link that named one of them by its component ref reached the tab on
  // every screen except the one that starts the runs.
  //
  // Deliberately NOT `saveFlag`, unlike `toggleStats` above: opening the
  // rail to answer one "show me" is not the reader choosing to keep it open,
  // and persisting it would leave every later session with a rail they never
  // asked for. Both rails' flags, because the caller (a notice) does not know
  // which screen is showing, and only the one on screen is drawn.
  const tasksTabRequest = ref(0);
  function showTasksTab() {
    statsOpen.value = true;
    workflowInspectorOpen.value = true;
    modelsRailOpen.value = true;
    tasksTabRequest.value += 1;
  }

  // Back-compat helper used elsewhere.
  function persistSidebarDocked(val) {
    setSidebarDocked(val);
  }

  return {
    sidebarDocked,
    sidebarPinned,
    autoRevealed,
    effectiveDocked,
    folderScanning,
    sidebarVisible,
    sidebarOverlay,
    statsOpen,
    workflowInspectorOpen,
    modelsRailOpen,
    inspectorNudge,
    tasksTabRequest,
    sidebarForcedHidden,
    statsForcedHidden,
    setSidebarDocked,
    setSidebarPinned,
    revealSidebar,
    hideAutoSidebar,
    toggleStats,
    toggleWorkflowInspector,
    openWorkflowInspector,
    setModelsRailOpen,
    toggleModelsRail,
    showTasksTab,
    persistSidebarDocked,
  };
});
