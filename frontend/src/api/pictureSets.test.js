import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("../utils/apiClient", () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

import { apiClient } from "../utils/apiClient";
import {
  listPictureSets,
  createPictureSet,
  patchPictureSet,
  deletePictureSet,
  getPictureSetMembership,
  addPictureToSet,
  removePictureFromSet,
  getLockedMembers,
} from "./pictureSets";

beforeEach(() => {
  apiClient.get.mockReset();
  apiClient.post.mockReset();
  apiClient.patch.mockReset();
  apiClient.delete.mockReset();
});

describe("api/pictureSets", () => {
  it("listPictureSets GETs /picture_sets with no config by default", async () => {
    apiClient.get.mockResolvedValue({ data: [{ id: 1 }] });
    const result = await listPictureSets();
    expect(apiClient.get).toHaveBeenCalledWith("/picture_sets", undefined);
    expect(result).toEqual([{ id: 1 }]);
  });

  it("listPictureSets prefixes an explicit backend base", async () => {
    apiClient.get.mockResolvedValue({ data: [] });
    await listPictureSets({ baseUrl: "http://host:9000" });
    expect(apiClient.get).toHaveBeenCalledWith(
      "http://host:9000/picture_sets",
      undefined,
    );
  });

  // The internal reference sets come back from the server; filtering them is
  // the caller's decision, not this module's.
  it("listPictureSets returns reference sets unfiltered", async () => {
    apiClient.get.mockResolvedValue({
      data: [{ reference_character: 3 }, { name: "Holiday" }],
    });
    expect(await listPictureSets()).toHaveLength(2);
  });

  it("createPictureSet POSTs the set body", async () => {
    apiClient.post.mockResolvedValue({ data: { id: 5 } });
    const result = await createPictureSet({ name: "Trip" });
    expect(apiClient.post).toHaveBeenCalledWith("/picture_sets", {
      name: "Trip",
    });
    expect(result).toEqual({ id: 5 });
  });

  it("patchPictureSet addresses the set by id", async () => {
    apiClient.patch.mockResolvedValue({ data: {} });
    await patchPictureSet(5, { name: "Trip 2" }, { baseUrl: "/be" });
    expect(apiClient.patch).toHaveBeenCalledWith("/be/picture_sets/5", {
      name: "Trip 2",
    });
  });

  it("deletePictureSet DELETEs the set", async () => {
    apiClient.delete.mockResolvedValue({ data: {} });
    await deletePictureSet(5);
    expect(apiClient.delete).toHaveBeenCalledWith("/picture_sets/5");
  });

  it("getPictureSetMembership defaults include_deleted to false", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await getPictureSetMembership([1, 2]);
    expect(apiClient.post).toHaveBeenCalledWith("/picture_sets/membership", {
      picture_ids: [1, 2],
      include_deleted: false,
    });
  });

  it("getPictureSetMembership forwards include_deleted when asked", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await getPictureSetMembership([1], { includeDeleted: true });
    expect(apiClient.post).toHaveBeenCalledWith("/picture_sets/membership", {
      picture_ids: [1],
      include_deleted: true,
    });
  });

  // Membership is per-picture, so add/remove address one picture at a time and
  // bulk actions are the caller's loop.
  it("addPictureToSet addresses one picture within one set", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await addPictureToSet(5, 42);
    expect(apiClient.post).toHaveBeenCalledWith("/picture_sets/5/members/42");
  });

  it("removePictureFromSet addresses one picture within one set", async () => {
    apiClient.delete.mockResolvedValue({ data: {} });
    await removePictureFromSet(5, 42, { baseUrl: "/be" });
    expect(apiClient.delete).toHaveBeenCalledWith(
      "/be/picture_sets/5/members/42",
    );
  });

  it("getLockedMembers GETs the locked-members route", async () => {
    apiClient.get.mockResolvedValue({ data: { sets: [] } });
    const result = await getLockedMembers();
    expect(apiClient.get).toHaveBeenCalledWith("/picture_sets/locked-members");
    expect(result).toEqual({ sets: [] });
  });
});
