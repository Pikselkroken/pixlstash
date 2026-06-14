import { describe, it, expect, beforeEach, vi } from "vitest";
import { useGridRealtimeSync } from "./useGridRealtimeSync.js";

const MY_ID = "my-tab-uuid";
const OTHER_ID = "other-tab-uuid";

function makeHarness(overrides = {}) {
  const grid = {
    insertGridImagesById: vi.fn(),
    refreshGridImage: vi.fn(),
    repositionImageByScore: vi.fn(),
    repositionImageBySmartScore: vi.fn(),
    refreshSmartScoreForImage: vi.fn(),
    removeImagesById: vi.fn(),
    isImagesLoading: vi.fn(() => false),
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
      if (f === "smart_score") return selectedSort.value.includes("SMART_SCORE");
      if (f === "character_likeness")
        return selectedSort.value.includes("CHARACTER_LIKENESS");
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
    expect(h.wsStore.addPendingExternalImportIds).toHaveBeenCalledWith([20, 21]);
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

  it("own-origin import echo (matching id) is suppressed", () => {
    const res = h.sync.handleMessage({
      type: "picture_imported",
      source: "ui",
      origin_client_id: MY_ID,
      picture_ids: [60],
    });
    expect(res.action).toBe("suppressed");
    expect(h.grid.insertGridImagesById).not.toHaveBeenCalled();
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
