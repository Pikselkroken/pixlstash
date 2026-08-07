import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  apiClient: { get: vi.fn(), post: vi.fn() },
}));

import { apiClient } from "../utils/apiClient";
import { getTagHealth, rebuildTagHealth } from "./tagHealth";

beforeEach(() => {
  apiClient.get.mockReset();
  apiClient.post.mockReset();
});

describe("api/tagHealth", () => {
  it("getTagHealth forwards the scope params", async () => {
    apiClient.get.mockResolvedValue({ data: { rows: [] } });
    const params = { project_id: 2, set_id: 5 };
    const result = await getTagHealth(params);
    expect(apiClient.get).toHaveBeenCalledWith("/tag_health", { params });
    expect(result).toEqual({ rows: [] });
  });

  it("getTagHealth sends an unscoped request for the whole library", async () => {
    apiClient.get.mockResolvedValue({ data: { rows: [] } });
    await getTagHealth({});
    expect(apiClient.get).toHaveBeenCalledWith("/tag_health", { params: {} });
  });

  it("rebuildTagHealth POSTs the rebuild sub-resource", async () => {
    apiClient.post.mockResolvedValue({ data: { building: true } });
    const result = await rebuildTagHealth();
    expect(apiClient.post).toHaveBeenCalledWith("/tag_health/rebuild");
    expect(result).toEqual({ building: true });
  });
});
