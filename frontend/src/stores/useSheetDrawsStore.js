// useSheetDrawsStore.js - which characters have sheet draws, and their state.
//
// A sheet draw is stored on its character (`GET /character-sheets/draws`), so
// the way back to the Sheets screen survives closing it: the sidebar's Sheets
// row under a character reads the newest draw from here, and the Tasks panel
// finds the character a "Character sheet" run belongs to by its run key.
//
// Read when the app starts and again whenever the set of ComfyUI runs in
// flight changes (a sheet run starting or ending changes it), and by the
// Sheets screen after anything it stores. Owner sessions only: the route is
// owner-only.

import { defineStore } from "pinia";
import { computed, onScopeDispose, ref, watch } from "vue";
import { listSheetDrawSummary } from "../api/characterSheets";
import { isReadOnly, onSessionReset } from "../utils/apiClient";
import { runKey } from "../utils/gpuMemory";
import { useTasksStore } from "./useTasksStore";

export const useSheetDrawsStore = defineStore("sheetDraws", () => {
  const tasks = useTasksStore();
  // character id -> {character_id, draws, newest, run_keys}
  const byCharacter = ref({});
  let asked = 0;

  async function refresh() {
    if (isReadOnly.value) return;
    const mine = ++asked;
    try {
      const { characters } = await listSheetDrawSummary();
      if (mine !== asked) return;
      byCharacter.value = Object.fromEntries(
        (characters || []).map((entry) => [entry.character_id, entry]),
      );
    } catch (e) {
      console.warn("Couldn't read the characters' sheet draws", e);
    }
  }

  /** run key -> character id, for every run of every draw. */
  const characterByRun = computed(() => {
    const out = new Map();
    for (const entry of Object.values(byCharacter.value)) {
      for (const key of entry.run_keys || []) out.set(key, entry.character_id);
    }
    return out;
  });

  /** The character a run was drawn for, or null when it is not a sheet run. */
  function characterOfRun(key) {
    return key ? (characterByRun.value.get(key) ?? null) : null;
  }

  function summaryOf(characterId) {
    return byCharacter.value[Number(characterId)] ?? null;
  }

  // A sheet run starting or ending changes the runs in flight.
  watch(
    () => tasks.comfyuiRuns.map(runKey).join(","),
    () => refresh(),
  );

  function reset() {
    asked += 1;
    byCharacter.value = {};
  }
  const unsubscribeSessionReset = onSessionReset(reset);
  onScopeDispose(() => unsubscribeSessionReset());

  return { byCharacter, refresh, characterOfRun, summaryOf };
});
