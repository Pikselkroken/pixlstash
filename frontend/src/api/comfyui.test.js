import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  apiClient: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));

import { apiClient } from "../utils/apiClient";
import {
  listWorkflows,
  deleteWorkflow,
  importWorkflow,
  runImageToImage,
  abortRun,
  getPictureRecipe,
  runRecipe,
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

  it("deleteWorkflow URL-encodes the workflow name", async () => {
    apiClient.delete.mockResolvedValue({ data: {} });
    await deleteWorkflow("my flow.json");
    expect(apiClient.delete).toHaveBeenCalledWith(
      "/comfyui/workflows/my%20flow.json",
    );
  });

  it("importWorkflow defaults overwrite to false", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await importWorkflow({ name: "flow", workflow: { nodes: [] } });
    expect(apiClient.post).toHaveBeenCalledWith("/comfyui/workflows/import", {
      name: "flow",
      workflow: { nodes: [] },
      overwrite: false,
    });
  });

  it("importWorkflow forwards an explicit overwrite", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await importWorkflow({ name: "flow", workflow: {}, overwrite: true });
    expect(apiClient.post).toHaveBeenCalledWith("/comfyui/workflows/import", {
      name: "flow",
      workflow: {},
      overwrite: true,
    });
  });

  it("runImageToImage POSTs the payload under the given base", async () => {
    apiClient.post.mockResolvedValue({ data: { prompts: ["p1"] } });
    const payload = { picture_ids: [1], workflow_name: "flow" };
    const result = await runImageToImage(payload);
    expect(apiClient.post).toHaveBeenCalledWith("/comfyui/run_i2i", payload);
    expect(result).toEqual({ prompts: ["p1"] });
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
    expect(apiClient.get).toHaveBeenCalledWith(
      "/comfyui/pictures/7/recipe",
    );
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

describe("api/comfyui runRecipe", () => {
  it("POSTs the payload to the relative route by default", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    const payload = { picture_id: 7, seed_mode: "randomize" };
    await runRecipe(payload);
    expect(apiClient.post).toHaveBeenCalledWith("/comfyui/run_recipe", payload);
  });

  it("posts to the recipe run route", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await runRecipe({ picture_id: 7 });
    expect(apiClient.post).toHaveBeenCalledWith(
      "/comfyui/run_recipe",
      { picture_id: 7 },
    );
  });

  it("returns the response body", async () => {
    const body = {
      status: "queued",
      prompts: [{ picture_id: 7, prompt_id: "p1" }],
    };
    apiClient.post.mockResolvedValue({ data: body });
    const result = await runRecipe({ picture_id: 7 });
    expect(result).toEqual(body);
  });
});
