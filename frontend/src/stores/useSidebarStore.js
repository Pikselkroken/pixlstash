import { computed, ref } from "vue";
import { defineStore } from "pinia";

function loadStatsOpen() {
  try {
    const stored = window.localStorage?.getItem("pixlstash:statsSidebarOpen");
    if (stored !== null) return stored !== "false";
    // Default hidden so the grid is uncluttered on first run; the user opens the
    // stats panel from the toolbar toggle and the choice is then persisted.
    return false;
  } catch {
    return false;
  }
}

function saveStatsOpen(val) {
  try {
    window.localStorage?.setItem(
      "pixlstash:statsSidebarOpen",
      val ? "true" : "false",
    );
  } catch {
    // ignore
  }
}

// The Workflows inspector's own open flag. It describes the selection, so it
// defaults OPEN: the grid is laid out around it from the first paint and a
// click on a card never reflows the columns under the pointer. The stats
// sidebar describes the library and keeps its own, closed-by-default flag.
const WORKFLOW_INSPECTOR_KEY = "pixlstash:workflowInspectorOpen";

function loadWorkflowInspectorOpen() {
  try {
    const stored = window.localStorage?.getItem(WORKFLOW_INSPECTOR_KEY);
    if (stored !== null && stored !== undefined) return stored !== "false";
    return true;
  } catch {
    return true;
  }
}

function saveWorkflowInspectorOpen(val) {
  try {
    window.localStorage?.setItem(WORKFLOW_INSPECTOR_KEY, val ? "true" : "false");
  } catch {
    // ignore
  }
}

// Width: full sidebar vs. narrow icon dock. Set in Settings → Appearance.
function loadSidebarDocked() {
  try {
    return window.localStorage?.getItem("pixlstash:sidebarDocked") === "true";
  } catch {
    return false;
  }
}

function saveSidebarDocked(val) {
  try {
    window.localStorage?.setItem(
      "pixlstash:sidebarDocked",
      val ? "true" : "false",
    );
  } catch {
    // ignore
  }
}

// Visibility: pinned (always shown, pushes the grid) vs. unpinned/auto (hidden,
// slides in as an overlay on hover). Toggled from the sidebar's Library header.
function loadSidebarPinned() {
  try {
    const stored = window.localStorage?.getItem("pixlstash:sidebarPinned");
    if (stored === "true") return true;
    if (stored === "false") return false;
  } catch {
    // ignore
  }
  return true;
}

function saveSidebarPinned(val) {
  try {
    window.localStorage?.setItem(
      "pixlstash:sidebarPinned",
      val ? "true" : "false",
    );
  } catch {
    // ignore
  }
}

export const useSidebarStore = defineStore("sidebar", () => {
  const sidebarDocked = ref(loadSidebarDocked()); // width pref
  const sidebarPinned = ref(loadSidebarPinned()); // visibility pref
  // Transient reveal state for auto-hide: true while the sidebar is peeked open.
  const autoRevealed = ref(false);
  const statsOpen = ref(loadStatsOpen());
  const workflowInspectorOpen = ref(loadWorkflowInspectorOpen());
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
    saveSidebarDocked(sidebarDocked.value);
  }

  function setSidebarPinned(val) {
    sidebarPinned.value = !!val;
    saveSidebarPinned(sidebarPinned.value);
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
    saveStatsOpen(statsOpen.value);
  }

  // The reader's own choice, from the toolbar toggle or the edge tab: kept.
  function toggleWorkflowInspector() {
    workflowInspectorOpen.value = !workflowInspectorOpen.value;
    saveWorkflowInspectorOpen(workflowInspectorOpen.value);
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
  // Deliberately NOT `saveStatsOpen`, unlike `toggleStats` above: opening the
  // rail to answer one "show me" is not the reader choosing to keep it open,
  // and persisting it would leave every later session with a rail they never
  // asked for. Both rails' flags, because the caller (a notice) does not know
  // which screen is showing, and only the one on screen is drawn.
  const tasksTabRequest = ref(0);
  function showTasksTab() {
    statsOpen.value = true;
    workflowInspectorOpen.value = true;
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
    showTasksTab,
    persistSidebarDocked,
  };
});
