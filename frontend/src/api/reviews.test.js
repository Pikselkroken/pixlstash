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

const BODY = { tag: "hat", project_id: 2 };

// Every review route is a passthrough: the only thing each one can get wrong is
// the verb, the path and the params it puts on the wire. One row per route says
// that more legibly than nine near-identical blocks, and a new route is a line
// rather than a paragraph.
//
// `deleteReviewsByStatus`'s status param is the row that matters most: it is
// what stops a "clear archived" from reaching an open review, so it must always
// be on the wire.
describe.each([
  ["listReviews passes the status as a query param",
    () => listReviews("OPEN"), "get", ["/reviews", { params: { status: "OPEN" } }]],
  ["getReview GETs one review",
    () => getReview(4), "get", ["/reviews/4"]],
  ["listReviewSuggestions forwards the paging params",
    () => listReviewSuggestions(4, { status: "PENDING", limit: 200 }), "get",
    ["/reviews/4/suggestions", { params: { status: "PENDING", limit: 200 } }]],
  ["createReview POSTs the body",
    () => createReview(BODY), "post", ["/reviews", BODY]],
  ["refreshReview POSTs to the refresh sub-resource",
    () => refreshReview(4), "post", ["/reviews/4/refresh"]],
  ["archiveReview POSTs to the archive sub-resource",
    () => archiveReview(4), "post", ["/reviews/4/archive"]],
  ["abortReview POSTs to the abort sub-resource",
    () => abortReview(4), "post", ["/reviews/4/abort"]],
  ["deleteReview DELETEs one review",
    () => deleteReview(4), "delete", ["/reviews/4"]],
  ["deleteReviewsByStatus always sends the status filter",
    () => deleteReviewsByStatus("ARCHIVED"), "delete",
    ["/reviews", { params: { status: "ARCHIVED" } }]],
])("api/reviews", (name, call, method, expected) => {
  it(name, async () => {
    apiClient[method].mockResolvedValue({ data: {} });
    await call();
    expect(apiClient[method]).toHaveBeenCalledWith(...expected);
  });
});

// The unwrapping is the other half of the contract and is asserted separately:
// a table row that also checked the return value would be asserting two
// unrelated things per row.
describe("api/reviews unwraps the response body", () => {
  it("returns res.data rather than the Axios envelope", async () => {
    apiClient.get.mockResolvedValue({ data: [{ id: 1 }] });
    expect(await listReviews("OPEN")).toEqual([{ id: 1 }]);
    apiClient.post.mockResolvedValue({ data: { id: 11 } });
    expect(await createReview(BODY)).toEqual({ id: 11 });
  });
});
