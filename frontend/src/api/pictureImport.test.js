import { describe, it, expect, beforeEach, vi } from "vitest";

// Pattern for API-module tests: mock the singleton apiClient, assert the module
// builds the right URL and returns the mapped body (never the axios envelope).
vi.mock("../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  apiClient: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));

import { apiClient } from "../utils/apiClient";
import { fetchStagingStatus, IMPORT_ENDPOINTS } from "./pictureImport";

beforeEach(() => {
  apiClient.get.mockReset();
});

describe("api/pictureImport: staging status buckets", () => {
  it("maps every completion bucket, Scrapheap matches included", async () => {
    apiClient.get.mockResolvedValue({
      data: {
        stage: "completed",
        staged: 4,
        total: 4,
        processed: 4,
        task_id: "t-1",
        imported_count: 1,
        duplicate_count: 1,
        scrapheaped_count: 2,
        scrapheaped_picture_ids: [7],
        failed_count: 0,
        cancelled_count: 0,
      },
    });

    const status = await fetchStagingStatus({
      backendUrl: "/be",
      stagingId: "s1",
    });

    expect(apiClient.get).toHaveBeenCalledWith(
      IMPORT_ENDPOINTS.status("/be", "s1"),
    );
    expect(status.importedCount).toBe(1);
    expect(status.duplicateCount).toBe(1);
    expect(status.scrapheapedCount).toBe(2);
    // Per-PICTURE, while the count is per-FILE: two staged copies of one
    // scrapheaped picture name its id once.
    expect(status.scrapheapedPictureIds).toEqual([7]);
    // The buckets are disjoint and sum to the total, so no summary line can
    // overstate what happened.
    expect(
      status.importedCount +
        status.duplicateCount +
        status.scrapheapedCount +
        status.failedCount +
        status.cancelledCount,
    ).toBe(status.total);
  });

  it("defaults the Scrapheap fields when an older backend omits them", async () => {
    apiClient.get.mockResolvedValue({
      data: { stage: "completed", imported_count: 3 },
    });
    const status = await fetchStagingStatus({
      backendUrl: "",
      stagingId: "s2",
    });
    expect(status.scrapheapedCount).toBeNull();
    // An array, never null: the caller iterates it to build the restore offer.
    expect(status.scrapheapedPictureIds).toEqual([]);
  });

  it("ignores a non-array scrapheaped_picture_ids rather than passing it on", async () => {
    apiClient.get.mockResolvedValue({
      data: { stage: "completed", scrapheaped_picture_ids: "7" },
    });
    const status = await fetchStagingStatus({
      backendUrl: "",
      stagingId: "s3",
    });
    expect(status.scrapheapedPictureIds).toEqual([]);
  });
});
