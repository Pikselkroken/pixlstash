import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  apiClient: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

import { apiClient } from "../utils/apiClient";
import {
  getStack,
  listStackPictures,
  createStack,
  setStackOrder,
  removeStackMembers,
  previewKeepCoverOnly,
  keepCoverOnly,
} from "./stacks";

beforeEach(() => {
  apiClient.get.mockReset();
  apiClient.post.mockReset();
  apiClient.patch.mockReset();
  apiClient.delete.mockReset();
});

describe("api/stacks", () => {
  it("getStack returns the stack with its member ids", async () => {
    apiClient.get.mockResolvedValue({ data: { id: 3, picture_ids: [1, 2] } });
    const result = await getStack(3);
    expect(apiClient.get).toHaveBeenCalledWith("/stacks/3");
    expect(result.picture_ids).toEqual([1, 2]);
  });

  // The grid only ever needs the grid projection; asking for the full record
  // is markedly slower, so that default must not drift.
  it("listStackPictures defaults to the grid projection", async () => {
    apiClient.get.mockResolvedValue({ data: [] });
    await listStackPictures(3);
    expect(apiClient.get).toHaveBeenCalledWith("/stacks/3/pictures", {
      params: { fields: "grid" },
    });
  });

  it("listStackPictures forwards a sort and its direction", async () => {
    apiClient.get.mockResolvedValue({ data: [] });
    await listStackPictures(3, { sort: "DATE", descending: true });
    expect(apiClient.get).toHaveBeenCalledWith("/stacks/3/pictures", {
      params: { fields: "grid", sort: "DATE", descending: "true" },
    });
  });

  // No sort means "the stack's own order"; sending an empty sort would ask the
  // server to re-order the members instead.
  it("listStackPictures omits an absent sort", async () => {
    apiClient.get.mockResolvedValue({ data: [] });
    await listStackPictures(3, { sort: undefined, descending: undefined });
    expect(apiClient.get).toHaveBeenCalledWith("/stacks/3/pictures", {
      params: { fields: "grid" },
    });
  });

  it("createStack sends the ids in the caller's order", async () => {
    apiClient.post.mockResolvedValue({ data: { id: 9 } });
    await createStack([5, 3, 4]);
    expect(apiClient.post).toHaveBeenCalledWith("/stacks", {
      picture_ids: [5, 3, 4],
    });
  });

  it("setStackOrder PATCHes the order sub-resource", async () => {
    apiClient.patch.mockResolvedValue({ data: {} });
    await setStackOrder(9, [3, 4, 5]);
    expect(apiClient.patch).toHaveBeenCalledWith("/stacks/9/order", {
      picture_ids: [3, 4, 5],
    });
  });

  // Dissolving is "remove every member", and the ids ride in a DELETE body,
  // which Axios only sends via config.data.
  it("removeStackMembers sends the ids as the DELETE body", async () => {
    apiClient.delete.mockResolvedValue({ data: {} });
    await removeStackMembers(9, [3, 4]);
    expect(apiClient.delete).toHaveBeenCalledWith("/stacks/9/members", {
      data: { picture_ids: [3, 4] },
    });
  });
});

describe("api/stacks: keep cover only", () => {
  it("previews against the dry-run sub-resource and returns the report", async () => {
    apiClient.post.mockResolvedValue({ data: { pictures_moving: 414 } });
    const report = await previewKeepCoverOnly({ stackIds: [12, 19] });
    expect(apiClient.post).toHaveBeenCalledWith(
      "/stacks/keep-cover-only/preview",
      { stack_ids: [12, 19] },
    );
    expect(report.pictures_moving).toBe(414);
  });

  // The server refuses a body with neither list. Sending `[]` for the half the
  // caller did not name would turn "I named stacks" into "I named stacks and no
  // pictures", which is a different request shape for no reason.
  it("omits the id list the caller did not name", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await previewKeepCoverOnly({ pictureIds: [101, 102] });
    expect(apiClient.post).toHaveBeenCalledWith(
      "/stacks/keep-cover-only/preview",
      { picture_ids: [101, 102] },
    );
  });

  it("unions both id lists when the caller names both", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await keepCoverOnly({ stackIds: [12], pictureIds: [101] });
    expect(apiClient.post).toHaveBeenCalledWith("/stacks/keep-cover-only", {
      stack_ids: [12],
      picture_ids: [101],
    });
  });

  it("carries a batch id only when one was minted", async () => {
    apiClient.post.mockResolvedValue({ data: { status: "success" } });
    await keepCoverOnly({ stackIds: [12], batchId: "cli-abc" });
    expect(apiClient.post).toHaveBeenCalledWith("/stacks/keep-cover-only", {
      stack_ids: [12],
      batch_id: "cli-abc",
    });

    apiClient.post.mockClear();
    await keepCoverOnly({ stackIds: [12] });
    expect(apiClient.post).toHaveBeenCalledWith("/stacks/keep-cover-only", {
      stack_ids: [12],
    });
  });

  it("returns the response body rather than the axios envelope", async () => {
    apiClient.post.mockResolvedValue({
      data: { status: "success", pictures_moved: 414 },
    });
    const result = await keepCoverOnly({ stackIds: [12] });
    expect(result).toEqual({ status: "success", pictures_moved: 414 });
  });
});
