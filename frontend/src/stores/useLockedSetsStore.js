// useLockedSetsStore.js - which pictures are frozen by a locked picture set.
//
// A picture set can be "locked" to freeze its label data (see the picture-set
// locking spec). A picture in at least one locked set is read-only everywhere
// it appears, so every surface that shows a picture (grid badge, overlay chip,
// context-menu gating) needs a cheap, shared answer to "is this picture locked,
// and by which set(s)?".
//
// Grid picture objects do not carry their set ids (Picture.grid_fields() omits
// them), so instead of widening every grid payload we keep one small store fed
// by GET /picture_sets/locked-members - it only touches locked sets, which are
// few. The store is refreshed on app start and on the same triggers the sidebar
// uses (a sidebar refresh / a CHANGED_PICTURES → pictures_changed ws event),
// wired from App.vue.

import { ref, computed, onScopeDispose } from "vue";
import { defineStore } from "pinia";
import { getLockedMembers } from "../api/pictureSets";
import { onSessionReset } from "../utils/apiClient";

// The single source of truth for the lock tooltip copy. Reused verbatim by the
// grid badge, the overlay chip, and the context-menu gating so the "why is this
// read-only / how do I unlock" wording never drifts between surfaces. Multiple
// locking sets are joined with commas.
export function buildLockReason(setNames) {
  const names = (Array.isArray(setNames) ? setNames : [])
    .filter((n) => n != null && String(n).length > 0)
    .map((n) => String(n));
  if (!names.length) return "";
  const joined = names.join(", ");
  return (
    `Locked - this picture is in the locked set '${joined}'. To edit it, ` +
    `unlock the set: right-click the set in the sidebar and choose Unlock, ` +
    `or untick Locked in Edit set.`
  );
}

// Tooltip copy for a picture shown only as a *reference* (a review twin /
// neighbour) that happens to be in a locked set. It explains why the reference
// carries no controls - distinct from `buildLockReason`, which tells the user
// how to unlock an editable-but-frozen picture. Single-sourced so the review
// cards never re-word it. Multiple locking sets are joined with commas.
export function buildReferenceReason(setNames) {
  const names = (Array.isArray(setNames) ? setNames : [])
    .filter((n) => n != null && String(n).length > 0)
    .map((n) => String(n));
  if (!names.length) return "";
  return `Reference only - this picture is in the locked set '${names.join(", ")}'.`;
}

export const useLockedSetsStore = defineStore("lockedSets", () => {
  // Raw payload: [{ id, name, picture_ids: [...] }] for every locked set.
  const sets = ref([]);

  // Coalesce overlapping refreshes: a burst of triggers (app start + an
  // immediate ws echo) collapses into at most one in-flight request plus one
  // trailing refetch, never a fetch storm.
  let inFlight = false;
  let refetchQueued = false;

  // Bumped by reset(). `GET /picture_sets/locked-members` is a scope-aware list
  // (`_LIST_AWARE` in pixlstash/authz/registry.py), so a response that was
  // already on the wire when the credential changed describes the PREVIOUS
  // one's locked sets. Tagging each request with the epoch it started in lets
  // the late response be dropped instead of written into the new session.
  let epoch = 0;

  // pictureId (Number) -> [locking set name, ...]. Built once per `sets`
  // change; every lookup below is then O(1).
  const pictureSetNames = computed(() => {
    const map = new Map();
    for (const s of sets.value) {
      const name = s?.name ?? "";
      for (const pid of s?.picture_ids || []) {
        const key = Number(pid);
        if (!Number.isFinite(key)) continue;
        const existing = map.get(key);
        if (existing) existing.push(name);
        else map.set(key, [name]);
      }
    }
    return map;
  });

  // Ids of all locked sets - for greying/locking set rows in the sidebar and
  // in the add-to-set control.
  const lockedSetIds = computed(
    () => new Set(sets.value.map((s) => s?.id).filter((id) => id != null)),
  );

  function isLocked(pictureId) {
    if (pictureId == null) return false;
    return pictureSetNames.value.has(Number(pictureId));
  }

  function lockedSetNames(pictureId) {
    if (pictureId == null) return [];
    return pictureSetNames.value.get(Number(pictureId)) || [];
  }

  // Ready-made tooltip string for a picture, or "" when it is not locked.
  function lockReason(pictureId) {
    return buildLockReason(lockedSetNames(pictureId));
  }

  async function fetch() {
    if (inFlight) {
      refetchQueued = true;
      return;
    }
    inFlight = true;
    const requestEpoch = epoch;
    try {
      const data = (await getLockedMembers()) ?? {};
      // The credential changed while this was in flight - these are the
      // previous session's locked sets and must not be shown to the next one.
      if (requestEpoch !== epoch) return;
      sets.value = Array.isArray(data.sets) ? data.sets : [];
    } catch (e) {
      // Lock badges/gating are advisory over a hard server-side 423 guard, so a
      // failed refresh must never break the grid - log and keep the last known
      // state rather than clearing it (which would silently drop lock badges).
      console.warn(
        "useLockedSetsStore: failed to load locked members; keeping last state",
        e,
      );
    } finally {
      if (requestEpoch === epoch) {
        inFlight = false;
        if (refetchQueued) {
          refetchQueued = false;
          fetch();
        }
      }
    }
  }

  /**
   * Drop the cached locked sets. Called on every auth-context transition.
   *
   * The epoch bump is the load-bearing half: without it a read that was already
   * on the wire would repopulate `sets` moments after the clear.
   */
  function reset() {
    epoch += 1;
    inFlight = false;
    refetchQueued = false;
    sets.value = [];
  }

  // Logout / login / share-token entry all funnel through the one chokepoint in
  // apiClient, so there is no second mechanism to keep in sync (issue #655).
  const unsubscribeSessionReset = onSessionReset(reset);
  onScopeDispose(() => unsubscribeSessionReset());

  return {
    sets,
    pictureSetNames,
    lockedSetIds,
    isLocked,
    lockedSetNames,
    lockReason,
    fetch,
    reset,
  };
});
