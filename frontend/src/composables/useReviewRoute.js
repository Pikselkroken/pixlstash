// useReviewRoute.js - URL <-> tag-review-overlay synchronisation.
//
// Mirrors the ImageOverlay `?overlay=<pictureId>` mechanics that live inline in
// ImageGrid.vue (`_pushOverlayRoute` / `_removeOverlayRoute` / the
// `route.query.overlay` watcher, ImageGrid.vue ~:3935-3971):
//
//   * `router.replace`, never `push` - opening, navigating inside, and closing
//     the overlay must not stack history entries. Back therefore pops to the
//     history entry that preceded the overlay, and the read-watcher below
//     reconciles the overlay shut on the way out. Same contract as the image
//     overlay; deliberately NOT a second, different back-semantics.
//   * a `syncing` re-entrancy flag so our own replace doesn't feed the
//     read-watcher, plus a no-op guard so an unchanged query never navigates.
//
// Scheme (any app route may carry these):
//
//   ?review=board            → overlay open on the tag-health board
//   ?review=<reviewId>       → overlay open on that review (an OPEN session or
//                              an ARCHIVED receipt - same id space, resolved on
//                              restore against the loaded lists)
//   ?review_project=<id>     → board scope: project
//   ?review_set=<id>         → board scope: set
//   ?review_character=<id|UNASSIGNED> → board scope: character
//
// Everything else the overlay holds (board sort, tag filter text, anomaly-only
// toggle, the zero-Priority disclosure, scroll, zoom, the tag panel and the
// new-review dialog) is deliberately NOT encoded: it is transient
// view-shaping state that is cheap to re-apply and would otherwise ride along
// in shared links as somebody else's incidental filter.

const REVIEW_KEY = "review";
export const REVIEW_BOARD = "board";
const REVIEW_SCOPE_KEYS = {
  projectId: "review_project",
  setId: "review_set",
  characterId: "review_character",
};
export const UNASSIGNED = "UNASSIGNED";

const ALL_KEYS = [REVIEW_KEY, ...Object.values(REVIEW_SCOPE_KEYS)];

// Vue Router hands back a string, an array (repeated key), or null (bare key).
function firstValue(raw) {
  if (Array.isArray(raw)) return raw.length ? raw[raw.length - 1] : undefined;
  return raw;
}

// Positive integer or null. Anything else (NaN, 0, negative, float, garbage)
// degrades to null rather than throwing.
function parsePositiveInt(raw) {
  const value = firstValue(raw);
  if (value == null || value === "") return null;
  const n = Number(value);
  return Number.isInteger(n) && n > 0 ? n : null;
}

function parseCharacter(raw) {
  const value = firstValue(raw);
  if (value == null || value === "") return null;
  if (String(value).toUpperCase() === UNASSIGNED) return UNASSIGNED;
  return parsePositiveInt(value);
}

/**
 * Read the review params out of a route query.
 *
 * `open` is driven by the mere PRESENCE of `?review` - `?review`, `?review=`,
 * `?review=true`, `?review=board` and `?review=nonsense` all open the overlay on
 * the board. A half-open overlay is never a reachable state.
 */
export function parseReviewQuery(query = {}) {
  const raw = firstValue(query[REVIEW_KEY]);
  // Vue Router represents a bare `?review` (no `=`) as null, so presence of the
  // key - not truthiness of its value - is what opens the overlay.
  const open = Object.hasOwn(query, REVIEW_KEY) && raw !== undefined;
  return {
    open,
    reviewId: open ? parsePositiveInt(raw) : null,
    scope: {
      projectId: parsePositiveInt(query[REVIEW_SCOPE_KEYS.projectId]),
      setId: parsePositiveInt(query[REVIEW_SCOPE_KEYS.setId]),
      characterId: parseCharacter(query[REVIEW_SCOPE_KEYS.characterId]),
    },
  };
}

/**
 * Build the next query object: `base` with every review param stripped, then
 * the current overlay state re-applied. Returns a plain object.
 */
export function buildReviewQuery(base = {}, state = {}) {
  const next = {};
  for (const [key, value] of Object.entries(base)) {
    if (!ALL_KEYS.includes(key)) next[key] = value;
  }
  if (!state.open) return next;

  const view = state.view || { type: "board" };
  next[REVIEW_KEY] =
    (view.type === "session" || view.type === "archived") && view.id != null
      ? String(view.id)
      : REVIEW_BOARD;

  const scope = state.scope || {};
  for (const [dim, key] of Object.entries(REVIEW_SCOPE_KEYS)) {
    const value = scope[dim];
    if (value != null && value !== "") next[key] = String(value);
  }
  return next;
}

function sameQuery(a, b) {
  const ak = Object.keys(a);
  const bk = Object.keys(b);
  if (ak.length !== bk.length) return false;
  return ak.every((k) => String(firstValue(a[k])) === String(firstValue(b[k])));
}

function scopeEquals(a = {}, b = {}) {
  return (
    (a.projectId ?? null) === (b.projectId ?? null) &&
    (a.setId ?? null) === (b.setId ?? null) &&
    String(a.characterId ?? "") === String(b.characterId ?? "")
  );
}

/**
 * Point an already-open overlay at `reviewId`.
 *
 * A stale id - a review that has since been archived-and-purged, deleted, or
 * simply never existed - resolves to neither list and falls back to the board.
 * It must never leave `store.view` asserting a session that isn't there.
 */
export function resolveReviewView(store, reviewId) {
  if (reviewId == null) {
    if (store.view.type !== "board") store.showBoard();
    return "board";
  }
  if (store.sessions.some((s) => s.id === reviewId)) {
    store.openSession(reviewId);
    return "session";
  }
  if (store.archived.some((a) => a.id === reviewId)) {
    store.openArchived(reviewId);
    return "archived";
  }
  store.showBoard();
  return "board";
}

/**
 * Wire the store <-> URL both ways. Call once, from the component that owns the
 * overlay mount point (App.vue).
 *
 * @param {object} route  reactive route (useRoute())
 * @param {object} router router instance (useRouter())
 * @param {object} store  useReviewSessionsStore() instance
 * @param {object} vue    { watch } - injected so the module stays unit-testable
 */
export function useReviewRoute(route, router, store, { watch }) {
  // Guards the read-watcher against our own writes (mirrors ImageGrid's
  // `_overlayRoutePushPending`).
  let syncing = false;

  function writeRoute() {
    const next = buildReviewQuery(route.query, {
      open: store.overlayOpen,
      view: store.view,
      scope: store.healthScope,
    });
    // No-op guard: an unchanged query must not produce a navigation at all, so
    // rapid view switches can't spam identical entries or reject as duplicates.
    if (sameQuery(next, route.query)) return;
    syncing = true;
    const done = () => {
      syncing = false;
    };
    Promise.resolve(router.replace({ query: next })).then(done, done);
  }

  function applyRoute() {
    const { open, reviewId, scope } = parseReviewQuery(route.query);

    if (!open) {
      if (store.overlayOpen) store.overlayOpen = false;
      return;
    }

    if (!store.overlayOpen) {
      // Pre-open: seed the scope directly so the overlay's own `store.load()`
      // fetches the board already scoped (setHealthScope would fire a second,
      // redundant /tag_health request). `pendingRestoreViewId` is consumed by
      // load() once the session lists have landed - only then can an id be
      // resolved to a session vs an archived receipt vs nothing.
      if (!scopeEquals(store.healthScope, scope))
        store.healthScope = { ...scope };
      store.pendingRestoreViewId = reviewId;
      store.overlayOpen = true;
      return;
    }

    // Already open (back/forward within the overlay's lifetime).
    if (!scopeEquals(store.healthScope, scope)) store.setHealthScope(scope);
    const view = store.view;
    if (reviewId == null) {
      if (view.type !== "board") store.showBoard();
      return;
    }
    if (view.type !== "board" && view.id === reviewId) return;
    resolveReviewView(store, reviewId);
  }

  // Store → URL.
  watch(
    () => [store.overlayOpen, store.view, store.healthScope],
    () => {
      if (syncing) return;
      writeRoute();
    },
    { deep: true },
  );

  // URL → store (browser back/forward, or a pasted link).
  watch(
    () => ALL_KEYS.map((k) => route.query[k]),
    () => {
      if (syncing) return;
      applyRoute();
    },
    { deep: true },
  );

  // Initial restore (page refresh / cold link).
  applyRoute();

  return { applyRoute, writeRoute };
}
