// useFavouritesStore.js - the owner's favourites, one mechanism on three
// surfaces (#1578): models on the shelf, workflow sets, workflow cards.
//
// A favourite is a tie-break, not a place. Every list keeps its groups and its
// sort; inside each group favourites lead (`favouritesFirst`). Nothing ever
// changes group, and with no favourites every list is exactly what it was.
//
// Keys, the same everywhere:
//   model    - the model's sha256 (a run by its cover's hash). A row with no
//              hash yet has no key and cannot be a favourite until it has one.
//   workflow - the card key (a stack by its cover card's key).
//   set      - the grid's set key: `hand:<id>`, `model:<head id>`.
//
// Per hub, on the server: a favourite follows the machine, not the library.

import { computed, onScopeDispose, ref } from "vue";
import { defineStore } from "pinia";
import { listFavourites, setFavourites } from "../api/favourites";
import { onSessionReset } from "../utils/apiClient";
import { errorMessage } from "../utils/apiError";
import { useNoticeStore } from "./useNoticeStore";

export const FAVOURITE_KINDS = ["model", "workflow", "set"];

/** The one shortcut that toggles a favourite, on every surface. */
export const FAVOURITE_KEY = "h";

const empty = () => ({ model: [], workflow: [], set: [] });

/**
 * Stable partition: favourites first, everything else after, each keeping the
 * order it came in. The whole ranking rule - callers sort and group first,
 * then call this per group.
 *
 * @template T
 * @param {T[]} items
 * @param {(item: T) => boolean} isFav
 * @returns {T[]}
 */
export function favouritesFirst(items, isFav) {
  const lead = [];
  const rest = [];
  for (const item of items ?? []) (isFav(item) ? lead : rest).push(item);
  return lead.length ? [...lead, ...rest] : [...(items ?? [])];
}

/**
 * The verb a selection offers: Unfavourite only when every keyed item already
 * is one; a mixed selection gets Favourite and makes them all favourites.
 *
 * @param {boolean} allFavourite
 */
export const favouriteVerb = (allFavourite) =>
  allFavourite ? "Unfavourite" : "Favourite";

export const useFavouritesStore = defineStore("favourites", () => {
  const keys = ref(empty());
  const loaded = ref(false);
  let epoch = 0;
  let inFlight = null;
  // Writes, numbered as they are sent: only the newest answer is applied.
  let writeSeq = 0;

  const byKind = computed(() =>
    Object.fromEntries(
      FAVOURITE_KINDS.map((kind) => [kind, new Set(keys.value[kind] ?? [])]),
    ),
  );

  /** Is this key a favourite? A missing key (an unhashed model) never is. */
  function isFavourite(kind, key) {
    if (key == null || key === "") return false;
    return byKind.value[kind]?.has(String(key)) ?? false;
  }

  /** True when there is at least one key and every one is a favourite. */
  function allFavourite(kind, list) {
    const own = (list ?? []).filter((key) => key != null && key !== "");
    return own.length > 0 && own.every((key) => isFavourite(kind, key));
  }

  function apply(body) {
    const next = empty();
    for (const kind of FAVOURITE_KINDS) {
      if (Array.isArray(body?.[kind])) next[kind] = body[kind].map(String);
    }
    keys.value = next;
  }

  /** Read once; later calls share the first read. */
  function ensureLoaded() {
    if (loaded.value) return Promise.resolve();
    return fetch();
  }

  function fetch() {
    if (inFlight) return inFlight;
    const started = epoch;
    const sentWrites = writeSeq;
    inFlight = listFavourites()
      .then((body) => {
        if (started !== epoch) return;
        // A write sent since this read started is newer than its answer.
        if (sentWrites === writeSeq) apply(body);
        loaded.value = true;
      })
      .catch((err) => {
        // Advisory ordering only: a failed read leaves every list as it was.
        console.warn("[favourites] could not read the favourites", err);
      })
      .finally(() => {
        if (started === epoch) inFlight = null;
      });
    return inFlight;
  }

  /** Mark or unmark keys of one kind locally, without asking the server. */
  function mark(kind, list, on) {
    const mine = new Set(keys.value[kind]);
    for (const key of list) on ? mine.add(key) : mine.delete(key);
    keys.value = { ...keys.value, [kind]: [...mine].sort() };
  }

  /**
   * Mark or unmark keys. Drawn at once, then replaced by the server's answer
   * when no newer write has been sent since (answers can land out of order).
   * On a refusal the owner is told. The newest write puts back the keys it
   * changed and re-reads the server, which also undoes an older refused write;
   * an older one leaves the marks to the newer write's answer, which is the
   * server's truth after both.
   *
   * @param {"model"|"workflow"|"set"} kind
   * @param {Array<string>} list
   * @param {boolean} on
   */
  async function setFavourite(kind, list, on) {
    const own = [
      ...new Set((list ?? []).filter((k) => k != null && k !== "").map(String)),
    ];
    if (!own.length) return;
    const changed = own.filter((key) => isFavourite(kind, key) !== on);
    mark(kind, own, on);
    const started = epoch;
    const seq = ++writeSeq;
    try {
      const body = await setFavourites(kind, own, on);
      if (started === epoch && seq === writeSeq) apply(body);
    } catch (err) {
      console.warn(`[favourites] could not ${on ? "mark" : "unmark"}`, kind, own, err);
      if (started !== epoch) return;
      if (seq === writeSeq) {
        mark(kind, changed, !on);
        fetch();
      }
      useNoticeStore().push({
        level: "error",
        text: errorMessage(
          err,
          on ? "That favourite could not be saved." : "That favourite could not be removed.",
        ),
      });
    }
  }

  /** Favourite a mixed selection, unfavourite one that is all favourites. */
  function toggle(kind, list) {
    return setFavourite(kind, list, !allFavourite(kind, list));
  }

  function reset() {
    epoch += 1;
    inFlight = null;
    loaded.value = false;
    keys.value = empty();
  }

  const unsubscribeSessionReset = onSessionReset(reset);
  onScopeDispose(() => unsubscribeSessionReset());

  return {
    keys,
    byKind,
    loaded,
    isFavourite,
    allFavourite,
    ensureLoaded,
    fetch,
    setFavourite,
    toggle,
    reset,
  };
});
