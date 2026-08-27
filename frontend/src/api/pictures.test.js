import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  apiClient: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));

import { apiClient } from "../utils/apiClient";
import {
  getAnomalyRegion,
  listPicturesByIds,
  getPictureCount,
  streamPictures,
  getLikenessGroups,
  faceSearch,
  likenessSearch,
  searchPictures,
  getPictureStats,
  clearGuestScoreSession,
  getPictureMetadata,
  getThumbnails,
  deletePictures,
  setPicturesProject,
  purgeScrapheap,
  restoreScrapheap,
  startExport,
  getExportStatus,
  downloadExport,
  downloadPicture,
} from "./pictures";

beforeEach(() => {
  apiClient.get.mockReset();
  apiClient.post.mockReset();
  apiClient.delete.mockReset();
});

describe("api/pictures", () => {
  it("listPicturesByIds repeats the id param once per picture", async () => {
    apiClient.get.mockResolvedValue({ data: [{ id: 4 }, { id: 5 }] });
    const result = await listPicturesByIds([4, 5]);
    expect(apiClient.get).toHaveBeenCalledWith("/pictures?id=4&id=5");
    expect(result).toEqual([{ id: 4 }, { id: 5 }]);
  });

  it("listPicturesByIds requests the by-ids route", async () => {
    apiClient.get.mockResolvedValue({ data: [] });
    await listPicturesByIds([4]);
    expect(apiClient.get).toHaveBeenCalledWith("/pictures?id=4");
  });

  it("listPicturesByIds appends the projection when asked", async () => {
    apiClient.get.mockResolvedValue({ data: [] });
    await listPicturesByIds([4, 5], { fields: "grid" });
    expect(apiClient.get).toHaveBeenCalledWith("/pictures?id=4&id=5&fields=grid");
  });

  it("getPictureCount appends a filter query when there is one", async () => {
    apiClient.get.mockResolvedValue({ data: { count: 12 } });
    const result = await getPictureCount("stack_leaders_only=true", { });
    expect(apiClient.get).toHaveBeenCalledWith(
      "/pictures/count?stack_leaders_only=true",
    );
    expect(result.count).toBe(12);
  });

  it("getPictureCount omits the query separator when unfiltered", async () => {
    apiClient.get.mockResolvedValue({ data: { count: 0 } });
    await getPictureCount();
    expect(apiClient.get).toHaveBeenCalledWith("/pictures/count");
  });

  // The grid runs several batches concurrently, so offset/limit must land on
  // the wire exactly as given rather than being tracked inside the module.
  it("streamPictures appends the caller's offset and batch limit", async () => {
    apiClient.get.mockResolvedValue({ data: { pictures: [] } });
    await streamPictures("fields=grid", { offset: 200, batchLimit: 50 });
    expect(apiClient.get).toHaveBeenCalledWith(
      "/pictures/stream?fields=grid&offset=200&batch_limit=50",
    );
  });

  it("getLikenessGroups encodes the threshold and appends the filter", async () => {
    apiClient.get.mockResolvedValue({ data: [] });
    await getLikenessGroups(0.4, "character_id=2");
    expect(apiClient.get).toHaveBeenCalledWith(
      "/pictures/likeness-groups?threshold=0.4&character_id=2",
    );
  });

  it("getLikenessGroups omits an empty filter", async () => {
    apiClient.get.mockResolvedValue({ data: [] });
    await getLikenessGroups(0.4);
    expect(apiClient.get).toHaveBeenCalledWith(
      "/pictures/likeness-groups?threshold=0.4",
    );
  });

  it("faceSearch POSTs the source face and a top-n cap", async () => {
    apiClient.post.mockResolvedValue({ data: [] });
    await faceSearch(7);
    expect(apiClient.post).toHaveBeenCalledWith(
      "/pictures/face-search?source_face_id=7&top_n=500",
    );
  });

  // Several sources are combined by MINIMUM similarity, so each one has to
  // reach the server as its own repeated param.
  it("likenessSearch repeats one source_picture_ids param per source", async () => {
    apiClient.post.mockResolvedValue({ data: [] });
    await likenessSearch([1, 2]);
    expect(apiClient.post).toHaveBeenCalledWith(
      "/pictures/likeness-search?source_picture_ids=1&source_picture_ids=2&top_n=500&threshold=0.05",
    );
  });

  it("searchPictures encodes the text and appends the scope filter", async () => {
    apiClient.get.mockResolvedValue({ data: [] });
    await searchPictures("red car", { query: "character_id=2" });
    expect(apiClient.get).toHaveBeenCalledWith(
      "/pictures/search?query=red%20car&threshold=0.1&top_n=10000&character_id=2",
    );
  });

  it("getPictureStats merges the filter query with the section params", async () => {
    apiClient.get.mockResolvedValue({ data: {} });
    await getPictureStats("character_id=2", { include: "cooc" });
    expect(apiClient.get).toHaveBeenCalledWith(
      "/pictures/stats?character_id=2",
      { params: { include: "cooc" } },
    );
  });

  it("getPictureStats drops the separator when there is no filter", async () => {
    apiClient.get.mockResolvedValue({ data: {} });
    await getPictureStats();
    expect(apiClient.get).toHaveBeenCalledWith("/pictures/stats", undefined);
  });

  it("clearGuestScoreSession DELETEs the session scores", async () => {
    apiClient.delete.mockResolvedValue({ data: {} });
    await clearGuestScoreSession();
    expect(apiClient.delete).toHaveBeenCalledWith(
      "/pictures/guest-scores/session",
    );
  });

  it("getPictureMetadata omits the query when nothing is asked for", async () => {
    apiClient.get.mockResolvedValue({ data: {} });
    await getPictureMetadata(42);
    expect(apiClient.get).toHaveBeenCalledWith("/pictures/42/metadata");
  });

  it("getPictureMetadata adds the smart-score and cache-buster params", async () => {
    apiClient.get.mockResolvedValue({ data: {} });
    await getPictureMetadata(42, { smartScore: true, cacheBuster: 99 });
    expect(apiClient.get).toHaveBeenCalledWith(
      "/pictures/42/metadata?smart_score=true&cb=99",
    );
  });

  it("getThumbnails POSTs the id batch", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await getThumbnails(["1", "2"]);
    expect(apiClient.post).toHaveBeenCalledWith("/pictures/thumbnails", {
      ids: ["1", "2"],
    });
  });

  it("deletePictures sends the ids as the DELETE body", async () => {
    apiClient.delete.mockResolvedValue({ data: { skipped_locked: [] } });
    await deletePictures([1, 2]);
    expect(apiClient.delete).toHaveBeenCalledWith("/pictures", {
      data: { picture_ids: [1, 2] },
    });
  });

  // No mode means SET the project; sending mode:undefined would be a different
  // request shape, so the key must be absent entirely.
  it("setPicturesProject omits the mode when setting", async () => {
    apiClient.patch = vi.fn().mockResolvedValue({ data: {} });
    await setPicturesProject([1], 5);
    expect(apiClient.patch).toHaveBeenCalledWith("/pictures/project", {
      picture_ids: [1],
      project_id: 5,
    });
  });

  it("setPicturesProject includes the mode when adding or removing", async () => {
    apiClient.patch = vi.fn().mockResolvedValue({ data: {} });
    await setPicturesProject([1], 5, { mode: "remove" });
    expect(apiClient.patch).toHaveBeenCalledWith("/pictures/project", {
      picture_ids: [1],
      project_id: 5,
      mode: "remove",
    });
  });

  // Omitting the ids means "empty the whole heap", so the key must not appear
  // as undefined and accidentally scope the purge to nothing.
  it("purgeScrapheap omits picture_ids when emptying everything", async () => {
    apiClient.delete.mockResolvedValue({ data: {} });
    await purgeScrapheap({ includeProtected: true, confirmToken: "tok-a" });
    expect(apiClient.delete).toHaveBeenCalledWith("/pictures/scrapheap", {
      data: { include_protected: true, confirm_token: "tok-a" },
    });
  });

  it("purgeScrapheap scopes to the given ids when they are supplied", async () => {
    apiClient.delete.mockResolvedValue({ data: {} });
    await purgeScrapheap({ pictureIds: [3], confirmToken: "tok-b" });
    expect(apiClient.delete).toHaveBeenCalledWith("/pictures/scrapheap", {
      data: {
        include_protected: false,
        confirm_token: "tok-b",
        picture_ids: [3],
      },
    });
  });

  // The server refuses the purge without the preview's single-use
  // confirm_token, so the field must always be on the wire - never silently
  // dropped when a caller forgets it.
  it("purgeScrapheap always sends confirm_token", async () => {
    apiClient.delete.mockResolvedValue({ data: {} });
    await purgeScrapheap({});
    expect(apiClient.delete).toHaveBeenCalledWith("/pictures/scrapheap", {
      data: { include_protected: false, confirm_token: undefined },
    });
  });

  it("restoreScrapheap sends no body when restoring everything", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await restoreScrapheap();
    expect(apiClient.post).toHaveBeenCalledWith(
      "/pictures/scrapheap/restore",
      undefined,
    );
  });

  it("restoreScrapheap scopes to the given ids", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await restoreScrapheap([3]);
    expect(apiClient.post).toHaveBeenCalledWith("/pictures/scrapheap/restore", {
      picture_ids: [3],
    });
  });

  it("startExport appends the selection query", async () => {
    apiClient.get.mockResolvedValue({ data: { task_id: "t1" } });
    const result = await startExport("id=1&export_type=full");
    expect(apiClient.get).toHaveBeenCalledWith(
      "/pictures/export?id=1&export_type=full",
    );
    expect(result.task_id).toBe("t1");
  });

  it("getExportStatus polls by task id", async () => {
    apiClient.get.mockResolvedValue({ data: { status: "in_progress" } });
    await getExportStatus("t1");
    expect(apiClient.get).toHaveBeenCalledWith("/pictures/export/status", {
      params: { task_id: "t1" },
    });
  });

  // The ZIP name lives in a response HEADER: a body-only return would rename
  // every download to the fallback, so the module parses it here.
  it("downloadExport returns the blob and the server's filename", async () => {
    const blob = new Blob(["zip"]);
    apiClient.get.mockResolvedValue({
      data: blob,
      headers: { "content-disposition": 'attachment; filename="trip.zip"' },
    });
    const result = await downloadExport("/pictures/export/download/t1");
    expect(apiClient.get).toHaveBeenCalledWith(
      "/pictures/export/download/t1",
      { responseType: "blob" },
    );
    expect(result).toEqual({ blob, filename: "trip.zip" });
  });

  it("downloadExport falls back to a default name without the header", async () => {
    const blob = new Blob(["zip"]);
    apiClient.get.mockResolvedValue({ data: blob, headers: {} });
    const result = await downloadExport("/dl");
    expect(result.filename).toBe("pixlstash_export.zip");
  });

  it("downloadPicture fetches the original media as a blob", async () => {
    const blob = new Blob(["picture"]);
    apiClient.get.mockResolvedValue({ data: blob });

    const result = await downloadPicture(42, ".JPG", {
      version: "pixel-hash",
    });

    expect(apiClient.get).toHaveBeenCalledWith(
      "/pictures/42.jpg?v=pixel-hash",
      { responseType: "blob" },
    );
    expect(result).toBe(blob);
  });

  it("getAnomalyRegion passes the tag as a query param", async () => {
    apiClient.get.mockResolvedValue({ data: { bbox: [0, 0, 1, 1] } });
    const result = await getAnomalyRegion(42, "hat");
    expect(apiClient.get).toHaveBeenCalledWith("/pictures/42/anomaly_region", {
      params: { tag: "hat" },
    });
    expect(result).toEqual({ bbox: [0, 0, 1, 1] });
  });

  // The caller caches a miss on rejection, so an unknown tag must reject
  // rather than resolve to null.
  it("getAnomalyRegion propagates an unknown-tag failure", async () => {
    apiClient.get.mockRejectedValue(new Error("404"));
    await expect(getAnomalyRegion(42, "nope")).rejects.toThrow("404");
  });
});
