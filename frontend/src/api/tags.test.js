import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("../utils/apiClient", () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));

import { apiClient } from "../utils/apiClient";
import { listTags, addPictureTag, removePictureTag } from "./tags";

beforeEach(() => {
  apiClient.get.mockReset();
  apiClient.post.mockReset();
  apiClient.delete.mockReset();
});

describe("api/tags", () => {
  it("listTags GETs the vocabulary with no config by default", async () => {
    apiClient.get.mockResolvedValue({ data: [{ tag: "hat" }] });
    const result = await listTags();
    expect(apiClient.get).toHaveBeenCalledWith("/tags", undefined);
    expect(result).toEqual([{ tag: "hat" }]);
  });

  it("listTags forwards query params", async () => {
    apiClient.get.mockResolvedValue({ data: [] });
    await listTags({ params: { min_count: 2 } });
    expect(apiClient.get).toHaveBeenCalledWith("/tags", {
      params: { min_count: 2 },
    });
  });

  it("addPictureTag POSTs the tag and returns the picture's tag list", async () => {
    apiClient.post.mockResolvedValue({ data: { tags: [{ id: 1 }] } });
    const result = await addPictureTag(42, "hat", { baseUrl: "/be" });
    expect(apiClient.post).toHaveBeenCalledWith("/be/pictures/42/tags", {
      tag: "hat",
    });
    expect(result.tags).toEqual([{ id: 1 }]);
  });

  // Removal is by tag id, not by name: two tags can render identically and the
  // id is what picks the row to drop.
  it("removePictureTag addresses the tag by id", async () => {
    apiClient.delete.mockResolvedValue({ data: {} });
    await removePictureTag(42, 7);
    expect(apiClient.delete).toHaveBeenCalledWith("/pictures/42/tags/7");
  });
});
