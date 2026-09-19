import { reactive } from "vue";
import { getPictureCount } from "../api/pictures";

// At most this many count requests in flight. Opening the model or LoRA menu
// asks for a count per visible row, and the rest wait their turn.
const MAX_IN_FLIGHT = 6;

// One cache for every caller, keyed by the full query, so the strip's "of N"
// and the menu header's are one request and cannot disagree.
const counts = reactive({});
let requested = new Set();
let generation = 0;
let inFlight = 0;
const waiting = [];

function pump() {
  while (inFlight < MAX_IN_FLIGHT && waiting.length) {
    const query = waiting.shift();
    const gen = generation;
    inFlight += 1;
    // Inside a promise, so even a synchronous throw releases its slot.
    Promise.resolve()
      .then(() => getPictureCount(query))
      .then((body) => {
        if (gen === generation) counts[query] = Number(body?.count ?? 0);
      })
      .catch((err) => {
        // The row just shows no number, and the next render asks again.
        console.warn("Filter count failed", query, err);
        if (gen === generation) requested.delete(query);
      })
      .finally(() => {
        inFlight -= 1;
        pump();
      });
  }
}

/**
 * Empty the cache and drop anything still queued. Called when the menu opens,
 * so counts reflect edits made while it was closed. A response already on the
 * wire is discarded when it lands.
 */
export function resetFilterCounts() {
  generation += 1;
  requested = new Set();
  waiting.length = 0;
  for (const key of Object.keys(counts)) delete counts[key];
}

/**
 * Counts for the filter menu's rows: how many pictures the current view holds
 * with that one choice applied, ignoring the other active filters. Each is a
 * `/pictures/count` over the grid's view plus one filter, so a row's number is
 * exactly what the grid would show with only that filter on.
 *
 * @param {() => string|null} baseQuery the grid's view, no filters,
 *   pre-encoded; `null` when the view cannot be counted (a search)
 */
export function useFilterCounts(baseQuery) {
  /**
   * The count for one extra filter, or `undefined` while it loads or failed.
   * Safe to call from a template: the request starts outside the render.
   *
   * @param {string} extra pre-encoded filter params, "" for the view's total
   */
  function count(extra) {
    const base = baseQuery();
    if (base == null) return undefined;
    // A likeness-sorted count runs two queued tasks on the one DB worker, and
    // its per-row numbers match the unsorted ones: only the total is asked.
    if (extra && base.includes("sort=CHARACTER_LIKENESS")) return undefined;
    const query = [base, extra].filter(Boolean).join("&");
    if (!requested.has(query)) {
      requested.add(query);
      waiting.push(query);
      queueMicrotask(pump);
    }
    return counts[query];
  }

  return { count, reset: resetFilterCounts };
}

/** `[["tag", "hat"]]` → `"tag=hat"`, for `count()`. */
export function filterParams(pairs) {
  return new URLSearchParams(pairs).toString();
}
