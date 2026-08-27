import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
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
  addCharacterFaceAssignments,
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

  it("listCharacters forwards query params", async () => {
    apiClient.get.mockResolvedValue({ data: [] });
    await listCharacters({ params: { project_id: 7 } });
    expect(apiClient.get).toHaveBeenCalledWith("/characters", {
      params: { project_id: 7 },
    });
  });

  // CONTRACT. Both routes declare `response_model=CharacterMutationResponse`
  // (pixlstash/routes/characters.py: POST ~1240, PATCH ~589) and return
  // {"status": "success", "character": ...}, so the record is NESTED. These
  // mocks mirror that envelope on purpose: a flat {id} mock here is what let
  // the create-and-assign flow ship broken, because every caller read `.id` off
  // the envelope and got undefined. If the backend ever flattens this, these
  // tests must fail before a user flow does.
  const CREATE_ENVELOPE = {
    status: "success",
    character: { id: 2, name: "Ada", project_id: null },
  };

  it("createCharacter POSTs the body and returns the mutation envelope", async () => {
    apiClient.post.mockResolvedValue({ data: CREATE_ENVELOPE });
    const result = await createCharacter({ name: "Ada" });
    expect(apiClient.post).toHaveBeenCalledWith("/characters", { name: "Ada" });
    expect(result).toEqual(CREATE_ENVELOPE);
    // The id lives under .character, never at the top level.
    expect(result.character.id).toBe(2);
    expect(result.id).toBeUndefined();
  });

  it("patchCharacter addresses the character by id and shares that envelope", async () => {
    const patchEnvelope = {
      status: "success",
      character: { id: 2, name: "Ada L." },
    };
    apiClient.patch.mockResolvedValue({ data: patchEnvelope });
    const result = await patchCharacter(2, { name: "Ada L." });
    expect(apiClient.patch).toHaveBeenCalledWith("/characters/2", {
      name: "Ada L.",
    });
    expect(result).toEqual(patchEnvelope);
    expect(result.character.id).toBe(2);
    expect(result.id).toBeUndefined();
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
    await addCharacterFaces(2, [7]);
    expect(apiClient.post).toHaveBeenCalledWith("/characters/2/faces", {
      picture_ids: [7],
    });
  });

  it("posts exact reviewed face assignments without legacy id fields", async () => {
    apiClient.post.mockResolvedValue({ data: { status: "success" } });
    await addCharacterFaceAssignments(
      2,
      [{ picture_id: 7, face_id: 11 }],
    );
    expect(apiClient.post).toHaveBeenCalledWith("/characters/2/faces", {
      face_assignments: [{ picture_id: 7, face_id: 11 }],
    });
  });

  // The ids ride in a DELETE body, which Axios only sends via config.data -
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
