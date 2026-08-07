import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  apiClient: { get: vi.fn() },
}));

import { apiClient } from "../utils/apiClient";
import { getSessionContext } from "./session";

beforeEach(() => {
  apiClient.get.mockReset();
});

describe("api/session", () => {
  it("getSessionContext GETs /session/context and returns the body", async () => {
    apiClient.get.mockResolvedValue({ data: { scope: "READ" } });
    const result = await getSessionContext();
    expect(apiClient.get).toHaveBeenCalledWith("/session/context");
    expect(result).toEqual({ scope: "READ" });
  });

  // Root.vue distinguishes an invalid share link from a valid one by whether
  // this rejects, so a failure must not resolve to a falsy context.
  it("getSessionContext propagates an invalid-token failure", async () => {
    apiClient.get.mockRejectedValue(new Error("401"));
    await expect(getSessionContext()).rejects.toThrow("401");
  });
});
