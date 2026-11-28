import { defineStore } from "pinia";
import { computed, onScopeDispose, ref } from "vue";
import { onSessionReset } from "../utils/apiClient";
import { runKey } from "../utils/gpuMemory";
import { useTasksStore } from "./useTasksStore";

/**
 * The sheet draws closed while they run (#2819): the sheet dialog keeps each
 * one's snapshot here when it closes, by person (`id`) or by item and person
 * (`kind:id`), and the Studio shows a placeholder for every one still drawing
 * or waiting to be picked, with how far its runs have got.
 *
 * ponytail: held for the app session only; a reload forgets an unfinished
 * draw (its sheets stay in the Scrapheap). Store it on the character if that
 * turns out to matter.
 */
export const useSheetDrawsStore = defineStore("sheetDraws", () => {
  // key -> { name, kind, character, snapshot }
  const saved = ref(new Map());
  const tasks = useTasksStore();

  function keep(key, entry) {
    saved.value.set(key, entry);
  }

  function forget(key) {
    saved.value.delete(key);
  }

  function get(key) {
    return saved.value.get(key) ?? null;
  }

  /** One placeholder per draw with sheets coming or to pick:
   * `{ key, kind, character, count, fraction, ready, status }`. A run is
   * done once ComfyUI no longer lists it; a running one counts its percent. */
  const placeholders = computed(() => {
    const live = new Map(
      tasks.comfyuiRuns.map((run) => [runKey(run), Number(run.percent) || 0]),
    );
    const out = [];
    for (const [key, entry] of saved.value) {
      const snapshot = entry.snapshot;
      const runs = snapshot?.runs ?? [];
      if (!["drawing", "pick"].includes(snapshot?.phase) || !runs.length) continue;
      const going = runs.filter((run) => !run.stopped && live.has(run.key));
      const done = runs.length - going.length;
      const fraction =
        (done + going.reduce((sum, run) => sum + live.get(run.key) / 100, 0)) / runs.length;
      const ready = going.length === 0;
      out.push({
        key,
        kind: entry.kind,
        character: entry.character,
        name: entry.label,
        count: runs.length,
        fraction,
        ready,
        status: ready
          ? "Sheets ready to pick"
          : `Drawing ${runs.length} ${runs.length === 1 ? "sheet" : "sheets"}…`,
      });
    }
    return out;
  });

  /** The placeholders of one kind, a person's (`characterId`) or all of it. */
  function of(kind, characterId) {
    return placeholders.value.filter(
      (p) => p.kind === kind && (characterId === undefined || p.character?.id === characterId),
    );
  }

  function reset() {
    saved.value = new Map();
  }

  const unsubscribe = onSessionReset(reset);
  onScopeDispose(() => unsubscribe());

  return { saved, keep, forget, get, placeholders, of, reset };
});
