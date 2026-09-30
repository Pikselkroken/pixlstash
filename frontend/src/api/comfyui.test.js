import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  apiClient: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));

import { apiClient } from "../utils/apiClient";
import {
  listWorkflows,
  importWorkflow,
  abortRun,
  getPictureRecipe,
} from "./comfyui";

beforeEach(() => {
  apiClient.get.mockReset();
  apiClient.post.mockReset();
  apiClient.delete.mockReset();
});

describe("api/comfyui", () => {
  it("listWorkflows GETs the relative route by default", async () => {
    apiClient.get.mockResolvedValue({ data: { workflows: [] } });
    const result = await listWorkflows();
    expect(apiClient.get).toHaveBeenCalledWith("/comfyui/workflows");
    expect(result).toEqual({ workflows: [] });
  });

  it("importWorkflow posts the name and the file, and nothing else", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await importWorkflow({ name: "flow", workflow: { nodes: [] } });
    expect(apiClient.post).toHaveBeenCalledWith("/comfyui/workflows/import", {
      name: "flow",
      workflow: { nodes: [] },
    });
  });

  it("abortRun POSTs the abort route", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await abortRun();
    expect(apiClient.post).toHaveBeenCalledWith("/comfyui/abort");
  });
});

describe("api/comfyui getPictureRecipe", () => {
  it("GETs the relative route by default", async () => {
    apiClient.get.mockResolvedValue({ data: { available: true } });
    await getPictureRecipe(7);
    expect(apiClient.get).toHaveBeenCalledWith("/comfyui/pictures/7/recipe");
  });

  it("requests the picture recipe route", async () => {
    apiClient.get.mockResolvedValue({ data: {} });
    await getPictureRecipe(7);
    expect(apiClient.get).toHaveBeenCalledWith("/comfyui/pictures/7/recipe");
  });

  // A picture without a recipe is a normal answer, so the body is returned as
  // it stands rather than being turned into an error.
  it("returns the response body", async () => {
    const body = { available: false, reason: "no_prompt_chunk" };
    apiClient.get.mockResolvedValue({ data: body });
    const result = await getPictureRecipe(7);
    expect(result).toEqual(body);
  });
});
