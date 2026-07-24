import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("../utils/apiClient", () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

import { apiClient } from "../utils/apiClient";
import {
  listCharacters,
  createCharacter,
  patchCharacter,
  deleteCharacter,
  getCharacterMembership,
  addCharacterFaces,
  removeCharacterFaces,
  getReferencePictures,
} from "./characters";

beforeEach(() => {
  apiClient.get.mockReset();
  apiClient.post.mockReset();
  apiClient.patch.mockReset();
  apiClient.delete.mockReset();
});

describe("api/characters", () => {
  it("listCharacters GETs /characters with no config by default", async () => {
    apiClient.get.mockResolvedValue({ data: [{ id: 1, name: "Ada" }] });
    const result = await listCharacters();
    expect(apiClient.get).toHaveBeenCalledWith("/characters", undefined);
    expect(result).toEqual([{ id: 1, name: "Ada" }]);
  });

  it("listCharacters forwards query params and a backend base", async () => {
    apiClient.get.mockResolvedValue({ data: [] });
    await listCharacters({ baseUrl: "/be", params: { project_id: 7 } });
    expect(apiClient.get).toHaveBeenCalledWith("/be/characters", {
      params: { project_id: 7 },
    });
  });

  it("createCharacter POSTs the body", async () => {
    apiClient.post.mockResolvedValue({ data: { id: 2 } });
    const result = await createCharacter({ name: "Ada" });
    expect(apiClient.post).toHaveBeenCalledWith("/characters", { name: "Ada" });
    expect(result).toEqual({ id: 2 });
  });

  it("patchCharacter addresses the character by id", async () => {
    apiClient.patch.mockResolvedValue({ data: {} });
    await patchCharacter(2, { name: "Ada L." });
    expect(apiClient.patch).toHaveBeenCalledWith("/characters/2", {
      name: "Ada L.",
    });
  });

  it("deleteCharacter DELETEs /characters/:id and returns the body", async () => {
    apiClient.delete.mockResolvedValue({ data: { deleted: true } });
    const result = await deleteCharacter(42);
    expect(apiClient.delete).toHaveBeenCalledWith("/characters/42");
    expect(result).toEqual({ deleted: true });
  });

  it("getCharacterMembership POSTs the picture ids", async () => {
    apiClient.post.mockResolvedValue({
      data: { 2: [7], pictures_with_faces: [7] },
    });
    const result = await getCharacterMembership([7, 8]);
    expect(apiClient.post).toHaveBeenCalledWith("/characters/membership", {
      picture_ids: [7, 8],
    });
    // A picture with no detected face is absent from pictures_with_faces, which
    // is what tells the caller an assignment there would be a no-op.
    expect(result.pictures_with_faces).toEqual([7]);
  });

  it("addCharacterFaces POSTs the ids to the faces sub-resource", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await addCharacterFaces(2, [7], { baseUrl: "/be" });
    expect(apiClient.post).toHaveBeenCalledWith("/be/characters/2/faces", {
      picture_ids: [7],
    });
  });

  // The ids ride in a DELETE body, which Axios only sends via config.data —
  // passing them any other way silently unassigns nothing.
  it("removeCharacterFaces sends the ids as the DELETE body", async () => {
    apiClient.delete.mockResolvedValue({ data: {} });
    await removeCharacterFaces(2, [7, 8]);
    expect(apiClient.delete).toHaveBeenCalledWith("/characters/2/faces", {
      data: { picture_ids: [7, 8] },
    });
  });

  it("getReferencePictures GETs the reference-pictures sub-resource", async () => {
    apiClient.get.mockResolvedValue({
      data: { reference_picture_ids: [4, 5] },
    });
    const result = await getReferencePictures(2);
    expect(apiClient.get).toHaveBeenCalledWith(
      "/characters/2/reference_pictures",
    );
    expect(result.reference_picture_ids).toEqual([4, 5]);
  });
});
