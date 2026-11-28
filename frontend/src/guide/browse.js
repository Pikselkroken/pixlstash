// Browsing and searching the Guide (#2825). Pure functions over the
// catalogue, so the sidebar, the grid and their tests agree.
import { CONCEPTS } from "./catalogue";

/** Every concept but New in 2.x, A–Z: the grid's order. */
export const GUIDE_CONCEPTS = CONCEPTS.filter((c) => c.id !== "new").sort(
  (a, b) => a.name.localeCompare(b.name),
);

export const TASK_COUNT = GUIDE_CONCEPTS.reduce(
  (n, c) => n + c.tasks.length,
  0,
);

/** The sidebar row a route is on: "all", "start", "new", "goal:…", "ws:…". */
export function browseOf(route) {
  const value = route?.query?.in;
  return typeof value === "string" && value ? value : "all";
}

/** The concepts one sidebar row shows (Start here is the caller's). */
export function conceptsIn(browse, startHere = []) {
  if (browse === "start")
    return GUIDE_CONCEPTS.filter((c) => startHere.includes(c.id));
  if (browse.startsWith("goal:")) {
    const goal = browse.slice(5);
    return GUIDE_CONCEPTS.filter((c) => c.goal === goal);
  }
  if (browse.startsWith("ws:")) {
    const ws = browse.slice(3);
    return GUIDE_CONCEPTS.filter((c) => c.workspaces?.includes(ws));
  }
  return GUIDE_CONCEPTS;
}

const words = (text) =>
  String(text ?? "")
    .toLowerCase()
    .split(/[^\p{L}\p{N}]+/u)
    .filter(Boolean);

/**
 * Whether `query` matches a concept: its name, summary, task names and
 * search terms, case-insensitively, from the start of any word. Every word
 * of the query must match somewhere.
 */
export function matches(concept, query) {
  const wanted = words(query);
  if (!wanted.length) return true;
  const haystack = [
    concept.name,
    concept.summary,
    ...concept.tasks.map((t) => t.name),
    ...(concept.searchTerms ?? []),
  ].flatMap(words);
  return wanted.every((w) => haystack.some((h) => h.startsWith(w)));
}
