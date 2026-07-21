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
  // Responsive override: a narrow/mobile window is always auto-hide + full width.
  const sidebarForcedHidden = ref(false);
  const statsForcedHidden = ref(false);

  const effectivePinned = computed(() =>
    sidebarForcedHidden.value ? false : sidebarPinned.value,
  );
  // Width used by the layout — forced to full on mobile.
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
    // When unpinning, keep it revealed — the pointer is still inside the sidebar
    // — until the pointer leaves. When pinning, clear the transient reveal.
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

  // Back-compat helper used elsewhere.
  function persistSidebarDocked(val) {
    setSidebarDocked(val);
  }

  return {
    sidebarDocked,
    sidebarPinned,
    autoRevealed,
    effectiveDocked,
    sidebarVisible,
    sidebarOverlay,
    statsOpen,
    sidebarForcedHidden,
    statsForcedHidden,
    setSidebarDocked,
    setSidebarPinned,
    revealSidebar,
    hideAutoSidebar,
    toggleStats,
    persistSidebarDocked,
  };
});
