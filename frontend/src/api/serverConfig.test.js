import { describe, it, expect, beforeEach, vi } from "vitest";

// Pattern for API-module tests: mock the singleton apiClient, assert the module
// builds the right URL and returns response.data (never the axios envelope).
vi.mock("../utils/apiClient", () => ({
  apiClient: { get: vi.fn(), patch: vi.fn() },
}));

import { apiClient } from "../utils/apiClient";
import {
  getScrapheapRetention,
  setScrapheapRetentionDays,
  getScrapheapRetentionImpact,
  SCRAPHEAP_RETENTION_FIELD,
} from "./serverConfig";

const URL = "/server-config/scrapheap-retention";

beforeEach(() => {
  apiClient.get.mockReset();
  apiClient.patch.mockReset();
});

describe("api/serverConfig", () => {
  it("getScrapheapRetention GETs the topic URL and returns the body", async () => {
    apiClient.get.mockResolvedValue({
      data: { [SCRAPHEAP_RETENTION_FIELD]: 60 },
    });
    const result = await getScrapheapRetention();
    expect(apiClient.get).toHaveBeenCalledWith(URL);
    expect(result).toEqual({ [SCRAPHEAP_RETENTION_FIELD]: 60 });
  });

  it("setScrapheapRetentionDays PATCHes the retention field", async () => {
    apiClient.patch.mockResolvedValue({
      data: { [SCRAPHEAP_RETENTION_FIELD]: 90 },
    });
    const result = await setScrapheapRetentionDays(90);
    expect(apiClient.patch).toHaveBeenCalledWith(URL, {
      [SCRAPHEAP_RETENTION_FIELD]: 90,
    });
    expect(result).toEqual({ [SCRAPHEAP_RETENTION_FIELD]: 90 });
  });

  it("setScrapheapRetentionDays sends null for the Never choice", async () => {
    apiClient.patch.mockResolvedValue({ data: {} });
    await setScrapheapRetentionDays(null);
    expect(apiClient.patch).toHaveBeenCalledWith(URL, {
      [SCRAPHEAP_RETENTION_FIELD]: null,
    });
  });

  it("getScrapheapRetentionImpact GETs /impact with the candidate days", async () => {
    apiClient.get.mockResolvedValue({
      data: { would_purge_count: 12, first_purge_at: "2026-08-01T00:00:00Z" },
    });
    const result = await getScrapheapRetentionImpact(30);
    expect(apiClient.get).toHaveBeenCalledWith(`${URL}/impact`, {
      params: { days: 30 },
    });
    expect(result).toEqual({
      would_purge_count: 12,
      first_purge_at: "2026-08-01T00:00:00Z",
    });
  });

  // A failed impact probe must reject, never resolve to a "nothing would be
  // deleted" shape: the caller has to distinguish "could not verify" from zero.
  it("getScrapheapRetentionImpact propagates transport failures", async () => {
    apiClient.get.mockRejectedValue(new Error("404"));
    await expect(getScrapheapRetentionImpact(30)).rejects.toThrow("404");
  });
});
