import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("../utils/apiClient", () => ({
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

  it("listPicturesByIds prefixes an explicit backend base", async () => {
    apiClient.get.mockResolvedValue({ data: [] });
    await listPicturesByIds([4], { baseUrl: "/be" });
    expect(apiClient.get).toHaveBeenCalledWith("/be/pictures?id=4");
  });

  it("listPicturesByIds appends the projection when asked", async () => {
    apiClient.get.mockResolvedValue({ data: [] });
    await listPicturesByIds([4, 5], { fields: "grid" });
    expect(apiClient.get).toHaveBeenCalledWith("/pictures?id=4&id=5&fields=grid");
  });

  it("getPictureCount appends a filter query when there is one", async () => {
    apiClient.get.mockResolvedValue({ data: { count: 12 } });
    const result = await getPictureCount("stack_leaders_only=true", {
      baseUrl: "/be",
    });
    expect(apiClient.get).toHaveBeenCalledWith(
      "/be/pictures/count?stack_leaders_only=true",
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
