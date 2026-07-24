import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("../utils/apiClient", () => ({
  apiClient: { get: vi.fn() },
}));

import { apiClient } from "../utils/apiClient";
import { getAnomalyRegion } from "./pictures";

beforeEach(() => {
  apiClient.get.mockReset();
});

describe("api/pictures", () => {
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
