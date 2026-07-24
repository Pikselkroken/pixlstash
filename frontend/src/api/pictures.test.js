import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("../utils/apiClient", () => ({
  apiClient: { get: vi.fn() },
}));

import { apiClient } from "../utils/apiClient";
import { getAnomalyRegion, listPicturesByIds } from "./pictures";

beforeEach(() => {
  apiClient.get.mockReset();
});

describe("api/pictures", () => {
  it("listPicturesByIds repeats the id param once per picture", async () => {
    apiClient.get.mockResolvedValue({ data: [{ id: 4 }, { id: 5 }] });
    const result = await listPicturesByIds([4, 5]);
    expect(apiClient.get).toHaveBeenCalledWith("/pictures?id=4&id=5");
    expect(result).toEqual([{ id: 4 }, { id: 5 }]);
  });

  it("listPicturesByIds prefixes an explicit backend base", async () => {
    apiClient.get.mockResolvedValue({ data: [] });
    await listPicturesByIds([4], { baseUrl: "/be" });
    expect(apiClient.get).toHaveBeenCalledWith("/be/pictures?id=4");
  });

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
