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
    inFlight = listFavourites()
      .then((body) => {
        if (started !== epoch) return;
        apply(body);
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

  /**
   * Mark or unmark keys. Drawn at once, then replaced by the server's answer;
   * on a refusal the previous marks come back and the owner is told.
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
    const before = keys.value;
    const mine = new Set(before[kind]);
    for (const key of own) on ? mine.add(key) : mine.delete(key);
    keys.value = { ...before, [kind]: [...mine].sort() };
    const started = epoch;
    try {
      const body = await setFavourites(kind, own, on);
      if (started === epoch) apply(body);
    } catch (err) {
      console.warn(`[favourites] could not ${on ? "mark" : "unmark"}`, kind, own, err);
      if (started !== epoch) return;
      keys.value = before;
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
