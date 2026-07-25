import { describe, it, expect, beforeEach, vi } from "vitest";

// Pattern for API-module tests: mock the singleton apiClient, assert the module
// builds the right URL and returns response.data (never the axios envelope).
vi.mock("../utils/apiClient", () => ({
  apiClient: { get: vi.fn(), patch: vi.fn() },
}));

import { apiClient } from "../utils/apiClient";
import { getUserConfig, patchUserConfig } from "./config";

beforeEach(() => {
  apiClient.get.mockReset();
  apiClient.patch.mockReset();
});

describe("api/config", () => {
  it("getUserConfig GETs /users/me/config and returns the body", async () => {
    apiClient.get.mockResolvedValue({ data: { hidden_tags: ["a"] } });
    const result = await getUserConfig();
    expect(apiClient.get).toHaveBeenCalledWith("/users/me/config");
    expect(result).toEqual({ hidden_tags: ["a"] });
  });

  it("patchUserConfig PATCHes the partial and returns the body", async () => {
    apiClient.patch.mockResolvedValue({ data: { ok: true } });
    const partial = { apply_tag_filter: true };
    const result = await patchUserConfig(partial);
    expect(apiClient.patch).toHaveBeenCalledWith("/users/me/config", partial);
    expect(result).toEqual({ ok: true });
  });
});
