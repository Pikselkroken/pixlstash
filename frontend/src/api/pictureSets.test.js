import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("../utils/apiClient", () => ({
  apiClient: { get: vi.fn() },
}));

import { apiClient } from "../utils/apiClient";
import { listPictureSets } from "./pictureSets";

beforeEach(() => {
  apiClient.get.mockReset();
});

describe("api/pictureSets", () => {
  it("listPictureSets GETs /picture_sets with no config when unparameterised", async () => {
    apiClient.get.mockResolvedValue({ data: [{ id: 1 }] });
    const result = await listPictureSets();
    expect(apiClient.get).toHaveBeenCalledWith("/picture_sets", undefined);
    expect(result).toEqual([{ id: 1 }]);
  });

  // The internal reference set is returned by the server; filtering it out is
  // the caller's decision, not this module's.
  it("listPictureSets returns the reference set unfiltered", async () => {
    apiClient.get.mockResolvedValue({
      data: [{ name: "reference_pictures" }, { name: "Holiday" }],
    });
    const result = await listPictureSets();
    expect(result).toHaveLength(2);
  });

  it("listPictureSets forwards query params when given them", async () => {
    apiClient.get.mockResolvedValue({ data: [] });
    await listPictureSets({ locked: true });
    expect(apiClient.get).toHaveBeenCalledWith("/picture_sets", {
      params: { locked: true },
    });
  });
});
