import { describe, it, expect, beforeEach, vi } from "vitest";
import { useGridRealtimeSync } from "./useGridRealtimeSync.js";

const MY_ID = "my-tab-uuid";
const OTHER_ID = "other-tab-uuid";

function makeHarness(overrides = {}) {
  const grid = {
    insertGridImagesById: vi.fn(),
    refreshGridImage: vi.fn(),
    refreshStackFacets: vi.fn(),
    refreshThumbnailUrls: vi.fn(),
    applyRotatedCards: vi.fn(),
    repositionImageByScore: vi.fn(),
    repositionImageBySmartScore: vi.fn(),
    refreshSmartScoreForImage: vi.fn(),
    removeImagesById: vi.fn(),
    isImagesLoading: vi.fn(() => false),
    isOverlayOpen: vi.fn(() => overrides.overlayOpen === true),
    markOverlayDeferredRefresh: vi.fn(),
  };
  const wsStore = {
    isUploadInProgress: false,
    addPendingExternalImportIds: vi.fn(),
    addSortChangedExternalIds: vi.fn(),
  };
  const reload = vi.fn();
  const refreshSidebar = vi.fn();
  // Mirror App.vue's pictureChangeAffectsView: empty/absent fields => affects
  // view; otherwise depends on whether a field is sort-relevant.
  const selectedSort = { value: overrides.selectedSort ?? "DATE_TAKEN" };
  const pictureChangeAffectsView = vi.fn((fields) => {
    if (!Array.isArray(fields) || fields.length === 0) return true;
    return fields.some((f) => {
      if (f === "smart_score")
        return selectedSort.value.includes("SMART_SCORE");
      if (f === "character_likeness")
        return selectedSort.value.includes("CHARACTER_LIKENESS");
      // Detections are an opt-in overlay layer, never a sort/filter field -
      // mirror App.vue.pictureChangeFieldAffectsView.
      if (f === "detections") return false;
      if (f === "pixels") return false;
      return true; // unknown field assumed relevant
    });
  });

  const sync = useGridRealtimeSync({
    getMyClientId: () => MY_ID,
    grid,
    wsStore,
    pictureChangeAffectsView,
    getSelectedSort: () => selectedSort.value,
    logger: { warn: vi.fn() },
    reload,
    refreshSidebar,
  });

  return { sync, grid, wsStore, reload, refreshSidebar, selectedSort };
}

describe("useGridRealtimeSync decision table", () => {
  let h;
  beforeEach(() => {
    h = makeHarness();
  });

  it("suppresses an echo of this tab's own optimistic op", () => {
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "ui",
      origin_client_id: MY_ID,
      picture_ids: [1, 2],
      change_kind: "updated",
      fields: ["tags"],
    });
    expect(res.action).toBe("suppressed");
    expect(h.grid.refreshGridImage).not.toHaveBeenCalled();
    expect(h.grid.removeImagesById).not.toHaveBeenCalled();
    expect(h.reload).not.toHaveBeenCalled();
  });

  it("reconciles own-origin smart_score echo under a smart-score sort (no reload)", () => {
    h.selectedSort.value = "SMART_SCORE";
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "ui",
      origin_client_id: MY_ID,
      picture_ids: [7],
      change_kind: "updated",
      fields: ["smart_score"],
    });
    expect(res.action).toBe("targeted");
    expect(h.grid.refreshSmartScoreForImage).toHaveBeenCalledWith(7);
    expect(h.reload).not.toHaveBeenCalled();
  });

  it("foreign-ui added -> inserts at sorted position + highlight", () => {
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "ui",
      origin_client_id: OTHER_ID,
      picture_ids: [10, 11],
      change_kind: "added",
    });
    expect(res.action).toBe("targeted");
    expect(h.grid.insertGridImagesById).toHaveBeenCalledWith([10, 11]);
  });

  it("foreign-ui added during a streaming fetch -> defers to the pill", () => {
    h.grid.isImagesLoading.mockReturnValue(true);
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "ui",
      origin_client_id: OTHER_ID,
      picture_ids: [10],
      change_kind: "added",
    });
    expect(res.action).toBe("pill");
    expect(h.grid.insertGridImagesById).not.toHaveBeenCalled();
    expect(h.wsStore.addPendingExternalImportIds).toHaveBeenCalledWith([10]);
  });

  it("foreign-ui updated (relevant fields) -> refreshGridImage per id", () => {
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "ui",
      origin_client_id: OTHER_ID,
      picture_ids: [3, 4],
      change_kind: "updated",
      fields: ["tags"],
    });
    expect(res.action).toBe("targeted");
    expect(h.grid.refreshGridImage).toHaveBeenCalledWith(3);
    expect(h.grid.refreshGridImage).toHaveBeenCalledWith(4);
  });

  it("foreign-ui updated with sort-field change -> reposition", () => {
    h.selectedSort.value = "SMART_SCORE";
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "ui",
      origin_client_id: OTHER_ID,
      picture_ids: [5],
      change_kind: "updated",
      fields: ["smart_score"],
    });
    expect(res.action).toBe("targeted");
    expect(h.grid.refreshSmartScoreForImage).toHaveBeenCalledWith(5);
  });

  it("foreign-ui updated with view-irrelevant fields -> ignored", () => {
    // smart_score field under a DATE sort does not affect the view.
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "ui",
      origin_client_id: OTHER_ID,
      picture_ids: [6],
      change_kind: "updated",
      fields: ["smart_score"],
    });
    expect(res.action).toBe("ignored");
    expect(h.grid.refreshGridImage).not.toHaveBeenCalled();
  });

  it("foreign-ui removed -> removeImagesById", () => {
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "ui",
      origin_client_id: OTHER_ID,
      picture_ids: [8, 9],
      change_kind: "removed",
    });
    expect(res.action).toBe("targeted");
    expect(h.grid.removeImagesById).toHaveBeenCalledWith([8, 9]);
  });

  it("external added -> New pictures pill", () => {
    const res = h.sync.handleMessage({
      type: "picture_imported",
      source: "external",
      origin_client_id: null,
      picture_ids: [20, 21],
    });
    expect(res.action).toBe("pill");
    expect(h.wsStore.addPendingExternalImportIds).toHaveBeenCalledWith([
      20, 21,
    ]);
    expect(h.grid.insertGridImagesById).not.toHaveBeenCalled();
  });

  it("external updated, sort-affecting -> Sort-order pill (no reshuffle)", () => {
    h.selectedSort.value = "SMART_SCORE";
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "external",
      origin_client_id: null,
      picture_ids: [30],
      change_kind: "updated",
      fields: ["smart_score"],
    });
    expect(res.action).toBe("pill");
    expect(h.wsStore.addSortChangedExternalIds).toHaveBeenCalledWith([30]);
    expect(h.grid.refreshGridImage).not.toHaveBeenCalled();
  });

  it("external updated, invisible field -> ignored (no fetch storm)", () => {
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "external",
      origin_client_id: null,
      picture_ids: [31],
      change_kind: "updated",
      fields: ["smart_score"], // under a DATE sort => invisible to the view
    });
    // A background recompute of a field that isn't displayed under the current
    // sort/filter must not trigger a per-card refetch across the whole view.
    expect(res.action).toBe("ignored");
    expect(h.grid.refreshGridImage).not.toHaveBeenCalled();
    expect(h.wsStore.addSortChangedExternalIds).not.toHaveBeenCalled();
  });

  it("external removed -> silent removal (never a 404 card)", () => {
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "external",
      origin_client_id: null,
      picture_ids: [40],
      change_kind: "removed",
    });
    expect(res.action).toBe("targeted");
    expect(h.grid.removeImagesById).toHaveBeenCalledWith([40]);
  });

  it("accepts legacy source 'user' as ui (transition compatibility)", () => {
    const res = h.sync.handleMessage({
      type: "picture_imported",
      source: "user",
      origin_client_id: OTHER_ID,
      picture_ids: [50],
    });
    // legacy 'user' from a different origin behaves as foreign-ui added.
    expect(res.action).toBe("targeted");
    expect(h.grid.insertGridImagesById).toHaveBeenCalledWith([50]);
  });

  // Expectation deliberately inverted. This asserted suppression, on the same
  // assumption the `restored` branch already had to walk back: that an
  // optimistic local op had applied the change. For an add there cannot have
  // been one -- the grid cannot insert a picture whose id the server assigns on
  // commit. A paste, or a drop outside the grid, goes through the sidebar
  // importer, which reports no per-file results because the grid is supposed to
  // refresh off this very broadcast. Suppressing it left a pasted picture
  // invisible until the view was switched away and back.
  it("own-origin import echo is applied, not suppressed", () => {
    const res = h.sync.handleMessage({
      type: "picture_imported",
      source: "ui",
      origin_client_id: MY_ID,
      picture_ids: [60],
    });
    expect(res.action).toBe("targeted");
    expect(res.reason).toBe("own-origin-added");
    expect(h.grid.insertGridImagesById).toHaveBeenCalledWith([60]);
  });

  it("still suppresses a genuine own-origin echo of an optimistic update", () => {
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "ui",
      origin_client_id: MY_ID,
      picture_ids: [61],
      change_kind: "updated",
      fields: ["tags"],
    });
    expect(res.action).toBe("suppressed");
  });

  it("does not refresh the sidebar for a view-irrelevant external update", () => {
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "external",
      origin_client_id: null,
      picture_ids: [70],
      change_kind: "updated",
      // smart_score under a DATE sort => no effect on the view: the event is
      // ignored entirely and the sidebar counts don't change.
      fields: ["smart_score"],
    });
    expect(res.action).toBe("ignored");
    expect(h.refreshSidebar).not.toHaveBeenCalled();
  });

  it("refreshes the sidebar for an affecting picture event", () => {
    h.sync.handleMessage({
      type: "pictures_changed",
      source: "ui",
      origin_client_id: OTHER_ID,
      picture_ids: [80],
      change_kind: "updated",
      fields: ["tags"],
    });
    expect(h.refreshSidebar).toHaveBeenCalledWith(true);
  });

  it("ignores non-picture event types", () => {
    const res = h.sync.handleMessage({ type: "characters_changed" });
    expect(res.action).toBe("ignored");
    expect(res.reason).toBe("not-a-picture-event");
  });
});

describe("useGridRealtimeSync - card-content-only updates (detections)", () => {
  it("own-origin detections echo -> targeted refresh per id (not suppressed)", () => {
    const h = makeHarness();
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "ui",
      origin_client_id: MY_ID,
      picture_ids: [1, 2],
      change_kind: "updated",
      fields: ["detections"],
    });
    expect(res.action).toBe("targeted");
    expect(res.reason).toBe("card-content-refresh");
    expect(h.grid.refreshGridImage).toHaveBeenCalledWith(1);
    expect(h.grid.refreshGridImage).toHaveBeenCalledWith(2);
    expect(h.reload).not.toHaveBeenCalled();
  });

  it("external detections update -> targeted refresh per id (not ignored)", () => {
    const h = makeHarness();
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "external",
      origin_client_id: null,
      picture_ids: [3, 4],
      change_kind: "updated",
      fields: ["detections"],
    });
    expect(res.action).toBe("targeted");
    expect(res.reason).toBe("card-content-refresh");
    expect(h.grid.refreshGridImage).toHaveBeenCalledWith(3);
    expect(h.grid.refreshGridImage).toHaveBeenCalledWith(4);
    expect(h.wsStore.addSortChangedExternalIds).not.toHaveBeenCalled();
    // A detections-only update is not view-affecting, so the sidebar count
    // must not churn.
    expect(h.refreshSidebar).not.toHaveBeenCalled();
  });

  it("foreign-ui detections update -> targeted refresh per id", () => {
    const h = makeHarness();
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "ui",
      origin_client_id: OTHER_ID,
      picture_ids: [5],
      change_kind: "updated",
      fields: ["detections"],
    });
    expect(res.action).toBe("targeted");
    expect(res.reason).toBe("card-content-refresh");
    expect(h.grid.refreshGridImage).toHaveBeenCalledWith(5);
  });

  it("detections update over the cap -> single reload (no per-id storm)", () => {
    const h = makeHarness();
    const manyIds = Array.from({ length: 51 }, (_, i) => i + 1);
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "external",
      origin_client_id: null,
      picture_ids: manyIds,
      change_kind: "updated",
      fields: ["detections"],
    });
    expect(res.action).toBe("reload");
    expect(res.reason).toBe("card-content-refresh-too-large");
    expect(h.reload).toHaveBeenCalledTimes(1);
    expect(h.grid.refreshGridImage).not.toHaveBeenCalled();
  });

  it("detections update while overlay open -> deferred, no immediate refresh", () => {
    const h = makeHarness({ overlayOpen: true });
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "ui",
      origin_client_id: MY_ID,
      picture_ids: [6, 7],
      change_kind: "updated",
      fields: ["detections"],
    });
    expect(res.action).toBe("targeted");
    expect(res.reason).toBe("card-content-refresh-overlay-deferred");
    expect(h.grid.refreshGridImage).not.toHaveBeenCalled();
    expect(h.grid.markOverlayDeferredRefresh).toHaveBeenCalledTimes(1);
  });

  it("mixed fields (detections + a view field) follow the OLD path, not the new branch", () => {
    const h = makeHarness();
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "ui",
      origin_client_id: OTHER_ID,
      picture_ids: [8],
      change_kind: "updated",
      // `project` is view-affecting -> this is NOT card-content-only, so it must
      // take the normal foreign-ui targeted-update path.
      fields: ["detections", "project"],
    });
    expect(res.action).toBe("targeted");
    expect(res.reason).toBe("foreign-ui-updated");
    expect(h.grid.refreshGridImage).toHaveBeenCalledWith(8);
  });

  it("a non-card-content field (smart_score) is not swallowed by the new branch", () => {
    const h = makeHarness({ selectedSort: "SMART_SCORE" });
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "external",
      origin_client_id: null,
      picture_ids: [9],
      change_kind: "updated",
      fields: ["smart_score"],
    });
    // Still the old external sort-affecting pill, not a card-content refresh.
    expect(res.action).toBe("pill");
    expect(res.reason).toBe("external-updated-sort-affecting");
    expect(h.wsStore.addSortChangedExternalIds).toHaveBeenCalledWith([9]);
  });
});

// The reported bug: "Keep cover only" collapsed a stack of five to its cover and
// the surviving cover went on rendering a stack badge of five, forever. The
// count is DERIVED per stack by the listing endpoint and is absent from the
// /pictures/{id}/metadata read `refreshGridImage` performs, so the per-card
// refresh every other branch uses cannot repair it, hence a branch, and a grid
// method, of its own. Both directions are covered: the collapse announces the
// covers, and the undo/redo announces the surviving members of every stack the
// lifecycle move touched.
describe("useGridRealtimeSync: stack-facet updates (stack_count)", () => {
  it("own-origin echo is NOT suppressed: the acting tab has no local count", () => {
    const h = makeHarness();
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "ui",
      origin_client_id: MY_ID,
      picture_ids: [1, 2],
      change_kind: "updated",
      fields: ["stack_count"],
    });
    expect(res.action).toBe("targeted");
    expect(res.reason).toBe("stack-facet-refresh");
    // ONE batched read for the whole set, not one per card.
    expect(h.grid.refreshStackFacets).toHaveBeenCalledTimes(1);
    expect(h.grid.refreshStackFacets).toHaveBeenCalledWith([1, 2]);
    // Never the per-card path: it would fetch /metadata, which carries no count.
    expect(h.grid.refreshGridImage).not.toHaveBeenCalled();
    expect(h.reload).not.toHaveBeenCalled();
  });

  it("foreign-ui update converges the second tab", () => {
    const h = makeHarness();
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "ui",
      origin_client_id: OTHER_ID,
      picture_ids: [5],
      change_kind: "updated",
      fields: ["stack_count"],
    });
    expect(res.reason).toBe("stack-facet-refresh");
    expect(h.grid.refreshStackFacets).toHaveBeenCalledWith([5]);
    expect(h.grid.refreshGridImage).not.toHaveBeenCalled();
  });

  it("external update refreshes in place and raises no pill", () => {
    const h = makeHarness();
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "external",
      origin_client_id: null,
      picture_ids: [6],
      change_kind: "updated",
      fields: ["stack_count"],
    });
    expect(res.reason).toBe("stack-facet-refresh");
    expect(h.grid.refreshStackFacets).toHaveBeenCalledWith([6]);
    // A badge is card content, never a reposition: nothing may reshuffle the
    // grid under the user for it.
    expect(h.wsStore.addSortChangedExternalIds).not.toHaveBeenCalled();
    expect(h.wsStore.addPendingExternalImportIds).not.toHaveBeenCalled();
  });

  it("reloads when stack membership changes the active stack-time order", () => {
    const h = makeHarness({ selectedSort: "STACK_UPDATED_AT" });
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "ui",
      origin_client_id: MY_ID,
      picture_ids: [6],
      change_kind: "updated",
      fields: ["stack_count"],
    });

    expect(res.action).toBe("reload");
    expect(res.reason).toBe("stack-time-sort-changed");
    expect(h.reload).toHaveBeenCalledTimes(1);
    expect(h.grid.refreshStackFacets).not.toHaveBeenCalled();
  });

  it("defers a stack-time reorder while the lightbox is open", () => {
    const h = makeHarness({
      selectedSort: "STACK_UPDATED_AT",
      overlayOpen: true,
    });
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "external",
      picture_ids: [6],
      change_kind: "updated",
      fields: ["stack_count"],
    });

    expect(res.action).toBe("deferred");
    expect(res.reason).toBe("stack-time-sort-changed-overlay-deferred");
    expect(h.reload).not.toHaveBeenCalled();
    expect(h.grid.markOverlayDeferredRefresh).toHaveBeenCalledTimes(1);
  });

  it("a large batch stays one read, never an escalated reload", () => {
    const h = makeHarness();
    const manyIds = Array.from({ length: 400 }, (_, i) => i + 1);
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "ui",
      origin_client_id: MY_ID,
      picture_ids: manyIds,
      change_kind: "updated",
      fields: ["stack_count"],
    });
    // The per-id cap exists to stop a fetch storm; there is no storm here, and
    // a reload would drop the ghosted copies the undo window is holding.
    expect(res.reason).toBe("stack-facet-refresh");
    expect(h.reload).not.toHaveBeenCalled();
    expect(h.grid.refreshStackFacets).toHaveBeenCalledTimes(1);
  });

  it("defers under an open overlay, like every other grid mutation", () => {
    const h = makeHarness({ overlayOpen: true });
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "ui",
      origin_client_id: MY_ID,
      picture_ids: [7],
      change_kind: "updated",
      fields: ["stack_count"],
    });
    expect(res.action).toBe("targeted");
    expect(res.reason).toBe("stack-facet-refresh-overlay-deferred");
    expect(h.grid.refreshStackFacets).not.toHaveBeenCalled();
    expect(h.grid.markOverlayDeferredRefresh).toHaveBeenCalledTimes(1);
  });

  it("mixed fields follow the OLD path: a lifted score still needs its sort", () => {
    const h = makeHarness({ selectedSort: "SMART_SCORE" });
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "ui",
      origin_client_id: OTHER_ID,
      picture_ids: [8],
      change_kind: "updated",
      fields: ["stack_count", "smart_score"],
    });
    expect(res.reason).toBe("foreign-ui-updated");
    expect(h.grid.refreshStackFacets).not.toHaveBeenCalled();
  });

  it("a removal is never swallowed by the branch", () => {
    const h = makeHarness();
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "ui",
      origin_client_id: OTHER_ID,
      picture_ids: [9],
      change_kind: "removed",
      fields: ["stack_count"],
    });
    // The copies of a collapse are `removed`, and a vanished card must still
    // vanish; only the covers are `updated`.
    expect(res.reason).toBe("foreign-ui-removed");
    expect(h.grid.refreshStackFacets).not.toHaveBeenCalled();
  });
});

describe("useGridRealtimeSync - pills deferred while the overlay is open", () => {
  it("external sort-affecting update defers to overlay close, raises no pill", () => {
    const h = makeHarness({ selectedSort: "SMART_SCORE", overlayOpen: true });
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "external",
      origin_client_id: null,
      picture_ids: [30],
      change_kind: "updated",
      fields: ["smart_score"],
    });
    expect(res.action).toBe("targeted");
    expect(res.reason).toBe("external-updated-sort-affecting-overlay-deferred");
    expect(h.wsStore.addSortChangedExternalIds).not.toHaveBeenCalled();
    expect(h.grid.markOverlayDeferredRefresh).toHaveBeenCalledTimes(1);
  });

  it("external add defers to overlay close, no New-pictures pill", () => {
    const h = makeHarness({ overlayOpen: true });
    const res = h.sync.handleMessage({
      type: "picture_imported",
      source: "external",
      origin_client_id: null,
      picture_ids: [20, 21],
    });
    expect(res.action).toBe("targeted");
    expect(res.reason).toBe("external-added-overlay-deferred");
    expect(h.wsStore.addPendingExternalImportIds).not.toHaveBeenCalled();
    expect(h.grid.markOverlayDeferredRefresh).toHaveBeenCalledTimes(1);
  });

  it("foreign-ui add defers to overlay close instead of inserting live", () => {
    const h = makeHarness({ overlayOpen: true });
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "ui",
      origin_client_id: OTHER_ID,
      picture_ids: [10, 11],
      change_kind: "added",
    });
    expect(res.action).toBe("targeted");
    expect(res.reason).toBe("foreign-ui-added-overlay-deferred");
    expect(h.grid.insertGridImagesById).not.toHaveBeenCalled();
    expect(h.grid.markOverlayDeferredRefresh).toHaveBeenCalledTimes(1);
  });

  it("still raises the pill for the same event when no overlay is open", () => {
    const h = makeHarness({ selectedSort: "SMART_SCORE", overlayOpen: false });
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "external",
      origin_client_id: null,
      picture_ids: [30],
      change_kind: "updated",
      fields: ["smart_score"],
    });
    expect(res.action).toBe("pill");
    expect(h.wsStore.addSortChangedExternalIds).toHaveBeenCalledWith([30]);
    expect(h.grid.markOverlayDeferredRefresh).not.toHaveBeenCalled();
  });

  it("external removal is NOT deferred while overlay open (no stale 404 card)", () => {
    const h = makeHarness({ overlayOpen: true });
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "external",
      origin_client_id: null,
      picture_ids: [40],
      change_kind: "removed",
    });
    expect(res.action).toBe("targeted");
    expect(res.reason).toBe("external-removed");
    expect(h.grid.removeImagesById).toHaveBeenCalledWith([40]);
  });
});

describe("useGridRealtimeSync - empty-id untargetable changes (restore-all)", () => {
  it("empty-id external add -> reload when the overlay is closed", () => {
    const h = makeHarness();
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "external",
      origin_client_id: null,
      change_kind: "added",
      picture_ids: [],
    });
    expect(res.action).toBe("reload");
    expect(res.reason).toBe("external-untargetable-empty-ids");
    expect(h.reload).toHaveBeenCalledTimes(1);
    expect(h.wsStore.addPendingExternalImportIds).not.toHaveBeenCalled();
  });

  it("empty-id external add -> deferred (no reload) when the overlay is open", () => {
    const h = makeHarness({ overlayOpen: true });
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "external",
      origin_client_id: null,
      change_kind: "added",
      picture_ids: [],
    });
    expect(res.action).toBe("deferred");
    expect(res.reason).toBe("external-untargetable-empty-ids-overlay-deferred");
    expect(h.reload).not.toHaveBeenCalled();
    expect(h.grid.markOverlayDeferredRefresh).toHaveBeenCalledTimes(1);
  });

  it("empty-id foreign-ui add -> reload (other tabs reflect a restore-all)", () => {
    const h = makeHarness();
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "ui",
      origin_client_id: OTHER_ID,
      change_kind: "added",
      picture_ids: [],
    });
    expect(res.action).toBe("reload");
    expect(res.reason).toBe("foreign-ui-untargetable-empty-ids");
    expect(h.reload).toHaveBeenCalledTimes(1);
    expect(h.grid.insertGridImagesById).not.toHaveBeenCalled();
  });

  it("empty-id removal still removes silently (not reloaded)", () => {
    const h = makeHarness();
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "external",
      origin_client_id: null,
      change_kind: "removed",
      picture_ids: [],
    });
    expect(res.action).toBe("targeted");
    expect(res.reason).toBe("external-removed");
    expect(h.reload).not.toHaveBeenCalled();
    expect(h.grid.removeImagesById).toHaveBeenCalledWith([]);
  });
});

describe("useGridRealtimeSync - batch update fetch-storm cap", () => {
  // Mirrors MAX_TARGETED_UPDATE in useGridRealtimeSync.js.
  const THRESHOLD = 50;
  const manyIds = Array.from({ length: THRESHOLD + 1 }, (_, i) => i + 1);
  const someIds = Array.from({ length: THRESHOLD }, (_, i) => i + 1);

  it("foreign-ui updated with >threshold ids -> single reload (no per-id loop)", () => {
    const h = makeHarness();
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "ui",
      origin_client_id: OTHER_ID,
      change_kind: "updated",
      fields: ["score"],
      picture_ids: manyIds,
    });
    expect(res.action).toBe("reload");
    expect(res.reason).toBe("foreign-ui-updated-too-large");
    expect(h.reload).toHaveBeenCalledTimes(1);
    expect(h.grid.refreshGridImage).not.toHaveBeenCalled();
  });

  it("foreign-ui updated with exactly threshold ids -> per-id refresh", () => {
    const h = makeHarness();
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "ui",
      origin_client_id: OTHER_ID,
      change_kind: "updated",
      fields: ["score"],
      picture_ids: someIds,
    });
    expect(res.action).toBe("targeted");
    expect(res.reason).toBe("foreign-ui-updated");
    expect(h.reload).not.toHaveBeenCalled();
    expect(h.grid.refreshGridImage).toHaveBeenCalledTimes(THRESHOLD);
  });

  it("foreign-ui updated with >threshold ids -> deferred when overlay open", () => {
    const h = makeHarness({ overlayOpen: true });
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "ui",
      origin_client_id: OTHER_ID,
      change_kind: "updated",
      fields: ["score"],
      picture_ids: manyIds,
    });
    expect(res.action).toBe("deferred");
    expect(res.reason).toBe("foreign-ui-updated-too-large-overlay-deferred");
    expect(h.reload).not.toHaveBeenCalled();
    expect(h.grid.markOverlayDeferredRefresh).toHaveBeenCalledTimes(1);
    expect(h.grid.refreshGridImage).not.toHaveBeenCalled();
  });
});

// A manual scheduler accumulates flush callbacks and only runs them on
// `tick()`, so a burst of events stays buffered (as it would inside the real
// 200ms debounce in App.vue) until we flush - letting us assert coalescing.
function makeCoalescingHarness(overrides = {}) {
  const grid = {
    insertGridImagesById: vi.fn(),
    refreshGridImage: vi.fn(),
    refreshStackFacets: vi.fn(),
    refreshThumbnailUrls: vi.fn(),
    applyRotatedCards: vi.fn(),
    repositionImageByScore: vi.fn(),
    repositionImageBySmartScore: vi.fn(),
    refreshSmartScoreForImage: vi.fn(),
    removeImagesById: vi.fn(),
    isImagesLoading: vi.fn(() => false),
    isOverlayOpen: vi.fn(() => overrides.overlayOpen === true),
    markOverlayDeferredRefresh: vi.fn(),
  };
  const wsStore = {
    isUploadInProgress: false,
    addPendingExternalImportIds: vi.fn(),
    addSortChangedExternalIds: vi.fn(),
  };
  const reload = vi.fn();
  const refreshSidebar = vi.fn();
  const selectedSort = { value: overrides.selectedSort ?? "DATE_TAKEN" };
  const pictureChangeAffectsView = vi.fn(() => true);

  // Window-style scheduler: the first event arms one pending flush; the manual
  // tick() runs it, mirroring the leading-edge fixed window in App.vue.
  let pending = null;
  const scheduler = {
    schedule: (flush) => {
      pending = flush;
    },
    cancel: () => {
      pending = null;
    },
  };
  const tick = () => {
    const f = pending;
    pending = null;
    if (f) f();
  };

  const sync = useGridRealtimeSync({
    getMyClientId: () => MY_ID,
    grid,
    wsStore,
    pictureChangeAffectsView,
    getSelectedSort: () => selectedSort.value,
    logger: { warn: vi.fn() },
    reload,
    refreshSidebar,
    scheduler,
  });

  return { sync, grid, wsStore, reload, refreshSidebar, selectedSort, tick };
}

describe("useGridRealtimeSync - coalescing window", () => {
  it("100 foreign updates for one id -> a single refreshGridImage on flush", () => {
    const h = makeCoalescingHarness();
    for (let i = 0; i < 100; i++) {
      h.sync.handleMessage({
        type: "pictures_changed",
        source: "ui",
        origin_client_id: OTHER_ID,
        change_kind: "updated",
        fields: ["tags"],
        picture_ids: [42],
      });
    }
    // Nothing applied yet - still inside the window.
    expect(h.grid.refreshGridImage).not.toHaveBeenCalled();
    h.tick();
    // Deduped to the single distinct id.
    expect(h.grid.refreshGridImage).toHaveBeenCalledTimes(1);
    expect(h.grid.refreshGridImage).toHaveBeenCalledWith(42);
  });

  it("batched inserts collapse to one insertGridImagesById call", () => {
    const h = makeCoalescingHarness();
    h.sync.handleMessage({
      type: "pictures_changed",
      source: "ui",
      origin_client_id: OTHER_ID,
      change_kind: "added",
      picture_ids: [1, 2],
    });
    h.sync.handleMessage({
      type: "pictures_changed",
      source: "ui",
      origin_client_id: OTHER_ID,
      change_kind: "added",
      picture_ids: [3],
    });
    h.tick();
    expect(h.grid.insertGridImagesById).toHaveBeenCalledTimes(1);
    expect(h.grid.insertGridImagesById).toHaveBeenCalledWith([1, 2, 3]);
  });

  it("external adds raise the New-pictures pill once per window", () => {
    const h = makeCoalescingHarness();
    h.sync.handleMessage({
      type: "picture_imported",
      source: "external",
      origin_client_id: null,
      picture_ids: [10],
    });
    h.sync.handleMessage({
      type: "picture_imported",
      source: "external",
      origin_client_id: null,
      picture_ids: [11],
    });
    h.tick();
    expect(h.wsStore.addPendingExternalImportIds).toHaveBeenCalledTimes(1);
    expect(h.wsStore.addPendingExternalImportIds).toHaveBeenCalledWith([
      10, 11,
    ]);
  });

  it("an add then a remove of the same id nets to a remove", () => {
    const h = makeCoalescingHarness();
    h.sync.handleMessage({
      type: "pictures_changed",
      source: "ui",
      origin_client_id: OTHER_ID,
      change_kind: "added",
      picture_ids: [5],
    });
    h.sync.handleMessage({
      type: "pictures_changed",
      source: "ui",
      origin_client_id: OTHER_ID,
      change_kind: "removed",
      picture_ids: [5],
    });
    h.tick();
    expect(h.grid.removeImagesById).toHaveBeenCalledWith([5]);
    expect(h.grid.insertGridImagesById).not.toHaveBeenCalled();
  });

  it("coalesced per-id batch over MAX_TARGETED_UPDATE escalates to one reload", () => {
    const h = makeCoalescingHarness();
    // 51 distinct ids, one event each - each event is sub-cap, but the window's
    // distinct-id total crosses MAX_TARGETED_UPDATE (50).
    for (let id = 1; id <= 51; id++) {
      h.sync.handleMessage({
        type: "pictures_changed",
        source: "ui",
        origin_client_id: OTHER_ID,
        change_kind: "updated",
        fields: ["tags"],
        picture_ids: [id],
      });
    }
    h.tick();
    expect(h.reload).toHaveBeenCalledTimes(1);
    expect(h.grid.refreshGridImage).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// change_kind: "restored" - a scrapheap comeback is not an import
// ---------------------------------------------------------------------------
//
// A picture coming back out of the Scrapheap puts a card back, exactly as an
// import does, and for a while both said `added`. The difference the SPA acts
// on is that `added` means NEW TO THE VAULT: the sidebar answers it with its
// NEW marker and the grid answers it with the new-picture flash. Neither is
// true of a picture that has been in the library the whole time, which is why
// `restored` is a kind of its own.

describe("useGridRealtimeSync - restored (scrapheap comeback)", () => {
  function restored(overrides = {}) {
    return {
      type: "pictures_changed",
      source: "ui",
      change_kind: "restored",
      picture_ids: [3, 4],
      ...overrides,
    };
  }

  it("reinserts on this tab's OWN undo instead of suppressing the echo", () => {
    const h = makeHarness();
    const res = h.sync.handleMessage(restored({ origin_client_id: MY_ID }));
    // The bug this fixes: suppressed as "my own optimistic op already did it",
    // when nothing local had. The grid kept showing the pre-undo state.
    expect(res.action).toBe("targeted");
    expect(h.grid.insertGridImagesById).toHaveBeenCalledWith([3, 4], {
      highlight: false,
    });
    expect(h.grid.removeImagesById).not.toHaveBeenCalled();
  });

  it("reinserts without the new-picture flash - it is a comeback, not an arrival", () => {
    const h = makeHarness();
    h.sync.handleMessage(restored({ origin_client_id: MY_ID }));
    const [, options] = h.grid.insertGridImagesById.mock.calls[0];
    expect(options).toEqual({ highlight: false });

    // …whereas a genuine import still flashes (no options at all).
    h.grid.insertGridImagesById.mockClear();
    h.sync.handleMessage({
      type: "picture_imported",
      source: "ui",
      origin_client_id: OTHER_ID,
      picture_ids: [9],
    });
    expect(h.grid.insertGridImagesById).toHaveBeenCalledWith([9]);
  });

  it("refreshes the sidebar WITHOUT raising its NEW marker", () => {
    const h = makeHarness();
    h.sync.handleMessage(restored({ origin_client_id: MY_ID }));
    // The counts really did change (All Pictures up, Scrapheap down), so the
    // sidebar must re-read - it just must not call it new.
    expect(h.refreshSidebar).toHaveBeenCalledWith(false);

    h.refreshSidebar.mockClear();
    h.sync.handleMessage({
      type: "picture_imported",
      source: "ui",
      origin_client_id: OTHER_ID,
      picture_ids: [9],
    });
    expect(h.refreshSidebar).toHaveBeenCalledWith(true);
  });

  it("applies another owner tab's restore in place", () => {
    const h = makeHarness();
    const res = h.sync.handleMessage(restored({ origin_client_id: OTHER_ID }));
    expect(res.action).toBe("targeted");
    expect(h.grid.insertGridImagesById).toHaveBeenCalledWith([3, 4], {
      highlight: false,
    });
  });

  it("raises the view-changed pill for an external restore, never the new-pictures one", () => {
    const h = makeHarness();
    const res = h.sync.handleMessage(
      restored({ source: "external", origin_client_id: null }),
    );
    expect(res.action).toBe("pill");
    expect(h.wsStore.addSortChangedExternalIds).toHaveBeenCalledWith([3, 4]);
    // "↑ 2 new pictures, click to load" would be a lie about these pictures.
    expect(h.wsStore.addPendingExternalImportIds).not.toHaveBeenCalled();
    expect(h.grid.insertGridImagesById).not.toHaveBeenCalled();
  });

  it("reloads when a restore-all names no ids", () => {
    const h = makeHarness();
    const res = h.sync.handleMessage(
      restored({ origin_client_id: MY_ID, picture_ids: [] }),
    );
    // `POST /pictures/scrapheap/restore` with no subset broadcasts an empty id
    // list; a per-id insert would silently do nothing.
    expect(res.action).toBe("reload");
    expect(h.reload).toHaveBeenCalled();
  });

  it("defers under an open overlay rather than restructuring the filmstrip", () => {
    const h = makeHarness({ overlayOpen: true });
    const res = h.sync.handleMessage(restored({ origin_client_id: MY_ID }));
    expect(res.action).toBe("targeted");
    expect(h.grid.markOverlayDeferredRefresh).toHaveBeenCalled();
    expect(h.grid.insertGridImagesById).not.toHaveBeenCalled();
  });

  it("falls back to the view-changed pill while a streaming fetch owns the grid", () => {
    const h = makeHarness();
    h.grid.isImagesLoading.mockReturnValue(true);
    const res = h.sync.handleMessage(restored({ origin_client_id: MY_ID }));
    expect(res.action).toBe("pill");
    expect(h.wsStore.addSortChangedExternalIds).toHaveBeenCalledWith([3, 4]);
    expect(h.wsStore.addPendingExternalImportIds).not.toHaveBeenCalled();
  });

  it("still treats a re-scrapheap on redo as a removal", () => {
    const h = makeHarness();
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "ui",
      origin_client_id: OTHER_ID,
      change_kind: "removed",
      picture_ids: [3, 4],
    });
    expect(res.action).toBe("targeted");
    expect(h.grid.removeImagesById).toHaveBeenCalledWith([3, 4]);
  });

  it("degrades an unknown kind to updated, exactly as before", () => {
    const h = makeHarness();
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "ui",
      origin_client_id: OTHER_ID,
      change_kind: "resurrected",
      fields: ["tags"],
      picture_ids: [3],
    });
    expect(res.reason).toBe("foreign-ui-updated");
  });
});

describe("useGridRealtimeSync - a rotate's pixels changed", () => {
  // The card's thumbnail URL comes from the batch-thumbnail endpoint, never
  // from /metadata, so refreshGridImage alone repaints the pre-rotate bitmap.
  // The forward rotate hides this because the tab that issued it refreshes the
  // URL itself; an undo arrives over the socket with no such local hook, which
  // is the bug this covers.
  it("own-origin undo echo -> refreshes the thumbnail URL, not just metadata", () => {
    const h = makeHarness();
    const res = h.sync.handleMessage({
      type: "pictures_changed",
      source: "ui",
      origin_client_id: MY_ID,
      picture_ids: [11, 12],
      change_kind: "updated",
      fields: ["pixels"],
    });
    expect(res.action).toBe("targeted");
    expect(res.reason).toBe("card-content-refresh");
    // One applier, not a metadata refresh followed by a thumbnail refresh: the
    // tile's shape and its bitmap have to land in the same frame or the picture
    // turns twice on screen. Asserting the old pair is asserting the bug.
    expect(h.grid.applyRotatedCards).toHaveBeenCalledWith([11, 12]);
    expect(h.grid.refreshGridImage).not.toHaveBeenCalled();
    expect(h.grid.refreshThumbnailUrls).not.toHaveBeenCalled();
  });

  it("a foreign tab's rotate also repaints here", () => {
    const h = makeHarness();
    h.sync.handleMessage({
      type: "pictures_changed",
      source: "ui",
      origin_client_id: OTHER_ID,
      picture_ids: [13],
      change_kind: "updated",
      fields: ["pixels"],
    });
    expect(h.grid.applyRotatedCards).toHaveBeenCalledWith([13]);
  });

  it("never reloads the grid or raises a pill - a turned photo does not move", () => {
    const h = makeHarness();
    h.sync.handleMessage({
      type: "pictures_changed",
      source: "ui",
      origin_client_id: OTHER_ID,
      picture_ids: [14],
      change_kind: "updated",
      fields: ["pixels"],
    });
    expect(h.reload).not.toHaveBeenCalled();
    expect(h.wsStore.addSortChangedExternalIds).not.toHaveBeenCalled();
  });

  it("leaves the thumbnail alone for a change that did not touch the file", () => {
    const h = makeHarness();
    h.sync.handleMessage({
      type: "pictures_changed",
      source: "ui",
      origin_client_id: OTHER_ID,
      picture_ids: [15],
      change_kind: "updated",
      fields: ["detections"],
    });
    // `detections` leaves the FILE alone, so it keeps the plain metadata
    // refresh and must not take the rotate applier's decode-then-commit path.
    expect(h.grid.refreshGridImage).toHaveBeenCalledWith(15);
    expect(h.grid.applyRotatedCards).not.toHaveBeenCalled();
  });
});
