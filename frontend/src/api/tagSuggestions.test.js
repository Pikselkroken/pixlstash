import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  apiClient: { post: vi.fn() },
}));

import { apiClient } from "../utils/apiClient";
import {
  resolveTagSuggestion,
  skipTagSuggestion,
  reopenTagSuggestion,
  bulkReopenTagSuggestions,
} from "./tagSuggestions";

beforeEach(() => {
  apiClient.post.mockReset();
});

describe("api/tagSuggestions", () => {
  it("resolveTagSuggestion puts the action in the path", async () => {
    apiClient.post.mockResolvedValue({ data: { ok: true } });
    const result = await resolveTagSuggestion(12, "accept");
    expect(apiClient.post).toHaveBeenCalledWith("/tag_suggestions/12/accept");
    expect(result).toEqual({ ok: true });
  });

  it("resolveTagSuggestion carries the hyphenated actions through", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await resolveTagSuggestion(12, "fix-twin");
    expect(apiClient.post).toHaveBeenCalledWith("/tag_suggestions/12/fix-twin");
  });

  it("skipTagSuggestion POSTs the skip sub-resource", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await skipTagSuggestion(12);
    expect(apiClient.post).toHaveBeenCalledWith("/tag_suggestions/12/skip");
  });

  it("reopenTagSuggestion POSTs the reopen sub-resource", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await reopenTagSuggestion(12);
    expect(apiClient.post).toHaveBeenCalledWith("/tag_suggestions/12/reopen");
  });

  // Callers undoing a batch of skips tell "already gone" from "broken" by the
  // status on the rejection, so the raw Axios error must survive the module.
  it("reopenTagSuggestion rejects with the Axios error intact", async () => {
    const err = Object.assign(new Error("Not Found"), {
      response: { status: 404 },
    });
    apiClient.post.mockRejectedValue(err);
    await expect(reopenTagSuggestion(12)).rejects.toMatchObject({
      response: { status: 404 },
    });
  });

  it("bulkReopenTagSuggestions scopes the reopen to one review", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await bulkReopenTagSuggestions(4);
    expect(apiClient.post).toHaveBeenCalledWith(
      "/tag_suggestions/bulk-reopen",
      { review_id: 4 },
    );
  });
});
