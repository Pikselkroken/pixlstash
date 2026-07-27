import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("../utils/apiClient", () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));

import { apiClient } from "../utils/apiClient";
import {
  listTags,
  addPictureTag,
  removePictureTag,
  bulkFetchTags,
  removeTagEverywhere,
  listTagPredictions,
  confirmTagPrediction,
  rejectTagPrediction,
} from "./tags";

beforeEach(() => {
  apiClient.get.mockReset();
  apiClient.post.mockReset();
  apiClient.delete.mockReset();
});

describe("api/tags", () => {
  it("listTags GETs the vocabulary with no config by default", async () => {
    apiClient.get.mockResolvedValue({ data: [{ tag: "hat" }] });
    const result = await listTags();
    expect(apiClient.get).toHaveBeenCalledWith("/tags", undefined);
    expect(result).toEqual([{ tag: "hat" }]);
  });

  it("listTags forwards query params", async () => {
    apiClient.get.mockResolvedValue({ data: [] });
    await listTags({ params: { min_count: 2 } });
    expect(apiClient.get).toHaveBeenCalledWith("/tags", {
      params: { min_count: 2 },
    });
  });

  it("addPictureTag POSTs the tag and returns the picture's tag list", async () => {
    apiClient.post.mockResolvedValue({ data: { tags: [{ id: 1 }] } });
    const result = await addPictureTag(42, "hat", { baseUrl: "/be" });
    expect(apiClient.post).toHaveBeenCalledWith("/be/pictures/42/tags", {
      tag: "hat",
    });
    expect(result.tags).toEqual([{ id: 1 }]);
  });

  // Removal is by tag id, not by name: two tags can render identically and the
  // id is what picks the row to drop.
  it("removePictureTag addresses the tag by id", async () => {
    apiClient.delete.mockResolvedValue({ data: {} });
    await removePictureTag(42, 7);
    expect(apiClient.delete).toHaveBeenCalledWith("/pictures/42/tags/7");
  });

  it("bulkFetchTags POSTs the picture ids", async () => {
    apiClient.post.mockResolvedValue({ data: [] });
    await bulkFetchTags([1, 2]);
    expect(apiClient.post).toHaveBeenCalledWith("/pictures/tags/bulk_fetch", {
      picture_ids: [1, 2],
    });
  });

  // Library-wide removal by NAME, unlike removePictureTag: the picture id only
  // scopes the request, so the tag must go in the body.
  it("removeTagEverywhere sends the tag name in the body", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await removeTagEverywhere(42, "hat");
    expect(apiClient.post).toHaveBeenCalledWith(
      "/pictures/42/tags/remove_all",
      { tag: "hat" },
    );
  });

  it("listTagPredictions asks for meta by default", async () => {
    apiClient.get.mockResolvedValue({ data: [] });
    await listTagPredictions(42);
    expect(apiClient.get).toHaveBeenCalledWith(
      "/pictures/42/tag_predictions?include_meta=1",
    );
  });

  it("listTagPredictions filters by status when asked", async () => {
    apiClient.get.mockResolvedValue({ data: [] });
    await listTagPredictions(42, { status: "REJECTED" });
    expect(apiClient.get).toHaveBeenCalledWith(
      "/pictures/42/tag_predictions?status=REJECTED&include_meta=1",
    );
  });

  it("confirmTagPrediction URL-encodes the tag", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await confirmTagPrediction(42, "red car");
    expect(apiClient.post).toHaveBeenCalledWith(
      "/pictures/42/tag_predictions/red%20car/confirm",
    );
  });

  it("rejectTagPrediction URL-encodes the tag", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await rejectTagPrediction(42, "red/car");
    expect(apiClient.post).toHaveBeenCalledWith(
      "/pictures/42/tag_predictions/red%2Fcar/reject",
    );
  });
});
