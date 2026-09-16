import { reactive, watch } from "vue";
import { getPictureCount } from "../api/pictures";

/**
 * Counts for the filter menu's rows: how many pictures the current view holds
 * with that one choice applied, ignoring the other active filters. Each is a
 * `/pictures/count` over the grid's view plus one filter, so a row's number is
 * exactly what the grid would show with only that filter on.
 *
 * Lazy and cached per view: a row asks for its count when it renders, and the
 * cache empties when the view changes or `reset()` is called (the menu opening).
 *
 * @param {() => string|null} baseQuery the grid's view, no filters,
 *   pre-encoded; `null` when the view cannot be counted (a search)
 */
export function useFilterCounts(baseQuery) {
  const counts = reactive({});
  let requested = new Set();
  let generation = 0;

  function reset() {
    generation += 1;
    requested = new Set();
    for (const key of Object.keys(counts)) delete counts[key];
  }

  watch(baseQuery, reset);

  /**
   * The count for one extra filter, or `undefined` while it loads or failed.
   * Safe to call from a template: the request starts outside the render.
   *
   * @param {string} extra pre-encoded filter params, "" for the view's total
   */
  function count(extra) {
    if (baseQuery() == null) return undefined;
    if (!requested.has(extra)) {
      requested.add(extra);
      const gen = generation;
      const base = baseQuery();
      const query = [base, extra].filter(Boolean).join("&");
      queueMicrotask(() => {
        getPictureCount(query)
          .then((body) => {
            if (gen === generation) counts[extra] = Number(body?.count ?? 0);
          })
          .catch((err) => {
            // The row just shows no number; the filter itself still works.
            console.warn("Filter count failed", query, err);
          });
      });
    }
    return counts[extra];
  }

  return { count, reset };
}

/** `[["tag", "hat"]]` → `"tag=hat"`, for `count()`. */
export function filterParams(pairs) {
  return new URLSearchParams(pairs).toString();
}
