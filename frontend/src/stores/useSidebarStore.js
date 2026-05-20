import { ref } from "vue";
import { defineStore } from "pinia";

function loadStatsOpen() {
  try {
    const stored = window.localStorage?.getItem("pixlstash:statsSidebarOpen");
    if (stored !== null) return stored !== "false";
    return !window.matchMedia("(hover: none) and (pointer: coarse)").matches;
  } catch {
    return true;
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

export const useSidebarStore = defineStore("sidebar", () => {
  const sidebarVisible = ref(true);
  const sidebarDocked = ref(loadSidebarDocked());
  const statsOpen = ref(loadStatsOpen());
  const sidebarForcedHidden = ref(false);
  const statsForcedHidden = ref(false);

  function toggleSidebar() {
    sidebarVisible.value = !sidebarVisible.value;
  }

  function toggleStats() {
    statsOpen.value = !statsOpen.value;
    saveStatsOpen(statsOpen.value);
  }

  function persistSidebarDocked(val) {
    sidebarDocked.value = val;
    saveSidebarDocked(val);
  }

  return {
    sidebarVisible,
    sidebarDocked,
    statsOpen,
    sidebarForcedHidden,
    statsForcedHidden,
    toggleSidebar,
    toggleStats,
    persistSidebarDocked,
  };
});
