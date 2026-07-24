import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("../utils/apiClient", () => ({
  apiClient: { get: vi.fn() },
}));

import { apiClient } from "../utils/apiClient";
import { getWorkerProgress } from "./workers";

beforeEach(() => {
  apiClient.get.mockReset();
});

describe("api/workers", () => {
  it("getWorkerProgress GETs /workers/progress and returns the body", async () => {
    apiClient.get.mockResolvedValue({
      data: { workers: { tagger: { current: 3 } }, process: { vram: 1 } },
    });
    const result = await getWorkerProgress();
    expect(apiClient.get).toHaveBeenCalledWith("/workers/progress");
    expect(result).toEqual({
      workers: { tagger: { current: 3 } },
      process: { vram: 1 },
    });
  });

  // The store polls this on a timer and logs failures; it must see the
  // rejection rather than a benign empty object.
  it("getWorkerProgress propagates failures", async () => {
    apiClient.get.mockRejectedValue(new Error("network"));
    await expect(getWorkerProgress()).rejects.toThrow("network");
  });
});
