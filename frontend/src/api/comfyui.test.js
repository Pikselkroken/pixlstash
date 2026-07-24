import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("../utils/apiClient", () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));

import { apiClient } from "../utils/apiClient";
import {
  listWorkflows,
  deleteWorkflow,
  importWorkflow,
  runImageToImage,
  abortRun,
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

  // Several call sites hold an explicit backend base; the module must place it
  // in front rather than silently dropping it.
  it("listWorkflows prefixes an explicit backend base", async () => {
    apiClient.get.mockResolvedValue({ data: {} });
    await listWorkflows({ baseUrl: "http://host:9000" });
    expect(apiClient.get).toHaveBeenCalledWith(
      "http://host:9000/comfyui/workflows",
    );
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
    const result = await runImageToImage(payload, { baseUrl: "/be" });
    expect(apiClient.post).toHaveBeenCalledWith("/be/comfyui/run_i2i", payload);
    expect(result).toEqual({ prompts: ["p1"] });
  });

  it("abortRun POSTs the abort route", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await abortRun();
    expect(apiClient.post).toHaveBeenCalledWith("/comfyui/abort");
  });
});
