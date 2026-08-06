import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("../utils/apiClient", () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

import { apiClient } from "../utils/apiClient";
import {
  listProjects,
  createProject,
  updateProject,
  deleteProject,
  getProjectMembership,
} from "./projects";

beforeEach(() => {
  apiClient.get.mockReset();
  apiClient.post.mockReset();
  apiClient.put.mockReset();
  apiClient.delete.mockReset();
});

describe("api/projects", () => {
  it("listProjects GETs /projects with no config by default", async () => {
    apiClient.get.mockResolvedValue({ data: [{ id: 1 }] });
    const result = await listProjects();
    expect(apiClient.get).toHaveBeenCalledWith("/projects", undefined);
    expect(result).toEqual([{ id: 1 }]);
  });

  it("listProjects forwards query params", async () => {
    apiClient.get.mockResolvedValue({ data: [] });
    await listProjects({ params: { archived: true } });
    expect(apiClient.get).toHaveBeenCalledWith("/projects", {
      params: { archived: true },
    });
  });

  it("createProject POSTs the body and returns the created project", async () => {
    apiClient.post.mockResolvedValue({ data: { id: 9 } });
    const result = await createProject({ name: "Trip", description: null });
    expect(apiClient.post).toHaveBeenCalledWith("/projects", {
      name: "Trip",
      description: null,
    });
    expect(result).toEqual({ id: 9 });
  });

  // The editor always sends the whole pair, so this is a PUT: a null
  // description clears it rather than being ignored.
  it("updateProject PUTs the full editable pair", async () => {
    apiClient.put.mockResolvedValue({ data: {} });
    await updateProject(9, { name: "Trip", description: null });
    expect(apiClient.put).toHaveBeenCalledWith("/projects/9", {
      name: "Trip",
      description: null,
    });
  });

  it("deleteProject DELETEs under the given base", async () => {
    apiClient.delete.mockResolvedValue({ data: {} });
    await deleteProject(9);
    expect(apiClient.delete).toHaveBeenCalledWith("/projects/9");
  });

  it("getProjectMembership POSTs the picture ids", async () => {
    apiClient.post.mockResolvedValue({
      data: { project_assignments: {}, unassigned_picture_ids: [3] },
    });
    const result = await getProjectMembership([3]);
    expect(apiClient.post).toHaveBeenCalledWith("/projects/membership", {
      picture_ids: [3],
    });
    expect(result.unassigned_picture_ids).toEqual([3]);
  });
});
