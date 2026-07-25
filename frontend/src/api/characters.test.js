import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("../utils/apiClient", () => ({
  apiClient: { get: vi.fn(), delete: vi.fn() },
}));

import { apiClient } from "../utils/apiClient";
import { listCharacters, deleteCharacter } from "./characters";

beforeEach(() => {
  apiClient.get.mockReset();
  apiClient.delete.mockReset();
});

describe("api/characters", () => {
  it("listCharacters GETs /characters with no config when no params", async () => {
    apiClient.get.mockResolvedValue({ data: [{ id: 1 }] });
    const result = await listCharacters();
    expect(apiClient.get).toHaveBeenCalledWith("/characters", undefined);
    expect(result).toEqual([{ id: 1 }]);
  });

  it("listCharacters forwards query params", async () => {
    apiClient.get.mockResolvedValue({ data: [] });
    await listCharacters({ project_id: 7 });
    expect(apiClient.get).toHaveBeenCalledWith("/characters", {
      params: { project_id: 7 },
    });
  });

  it("deleteCharacter DELETEs /characters/:id and returns the body", async () => {
    apiClient.delete.mockResolvedValue({ data: { deleted: true } });
    const result = await deleteCharacter(42);
    expect(apiClient.delete).toHaveBeenCalledWith("/characters/42");
    expect(result).toEqual({ deleted: true });
  });
});
