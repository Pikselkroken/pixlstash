import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("../utils/apiClient", () => ({
  apiClient: { get: vi.fn() },
}));

import { apiClient } from "../utils/apiClient";
import { listProjects } from "./projects";

beforeEach(() => {
  apiClient.get.mockReset();
});

describe("api/projects", () => {
  it("listProjects GETs /projects with no config when unparameterised", async () => {
    apiClient.get.mockResolvedValue({ data: [{ id: 1 }] });
    const result = await listProjects();
    expect(apiClient.get).toHaveBeenCalledWith("/projects", undefined);
    expect(result).toEqual([{ id: 1 }]);
  });

  it("listProjects forwards query params when given them", async () => {
    apiClient.get.mockResolvedValue({ data: [] });
    await listProjects({ archived: true });
    expect(apiClient.get).toHaveBeenCalledWith("/projects", {
      params: { archived: true },
    });
  });
});
