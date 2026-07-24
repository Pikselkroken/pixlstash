import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("../utils/apiClient", () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));

import { apiClient } from "../utils/apiClient";
import {
  listReviews,
  getReview,
  listReviewSuggestions,
  createReview,
  refreshReview,
  archiveReview,
  abortReview,
  deleteReview,
  deleteReviewsByStatus,
} from "./reviews";

beforeEach(() => {
  apiClient.get.mockReset();
  apiClient.post.mockReset();
  apiClient.delete.mockReset();
});

describe("api/reviews", () => {
  it("listReviews passes the status as a query param", async () => {
    apiClient.get.mockResolvedValue({ data: [{ id: 1 }] });
    const result = await listReviews("OPEN");
    expect(apiClient.get).toHaveBeenCalledWith("/reviews", {
      params: { status: "OPEN" },
    });
    expect(result).toEqual([{ id: 1 }]);
  });

  it("getReview GETs one review", async () => {
    apiClient.get.mockResolvedValue({ data: { id: 4 } });
    const result = await getReview(4);
    expect(apiClient.get).toHaveBeenCalledWith("/reviews/4");
    expect(result).toEqual({ id: 4 });
  });

  it("listReviewSuggestions forwards the paging params", async () => {
    apiClient.get.mockResolvedValue({ data: { items: [] } });
    await listReviewSuggestions(4, { status: "PENDING", limit: 200 });
    expect(apiClient.get).toHaveBeenCalledWith("/reviews/4/suggestions", {
      params: { status: "PENDING", limit: 200 },
    });
  });

  it("createReview POSTs the body and returns the created review", async () => {
    apiClient.post.mockResolvedValue({ data: { id: 11 } });
    const body = { tag: "hat", project_id: 2 };
    const result = await createReview(body);
    expect(apiClient.post).toHaveBeenCalledWith("/reviews", body);
    expect(result).toEqual({ id: 11 });
  });

  it("refreshReview POSTs to the refresh sub-resource", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await refreshReview(4);
    expect(apiClient.post).toHaveBeenCalledWith("/reviews/4/refresh");
  });

  it("archiveReview POSTs to the archive sub-resource", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await archiveReview(4);
    expect(apiClient.post).toHaveBeenCalledWith("/reviews/4/archive");
  });

  it("abortReview POSTs to the abort sub-resource", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await abortReview(4);
    expect(apiClient.post).toHaveBeenCalledWith("/reviews/4/abort");
  });

  it("deleteReview DELETEs one review", async () => {
    apiClient.delete.mockResolvedValue({ data: {} });
    await deleteReview(4);
    expect(apiClient.delete).toHaveBeenCalledWith("/reviews/4");
  });

  // The status param is what stops a "clear archived" from reaching an open
  // review, so it must always be on the wire.
  it("deleteReviewsByStatus always sends the status filter", async () => {
    apiClient.delete.mockResolvedValue({ data: {} });
    await deleteReviewsByStatus("ARCHIVED");
    expect(apiClient.delete).toHaveBeenCalledWith("/reviews", {
      params: { status: "ARCHIVED" },
    });
  });
});
