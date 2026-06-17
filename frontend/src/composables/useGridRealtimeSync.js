// useGridRealtimeSync — owns the WebSocket → grid update decision table.
//
// App.vue keeps only the socket lifecycle (connect / reconnect / close /
// set_filters). Every incoming message is handed to `handleMessage(payload)`
// here, which decides between:
//   - suppressing an echo of this tab's own optimistic op,
//   - a targeted in-place op (insert / refresh / reposition / remove),
//   - raising one of the two pills (new pictures / sort changed externally),
//   - or, rarely, a logged full reload.
//
// All dependencies (stores, grid imperative API, predicates, logger) are
// injected so the decision table can be unit-tested without a real grid or a
// live Pinia instance.

const TARGETED = "targeted";
const PILL = "pill";
const SUPPRESSED = "suppressed";
const RELOAD = "reload";
const DEFERRED = "deferred";
const IGNORED = "ignored";

// Above this many ids, the per-id targeted-update loop (one /metadata +
// thumbnail fetch each) becomes a fetch storm. A foreign-tab "set project on
// 400 pictures" or "apply scores" lands here; doing one reload (or deferring it
// under an open overlay) is cheaper than hundreds of single-card refreshes.
const MAX_TARGETED_UPDATE = 50;

// Server-computed sort fields whose value the originating tab can only guess
// optimistically; even an own-origin echo for these needs a single-card
// reconcile under the matching sort so the optimistic guess can't diverge from
// server truth.
const SERVER_COMPUTED_SORT_FIELDS = new Set(["smart_score", "character_likeness"]);

function asPictureIds(payload) {
  return Array.isArray(payload?.picture_ids) ? payload.picture_ids : [];
}

function normaliseSource(payload) {
  // `picture_imported`'s source migrates from "user" to "ui"; accept both.
  const source = payload?.source;
  if (source === "ui" || source === "user") return "ui";
  if (source === "external") return "external";
  // No source → treat as external (the conservative default for unattributed
  // background work).
  return "external";
}

function resolveChangeKind(payload) {
  const kind = payload?.change_kind;
  if (kind === "added" || kind === "updated" || kind === "removed") return kind;
  // `picture_imported` is implicitly an addition.
  if (payload?.type === "picture_imported") return "added";
  return "updated";
}

/**
 * @param {Object} deps
 * @param {() => string|null} deps.getMyClientId   Active tab's client id.
 * @param {Object} deps.grid                        Imperative grid API (the ImageGrid template-ref's exposed methods, or spies in tests). Expected: insertGridImagesById, refreshGridImage, repositionImageByScore, repositionImageBySmartScore, removeImagesById, isImagesLoading.
 * @param {Object} deps.wsStore                     useWsStore instance (pill ids + setters).
 * @param {(fields: string[]) => boolean} deps.pictureChangeAffectsView
 * @param {() => string} deps.getSelectedSort       Active sort key (e.g. "SMART_SCORE", "DATE_TAKEN").
 * @param {Object} [deps.logger]                    console-like; defaults to console.
 * @param {() => void} [deps.reload]                Full-reload fallback.
 * @param {() => void} [deps.refreshSidebar]        Sidebar picture-count refresh.
 */
export function useGridRealtimeSync(deps) {
  const {
    getMyClientId,
    grid,
    wsStore,
    pictureChangeAffectsView,
    getSelectedSort,
    logger = console,
    reload = () => {},
    refreshSidebar = () => {},
  } = deps;

  function isOverlayOpen() {
    return grid.isOverlayOpen?.() === true;
  }

  // While the lightbox overlay is open the grid sequence is frozen for
  // navigation (see ImageOverlay's frozen filmstrip). A pill would either flash
  // a "sort order changed" / "new pictures" prompt for the user's own in-overlay
  // edits or reshuffle the grid under them. Instead, flag a deferred in-place
  // reconcile that ImageGrid.closeOverlay() runs on close (refetch → re-filter +
  // re-sort), and raise no pill. Returns true when the change was deferred.
  function deferWhileOverlayOpen() {
    if (!isOverlayOpen()) return false;
    grid.markOverlayDeferredRefresh?.();
    return true;
  }

  // A change we can't (or shouldn't) apply with a targeted op: an untargetable
  // empty-id add/update, or a batch too large for the per-id loop. Reload the
  // grid, or defer that reload to overlay close so we never reshuffle the grid
  // under the lightbox. Returns a `{ action, reason }` descriptor.
  function reloadOrDefer(reason) {
    if (deferWhileOverlayOpen()) {
      logger.warn?.(
        `useGridRealtimeSync: deferring grid reload under open overlay (${reason})`,
      );
      return { action: DEFERRED, reason: `${reason}-overlay-deferred` };
    }
    logger.warn?.(`useGridRealtimeSync: full grid reload (${reason})`);
    reload();
    return { action: RELOAD, reason };
  }

  function isSmartScoreSort() {
    return String(getSelectedSort() || "").includes("SMART_SCORE");
  }

  function isLikenessSort() {
    const sort = String(getSelectedSort() || "");
    return sort.includes("CHARACTER_LIKENESS") || sort.includes("LIKENESS");
  }

  // True when one of the changed fields is a server-computed sort field that is
  // also the active sort — the only case where an own-origin echo still needs a
  // single-card reconcile.
  function fieldsAreActiveServerSort(fields) {
    if (!Array.isArray(fields) || !fields.length) return false;
    return fields.some((field) => {
      if (!SERVER_COMPUTED_SORT_FIELDS.has(field)) return false;
      if (field === "smart_score") return isSmartScoreSort();
      if (field === "character_likeness") return isLikenessSort();
      return false;
    });
  }

  // Reconcile a single card under the active sort. The WS event never carries
  // the new server-computed value, so for smart-score we fetch-then-reposition
  // (refreshSmartScoreForImage does both with the true value); otherwise we
  // just refresh the card's metadata in place.
  function reconcileServerSortField(id, fields) {
    if (fields.includes("smart_score") && isSmartScoreSort()) {
      grid.refreshSmartScoreForImage?.(id);
      return;
    }
    grid.refreshGridImage?.(id);
  }

  function applyTargetedUpdate(id, fields) {
    if (fields.includes("smart_score") && isSmartScoreSort()) {
      // Fetch the true smart_score and reposition off it.
      grid.refreshSmartScoreForImage?.(id);
      return;
    }
    // Any other change (incl. a score change): refresh the card's metadata in
    // place. refreshGridImage re-fetches so the new value is reflected.
    grid.refreshGridImage?.(id);
  }

  // --- Echo of this tab's own optimistic op -------------------------------
  function handleOwnOrigin(payload, changeKind, fields, pictureIds) {
    if (changeKind === "updated" && fieldsAreActiveServerSort(fields)) {
      // Optimistic guess for a server-computed sort field can diverge from
      // server truth — reconcile each card, never reload. Cap the per-id
      // fetch loop: a large own-origin batch reconcile becomes a fetch storm.
      if (pictureIds.length > MAX_TARGETED_UPDATE) {
        return reloadOrDefer("own-origin-server-sort-reconcile-too-large");
      }
      for (const id of pictureIds) reconcileServerSortField(id, fields);
      return { action: TARGETED, reason: "own-origin-server-sort-reconcile" };
    }
    return { action: SUPPRESSED, reason: "own-origin-echo" };
  }

  // --- Owner UI change from a different tab -------------------------------
  function handleForeignUi(payload, changeKind, fields, pictureIds) {
    if (changeKind === "removed") {
      grid.removeImagesById?.(pictureIds);
      return { action: TARGETED, reason: "foreign-ui-removed" };
    }
    // An empty-id add/update can't be targeted (e.g. restore-all broadcasts
    // change_kind:"added" with picture_ids:[]). The per-id ops below would be
    // silent no-ops, so other owner tabs would never reflect the change. Reload
    // (or defer under an open overlay) rather than no-op.
    if (!pictureIds.length) {
      return reloadOrDefer("foreign-ui-untargetable-empty-ids");
    }
    if (changeKind === "added") {
      if (deferWhileOverlayOpen()) {
        return { action: TARGETED, reason: "foreign-ui-added-overlay-deferred" };
      }
      if (grid.isImagesLoading?.()) {
        // Streaming fetch owns allGridImages; defer to the pill.
        wsStore.addPendingExternalImportIds?.(pictureIds);
        return { action: PILL, reason: "foreign-ui-added-during-load" };
      }
      grid.insertGridImagesById?.(pictureIds);
      return { action: TARGETED, reason: "foreign-ui-added" };
    }
    // updated
    if (!pictureChangeAffectsView(fields)) {
      return { action: IGNORED, reason: "foreign-ui-updated-irrelevant" };
    }
    // score/project (and any other view-affecting field) always render under the
    // current sort, so we can't skip these — but each applyTargetedUpdate is a
    // /metadata + thumbnail fetch. A large foreign batch (apply-scores /
    // set-project on hundreds of pictures) would be a fetch storm; cap it with a
    // single reload (or defer under an open overlay) instead of the per-id loop.
    if (pictureIds.length > MAX_TARGETED_UPDATE) {
      return reloadOrDefer("foreign-ui-updated-too-large");
    }
    for (const id of pictureIds) applyTargetedUpdate(id, fields);
    return { action: TARGETED, reason: "foreign-ui-updated" };
  }

  // --- Change from outside the UI -----------------------------------------
  function handleExternal(payload, changeKind, fields, pictureIds) {
    if (changeKind === "removed") {
      // Never leave a stale 404-clickable card; remove silently.
      grid.removeImagesById?.(pictureIds);
      return { action: TARGETED, reason: "external-removed" };
    }
    // An empty-id add/update can't be targeted (e.g. a restore-all broadcast, or
    // an unattributed bulk change with no ids). The pill / per-id branches below
    // would no-op on [], so the change would be lost. Reload (or defer under an
    // open overlay) rather than silently drop it.
    if (!pictureIds.length) {
      return reloadOrDefer("external-untargetable-empty-ids");
    }
    if (changeKind === "added") {
      if (deferWhileOverlayOpen()) {
        return { action: TARGETED, reason: "external-added-overlay-deferred" };
      }
      wsStore.addPendingExternalImportIds?.(pictureIds);
      return { action: PILL, reason: "external-added" };
    }
    // updated
    if (pictureChangeAffectsView(fields)) {
      if (deferWhileOverlayOpen()) {
        // The classic case: the user's own tag edit kicks off a background
        // smart_score recompute that arrives origin-less (external) and would
        // raise the "sort order changed" pill. Defer it to overlay close.
        return {
          action: TARGETED,
          reason: "external-updated-sort-affecting-overlay-deferred",
        };
      }
      // Would reshuffle the grid — raise the pill instead of moving cards under
      // the user.
      wsStore.addSortChangedExternalIds?.(pictureIds);
      return { action: PILL, reason: "external-updated-sort-affecting" };
    }
    // The changed fields are known and invisible to the current sort/filter
    // (the only way to reach here — empty/unknown fields make affectsView true).
    // The classic case is a background `smart_score` recompute under a date
    // sort. Skip entirely, exactly as the old App.vue handler did: a per-id
    // refresh would fire a /metadata + thumbnail fetch for every affected card
    // in the view (a fetch storm on a full-library recompute) to update a value
    // that isn't even displayed under the current sort/filter.
    return { action: IGNORED, reason: "external-updated-invisible-field" };
  }

  /**
   * Apply a single picture-mutation event. Returns a `{ action, reason }`
   * descriptor (used by tests; App.vue ignores the return).
   */
  function handlePictureEvent(payload) {
    const pictureIds = asPictureIds(payload);
    if (!pictureIds.length && payload?.type !== "pictures_changed") {
      return { action: IGNORED, reason: "no-picture-ids" };
    }
    const myClientId = getMyClientId();
    const originClientId = payload?.origin_client_id ?? null;
    const source = normaliseSource(payload);
    const changeKind = resolveChangeKind(payload);
    const fields = Array.isArray(payload?.fields) ? payload.fields : [];

    if (originClientId && myClientId && originClientId === myClientId) {
      return handleOwnOrigin(payload, changeKind, fields, pictureIds);
    }
    if (source === "ui") {
      return handleForeignUi(payload, changeKind, fields, pictureIds);
    }
    if (source === "external") {
      return handleExternal(payload, changeKind, fields, pictureIds);
    }

    // Unrecognised shape (e.g. bulk sort/filter-defining change) → rare,
    // logged full reload.
    logger.warn?.(
      "useGridRealtimeSync: falling back to full reload for unrecognised event",
      { type: payload?.type, source, changeKind },
    );
    reload();
    return { action: RELOAD, reason: "fallback" };
  }

  /**
   * Entry point for any /ws/updates message that concerns the picture grid.
   * App.vue routes the picture-change message types here and handles the
   * remaining (tags / descriptions / characters / snapshots / plugin) branches.
   */
  function handleMessage(payload) {
    const type = payload?.type;
    const isPictureChange =
      type === "pictures_changed" || type === "picture_imported";
    if (!isPictureChange) {
      return { action: IGNORED, reason: "not-a-picture-event" };
    }

    const affectsView = pictureChangeAffectsView(payload?.fields);

    // The sidebar picture-count only changes for adds/removes or changes that
    // affect the view; skip the churn for pure background recomputes that the
    // current sort/filter ignore (preserves the smart-score-under-date-sort
    // optimisation for the sidebar).
    const changeKind = resolveChangeKind(payload);
    const touchesSidebar =
      affectsView || changeKind === "added" || changeKind === "removed";
    if (!wsStore.isUploadInProgress && touchesSidebar) {
      refreshSidebar(true);
    }

    return handlePictureEvent(payload);
  }

  return {
    handleMessage,
    // Exposed for finer-grained tests and reuse.
    handlePictureEvent,
  };
}

export const __testing = {
  normaliseSource,
  resolveChangeKind,
  asPictureIds,
};
