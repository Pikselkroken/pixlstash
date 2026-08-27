// useEntityListsStore.js - the shared character / picture-set / project LISTS.
//
// Three surfaces need "every character", "every picture set" and "every
// project": the sidebar tree, the image context menu's Person/Set/Project
// flyouts, and the tag-review scope pickers. Each used to fetch and keep its
// own private copy, and the context menu refetched all three on every hover
// (the menu is `v-if`-mounted, so its children - and their only cache - were
// destroyed on every close). On a large library those reads are slow enough to
// make the flyouts feel stuck. See frontend_architecture.md §4 Tier 1.
//
// The lists live here instead, with stale-while-revalidate semantics: a caller
// renders the cached list immediately and calls `refresh()` without awaiting
// it, so the network never gates a menu opening.
//
// NOT to be confused with `useEntityNamesStore`, which holds id → name maps for
// the breadcrumb only. This store holds the full row objects.
//
// ── Why the sidebar's counts ride along (issue #651) ────────────────────────
// `characters` and `projects` are read with `include_counts=true`, so every row
// carries its `image_count` (and, for characters, `project_image_count`). That
// replaces the sidebar's per-entity `/{id}/summary` fan-out, one request per
// character on every refresh, with two list reads it was making anyway.
//
// The counts live on the SHARED list rather than in a second "list with counts"
// cache on purpose: two shapes for one entity would mean two caches to keep
// coherent, two invalidation paths and two epochs, and the flyout/scope-picker
// consumers would race the sidebar for the same rows. They simply ignore the
// extra fields. Both count fields are also independent of the sidebar's current
// project selection (the backend scopes `project_image_count` to the
// character's OWN `project_id`), so one cached response is valid in BOTH
// sidebar view modes and a mode switch never invalidates it.
//
// ── Security (see the review on issue #646) ─────────────────────────────────
// `GET /characters`, `/picture_sets` and `/projects` are `SCOPED_LIST` authz
// routes: their CONTENT is an authorization decision, filtered to the calling
// credential's scope. So:
//
//   * The cache is IN MEMORY ONLY. It must never reach localStorage /
//     sessionStorage, where it would outlive the credential that produced it.
//   * `reset()` drops everything on every auth-context transition (logout,
//     login, share-token entry, vault switch) and is wired to the single
//     `onSessionReset` chokepoint in `utils/apiClient.js`. A response that was
//     already in flight when the context changed is DISCARDED (the `epoch`
//     guard below) rather than written into the new session's cache.
//   * `invalidate()` only ever triggers a server-authoritative refetch. Nothing
//     here is ever patched from a WebSocket event payload - `origin_client_id`
//     is attacker-controllable and is echo-matching only (integration
//     _architecture.md §8.1).

import { computed, onScopeDispose, ref } from "vue";
import { defineStore } from "pinia";
import { listCharacters } from "../api/characters";
import { listPictureSets } from "../api/pictureSets";
import { listProjects } from "../api/projects";
import { isReadOnly, onSessionReset, sessionContext } from "../utils/apiClient";

/** The three lists this store owns. */
export const ENTITY_KINDS = ["characters", "sets", "projects"];

/**
 * kind → the api module function that reads the whole list.
 *
 * Characters and projects ask for `include_counts` so the sidebar can render
 * its per-row image counts off this list instead of one `/{id}/summary` request
 * per row (see the header note).
 */
const FETCHERS = {
  characters: ({ baseUrl }) =>
    listCharacters({ baseUrl, params: { include_counts: true } }),
  sets: listPictureSets,
  projects: ({ baseUrl }) =>
    listProjects({ baseUrl, params: { include_counts: true } }),
};

function emptyLists() {
  return { characters: [], sets: [], projects: [] };
}

function zeroed() {
  return { characters: 0, sets: 0, projects: 0 };
}

function falsed() {
  return { characters: false, sets: false, projects: false };
}

export const useEntityListsStore = defineStore("entityLists", () => {
  // The cached rows, exactly as the server returned them.
  const lists = ref(emptyLists());
  // kind → epoch-ms of the last successful read; 0 means "never fetched", which
  // is what separates a cold cache from a genuinely empty library.
  const fetchedAt = ref(zeroed());
  // kind → a request is in flight (whether or not there is a cache to show).
  const pending = ref(falsed());

  // kind → the in-flight promise, so N callers asking at once cost one request.
  // Deliberately a plain Map: nothing renders off it (mirrors useDedupStore's
  // `scopeCountsInFlight`).
  const inFlight = new Map();

  // An invalidation is stronger than an ordinary cache read: when it arrives
  // during an in-flight read, that response may predate the mutation/event.
  // Remember one trailing read per kind so the final cache is authoritative.
  const trailingInvalidations = new Map();

  // Bumped by reset(). A response tagged with a stale epoch is dropped instead
  // of being written into the cache - otherwise a list fetched under the
  // previous credential could land after a logout and render to the next one.
  let epoch = 0;

  const characters = computed(() => lists.value.characters);
  const pictureSets = computed(() => lists.value.sets);
  const projects = computed(() => lists.value.projects);

  /** Have we ever successfully read this list? */
  function has(kind) {
    return (fetchedAt.value[kind] ?? 0) > 0;
  }

  /**
   * Is this kind loading with nothing to show yet?
   *
   * A revalidation over a warm cache is deliberately NOT "loading" - the point
   * of the cache is that the previous list stays on screen meanwhile.
   *
   * @param {string} kind
   * @returns {boolean}
   */
  function isLoading(kind) {
    return !!pending.value[kind] && !has(kind);
  }

  /**
   * Can the current credential read this list at all?
   *
   * A share token scoped to a single character or set has no project scope, so
   * `GET /projects` would 403. Skipping the call keeps the console clean and
   * the flyout honest (an empty list, not an error).
   *
   * @param {string} kind
   * @returns {boolean}
   */
  function canFetch(kind) {
    if (kind !== "projects") return true;
    const resourceType = sessionContext.value?.resource_type;
    return !(
      isReadOnly.value &&
      resourceType != null &&
      resourceType !== "project"
    );
  }

  /**
   * Does this session have any project information at all?
   *
   * The reactive form of `canFetch("projects")`, and the ONE definition of
   * "this credential was granted no project scope" that the UI reads. A token
   * scoped to a character, a picture or a set is 403'd by `GET /projects`
   * (`routes/projects.py` checks `resource_type` before it reads anything), so
   * its project list is not merely empty; it does not exist. Surfaces that
   * would otherwise render a project control, a project row or a project count
   * gate on this and render NOTHING instead: absent project information is
   * omitted, never shown as an empty menu or an error.
   *
   * Deliberately false ONLY for a resource-scoped token. An owner and an
   * unscoped read-only token both keep every project affordance they had,
   * because over-blocking is its own regression.
   */
  const canSeeProjects = computed(() => canFetch("projects"));

  function writeList(kind, rows) {
    lists.value = { ...lists.value, [kind]: rows };
    fetchedAt.value = { ...fetchedAt.value, [kind]: Date.now() };
  }

  /**
   * Read one list from the server, de-duplicated while a request is in flight.
   *
   * This is the "revalidate" half of stale-while-revalidate: callers that are
   * rendering a menu should NOT await it - the reactive getters already hold
   * the previous list, and this call swaps in the fresh one when it lands.
   *
   * On failure the previous list is kept (a stale menu beats an empty one) and
   * the error is logged with the kind and base URL.
   *
   * @param {string} kind - one of {@link ENTITY_KINDS}.
   * @param {Object} [options]
   * @param {string} [options.baseUrl=""] - backend base; "" addresses the API
   *   relatively. Requests are de-duplicated on `kind` alone, since the whole
   *   app talks to one backend.
   * @returns {Promise<Array>} the list now in the cache.
   */
  function refresh(kind, { baseUrl = "" } = {}) {
    if (!FETCHERS[kind]) {
      console.warn(
        `[entityLists] refresh called with an unknown kind: ${kind}`,
      );
      return Promise.resolve([]);
    }
    const existing = inFlight.get(kind);
    if (existing) return existing;

    if (!canFetch(kind)) {
      writeList(kind, []);
      return Promise.resolve(lists.value[kind]);
    }

    const requestEpoch = epoch;
    pending.value = { ...pending.value, [kind]: true };
    const request = FETCHERS[kind]({ baseUrl })
      .then((rows) => {
        // The credential changed while this was in flight - this response
        // belongs to the previous session and must not be shown.
        if (requestEpoch !== epoch) return [];
        if (!Array.isArray(rows)) {
          console.warn(
            `[entityLists] unexpected ${kind} response; expected an array:`,
            rows,
          );
        }
        writeList(kind, Array.isArray(rows) ? rows : []);
        return lists.value[kind];
      })
      .catch((err) => {
        console.warn(
          `[entityLists] failed to fetch ${kind} (baseUrl=${baseUrl || "relative"}):`,
          err,
        );
        // Keep whatever was cached: a stale list is still usable, and blanking
        // it would turn a transient network error into an empty menu.
        return requestEpoch === epoch ? lists.value[kind] : [];
      })
      .finally(() => {
        // Identity-checked: a pre-reset request can settle AFTER a post-reset
        // one for the same kind, and an unconditional delete would evict the
        // successor's dedup entry and cost a redundant third request. (No stale
        // write is possible either way - the epoch guard above covers that.)
        if (inFlight.get(kind) === request) inFlight.delete(kind);
        if (requestEpoch === epoch) {
          pending.value = { ...pending.value, [kind]: false };
        }
      });
    inFlight.set(kind, request);
    return request;
  }

  /**
   * Refetch after something may have changed the lists server-side.
   *
   * Refetch-ONLY by design: a `characters_changed` WebSocket event, or this
   * client's own create/rename/delete, may say "ask again" but may never hand
   * this store its new contents. The cache is deliberately left on screen while
   * the refetch runs, so an event never blanks an open menu.
   *
   * @param {string[]} [kinds=ENTITY_KINDS]
   * @param {Object} [options]
   * @param {string} [options.baseUrl=""]
   * @returns {Promise<Array[]>}
   */
  function invalidate(kinds = ENTITY_KINDS, options = {}) {
    const targets = Array.isArray(kinds) ? kinds : [kinds];
    return Promise.all(
      targets.map((kind) => {
        const existing = inFlight.get(kind);
        if (!existing) return refresh(kind, options);

        trailingInvalidations.set(kind, options);
        return existing.then(() => {
          // Another invalidation callback may already have started the shared
          // trailing read. Join it instead of creating a third request.
          const successor = inFlight.get(kind);
          if (successor) return successor;
          const trailing = trailingInvalidations.get(kind);
          if (!trailing) return lists.value[kind] ?? [];
          trailingInvalidations.delete(kind);
          return refresh(kind, trailing);
        });
      }),
    );
  }

  /**
   * Drop the whole cache. Called on every auth-context transition.
   *
   * Bumping the epoch is the load-bearing part: it both discards responses
   * already in flight and guarantees the next render sees an empty list rather
   * than the previous credential's.
   */
  function reset() {
    epoch += 1;
    inFlight.clear();
    trailingInvalidations.clear();
    lists.value = emptyLists();
    fetchedAt.value = zeroed();
    pending.value = falsed();
  }

  // Logout / login / share-token entry / vault switch all funnel through the
  // one chokepoint in apiClient, so there is no second mechanism to keep in
  // sync. Unregistered with the pinia instance that owns this store.
  const unsubscribe = onSessionReset(reset);
  onScopeDispose(() => unsubscribe());

  return {
    // state
    lists,
    fetchedAt,
    pending,
    // getters
    characters,
    pictureSets,
    projects,
    canSeeProjects,
    has,
    isLoading,
    // actions
    refresh,
    invalidate,
    reset,
  };
});
