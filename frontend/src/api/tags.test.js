import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("../utils/apiClient", () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
  // Same shape as the real helper: the header only exists when the caller is
  // part of a gesture, so an ordinary call still sends no config at all.
  operationBatchHeaders: (batchId) =>
    batchId ? { headers: { "X-Operation-Batch-Id": batchId } } : undefined,
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
    const result = await addPictureTag(42, "hat");
    expect(apiClient.post).toHaveBeenCalledWith("/pictures/42/tags", {
      tag: "hat",
    });
    expect(result.tags).toEqual([{ id: 1 }]);
  });

  // Removal is by tag id, not by name: two tags can render identically and the
  // id is what picks the row to drop.
  it("removePictureTag addresses the tag by id", async () => {
    apiClient.delete.mockResolvedValue({ data: {} });
    await removePictureTag(42, 7);
    expect(apiClient.delete).toHaveBeenCalledWith(
      "/pictures/42/tags/7",
      undefined,
    );
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
      undefined,
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
      undefined,
      undefined,
    );
  });

  it("rejectTagPrediction URL-encodes the tag", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await rejectTagPrediction(42, "red/car");
    expect(apiClient.post).toHaveBeenCalledWith(
      "/pictures/42/tag_predictions/red%2Fcar/reject",
      undefined,
      undefined,
    );
  });

  // One gesture, one undo step: every request of a compound gesture carries the
  // same X-Operation-Batch-Id, which the backend stores as the operations'
  // batch_id (docs/backend_architecture.md §21.2).
  it("puts the gesture batch id on the header of every tag mutation", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    apiClient.delete.mockResolvedValue({ data: {} });
    const config = { headers: { "X-Operation-Batch-Id": "cli-gesture-1" } };

    await removeTagEverywhere(42, "hat", { batchId: "cli-gesture-1" });
    expect(apiClient.post).toHaveBeenLastCalledWith(
      "/pictures/42/tags/remove_all",
      { tag: "hat" },
      config,
    );

    await rejectTagPrediction(42, "hat", { batchId: "cli-gesture-1" });
    expect(apiClient.post).toHaveBeenLastCalledWith(
      "/pictures/42/tag_predictions/hat/reject",
      undefined,
      config,
    );

    await confirmTagPrediction(42, "hat", { batchId: "cli-gesture-1" });
    expect(apiClient.post).toHaveBeenLastCalledWith(
      "/pictures/42/tag_predictions/hat/confirm",
      undefined,
      config,
    );

    await removePictureTag(42, 7, { batchId: "cli-gesture-1" });
    expect(apiClient.delete).toHaveBeenLastCalledWith(
      "/pictures/42/tags/7",
      config,
    );
  });
});
